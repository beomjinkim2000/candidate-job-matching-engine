"""근거 문단 렌더링 — 저장하지 않고 그래프에서 만들어낸다.

저장된 문장은 그래프와 어긋날 수 있다. 어긋나면 사람은 문장을 믿고, 검산은 그래프를
보므로 **둘 다 통과하면서 서로 다른 말을 하는 상태**가 된다. 그래서 문단은 매번
`trace()`에서 다시 만든다.

`Score.rationale`(채점자가 쓴 문장)은 지우지 않고 문단 끝에 **따로 표시해** 붙인다 —
그건 근거가 아니라 채점자의 서술이고, 둘을 섞으면 무엇이 검증된 것인지 사라진다.
"""

from __future__ import annotations

from .graph import EvidenceGraph
from .objects import BBox, Criterion, Evidence, Requirement, Score

_LAYER_LABEL = {"gate": "0층 게이트", "fact": "1층 사실 채점", "judgment": "2층 판단 채점"}
_KIND_LABEL = {"required": "필수", "preferred": "우대", "gate": "게이트"}
_STATUS_LABEL = {"draft": "AI 초안", "human_validated": "사람 확인함"}


def _bbox_text(bbox: BBox) -> str:
    return (
        f"{bbox.page}쪽 ({bbox.x1},{bbox.y1})-({bbox.x2},{bbox.y2}), "
        f"기준 이미지 {bbox.img_w}x{bbox.img_h}px"
    )


def render_rationale(graph: EvidenceGraph, score_id: str) -> str:
    """Score 하나의 근거를 사람이 읽는 한 문단으로 만든다."""
    index = graph.index()
    score = index.get(score_id)
    if not isinstance(score, Score):
        raise ValueError(f"Score를 찾을 수 없다: {score_id}")

    # 항목은 Score의 필드에서 바로 찾는다. trace를 타면 Evidence가 여러 Criterion을
    # 지지할 때 엉뚱한 항목이 잡힐 수 있다 — 점수가 매겨진 항목은 언제나 이 하나다.
    criterion = index.get(score.criterion_id)
    criterion = criterion if isinstance(criterion, Criterion) else None

    evidences: list[Evidence] = []
    requirements: list[Requirement] = []
    for link in graph.trace(score_id):
        target = index.get(link.dst)
        if isinstance(target, Evidence) and target not in evidences:
            evidences.append(target)
        elif isinstance(target, Requirement) and target not in requirements:
            requirements.append(target)

    lines: list[str] = []

    label = criterion.label if criterion else score.criterion_id
    who = f", 심사위원 {score.judge_id}" if score.judge_id else ""
    lines.append(
        f"[{score.criterion_id}] {label} — {score.value:g}점 "
        f"({_LAYER_LABEL.get(score.layer, score.layer)}{who}, 지원자 {score.candidate_id})."
    )

    if evidences:
        quotes = " / ".join(
            f"{ev.resume_id}의 {ev.span.start}~{ev.span.end}번째 글자 「{ev.quote}」"
            for ev in evidences
        )
        lines.append(f"근거로 삼은 이력서 구간: {quotes}.")
    elif score.layer == "gate":
        lines.append("게이트 판정이라 이력서 인용이 아니라 조건 충족 여부로 결론이 났다.")
    else:
        lines.append("근거로 이어진 이력서 구간이 없다 (검산 G1 위반 상태).")

    if requirements:
        for req in requirements:
            kind = _KIND_LABEL.get(req.kind, req.kind)
            status = _STATUS_LABEL.get(req.review_status, req.review_status)
            lines.append(
                f"이 항목은 공고 조건 {req.id} 「{req.text}」에서 나왔다 "
                f"({kind}, 근거등급 {req.evidence_grade}, "
                f"판정 사다리 {req.ladder_step}단계, {status})."
            )
            bbox = getattr(req, "source_bbox", None)
            if bbox is not None:
                lines.append(f"그 조건을 읽은 자리: 공고 이미지 {_bbox_text(bbox)}.")
    else:
        lines.append("공고 조건까지 이어지는 경로가 없다 (검산 G3 위반 상태).")

    if criterion is not None:
        anchor = criterion.anchors.get(round(score.value))
        if anchor:
            lines.append(f"{round(score.value)}점 기준점: 「{anchor}」.")

    if score.rationale:
        lines.append(f"채점자 서술: 「{score.rationale}」")

    return " ".join(lines)
