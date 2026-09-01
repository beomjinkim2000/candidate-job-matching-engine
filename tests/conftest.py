"""테스트 공용 장치 — **실물 OpenAI 클라이언트를 만드는 코드가 여기 없다.**

step 11이 고른 케이스 중 셋(직군 교차·유형 분리·장황함)은 파이프라인을 끝까지 돌려야
한다. 그런데 판단 층은 LLM이고 과제 예산은 $5인데 이미 넘겼다
(`data/.judge_usage.json`, 2026-09-01 시점 645회 · $5.95).

그래서 **판단 층만 스텁으로 갈아 끼운다.** 아래 두 스텁은 `client.chat.completions.create`
하나만 흉내 내고, 네트워크를 쓰지 않는다.

- `ConstantJudge` — 항목·이력서와 무관하게 **언제나 같은 점수**.
  직군 교차에 쓴다. 점수 차가 판단 층에서 나올 수 없게 만든다
- `OverlapJudge` — 항목 이름과 이력서의 **글자 겹침**을 1~5점으로.
  유형 분리에 쓴다. 판단 층이 내용에 반응해야 하는 케이스다

**`ConstantJudge`가 직군 교차의 요점이다.** 심사위원이 상수면 총점 차이는 게이트·사실
층과 루브릭 가중치에서만 나온다 — 그 층은 전부 결정적 코드다. 「하락이 스텁의 재주였다」는
반론이 원천적으로 성립하지 않는다.

**`OverlapJudge`는 심사위원의 대역이지 심사위원이 아니다.** 겹침 비율은 우리가 정한
식이고, 그 식이 좋은 채점자라는 근거는 없다. 이 스텁으로 통과한 테스트는
「파이프라인이 판단 점수를 받아 순서대로 합친다」까지만 말한다.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from matching.config import Settings
from matching.model import EvidenceGraph, Requirement, Resume
from matching.pipeline import RubricProposal
from matching.rubric import build_rubric
from matching.scorer.mask import MASK_CHAR

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
INDEX_JSON = REPO_ROOT / "phases" / "matching-engine" / "index.json"


# --- 픽스처 공고·이력서 -----------------------------------------------------


def load_posting(name: str) -> tuple[list[Requirement], list[Requirement]]:
    """`tests/fixtures/cross-job/posting-{name}.json` → (조건, 담당업무).

    **`data/`를 읽지 않는다.** 테스트가 제출용 데이터셋에 매이면 데이터를 못 고친다
    (`tests/CLAUDE.md`).
    """
    body = json.loads((FIXTURES / "cross-job" / f"posting-{name}.json").read_text("utf-8"))
    requirements = [Requirement.model_validate(item) for item in body["requirements"]]
    duties = [Requirement.model_validate(item) for item in body["duties"]]
    return requirements, duties


def load_resumes(name: str) -> list[dict]:
    """이력서 픽스처. `intended_type`(완벽/부분/미스)은 **여기서만** 산다.

    `Resume`에 담지 않는 이유는 `model/objects.py`의 `Resume` docstring에 있다 —
    채점 경로가 볼 수 있는 자리에 두면 유형 분리 테스트가 자기 자신을 검사하게 된다.
    """
    body = json.loads((FIXTURES / "cross-job" / f"resumes-{name}.json").read_text("utf-8"))
    return body["candidates"]


def resumes_of(name: str, *candidate_ids: str) -> list[Resume]:
    """픽스처 이력서를 `Resume`로. 인자를 안 주면 전원."""
    wanted = set(candidate_ids)
    return [
        Resume(candidate_id=item["candidate_id"], text=item["text"])
        for item in load_resumes(name)
        if not wanted or item["candidate_id"] in wanted
    ]


def make_settings(**overrides) -> Settings:
    """테스트 설정. 모델명을 고정해 결과 JSON이 실행마다 흔들리지 않게 한다."""
    base = {"judge_model": "stub-judge-for-tests", "max_total_calls": 500}
    base.update(overrides)
    return Settings(**base)


def make_proposal(name: str, settings: Settings | None = None) -> RubricProposal:
    """픽스처 공고 하나 → **승인된** 루브릭 제안.

    루브릭은 실제 경로와 같은 `build_rubric`이 만든다. 여기서 항목을 손으로 적으면
    「루브릭이 공고에서 나왔다」를 시험할 수 없다.
    """
    active = settings if settings is not None else make_settings()
    requirements, duties = load_posting(name)
    graph = EvidenceGraph()
    criteria = build_rubric(requirements, active, graph, duties)
    return RubricProposal(
        posting_id=f"fx-{name}",
        source_kind="local",
        requirements=[*requirements, *duties],
        criteria=criteria,
        graph=graph,
        posting_revision="fixture-revision",
        approved_at=datetime(2026, 9, 1, 9, 0).astimezone(),
        approved_by="픽스처 담당자",
    )


# --- 가짜 심사위원 ---------------------------------------------------------

_ITEM_HEADER = "[채점할 항목]"
_RESUME_HEADER = "[이력서]"
# `judge/prompt.py`의 `with_offsets()`가 붙이는 표기. 줄 머리 숫자가 곧 문자 오프셋이다.
_OFFSET_LINE = re.compile(r"^\[(\d+)\] (.*)$")
_LETTERS = re.compile(r"[^0-9A-Za-z가-힣]")

# 겹침 비율이 이 값이면 만점으로 본다. **임의값이다** — 필요한 성질은 「겹침이
# 늘면 점수도 는다」는 단조성뿐이고, 이 숫자에는 근거가 없다.
FULL_OVERLAP = 0.5


def _bigrams(text: str) -> set[str]:
    """글자 2개짜리 조각 집합. 한국어에 형태소 분석기 없이 겹침을 재는 가장 둔한 방법이다.

    조사가 붙은 「설비를」과 「설비」가 `설비` 조각을 공유하므로, 낱말 사전 없이도
    겹침이 잡힌다. 둔한 대신 **직군 어휘가 코드에 들어오지 않는다.**
    """
    letters = _LETTERS.sub("", text)
    return {letters[index : index + 2] for index in range(len(letters) - 1)}


def _parse_prompt(messages: list[dict]) -> tuple[str, list[tuple[int, str]]]:
    """프롬프트에서 (항목 이름, [(오프셋, 줄)])을 도로 읽어낸다.

    스텁이 지원자 목록이나 정답표를 미리 들고 있지 않게 하려는 것이다 — 프롬프트에
    실제로 담긴 것만 보고 답하면, 스텁이 「알고 맞히는」 경로가 사라진다.
    """
    user = next(item["content"] for item in messages if item["role"] == "user")
    lines = user.split("\n")

    label = ""
    for position, line in enumerate(lines):
        if line == _ITEM_HEADER:
            label = lines[position + 1].split(" · ", 1)[-1]
            break

    start = lines.index(_RESUME_HEADER)
    resume_lines: list[tuple[int, str]] = []
    for line in lines[start:]:
        matched = _OFFSET_LINE.match(line)
        if matched:
            resume_lines.append((int(matched.group(1)), matched.group(2)))
    return label, resume_lines


def _pick_quote(label: str, resume_lines: list[tuple[int, str]]) -> dict | None:
    """항목과 가장 많이 겹치는 줄 하나를 인용으로 고른다.

    **겹침이 0이어도 고른다.** 진짜 심사위원이라면 근거가 없을 때 quotes를 비우는 것이
    맞지만(`judge/prompt.py` 지시문 2), 스텁이 그러면 미스매칭 지원자에서
    `NoGroundedResponse`가 나 파이프라인이 멈춘다. 그러면 「점수가 낮다」를 잴 수 없다.
    """
    target = _bigrams(label)
    best: tuple[float, int, str] | None = None
    for offset, line in resume_lines:
        if not line.strip() or MASK_CHAR in line:
            continue  # 가린 자리를 인용하면 `keep_quotes`가 버린다
        share = len(target & _bigrams(line)) / len(target) if target else 0.0
        if best is None or share > best[0]:
            best = (share, offset, line)
    if best is None:
        return None
    _, offset, line = best
    return {"start": offset, "end": offset + len(line), "text": line}


class _StubJudge:
    """`client.chat.completions.create` 하나만 흉내 낸다. 네트워크를 쓰지 않는다."""

    def __init__(self) -> None:
        self.calls = 0
        self.chat = SimpleNamespace(completions=self)

    def score_for(self, label: str, resume_lines: list[tuple[int, str]]) -> tuple[int, str]:
        raise NotImplementedError

    def create(self, **kwargs):
        self.calls += 1
        label, resume_lines = _parse_prompt(kwargs["messages"])
        value, why = self.score_for(label, resume_lines)
        quote = _pick_quote(label, resume_lines)
        payload = {
            "quotes": [quote] if quote else [],
            "reasoning": why,
            "score": value,
        }
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))
                )
            ],
            usage=SimpleNamespace(prompt_tokens=1200, completion_tokens=180),
        )


class ConstantJudge(_StubJudge):
    """**항목과 이력서를 보지 않고** 언제나 같은 점수를 준다.

    직군 교차 테스트의 심사위원이다. 이걸 쓰면 판단 층이 모든 지원자·모든 공고에서
    같은 값이 되어, 총점 차이가 **게이트·사실 층과 루브릭 가중치**에서만 나온다.
    """

    def __init__(self, value: int = 3) -> None:
        super().__init__()
        self.value = value

    def score_for(self, label, resume_lines):
        return self.value, "스텁 심사위원 — 항목과 무관하게 고정 점수를 낸다."


class OverlapJudge(_StubJudge):
    """항목 이름과 이력서의 **글자 겹침**을 1~5점으로 옮긴다. 결정적이다.

    직군 어휘를 들고 있지 않다 — 겹칠 글자는 프롬프트에 실린 항목 이름에서 그때그때
    나온다. 그래서 공고가 바뀌면 이 스텁의 판정도 따라 바뀐다.
    """

    def score_for(self, label, resume_lines):
        target = _bigrams(label)
        document: set[str] = set()
        for _, line in resume_lines:
            document |= _bigrams(line)
        share = len(target & document) / len(target) if target else 0.0
        value = 1 + round(4 * min(1.0, share / FULL_OVERLAP))
        return value, f"항목 이름과의 글자 겹침 {share:.3f} — 스텁 환산."


# --- pytest 픽스처 ---------------------------------------------------------


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture(scope="session")
def repeat_sigma() -> float | None:
    """step 6이 실측한 반복 안정성 σ. **없으면 `None`이고, 그때는 판정하지 않는다.**

    저장 주소는 `index.json`의 step 6 `summary_data["repeat_sigma"]` **하나뿐**이다
    (`phases/matching-engine/step11.md`). 두 곳에 적으면 홀드아웃 테스트가 어느 쪽을
    볼지 모르고, 못 찾으면 조용히 skip된다.

    **기본값 0.5를 넣지 않는다.** 0.5는 σ의 **임계값**이지 실측 노이즈가 아니다.
    임계를 노이즈 추정치 자리에 넣으면 그 비교는 아무 말도 하지 않는다 (R4 심사).
    """
    if not INDEX_JSON.exists():
        return None
    body = json.loads(INDEX_JSON.read_text("utf-8"))
    for step in body.get("steps", []):
        if step.get("step") == 6:
            value = (step.get("summary_data") or {}).get("repeat_sigma")
            return float(value) if value is not None else None
    return None
