"""PP-StructureV3 — 표·레이아웃까지 읽는지 확인.

줄 단위 OCR은 표에서 셀 소속을 잃는다. 「Java 3년 이상」이 어느 모집분야 것인지
모르면 채점이 틀린다. 그래서 표를 표로 읽는 게 필요한지 여기서 본다.

    .venv/bin/python structure_check.py <이미지경로>
"""

import sys
import time
from collections import Counter

from paddleocr import PPStructureV3

path = sys.argv[1]

pipe = PPStructureV3(
    lang="korean",
    use_doc_orientation_classify=False,
    use_doc_unwarping=False,
    use_formula_recognition=False,
    use_chart_recognition=False,
)

started = time.perf_counter()
results = pipe.predict(path)
print(f"\n===== {time.perf_counter() - started:.1f}초 =====\n")

for page in results:
    d = page if isinstance(page, dict) else getattr(page, "json", {})
    d = d.get("res", d)

    layout = d.get("layout_det_res", {}).get("boxes", [])
    kinds = Counter(b.get("label") for b in layout)
    print("레이아웃 영역:", dict(kinds) or "(없음)")
    for b in layout:
        c = [round(v) for v in b.get("coordinate", [])]
        print(f"  {b.get('label'):<14} score={b.get('score', 0):.2f}  {c}")

    tables = d.get("table_res_list", [])
    print(f"\n표 {len(tables)}개")
    for i, t in enumerate(tables, 1):
        html = t.get("pred_html", "")
        rows = html.count("<tr")
        cols = html.split("</tr>")[0].count("<td") if "</tr>" in html else 0
        print(f"  표{i}: {rows}행 × 첫줄 {cols}칸 · 셀좌표 "
              f"{len(t.get('cell_box_list', []))}개")
        print("  " + html[:600].replace("\n", " "))
