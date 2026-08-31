"""프로덕션 경로 — **구현하지 않는다.** 인터페이스와 설계만 둔다.

과제가 구현 불필요를 명시했고("실제로 구현하지 않으셔도 되고"), 구현하면 검증 안 된
코드가 제출물에 들어간다. 그렇다고 파일을 안 만들면 **설계했다는 증거가 없다.**
"""

from __future__ import annotations

from pathlib import Path

from .base import PostingRef

_REASON = (
    "ClientFeedSource는 이번 과제에서 구현하지 않는다 (과제 요구: 「실제로 구현하지 "
    "않으셔도 되고」). 설계는 docs/LEGAL_ARCHITECTURE.md · docs/IMAGE_ACQUISITION.md §3."
)


class ClientFeedSource:
    """프로덕션 경로. 고객사가 자사 공고 원본 에셋을 push하면 받는다.

    이번 과제에서는 구현하지 않는다 (과제 요구: "실제로 구현하지 않으셔도 되고").
    설계는 docs/LEGAL_ARCHITECTURE.md · docs/IMAGE_ACQUISITION.md §3.

    핵심: 고객사는 사람인 API를 쓰지 않는다. 공고 이미지 원본은 처음부터
    고객사가 만들어 갖고 있고 사람인에 업로드한 것이므로, 이 경로에서는
    사람인이 아예 관여하지 않는다. 우리도 호출하지 않는다(push only).

    받는 이미지가 데모와 다른 파일이다 — 원본이라 해상도·압축·분할이 다르다.
    그래서 BBox에 기준 이미지 크기(img_w/img_h)가 반드시 함께 저장돼야 한다.
    """

    def list_postings(self, **query: object) -> list[PostingRef]:
        raise NotImplementedError(_REASON)

    def fetch_images(self, posting_id: str) -> list[Path]:
        raise NotImplementedError(_REASON)
