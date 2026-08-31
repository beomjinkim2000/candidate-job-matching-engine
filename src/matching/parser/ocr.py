"""줄과 좌표 — 이미지에서 글자가 나오는 유일한 통로.

**이미지를 LLM에 보내지 않는다** (`src/CLAUDE.md`). 좌표의 출처는 OCR 하나뿐이고,
그게 검산 G4(「모든 조건에 `source_bbox`가 있다」)를 「공고 원문을 복붙하지 않았다」의
기계적 증명으로 만든다. VLM에 물으면 그럴듯한 좌표를 지어내므로 **검사 대상이
지어낸 값**이 되어 증명이 무효가 된다.

엔진 둘을 지원하고 **기본은 `paddle`**이다. 정확도가 아니라 이식성 때문이다 —
평가자가 윈도우를 쓸 수 있고 `vision`은 macOS에서만 돈다 (`docs/OCR_EVIDENCE.md` §1).

| 엔진 | 어디서 | 실측 평균 신뢰도 | 소요 |
|---|---|---|---|
| `paddle` | 윈도우·맥·리눅스 | 0.936 | 첫 회 모델 다운로드 후 ~40초 |
| `vision` | macOS만 | 0.802 | ~3초 (띄어쓰기가 살아 있다) |

EasyOCR은 같은 이미지에서 0.396이었고 2배·3배 확대로도 안 고쳐져 뺐다 (§1).
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import tempfile
import time
from functools import lru_cache
from pathlib import Path
from typing import Literal

from PIL import Image
from pydantic import BaseModel, ConfigDict

from ..model.objects import BBox

# PaddleOCR은 기본값이 전 코어다. 그대로 두면 맥이 멈춘다 — 2026-09-01 00:50에 실제로
# 멈췄다. **`paddle` import 보다 먼저 걸어야 먹는다.** 나중에 걸면 무시된다.
for _var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ.setdefault(_var, "2")

OcrEngine = Literal["paddle", "vision"]

OCR_FILENAME = "ocr.json"

# 줄 사이를 잇는 문자. `source_span`이 가리키는 문서가 이 문자로 이어붙인 결과다 —
# 구분자를 바꾸면 이미 저장된 span이 전부 어긋난다.
LINE_SEP = "\n"

_PADDLE_THREADS = 2


class OcrUnavailable(RuntimeError):
    """엔진을 쓸 수 없다. **조용히 다른 엔진으로 넘어가지 않는다** —
    어느 엔진이 읽었는지가 `provenance.json`에 남는 사실이라, 바꿔치면 기록이 거짓이 된다.
    """


class OcrLine(BaseModel):
    """OCR이 뱉은 줄 하나. **손대지 않는다** — 이 값이 좌표의 원본이다."""

    model_config = ConfigDict(extra="forbid")

    id: str  # "L-001"
    text: str
    conf: float
    bbox: BBox
    x0: int  # bbox.x1 별칭. **들여쓰기 판정이 이것만 본다**
    height: int


class OcrResult(BaseModel):
    """한 공고의 OCR 결과 전부. `data/postings/{id}/ocr.json`의 스키마.

    ⛔ **커밋하지 않는다.** 이미지를 글자로 옮긴 것이라 공고 본문 그 자체다
    (`docs/SCHEDULE.md` §3). 그림과 글자를 다르게 취급하지 않는다.
    """

    model_config = ConfigDict(extra="forbid")

    engine: OcrEngine
    engine_version: str
    image_path: str
    img_w: int
    img_h: int
    lines: list[OcrLine]  # 읽는 순서: page → y 오름차순 → x 오름차순
    avg_conf: float
    elapsed_sec: float

    def document(self) -> str:
        """줄을 이어붙인 문서. **`source_span`의 좌표계가 이것이다.**

        조건 텍스트를 이 문서에서 잘라낸 구간으로 정의하면, 검증이 유사도가 아니라
        **문자열 동일성**으로 끝난다 (`verify.py`).
        """
        return LINE_SEP.join(line.text for line in self.lines)

    def offsets(self) -> dict[str, tuple[int, int]]:
        """`line_id → (start, end)` — `document()` 안에서 그 줄이 차지하는 구간."""
        table: dict[str, tuple[int, int]] = {}
        cursor = 0
        for line in self.lines:
            table[line.id] = (cursor, cursor + len(line.text))
            cursor += len(line.text) + len(LINE_SEP)
        return table


# ---------------------------------------------------------------- PaddleOCR


@lru_cache(maxsize=1)
def _paddle():
    try:
        from paddleocr import PaddleOCR
    except ImportError as exc:  # pragma: no cover - 설치 여부에 따라 갈린다
        raise OcrUnavailable(
            "paddleocr가 없다. `pip install -e \".[ocr]\"` — OCR은 선택 그룹이라 "
            "채점 재현에는 필요 없다 (pyproject.toml)"
        ) from exc

    return PaddleOCR(
        lang="korean",
        cpu_threads=_PADDLE_THREADS,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
    )


def _run_paddle(path: Path) -> list[tuple[str, float, list]]:
    out: list[tuple[str, float, list]] = []
    for page in _paddle().predict(str(path)):
        if isinstance(page, dict):  # 3.x
            boxes = page.get("rec_polys") or page.get("dt_polys") or []
            for text, score, box in zip(
                page.get("rec_texts", []), page.get("rec_scores", []), boxes, strict=False
            ):
                out.append((text, float(score), box))
        else:  # 2.x
            for box, (text, score) in page:
                out.append((text, float(score), box))
    return out


def _paddle_version() -> str:
    try:
        from importlib.metadata import version

        return f"paddleocr {version('paddleocr')}"
    except Exception:  # pragma: no cover
        return "paddleocr unknown"


# ------------------------------------------------------------ macOS Vision

_VISION_SOURCE = Path(__file__).with_name("vision_ocr.swift")


@lru_cache(maxsize=1)
def _vision_binary() -> Path:
    """swift 소스를 한 번만 컴파일한다. 매번 하면 3초씩 든다.

    산출물을 패키지 디렉터리가 아니라 임시 디렉터리에 둔다 — 설치된 패키지 경로는
    쓰기 권한이 없을 수 있고, 컴파일 결과는 원본이 아니라 캐시다.
    """
    if platform.system() != "Darwin":
        raise OcrUnavailable("vision 엔진은 macOS에서만 돈다. `--ocr-engine paddle`을 쓴다")
    if not _VISION_SOURCE.exists():  # pragma: no cover
        raise OcrUnavailable(f"{_VISION_SOURCE.name}이 없다")

    binary = Path(tempfile.gettempdir()) / "matching_vision_ocr"
    if not binary.exists() or binary.stat().st_mtime < _VISION_SOURCE.stat().st_mtime:
        try:
            subprocess.run(
                ["swiftc", "-O", str(_VISION_SOURCE), "-o", str(binary)],
                check=True,
                capture_output=True,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise OcrUnavailable(f"swiftc 컴파일 실패: {exc}") from exc
    return binary


def _run_vision(path: Path) -> list[tuple[str, float, list]]:
    proc = subprocess.run(
        [str(_vision_binary()), str(path)], capture_output=True, text=True, check=True
    )
    out: list[tuple[str, float, list]] = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        text, conf, box = line.rsplit("\t", 2)
        x0, y0, x1, y1 = (int(v) for v in box.split(","))
        out.append((text, float(conf), [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]))
    return out


def _vision_version() -> str:
    return f"macOS Vision {platform.mac_ver()[0] or 'unknown'}"


_ENGINES = {
    "paddle": (_run_paddle, _paddle_version),
    "vision": (_run_vision, _vision_version),
}


# ------------------------------------------------------------------- 실행


def _rect(box: list) -> tuple[int, int, int, int]:
    xs = [float(point[0]) for point in box]
    ys = [float(point[1]) for point in box]
    return int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))


def run_ocr(image_path: Path, engine: str = "paddle", page: int = 1) -> OcrResult:
    """이미지 한 장 → 줄과 좌표.

    `page`는 공고에 이미지가 여러 장일 때 어느 장인지다. `BBox.page`에 실려
    좌표가 어느 이미지 기준인지를 잃지 않게 한다.
    """
    if engine not in _ENGINES:
        raise OcrUnavailable(f"모르는 엔진: {engine!r} (paddle · vision)")
    image_path = Path(image_path)
    if not image_path.exists():
        raise OcrUnavailable(f"{image_path}: 이미지가 없다")

    with Image.open(image_path) as image:
        img_w, img_h = image.width, image.height

    runner, describe = _ENGINES[engine]
    started = time.perf_counter()
    raw = runner(image_path)
    elapsed = time.perf_counter() - started

    boxed = [(text, conf, _rect(box)) for text, conf, box in raw if text.strip()]
    # 읽는 순서: 위에서 아래로, 같은 높이면 왼쪽부터.
    boxed.sort(key=lambda row: (row[2][1], row[2][0]))

    lines = [
        OcrLine(
            id=f"L-{index:03d}",
            text=text,
            conf=round(conf, 4),
            bbox=BBox(page=page, x1=x0, y1=y0, x2=x1, y2=y1, img_w=img_w, img_h=img_h),
            x0=x0,
            height=y1 - y0,
        )
        for index, (text, conf, (x0, y0, x1, y1)) in enumerate(boxed, start=1)
    ]
    confs = [line.conf for line in lines]

    return OcrResult(
        engine=engine,  # type: ignore[arg-type]
        engine_version=describe(),
        image_path=image_path.name,  # **디렉터리 경로를 남기지 않는다** (환경 노출)
        img_w=img_w,
        img_h=img_h,
        lines=lines,
        avg_conf=round(sum(confs) / len(confs), 4) if confs else 0.0,
        elapsed_sec=round(elapsed, 2),
    )


def merge_results(results: list[OcrResult]) -> OcrResult:
    """이미지가 여러 장인 공고를 한 벌로 합친다. **줄 id를 다시 매긴다.**

    합치지 않고 첫 장만 읽으면 뒷장의 조건이 조용히 사라진다. `bbox.page`가 남아 있어
    좌표는 여전히 원래 이미지를 가리킨다.
    """
    if not results:
        raise OcrUnavailable("합칠 OCR 결과가 없다")
    if len(results) == 1:
        return results[0]

    lines: list[OcrLine] = []
    for result in results:
        for line in result.lines:
            lines.append(line)
    lines.sort(key=lambda line: (line.bbox.page, line.bbox.y1, line.bbox.x1))
    renumbered = [line.model_copy(update={"id": f"L-{i:03d}"}) for i, line in enumerate(lines, 1)]
    confs = [line.conf for line in renumbered]

    head = results[0]
    return OcrResult(
        engine=head.engine,
        engine_version=head.engine_version,
        image_path=", ".join(result.image_path for result in results),
        img_w=max(result.img_w for result in results),
        img_h=max(result.img_h for result in results),
        lines=renumbered,
        avg_conf=round(sum(confs) / len(confs), 4) if confs else 0.0,
        elapsed_sec=round(sum(result.elapsed_sec for result in results), 2),
    )


def load_or_run_ocr(
    directory: Path | str,
    image_paths: list[Path],
    engine: str = "paddle",
    reocr: bool = False,
) -> tuple[OcrResult, bool]:
    """`ocr.json`이 있으면 그대로 쓴다. 반환의 두 번째 값이 「새로 돌렸나」다.

    **같은 이미지를 두 번 OCR하지 않는다** — 공고당 3~40초이고, 규칙(임계값·사다리)을
    고칠 때마다 다시 돌리면 반복이 느려서 규칙을 안 고치게 된다. 재실행은 `reocr=True`
    로만 한다 (`step3.md` 3-A).
    """
    path = Path(directory) / OCR_FILENAME
    if path.exists() and not reocr:
        return OcrResult.model_validate_json(path.read_text(encoding="utf-8")), False

    results = [
        run_ocr(image, engine=engine, page=index)
        for index, image in enumerate(image_paths, start=1)
    ]
    merged = merge_results(results)
    body = json.dumps(merged.model_dump(mode="json"), ensure_ascii=False, indent=2)
    path.write_text(body + "\n", encoding="utf-8")
    return merged, True
