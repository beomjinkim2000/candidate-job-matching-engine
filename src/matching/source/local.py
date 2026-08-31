"""이미 확보한 이미지를 `data/postings/`에서 읽는 어댑터. 개발·테스트·재실행용.

**네트워크를 쓰지 않는다.** 그래서 이 어댑터에 대한 테스트는 실물 호출 없이 돈다.

`docs/SCHEDULE.md` §2의 **경로 B**(키 미발급 → 사람이 이미지를 놓음)에서 파이프라인의
실제 입구가 된다. 「이미 내려받은 이미지를 읽는다」이므로 **이미지가 없으면 이 어댑터도
아무것도 못 한다** — 그 사실을 `SourceUnavailable`로 시끄럽게 알린다.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .base import (
    IMAGE_GLOB,
    POSTINGS_SUBDIR,
    PostingRef,
    SourceUnavailable,
    default_data_dir,
    image_paths,
    posting_dir,
)
from .provenance import PROVENANCE_FILENAME, read_provenance


class LocalSource:
    """`data/postings/{id}/img_*.png`를 읽는다. 네트워크 없음.

    **회사명·공고 제목을 모른다.** 우리가 저장하지 않기 때문이다 — 남기는 것은
    `provenance.json`의 해시와 대상 직무 라벨뿐이고, 회사명·제목은 그 안에 없다.
    그래서 `PostingRef.title`·`company`가 빈 문자열이다. 동일성 확인이 필요하면
    `SaraminRegistry.lookup()`이 그 일을 한다 — **본문 어댑터가 할 일이 아니다.**
    """

    def __init__(self, data_dir: Path | str | None = None):
        self._data_dir = Path(data_dir) if data_dir is not None else default_data_dir()

    def list_postings(self, **query: object) -> list[PostingRef]:
        """공고 디렉터리를 훑는다. **질의를 받지 않는다** — 로컬에는 검색할 색인이 없다.

        인자는 `PostingSource` 계약을 맞추려고 받고 무시한다. 이미지가 0장인 디렉터리도
        결과에 넣는다 — 「자리는 있는데 이미지가 안 놓였다」가 지금 우리 상태이고,
        그걸 감추면 경로 B가 어디까지 왔는지 안 보인다.
        """
        root = self._data_dir / POSTINGS_SUBDIR
        if not root.is_dir():
            return []
        refs: list[PostingRef] = []
        for directory in sorted(path for path in root.iterdir() if path.is_dir()):
            refs.append(
                PostingRef(
                    posting_id=directory.name,
                    title="",
                    company="",
                    image_paths=image_paths(directory),
                    fetched_at=self._acquired_at(directory),
                    source_kind="local",
                )
            )
        return refs

    def fetch_images(self, posting_id: str) -> list[Path]:
        """이미 놓인 이미지의 경로. 내려받지 않는다."""
        directory = posting_dir(self._data_dir, posting_id)
        if not directory.is_dir():
            raise SourceUnavailable(f"{directory}: 공고 디렉터리가 없다")
        paths = image_paths(directory)
        if not paths:
            raise SourceUnavailable(
                f"{directory}: {IMAGE_GLOB}가 0장이다 — 사람이 이미지를 놓아야 한다 "
                "(docs/SCHEDULE.md §2 경로 B). 놓은 뒤 `python -m matching acquire`를 부른다"
            )
        return paths

    @staticmethod
    def _acquired_at(directory: Path) -> datetime:
        """확보 시각. `provenance.json`이 있으면 **거기 적힌 시각**이 사실이다."""
        if (directory / PROVENANCE_FILENAME).exists():
            try:
                return read_provenance(directory).acquired_at
            except ValueError:
                pass
        return datetime.fromtimestamp(directory.stat().st_mtime).astimezone()
