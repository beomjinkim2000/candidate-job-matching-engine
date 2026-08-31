"""OCR 확인용 스파이크.

목적은 하나 — **공고 이미지에서 줄과 좌표가 나오는지, 그 좌표로 구조가 갈리는지**를
눈으로 보는 것. 채점도 루브릭도 여기 없다. 검증이 끝나면 규칙만 `src/parser/`로 옮긴다.

엔진 둘을 같은 화면에서 비교한다:
  paddle — PaddleOCR. 윈도우·맥·리눅스 전부 `pip` 하나로 돈다. **기본값**
  vision — macOS 내장 Vision. 맥에서만. 띄어쓰기가 살아있다

EasyOCR은 뺐다. 같은 이미지에서 평균 신뢰도 0.396, 3배로 키워도 안 고쳐졌다.

    .venv/bin/python -m uvicorn app:app --port 8010
"""

import io
import os
import subprocess
import tempfile
import time
from functools import lru_cache
from pathlib import Path

# PaddleOCR 은 기본값이 전 코어다. 그대로 두면 맥이 멈춘다 (2026-09-01에 실제로 멈췄다).
# paddle import 보다 먼저 걸어야 먹는다.
for _v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ.setdefault(_v, "2")

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image

HERE = Path(__file__).parent
STATIC = HERE / "static"

app = FastAPI(title="OCR 확인용 스파이크")


# ---------------------------------------------------------------- PaddleOCR

@lru_cache(maxsize=1)
def get_paddle():
    """첫 호출에서 한국어 모델을 내려받는다 (한 번만)."""
    from paddleocr import PaddleOCR

    return PaddleOCR(
        lang="korean",
        cpu_threads=2,          # 전 코어를 쓰게 두면 맥이 멈춘다
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
    )


def run_paddle(path: Path) -> list[tuple[str, float, list]]:
    out = []
    for page in get_paddle().predict(str(path)):
        if isinstance(page, dict):  # 3.x
            boxes = page.get("rec_polys") or page.get("dt_polys") or []
            for text, score, box in zip(page.get("rec_texts", []),
                                        page.get("rec_scores", []), boxes):
                out.append((text, float(score), box))
        else:  # 2.x
            for box, (text, score) in page:
                out.append((text, float(score), box))
    return out


# ------------------------------------------------------------ macOS Vision

@lru_cache(maxsize=1)
def vision_binary() -> Path:
    """swift 파일을 한 번만 컴파일해 둔다 (매번 컴파일하면 3초씩 든다)."""
    src, out = HERE / "vision_ocr.swift", HERE / ".vision_ocr"
    if not out.exists() or out.stat().st_mtime < src.stat().st_mtime:
        subprocess.run(["swiftc", "-O", str(src), "-o", str(out)], check=True)
    return out


def run_vision(path: Path) -> list[tuple[str, float, list]]:
    proc = subprocess.run([str(vision_binary()), str(path)],
                          capture_output=True, text=True, check=True)
    out = []
    for line in proc.stdout.splitlines():
        text, conf, box = line.rsplit("\t", 2)
        x0, y0, x1, y1 = (int(v) for v in box.split(","))
        out.append((text, float(conf), [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]))
    return out


ENGINES = {"paddle": run_paddle, "vision": run_vision}


# ------------------------------------------------------------------- 라우트

@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.post("/ocr")
async def ocr(file: UploadFile = File(...), engine: str = Form("paddle")):
    if engine not in ENGINES:
        return JSONResponse({"error": f"모르는 엔진: {engine}"}, status_code=400)

    raw = await file.read()
    img = Image.open(io.BytesIO(raw)).convert("RGB")

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        img.save(tmp.name)
        path = Path(tmp.name)
    try:
        started = time.perf_counter()
        result = ENGINES[engine](path)
        elapsed = time.perf_counter() - started
    finally:
        path.unlink(missing_ok=True)

    lines = []
    for text, conf, box in result:
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
        x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        lines.append({
            "text": text,
            "conf": round(conf, 3),
            "bbox": [round(x0), round(y0), round(x1), round(y1)],
            "height": round(y1 - y0, 1),
            "x0": round(x0, 1),
        })

    # 읽는 순서: 위에서 아래로, 같은 높이면 왼쪽부터
    lines.sort(key=lambda line: (line["bbox"][1], line["bbox"][0]))

    heights = sorted(line["height"] for line in lines)
    median_height = heights[len(heights) // 2] if heights else 0.0
    confs = [line["conf"] for line in lines]

    return JSONResponse({
        "engine": engine,
        "width": img.width,
        "height": img.height,
        "line_count": len(lines),
        "median_height": round(median_height, 1),
        "avg_conf": round(sum(confs) / len(confs), 3) if confs else 0.0,
        "elapsed_sec": round(elapsed, 2),
        "lines": lines,
    })
