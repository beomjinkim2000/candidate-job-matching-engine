"""루브릭 조립 — 공고 조건에서 채점 항목을 만든다.

- `anchors` — 기준점 패턴. **루브릭에서 고정된 것은 이것뿐이다**
- `build` — 조건·담당업무 → `Criterion` 목록 (층 배정 · 가중치 배분 · `derived_from` Link)
- `review` — 고객사 승인. `review_status`만 바꾼다

**LLM을 부르지 않는다.** 조립은 전부 산술과 문자열 규칙이다 — 루브릭 생성과 채점을 같은
모델에게 맡기면 자기가 만족시킬 수 있는 관대한 기준을 만든다는 보고가 있다
(`docs/RUBRIC_GENERATION_EVIDENCE.md` §3-3).
"""

from .anchors import ANCHOR_LEVELS, ANCHOR_TEMPLATE, make_anchors
from .build import (
    GATE_MARKERS,
    TOTAL_POINTS,
    assign_layer,
    build_rubric,
    is_countable,
    is_gate,
    make_label,
)
from .review import apply_approval, pending

__all__ = [
    "ANCHOR_LEVELS",
    "ANCHOR_TEMPLATE",
    "GATE_MARKERS",
    "TOTAL_POINTS",
    "apply_approval",
    "assign_layer",
    "build_rubric",
    "is_countable",
    "is_gate",
    "make_anchors",
    "make_label",
    "pending",
]
