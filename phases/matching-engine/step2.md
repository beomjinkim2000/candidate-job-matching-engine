# Step 2: posting-source

## 읽어야 할 파일

- `src/CLAUDE.md` — 모듈 경계 표의 `crawler` 행 · **「API의 역할 — 본문 파이프가 아니라 대조군」**
- `docs/SARAMIN_API.md` — **§3 필독.** 어느 응답 필드를 읽지 **않는지**가 여기 있다.
  루트 `CLAUDE.md:53`이 「crawler·registry 구현 전 필독」으로 지정한 문서다
- `docs/SCHEDULE.md` — **§2 이미지 확보 분기.** 키가 안 나왔을 때 몇 시에 무엇을 하는지
- `CLAUDE.md` — 「외부 의존성 상태」(사람인 API 승인 대기, 1일 500회 제한)
- `src/matching/config.py`, `src/matching/model/objects.py` (step 0~1 산출물)

## 작업

공고 이미지를 확보하는 계층. **인터페이스로 분리한다.** 이유는 세 가지다.

1. 사람인 API 키가 아직 없다 (승인 대기). 키 없이도 나머지 파이프라인을 개발해야 한다
2. **과제 CRITICAL — 법적 우회 구조.** 데이터가 우리가 아니라 **고객사를 거쳐** 들어오는
   구조를 설계해야 한다. `docs/LEGAL_ARCHITECTURE.md`가 정본이다. **구현은 안 하되
   자리는 만든다** — 자리가 없으면 설계했다는 증거가 없다
3. 데모 경로(크롤링)와 프로덕션 경로(고객사 push)가 **결과에서 구분돼야 한다**

### `src/matching/source/base.py`

```python
class PostingRef(BaseModel):
    posting_id: str
    title: str            # 공고 제목 (메타데이터. 본문 아님)
    company: str
    image_paths: list[Path]   # 로컬에 내려받은 본문 이미지
    fetched_at: datetime
    source_kind: Literal["saramin_api", "local", "client_feed"]

class PostingSource(Protocol):
    def list_postings(self, **query) -> list[PostingRef]: ...
    def fetch_images(self, posting_id: str) -> list[Path]: ...
```

### `src/matching/source/saramin.py`

**먼저 `docs/IMAGE_ACQUISITION.md`를 읽어라.** 이 어댑터가 하는 일의 절반은 API가 아니다.

**사람인 API는 이미지를 주지 않는다.** 검증된 응답 필드는 다음이 전부다.

```
id · url · active · posting-date · modification-timestamp · expiration-date
company · position · experience-level · required-education-level
keyword · salary · read-cnt · apply-cnt
```

**본문 필드도 이미지 URL 필드도 없다.** `url`은 공고 **페이지 링크**다.

그래서 메서드를 **두 개로 나눈다.** 합치지 마라 — 합치면 어디까지가 API인지가 코드에서
사라진다.

```python
def list_postings(self, **q) -> list[PostingRef]:
    """✅ API. GET https://oapi.saramin.co.kr/job-search?access-key=...
    메타데이터와 공고 페이지 url만 얻는다."""

def fetch_images(self, posting_id: str) -> list[Path]:
    """❌ API가 아니다. 세 단계다.
       (1) url의 HTML 페이지를 받는다        ← 스크래핑
       (2) 본문 <img> 태그를 찾는다           ← 파싱
       (3) 사람인 CDN에서 파일을 내려받는다    ← 다운로드
    이 셋은 오픈 API가 부여한 "제공하는 범위 내" 권리에 포함되지 않는다.
    데모 전용. docs/IMAGE_ACQUISITION.md §2 참조."""
```

지키는 선:

- **공고 2개만** 가져온다. 대량 수집 금지
- 호출 횟수를 `data/.saramin_quota.json`에 기록한다. 1일 500회 제한이라 개발 중 무심코
  소진하면 복구가 안 된다. 하루 200회를 넘으면 예외
- `fetch_images`에는 **요청 간 지연**을 넣는다 (최소 1초). 이유: 짧은 시간에 반복 요청하면
  서비스에 부담을 준다
- 내려받은 이미지는 `data/postings/{posting_id}/img_{n}.png`에 저장한다.
  **`.gitignore`에서 빼는 것은 `data/postings/*/img_*.png` 패턴 하나뿐이다**
  (`step0.md`가 그 줄을 못박았다). **`data/`나 `data/postings/`를 통째로 무시하지 마라** —
  같은 디렉터리의 `provenance.json`·`ocr.json`·`requirements.json`이 함께 사라지고,
  그러면 **이미지를 뺀 대가로 남긴 유일한 증거까지 없어진다**
- **저장 직후 이미지 픽셀 크기를 기록한다** — `BBox`의 `img_w`/`img_h`가 이걸 쓴다.
  안 하면 좌표가 어느 이미지 기준인지 알 수 없다
- 본문 텍스트를 HTML에서 긁어 저장하지 마라. `<img>`의 `alt`도 저장하지 마라

**`SARAMIN_ACCESS_KEY`가 없으면 `SourceUnavailable` 예외를 던진다.** 조용히 다른
소스로 넘어가지 마라. 이유: 과제 CRITICAL이 "API로 크롤링"을 요구하므로, 대체 경로가
조용히 쓰이면 요구를 어긴 걸 아무도 모른다.

### `src/matching/source/local.py`

이미 내려받은 이미지를 `data/postings/`에서 읽는 어댑터. 개발·테스트·재실행용이다.
네트워크를 쓰지 않는다.

### `src/matching/source/registry.py` — API의 진짜 자리

**본문을 나르는 것(`PostingSource`)과 메타데이터를 조회하는 것을 타입으로 분리한다.**
`PostingSource`는 어댑터가 3종이고 그중 하나가 데모 전용인데, **레지스트리는 하나뿐이고
데모·프로덕션 두 경로에 다 쓴다.** 그 차이가 코드에 드러나야 한다.

```python
class PostingMeta(BaseModel):
    company: str
    title: str
    posting_date: date
    modification_timestamp: str        # 승인 무효화의 기준
    expiration_date: date | None
    active: bool
    # experience_level / required_education_level 은 여기 없다. 아래 경고를 읽어라.

class PostingRegistry(Protocol):
    """공고의 메타데이터·상태를 조회한다. 본문·이미지는 다루지 않는다."""
    def lookup(self, company: str, title: str) -> PostingMeta | None: ...
    def current(self, posting_id: str) -> PostingMeta | None: ...

class SaraminRegistry:
    """사람인 오픈 API. ✅ 전부 약관 범위 안이다 —
    검색 API를 검색·메타데이터 용도로만 쓴다.
    docs/IMAGE_ACQUISITION.md §3.5 참조."""
```

레지스트리가 하는 일은 **둘**이다.

| 역할 | 쓰는 필드 | 결과 |
|---|---|---|
| ① 동일성 확인 | `company` · `title` · `posting_date` | 고객사가 보낸 게 실제 게시된 공고인지 |
| ② **상태·수정 감시** | `active` · `expiration_date` · `modification_timestamp` | 마감 공고 차단 · **승인 무효화**(검산 G7) |
| ~~③ 파싱 교차검증~~ | — | **잘라냈다.** 아래를 읽어라 |

> ⛔ **`experience-level` · `required-education-level` · `keyword` · `salary` · `job-code`를
> 읽지 마라.** 이 필드들은 **요구조건 텍스트 그 자체**라, 대조에만 쓰더라도 그 텍스트가
> 결과에 영향을 준 것이 된다 — 과제 CRITICAL(**공고 원문 복사·붙여넣기 금지**) 위반 소지다.
> **`PostingMeta`에 필드 자체를 만들지 않는다.** 담지 않으면 실수로 쓸 수도 없다.
> 정본은 `docs/SARAMIN_API.md` §3, 결정은 `src/CLAUDE.md:194-199`.
>
> **잃는 것을 적어둔다**: 독립 대조군이 사라진다. 파싱 검증이 OCR 경로 안에서만
> 이뤄지므로 **원본 이미지가 잘못 파싱됐을 때 잡아줄 자동 수단이 없다.**
> 남은 방어는 **승인 게이트(사람)** 하나뿐이다. 이건 트레이드오프이고, 우리는
> 「원문 복붙 0」쪽을 택했다.

### `src/matching/source/client_feed.py` — 프로덕션 경로 (**구현하지 않는다**)

```python
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
    def list_postings(self, **q): raise NotImplementedError(...)
    def fetch_images(self, posting_id): raise NotImplementedError(...)
```

**본문을 채우지 마라.** 인터페이스와 docstring만 둔다. 이유: 과제가 구현 불필요를
명시했고, 구현하면 검증 안 된 코드가 제출물에 들어간다.

### `src/matching/source/provenance.py` — 출처 증거. **이 파일의 담당자는 step 2다**

이미지는 `.gitignore`이고, 본문 저장도 공고 ID·URL도 금지다. 그러면 **clone한 사람이
「이 사람이 사람인 이미지 공고를 확보해 이미지에서 파싱했다」를 확인할 파일이 하나도 없다.**
남길 수 있는 것만 남긴다 — **해시**다.

```python
class Provenance(BaseModel):
    posting_id: str
    source_kind: Literal["saramin_api", "local", "client_feed"]
    acquired_at: datetime
    target_position: str | None      # 한 공고에 직무가 여럿일 때
    image_sha256: list[str]
    image_size: list[tuple[int, int]]
    ocr_engine: str | None = None    # step 3이 채운다
    ocr_sha256: str | None = None    # step 3이 채운다
    api_verified: bool = False
    api_verified_at: datetime | None = None

def write_provenance(posting_dir: Path, source_kind: str,
                     target_position: str | None = None) -> Provenance:
    """디렉터리의 img_*.png를 읽어 해시·크기를 계산하고 provenance.json을 쓴다."""

def verify_provenance(posting_dir: Path) -> list[str]:
    """해시가 실제 파일과 맞는지, ocr.json의 해시와 일치하는지 확인. 위반 목록을 반환."""
```

**이 파일은 레포에 커밋된다.** 이미지 없이도 남는 유일한 출처 증거다. 이미지를 가진
사람은 해시를 대조해 **같은 파일인지 확인**할 수 있고, `requirements.json`의 `ocr_sha256`과
여기 값이 다르면 **조건이 다른 OCR 결과에서 나온 것**이다.

**원문도 URL도 넣지 마라.** 해시는 원문이 아니다 — 그게 이 설계의 요점이다.

### CLI — 이미지를 놓은 사람이 부르는 명령

```bash
python -m matching acquire --posting data/postings/kt-b2c \
    --source local --position "B2C마케팅&세일즈"
```

`docs/SCHEDULE.md` §2의 **경로 B**(키 미발급 → 이미지 수동 확보)에서, 사람이 이미지를
디렉터리에 놓은 **직후** 이 명령을 부른다. 하는 일은 `write_provenance` 하나다.

- 경로 A(키 있음)에서는 `SaraminSource.fetch_images()`가 **내부에서 자동 호출**한다.
  두 경로가 같은 파일을 만든다
- `img_*.png`가 0장이면 **예외를 던지고 파일을 쓰지 않는다.** 빈 증거를 만들지 마라

### `src/matching/source/__init__.py`

```python
def get_source(kind: str, settings: Settings) -> PostingSource: ...
```

`SaraminSource`의 클래스 docstring **첫 줄**에 다음을 박는다.

```
데모 전용. 프로덕션에서 이 어댑터를 쓰면 안 된다 —
서비스 제공사가 직접 수집하는 구조가 되어 DB제작자의 권리·부정경쟁방지법·
이용약관 문제가 발생한다. 프로덕션 경로는 ClientFeedSource다.
docs/LEGAL_ARCHITECTURE.md 참조.
```

`PostingRef.source_kind`(`saramin_api` / `local` / `client_feed`)가 **결과 JSON에 그대로
실려** UI까지 간다. `saramin_api`면 UI가 「데모 데이터」 배지를 띄운다. 데모 결과를
프로덕션 결과와 헷갈릴 수 없게 만드는 것이 목적이다.

## Acceptance Criteria

```bash
ruff check src/matching/source
pytest tests/test_source.py tests/test_provenance.py -q
python -m matching acquire --help

# --- 요구 ①: 판정을 산문이 아니라 기계로 한다 ---
python3 - <<'PYEOF'
import json, pathlib
q = pathlib.Path("data/.saramin_quota.json")
ok = q.exists() and any(r.get("status") == 200 for r in json.loads(q.read_text()).get("calls", []))
want = "verified" if ok else "unverified"
idx = json.loads(pathlib.Path("phases/matching-engine/index.json").read_text())
got = next(s for s in idx["steps"] if s["step"] == 2)["requirement_status"]["req1_api_crawl"]
assert got == want, f"req1_api_crawl={got} 인데 실호출 기록은 {'있음' if ok else '없음'} → {want} 여야 한다"
print(f"req1_api_crawl = {got} (실호출 기록 {'있음' if ok else '없음'})")
PYEOF

# --- 요구 ⑧: 법적 우회 구조는 **설계만**. 구현하면 요구 위반이다 ---
python3 -c "
import pathlib,re
s=pathlib.Path('src/matching/source/client_feed.py').read_text()
assert s.count('NotImplementedError')>=2, 'ClientFeedSource가 구현돼 있다 — 요구 ⑧ 위반'
assert 'LEGAL_ARCHITECTURE' in s, '설계 문서 참조가 docstring에 없다'
print('요구 ⑧: 인터페이스만 존재')
"
```

**첫 블록이 요구 ①의 AC다.** 이전엔 「`.saramin_quota.json`에 200 응답이 있을 때만
`verified`」가 **산문으로만** 있었다 — 산문은 지켜지지 않아도 초록이 뜬다.
이제 **실호출 기록과 기록된 상태가 어긋나면 AC가 깨진다.** 올려 적는 것도, 내려 적는
것도 막힌다.

`tests/test_source.py`는 **네트워크를 쓰지 않는다.** `local.py`만 테스트하고,
`saramin.py`는 키가 없을 때 `SourceUnavailable`을 던지는지만 확인한다.

`tests/test_provenance.py` 최소 케이스:

- 임시 디렉터리에 png 2장을 놓고 `write_provenance`가 **해시 2개와 크기 2개**를 쓰는지
- **이미지 0장이면 예외를 던지고 파일을 안 만드는지**
- 파일을 한 바이트 바꾸면 `verify_provenance`가 **위반을 잡는지**
- `provenance.json`에 **원문·URL·공고 ID가 안 들어가는지**
  (`assert not re.search(r'https?://|rec_idx', raw)`)

**예상 소요: 40분** (`docs/SCHEDULE.md` §1의 04:30 구간).

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트:
   - `PostingSource` Protocol을 두 어댑터가 모두 만족하는가?
   - 쿼터 기록 파일이 `.gitignore`에 걸리는가? (`data/.saramin_quota.json`)
   - access-key가 로그·예외 메시지에 찍히지 않는가?
   - **`PostingMeta`에 조건성 필드가 없는가?**
     (`grep -nE "experience_level|required_education|keyword|salary|job_code" src/matching/source/` 가 비어야 한다)
3. `index.json`의 step 2를 업데이트한다. **아래 규칙 그대로 적는다.**

### status 규칙 — 이 step이 담당하는 것은 코드지 데이터가 아니다

> **2026-09-01 02:40 KST 개정.** 이전 판은 「키가 없어 크롤링을 못 했어도 `status`를
> `completed`로 두라」고 지시했다. 그건 **요구 ①②를 한 번도 실행 못 한 상태를 정상 종료로
> 기록하라는 지시**였고, 하네스 자기 규약(`harness.md:124` — 외부 자원 부재 → `blocked` 후
> 즉시 중단)을 정면으로 뒤집었다. 그러면서 근거로 든 「`local.py`만으로 다음 step이
> 진행 가능하다」도 성립하지 않았다 — `local.py`는 **이미 내려받은 이미지를 읽는**
> 어댑터인데, 키가 없으면 그 이미지가 애초에 없다.

**경계를 다시 그었다.**

| | 담당 | 키가 없을 때 |
|---|---|---|
| **step 2** (이 step) | 어댑터 **코드**. 네트워크를 쓰지 않는 테스트 | `completed` — 코드는 완성됐다 |
| **step 3** | 실제 공고 이미지를 **읽는 것** | **`blocked`** — 입력이 없다 |

- step 2의 `status`는 **AC 커맨드 통과 여부만으로** 정한다. AC가 네트워크를 안 쓰므로
  키 유무와 무관하게 판정된다. 이건 도피가 아니라 **경계가 맞아떨어지는 것**이다
- **키가 없으면 `summary`에 정확히 이렇게 적는다**:
  `"어댑터 코드 완성. SaraminSource는 키 미발급으로 실호출 미검증 — 요구 ① 실증은 step 3의 전제조건"`
- **`index.json`의 step 2 항목에 `requirement_status` 필드를 추가한다.**
  이게 요구 ①②의 실증 여부를 status와 분리해 기록하는 자리다:

```json
{ "step": 2, "name": "posting-source", "status": "completed",
  "requirement_status": { "req1_api_crawl": "unverified", "req2_image_parse": "pending" },
  "summary": "..." }
```

  값은 `"verified"`(실호출 로그 있음) · `"unverified"`(코드만) · `"pending"`(아직 안 함) 셋뿐이다.
  **`status: completed`가 `req1: verified`를 뜻하지 않는다.** 두 칸이 따로 있는 이유가 그것이다.

- `req1_api_crawl`을 `"verified"`로 올릴 수 있는 조건은 **하나뿐**이다 —
  `data/.saramin_quota.json`에 200 응답 기록이 남아 있을 때. 그 파일이 없으면 `"unverified"`다.
  자기 판단으로 올리지 마라.

### 이 step이 끝난 뒤 누가 무엇을 하나 — 공백을 남기지 않는다

| 행위 | 누가 | 언제 | 무엇이 검사하나 |
|---|---|---|---|
| `provenance.py` **코드** 작성 | **step 2** (이 step) | 지금 | 위 AC의 `pytest tests/test_provenance.py` |
| 이미지를 `data/postings/{id}/`에 **놓는 것** | **사람** | `docs/SCHEDULE.md` §2의 06:00 분기 | step 3의 전제조건 게이트 |
| `provenance.json` **생성** | `python -m matching acquire` (사람이 부름) 또는 `SaraminSource.fetch_images()` (자동) | 이미지를 놓은 직후 | **step 3의 AC** |
| `ocr_engine`·`ocr_sha256` 채우기 | **step 3** | 파싱 직후 | step 3의 AC |

**「사람이 한다」를 적는 것이 공백을 메우는 방법이다.** 이미지를 내려받는 행위는 Claude
세션이 할 수 없다(브라우저가 필요하다). 그걸 step으로 위장하면 안 돌아가는 step이 하나
생긴다. **대신 그 앞뒤를 코드로 만들고, 사람이 안 하면 step 3이 `blocked`로 멈춘다.**

## 금지사항

- **공고 본문 텍스트를 API 응답에서 가져와 저장하지 마라.** 이유가 **둘**이다.
  (1) 과제 CRITICAL — 이미지에서 파싱한 결과만이 파이프라인 입력이다.
  (2) **원문을 쌓으면 그게 곧 DB 복제**다 (`docs/LEGAL_ARCHITECTURE.md` §3-③).
  제목·회사명 같은 메타데이터는 괜찮지만 **본문은 이미지만** 쓴다.
- **`ClientFeedSource`를 구현하지 마라.** 이유: 과제가 구현 불필요를 명시했다.
- 사람인 공고 ID·URL을 결과 JSON에 남기지 마라. 이유: 우리 DB가 사람인 DB의 부분
  복제가 된다.
- 이미지가 아닌 공고(HTML 본문 공고)를 대상으로 삼지 마라. 이유: 과제가 이미지 공고를
  지정했다.
- API 키를 URL째로 로깅하지 마라. 이유: 쿼리 파라미터 인증이라 URL에 키가 들어간다.
  로깅 시 `access-key=***`로 치환한다.
- 재시도 루프를 무한으로 돌리지 마라. 이유: 1일 500회 제한을 몇 초 만에 태운다.
