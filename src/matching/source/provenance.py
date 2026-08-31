"""출처 증거 — 「이 이미지에서 파싱했다」의 유일한 남는 자국.

이미지는 `.gitignore`이고(공고 본문 = 그림), `ocr.json`도 마찬가지다(공고 본문 = 글자).
본문 저장도, 사람인 공고 ID·URL을 남기는 것도 금지다. 그러면 **clone한 사람이
「이 사람이 이미지 공고를 확보해 이미지에서 파싱했다」를 확인할 파일이 하나도 없다.**

남길 수 있는 것만 남긴다 — **해시**다. 해시는 원문이 아니다. 그게 이 설계의 요점이다.
이미지를 가진 사람은 해시를 대조해 **같은 파일인지** 확인할 수 있고,
`requirements.json`의 `ocr_sha256`이 여기 값과 다르면 그 조건들은 **다른 OCR 결과**에서
나온 것이다.

**이 파일이 만드는 `provenance.json`은 레포에 커밋된다** (`docs/SCHEDULE.md` §3).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from PIL import Image
from pydantic import BaseModel, ConfigDict, ValidationError

from .base import IMAGE_GLOB, SourceKind, image_paths

PROVENANCE_FILENAME = "provenance.json"
OCR_FILENAME = "ocr.json"
REQUIREMENTS_FILENAME = "requirements.json"

_HASH_CHUNK = 1 << 20


class ProvenanceError(RuntimeError):
    """증거를 만들 수 없다. **빈 증거를 만들지 않는다.**"""


class Provenance(BaseModel):
    """`data/postings/{id}/provenance.json`의 스키마.

    **원문도 URL도 넣지 마라.** `target_position`은 사람이 CLI에 준 대상 직무 라벨이고,
    한 공고에 직무가 여럿일 때 어느 구간을 읽었는지를 남기는 자리다.
    """

    model_config = ConfigDict(extra="forbid")

    posting_id: str
    source_kind: SourceKind
    acquired_at: datetime
    target_position: str | None = None
    image_sha256: list[str]
    image_size: list[tuple[int, int]]
    ocr_engine: str | None = None  # step 3이 채운다
    ocr_sha256: str | None = None  # step 3이 채운다
    api_verified: bool = False
    api_verified_at: datetime | None = None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def read_provenance(directory: Path | str) -> Provenance:
    path = Path(directory) / PROVENANCE_FILENAME
    return Provenance.model_validate_json(path.read_text(encoding="utf-8"))


def write_provenance(
    directory: Path | str,
    source_kind: SourceKind,
    target_position: str | None = None,
) -> Provenance:
    """디렉터리의 `img_*.png`를 읽어 해시·크기를 계산하고 `provenance.json`을 쓴다.

    **크기를 여기서 재는 것이 `BBox.img_w`/`img_h`의 출처다.** 안 재면 좌표가 어느 이미지
    기준인지 알 수 없고, 데모 이미지에서 만든 결과를 원본 이미지로 다시 볼 때 근거 추적이
    전부 어긋난다 (`docs/IMAGE_ACQUISITION.md` §4-②).

    두 경로가 같은 파일을 만든다 — 사람이 부르는 `python -m matching acquire`와
    `SaraminSource.fetch_images()`의 내부 호출.
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise ProvenanceError(f"{directory}: 공고 디렉터리가 없다")

    images = image_paths(directory)
    if not images:
        raise ProvenanceError(
            f"{directory}: {IMAGE_GLOB}가 0장이다 — 빈 증거를 만들지 않는다. "
            "이미지를 놓은 뒤 다시 부른다 (docs/SCHEDULE.md §2)"
        )

    sizes: list[tuple[int, int]] = []
    for path in images:
        with Image.open(path) as image:
            sizes.append((image.width, image.height))

    provenance = Provenance(
        posting_id=directory.name,
        source_kind=source_kind,
        acquired_at=datetime.now().astimezone(),
        target_position=target_position,
        image_sha256=[sha256_file(path) for path in images],
        image_size=sizes,
    )
    body = json.dumps(provenance.model_dump(mode="json"), ensure_ascii=False, indent=2)
    (directory / PROVENANCE_FILENAME).write_text(body + "\n", encoding="utf-8")
    return provenance


def verify_provenance(directory: Path | str) -> list[str]:
    """기록된 해시가 실제 파일과 맞는지 확인하고 **위반 목록**을 반환한다. 빈 목록이면 통과.

    확인하는 것:

    - `provenance.json`이 있는가 · 읽히는가
    - 이미지 장수와 각 파일의 해시가 기록과 같은가
    - `ocr.json`이 있다면 그 해시가 기록과 같은가
    - `requirements.json`이 있다면 그 `ocr_sha256`이 여기 값과 같은가

    **「파일이 없다」를 전부 위반으로 세지 않는다.** `img_*.png`·`ocr.json`은 커밋되지
    않으므로 clone 직후에는 원래 없다. 장수 불일치로는 잡히고(0장 ≠ 기록 n장),
    그건 「대조할 수 없다」는 사실 그대로다.
    """
    directory = Path(directory)
    violations: list[str] = []

    if not (directory / PROVENANCE_FILENAME).exists():
        return [f"{PROVENANCE_FILENAME}이 없다 — 출처를 확인할 수단이 없다"]
    try:
        provenance = read_provenance(directory)
    except (ValidationError, ValueError) as exc:
        return [f"{PROVENANCE_FILENAME}을 읽을 수 없다: {exc.__class__.__name__}"]

    images = image_paths(directory)
    if len(images) != len(provenance.image_sha256):
        violations.append(
            f"이미지 장수 불일치 — 기록 {len(provenance.image_sha256)}장, 실제 {len(images)}장"
        )
    for path, recorded in zip(images, provenance.image_sha256, strict=False):
        actual = sha256_file(path)
        if actual != recorded:
            violations.append(
                f"{path.name}: 해시 불일치 — 기록 {recorded[:12]}…, 실제 {actual[:12]}…"
            )

    ocr = directory / OCR_FILENAME
    if ocr.exists():
        if provenance.ocr_sha256 is None:
            violations.append(
                f"{OCR_FILENAME}이 있는데 ocr_sha256이 비어 있다 — "
                "어느 OCR 결과인지 증명되지 않는다"
            )
        elif sha256_file(ocr) != provenance.ocr_sha256:
            violations.append(f"{OCR_FILENAME}: 해시 불일치 — 다른 조건에서 나온 OCR 결과다")

    requirements = directory / REQUIREMENTS_FILENAME
    if requirements.exists() and provenance.ocr_sha256 is not None:
        try:
            recorded = json.loads(requirements.read_text(encoding="utf-8")).get("ocr_sha256")
        except (json.JSONDecodeError, AttributeError):
            recorded = None
            violations.append(f"{REQUIREMENTS_FILENAME}을 읽을 수 없다")
        if recorded is not None and recorded != provenance.ocr_sha256:
            violations.append(
                f"{REQUIREMENTS_FILENAME}의 ocr_sha256이 다르다 — "
                "그 조건들은 다른 OCR 결과에서 나왔다"
            )

    return violations
