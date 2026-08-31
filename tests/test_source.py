"""소스 계층의 계약 시험 — **네트워크를 쓰지 않는다.**

케이스 선정 근거 (`tests/CLAUDE.md`: 커버리지가 아니라 무엇을 골랐는지가 평가 대상):

1. **조건성 필드가 `PostingMeta`에 실릴 수 없다** — 과제 CRITICAL(원문 복붙 금지)을
   기계로 잡는 유일한 지점이다. 문서에 「안 읽는다」라고 적는 것과, 넣으면 터지는 것은 다르다
2. **키가 없으면 `SaraminSource`가 시끄럽게 실패한다** — 조용히 `LocalSource`로 넘어가면
   요구 ①(API 크롤링)을 어긴 것을 아무도 모른다
3. **키가 예외 문자열에 안 실린다** — 쿼리 파라미터 인증이라 URL에 키가 박힌다.
   가장 현실적인 누출 경로가 예외·로그다
4. **쿼터가 한도에서 멈춘다** — 1일 한도는 복구되지 않는다. 넘고 나서 아는 것은 늦다
5. **`ClientFeedSource`가 구현돼 있지 않다** — 구현하면 요구 ⑧ 위반이다

`saramin.py`는 키가 없을 때의 행동과 순수 함수(`redact`·`local_posting_id`·`jobs_of`)만
시험한다. 실호출은 이 파일의 범위가 아니다.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from PIL import Image
from pydantic import ValidationError

from matching.config import Settings
from matching.source import (
    DAILY_CALL_LIMIT,
    ClientFeedSource,
    LocalSource,
    PostingMeta,
    PostingRegistry,
    PostingSource,
    QuotaExceeded,
    QuotaLog,
    SaraminRegistry,
    SaraminSource,
    SourceUnavailable,
    get_source,
    local_posting_id,
    posting_dir,
    redact,
)
from matching.source.saramin import jobs_of

FAKE_KEY = "AAAA1111BBBB2222"


def _posting(tmp_path, posting_id: str, images: int = 1):
    directory = posting_dir(tmp_path, posting_id)
    directory.mkdir(parents=True)
    for index in range(1, images + 1):
        Image.new("RGB", (12, 8), "white").save(directory / f"img_{index}.png")
    return directory


# --- 1. 조건성 필드는 담지 않는다. 담지 않으면 실수로 쓸 수도 없다 ---


def test_posting_meta_has_no_conditional_fields():
    """`docs/SARAMIN_API.md` §3이 금지한 필드가 **모델에 존재하지 않는다.**"""
    forbidden = {
        "experience_level",
        "required_education_level",
        "keyword",
        "salary",
        "job_code",
        "job_mid_code",
        "industry",
        "location",
        "job_type",
    }
    assert forbidden & set(PostingMeta.model_fields) == set()
    assert set(PostingMeta.model_fields) == {
        "company",
        "title",
        "posting_date",
        "modification_timestamp",
        "expiration_date",
        "active",
    }


def test_posting_meta_rejects_conditional_field():
    """응답을 통째로 넘겨도 조용히 통과하지 않는다 — `extra="forbid"`가 터뜨린다."""
    with pytest.raises(ValidationError):
        PostingMeta(
            company="가나테크",
            title="백엔드 개발자",
            posting_date="2026-08-20",
            modification_timestamp="1756000000",
            expiration_date=None,
            active=True,
            experience_level="경력 6~10년",
        )


# --- 2. 키가 없으면 조용히 넘어가지 않는다 ---


def test_saramin_source_without_key_raises(tmp_path):
    source = SaraminSource(access_key=None, data_dir=tmp_path)
    with pytest.raises(SourceUnavailable):
        source.list_postings(count=2)
    # 호출을 시도조차 하지 않았으므로 실호출 기록이 생기면 안 된다 —
    # 이 파일이 요구 ①의 실증이라 거짓 양성이 곧 거짓 주장이 된다.
    assert not (tmp_path / ".saramin_quota.json").exists()


def test_saramin_registry_without_key_raises(tmp_path):
    registry = SaraminRegistry(access_key="", data_dir=tmp_path)
    with pytest.raises(SourceUnavailable):
        registry.current("12345678")


def test_fetch_images_requires_list_first(tmp_path):
    """페이지 주소는 메모리에만 있다. 디스크에서 되살아나지 않는다."""
    source = SaraminSource(access_key=FAKE_KEY, data_dir=tmp_path)
    with pytest.raises(SourceUnavailable):
        source.fetch_images("sara-0123456789")


# --- 3. 키는 밖으로 나가는 어떤 문자열에도 안 실린다 ---


def test_redact_removes_access_key():
    raw = f"HTTP error for url 'https://oapi.saramin.co.kr/job-search?access-key={FAKE_KEY}&count=2'"
    assert FAKE_KEY not in redact(raw)
    assert "access-key=***" in redact(raw)


def test_source_repr_does_not_leak_key(tmp_path):
    """`repr`·`str`·f-string 어디로도 안 나간다.

    `vars(obj)`까지는 막지 않는다 — 키를 들고 있는 객체는 전부 거기서 새고,
    막을 수 있다고 적으면 그게 거짓이 된다. 막는 것은 **실수로 찍히는 경로**다.
    """
    source = SaraminSource(access_key=FAKE_KEY, data_dir=tmp_path)
    registry = SaraminRegistry(access_key=FAKE_KEY, data_dir=tmp_path)
    for text in (repr(source), str(source), f"{source}", repr(registry), f"{registry}"):
        assert FAKE_KEY not in text
        assert "***set***" in text


# --- 4. 쿼터. 1일 한도는 복구되지 않는다 ---


def test_quota_blocks_at_daily_limit(tmp_path):
    quota = QuotaLog(tmp_path / ".saramin_quota.json")
    quota.assert_available()  # 0회일 때는 통과
    for _ in range(DAILY_CALL_LIMIT):
        quota.record("job-search", 200)
    with pytest.raises(QuotaExceeded):
        quota.assert_available()


def test_quota_counts_today_only(tmp_path):
    """어제 기록은 오늘 한도를 먹지 않는다. 한도는 **일 단위**로 초기화된다."""
    path = tmp_path / ".saramin_quota.json"
    yesterday = (datetime.now().astimezone() - timedelta(days=1)).isoformat()
    path.write_text(
        json.dumps({"calls": [{"at": yesterday, "endpoint": "job-search", "status": 200}] * 300}),
        encoding="utf-8",
    )
    QuotaLog(path).assert_available()


def test_quota_record_shape_matches_requirement_ac(tmp_path):
    """AC가 읽는 모양 그대로여야 한다 — `calls[].status == 200`이 요구 ① 실증의 판정식이다."""
    path = tmp_path / ".saramin_quota.json"
    QuotaLog(path).record("job-search", 200)
    calls = json.loads(path.read_text(encoding="utf-8"))["calls"]
    assert any(call.get("status") == 200 for call in calls)


# --- 5. 프로덕션 경로는 설계만 있고 구현은 없다 ---


def test_client_feed_is_not_implemented():
    source = ClientFeedSource()
    with pytest.raises(NotImplementedError):
        source.list_postings()
    with pytest.raises(NotImplementedError):
        source.fetch_images("any")


# --- 어댑터 계약 ---


def test_adapters_satisfy_protocol(tmp_path):
    assert isinstance(LocalSource(tmp_path), PostingSource)
    assert isinstance(SaraminSource(access_key=None, data_dir=tmp_path), PostingSource)
    assert isinstance(ClientFeedSource(), PostingSource)
    assert isinstance(SaraminRegistry(access_key=None, data_dir=tmp_path), PostingRegistry)


def test_local_source_lists_postings(tmp_path):
    _posting(tmp_path, "kt-nw", images=2)
    _posting(tmp_path, "nexon-game", images=0)
    refs = LocalSource(tmp_path).list_postings()

    assert [ref.posting_id for ref in refs] == ["kt-nw", "nexon-game"]
    assert [len(ref.image_paths) for ref in refs] == [2, 0]
    # source_kind가 결과에 실려 UI까지 간다. 데모/로컬을 헷갈릴 수 없게 하는 값이다.
    assert {ref.source_kind for ref in refs} == {"local"}


def test_local_source_without_images_is_loud(tmp_path):
    """이미지가 없으면 step 3이 멈춰야 한다. 빈 목록을 돌려주면 조용히 통과한다."""
    _posting(tmp_path, "kt-nw", images=0)
    with pytest.raises(SourceUnavailable):
        LocalSource(tmp_path).fetch_images("kt-nw")


def test_local_source_rejects_path_traversal(tmp_path):
    with pytest.raises(ValueError):
        LocalSource(tmp_path).fetch_images("../../etc")


def test_get_source_rejects_unknown_kind():
    settings = Settings()
    assert isinstance(get_source("local", settings), LocalSource)
    assert isinstance(get_source("saramin_api", settings), SaraminSource)
    with pytest.raises(ValueError):
        get_source("saramin", settings)  # 오타가 조용히 로컬로 떨어지면 안 된다


def test_local_posting_id_hides_saramin_id():
    """사람인 공고 ID가 디렉터리 이름·`provenance.json`에 그대로 남지 않는다."""
    assert "54832105" not in local_posting_id("54832105")
    assert local_posting_id("54832105") == local_posting_id("54832105")
    assert local_posting_id("54832105") != local_posting_id("54828914")


def test_jobs_of_handles_single_and_many():
    """1건일 때 dict, 여러 건일 때 list로 온다. 한쪽만 다루면 1건 조회가 조용히 빈다."""
    assert jobs_of({"jobs": {"job": {"id": "1"}}}) == [{"id": "1"}]
    assert jobs_of({"jobs": {"job": [{"id": "1"}, {"id": "2"}]}}) == [{"id": "1"}, {"id": "2"}]
    assert jobs_of({"jobs": {}}) == []
