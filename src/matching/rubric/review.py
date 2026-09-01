"""고객사 승인 — **`review_status`만 바꾼다. 점수는 건드리지 않는다.**

승인 여부로 점수가 움직이면 「승인 전 결과」와 「승인 후 결과」가 **다른 시스템**이 된다.
그러면 승인 화면에서 본 순위와 최종 순위가 달라도 아무도 이상하게 여기지 않는다.
승인이 바꾸는 것은 **표시**다 (`src/CLAUDE.md`: 「승인이 점수 계산식을 바꾸지 않는다」).

승인은 한 방향으로만 간다 — `draft → human_validated`. 되돌리지 않는 이유: 되돌리는 일은
「승인 취소」가 아니라 **항목 삭제·필수↔우대 뒤집기**이고, 그 둘은 판정이 바뀌므로
`contradicts` Link로 원래 판정을 남겨야 한다 (step 7). 여기서 조용히 내리면 그 기록이 없다.
"""

from __future__ import annotations

from ..model.objects import Criterion


def apply_approval(criteria: list[Criterion], approvals: dict[str, bool]) -> list[Criterion]:
    """승인된 항목만 `review_status`를 `human_validated`로 올린다.

    **원본을 고치지 않고 새 목록을 만든다** — 승인 전후를 나란히 두고 「무엇이 바뀌었나」를
    보여줄 수 있어야 하기 때문이다. `weight`·`anchors`는 복사되어 그대로 남는다.
    """
    return [
        criterion.model_copy(update={"review_status": "human_validated"})
        if approvals.get(criterion.id)
        else criterion
        for criterion in criteria
    ]


def pending(criteria: list[Criterion]) -> list[Criterion]:
    """아직 `draft`인 항목. UI가 승인 화면에 띄운다."""
    return [criterion for criterion in criteria if criterion.review_status == "draft"]
