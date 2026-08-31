"""PaddleOCR 한국어 품질 확인.

EasyOCR이 실제로 돌려보니 못 썼다. 알려진 평판이 아니라 같은 이미지로 직접 본다.

    .venv/bin/python paddle_check.py <이미지경로>
출력: 줄마다  신뢰도 \t x0,y0,x1,y1 \t 텍스트
"""

import sys
import time

from paddleocr import PaddleOCR

path = sys.argv[1]

ocr = PaddleOCR(lang="korean", use_doc_orientation_classify=False,
                use_doc_unwarping=False, use_textline_orientation=False)

started = time.perf_counter()
raw = ocr.predict(path)
elapsed = time.perf_counter() - started

lines = []
for page in raw:
    # 3.x 는 dict, 2.x 는 [[box, (text, score)], ...]
    if isinstance(page, dict):
        texts = page.get("rec_texts", [])
        scores = page.get("rec_scores", [])
        boxes = page.get("rec_polys", page.get("dt_polys", []))
        for text, score, box in zip(texts, scores, boxes):
            lines.append((text, float(score), box))
    else:
        for box, (text, score) in page:
            lines.append((text, float(score), box))


def rect(box):
    xs = [float(p[0]) for p in box]
    ys = [float(p[1]) for p in box]
    return int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))


lines.sort(key=lambda t: (rect(t[2])[1], rect(t[2])[0]))
avg = sum(s for _, s, _ in lines) / len(lines) if lines else 0
print(f"줄 {len(lines)}개 · 평균신뢰 {avg:.3f} · {elapsed:.1f}초\n")
for text, score, box in lines:
    x0, y0, x1, y1 = rect(box)
    print(f"{score:.2f}\t{x0},{y0},{x1},{y1}\t{text}")
