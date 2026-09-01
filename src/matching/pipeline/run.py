"""파이프라인 — **두 동강 낸다. 중간에 사람이 멈춰 서야 한다.**

```
prepare(posting)  →  RubricProposal      전부 draft
        ⛔ 승인 게이트 — 사람이 승인해야 다음이 없다
score(approved, resumes)  →  RunResult
```

## 왜 멈추는가

`review_status`가 코드 어딘가의 선택 함수로만 있으면 `human_validated`에 **도달할 경로가
없다.** 그러면 배지는 영원히 「AI 초안」이고, 우리가 남의 채용 기준을 대신 정한 것이 된다
(`docs/LEGAL_ARCHITECTURE.md` §4 · `docs/KAIREN_OS_ANALYSIS.md` §3-1).

- `prepare()`는 채점하지 않는다. **이력서를 아예 받지 않는다** — 받을 수 없으면 실수로
  채점할 수도 없다
- `score()`는 `approved_at`이 없으면 `ApprovalRequired`를 던진다
- 건너뛰려면 `settings.skip_approval=True`를 **명시적으로** 줘야 하고, 그때는
  `RunResult.unapproved=True`가 되어 결과 JSON과 화면 상단에 「미승인」이 붙는다.
  조용히 지나갈 수 없게 만드는 것이 요점이다

## 승인에는 유효기간이 있다 — 검산 G7

승인은 **그 시점의 공고**에 대한 것이므로 공고가 수정되면 낡는다. `score()`는 시작할 때
`PostingRegistry.current()`로 현재 값을 다시 조회해 `modification-timestamp`가 다르면
`ApprovalStale`을 던진다. 공고가 `active=0`이거나 마감일이 지났어도 마찬가지다.

> 근거: Kairen OS — *「`human_validated`는 사람이 **현재 revision**을 확인한 뒤에만 쓴다」*
> 이걸 안 하면 **낡은 루브릭으로 계속 채점하면서 「사람 확인함」 배지를 달고 있게 된다.**

레지스트리를 안 주면 G7을 **검사할 수 없다.** 그때는 통과시키되 `revision_checked=False`로
결과에 적는다 — 안 적으면 「검사해서 통과」와 「검사 못 함」이 화면에서 같아 보인다.
사람인 키가 아직 없는 지금(`docs/SCHEDULE.md` §2 경로 B)이 정확히 그 상태다.

## 검산은 aggregate 앞에 있다

`enforce()`가 먼저다. 위반이 있으면 **결과를 내보내지 않고** `GovernanceError`를 던진다.
검산이 경고로 내려가는 순간 근거 없는 점수가 화면에 나간다. 위반 목록은 예외가 들고
다니므로 어느 Object가 왜 막혔는지 알 수 있다.

## 원본 파기

`score()`가 끝나면 이력서 원문을 메모리에서 버린다. `RunResult`에 남는 것은 구조화된
조건·루브릭·점수·Link뿐이고, 인용은 **조각**이다 — **결과 JSON만으로는 이력서 내용이
복원되지 않는다** (`docs/LEGAL_ARCHITECTURE.md` §3-③).
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from ..config import Settings
from ..judge.panel import USAGE_FILENAME, CallBudget, UsageReport, judge_criterion
from ..model.governance import enforce
from ..model.graph import EvidenceGraph
from ..model.objects import Criterion, Requirement, Resume

# `ParseReport`만 가져온다. **OCR 엔진은 여기서 안 딸려 온다** — `parser/ocr.py`가
# paddle을 함수 안에서 import하므로(`ocr` 선택 그룹), 모듈 수준 import로 늘어나는 짐은
# Pillow 하나이고 그건 기본 의존성이다. `prepare()` 안의 `parse_posting` 지연 import는
# 그대로 둔다 — 무거운 것은 그 함수를 **부를 때** 들어온다.
from ..parser import ParseReport
from ..rubric.branch import BRANCHES_FILENAME, resolve_branches
from ..rubric.build import build_rubric
from ..scorer.fact import score_fact
from ..scorer.gate import run_gates
from ..scorer.normalize import load_aliases
from ..source.base import PostingRef, SourceKind, default_data_dir, posting_dir
from .aggregate import CandidateResult, aggregate
from .rank import rank as rank_results

if TYPE_CHECKING:  # 레지스트리는 httpx를 끌고 온다. 타입에만 쓰므로 런타임에 안 부른다
    from ..source.registry import PostingRegistry

RUNS_SUBDIR = "runs"
RESULT_FILENAME = "result.json"

JUDGMENT_LAYER = "judgment"


class ApprovalRequired(RuntimeError):
    """고객사 승인 없이 채점하려 했다. **기본값이 거부다.**"""


class ApprovalStale(RuntimeError):
    """승인이 현재 공고에 대한 것이 아니다 (검산 G7).

    공고가 수정됐거나, 마감됐거나, 조회되지 않는다. 셋 다 「지금 이 공고를 사람이
    확인했다」가 성립하지 않는 상태다.
    """


class RubricProposal(BaseModel):
    """승인 화면에 올라가는 것 — **전부 `draft`다.**

    `posting_revision`이 승인의 유효기간이다. 이 값이 없으면 `score()`가 G7을 검사할
    수 없고, 그 사실이 `RunResult.revision_checked=False`로 결과에 남는다.
    """

    model_config = ConfigDict(extra="forbid")

    posting_id: str
    source_kind: SourceKind
    requirements: list[Requirement]
    criteria: list[Criterion]
    graph: EvidenceGraph
    posting_revision: str | None  # API의 modification-timestamp
    approved_at: datetime | None = None
    approved_by: str | None = None
    # 파싱이 어떻게 됐는지. **화면에 그대로 나간다** (`step9.md` 9번). 임계값 2개가 공고
    # 1건 실측에서 나온 값이라 다른 공고에서 빗나갈 수 있는데, `role_counts`와 `ambiguous`
    # 비율만 보면 어디가 틀어졌는지 보인다. 결과에 안 실으면 그걸 볼 방법이 없다.
    parse_report: ParseReport | None = None


class RunResult(BaseModel):
    """완주 1회의 결과. `data/runs/{run_id}/result.json`에 그대로 저장된다.

    **그래프를 함께 싣는다** — UI가 점수 하나에서 공고 이미지 좌표까지 따라가려면
    (`EvidenceGraph.trace()`) 결과와 같은 파일에 있어야 한다. 따로 두면 둘이 어긋난
    상태를 만들 수 있다.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    posting_id: str
    source_kind: SourceKind
    created_at: datetime
    ranked: list[CandidateResult]
    graph: EvidenceGraph
    # --- 승인 상태. **화면 상단에 그대로 나간다** ---
    unapproved: bool  # True면 「미승인」 표시
    approved_at: datetime | None
    approved_by: str | None
    posting_revision: str | None
    revision_checked: bool  # G7을 실제로 검사했나 (레지스트리가 있었나)
    # --- 비용. 「이 결과를 만드는 데 n회 / $x / 모델 <이름>」 ---
    cost: UsageReport
    # 이 결과의 조건이 어떤 파싱에서 나왔나. `RubricProposal`에서 그대로 옮긴다.
    parse_report: ParseReport | None = None


def _plain_requirement(requirement: Requirement) -> Requirement:
    """`RequirementRecord`를 기본 `Requirement`로 되돌린다.

    파서는 `line_ids`(OCR 줄 역참조)를 붙인 하위 타입을 만든다. 그건 파서 안에서만 뜻이
    있고(`parser/classify.py`), `Requirement`가 `extra="forbid"`라 **그대로 결과에 실으면
    저장한 JSON을 되읽을 때 터진다** — step 8의 `GET /runs/{run_id}`가 바로 그 경로다.
    역참조는 `requirements.json`에 그대로 남아 있으므로 여기서 떼도 잃는 것이 없다.
    """
    if type(requirement) is Requirement:
        return requirement
    return Requirement.model_validate(requirement.model_dump(exclude={"line_ids"}))


def prepare(
    posting_ref: PostingRef,
    settings: Settings,
    *,
    client=None,
    data_dir: Path | str | None = None,
    registry: PostingRegistry | None = None,
    registry_posting_id: str | None = None,
) -> RubricProposal:
    """공고 하나를 파싱해 **승인 대기 상태의 루브릭**으로 만든다. 채점하지 않는다.

    `client`가 쓰이는 자리는 둘이고 **둘 다 텍스트만 보낸다. 이미지는 안 간다.**

    | 어디 | 무엇을 보내나 | 횟수 |
    |---|---|---|
    | 파서의 헤더 역할 분류 | 섹션 제목 문자열 몇 개 | 공고당 1회 |
    | 루브릭의 조건 갈래 분류 | 조건 문구 | 공고당 1회 |

    둘 다 캐시가 맞으면 0회다. **못 부르면(`client=None`) 갈래는 옛 글자 모양 규칙으로
    떨어지고**, 헤더 역할은 캐시가 없으면 `ParseError`로 멈춘다 — 후자는 기본값을 고를
    수 없는 판정이라 그렇다 (`parser/header_role.py`).

    `registry_posting_id`는 **사람인 공고 ID**다. 우리 쪽 식별자(`posting_ref.posting_id`)와
    다르다 (`source/registry.py`). 안 주면 우리 식별자로 조회하고, 못 찾으면
    `posting_revision`이 `None`으로 남는다 — 그 상태가 결과에 드러난다.
    """
    # 무거운 의존성(OCR 엔진)을 승인 화면만 쓰는 사람에게 지우지 않는다 (`api/cli.py`와 같다).
    from ..parser import parse_posting

    _requirements, graph, report = parse_posting(
        posting_ref, settings, client=client, data_dir=data_dir
    )
    graph.requirements = [_plain_requirement(req) for req in graph.requirements]

    # 조건과 담당업무를 **`kind`로** 가른다. 파서가 둘을 같은 그래프에 담고 순서까지
    # 유지하므로(`R-*` 다음 `D-*`) 반환값을 다시 대조할 필요가 없다.
    scored = [req for req in graph.requirements if req.kind != "duty"]
    duties = [req for req in graph.requirements if req.kind == "duty"]

    # 조건이 **이력서에서 어떻게 확인되는가**를 먼저 정한다. 층 배정과 기준점이 둘 다
    # 여기서 나오므로 루브릭을 짓기 전이어야 한다 (`rubric/branch.py`).
    root = Path(data_dir) if data_dir is not None else default_data_dir()
    branch_result = resolve_branches(
        scored,
        client=client,
        cache_path=posting_dir(root, posting_ref.posting_id) / BRANCHES_FILENAME,
        model=settings.header_model,
    )
    criteria = build_rubric(scored, settings, graph, duties, branches=branch_result.branches)

    # **화면의 「LLM 호출」은 이 공고를 준비하며 부른 총 횟수여야 한다.** 파서 것만 세면
    # 갈래 분류 1회가 회계에서 사라지고, 그런 「없는 지출」이 이 프로젝트에서 이미 사고를
    # 냈다 (`judge/panel.py`의 `spend`).
    if branch_result.llm_calls:
        report = report.model_copy(
            update={"llm_calls": report.llm_calls + branch_result.llm_calls}
        )

    posting_revision: str | None = None
    if registry is not None:
        meta = registry.current(registry_posting_id or posting_ref.posting_id)
        posting_revision = meta.modification_timestamp if meta is not None else None

    # **전부 draft로 돌려준다.** 승인은 `rubric/review.apply_approval()`이 한다.
    return RubricProposal(
        posting_id=posting_ref.posting_id,
        source_kind=posting_ref.source_kind,
        requirements=list(graph.requirements),
        criteria=criteria,
        graph=graph,
        posting_revision=posting_revision,
        parse_report=report,
    )


def _check_approval(
    proposal: RubricProposal,
    settings: Settings,
    registry: PostingRegistry | None,
    registry_posting_id: str | None,
) -> tuple[bool, bool]:
    """승인 게이트와 검산 G7. `(미승인인가, revision을 실제로 검사했는가)`.

    **순서가 규칙이다.** 공고 상태(마감·비활성)를 먼저 본다 — 승인 여부와 무관하게
    마감된 공고는 채점하지 않는다. 그다음 승인 유무, 마지막이 revision 일치다.
    """
    current = None
    if registry is not None:
        current = registry.current(registry_posting_id or proposal.posting_id)
        if current is None:
            raise ApprovalStale(
                f"{proposal.posting_id}: 공고를 조회할 수 없다 — "
                "지금 게시 중인 공고인지 확인되지 않은 상태로 채점하지 않는다 (검산 G7)"
            )
        if not current.active:
            raise ApprovalStale(
                f"{proposal.posting_id}: 마감된 공고다 (active=0) — 채점하지 않는다"
            )
        if current.expiration_date is not None and current.expiration_date < date.today():
            raise ApprovalStale(
                f"{proposal.posting_id}: 마감일({current.expiration_date})이 지났다 — "
                "채점하지 않는다"
            )

    unapproved = proposal.approved_at is None
    if unapproved and not settings.skip_approval:
        raise ApprovalRequired(
            f"{proposal.posting_id}: 고객사 승인이 없다. 루브릭은 아직 AI 초안이다. "
            "건너뛰려면 settings.skip_approval=True를 명시적으로 준다 — "
            "그때는 결과와 화면에 「미승인」이 표시된다"
        )

    if not unapproved and current is not None:
        if current.modification_timestamp != proposal.posting_revision:
            raise ApprovalStale(
                f"{proposal.posting_id}: 승인 이후 공고가 수정됐다 "
                f"(승인 시점 {proposal.posting_revision!r} → 현재 "
                f"{current.modification_timestamp!r}). 낡은 루브릭으로 채점하지 않는다 (검산 G7)"
            )

    return unapproved, current is not None


def score(
    proposal: RubricProposal,
    resumes: list[Resume],
    settings: Settings,
    *,
    client=None,
    registry: PostingRegistry | None = None,
    registry_posting_id: str | None = None,
    budget: CallBudget | None = None,
    data_dir: Path | str | None = None,
    now: datetime | None = None,
) -> RunResult:
    """승인된 루브릭으로 이력서를 채점하고 랭킹을 만든다.

    순서는 `step7.md`의 그림 그대로다 — 게이트 → 사실 → 판단 → **검산** → 집계 → 랭킹.
    검산이 집계 앞에 있는 것이 핵심이다.

    마스킹은 각 층이 **스스로** 건다 (`scorer/mask.py`의 계약). 여기서 미리 가려 넘기면
    가린 글로 두 번 가리게 되고, 무엇보다 「호출자가 깜빡할 자리」가 다시 생긴다.

    **게이트 탈락자도 끝까지 채점한다.** 탈락 사유만 남기면 「그 외에는 어땠나」를 볼 수
    없어 게이트가 틀렸을 때 되돌릴 근거가 사라진다. 게이트는 면허·법정자격만 보도록 좁게
    설계돼 있어(`scorer/gate.py`) 탈락자가 드물므로 비용도 문제되지 않는다.
    """
    unapproved, revision_checked = _check_approval(
        proposal, settings, registry, registry_posting_id
    )

    root = Path(data_dir) if data_dir is not None else default_data_dir()
    created_at = now if now is not None else datetime.now().astimezone()
    run_id = f"{proposal.posting_id}-{created_at:%Y%m%d-%H%M%S}"

    # 제안서의 그래프를 건드리지 않는다 — 같은 제안으로 여러 번 채점할 수 있어야 한다.
    graph = proposal.graph.model_copy(deep=True)
    criteria = list(proposal.criteria)
    judgment_criteria = [item for item in criteria if item.layer == JUDGMENT_LAYER]
    if judgment_criteria and client is None:
        raise ValueError(
            f"판단 층 항목이 {len(judgment_criteria)}개인데 심사위원 클라이언트가 없다. "
            "조용히 건너뛰면 그 항목들이 0점이 되어 「경험 없음」과 구별이 사라진다"
        )

    owns_budget = budget is None
    active_budget = budget if budget is not None else CallBudget(settings, root / USAGE_FILENAME)
    aliases = load_aliases()  # 12명이 같은 표를 쓴다. 매번 파일을 읽을 이유가 없다

    # 검산 G2가 인용을 대조할 원문. **결과에는 싣지 않는다** (원본 파기).
    resume_texts = {resume.candidate_id: resume.text for resume in resumes}

    for resume in resumes:
        run_gates(resume, criteria, graph, aliases=aliases)
        score_fact(resume, criteria, graph, settings=settings, aliases=aliases)
        for criterion in judgment_criteria:
            judge_criterion(
                criterion,
                resume,
                resume.text,
                graph,
                settings,
                client,
                budget=active_budget,
            )

    # ⛔ 여기서 막힌다. 위반이 있으면 결과를 내보내지 않는다.
    enforce(graph, resume_texts)

    results = [
        aggregate(
            [score for score in graph.scores if score.candidate_id == resume.candidate_id],
            criteria,
            graph,
            settings,
            graph_ref=run_id,
        )
        for resume in resumes
    ]

    result = RunResult(
        run_id=run_id,
        posting_id=proposal.posting_id,
        source_kind=proposal.source_kind,
        created_at=created_at,
        ranked=rank_results(results),
        graph=graph,
        unapproved=unapproved,
        approved_at=proposal.approved_at,
        approved_by=proposal.approved_by,
        posting_revision=proposal.posting_revision,
        revision_checked=revision_checked,
        cost=active_budget.report(),
        parse_report=proposal.parse_report,
    )

    if owns_budget:
        active_budget.save()
    save_run(result, root)

    # --- 원본 파기 -----------------------------------------------------------
    # 이력서 원문은 검산이 끝난 시점에 할 일이 없다. `RunResult`에는 애초에 안 실었고,
    # 여기서 지역 사본까지 버린다. 근거 문단은 그래프의 **인용 조각**으로 렌더링된다.
    resume_texts.clear()
    return result


def save_run(result: RunResult, data_dir: Path | str | None = None) -> Path:
    """`data/runs/{run_id}/result.json`에 저장하고 그 경로를 준다."""
    root = Path(data_dir) if data_dir is not None else default_data_dir()
    directory = root / RUNS_SUBDIR / result.run_id
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / RESULT_FILENAME
    target.write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def load_run(run_id: str, data_dir: Path | str | None = None) -> RunResult:
    """저장된 결과를 되읽는다. **재채점하지 않는다** (step 8의 `GET /runs/{run_id}`)."""
    root = Path(data_dir) if data_dir is not None else default_data_dir()
    body = (root / RUNS_SUBDIR / run_id / RESULT_FILENAME).read_text(encoding="utf-8")
    return RunResult.model_validate_json(body)
