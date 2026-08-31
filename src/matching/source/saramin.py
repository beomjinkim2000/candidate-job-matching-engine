"""사람인 어댑터 — **절반은 API가 아니다.**

`docs/IMAGE_ACQUISITION.md` §1~2가 정본이다. 요약하면: 사람인 오픈 API는 공고 목록과
메타데이터·페이지 주소를 주지만 **본문도 이미지 URL도 주지 않는다.** 그래서 이미지 한 장을
얻는 데 네 단계가 들고, 그중 **1단계만 API**다.

이 파일은 그 경계를 메서드 두 개로 갈라 둔다. 합치면 어디까지가 API인지가 코드에서 사라진다.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx
from PIL import Image

from .base import (
    PostingRef,
    QuotaExceeded,
    SourceUnavailable,
    default_data_dir,
    image_paths,
    posting_dir,
)
from .provenance import write_provenance

JOB_SEARCH_URL = "https://oapi.saramin.co.kr/job-search"

# 공식 한도는 1일 500회다. 개발 중 무심코 소진하면 그날은 복구가 안 되므로 **200에서 막는다**
# (`phases/matching-engine/step2.md`). 남는 300회는 실수 예산이다.
DAILY_CALL_LIMIT = 200

# 이미지 요청 사이 최소 지연(초). 짧은 시간에 반복 요청하면 서비스에 부담을 준다.
MIN_REQUEST_DELAY = 1.0

_ACCESS_KEY_PATTERN = re.compile(r"(access-key=)[^&\s\"']+", re.IGNORECASE)
_IMG_SRC_PATTERN = re.compile(r"<img\b[^>]*?\bsrc\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE)

# 사람인 오류 코드 → 우리 처리 (`docs/SARAMIN_API.md` §5)
_ERROR_MESSAGES = {
    1: "access-key가 요청에 없다 (설정 오류)",
    2: "access-key가 무효다",
    3: "요청 파라미터 오류",
    4: "일일 요청 한도 초과",
    99: "사람인 서버 오류",
}


def redact(text: str) -> str:
    """로그·예외 메시지에서 키를 지운다.

    쿼리 파라미터 인증이라 **URL 안에 키가 들어간다.** httpx의 예외 문자열에도 URL이
    통째로 실리므로, 밖으로 나가는 문자열은 전부 여기를 통과시킨다.
    """
    return _ACCESS_KEY_PATTERN.sub(r"\1***", text)


def local_posting_id(saramin_id: str) -> str:
    """사람인 공고 ID → **우리 쪽 식별자**.

    금지사항: 「사람인 공고 ID·URL을 결과 JSON에 남기지 마라 — 남기면 우리 DB가 사람인 DB의
    부분 복제가 된다」. 그런데 `posting_id`는 디렉터리 이름이 되고 `provenance.json`에
    실려 **커밋된다.** 그래서 원본 ID를 그대로 쓰지 않는다.

    되짚기를 막는 장치가 아니다 — 짧은 숫자 ID는 전수 대조로 되짚힌다. 목적은
    **우리 산출물이 사람인 DB의 부분 복제가 되지 않게 하는 것**이다.
    """
    return "sara-" + hashlib.sha256(saramin_id.encode("utf-8")).hexdigest()[:10]


class QuotaLog:
    """호출 기록 — `data/.saramin_quota.json`.

    이 파일이 요구 ①(「API로 크롤링해서 확보한다」)의 **유일한 실증**이다.
    `index.json`의 `req1_api_crawl`을 `verified`로 올릴 수 있는 조건이 「여기에 200 응답
    기록이 있을 때」 하나뿐이다 (step2.md AC).

    `SaraminSource`와 `SaraminRegistry`가 **같은 로그를 공유한다.** 한도는 앱 단위이지
    클래스 단위가 아니다.
    """

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"calls": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"calls": []}
        if not isinstance(data, dict) or not isinstance(data.get("calls"), list):
            return {"calls": []}
        return data

    def calls_on(self, day: date) -> int:
        prefix = day.isoformat()
        return sum(
            1 for call in self._load()["calls"] if str(call.get("at", "")).startswith(prefix)
        )

    def assert_available(self) -> None:
        used = self.calls_on(datetime.now().astimezone().date())
        if used >= DAILY_CALL_LIMIT:
            raise QuotaExceeded(
                f"오늘 {used}회 호출했다 — 자체 한도 {DAILY_CALL_LIMIT}회에서 멈춘다. "
                "사람인 1일 한도는 500회이고 복구되지 않는다"
            )

    def record(self, endpoint: str, status: int) -> None:
        data = self._load()
        data["calls"].append(
            {
                "at": datetime.now().astimezone().isoformat(),
                "endpoint": endpoint,
                "status": status,
            }
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


def job_search(
    access_key: str | None,
    quota: QuotaLog,
    timeout: float = 10.0,
    **params: Any,
) -> dict[str, Any]:
    """✅ API 호출 한 번. `GET /job-search`.

    요청 파라미터는 **호출자가 준다.** 우리가 허용목록을 만들지 않는 이유는, 경계가
    「무엇을 묻느냐」가 아니라 **「응답의 무엇을 읽느냐」**에 있기 때문이다 —
    그 경계는 `PostingMeta`(조건성 필드를 아예 만들지 않는다)와
    `SaraminSource._to_ref`가 강제한다.

    **재시도하지 않는다.** 1일 한도가 있는 자원에서 재시도 루프는 몇 초 만에 그날을 태운다.
    """
    if not access_key:
        raise SourceUnavailable(
            "SARAMIN_ACCESS_KEY가 없다. 조용히 다른 소스로 넘어가지 않는다 — "
            "요구 ①이 API 크롤링이므로 대체 경로가 조용히 쓰이면 아무도 모른다"
        )
    quota.assert_available()

    query = {"access-key": access_key, **params}
    try:
        response = httpx.get(
            JOB_SEARCH_URL,
            params=query,
            headers={"Accept": "application/json"},
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        quota.record("job-search", 0)
        # `from None` — 원 예외의 문자열에 키가 박힌 URL이 들어 있다. 체인을 끊는다.
        raise SourceUnavailable(f"사람인 API 호출 실패: {redact(str(exc))}") from None

    quota.record("job-search", response.status_code)
    if response.status_code != 200:
        raise SourceUnavailable(f"사람인 API가 HTTP {response.status_code}를 반환했다")

    try:
        payload = response.json()
    except ValueError:
        raise SourceUnavailable("사람인 API 응답이 JSON이 아니다") from None

    code = payload.get("code") if isinstance(payload, dict) else None
    if code is not None:
        message = _ERROR_MESSAGES.get(int(code), "알 수 없는 오류")
        error = QuotaExceeded if int(code) == 4 else SourceUnavailable
        raise error(f"사람인 API 오류 code={code}: {message}")
    return payload


def jobs_of(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """응답에서 공고 목록을 꺼낸다. 1건일 때 dict, 여러 건일 때 list로 온다."""
    jobs = (payload.get("jobs") or {}).get("job")
    if jobs is None:
        return []
    return list(jobs) if isinstance(jobs, list) else [jobs]


class SaraminSource:
    """데모 전용. 프로덕션에서 이 어댑터를 쓰면 안 된다 —
    서비스 제공사가 직접 수집하는 구조가 되어 DB제작자의 권리·부정경쟁방지법·
    이용약관 문제가 발생한다. 프로덕션 경로는 ClientFeedSource다.
    docs/LEGAL_ARCHITECTURE.md 참조.

    지키는 선 (`docs/IMAGE_ACQUISITION.md` §2):

    - 공고 **2개만** 가져온다. 대량 수집을 하지 않는다
    - 이미지는 `data/postings/`에 두고 레포에 커밋하지 않는다
    - **공고 본문 텍스트를 저장하지 않는다.** `<img>`의 `alt`도 저장하지 않는다
    - 사람인 공고 ID·URL을 산출물에 남기지 않는다 (`local_posting_id` 참조)
    - 이미지 요청 사이에 최소 1초를 둔다
    """

    def __init__(
        self,
        access_key: str | None,
        data_dir: Path | str | None = None,
        delay_seconds: float = MIN_REQUEST_DELAY,
        timeout: float = 10.0,
    ):
        self._access_key = access_key
        self._data_dir = Path(data_dir) if data_dir is not None else default_data_dir()
        self._delay = max(delay_seconds, MIN_REQUEST_DELAY)
        self._timeout = timeout
        self.quota = QuotaLog(self._data_dir / ".saramin_quota.json")
        # 공고 페이지 주소는 **메모리에만** 둔다. 디스크에 쓰면 그게 곧 사람인 DB의 부분 복제다.
        # 프로세스를 다시 띄우면 list_postings()를 다시 불러야 한다 — 의도된 비용이다.
        self._page_urls: dict[str, str] = {}

    def __repr__(self) -> str:
        """키를 절대 찍지 않는다 (`config.Settings.__repr__`와 같은 이유).

        기본 `repr`도 속성을 안 보여주지만, **의도를 코드에 남긴다** — 나중에 이 클래스가
        dataclass가 되면 기본 `repr`이 키를 통째로 뱉는다.
        """
        state = "***set***" if self._access_key else None
        return f"SaraminSource(access_key={state!r}, data_dir={str(self._data_dir)!r})"

    __str__ = __repr__

    def list_postings(self, **query: Any) -> list[PostingRef]:
        """✅ API. `GET https://oapi.saramin.co.kr/job-search?access-key=...`

        메타데이터와 공고 **페이지 주소**만 얻는다. 이미지는 여기서 나오지 않는다.
        """
        payload = job_search(self._access_key, self.quota, timeout=self._timeout, **query)
        return [self._to_ref(job) for job in jobs_of(payload)]

    def fetch_images(self, posting_id: str, target_position: str | None = None) -> list[Path]:
        """❌ API가 아니다. 세 단계다.

        (1) url의 HTML 페이지를 받는다        ← 스크래핑
        (2) 본문 <img> 태그를 찾는다           ← 파싱
        (3) 사람인 CDN에서 파일을 내려받는다    ← 다운로드

        이 셋은 오픈 API가 부여한 "제공하는 범위 내" 권리에 포함되지 않는다.
        데모 전용. docs/IMAGE_ACQUISITION.md §2 참조.

        **알려진 한계**: 본문이 iframe 안에 있거나 스크립트로 그려지면 (2)가 아무것도
        못 찾는다. 그때는 경로 B로 간다 — 사람이 이미지를 디렉터리에 놓고
        `python -m matching acquire`를 부른다 (`docs/SCHEDULE.md` §2).
        """
        page_url = self._page_urls.get(posting_id)
        if page_url is None:
            raise SourceUnavailable(
                f"{posting_id}의 공고 페이지 주소를 모른다 — list_postings()를 먼저 부른다. "
                "주소는 메모리에만 있고 디스크에 쓰지 않는다"
            )

        html = self._get(page_url).text  # (1) 스크래핑. 본문 텍스트는 저장하지 않는다
        sources = self._image_sources(html, base=page_url)  # (2) 파싱. src만, alt는 안 본다
        if not sources:
            raise SourceUnavailable(
                f"{posting_id}: 본문 이미지를 찾지 못했다 — 이미지 공고가 아니거나 "
                "본문이 iframe/스크립트 안에 있다"
            )

        destination = posting_dir(self._data_dir, posting_id)
        destination.mkdir(parents=True, exist_ok=True)
        for index, src in enumerate(sources, start=1):
            time.sleep(self._delay)  # (3) 요청 간 지연
            self._save_image(self._get(src).content, destination / f"img_{index}.png")

        # 경로 A와 경로 B가 **같은 파일**을 만든다. 여기서 픽셀 크기가 기록되고,
        # 그 값이 BBox.img_w/img_h의 출처가 된다.
        write_provenance(destination, "saramin_api", target_position=target_position)
        return image_paths(destination)

    def _to_ref(self, job: dict[str, Any]) -> PostingRef:
        """응답에서 **상태·식별 필드만** 읽는다.

        조건성 필드는 읽지 않는다 (`docs/SARAMIN_API.md` §3). 대조에만 쓰더라도 그 텍스트가
        결과에 영향을 준 것이 되고, 그건 과제 CRITICAL(원문 복붙 금지) 위반 소지다.
        """
        posting_id = local_posting_id(str(job.get("id", "")))
        self._page_urls[posting_id] = str(job.get("url", ""))
        position = job.get("position") or {}
        company = (job.get("company") or {}).get("detail") or {}
        return PostingRef(
            posting_id=posting_id,
            title=str(position.get("title", "")),
            company=str(company.get("name", "")),
            image_paths=[],
            fetched_at=datetime.now().astimezone(),
            source_kind="saramin_api",
        )

    def _get(self, url: str) -> httpx.Response:
        """API가 아닌 요청. **쿼터에 기록하지 않는다** — 한도는 API 호출에만 걸린다."""
        try:
            response = httpx.get(url, timeout=self._timeout, follow_redirects=True)
        except httpx.HTTPError as exc:
            raise SourceUnavailable(f"내려받기 실패: {redact(str(exc))}") from None
        if response.status_code != 200:
            raise SourceUnavailable(f"내려받기 실패: HTTP {response.status_code}")
        return response

    @staticmethod
    def _image_sources(html: str, base: str) -> list[str]:
        """`<img>`의 `src`만 뽑는다. **`alt`는 읽지 않는다** — 그건 본문 텍스트다."""
        found: list[str] = []
        seen: set[str] = set()
        for raw in _IMG_SRC_PATTERN.findall(html):
            if raw.startswith("data:"):
                continue
            url = urljoin(base, raw)
            if not url.startswith(("http://", "https://")) or url in seen:
                continue
            seen.add(url)
            found.append(url)
        return found

    @staticmethod
    def _save_image(data: bytes, path: Path) -> None:
        """PNG로 정규화해 저장한다.

        받은 바이트를 그대로 `.png` 이름으로 쓰면 파일명이 거짓말을 한다. 그리고 해시는
        **우리가 파싱한 그 파일**에 대한 것이어야 하므로, 저장한 결과를 기준으로 잡는다.
        """
        with Image.open(BytesIO(data)) as image:
            if image.mode not in {"RGB", "RGBA", "L", "P"}:
                image = image.convert("RGB")
            image.save(path, format="PNG")
