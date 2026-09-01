"""바깥에서 온 공고 그림을 `data/postings/{id}/`에 앉힌다.

**이 레포에서 신뢰할 수 없는 바이트를 디스크에 쓰는 유일한 자리다.** 그래서 진입점
(`server.py`)에서 떼어 따로 뒀다 — 여기 적힌 것이 전부 검사이고, HTTP 배관에 섞여
있으면 읽는 사람이 무엇이 검사이고 무엇이 배관인지 가려낼 수 없다.

`server.py`도 `cli.py`도 import하지 않는다. HTTP를 모르는 채로 두면 시험이 서버를
띄우지 않고도 이 규칙들을 그대로 시험할 수 있다 (`service.py`가 같은 이유로 argparse도
HTTP도 들이지 않는다).

## 있는 공고에는 한 바이트도 쓰지 않는다 — 이 기능의 핵심 안전장치다

커밋돼 있는 것은 조건과 **좌표**(`requirements.json`의 `source_bbox`)다. 좌표는 특정
그림 위에서만 뜻이 있다. 그림만 갈아 끼우면 근거를 눌렀을 때 엉뚱한 자리에 네모가
그려지고, 그건 **틀린 근거를 그럴듯하게 보여주는 것**이라 아무것도 안 보여주는 것보다
나쁘다. `run.py`의 `download_and_verify()`가 해시가 다르면 받은 그림을 버리는 것과
같은 이유다. 다른 점은 하나 — 업로드본에는 **대조할 해시가 아예 없다.** 그래서 대조가
아니라 금지다.

## 확장자를 믿지 않는다

이름이 `.png`인 것과 내용이 PNG인 것은 다른 말이다. 앞의 것만 보면 아무 파일이나
`data/postings/` 안으로 들어오고, 그 뒤에 도는 것은 OCR과 PIL이다. 그래서 **머리
바이트로 형식을 정하고, 실제로 디코드까지 해 본다.** 두 겹인 이유는 아래 `_decode()`에
적었다.

## 받은 것이 JPEG여도 저장은 PNG다

파일명 규칙이 `IMAGE_GLOB = "img_*.png"` 하나뿐이라(`source/base.py`) JPEG를
`img_1.jpg`로 두면 `image_paths()`가 못 찾고, 게다가 `.gitignore`가 막는 패턴도
`img_*.png`라 **공고 본문이 조용히 커밋된다.** 그렇다고 JPEG 바이트를 `.png` 이름으로
두면 파일명이 거짓말을 한다. 그래서 다시 인코딩해서 **정말 PNG로 만든다** — 픽셀은
그대로이므로 좌표도 그대로다.
"""

from __future__ import annotations

import io
import json
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import BinaryIO

from PIL import Image

from ..source import Provenance, SourceKind, posting_dir, write_provenance
from .service import EntryError

IMAGE_SOURCE_FILENAME = "image_source.json"

# 업로드본의 출처는 **`local`이다.** 고를 수 있는 자리는 셋뿐이고(`source/base.py`의
# `SourceKind` — 「이 셋이 전부다. 늘리면 UI가 무엇을 데모로 표시해야 하는지가 흐려진다」)
# 셋 다 검토했다.
#
# - `saramin_api` — 우리가 API로 받아 온 것. 업로드본은 그게 아니다. 그냥 거짓말이다
# - `client_feed` — 프로덕션(고객사 push) 경로. 화면의 `isDemo()`는 **이 값 하나만**
#   데모가 아닌 것으로 본다(`static/index.html`). 여기에 이 값을 주면 사람이 방금 올린
#   시험용 그림이 화면에서 「실서비스 데이터」가 된다. 데모와 프로덕션을 가르는 유일한
#   표시를 망가뜨리는 쪽이라 셋 중 가장 나쁘다
# - `local` — 「네트워크를 타지 않고 `data/postings/{id}/`에 놓인 그림을 읽었다」.
#   업로드는 그림을 **놓는 손이 사람 손에서 브라우저로 바뀐 것**뿐이고, 그 뒤 읽는
#   것은 `LocalSource` 그대로다 — `load_posting_ref()`가 실제로 부르는 그 어댑터이고,
#   `docs/SCHEDULE.md` §2의 경로 B와 같은 상태다
#
# 새 값 `upload`를 만들지 않은 이유: `source/__init__.py`의 `get_source()`가 「알 수 없는
# 값에 기본값을 주지 않는다」로 `ValueError`를 던진다. 값을 늘리면 그 분기가 깨지고,
# 깨지지 않게 하려면 `upload` → `LocalSource`로 이어야 하는데 **그게 곧 local이라는 뜻**
# 이다. `--source` choices(`cli.py`)와 `SOURCE_ADAPTERS`(`service.py`)까지 같이 늘어난다.
#
# 대신 「받아올 곳이 없다」는 사실은 `image_source.json`의 **빈 `image_url`**이 남긴다.
# 크롤링본에는 URL이 있고 업로드본에는 없다 — 기계로 가를 수 있는 자국이다.
UPLOAD_SOURCE_KIND: SourceKind = "local"

# 머리 바이트. 확장자가 아니라 이것으로 형식을 정한다.
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"

# 장당 상한. 실측 공고 그림이 **1.4 MB(860×4920px)**이다. 스캔본·고해상도 캡처가 그보다
# 훨씬 큰 경우가 있어 열 배 넘게 잡되, 이 위로는 OCR이 몇 분이 아니라 몇십 분이 된다
# (`run.py`의 `OCR_SECONDS_PER_PIXEL` = 0.084초/px).
MAX_IMAGE_BYTES = 20 * 1024 * 1024

# 합계 상한. 한 장 상한만 두면 아홉 장 × 20 MB가 통과한다.
MAX_TOTAL_BYTES = 60 * 1024 * 1024

# 장수 상한이 **한 자리 수**인 것은 임의값이 아니다. `image_paths()`가
# `sorted(glob("img_*.png"))`이라 정렬이 문자열 순서다 — `img_10.png`이 `img_2.png`
# **앞**에 온다. 두 자리로 넘어가는 순간 쪽 순서가 뒤집히고, 그러면 `BBox.page`가
# 다른 쪽을 가리켜 좌표가 조용히 틀린다. 자릿수를 채워 넣는(`img_01.png`) 방법도 있지만
# 그러면 `run.py`가 만드는 이름(`img_{index}.png`)과 규칙이 둘로 갈린다.
MAX_IMAGE_COUNT = 9

_CHUNK = 1 << 16


class UploadConflict(RuntimeError):
    """이미 있는 공고다. **덮어쓰지 않는다** (위 모듈 설명)."""


def store_posting_images(
    data_dir: Path | str,
    posting_id: str,
    files: Sequence[tuple[str, BinaryIO]],
    *,
    position: str | None = None,
) -> Provenance:
    """올라온 그림을 새 공고 디렉터리로 만든다. **파싱도 채점도 하지 않는다.**

    `files`는 `(보낸 이름, 열린 스트림)` 짝의 순서 있는 목록이고, **그 순서가 곧 쪽
    순서**다. 보낸 이름은 사람에게 어느 파일이 문제였는지 말할 때만 쓴다 — 저장 이름을
    거기서 만들지 않는다.

    돌려주는 것은 `write_provenance()`가 만든 `Provenance` 그대로다. 해시·픽셀 크기를
    여기서 따로 계산하지 않는 이유: 그 함수가 `acquire` 경로와 사람인 경로가 쓰는 바로
    그 함수라, 여기서 손으로 만들면 **업로드본만 스키마가 갈릴 자리**가 생긴다.
    """
    directory = posting_dir(data_dir, posting_id)  # 구분자·상위 참조는 여기서 막힌다

    # 있는 디렉터리에는 손대지 않는다. 아래 `mkdir(exist_ok=False)`가 최종 판정이고
    # (동시에 두 번 올라오는 경우까지 막는다), 이 검사는 60 MB를 읽고 나서 버리는 일을
    # 없애려는 것이다.
    if directory.exists():
        raise UploadConflict(
            f"{posting_id}: 이미 있는 공고다 — 덮어쓰지 않는다. 커밋된 조건의 좌표는 "
            "그때 그 그림 위에서만 맞아서, 그림만 바꾸면 근거가 엉뚱한 자리를 가리킨다. "
            "다른 식별자로 올린다"
        )

    if not files:
        raise EntryError("공고 이미지가 없다 — 최소 한 장이 필요하다")
    if len(files) > MAX_IMAGE_COUNT:
        raise EntryError(
            f"이미지가 {len(files)}장이다 — 한 번에 {MAX_IMAGE_COUNT}장까지 받는다"
        )

    # **전부 검사한 뒤에 디스크를 건드린다.** 한 장씩 쓰면서 검사하면 세 번째가 가짜일 때
    # 앞의 두 장이 남고, 그 디렉터리가 다음 시도를 409로 막는다.
    pages: list[bytes] = []
    remaining = MAX_TOTAL_BYTES
    for index, (name, stream) in enumerate(files, start=1):
        label = name.strip() or f"{index}번째 파일"
        raw = _read_capped(label, stream, remaining)
        remaining -= len(raw)
        pages.append(_decode(label, raw))

    directory.mkdir(parents=True, exist_ok=False)
    try:
        for index, data in enumerate(pages, start=1):
            (directory / f"img_{index}.png").write_bytes(data)
        provenance = write_provenance(
            directory,
            UPLOAD_SOURCE_KIND,
            target_position=(position or "").strip() or None,
        )
        _write_image_source(directory, provenance.posting_id)
    except Exception:
        # 반쪽짜리 공고를 남기지 않는다. 남기면 그 자리가 409로 막혀 다시 올릴 수도 없다.
        shutil.rmtree(directory, ignore_errors=True)
        raise
    return provenance


def _read_capped(label: str, stream: BinaryIO, total_left: int) -> bytes:
    """상한을 넘으면 **넘은 순간 멈춘다.** 다 읽고 나서 길이를 재지 않는다."""
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = stream.read(_CHUNK)
        if not chunk:
            break
        size += len(chunk)
        if size > MAX_IMAGE_BYTES:
            raise EntryError(
                f"{label}: 한 장 상한 {MAX_IMAGE_BYTES // 1048576} MB를 넘는다"
            )
        if size > total_left:
            raise EntryError(f"{label}: 합계 상한 {MAX_TOTAL_BYTES // 1048576} MB를 넘는다")
        chunks.append(chunk)
    if not size:
        # 빈 칸을 조용히 건너뛰지 않는다. 건너뛰면 세 장을 올렸는데 두 장이 저장되고,
        # 그 사실이 아무 데도 안 남는다.
        raise EntryError(f"{label}: 내용이 비어 있다")
    return b"".join(chunks)


def _decode(label: str, raw: bytes) -> bytes:
    """PNG·JPEG만 통과시키고 **PNG 바이트로** 돌려준다.

    검사가 두 겹이다.

    1. **머리 바이트** — 받는 형식을 PNG·JPEG 둘로 못 박는다. PIL은 GIF·BMP·TIFF·WEBP를
       비롯해 수십 가지를 열 수 있어서, 디코드만 시험하면 「PIL이 열리면 통과」가 되어
       버린다. 그건 우리가 정한 범위가 아니다
    2. **실제 디코드** — 머리 여덟 바이트만 PNG로 맞춰 놓은 파일은 1번을 지난다.
       `load()`까지 해 봐야 그림인지 알 수 있다. 여기서 안 잡으면 `write_provenance()`가
       PIL로 크기를 재다가 죽고, 그때는 이미 디렉터리가 만들어진 뒤다
    """
    if raw.startswith(_PNG_MAGIC):
        kind = "PNG"
    elif raw.startswith(_JPEG_MAGIC):
        kind = "JPEG"
    else:
        raise EntryError(
            f"{label}: PNG도 JPEG도 아니다 — 파일 이름이 아니라 내용으로 판정한다"
        )

    try:
        with Image.open(io.BytesIO(raw)) as image:
            image.load()  # 열리기만 하는 파일을 여기서 거른다
            if image.format != kind:
                raise EntryError(f"{label}: 머리 바이트는 {kind}인데 내용이 다르다")
            if kind == "PNG":
                # 이미 PNG다. 다시 인코딩하지 않는다 — 저장한 바이트가 올린 바이트와
                # 같아야 `provenance.json`의 해시로 올린 사람이 대조할 수 있다.
                return raw
            # JPEG는 PNG로 바꾼다(위 모듈 설명). 회색조는 그대로 두고, PNG가 그대로 담을
            # 수 없는 모드(CMYK 등)만 RGB로 옮긴다.
            source = image if image.mode in {"L", "RGB", "RGBA"} else image.convert("RGB")
            buffer = io.BytesIO()
            source.save(buffer, format="PNG")
            return buffer.getvalue()
    except EntryError:
        raise
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise EntryError(f"{label}: 그림으로 읽을 수 없다 ({type(exc).__name__})") from exc


def _write_image_source(directory: Path, posting_id: str) -> None:
    """`image_source.json` — **업로드본은 `image_url`이 빈 배열이다.**

    크롤링본은 여기 URL이 있어서 `run.py`의 `fetch_images()`가 그림 없이 clone한
    사람에게 다시 받아 준다. 업로드본에는 받아올 곳이 없다. **없는데 있는 척하지
    않는다** — 빈 배열이면 `fetch_images()`가 조용히 건너뛴다(`urls`가 비면 `continue`).

    스키마는 `data/postings/{id}/image_source.json`과 같은 세 키다. 늘리지 않는다.
    """
    body = {
        "posting_id": posting_id,
        "note": (
            "업로드로 들어온 그림이라 받아올 곳이 없다. image_url이 빈 배열인 것이 그 뜻이다 "
            "— img_*.png를 지우면 provenance.json의 해시와 대조할 원본을 다시 구할 방법이 "
            "없다. 순서는 image_sha256과 같다."
        ),
        "image_url": [],
    }
    (directory / IMAGE_SOURCE_FILENAME).write_text(
        json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
