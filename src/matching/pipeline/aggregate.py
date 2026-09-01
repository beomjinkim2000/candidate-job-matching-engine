"""층별 점수 → **0~100점 하나.** 이 층에는 LLM이 없다 — 결정적이어야 한다.

## 1점이 0점이 되는 것에 주의한다

5점 척도의 1점은 「관련 경험이 없거나, 있어도 구체적 행동 서술이 없음」이다
(`src/CLAUDE.md`의 기준점 패턴). 그래서 판단 층 정규화는 **`(raw - 1) / 4`**다.

`raw / 5`로 하면 **최하점에도 20%가 붙는다.** 그러면 항목이 많은 공고일수록 아무 관련
경험이 없는 지원자의 총점이 올라가고, 「관련 경험 없음」과 「조금 있음」의 간격이
「조금 있음」과 「명확함」의 간격보다 좁아진다. 척도의 뜻이 바뀌는 것이다.

사실 층은 `raw`가 이미 0~1(커버리지·포화함수 값)이라 그대로 쓴다.

## 만점은 언제나 100이다

`build_rubric`이 항목 수와 무관하게 가중치 총합을 100으로 맞춰 놓았다. 이 파일이 할 일은
그 계약을 **깨지 않는 것**이다. 그래서 채점되지 않은 항목이 하나라도 있으면 예외를
던진다 — 그 항목만 빼면 그 지원자의 만점이 남들과 달라져 **등수 비교가 성립하지 않는다.**
조용히 빼면 채점에 실패한 지원자가 오히려 높은 비율을 받는 일까지 생긴다.

## 게이트 판정을 여기서 다시 내리지 않는다

`GateResult`는 `run_gates()`가 이미 내린 판정이고, 그 결과는 `layer == "gate"`인 `Score`로
그래프에 남아 있다. 여기서는 **그것을 읽어 옮길 뿐**이다. 판정 자리를 두 곳에 두면 한쪽만
고쳐 놓고 고쳤다고 생각하게 된다 (`scorer/gate.py`와 같은 이유).
"""

from __future__ import annotations

from math import fsum

from pydantic import BaseModel, ConfigDict

from ..config import Settings
from ..judge.schema import SCALE_MAX, SCALE_MIN
from ..model.graph import EvidenceGraph
from ..model.objects import (
    Criterion,
    Evidence,
    EvidenceGrade,
    Requirement,
    ReviewStatus,
    Score,
    ScoreLayer,
)
from ..model.render import render_rationale
from ..scorer.gate import GATE_LAYER, GateResult

JUDGMENT_LAYER = "judgment"

# 5점 척도를 0~1로 옮길 때의 분모. `SCALE_MAX - SCALE_MIN`이지 `SCALE_MAX`가 아니다.
_SCALE_RANGE = float(SCALE_MAX - SCALE_MIN)


class AggregateError(RuntimeError):
    """층별 점수를 하나로 합칠 수 없다. **부분 합계를 내지 않는다.**"""


class AxisScore(BaseModel):
    """축(=루브릭 항목) 하나의 결과. 화면의 한 줄이 이것 하나다.

    `evidence_grade`와 `review_status`가 여기 실려 있는 것이 설계의 요점이다 —
    **점수만 보여주면 그 점수가 무엇에 근거했고 누가 확인한 것인지 사라진다.**
    등급은 점수에 곱하지 않는다 (`src/CLAUDE.md`). 곱하면 점수가 낮은 이유가
    적합도 때문인지 근거 부족 때문인지 구분이 없어진다.
    """

    model_config = ConfigDict(extra="forbid")

    criterion_id: str
    label: str
    layer: ScoreLayer
    raw: float  # fact 0~1 / judgment 1~5 / gate 1.0=통과 0.0=탈락
    weighted: float  # 가중 적용 후 점수
    max_weighted: float  # 이 축의 만점 (= Criterion.weight)
    rationale: str  # render_rationale()의 결과. **저장된 문장이 아니다**
    evidence_ids: list[str]
    evidence_grade: EvidenceGrade  # 조건의 근거 등급 (표시용)
    review_status: ReviewStatus  # 항목의 승인 상태 (표시용)


class CandidateResult(BaseModel):
    """지원자 한 명의 최종 결과.

    `rank`가 `None`이면 게이트 탈락자다 — 점수가 없는 것이 아니라 **랭킹에서 분리된
    것**이고, 사유는 `gate.reasons`에 문장으로 남는다.
    """

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    total: float  # 0~100
    rank: int | None  # 탈락자는 None
    gate: GateResult
    breakdown: list[AxisScore]
    graph_ref: str  # 이 결과의 근거 그래프를 어디서 찾는가 (`RunResult.run_id`)


def normalize_raw(layer: str, raw: float) -> float:
    """원점수를 0~1로. **판단 층만 척도가 다르다.**

    1점 → 0.0, 3점 → 0.5, 5점 → 1.0. 이 함수를 `raw / SCALE_MAX`로 고치지 마라 —
    이유는 모듈 docstring에 있다.
    """
    if layer == JUDGMENT_LAYER:
        unit = (raw - SCALE_MIN) / _SCALE_RANGE
    else:
        unit = raw
    return min(max(unit, 0.0), 1.0)


def _gate_result(breakdown: list[AxisScore]) -> GateResult:
    """게이트 축만 골라 `run_gates()`의 판정을 그대로 옮긴다.

    게이트 항목이 하나도 없으면 **통과**다 (`scorer/gate.py`의 계약과 같다).
    """
    failed = [axis.criterion_id for axis in breakdown if axis.layer == GATE_LAYER and axis.raw <= 0]
    reasons = [
        axis.rationale for axis in breakdown if axis.layer == GATE_LAYER and axis.raw <= 0
    ]
    return GateResult(passed=not failed, failed_criteria=failed, reasons=reasons)


def aggregate(
    scores: list[Score],
    criteria: list[Criterion],
    graph: EvidenceGraph,
    settings: Settings | None = None,
    *,
    graph_ref: str = "",
) -> CandidateResult:
    """지원자 한 명의 층별 점수를 0~100점 하나로 합친다.

    `scores`는 **한 지원자의 것만** 받는다. 섞여 오면 예외를 던진다 — 조용히 걸러내면
    호출부가 잘못 넘긴 것을 아무도 못 본다.

    `settings`는 **읽지 않는다.** 배점(사실 35 / 판단 65)은 `build_rubric`이 이미
    `Criterion.weight`에 녹여 놓았고, 여기서 다시 읽으면 배점이 사는 자리가 둘이 된다.
    자리를 남겨 둔 것은 호출부의 모양이 `step7.md` 명세 그대로이기 때문이다.

    `Score.rationale`(채점자가 쓴 문장)을 근거로 쓰지 않는다. `AxisScore.rationale`은
    `render_rationale()`이 **그래프를 따라가** 만든 문단이다 (`model/render.py`).
    """
    candidates = {score.candidate_id for score in scores}
    if len(candidates) != 1:
        found = ", ".join(sorted(candidates)) or "없음"
        raise AggregateError(f"한 지원자의 점수만 받는다 — 받은 지원자: {found}")
    candidate_id = candidates.pop()

    by_criterion: dict[str, Score] = {}
    for score in scores:
        if score.criterion_id in by_criterion:
            raise AggregateError(
                f"{candidate_id} / {score.criterion_id}에 점수가 둘이다 — "
                "어느 쪽이 최종인지 알 수 없다"
            )
        by_criterion[score.criterion_id] = score

    index = graph.index()
    breakdown: list[AxisScore] = []
    for criterion in criteria:
        score = by_criterion.pop(criterion.id, None)
        if score is None:
            # **조용히 빼지 않는다.** 뺀 항목만큼 그 지원자의 만점이 줄어 등수 비교가
            # 성립하지 않는다 (모듈 docstring).
            raise AggregateError(
                f"{candidate_id}: 항목 {criterion.id}({criterion.label})에 점수가 없다 — "
                "만점이 지원자마다 달라지므로 부분 합계를 내지 않는다"
            )

        requirement = index.get(criterion.requirement_id)
        if not isinstance(requirement, Requirement):
            # 여기 오면 검산 G3이 먼저 막았어야 한다.
            raise AggregateError(
                f"{criterion.id}의 공고 조건({criterion.requirement_id})이 그래프에 없다 — "
                "검산 G3이 먼저 막았어야 한다"
            )

        unit = normalize_raw(criterion.layer, score.value)
        breakdown.append(
            AxisScore(
                criterion_id=criterion.id,
                label=criterion.label,
                layer=criterion.layer,
                raw=score.value,
                weighted=round(criterion.weight * unit, 6),
                max_weighted=round(criterion.weight, 6),
                rationale=render_rationale(graph, score.id),
                evidence_ids=[
                    link.dst
                    for link in graph.out(score.id, "grounded_in")
                    if isinstance(index.get(link.dst), Evidence)
                ],
                evidence_grade=requirement.evidence_grade,
                review_status=criterion.review_status,
            )
        )

    if by_criterion:
        # 루브릭에 없는 항목의 점수가 남았다. 루브릭이 바뀐 뒤의 옛 점수일 수 있다.
        extra = ", ".join(sorted(by_criterion))
        raise AggregateError(f"{candidate_id}: 루브릭에 없는 항목의 점수가 남았다 — {extra}")

    return CandidateResult(
        candidate_id=candidate_id,
        total=round(fsum(axis.weighted for axis in breakdown), 6),
        rank=None,  # 랭킹은 `rank()`가 매긴다. 혼자서는 자기 등수를 모른다
        gate=_gate_result(breakdown),
        breakdown=breakdown,
        graph_ref=graph_ref,
    )


def layer_total(result: CandidateResult, layer: str) -> float:
    """한 층이 실제로 받은 점수 합. 동점 처리와 화면 표시에 쓴다."""
    return round(fsum(axis.weighted for axis in result.breakdown if axis.layer == layer), 6)


def layer_max(result: CandidateResult, layer: str) -> float:
    """한 층의 만점 합."""
    return round(fsum(axis.max_weighted for axis in result.breakdown if axis.layer == layer), 6)
