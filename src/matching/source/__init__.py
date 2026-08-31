"""공고 이미지를 확보하는 계층 — 어댑터 3종 · 레지스트리 · 출처 증거.

| | 무엇 | 네트워크 |
|---|---|---|
| `SaraminSource` | **데모 전용.** API 목록 조회 + 페이지 스크래핑 | 쓴다 |
| `LocalSource` | 이미 놓인 이미지를 읽는다 (개발·경로 B) | 안 쓴다 |
| `ClientFeedSource` | **프로덕션. 구현하지 않는다** | — |
| `SaraminRegistry` | 메타데이터·상태 조회. 두 경로에 다 남는다 | 쓴다 |
"""

from __future__ import annotations

from ..config import Settings
from .base import (
    IMAGE_GLOB,
    POSTINGS_SUBDIR,
    PostingRef,
    PostingSource,
    QuotaExceeded,
    SourceKind,
    SourceUnavailable,
    default_data_dir,
    image_paths,
    posting_dir,
)
from .client_feed import ClientFeedSource
from .local import LocalSource
from .provenance import (
    PROVENANCE_FILENAME,
    Provenance,
    ProvenanceError,
    read_provenance,
    verify_provenance,
    write_provenance,
)
from .registry import PostingMeta, PostingRegistry, SaraminRegistry
from .saramin import DAILY_CALL_LIMIT, QuotaLog, SaraminSource, local_posting_id, redact

__all__ = [
    "DAILY_CALL_LIMIT",
    "IMAGE_GLOB",
    "PROVENANCE_FILENAME",
    "POSTINGS_SUBDIR",
    "ClientFeedSource",
    "LocalSource",
    "PostingMeta",
    "PostingRef",
    "PostingRegistry",
    "PostingSource",
    "Provenance",
    "ProvenanceError",
    "QuotaExceeded",
    "QuotaLog",
    "SaraminRegistry",
    "SaraminSource",
    "SourceKind",
    "SourceUnavailable",
    "default_data_dir",
    "get_source",
    "image_paths",
    "local_posting_id",
    "posting_dir",
    "read_provenance",
    "redact",
    "verify_provenance",
    "write_provenance",
]


def get_source(kind: str, settings: Settings) -> PostingSource:
    """`source_kind` 문자열 하나로 어댑터를 고른다.

    **알 수 없는 값에 기본값을 주지 않는다.** 오타가 조용히 `LocalSource`로 떨어지면
    「API로 크롤링했다」와 「로컬 이미지를 읽었다」가 결과에서 뒤바뀐다.
    """
    if kind == "local":
        return LocalSource()
    if kind == "saramin_api":
        return SaraminSource(access_key=settings.saramin_access_key)
    if kind == "client_feed":
        # 인스턴스는 만들어진다. 메서드가 NotImplementedError를 던진다 — 설계는 있고
        # 구현은 없다는 상태를 그대로 표현한다.
        return ClientFeedSource()
    raise ValueError(f"알 수 없는 source_kind: {kind!r} (saramin_api · local · client_feed)")
