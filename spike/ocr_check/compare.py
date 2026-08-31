"""EasyOCR을 배율별로 돌려서 어디서 읽히기 시작하는지 본다.

    .venv/bin/python compare.py <이미지경로>
"""

import sys
import time

import numpy as np
from PIL import Image

path = sys.argv[1]
base = Image.open(path).convert("RGB")
print(f"원본 {base.size}\n")

import easyocr  # noqa: E402

reader = easyocr.Reader(["ko", "en"], gpu=False)

for scale in (1, 2, 3):
    img = base if scale == 1 else base.resize(
        (base.width * scale, base.height * scale), Image.LANCZOS
    )
    started = time.perf_counter()
    result = reader.readtext(np.array(img))
    elapsed = time.perf_counter() - started

    confs = [float(c) for _, _, c in result]
    heights = sorted(max(p[1] for p in b) - min(p[1] for p in b) for b, _, _ in result)
    med_h = heights[len(heights) // 2] / scale if heights else 0

    print(f"===== {scale}배 ({img.size}) — 줄 {len(result)}개 · "
          f"평균신뢰 {sum(confs)/len(confs):.3f} · 원본기준 중앙높이 {med_h:.1f}px · "
          f"{elapsed:.1f}초 =====")
    for _, text, conf in result[:25]:
        print(f"  {conf:.2f}  {text}")
    print()
