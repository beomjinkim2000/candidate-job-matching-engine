"""공고 이미지를 확보하는 계층의 계약.

**어댑터를 인터페이스로 가르는 이유가 셋이다** (`phases/matching-engine/step2.md`).

1. 사람인 API 키가 아직 없다. 키 없이도 나머지 파이프라인이 개발돼야 한다
2. 과제 CRITICAL — 데이터가 우리가 아니라 **고객사를 거쳐** 들어오는 구조를 설계해야 한다.
   구현은 안 하되 자리는 만든다. 자리가 없으면 설계했다는 증거가 없다
   (`docs/LEGAL_ARCHITECTURE.md`)
3. 데모 경로(크롤링)와 프로덕션 경로(고객사 push)가 **결과에서 구분돼야 한다**

`PostingRef.source_kind`가 결과 JSON에 그대로 실려 UI까지 간다. 데모 결과를 프로덕션
결과와 헷갈릴 수 없게 만드는 것이 목적이다.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

# 이 셋이 전부다. 늘리면 UI가 무엇을 데모로 표시해야 하는지가 흐려진다.
SourceKind = Literal["saramin_api", "local", "client_feed"]

# 공고 본문 이미지의 파일명 규칙. `.gitignore`가 막는 패턴과 **같은 문자열**이어야 한다 —
# 어긋나면 이미지가 조용히 커밋된다.
IMAGE_GLOB = "img_*.png"

POSTINGS_SUBDIR = "postings"


class SourceUnavailable(RuntimeError):
    """소스를 쓸 수 없다.

    **조용히 다른 소스로 넘어가지 않는다.** 과제 CRITICAL이 「API로 크롤링」을 요구하므로,
    대체 경로가 조용히 쓰이면 요구를 어긴 것을 아무도 모른다.
    """


class QuotaExceeded(SourceUnavailable):
    """오늘 쓸 수 있는 호출을 다 썼다. 1일 한도는 복구되지 않는다."""


class PostingRef(BaseModel):
    """확보한 공고 하나를 가리키는 손잡이.

    `title`·`company`는 **메타데이터**다. 공고 본문이 아니다 —
    본문은 이미지에서만 온다 (과제 CRITICAL).
    """

    model_config = ConfigDict(extra="forbid")

    posting_id: str
    title: str
    company: str
    image_paths: list[Path]
    fetched_at: datetime
    source_kind: SourceKind


@runtime_checkable
class PostingSource(Protocol):
    """본문(이미지)을 나르는 것. 메타데이터 조회는 `PostingRegistry`가 따로 한다.

    **두 메서드를 합치지 마라.** `list_postings()`만 API이고 `fetch_images()`는 아니다.
    합치면 어디까지가 API인지가 코드에서 사라진다 (`docs/IMAGE_ACQUISITION.md` §5).
    """

    def list_postings(self, **query: object) -> list[PostingRef]: ...

    def fetch_images(self, posting_id: str) -> list[Path]: ...


def default_data_dir() -> Path:
    """`data/`의 위치. 테스트는 인자로 덮어쓰고, 실행은 이 값을 쓴다."""
    override = os.environ.get("MATCHING_DATA_DIR")
    if override:
        return Path(override)
    # src/matching/source/base.py → parents[3] 가 레포 루트
    return Path(__file__).resolve().parents[3] / "data"


def posting_dir(data_dir: Path | str, posting_id: str) -> Path:
    """`data/postings/{posting_id}/`.

    `posting_id`를 경로 조각으로 쓰므로 구분자·상위 참조를 막는다. 외부에서 온 문자열이
    디렉터리를 벗어나면 엉뚱한 곳에 파일을 쓴다.
    """
    if not posting_id or posting_id in {".", ".."} or {"/", "\\"} & set(posting_id):
        raise ValueError(f"공고 식별자로 쓸 수 없는 값이다: {posting_id!r}")
    return Path(data_dir) / POSTINGS_SUBDIR / posting_id


def image_paths(directory: Path | str) -> list[Path]:
    """공고 디렉터리의 본문 이미지. **정렬된 순서가 곧 페이지 순서**다 —
    `BBox.page`와 `Provenance.image_sha256`의 인덱스가 이 순서를 따른다.
    """
    return sorted(Path(directory).glob(IMAGE_GLOB))
