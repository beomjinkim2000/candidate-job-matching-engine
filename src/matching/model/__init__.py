"""근거 모델 — 파이프라인 전체가 이 타입만 주고받는다.

- `objects` — Object 정의 (Requirement · Criterion · Evidence · Score · Link)
- `graph` — Object를 담고 잇고 따라가는 `EvidenceGraph`
- `governance` — 검산 G1~G5 (런타임 게이트)
- `render` — 근거 문단을 그래프에서 만들어낸다
"""

from .governance import GovernanceError, Violation, check, enforce
from .graph import TRACE_CHAIN, EvidenceGraph
from .objects import (
    EVIDENCE_GRADES,
    RELATIONS,
    REQUIREMENT_BRANCHES,
    BBox,
    Criterion,
    Evidence,
    EvidenceGrade,
    GraphObject,
    Link,
    Relation,
    Requirement,
    RequirementBranch,
    RequirementKind,
    Resume,
    ReviewStatus,
    Score,
    ScoreLayer,
    Span,
)
from .render import render_rationale

__all__ = [
    "EVIDENCE_GRADES",
    "RELATIONS",
    "REQUIREMENT_BRANCHES",
    "TRACE_CHAIN",
    "BBox",
    "Criterion",
    "Evidence",
    "EvidenceGrade",
    "EvidenceGraph",
    "GovernanceError",
    "GraphObject",
    "Link",
    "Relation",
    "Requirement",
    "RequirementBranch",
    "RequirementKind",
    "Resume",
    "ReviewStatus",
    "Score",
    "ScoreLayer",
    "Span",
    "Violation",
    "check",
    "enforce",
    "render_rationale",
]
