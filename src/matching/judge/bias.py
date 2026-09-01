"""순서 편향 점검 — **기본 실행에서는 돌지 않는다.**

지원자 제시 순서를 뒤집어 한 번 더 채점하고 순위가 바뀌었는지 보고한다. 호출 횟수가
2배가 되므로 **채점 경로가 이 함수를 부르지 않는다** — `judge_criterion()`에서 여기로
가는 길이 아예 없다. 켜는 스위치는 진입점이 보는 `settings.judge_order_check`이고,
그 기본값은 `False`다 ($5 예산).

## 이 점검이 실제로 재는 것

**우리 설계에서는 한 호출에 지원자가 한 명뿐이다.** 그래서 「먼저 제시된 쪽을 선호하는
편향」이 들어올 자리가 프롬프트에 아예 없다 — 순서가 프롬프트에 존재하지 않으므로
구조적으로 순서 불변이다.

그러면 이 점검은 무엇을 재는가. **같은 프롬프트를 다시 보냈을 때 같은 답이 오는가**다.
`temperature=0`이라도 재현이 보장되지 않으므로, 순위가 흔들리면 그것은 순서 편향이
아니라 **모델의 잔여 비결정성**이다. 둘을 구별해 적어 두는 것이 이 파일의 요지다 —
「순서 편향 없음」이라고만 쓰면 무엇을 확인했는지가 사라진다.

한 호출에 여러 지원자를 넣는 설계로 바꾸는 순간 이 점검의 의미가 달라진다. 그때는 이
docstring부터 고쳐야 한다.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from ..config import Settings
from ..model.graph import EvidenceGraph
from ..model.objects import Criterion, Resume
from .panel import CallBudget, judge_criterion
from .prompt import ScoringExample


class OrderCheckResult(BaseModel):
    """순서를 뒤집은 두 번의 채점 결과와 그 차이."""

    model_config = ConfigDict(extra="forbid")

    criterion_id: str
    forward: dict[str, float]  # candidate_id → 점수 (주어진 순서)
    reverse: dict[str, float]  # candidate_id → 점수 (뒤집은 순서)
    forward_order: list[str]  # 점수 높은 순
    reverse_order: list[str]
    rank_changes: int  # 두 순위에서 자리가 다른 지원자 수
    stable: bool  # 순위 변동 0건인가
    calls: int  # 이 점검이 쓴 호출 수


def _ranking(scores: dict[str, float]) -> list[str]:
    """점수 내림차순. 동점은 `candidate_id` 순 — **임의지만 재현 가능해야 한다.**"""
    return sorted(scores, key=lambda cid: (-scores[cid], cid))


def order_check(
    criterion: Criterion,
    candidates: Sequence[Resume],
    settings: Settings,
    client,
    *,
    examples: Sequence[ScoringExample] | None = None,
    budget: CallBudget | None = None,
) -> OrderCheckResult:
    """주어진 순서와 뒤집은 순서로 각각 채점하고 순위 변동을 보고한다.

    두 번의 채점은 **각각 새 그래프**에 담는다. 같은 그래프에 담으면 같은 지원자·항목의
    `Score` id가 겹쳐 두 번째가 거부된다 — 그래프는 한 실행의 판단을 담는 자리이지
    같은 판단을 두 번 담는 자리가 아니다.
    """
    active_budget = budget if budget is not None else CallBudget(settings)
    before = active_budget.calls

    def sweep(order: Sequence[Resume]) -> dict[str, float]:
        graph = EvidenceGraph()
        return {
            resume.candidate_id: judge_criterion(
                criterion,
                resume,
                resume.text,
                graph,
                settings,
                client,
                examples=examples,
                budget=active_budget,
            ).value
            for resume in order
        }

    forward = sweep(list(candidates))
    reverse = sweep(list(reversed(candidates)))

    forward_order = _ranking(forward)
    reverse_order = _ranking(reverse)
    changes = sum(1 for a, b in zip(forward_order, reverse_order, strict=True) if a != b)

    return OrderCheckResult(
        criterion_id=criterion.id,
        forward=forward,
        reverse=reverse,
        forward_order=forward_order,
        reverse_order=reverse_order,
        rank_changes=changes,
        stable=changes == 0,
        calls=active_budget.calls - before,
    )
