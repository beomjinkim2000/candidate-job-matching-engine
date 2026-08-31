"""검산 G1~G5 — 결과를 내보내기 전에 전부 통과해야 한다.

**테스트가 아니라 런타임 게이트다** (`src/CLAUDE.md`). 특히 G4는 과제 CRITICAL 규칙
(「공고 원문 텍스트 복사·붙여넣기 금지」)의 기계적 증명이다 — 좌표 없는 조건이 하나라도
있으면 그건 이미지에서 나온 것이 아니다.

G6(판단유탈 대장)·G7(승인 유효성)은 여기 없다. G6은 step 12의 `Claim`이, G7은 step 7의
승인 게이트가 각각 자기 자료구조를 갖고 판정한다. 이 파일은 그래프만으로 판정 가능한
다섯 개를 본다.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from .graph import EvidenceGraph
from .objects import EVIDENCE_GRADES, Evidence, Requirement


class Violation(BaseModel):
    """검산 위반 한 건. 어느 규칙이 어느 Object에서 깨졌는지."""

    model_config = ConfigDict(extra="forbid")

    rule: str  # "G1"
    object_id: str
    message: str


class GovernanceError(Exception):
    """검산 위반이 남은 채로 결과를 내보내려 할 때.

    위반 목록을 들고 다닌다 — 호출자가 다시 `check()`를 부르지 않아도 되게.
    """

    def __init__(self, violations: list[Violation]) -> None:
        self.violations = violations
        head = "; ".join(f"{v.rule} {v.object_id}: {v.message}" for v in violations[:5])
        more = f" (외 {len(violations) - 5}건)" if len(violations) > 5 else ""
        super().__init__(f"검산 위반 {len(violations)}건 — {head}{more}")


def check(graph: EvidenceGraph, resume_texts: dict[str, str]) -> list[Violation]:
    """그래프를 검산한다. 위반이 없으면 빈 목록.

    `resume_texts`는 `{resume_id: 이력서 원문}`이다. G2가 인용을 대조하는 데 쓴다.
    """
    index = graph.index()
    violations: list[Violation] = []

    # --- G1. 모든 Score에 grounded_in Link가 1개 이상 -----------------------
    # 예외: layer == "gate"인 Score는 탈락 판정이라 이력서 인용이 아니라 조건 자체에서
    # 나온다. `derived_from`으로 Requirement에 붙어 있으면 통과시킨다.
    for score in graph.scores:
        grounded = [
            link for link in graph.out(score.id, "grounded_in")
            if isinstance(index.get(link.dst), Evidence)
        ]
        if grounded:
            continue
        if score.layer == "gate":
            derived = [
                link for link in graph.out(score.id, "derived_from")
                if isinstance(index.get(link.dst), Requirement)
            ]
            if derived:
                continue
            violations.append(
                Violation(
                    rule="G1",
                    object_id=score.id,
                    message="게이트 점수인데 derived_from으로 이어진 조건이 없다",
                )
            )
            continue
        violations.append(
            Violation(
                rule="G1",
                object_id=score.id,
                message="grounded_in으로 이어진 Evidence가 없다 — 근거 없는 점수다",
            )
        )

    # --- G2. Evidence의 quote가 이력서 원문과 글자까지 일치 ------------------
    # **유사도·부분일치를 쓰지 않는다.** 이 검산의 목적이 「인용을 지어냈는가」를 잡는
    # 것인데, 느슨하게 비교하면 지어낸 인용이 그대로 통과한다.
    for ev in graph.evidence:
        text = resume_texts.get(ev.resume_id)
        if text is None:
            violations.append(
                Violation(
                    rule="G2",
                    object_id=ev.id,
                    message=f"이력서 원문이 없다: {ev.resume_id}",
                )
            )
            continue
        span = ev.span
        if span.start < 0 or span.end < span.start or span.end > len(text):
            violations.append(
                Violation(
                    rule="G2",
                    object_id=ev.id,
                    message=(
                        f"span이 원문 범위 밖이다: [{span.start}, {span.end}) / "
                        f"원문 {len(text)}자"
                    ),
                )
            )
            continue
        if not ev.quote:
            # 빈 인용은 어떤 span과도 맞아떨어진다. 아무것도 증명하지 않는다.
            violations.append(
                Violation(rule="G2", object_id=ev.id, message="인용이 비어 있다"),
            )
            continue
        actual = text[span.start : span.end]
        if actual != ev.quote:
            violations.append(
                Violation(
                    rule="G2",
                    object_id=ev.id,
                    message=f"인용이 원문과 다르다: 원문 「{actual}」 / 인용 「{ev.quote}」",
                )
            )

    # --- G3. 모든 Criterion에 derived_from Requirement 존재 ------------------
    for crit in graph.criteria:
        linked = [
            link.dst for link in graph.out(crit.id, "derived_from")
            if isinstance(index.get(link.dst), Requirement)
        ]
        if not linked:
            violations.append(
                Violation(
                    rule="G3",
                    object_id=crit.id,
                    message="derived_from으로 이어진 공고 조건이 없다 — 직군 하드코딩 흔적",
                )
            )
        elif crit.requirement_id not in linked:
            # 필드와 Link가 다른 조건을 가리키면 근거 문단이 실제와 다른 조건을 보여준다.
            violations.append(
                Violation(
                    rule="G3",
                    object_id=crit.id,
                    message=(
                        f"requirement_id({crit.requirement_id})가 "
                        f"derived_from Link({', '.join(linked)})와 다르다"
                    ),
                )
            )

    # --- G4. 모든 Requirement에 source_bbox 존재 ----------------------------
    # 필드가 required라 `None`은 검증을 건너뛴 경로에서만 생긴다. 더 현실적인 실패는
    # **넓이 0인 좌표로 필드만 채우는 것**이라 그것도 여기서 잡는다.
    for req in graph.requirements:
        bbox = getattr(req, "source_bbox", None)
        if bbox is None:
            violations.append(
                Violation(
                    rule="G4",
                    object_id=req.id,
                    message="source_bbox가 없다 — 이미지에서 나온 조건이 아니다",
                )
            )
        elif bbox.x2 <= bbox.x1 or bbox.y2 <= bbox.y1:
            violations.append(
                Violation(
                    rule="G4",
                    object_id=req.id,
                    message=(
                        f"source_bbox의 넓이가 0이다: "
                        f"({bbox.x1},{bbox.y1})-({bbox.x2},{bbox.y2})"
                    ),
                )
            )
        elif bbox.img_w <= 0 or bbox.img_h <= 0:
            violations.append(
                Violation(
                    rule="G4",
                    object_id=req.id,
                    message=f"기준 이미지 크기가 없다: {bbox.img_w}x{bbox.img_h}",
                )
            )

    # --- G5. 모든 Requirement에 evidence_grade 존재 -------------------------
    for req in graph.requirements:
        grade = getattr(req, "evidence_grade", None)
        if grade not in EVIDENCE_GRADES:
            violations.append(
                Violation(
                    rule="G5",
                    object_id=req.id,
                    message=f"근거 등급이 E2/E1/E0 중 하나가 아니다: {grade!r}",
                )
            )

    return violations


def enforce(graph: EvidenceGraph, resume_texts: dict[str, str]) -> None:
    """위반이 하나라도 있으면 GovernanceError를 던진다."""
    violations = check(graph, resume_texts)
    if violations:
        raise GovernanceError(violations)
