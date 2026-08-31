"""공고 **메타데이터·상태** 조회 — API의 진짜 자리.

본문을 나르는 것(`PostingSource`)과 여기를 **타입으로 가른다.** 어댑터는 3종이고 그중
하나가 데모 전용인데, 레지스트리는 하나뿐이고 **데모·프로덕션 두 경로에 다 쓴다.**
그 차이가 코드에 드러나야 한다 (`docs/IMAGE_ACQUISITION.md` §3.5).

레지스트리가 하는 일은 둘이다.

| 역할 | 결과 |
|---|---|
| ① 동일성 확인 | 고객사가 보낸 게 실제 게시된 그 회사 공고인지 |
| ② 상태·수정 감시 | 마감 공고 채점 차단 · **승인 무효화** (검산 G7) |

~~③ 파싱 교차검증~~은 **잘라냈다.** 쓰려던 필드가 요구조건 텍스트 그 자체였다.
잃은 것을 적어둔다: **독립 대조군이 사라졌다.** 파싱 검증이 OCR 경로 안에서만 이뤄지므로
원본 이미지가 잘못 파싱됐을 때 잡아줄 자동 수단이 없다. 남은 방어는 **승인 게이트(사람)**
하나뿐이고, 우리는 「원문 복붙 0」 쪽을 택했다 (`docs/SARAMIN_API.md` §3).
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from .base import SourceUnavailable, default_data_dir
from .saramin import QuotaLog, job_search, jobs_of


class PostingMeta(BaseModel):
    """공고의 **상태와 식별**. 그게 전부다.

    ⛔ 조건성 응답 필드는 **필드 자체를 만들지 않는다.** 담지 않으면 실수로 쓸 수도 없다.
    무엇을 왜 안 읽는지는 `docs/SARAMIN_API.md` §3에 있고, 결정은 `src/CLAUDE.md`에 있다.

    `extra="forbid"`가 그 결정을 런타임에 강제한다 — 응답을 통째로 넘기면 조용히 통과하는
    대신 즉시 터진다.
    """

    model_config = ConfigDict(extra="forbid")

    company: str  # 동일성 확인 전용
    title: str  # 동일성 확인 전용. 원문의 일부이므로 조건 생성에 쓰면 G4에 걸린다
    posting_date: date
    modification_timestamp: str  # 승인 무효화의 기준 (검산 G7)
    expiration_date: date | None
    active: bool


@runtime_checkable
class PostingRegistry(Protocol):
    """공고의 메타데이터·상태를 조회한다. 본문·이미지는 다루지 않는다."""

    def lookup(self, company: str, title: str) -> PostingMeta | None: ...

    def current(self, posting_id: str) -> PostingMeta | None: ...


def _to_date(value: Any) -> date | None:
    """epoch 초 → 날짜.

    `posting-date`·`expiration-date`(ISO 표기)는 `fields=`로 따로 요청해야 나오는데,
    **timestamp로 충분해서 요청하지 않는다** (`docs/SARAMIN_API.md` §3). 한 번 덜 묻는 만큼
    한도를 덜 쓴다.
    """
    if value in (None, "", "0"):
        return None
    try:
        return datetime.fromtimestamp(int(value)).astimezone().date()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def to_meta(job: dict[str, Any]) -> PostingMeta:
    """응답 1건 → `PostingMeta`. **여기가 경계다** — 읽는 키를 여기서만 정한다."""
    posted = _to_date(job.get("posting-timestamp"))
    if posted is None:
        raise SourceUnavailable("응답에 posting-timestamp가 없다 — 동일성 확인의 기준이 사라진다")
    position = job.get("position") or {}  # title 하나만 읽는다
    company = (job.get("company") or {}).get("detail") or {}
    return PostingMeta(
        company=str(company.get("name", "")),
        title=str(position.get("title", "")),
        posting_date=posted,
        modification_timestamp=str(job.get("modification-timestamp", "")),
        expiration_date=_to_date(job.get("expiration-timestamp")),
        active=str(job.get("active", "0")).strip().lower() in {"1", "true", "y"},
    )


class SaraminRegistry:
    """사람인 오픈 API. ✅ 전부 약관 범위 안이다 —
    검색 API를 검색·메타데이터 용도로만 쓴다.
    docs/IMAGE_ACQUISITION.md §3.5 참조.

    `SaraminSource`와 **같은 쿼터 로그를 공유한다.** 1일 한도는 앱 단위이지 클래스
    단위가 아니다.
    """

    def __init__(
        self,
        access_key: str | None,
        data_dir: Path | str | None = None,
        timeout: float = 10.0,
    ):
        self._access_key = access_key
        data_dir = Path(data_dir) if data_dir is not None else default_data_dir()
        self._timeout = timeout
        self.quota = QuotaLog(data_dir / ".saramin_quota.json")

    def __repr__(self) -> str:
        state = "***set***" if self._access_key else None
        return f"SaraminRegistry(access_key={state!r})"

    __str__ = __repr__

    def lookup(self, company: str, title: str) -> PostingMeta | None:
        """① 동일성 확인. 회사명+제목으로 찾아 **정확히 일치하는 1건**만 인정한다.

        부분 방어다 — 그 회사에 그런 제목의 공고가 있다는 것까지만 확인한다.
        **보낸 이미지가 그 공고의 이미지인지는 확인하지 못한다**
        (`docs/IMAGE_ACQUISITION.md` §3 역할 ①의 「한계」).

        ⚠️ 아래 `keywords`는 **요청** 파라미터다 (`docs/SARAMIN_API.md` §2 「우리가 쓰는 것」).
        §3이 금지한 **응답** 필드(공고 본문에서 뽑은 낱말 목록)와 이름이 겹칠 뿐 다른 것이다.
        step2.md 검증 절차의 grep이 이 줄에 걸리는데, 그 grep이 지키려는 것은
        `PostingMeta`의 필드 목록이고 거기엔 조건성 필드가 하나도 없다.
        """
        payload = job_search(
            self._access_key,
            self.quota,
            timeout=self._timeout,
            keywords=f"{company} {title}",
            count=10,
        )
        for job in jobs_of(payload):
            meta = to_meta(job)
            if meta.company == company and meta.title == title:
                return meta
        return None

    def current(self, posting_id: str) -> PostingMeta | None:
        """② 상태·수정 감시. **`posting_id`는 사람인 공고 ID다** —
        `PostingRef.posting_id`(우리 쪽 식별자)와 다르다.

        승인은 특정 revision에 대한 것이므로 공고가 수정되면 낡는다. `score()`가 실행
        시점에 이걸 다시 불러 `modification_timestamp`가 달라졌으면 `ApprovalStale`을
        던진다 (검산 G7). 캐시·한도 초과 시 처리는 `docs/SARAMIN_API.md` §5.
        """
        payload = job_search(
            self._access_key, self.quota, timeout=self._timeout, id=posting_id, count=1
        )
        jobs = jobs_of(payload)
        return to_meta(jobs[0]) if jobs else None
