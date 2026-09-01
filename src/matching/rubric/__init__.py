"""루브릭 조립 — 공고 조건에서 채점 항목을 만든다.

- `anchors` — 기준점 패턴. **루브릭에서 고정된 것은 이것뿐이다** (서술형 · 충족형 둘)
- `branch` — 조건이 이력서에서 **어떻게 확인되는가**(`term`/`binary`/`graded`).
  **이 모듈에서 LLM을 부르는 유일한 자리**이고, 못 부르면 옛 글자 모양 규칙으로 떨어진다
- `build` — 조건·담당업무 → `Criterion` 목록 (층 배정 · 가중치 배분 · `derived_from` Link)
- `review` — 고객사 승인. `review_status`만 바꾼다

**루브릭 조립은 LLM을 부르지 않는다.** 항목·가중치·기준점은 전부 산술과 문자열 규칙에서
나온다 — 루브릭 생성과 채점을 같은 모델에게 맡기면 자기가 만족시킬 수 있는 관대한 기준을
만든다는 보고가 있다 (`docs/RUBRIC_GENERATION_EVIDENCE.md` §3-3).

**`branch`만 예외이고, 그것도 기준을 만들지 않는다.** 묻는 것은 「이 조건을 이력서에서
어떻게 확인하나」 하나뿐이라 답이 정하는 것은 **어느 잣대를 쓸지**이지 잣대의 눈금이
아니다. 눈금(기준점·가중치)은 여전히 코드에 고정돼 있다.
"""

from .anchors import (
    ANCHOR_LEVELS,
    ANCHOR_TEMPLATE,
    ANCHOR_TEMPLATES,
    SATISFACTION_TEMPLATE,
    make_anchors,
)
from .branch import (
    BRANCHES_FILENAME,
    BranchError,
    BranchResult,
    classify_branches,
    fallback_branch,
    resolve_branches,
)
from .build import (
    BRANCH_LAYERS,
    GATE_MARKERS,
    TOTAL_POINTS,
    assign_layer,
    branch_of,
    build_rubric,
    is_countable,
    is_gate,
    make_label,
)
from .review import apply_approval, pending

__all__ = [
    "ANCHOR_LEVELS",
    "ANCHOR_TEMPLATE",
    "ANCHOR_TEMPLATES",
    "BRANCHES_FILENAME",
    "BRANCH_LAYERS",
    "GATE_MARKERS",
    "SATISFACTION_TEMPLATE",
    "TOTAL_POINTS",
    "BranchError",
    "BranchResult",
    "apply_approval",
    "assign_layer",
    "branch_of",
    "build_rubric",
    "classify_branches",
    "fallback_branch",
    "is_countable",
    "is_gate",
    "make_anchors",
    "make_label",
    "pending",
    "resolve_branches",
]
