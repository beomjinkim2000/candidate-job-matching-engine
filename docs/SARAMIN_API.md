# 사람인 Open API — 사양과 우리가 쓰는 범위

출처: <https://oapi.saramin.co.kr/guide/job-search> · <https://oapi.saramin.co.kr/guide/job-search-id>
(2026-08-31 확인)

> **이 문서의 핵심은 사양이 아니라 경계다.**
> 응답 필드 중 **조건성 필드는 읽지 않는다** — 과제 CRITICAL(원문 복붙 금지) 때문이다.
> 무엇을 왜 안 읽는지는 §3.

---

## 1. 기본

| | |
|---|---|
| 엔드포인트 | `GET https://oapi.saramin.co.kr/job-search` |
| 인증 | 쿼리 파라미터 **`access-key`** (필수) |
| 헤더 | `Accept: application/json` 또는 `application/xml` |
| 단건 조회 | 같은 엔드포인트에 **`id`** 파라미터 |

**`access-key`는 `.env`로만 관리한다.** 코드·문서·로그·화면 어디에도 값을 쓰지 않는다.

```bash
# .env
SARAMIN_ACCESS_KEY=...
```

### 파라미터 표기 규칙

- 파라미터 **이름은 대소문자 구분**, 값은 구분 안 함
- 여러 값은 **공백 또는 쉼표**로 구분
- **와일드카드·AND/OR 연산자 없음**
- 한 파라미터 안의 여러 값은 **OR**, 서로 다른 파라미터끼리는 **AND**

---

## 2. 요청 파라미터

### 우리가 쓰는 것

| 파라미터 | 쓰임 |
|---|---|
| `access-key` | 필수 |
| `id` | 단건 조회. **동일성 확인·상태 감시의 기본 경로** |
| `keywords` | 데모에서 이미지 공고를 찾을 때 |
| `published_min` · `published_max` | 게시 기간 필터 |
| **`updated_min` · `updated_max`** | **수정 감시.** 「이 시각 이후 수정된 공고」 질의 |
| `sort=ud` | 최근 수정순 정렬 |
| `start` · `count` | 페이지네이션 |
| `fields` | 선택 필드 요청 |

### 페이지네이션

| | 기본값 | 최대 |
|---|---|---|
| `start` | 0 | — (0부터 시작하는 **페이지 번호**) |
| `count` | 10 | **110** |

### 정렬 `sort`

`pd` 등록일 내림차순(기본) · `pa` 등록일 오름차순 · **`ud` 수정일 최근순** ·
`ua` 수정일 오름차순 · `da` 마감일 오름차순 · `dd` 마감일 내림차순 ·
`rc` 조회수 내림차순 · `ac` 지원자수 내림차순

### 안 쓰는 검색 파라미터

`bbs_gb` · `stock` · `sr` · `loc_cd` · `loc_mcd` · `loc_bcd` · `ind_cd` ·
`job_mid_cd` · `job_cd` · `job_type` · `edu_lv` · `deadline`

**직군·지역으로 거르지 않는다.** 직군 무관 일반화를 검증하려면 **직군이 서로 다른
공고 2개**가 필요한데, 직군 코드로 좁히면 그 검증이 무의미해진다.

---

## 3. 응답 필드 — 쓰는 것과 안 읽는 것

**이 절이 이 문서의 본체다.**

### ✅ 상태·식별 — 원문이 아니다. 쓴다

| 필드 | 기본 제공 | 쓰임 |
|---|---|---|
| `id` | ✅ | 공고 식별 |
| `url` | ✅ | **이미지에 도달하는 경로** |
| `active` | ✅ | `1` 게시중 / `0` 마감 → **마감 공고 채점 차단** |
| **`modification-timestamp`** | ✅ | **승인 무효화 판정 (검산 G7)** |
| `posting-timestamp` | ✅ | 게시 시각 |
| `opening-timestamp` | ✅ | 접수 시작 |
| `expiration-timestamp` | ✅ | 마감 시각 → 채점 차단 |
| `close-type.code` | ✅ | `1` 접수마감 · `2` 채용시 · `3` 상시 · `4` 수시 |
| `posting-date` · `expiration-date` | ❌ `fields=` 필요 | ISO 8601 표기. **timestamp로 충분해서 요청 안 함** |

### ⚠️ 식별 정보 — 대조에만

| 필드 | 쓰임 | 제한 |
|---|---|---|
| `company.detail.name` | 동일성 확인 | 대조만 |
| `company.detail.href` | — | 안 씀 |
| `position.title` | 동일성 확인 | **원문의 일부다.** 조건 생성에 쓰면 안 됨 |

`position.title`로 `Requirement`를 만들면 이미지 좌표가 없어
**검산 G4에 걸린다** — 기계적으로 막혀 있다.

### ❌ 조건성 필드 — 읽지 않는다

| 필드 | 왜 안 읽나 |
|---|---|
| **`position.experience-level`** (`code`·`min`·`max`·`name`) | **「경력 6~10년」은 요구조건 그 자체**다. 이미지에서 파싱해야 할 것을 텍스트로 받는 셈 |
| **`position.required-education-level`** | 같은 이유. 학력 요구조건이다 |
| `keyword` | 공고 본문에서 뽑은 키워드. 원문에 가깝다 |
| `salary` | 처우 조건 |
| `position.job-code` · `job-mid-code` · `industry` · `location` · `job-type` | 조건성. 게다가 **직군 코드는 일반화 검증을 오염시킨다** |
| `read-cnt` · `apply-cnt` | 채점과 무관 |

> **과제 CRITICAL**: *"공고 원문 텍스트 복사·붙여넣기 금지.
> 이미지에서 파싱한 결과만이 파이프라인의 입력이다."*
>
> 대조에만 쓰더라도 그 텍스트가 결과에 영향을 준 것이 된다.
> **회색지대를 남기지 않기로 했다.**

### 코드에서 강제한다

```python
class PostingMeta(BaseModel):
    """상태만 담는다. 조건성 필드는 필드 자체를 만들지 않는다."""
    id: str
    url: str
    active: bool
    modification_timestamp: int
    expiration_timestamp: int
    company_name: str      # 동일성 확인 전용
    position_title: str    # 동일성 확인 전용
```

**담지 않으면 실수로 쓸 수도 없다.** 파서가 응답을 이 모델로 바로 좁힌다.

---

## 4. 응답에 없는 것

**본문 텍스트도 이미지 URL도 없다.** 단건 조회(`id`)에도 없다.

이건 결함이 아니라 **성격**이다 — 채용정보 **검색·집계용** API이지 본문 배포용이 아니다.

그래서 데모에서 이미지 한 장을 얻는 데 4단계가 든다.

| 단계 | 무엇 | API인가 |
|---|---|---|
| 1 | 공고 목록 조회 | **✅ API** |
| 2 | `url`로 상세 페이지 접근 | ❌ |
| 3 | 본문 이미지 URL 추출 | ❌ **약관 밖 구간** |
| 4 | 이미지 내려받기 | ❌ |

> 과제 ⑧(법적 우회 구조)이 필요한 진짜 이유가 여기다.
> 문제는 「API를 쓴다」가 아니라 **「API가 안 주는 걸 API 밖에서 가져온다」**는 데 있다.
> 프로덕션에서 3단계가 사라지는 이유는 **고객사가 자기 이미지를 이미 갖고 있기 때문**이다.
> 정본: `docs/LEGAL_ARCHITECTURE.md` · `docs/IMAGE_ACQUISITION.md`

---

## 5. 오류 코드와 호출 한도

```json
{ "code": 2, "message": "Invalid access-key" }
```

| `code` | 뜻 | 우리 처리 |
|---|---|---|
| 1 | `access-key` 없음 | 설정 오류. 즉시 중단 |
| 2 | `access-key` 무효 | 즉시 중단 |
| 3 | 요청 파라미터 오류 | 즉시 중단 |
| **4** | **일일 요청 한도 초과** | **`LocalSource`로 전환.** 채점은 계속된다 |
| 99 | 서버 오류 | 재시도 |

**일일 한도 500회** (앱 등록 시 안내). 문서에 수치가 명시돼 있지 않아
**운영 중 `code: 4`로 확인하는 것이 유일한 신뢰 경로**다.

### 한도가 G7에 주는 제약

G7은 **채점 실행 시점마다 재조회**한다. 공고 2개면 실행당 2회다.
개발 중 반복 실행이 한도를 먹으므로 —

- `modification-timestamp`를 **짧게 캐시**하고 캐시 만료 시에만 재조회
- 캐시 만료 시간은 설정값. **기본은 짧게** — G7이 헐거워지면 존재 이유가 없다
- `code: 4`가 오면 **채점을 막지 않고 `posting_revision` 검증만 「확인 불가」로 표시**한다.
  차단하면 한도 초과가 서비스 중단이 된다

> **여기는 판단이 갈린다.** 엄격하게 하면 한도 초과 시 아무것도 못 하고,
> 느슨하게 하면 낡은 루브릭으로 채점될 수 있다.
> **「확인 불가」를 화면에 남기는 쪽**을 택했다 — 조용히 통과시키지 않으면서 멈추지도 않는다.

---

## 6. 발급 절차 — 즉시 발급이 아니다

이용신청 → **승인 메일** → 앱 등록 → `access-key` 발급.

**현재 승인 대기 중.** 막히면 `LocalSource`(저장해 둔 응답 픽스처)로 우회한다.

---

## 7. 코드 표 (참고용, 우리는 안 씀)

`/guide/code-table1` 근무형태·학력·급여 · `/guide/code-table2` 지역 ·
`/guide/code-table3` 산업·업종 · `/guide/code-table5` 직무·직종

**전부 조건성 코드**라 §3에서 차단한 것들이다. 링크만 남긴다.

---

## 관련

`docs/IMAGE_ACQUISITION.md` (이미지 확보 4단계) ·
`docs/LEGAL_ARCHITECTURE.md` (크롤링 주체와 경계) ·
`src/CLAUDE.md` (`PostingSource` vs `PostingRegistry`)
