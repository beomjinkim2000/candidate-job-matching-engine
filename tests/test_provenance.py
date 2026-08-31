"""출처 증거의 계약 시험.

케이스 선정 근거 (`tests/CLAUDE.md`): `provenance.json`은 **이미지 없이 남는 유일한
출처 증거**다. 이미지도 `ocr.json`도 커밋되지 않으므로, clone한 사람이 「이 사람이 이미지
공고를 확보해 이미지에서 파싱했다」를 확인할 방법이 이 파일 하나뿐이다.

그래서 고른 것은 **증거가 거짓말을 할 수 있는 네 가지 경로**다.

1. 해시·크기를 실제로 재는가 (안 재면 `BBox.img_w/img_h`의 출처가 없다)
2. **이미지가 0장인데 증거가 만들어지는가** — 빈 증거는 「확보했다」는 거짓 주장이 된다
3. 파일이 바뀌었는데 통과하는가 — 한 바이트만 달라도 다른 이미지다
4. **원문·URL·공고 ID가 새어 들어가는가** — 그러면 레포가 사람인 DB의 부분 복제가 된다
"""

from __future__ import annotations

import json
import re

import pytest
from PIL import Image

from matching.source import (
    PROVENANCE_FILENAME,
    ProvenanceError,
    read_provenance,
    verify_provenance,
    write_provenance,
)


def _images(directory, sizes=((120, 400), (120, 250))):
    directory.mkdir(parents=True, exist_ok=True)
    for index, (width, height) in enumerate(sizes, start=1):
        Image.new("RGB", (width, height), "white").save(directory / f"img_{index}.png")
    return directory


# --- 1. 실제로 재는가 ---


def test_write_provenance_records_hash_and_size(tmp_path):
    directory = _images(tmp_path / "kt-nw")
    provenance = write_provenance(directory, "local", target_position="B2C마케팅&세일즈")

    assert provenance.posting_id == "kt-nw"
    assert provenance.source_kind == "local"
    assert len(provenance.image_sha256) == 2
    assert all(len(digest) == 64 for digest in provenance.image_sha256)
    assert provenance.image_size == [(120, 400), (120, 250)]
    # step 3이 채우는 자리는 비어 있어야 한다. 미리 채우면 「어느 OCR이었나」가 거짓이 된다.
    assert provenance.ocr_engine is None and provenance.ocr_sha256 is None
    # 실호출 증거가 없으므로 api_verified는 False다. 자기 판단으로 올리지 않는다.
    assert provenance.api_verified is False

    saved = read_provenance(directory)
    assert saved.image_sha256 == provenance.image_sha256
    assert verify_provenance(directory) == []


# --- 2. 빈 증거를 만들지 않는다 ---


def test_write_provenance_refuses_empty_directory(tmp_path):
    directory = tmp_path / "kt-nw"
    directory.mkdir()
    with pytest.raises(ProvenanceError):
        write_provenance(directory, "local")
    assert not (directory / PROVENANCE_FILENAME).exists()


def test_write_provenance_ignores_non_image_files(tmp_path):
    """`img_*.png` 말고 다른 파일이 있어도 이미지로 세지 않는다."""
    directory = tmp_path / "kt-nw"
    directory.mkdir()
    (directory / "notes.txt").write_text("메모", encoding="utf-8")
    with pytest.raises(ProvenanceError):
        write_provenance(directory, "local")


# --- 3. 파일이 바뀌면 잡는다 ---


def test_verify_catches_modified_image(tmp_path):
    directory = _images(tmp_path / "kt-nw")
    write_provenance(directory, "local")

    target = directory / "img_2.png"
    target.write_bytes(target.read_bytes() + b"\x00")  # 한 바이트 추가

    violations = verify_provenance(directory)
    assert len(violations) == 1
    assert "img_2.png" in violations[0]


def test_verify_catches_missing_image(tmp_path):
    """clone 직후처럼 이미지가 없으면 「대조할 수 없다」가 위반으로 나온다."""
    directory = _images(tmp_path / "kt-nw")
    write_provenance(directory, "local")
    (directory / "img_1.png").unlink()

    violations = verify_provenance(directory)
    assert any("장수 불일치" in violation for violation in violations)


def test_verify_reports_missing_provenance(tmp_path):
    directory = _images(tmp_path / "kt-nw")
    assert verify_provenance(directory) == [
        f"{PROVENANCE_FILENAME}이 없다 — 출처를 확인할 수단이 없다"
    ]


def test_verify_catches_unrecorded_ocr(tmp_path):
    """`ocr.json`이 있는데 해시 기록이 없으면 어느 OCR 결과인지 증명되지 않는다 (step 3의 전제)."""
    directory = _images(tmp_path / "kt-nw")
    write_provenance(directory, "local")
    (directory / "ocr.json").write_text('{"lines": []}', encoding="utf-8")

    violations = verify_provenance(directory)
    assert any("ocr_sha256" in violation for violation in violations)


# --- 4. 원문·URL·공고 ID가 안 들어간다 ---


def test_provenance_file_leaks_nothing(tmp_path):
    directory = _images(tmp_path / "kt-nw")
    write_provenance(directory, "local", target_position="B2C마케팅&세일즈")

    raw = (directory / PROVENANCE_FILENAME).read_text(encoding="utf-8")
    assert not re.search(r"https?://|rec_idx", raw)

    payload = json.loads(raw)
    assert set(payload) == {
        "posting_id",
        "source_kind",
        "acquired_at",
        "target_position",
        "image_sha256",
        "image_size",
        "ocr_engine",
        "ocr_sha256",
        "api_verified",
        "api_verified_at",
    }
