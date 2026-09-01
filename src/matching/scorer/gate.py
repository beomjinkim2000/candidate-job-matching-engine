"""0층 게이트 — **면허·법정 자격증만. 여기서 넓히지 않는다.**

공고가 「필수」라고 쓴 조건도 게이트가 아니다. 문턱식(하나 못 넘으면 탈락)과 합산식을
비교한 자료에서 **합산식이 대부분 조건에서 더 나은 선발 효용**을 냈고, 버스 운전사 지원자
398명 자료에서는 **문턱식으로 자르니 특정 집단의 탈락률이 유의미하게 올랐다**
(`docs/TRADEOFFS.md` A-2). 그래서 탈락시키는 것은 **없으면 그 일을 법적으로 못 하는 것**
뿐이고, 무엇이 거기 드는지는 `settings.gate_kinds`가 정한다 — 코드가 아니라 설정이다.

무엇이 게이트 항목인지는 이 파일이 정하지 않는다. `rubric/build.py`가 층을 배정할 때
이미 정했고(`layer == "gate"`), 여기서는 그 항목만 본다. **판정 자리를 두 곳에 두지
않는다** — 두면 한쪽만 고쳐 놓고 고쳤다고 생각하게 된다.

## 의심스러우면 통과시킨다

탈락은 되돌릴 수 없는 판정이다. 그래서 **조건에서 뽑은 표현이 하나도 안 걸릴 때만**
떨어뜨린다. 절반만 걸린 사람은 통과시키고, 그 애매함은 판단 층이 본다.

반대로 하면(전부 걸려야 통과) 자격증 이름을 조금 다르게 적은 사람이 조용히 떨어진다.
그 실패는 결과 화면에 「탈락」으로만 보이고 왜 떨어졌는지는 안 보인다 — **게이트가
틀렸을 때 가장 비싼 방향**이다.

## 떨어져도 결과에는 남는다

탈락자는 랭킹에서 **분리**되지 그 자리에서 사라지지 않는다. 사유가 함께 실려 나가고
(`GateResult.reasons`), 랭킹은 step 7이 `rank=None`으로 목록 끝에 붙인다.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from ..model.graph import EvidenceGraph
from ..model.objects import Criterion, Evidence, Requirement, Resume, Score
from .mask import mask_sensitive
from .normalize import expand, key_terms, load_aliases, normalize_with_map

GATE_LAYER = "gate"


class GateResult(BaseModel):
    """0층 판정 한 벌. 통과 여부와 **사람이 읽는 탈락 사유**."""

    model_config = ConfigDict(extra="forbid")

    passed: bool
    failed_criteria: list[str]
    reasons: list[str]


def run_gates(
    resume: Resume,
    criteria: list[Criterion],
    graph: EvidenceGraph,
    *,
    aliases: dict[str, tuple[str, ...]] | None = None,
) -> GateResult:
    """게이트 항목을 판정한다. 게이트 항목이 없으면 **언제나 통과**다.

    판정마다 `Score`(layer=`gate`)를 그래프에 남긴다. 통과는 걸린 이력서 구간을
    `grounded_in`으로, 탈락은 조건을 `derived_from`으로 잇는다 — 후자가 검산 G1이
    게이트에 열어 둔 예외다 (`model/governance.py`).

    게이트 항목의 `weight`는 0이다 (`rubric/build.py`). 그래서 이 `Score`가 그래프에
    들어가도 총점에는 영향이 없다 — 통과/탈락은 점수가 아니라 **분리**다.
    """
    table = aliases if aliases is not None else load_aliases()
    masked, _ = mask_sensitive(resume.text)
    document = normalize_with_map(masked)

    failed: list[str] = []
    reasons: list[str] = []

    for criterion in criteria:
        if criterion.layer != GATE_LAYER:
            continue

        requirement = graph.get(criterion.requirement_id)
        requirement = requirement if isinstance(requirement, Requirement) else None
        text = requirement.text if requirement is not None else criterion.label
        terms = key_terms(text)

        hit_span = None
        hit_term = ""
        for term in terms:
            for spelling in expand(term, table):
                spans = document.find_all(spelling, limit=1)
                if spans:
                    hit_span, hit_term = spans[0], spelling
                    break
            if hit_span is not None:
                break

        passed = hit_span is not None
        if passed:
            rationale = f"게이트 조건에서 뽑은 표현 「{hit_term}」 — 이력서에서 찾았다."
        else:
            looked_for = ", ".join(terms) or "없음"
            rationale = (
                f"게이트 조건 「{criterion.label}」 — 이력서에서 해당하는 표현을 찾지 못해 "
                f"탈락으로 판정했다 (찾던 표현: {looked_for})."
            )
            failed.append(criterion.id)
            reasons.append(rationale)

        score = Score(
            id=f"S-{resume.candidate_id}-{criterion.id}",
            criterion_id=criterion.id,
            candidate_id=resume.candidate_id,
            value=1.0 if passed else 0.0,
            layer=GATE_LAYER,
            judge_id=None,
            rationale=rationale,
        )
        graph.add(score)

        if hit_span is not None:
            evidence = Evidence(
                id=f"E-{resume.candidate_id}-{criterion.id}-01",
                resume_id=resume.candidate_id,
                span=hit_span,
                quote=resume.text[hit_span.start : hit_span.end],
            )
            graph.add(evidence)
            graph.link(evidence.id, "supports", criterion.id)
            graph.link(score.id, "grounded_in", evidence.id)
        elif requirement is not None:
            graph.link(score.id, "derived_from", requirement.id)
        else:
            graph.link(score.id, "derived_from", criterion.id)

    return GateResult(passed=not failed, failed_criteria=failed, reasons=reasons)
