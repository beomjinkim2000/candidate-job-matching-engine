"""진입점의 계약 시험 — **실물 API도 실물 브라우저도 부르지 않는다.**

step 8이 만든 것은 **문 두 개(터미널·HTTP)와 그 안쪽 함수 하나**다. 그래서 여기서
확인하는 것은 「점수가 맞나」가 아니라 **「문이 약속한 대로 여닫히나」**다.

| 고른 케이스 | 깨지면 무엇이 거짓말이 되나 |
|---|---|
| `POST /score`가 랭킹을 준다 | 과제 요구 ④. 진입점이 결과를 못 내면 나머지가 무의미하다 |
| 검산 위반이면 422이고 **점수가 없다** | 「부분 결과 금지」. 근거 없는 점수가 화면에 나간다 |
| 키가 없으면 503이고 **키가 안 실린다** | 과제 CRITICAL(키 비노출) · 「0점으로 대신 않는다」 |
| `approve`가 `human_validated`로 올린다 | 없으면 「사람 확인함」 배지가 **안 바뀌는 장식**이다 |
| `weight`가 오면 400 | 「가중치는 못 바꾼다」. 배점을 만지면 직군 무관 일반화가 무너진다 |
| revision이 다르면 409 | 검산 G7. 낡은 루브릭에 「사람 확인함」을 달고 있게 된다 |
| `GET /runs/{id}` 두 번이 같다 | **재채점 금지.** 새로고침마다 순위가 뒤집히면 신뢰가 사라진다 |
| 이미지가 없으면 404 **+ 사유** | 바탕을 못 주면 좌표가 값을 잃는다. 조용한 빈 칸 금지 |
| 뒤집기·삭제가 배점을 다시 돌린다 | 「만점 100」. 안 돌리면 총합이 100이 아니다 |
| CLI와 HTTP가 같은 함수를 지난다 | 두 경로가 갈리면 어느 쪽이 맞는지 알 수 없다 |
| `GET /trace`가 LLM을 안 부른다 | 되돌아오는 화면이다. 호출이 새면 예산($5)이 새로고침으로 샌다 |
| 캐시가 없으면 부르는 대신 멈춘다 | 「맞기를 바란다」는 방어가 아니다. 어긋난 날 돈이 나간다 |
| `GET /resume`이 채점과 같은 글 | 다른 문자열 위에 그리면 **근거 사슬이 거짓말**을 한다 |
| 무엇을 가렸는지 함께 준다 | 가린 사실을 감추면 마스킹이 있었는지조차 알 수 없다 |

심사위원은 `test_aggregate.py`와 같은 **고정 픽스처**다.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import re
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from matching.api import cli, server, service
from matching.config import Settings
from matching.model import BBox, EvidenceGraph, Requirement, Resume, Span
from matching.parser import OcrLine, OcrResult
from matching.pipeline import RubricProposal
from matching.rubric import build_rubric, is_countable
from matching.source import PROVENANCE_FILENAME, Provenance

POSTING_ID = "test-posting"
REVISION = "1756700000"

# 갈래 분류 프롬프트가 조건에 붙이는 번호 (`rubric/branch.build_prompt`).
_NUMBERED_ITEM = re.compile(r"^(\d+)\. (.+)$", re.MULTILINE)

# 조건 문구에 직군 어휘를 넣지 않는다 — 층 배정은 **문자의 종류**로만 갈린다.
FACT_TEXT = "Python 및 SQL 활용 경험"
JUDGMENT_TEXT = "여러 이해관계자를 조율해 본 경험이 있는 분"

MARKERS = {"A-01": "코드명 알파", "A-02": "코드명 베타"}

RESUMES: dict[str, str] = {
    "A-01": (
        "프로젝트 코드명 알파\n"
        "Python과 SQL로 사내 정산 배치를 만들고 직접 운영했습니다.\n"
        "여러 부서와 매주 협의체를 열어 요구를 모으고 우선순위를 정했습니다.\n"
    ),
    "A-02": (
        "프로젝트 코드명 베타\n"
        "Python으로 리포트 생성을 자동화했습니다.\n"
        "의견이 갈릴 때 회의를 열어 조율한 적이 있습니다.\n"
    ),
}

QUOTES = {
    "A-01": "여러 부서와 매주 협의체를 열어 요구를 모으고 우선순위를 정했습니다",
    "A-02": "의견이 갈릴 때 회의를 열어 조율한 적이 있습니다",
}

PLAN = {"A-01": 5, "A-02": 3}


# --- 붙박이 입력 -------------------------------------------------------------


def _settings(**overrides) -> Settings:
    base = {"judge_model": "fixed-model-2026-08-31", "max_total_calls": 200}
    base.update(overrides)
    return Settings(**base)


def _req(req_id: str, text: str, kind: str = "required") -> Requirement:
    return Requirement(
        id=req_id,
        text=text,
        kind=kind,  # type: ignore[arg-type]
        evidence_grade="E2",
        ladder_step=1,
        source_bbox=BBox(page=1, x1=10, y1=20, x2=800, y2=48, img_w=860, img_h=2533),
        source_span=Span(start=0, end=len(text)),
    )


def _proposal(*, approved: bool = True, settings: Settings | None = None) -> RubricProposal:
    active = settings if settings is not None else _settings()
    requirements = [_req("R-01", FACT_TEXT), _req("R-02", JUDGMENT_TEXT, "preferred")]
    graph = EvidenceGraph()
    criteria = build_rubric(requirements, active, graph)
    return RubricProposal(
        posting_id=POSTING_ID,
        source_kind="local",
        requirements=requirements,
        criteria=criteria,
        graph=graph,
        posting_revision=REVISION,
        approved_at=datetime(2026, 9, 1, 9, 0).astimezone() if approved else None,
        approved_by="고객사 담당자" if approved else None,
    )


class _ScriptedClient:
    """이력서마다 정해진 점수를 준다. 프롬프트에서 지원자를 알아본다 — 호출 순서에 기대면
    채점 순서를 바꿨을 때 테스트가 조용히 다른 것을 재게 된다.
    """

    def __init__(self, plan: dict[str, int] | None = None) -> None:
        self.plan = dict(plan if plan is not None else PLAN)
        self.calls = 0
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        self.calls += 1
        body = " ".join(str(message.get("content", "")) for message in kwargs["messages"])
        for candidate_id, marker in MARKERS.items():  # noqa: B007 — break로 값을 쓴다
            if marker in body:
                break
        else:
            raise AssertionError("프롬프트에서 지원자를 알아보지 못했다")

        text = RESUMES[candidate_id]
        fragment = QUOTES[candidate_id]
        start = text.index(fragment)
        payload = {
            "quotes": [{"start": start, "end": start + len(fragment), "text": fragment}],
            "reasoning": "본인 역할과 결과가 얼마나 구체적으로 적혔는지만 보았다.",
            "score": self.plan[candidate_id],
        }
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))
                )
            ],
            usage=SimpleNamespace(prompt_tokens=1200, completion_tokens=180),
        )


class _LeakyClient:
    """호출하면 **키가 박힌 예외**를 던진다. 응답에 그 문자열이 살아 나오면 사고다."""

    LEAK = "LEAKME0001"

    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=self)

    def create(self, **_kwargs):
        raise RuntimeError(f"connect failed: https://example.test/x?access-key={self.LEAK}")


# --- 합성 공고 한 장 ---------------------------------------------------------
#
# **실물 `data/`를 쓰지 않는다.** `ocr.json`은 공고 본문이라 커밋되지 않으므로 CI에는
# 없고, 제출용 데이터셋에 테스트가 의존하면 그 데이터를 못 고치게 된다
# (`tests/CLAUDE.md` 「픽스처는 제출 데이터와 분리한다」).
#
# 줄 판정은 좌표로 갈린다 — x0가 `header_x_threshold`(100) 미만이면 섹션 제목이고,
# 불릿으로 시작하면 항목이다 (`parser/layout.classify_lines`).
POSTING_LINES: tuple[tuple[str, str, int, int], ...] = (
    ("L-001", "지원자격", 40, 100),
    ("L-002", f"· {FACT_TEXT}", 140, 140),
    ("L-003", f"· {JUDGMENT_TEXT}", 140, 180),
    ("L-004", "우대사항", 40, 240),
    ("L-005", "· 대규모 트래픽 처리 경험", 140, 280),
    ("L-006", "복리후생", 40, 340),
    ("L-007", "· 재택근무 가능", 140, 380),
)

# 3-C가 LLM에게 물어볼 줄과 받을 답. `복리후생`이 `excluded`인 것이 요점이다 —
# 안 가리면 「재택근무 가능」이 지원자에게 요구되는 조건으로 들어간다.
POSTING_HEADERS = ["지원자격", "우대사항", "복리후생"]
POSTING_ROLES = {"지원자격": "requirement", "우대사항": "preferred", "복리후생": "excluded"}


def _seed_posting(data_dir: Path) -> Path:
    """`ocr.json` + `provenance.json` + 이미지 자리. **`header_roles.json`은 안 쓴다.**

    캐시를 미리 깔면 「캐시가 없을 때 LLM을 부르는가」를 시험할 수 없다. 캐시는
    `POST /prepare`가 만든다 — 실제 경로가 그렇다.
    """
    directory = data_dir / service.POSTINGS_SUBDIR / POSTING_ID
    directory.mkdir(parents=True, exist_ok=True)
    # 이미지는 **열리지 않는다.** OCR 결과가 파일로 있으므로 파서는 이미지를 안 본다.
    image = directory / "img_1.png"
    image.write_bytes(b"fixture-not-a-real-image")

    lines = [
        OcrLine(
            id=line_id,
            text=text,
            conf=0.95,
            x0=x0,
            height=20,
            bbox=BBox(page=1, x1=x0, y1=y1, x2=x0 + 400, y2=y1 + 20, img_w=860, img_h=2533),
        )
        for line_id, text, x0, y1 in POSTING_LINES
    ]
    ocr = OcrResult(
        engine="paddle",
        engine_version="fixture",
        image_path=str(image),
        img_w=860,
        img_h=2533,
        lines=lines,
        avg_conf=0.95,
        elapsed_sec=0.0,
    )
    (directory / server.OCR_FILENAME).write_text(ocr.model_dump_json(indent=2), encoding="utf-8")

    provenance = Provenance(
        posting_id=POSTING_ID,
        source_kind="local",
        acquired_at=datetime(2026, 9, 1, 4, 0).astimezone(),
        image_sha256=[hashlib.sha256(image.read_bytes()).hexdigest()],
        image_size=[(860, 2533)],
    )
    (directory / PROVENANCE_FILENAME).write_text(
        provenance.model_dump_json(indent=2), encoding="utf-8"
    )
    return directory


class _HeaderClient:
    """공고 준비가 부르는 **텍스트 분류 둘**에 답하는 대역 — 헤더 역할과 조건 갈래.

    **부른 횟수를 센다.** 그 숫자가 시험 대상이다.

    갈래는 **옛 글자 모양 규칙 그대로** 답한다 (`is_countable`). 이 픽스처의 층 배정을
    그대로 두기 위해서다 — 여기서 재는 것은 「몇 번 불렀나」이지 「잘 분류하나」가 아니고,
    갈래 판정 자체는 `test_rubric.py`가 본다.
    """

    def __init__(self, roles: dict[str, str] | None = None) -> None:
        self.roles = dict(roles if roles is not None else POSTING_ROLES)
        self.calls = 0
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        self.calls += 1
        body = " ".join(str(message.get("content", "")) for message in kwargs["messages"])
        schema = kwargs.get("response_format", {}).get("json_schema", {}).get("name")
        if schema == "requirement_branches":
            return self._branches(body)
        labels = [{"text": text, "role": role} for text, role in self.roles.items() if text in body]
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps({"labels": labels}, ensure_ascii=False)
                    )
                )
            ],
            usage=SimpleNamespace(prompt_tokens=200, completion_tokens=40),
        )

    def _branches(self, body: str):
        """번호가 매겨져 온 조건에 갈래로 답한다."""
        labels = [
            {
                "index": int(match.group(1)),
                "branch": "term" if is_countable(match.group(2)) else "graded",
            }
            for match in _NUMBERED_ITEM.finditer(body)
        ]
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps({"labels": labels}, ensure_ascii=False)
                    )
                )
            ],
            usage=SimpleNamespace(prompt_tokens=200, completion_tokens=40),
        )


def _seed_resumes(data_dir: Path, candidate_ids=("A-01", "A-02")) -> None:
    directory = data_dir / service.RESUMES_SUBDIR / POSTING_ID
    directory.mkdir(parents=True, exist_ok=True)
    for candidate_id in candidate_ids:
        body = {"candidate_id": candidate_id, "text": RESUMES[candidate_id]}
        (directory / f"{candidate_id}.json").write_text(
            json.dumps(body, ensure_ascii=False), encoding="utf-8"
        )
    # 이력서가 아닌 파일도 함께 둔다 — 이름이 아니라 **내용으로** 가르는지 확인한다.
    (directory / "index.json").write_text(
        json.dumps({"posting_id": POSTING_ID, "count": len(candidate_ids)}, ensure_ascii=False),
        encoding="utf-8",
    )


# 「고정 픽스처 심사위원」과 「심사위원 없음」을 구별하는 표. `None`을 그대로 쓰면
# 둘이 같아져 「키가 없을 때」를 시험할 수 없다.
_DEFAULT_JUDGE = object()


def _client(
    tmp_path: Path,
    *,
    proposal: RubricProposal | None = None,
    settings: Settings | None = None,
    judge=_DEFAULT_JUDGE,
    seed: bool = True,
    raise_server_exceptions: bool = True,
) -> TestClient:
    active = settings if settings is not None else _settings()
    if seed:
        _seed_resumes(tmp_path)
    proposals = {POSTING_ID: proposal} if proposal is not None else {}
    app = server.create_app(
        active,
        data_dir=tmp_path,
        client=_ScriptedClient() if judge is _DEFAULT_JUDGE else judge,
        proposals=proposals,
    )
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


# --- 채점 -------------------------------------------------------------------


def test_score가_랭킹과_근거를_돌려준다(tmp_path):
    """과제 요구 ③·④의 진입점. 0~100점 · 순위 · 축별 근거가 한 응답에 있어야 한다."""
    client = _client(tmp_path, proposal=_proposal())

    response = client.post("/score", json={"posting_id": POSTING_ID})
    assert response.status_code == 200

    body = response.json()
    ranked = body["ranked"]
    assert [item["candidate_id"] for item in ranked] == ["A-01", "A-02"]
    assert [item["rank"] for item in ranked] == [1, 2]
    assert 0.0 <= ranked[1]["total"] < ranked[0]["total"] <= 100.0
    # 「사람이 읽을 수 있는 근거를 함께 낸다」 — 점수만 있는 응답은 요구의 절반이다.
    assert all(axis["rationale"] for axis in ranked[0]["breakdown"])
    assert sum(axis["max_weighted"] for axis in ranked[0]["breakdown"]) == pytest.approx(100.0)


def test_resume_ids로_고른_지원자만_채점한다(tmp_path):
    """없는 id를 조용히 빼지 않는다 — 6명을 요청했는데 5명이 채점되면 아무 데도 안 남는다."""
    client = _client(tmp_path, proposal=_proposal())

    picked = client.post("/score", json={"posting_id": POSTING_ID, "resume_ids": ["A-02"]})
    assert picked.status_code == 200
    assert [item["candidate_id"] for item in picked.json()["ranked"]] == ["A-02"]

    missing = client.post("/score", json={"posting_id": POSTING_ID, "resume_ids": ["A-99"]})
    assert missing.status_code == 400
    assert "A-99" in missing.json()["detail"]


def test_검산이_깨지면_422이고_본문에_점수가_없다(tmp_path):
    """검산은 경고가 아니라 **런타임 게이트**다. 경고로 내려가면 근거 없는 점수가 나간다."""
    broken = _proposal()
    # G3이 보는 Link를 끊는다 — 「이 항목이 공고에서 나왔다」의 유일한 증거다.
    broken.graph.links = [link for link in broken.graph.links if link.rel != "derived_from"]

    client = _client(tmp_path, proposal=broken)
    response = client.post("/score", json={"posting_id": POSTING_ID})

    assert response.status_code == 422
    body = response.json()
    assert body["violations"] and any(item["rule"] == "G3" for item in body["violations"])
    # **부분 결과를 주지 않는다.** 점수가 실릴 자리 자체가 없어야 한다.
    assert "ranked" not in body
    assert "total" not in response.text


def test_키가_없으면_503이고_응답에_키가_실리지_않는다(tmp_path):
    """판단 층을 0점으로 대신하지 않는다 — 그러면 「채점 못 함」과 「경험 없음」이 같아진다.

    두 방향을 함께 본다. (a) 키가 없을 때 멈추는가 (b) 키가 박힌 예외가 그대로 나가는가.
    """
    # judge=None이면 create_app이 설정에서 만든다 — 키가 비어 있으므로 결국 None이다.
    bare = _client(tmp_path, proposal=_proposal(), judge=None, settings=_settings())
    response = bare.post("/score", json={"posting_id": POSTING_ID})
    assert response.status_code == 503
    assert "OPENAI_API_KEY" in response.json()["detail"]

    leaky = _client(
        tmp_path,
        proposal=_proposal(),
        settings=_settings(openai_api_key=_LeakyClient.LEAK),
        judge=_LeakyClient(),
        raise_server_exceptions=False,
    )
    leaked = leaky.post("/score", json={"posting_id": POSTING_ID})
    assert leaked.status_code == 500
    assert _LeakyClient.LEAK not in leaked.text
    assert _LeakyClient.LEAK not in str(dict(leaked.headers))


# --- 승인 -------------------------------------------------------------------


def test_approve가_검토_상태를_사람_확인함으로_올린다(tmp_path):
    """이 경로가 없으면 `review_status`는 영원히 `draft` 한 값이고,
    화면의 「사람 확인함」 배지는 **절대 바뀌지 않는 장식**이 된다.
    """
    draft = _proposal(approved=False)
    fact = next(item for item in draft.criteria if item.layer == "fact")
    judgment = next(item for item in draft.criteria if item.layer == "judgment")
    client = _client(tmp_path, proposal=draft)

    response = client.post(
        "/approve",
        json={
            "posting_id": POSTING_ID,
            "posting_revision": REVISION,
            "decisions": [
                {"criterion_id": fact.id, "action": "approve"},
                {"criterion_id": judgment.id, "action": "flip"},
            ],
        },
    )
    assert response.status_code == 200

    body = response.json()
    assert body["approved_at"]
    rows = {item["criterion_id"]: item for item in body["criteria"]}
    assert rows[fact.id]["review_status"] == "human_validated"
    assert rows[fact.id]["kind"] == "required"
    # **뒤집힌 결과가 응답에 있어야 한다** — 없으면 화면은 전체를 다시 불러와야 한다.
    assert rows[judgment.id]["review_status"] == "human_validated"
    assert rows[judgment.id]["kind"] == "required"  # preferred → required
    assert not any(item["deleted"] for item in body["criteria"])

    # 승인했으므로 이제 채점이 통과한다 — 승인 게이트가 실제로 문이었다는 증거다.
    scored = client.post("/score", json={"posting_id": POSTING_ID})
    assert scored.status_code == 200
    assert scored.json()["unapproved"] is False


def test_승인하지_않으면_채점하지_않는다(tmp_path):
    """기본값이 거부다. 조용히 통과하면 우리가 남의 채용 기준을 대신 정한 것이 된다."""
    client = _client(tmp_path, proposal=_proposal(approved=False))
    response = client.post("/score", json={"posting_id": POSTING_ID})

    assert response.status_code == 409
    assert "승인" in response.json()["detail"]


def test_weight를_실어_보내면_400이다(tmp_path):
    """가중치를 승인 화면이 만지면 직군 무관 일반화가 무너진다 (`src/CLAUDE.md`).

    형식 오류(422)로 떨어뜨리지 않고 **사유가 붙은 400**을 준다 — 화면이 왜 막혔는지
    알아야 사용자에게 설명할 수 있다.
    """
    draft = _proposal(approved=False)
    client = _client(tmp_path, proposal=draft)

    response = client.post(
        "/approve",
        json={
            "posting_id": POSTING_ID,
            "posting_revision": REVISION,
            "decisions": [
                {"criterion_id": draft.criteria[0].id, "action": "approve", "weight": 50.0}
            ],
        },
    )
    assert response.status_code == 400
    assert "가중치" in response.json()["detail"]

    # 몸통에 실어도 막힌다 — 항목 안에만 막으면 우회로가 남는다.
    outer = client.post(
        "/approve",
        json={
            "posting_id": POSTING_ID,
            "posting_revision": REVISION,
            "weight": 50.0,
            "decisions": [{"criterion_id": draft.criteria[0].id, "action": "approve"}],
        },
    )
    assert outer.status_code == 400


def test_revision이_다르면_409다(tmp_path):
    """승인은 **그 시점의 공고**에 대한 것이다. 공고가 수정되면 낡는다 (검산 G7).

    이걸 안 하면 낡은 루브릭으로 계속 채점하면서 「사람 확인함」 배지를 달고 있게 된다.
    """
    client = _client(tmp_path, proposal=_proposal(approved=False))
    response = client.post(
        "/approve",
        json={
            "posting_id": POSTING_ID,
            "posting_revision": "1756799999",
            "decisions": [{"criterion_id": "C-01", "action": "approve"}],
        },
    )
    assert response.status_code == 409
    assert "G7" in response.json()["detail"]


def test_항목을_지우면_배점을_다시_돌려_총합이_100으로_남는다(tmp_path):
    """승인이 점수 계산식을 바꾸지는 않지만, 항목이 빠지면 **같은 식을 다시 돌려야** 한다.

    안 그러면 만점이 100 밑으로 내려가 「0~100점」이 거짓말이 된다.
    """
    draft = _proposal(approved=False)
    judgment = next(item for item in draft.criteria if item.layer == "judgment")
    client = _client(tmp_path, proposal=draft)

    response = client.post(
        "/approve",
        json={
            "posting_id": POSTING_ID,
            "posting_revision": REVISION,
            "decisions": [{"criterion_id": judgment.id, "action": "delete"}],
        },
    )
    assert response.status_code == 200
    rows = {item["criterion_id"]: item for item in response.json()["criteria"]}
    assert rows[judgment.id]["deleted"] is True

    scored = client.post("/score", json={"posting_id": POSTING_ID})
    assert scored.status_code == 200
    breakdown = scored.json()["ranked"][0]["breakdown"]
    assert judgment.id not in {axis["criterion_id"] for axis in breakdown}
    assert sum(axis["max_weighted"] for axis in breakdown) == pytest.approx(100.0)


# --- 읽기 -------------------------------------------------------------------


def test_runs를_두_번_불러도_같은_결과다(tmp_path):
    """새로고침마다 다시 채점하면 심사위원이 비결정적이라 **순위가 뒤집힌다.**

    「같은 픽스처라 같은 답이 나온 것」이 아님을 보이려고, 첫 채점 뒤 심사위원을
    **다른 점수를 주는 것으로 갈아 끼운다.** 그래도 응답이 같아야 읽기만 한 것이다.
    """
    client = _client(tmp_path, proposal=_proposal())
    created = client.post("/score", json={"posting_id": POSTING_ID})
    run_id = created.json()["run_id"]

    # 여기서부터 채점하면 결과가 달라진다.
    client.app.state.matching.client = _ScriptedClient({"A-01": 1, "A-02": 5})

    first = client.get(f"/runs/{run_id}")
    second = client.get(f"/runs/{run_id}")
    assert first.status_code == 200
    assert first.json() == second.json() == created.json()


def test_없는_실행을_읽으면_404다(tmp_path):
    client = _client(tmp_path, proposal=_proposal())
    response = client.get("/runs/없는-실행-20260901-120000")
    assert response.status_code == 404


def test_이미지가_없으면_404와_사유가_함께_나온다(tmp_path):
    """bbox를 저장해 놓고 바탕 이미지를 못 주면 좌표가 값을 잃는다.
    **조용히 빈 칸을 주지 않는다** — 왜 못 그리는지가 화면에 있어야 한다.
    """
    client = _client(tmp_path, proposal=_proposal())
    response = client.get(f"/image/{POSTING_ID}")

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert "이미지 없음" in detail and "좌표 표시 불가" in detail


def test_trace가_파싱을_보여주되_LLM을_다시_부르지_않는다(tmp_path):
    """화면 ②는 stepper로 **몇 번이고 되돌아오는 자리**다. 새로고침이 호출을 만들면
    예산 $5가 화면 조작으로 사라진다.

    「호출이 0인가」를 세는 방식이 요점이다 — 응답의 `llm_calls`는 파서가 스스로 적은
    값이라 파서를 믿는 검사이고, `_HeaderClient.calls`는 **실제로 부른 횟수**다. 둘 다 본다.
    """
    _seed_posting(tmp_path)
    llm = _HeaderClient()
    client = _client(tmp_path, judge=llm)

    prepared = client.post("/prepare", json={"posting_id": POSTING_ID})
    assert prepared.status_code == 200
    # 헤더 역할 1회 + 조건 갈래 1회. **공고당 이 둘뿐이다** — 둘 다 텍스트만 보내고
    # 둘 다 캐시가 맞으면 0회다 (`rubric/branch.py`).
    assert llm.calls == 2

    first = client.get(f"/trace/{POSTING_ID}")
    assert first.status_code == 200
    body = first.json()
    assert llm.calls == 2  # ← 이 줄이 이 테스트의 전부다
    assert body["cached"] is False
    assert body["parse_report"]["llm_calls"] == 0

    # 화면이 필요로 하는 것: 줄마다 좌표·판정·조건 역참조.
    assert [line["id"] for line in body["lines"]] == [item[0] for item in POSTING_LINES]
    header = next(line for line in body["lines"] if line["text"] == "지원자격")
    assert header["role"] == "header" and header["box"]["img_w"] == body["img_w"]
    item = next(line for line in body["lines"] if line["text"].endswith(FACT_TEXT))
    assert item["role"] == "item" and item["req"] is not None

    # **모델이 본 것이 이게 전부라는 걸 화면이 보여줘야 한다.** 좌표도 이미지도 안 갔다.
    assert body["sent_to_llm"]["headers"] == POSTING_HEADERS
    assert body["header_roles"] == POSTING_ROLES
    excluded = next(block for block in body["blocks"] if block["header"] == "복리후생")
    assert excluded["scored"] is False  # 복리후생이 조건으로 올라가면 점수가 틀린다

    second = client.get(f"/trace/{POSTING_ID}")
    assert second.status_code == 200
    assert second.json()["cached"] is True
    assert llm.calls == 2
    assert {k: v for k, v in second.json().items() if k != "cached"} == {
        k: v for k, v in body.items() if k != "cached"
    }


def test_trace는_캐시가_없으면_LLM을_부르는_대신_멈춘다(tmp_path):
    """**「캐시가 맞기를 바란다」는 방어가 아니다.** 어긋난 날 조용히 돈이 나간다.

    `POST /prepare`를 거치지 않은 공고는 헤더 역할 캐시가 없다. 그때 이 엔드포인트가
    LLM으로 넘어가면 GET 하나가 유료 호출이 된다 — 부를 클라이언트를 아예 안 넘기므로
    구조적으로 불가능하고, 그 사실이 409와 사유로 나온다.
    """
    _seed_posting(tmp_path)
    llm = _HeaderClient()
    client = _client(tmp_path, judge=llm)

    response = client.get(f"/trace/{POSTING_ID}")
    assert response.status_code == 409
    assert llm.calls == 0
    assert "/prepare" in response.json()["detail"]


def test_없는_공고의_trace는_404이고_경로_조각은_400이다(tmp_path):
    """조용히 빈 값을 주지 않는다. 경로 조각은 `_safe_id`가 먼저 막는다."""
    client = _client(tmp_path)

    missing = client.get("/trace/없는-공고")
    assert missing.status_code == 404
    assert "공고가 없다" in missing.json()["detail"]

    traversal = client.get("/trace/..%5C..%5Cetc")
    assert traversal.status_code == 400


def test_resume이_채점이_읽은_문자열을_그대로_준다(tmp_path):
    """근거를 **다른 문자열 위에** 그리면 근거 사슬이 거짓말을 한다.

    검산 G2가 「인용이 원문에 실재하는가」를 파이프라인 안에서 재는데, 화면은 그 원문을
    HTTP로 따로 받는다. 그 둘이 어긋나면 검산이 통과한 결과가 화면에서 어긋난 자리를
    하이라이트한다. 그래서 **실제 채점 결과의 span으로** 이 응답을 자른다.
    """
    client = _client(tmp_path, proposal=_proposal())
    scored = client.post("/score", json={"posting_id": POSTING_ID})
    assert scored.status_code == 200

    evidence = [item for item in scored.json()["graph"]["evidence"] if item["resume_id"] == "A-01"]
    assert evidence, "판단 층 인용이 하나도 없다 — 이 테스트가 아무것도 재지 못한다"

    response = client.get("/resume/A-01")
    assert response.status_code == 200
    body = response.json()
    assert body["posting_id"] == POSTING_ID
    assert body["length"] == len(body["text"]) == len(RESUMES["A-01"])
    for item in evidence:
        span = item["span"]
        assert body["text"][span["start"] : span["end"]] == item["quote"]


def test_resume이_무엇을_가렸는지_함께_준다(tmp_path):
    """가린 사실을 감추면 마스킹이 있었는지조차 알 수 없다 (`step9.md` 4번).

    길이 보존도 같이 본다 — 가리면서 길이가 바뀌면 위 테스트의 오프셋이 전부 어긋난다.
    """
    _seed_resumes(tmp_path)
    text = (
        "성명: 강유리 (여 / 1999.04.11 / 만 26세)\n"
        "한국대학교 컴퓨터공학과 졸업 (2018.03 ~ 2022.02)\n"
        "Python으로 사내 정산 배치를 만들었습니다.\n"
    )
    directory = tmp_path / service.RESUMES_SUBDIR / POSTING_ID
    (directory / "A-09.json").write_text(
        json.dumps({"candidate_id": "A-09", "text": text}, ensure_ascii=False), encoding="utf-8"
    )
    client = _client(tmp_path)

    body = client.get("/resume/A-09").json()
    assert "강유리" not in body["text"] and "한국대학교" not in body["text"]
    assert len(body["text"]) == len(text)  # 마스킹이 길이를 바꾸면 span이 전부 어긋난다
    # 경력 기간(`2018.03`)은 남는다 — 가리면 연차를 못 센다 (`scorer/mask.py`).
    assert "2018.03" in body["text"]
    assert "이름" in body["masked_fields"] and "학교명" in body["masked_fields"]
    for item in body["masked"]:
        segment = body["text"][item["start"] : item["end"]]
        assert set(segment) <= {body["mask_char"], "\n"}, item


def test_없는_이력서는_404이고_같은_id가_둘이면_409다(tmp_path):
    """조용히 하나를 고르면 **다른 공고의 이력서 위에** 근거를 그리게 된다."""
    client = _client(tmp_path)

    missing = client.get("/resume/A-99")
    assert missing.status_code == 404
    assert "A-99" in missing.json()["detail"]
    assert client.get("/resume/..%5C..%5Cetc").status_code == 400

    # 같은 지원자 식별자를 다른 공고에도 놓는다.
    other = tmp_path / service.RESUMES_SUBDIR / "다른-공고"
    other.mkdir(parents=True, exist_ok=True)
    (other / "A-01.json").write_text(
        json.dumps({"candidate_id": "A-01", "text": "다른 공고의 지원자다.\n"}, ensure_ascii=False),
        encoding="utf-8",
    )
    ambiguous = client.get("/resume/A-01")
    assert ambiguous.status_code == 409
    assert "posting_id" in ambiguous.json()["detail"]
    # 공고를 지정하면 그 공고의 것을 준다.
    picked = client.get("/resume/A-01", params={"posting_id": "다른-공고"})
    assert picked.status_code == 200
    assert picked.json()["posting_id"] == "다른-공고"


def test_첫_화면이_열린다(tmp_path):
    """UI는 step 9가 놓는다. 그 전에도 문은 열려 있어야 한다 — 500이면 진입점이 없는 것이다."""
    client = _client(tmp_path, proposal=_proposal())
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


# --- 두 진입점이 한 함수를 지난다 ---------------------------------------------


def test_CLI와_HTTP가_같은_채점_함수를_부른다():
    """로직을 양쪽에 복제하면 **두 경로의 결과가 갈렸을 때 어느 쪽이 맞는지 알 수 없다.**

    문자열 대조는 둔한 검사지만, 한쪽이 `pipeline.score()`를 직접 부르기 시작하면
    바로 걸린다. 그때가 복제가 시작되는 지점이다.
    """
    cli_source = inspect.getsource(cli._cmd_score)
    server_source = inspect.getsource(server.create_app)

    assert "score_proposal" in cli_source
    assert "score_proposal" in server_source
    # 채점을 일으키는 HTTP 엔드포인트는 하나다.
    assert server_source.count("@app.post") == 3
    assert server_source.count("score_proposal(") == 1


def test_이력서인지를_파일_이름이_아니라_내용으로_가른다(tmp_path):
    """`index.json`·`holdout.json`이 같은 디렉터리에 있다. 제외할 이름을 코드에 박으면
    파일이 하나 늘 때마다 조용히 이력서로 섞인다.
    """
    _seed_resumes(tmp_path)
    directory = tmp_path / service.RESUMES_SUBDIR / POSTING_ID
    (directory / "holdout.json").write_text(
        json.dumps({"posting_id": POSTING_ID, "requirement_ids": ["R-01"]}, ensure_ascii=False),
        encoding="utf-8",
    )

    loaded = service.load_resumes(directory)
    assert [resume.candidate_id for resume in loaded] == ["A-01", "A-02"]
    assert all(isinstance(resume, Resume) for resume in loaded)
