"""CLI와 HTTP가 부르는 **같은 함수 한 벌.**

과제 요구는 「CLI 또는 API 엔드포인트 **하나**로 실행 가능」이다. 사람이 손대는 자리는
둘이지만(터미널·브라우저) 그 아래 도는 코드는 하나여야 한다 — **두 경로의 결과가
갈리면 어느 쪽이 맞는지 확인할 방법이 없다.**

그래서 이 파일에는 argparse도 HTTP도 없다. `cli.py`는 인자를, `server.py`는 JSON을
각자 방식대로 받아 **여기 같은 함수를 부르고**, 채점 자체는 `pipeline.score()` 하나다.

## 여기서 하지 않는 것

- **점수를 만들지 않는다.** 집계·랭킹·검산은 전부 `pipeline`에 있다
- **설정을 고쳐 쓰지 않는다.** `--ocr-engine` 같은 런타임 인자는 `dataclasses.replace`로
  **사본**에 얹는다. 서버는 설정 하나를 모든 요청이 공유하므로, 한 요청이 원본을 고치면
  다음 요청이 그 값을 물려받는다
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from ..config import Settings
from ..model.objects import Criterion, Requirement, RequirementKind, Resume, ReviewStatus
from ..pipeline import RubricProposal, RunResult, prepare, score
from ..rubric.build import assign_layer, build_rubric
from ..source import (
    PROVENANCE_FILENAME,
    LocalSource,
    PostingRef,
    Provenance,
    SourceUnavailable,
    read_provenance,
)

RESUMES_SUBDIR = "resumes"
POSTINGS_SUBDIR = "postings"

JUDGMENT_LAYER = "judgment"

# `--source`가 고르는 것은 **어댑터**이지 출처가 아니다. 결과에 실리는 `source_kind`는
# `provenance.json`이 정한다 — 플래그 한 줄로 출처를 바꿀 수 있으면 그건 증거가 아니다.
SOURCE_ADAPTERS: dict[str, str] = {"local": "local", "saramin": "saramin_api"}

# 승인 화면이 뒤집을 수 있는 짝. `gate`·`duty`는 여기 없다 — 게이트는 「없으면 법적으로
# 그 일을 못 하는 것」이고 담당업무는 애초에 지원자에게 요구된 조건이 아니다.
FLIP_PAIRS: dict[str, RequirementKind] = {"required": "preferred", "preferred": "required"}

Action = Literal["approve", "flip", "delete"]


class EntryError(RuntimeError):
    """진입점이 받은 입력으로는 실행할 수 없다. **사용자가 고칠 수 있는 것만** 여기 온다."""


class JudgeUnavailable(RuntimeError):
    """판단 층을 채점해야 하는데 심사위원을 부를 수 없다.

    **0점으로 대신하지 않는다.** 그러면 「채점하지 못했다」와 「관련 경험이 없다」가
    같은 점수가 되어 65점이 조용히 사라진다 (`judge/panel.py`의 `NoGroundedResponse`와
    같은 이유).
    """


class Decision(BaseModel):
    """승인 화면이 항목 하나에 내린 결정.

    `extra="allow"`인 것이 의도다 — `weight`가 오면 **거부가 아니라 감지**해서
    「가중치는 못 바꾼다」를 사유가 붙은 400으로 돌려주기 위해서다. 형식 오류로
    떨어뜨리면 화면은 왜 막혔는지 모른다.
    """

    model_config = ConfigDict(extra="allow")

    criterion_id: str
    action: Action


class ApprovedCriterion(BaseModel):
    """승인 결과 한 줄. **`kind`가 여기 있는 것이 요점이다.**

    필수/우대는 `Requirement.kind`에 있고 `Criterion`에는 없다. 화면이 뒤집기 버튼을
    누르면 그 줄을 「우대」로 고쳐 그려야 하는데, 응답에 `kind`가 없으면 무엇을 보고
    고쳐 그릴지 알 수 없어 결국 전체를 다시 불러오게 된다.
    """

    model_config = ConfigDict(extra="forbid")

    criterion_id: str
    requirement_id: str
    kind: RequirementKind
    review_status: ReviewStatus
    deleted: bool


# --- 입력 읽기 ---------------------------------------------------------------


def resolve_source(name: str) -> str:
    """`--source` 값 → 어댑터 이름. **알 수 없는 값에 기본값을 주지 않는다.**"""
    kind = SOURCE_ADAPTERS.get(name)
    if kind is None:
        allowed = " · ".join(SOURCE_ADAPTERS)
        raise EntryError(f"알 수 없는 source: {name!r} ({allowed})")
    return kind


def load_resumes(directory: Path | str, resume_ids: Sequence[str] | None = None) -> list[Resume]:
    """이력서 디렉터리 하나를 읽는다.

    **이력서인지를 파일 이름이 아니라 내용으로 가른다.** 같은 디렉터리에 `index.json`
    (데이터셋 설계 메모)과 `holdout.json`(홀드아웃 조건 목록)이 함께 있는데, 제외할
    이름을 코드에 박으면 파일이 하나 늘 때마다 조용히 이력서로 섞인다.

    `resume_ids`를 주면 **준 순서대로** 돌려준다. 없는 id는 조용히 빼지 않는다 —
    6명을 요청했는데 5명이 채점되면 그 사실이 어디에도 안 남는다.
    """
    root = Path(directory)
    if not root.is_dir():
        raise EntryError(f"{root}: 이력서 디렉터리가 없다")

    found: dict[str, Resume] = {}
    for path in sorted(root.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise EntryError(f"{path.name}: JSON을 읽을 수 없다 ({exc.msg})") from exc
        if not isinstance(raw, dict) or "candidate_id" not in raw or "text" not in raw:
            continue
        resume = Resume(candidate_id=str(raw["candidate_id"]), text=str(raw["text"]))
        found[resume.candidate_id] = resume

    if not found:
        raise EntryError(
            f"{root}: 이력서를 한 통도 못 찾았다 — candidate_id와 text를 가진 JSON이 필요하다"
        )

    if resume_ids is None:
        return [found[key] for key in sorted(found)]

    missing = [item for item in resume_ids if item not in found]
    if missing:
        raise EntryError(f"{root}: 없는 지원자다 — {', '.join(missing)}")
    return [found[item] for item in resume_ids]


def make_client(settings: Settings):
    """OpenAI 클라이언트. **키가 없으면 `None`이고 여기서 막지 않는다.**

    키가 필요한 자리(헤더 역할 분류·심사위원)에서 판단한다 — 로더가 막으면 키 없이도
    도는 결정적 경로(사실 채점·랭킹)까지 같이 죽는다 (`config.load_settings`와 같은 규칙).
    """
    if not settings.openai_api_key:
        return None
    from openai import OpenAI

    return OpenAI(api_key=settings.openai_api_key)


def set_target_position(directory: Path | str, position: str) -> Provenance:
    """`provenance.json`의 `target_position`만 갈아 끼운다.

    파서는 대상 직무를 **오직 `provenance.json`에서** 읽으므로(`parser/__init__.py` 3-B′)
    `--position`은 여기를 지나야 한다.

    `write_provenance()`를 부르지 않는 이유: 그 함수는 파일을 처음부터 다시 만들어
    step 3이 채운 `ocr_engine`·`ocr_sha256`을 **`None`으로 되돌린다.** 그러면 「어느 OCR
    결과에서 나온 조건인가」의 증거가 사라지고 `verify_provenance()`가 위반을 낸다.
    """
    root = Path(directory)
    provenance = read_provenance(root)
    if provenance.target_position == position:
        return provenance
    updated = provenance.model_copy(update={"target_position": position})
    body = json.dumps(updated.model_dump(mode="json"), ensure_ascii=False, indent=2)
    (root / PROVENANCE_FILENAME).write_text(body + "\n", encoding="utf-8")
    return updated


def load_posting_ref(directory: Path | str, settings: Settings, *, source: str) -> PostingRef:
    """공고 디렉터리 하나를 `PostingRef`로.

    **이미지를 다시 확보하지 않는다.** 그건 `python -m matching acquire`의 일이고,
    키가 온 뒤에도 이미지는 다시 받지 않는다 (`docs/SCHEDULE.md` §2). 그래서 여기서
    `--source saramin`이 하는 일은 **키를 요구하는 것**뿐이고, 키가 없으면 조용히
    로컬로 넘어가지 않고 `SourceUnavailable`을 던진다 (`source/base.py`의 계약).
    """
    kind = resolve_source(source)
    if kind == "saramin_api" and not settings.saramin_access_key:
        raise SourceUnavailable(
            "사람인 API 키 없음 — .env의 SARAMIN_ACCESS_KEY가 비어 있다. "
            "이미 확보한 이미지로 돌리려면 source를 local로 준다"
        )

    root = Path(directory)
    provenance = read_provenance(root)
    refs = LocalSource(data_dir=root.parent.parent).list_postings()
    ref = next((item for item in refs if item.posting_id == provenance.posting_id), None)
    if ref is None:
        raise EntryError(f"{provenance.posting_id}: 공고 디렉터리를 못 찾았다")
    if not ref.image_paths:
        raise SourceUnavailable(
            f"{provenance.posting_id}: 공고 이미지가 0장이다 — "
            "이미지를 놓고 `python -m matching acquire`를 먼저 부른다"
        )
    # 출처는 플래그가 아니라 증거가 정한다.
    return ref.model_copy(update={"source_kind": provenance.source_kind})


# --- 준비 · 채점 -------------------------------------------------------------


def prepare_posting(
    posting_path: Path | str,
    settings: Settings,
    *,
    source: str = "local",
    position: str | None = None,
    ocr_engine: str | None = None,
    client=None,
) -> RubricProposal:
    """공고 하나 → **승인 대기 상태의 루브릭.** 채점하지 않는다.

    `settings`를 고치지 않고 사본에 런타임 인자를 얹는다 — 서버는 설정 하나를 모든
    요청이 공유한다.
    """
    directory = Path(posting_path)
    if not directory.is_dir():
        raise EntryError(f"{directory}: 공고 디렉터리가 없다")

    active = replace(settings, ocr_engine=ocr_engine) if ocr_engine else settings
    if position:
        set_target_position(directory, position)

    ref = load_posting_ref(directory, active, source=source)
    return prepare(ref, active, client=client, data_dir=directory.parent.parent)


def without_judgment(
    proposal: RubricProposal, settings: Settings
) -> tuple[RubricProposal, list[str]]:
    """`--no-judge` — 판단 층 항목을 **루브릭에서 빼고 다시 짓는다.**

    빼기만 하면 만점이 100 밑으로 내려가 「0~100점」이 거짓말이 되고, 그렇다고 자리를
    0점으로 채우면 「판단하지 않았다」와 「1점(관련 경험 없음)」이 같아진다. 다시 지으면
    남은 층이 100점을 나눠 갖는다 — `rubric/build.py`의 `_layer_totals`가 이미 그렇게
    설계돼 있다.

    **개발용이다.** 65점을 담당하던 축이 통째로 없으므로 결과는 제출물로 성립하지
    않는다 (`docs/COST_BUDGET.md` §5). 뺀 항목 이름을 돌려주는 것은 호출부가 그 사실을
    반드시 화면에 찍게 하기 위해서다.
    """
    graph = proposal.graph.model_copy(deep=True)
    dropped = [item.label for item in graph.criteria if item.layer == JUDGMENT_LAYER]
    stale = {item.id for item in graph.criteria}
    # **제안서가 이미 정한 갈래를 그대로 쓴다.** 다시 물으면 LLM을 한 번 더 부르고,
    # 안 물으면 옛 글자 모양 규칙으로 떨어져 **여기서만 층이 달라진다** — 그러면 화면이
    # 「판단 층을 뺐다」고 말하면서 실제로는 다른 항목을 뺀 셈이 된다.
    branches = {item.requirement_id: item.branch for item in proposal.criteria}
    graph.criteria = []
    graph.links = [link for link in graph.links if link.src not in stale]

    kept = [
        req
        for req in graph.requirements
        if assign_layer(req, settings, branches) != JUDGMENT_LAYER
    ]
    if not kept:
        raise EntryError(
            "판단 층을 빼면 채점할 항목이 하나도 남지 않는다 — 이 공고는 --no-judge로 못 돈다"
        )

    criteria = build_rubric(kept, settings, graph, branches=branches)
    return proposal.model_copy(update={"criteria": criteria, "graph": graph}), dropped


def require_judge(proposal: RubricProposal, client) -> None:
    """판단 층이 있는데 심사위원을 못 부르면 **여기서 멈춘다.**

    예외 메시지에 키를 담지 않는다 — 「있다/없다」만 말한다.
    """
    pending = [item for item in proposal.criteria if item.layer == JUDGMENT_LAYER]
    if pending and client is None:
        raise JudgeUnavailable(
            f"OpenAI API 키 없음 — 판단 층 {len(pending)}개를 채점할 수 없다. "
            ".env의 OPENAI_API_KEY를 채우거나 판단 층을 빼고 돈다"
        )


def score_proposal(
    proposal: RubricProposal,
    resumes: list[Resume],
    settings: Settings,
    *,
    client=None,
    data_dir: Path | str | None = None,
    no_judge: bool = False,
) -> tuple[RunResult, list[str]]:
    """승인 상태의 제안 하나를 채점한다. **CLI와 HTTP가 둘 다 이 함수를 지난다.**

    돌려주는 둘째 값은 `--no-judge`로 뺀 항목 이름이다. 비어 있지 않으면 호출부가
    반드시 경고를 찍는다.
    """
    dropped: list[str] = []
    if no_judge:
        proposal, dropped = without_judgment(proposal, settings)
    require_judge(proposal, client)
    result = score(proposal, resumes, settings, client=client, data_dir=data_dir)
    return result, dropped


# --- 승인 -------------------------------------------------------------------


def apply_decisions(
    proposal: RubricProposal,
    decisions: Sequence[Decision],
    settings: Settings,
    *,
    approved_by: str | None = None,
    now: datetime | None = None,
) -> tuple[RubricProposal, list[ApprovedCriterion]]:
    """승인 화면의 결정을 반영한다 — **승인 / 필수·우대 뒤집기 / 삭제 셋뿐이다.**

    `src/CLAUDE.md`: 「승인이 점수 계산식을 바꾸지 않는다. 필수/우대 판정과 항목 유무는
    바뀌지만, 가중치·기준점·매처는 그대로다.」 그래서 **식은 그대로 두고 다시 돌린다** —
    뒤집기는 `kind`를, 삭제는 항목 수를 바꾸므로 배분 결과는 당연히 달라지고, 다시
    돌리지 않으면 **총합이 100이 아니게 된다.**

    `criterion_id`는 그대로 둔다. 다시 지으면서 번호를 새로 매기면 화면이 방금 누른
    줄을 못 찾는다.

    뒤집기·삭제는 `contradicts` Link로 원래 판정을 남긴다 — `C-03 ──contradicts──▶ R-03`.
    삭제된 항목은 그래프에 **가중치 0으로 남긴다**: 지우면 그 Link가 허공을 가리키고,
    「사람이 뺐다」는 기록도 함께 사라진다.
    """
    by_id = {item.id: item for item in proposal.criteria}
    for decision in decisions:
        if "weight" in (decision.model_extra or {}):
            raise EntryError(
                f"{decision.criterion_id}: 가중치는 승인 화면에서 바꿀 수 없다 — "
                "할 수 있는 것은 승인 · 필수/우대 뒤집기 · 삭제 셋뿐이다"
            )
        if decision.criterion_id not in by_id:
            raise EntryError(f"루브릭에 없는 항목이다: {decision.criterion_id}")

    graph = proposal.graph.model_copy(deep=True)
    requirements = {req.id: req for req in graph.requirements}
    criteria = {item.id: item for item in graph.criteria}

    decided: dict[str, Action] = {}
    deleted: set[str] = set()
    for decision in decisions:
        criterion = criteria[decision.criterion_id]
        requirement = requirements.get(criterion.requirement_id)
        if requirement is None:
            # 여기 오면 검산 G3이 먼저 막았어야 한다.
            raise EntryError(
                f"{criterion.id}의 공고 조건({criterion.requirement_id})이 그래프에 없다"
            )

        if decision.action == "flip":
            flipped = FLIP_PAIRS.get(requirement.kind)
            if flipped is None:
                raise EntryError(
                    f"{criterion.id}: {requirement.kind} 조건은 뒤집을 수 없다 — "
                    "필수↔우대만 뒤집는다"
                )
            requirement.kind = flipped
            graph.link(criterion.id, "contradicts", requirement.id)
        elif decision.action == "delete":
            deleted.add(criterion.id)
            graph.link(criterion.id, "contradicts", requirement.id)
        decided[criterion.id] = decision.action

    # 배분을 **같은 식으로** 다시 돌린다. 버리는 그래프에 지어서 우리 그래프를 안 건드린다.
    survivors = [
        requirements[criteria[cid].requirement_id]
        for cid in criteria
        if cid not in deleted and criteria[cid].requirement_id in requirements
    ]
    scratch = proposal.graph.model_copy(deep=True)
    scratch.criteria = []
    scratch.links = []
    scratch.requirements = []
    # 갈래는 승인 대상이 아니다 — 화면에서 할 수 있는 것은 승인·뒤집기·삭제 셋뿐이다.
    # 그래서 다시 묻지 않고 제안서의 값을 그대로 넘긴다. 안 넘기면 배분이 **다른 층
    # 배정 위에서** 다시 계산되어 승인이 점수를 바꾼 것이 된다.
    branches = {item.requirement_id: item.branch for item in proposal.criteria}
    fresh = build_rubric(
        [req.model_copy(deep=True) for req in survivors], settings, scratch, branches=branches
    )
    weights = {item.requirement_id: item.weight for item in fresh}

    updated: list[Criterion] = []
    report: list[ApprovedCriterion] = []
    for criterion in graph.criteria:
        gone = criterion.id in deleted
        status: ReviewStatus = (
            "human_validated" if criterion.id in decided else criterion.review_status
        )
        criterion.weight = 0.0 if gone else weights.get(criterion.requirement_id, 0.0)
        criterion.review_status = status
        if not gone:
            updated.append(criterion)
        report.append(
            ApprovedCriterion(
                criterion_id=criterion.id,
                requirement_id=criterion.requirement_id,
                kind=_kind_of(requirements.get(criterion.requirement_id)),
                review_status=status,
                deleted=gone,
            )
        )

    approved_at = now if now is not None else datetime.now().astimezone()
    approved = proposal.model_copy(
        update={
            "criteria": updated,
            "graph": graph,
            "requirements": list(graph.requirements),
            "approved_at": approved_at,
            "approved_by": approved_by or "승인 화면",
        }
    )
    return approved, report


def _kind_of(requirement: Requirement | None) -> RequirementKind:
    """조건이 사라진 항목은 없어야 하지만, 있으면 그 사실을 `duty`로 감추지 않는다."""
    if requirement is None:
        raise EntryError("항목에 이어진 공고 조건이 없다 — 검산 G3이 먼저 막았어야 한다")
    return requirement.kind
