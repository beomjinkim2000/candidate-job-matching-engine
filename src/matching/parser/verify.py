"""파싱 검증 — **코드가 한다. LLM이 아니다.**

같은 모델에게 「네가 읽은 게 맞냐」고 물으면 **같은 실수를 두 번 한다**
(`docs/TRADEOFFS.md` E-1). 그리고 기본 경로에서 조건은 **OCR이 읽은 줄에서만** 나오므로,
검증이 유사도 판단이 아니라 **문자열 동일성**으로 끝난다. 그게 이 파일이 짧은 이유다.

검산 G4는 「좌표가 있는가」만 본다. 여기는 그 좌표가 **가리키는 곳에 실제로 그 글자가
있는가**를 본다. 둘이 합쳐져야 「원문을 복붙하지 않았다」가 증명된다 — 좌표를 0으로
채우고 텍스트를 지어내는 실패는 G4를 통과하고 여기서 걸린다.
"""

from __future__ import annotations

from ..model.governance import Violation
from ..model.objects import BBox
from .classify import RequirementRecord
from .ocr import LINE_SEP, OcrResult


def union_bbox(boxes: list[BBox]) -> BBox:
    """줄들의 합집합 박스. 항목이 여러 줄이면 그 줄들을 다 덮는 사각형이 좌표다."""
    if not boxes:
        raise ValueError("합칠 좌표가 없다 — 좌표를 못 만드는 항목은 버린다 (G4)")
    head = boxes[0]
    return BBox(
        page=head.page,
        x1=min(box.x1 for box in boxes),
        y1=min(box.y1 for box in boxes),
        x2=max(box.x2 for box in boxes),
        y2=max(box.y2 for box in boxes),
        img_w=head.img_w,
        img_h=head.img_h,
    )


def verify(requirements: list[RequirementRecord], ocr: OcrResult) -> list[Violation]:
    """위반 목록. 빈 목록이면 통과."""
    lines = {line.id: line for line in ocr.lines}
    document = ocr.document()
    violations: list[Violation] = []

    for req in requirements:
        ids = list(req.line_ids)
        if not ids:
            violations.append(
                Violation(rule="P0", object_id=req.id, message="line_ids가 비었다 — 역참조가 없다")
            )
            continue

        missing = [line_id for line_id in ids if line_id not in lines]
        if missing:
            violations.append(
                Violation(
                    rule="P0",
                    object_id=req.id,
                    message=f"OCR에 없는 줄을 가리킨다: {', '.join(missing)}",
                )
            )
            continue

        used = [lines[line_id] for line_id in ids]

        # --- P1. 조건 문구가 그 줄들 안에 실재하는가 -------------------------
        joined = LINE_SEP.join(line.text for line in used)
        if req.text not in joined:
            violations.append(
                Violation(
                    rule="P1",
                    object_id=req.id,
                    message=f"조건 문구가 가리킨 줄에 없다: 「{req.text[:30]}」",
                )
            )

        # --- P2. span으로 자른 것이 문구와 **글자까지** 같은가 ---------------
        # 유사도를 쓰지 않는다. 느슨하게 비교하면 지어낸 문구가 그대로 통과한다.
        span = req.source_span
        if span is None:
            violations.append(
                Violation(rule="P2", object_id=req.id, message="source_span이 없다")
            )
        elif span.start < 0 or span.end > len(document) or span.end < span.start:
            violations.append(
                Violation(
                    rule="P2",
                    object_id=req.id,
                    message=(
                        f"span이 OCR 문서 범위 밖이다: "
                        f"[{span.start}, {span.end}) / {len(document)}자"
                    ),
                )
            )
        elif document[span.start : span.end] != req.text:
            violations.append(
                Violation(
                    rule="P2",
                    object_id=req.id,
                    message=(
                        f"span이 가리키는 글자가 다르다: "
                        f"원문 「{document[span.start : span.end][:20]}」 / "
                        f"조건 「{req.text[:20]}」"
                    ),
                )
            )

        # --- P3. 좌표가 그 줄들의 합집합인가 ---------------------------------
        expected = union_bbox([line.bbox for line in used])
        if req.source_bbox != expected:
            violations.append(
                Violation(
                    rule="P3",
                    object_id=req.id,
                    message=(
                        f"좌표가 줄들의 합집합과 다르다: "
                        f"기록 ({req.source_bbox.x1},{req.source_bbox.y1})-"
                        f"({req.source_bbox.x2},{req.source_bbox.y2}) / "
                        f"실제 ({expected.x1},{expected.y1})-({expected.x2},{expected.y2})"
                    ),
                )
            )

    return violations
