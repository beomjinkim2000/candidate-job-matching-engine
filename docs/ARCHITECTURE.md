# 아키텍처

## 디렉토리 구조

```
run.py                  # 단일 진입점 — 설치·키·이미지·서버·브라우저
start.command / .bat    # 더블클릭용 껍데기. 로직은 전부 run.py에 있다

src/matching/
├── source/     # 공고 이미지 확보 (어댑터 3종)
├── parser/     # 이미지 → 줄·좌표 → 섹션·항목 → 조건
├── rubric/     # 조건 → 채점 항목·배점·점수 기준
├── scorer/     # 게이트 · 사실 채점 · 마스킹  (결정적)
├── judge/      # 심사위원 호출·집계          (여기만 비결정적)
├── model/      # Object + Link 그래프, 검산
├── pipeline/   # prepare() ⛔ score() — 두 동강 난 실행 경로
└── api/        # FastAPI 라우트 + 정적 HTML 한 장

data/
├── postings/<id>/   # 이미지 · 조건 · 출처 기록
├── resumes/<id>/    # 목업 이력서
└── runs/<run-id>/   # 실행 결과
```

**모듈 경계가 곧 책임 경계다.** 특히 `scorer`와 `judge`가 갈려 있는 것이 핵심이다 —
**세는 것은 코드가, 판단하는 것은 심사위원이** 한다.

## 데이터 흐름

```
공고 이미지 (source)
   ↓
줄 + 좌표 (parser · PaddleOCR)
   ↓
섹션·항목 (parser · 코드)          ← 불릿 · 들여쓰기 · x좌표
   ↓
헤더 역할 (parser · LLM 1회)       ← 텍스트만 보낸다. 이미지 안 보냄
   ↓
필수/우대 + 근거 등급 (parser · 코드 사다리 5단)
   ↓
갈래 term/binary/graded (rubric · LLM 1회)
   ↓
채점 항목 · 배점 · 점수 기준 (rubric · 코드)
   ↓
⛔ 사람 승인 ─────────── 여기서 실제로 멈춘다
   ↓
게이트 → 사실 채점 (scorer · 코드)
   ↓
판단 채점 (judge · 심사위원 2명, 이견 시 3번째)
   ↓
검산 G1~G7 (model)  ← 하나라도 걸리면 결과가 안 나간다
   ↓
합산 · 랭킹 (pipeline)
```

**LLM은 세 자리에서만 쓴다** — 헤더 역할 1회, 갈래 1회, 채점 132~135회.
나머지는 전부 코드라 같은 입력에 항상 같은 출력이 나온다.

## 패턴

### 승인 게이트는 필드가 아니라 절차다

파이프라인을 **두 함수로 쪼갠 것**이 이 설계의 중심이다.

```python
prepare(posting_ref, settings, ...) -> RubricProposal   # 이력서 파라미터가 없다
        ⛔
score(proposal, resumes, ...) -> RunResult              # 승인 없으면 예외
```

`prepare()`에는 이력서를 넣을 자리 자체가 없어서 **채점하고 싶어도 못 한다.**
`score()`는 `approved_at`이 없으면 `ApprovalRequired`를 던진다.
건너뛰려면 `skip_approval`을 명시해야 하고, 그때는 결과에 `unapproved=True`가 실려
화면 상단에 배지가 붙는다.

### 근거는 문장이 아니라 Link다

```
Score ──grounded_in──▶ Evidence ──supports──▶ Criterion
                                       └──derived_from──▶ Requirement
                                                   └──extracted_from──▶ 공고 그림 좌표
```

관계 종류는 **5개로 고정**한다 — `extracted_from` · `derived_from` · `supports` ·
`grounded_in` · `contradicts`. 늘리면 검산 규칙이 따라 늘어난다.

근거 문장은 이 Link 위에 **렌더링하는 결과물**이지 저장 단위가 아니다.

### 검산은 테스트가 아니라 런타임 게이트다

`model`이 결과를 내보내기 전에 G1~G7을 검사한다. 개발 중에만 도는 것이 아니라
**매 실행마다** 돈다. 특히 **G4(모든 조건에 이미지 좌표가 있는가)가
「공고 원문을 복붙하지 않았다」의 기계적 증명**이다.

### 어댑터로 데모와 프로덕션을 가른다

| 어댑터 | 쓰임 | 상태 |
|---|---|---|
| `LocalSource` | 디렉터리에 놓인 이미지를 읽는다 | 사용 중 |
| `SaraminSource` | API + 페이지 스크래핑 (**데모 전용**) | 코드만. **키 반려로 미실행** |
| `ClientFeedSource` | 고객사가 push (**프로덕션**) | **인터페이스만. 구현 안 함** |

`list_postings()`만 API이고 `fetch_images()`는 아니다. **메서드를 합치지 않는다** —
합치면 「API로 가져왔다」와 「스크래핑했다」가 한 덩어리가 되어 법적 경계가 흐려진다.

## 상태 관리

**서버는 상태를 거의 들지 않는다.**

| 무엇 | 어디에 |
|---|---|
| 조건·루브릭·점수·Link | `data/` 밑 JSON. 실행마다 파일로 남는다 |
| 승인 여부 | `RubricProposal.approved_at` — 메모리 + 결과 JSON |
| 화면이 보던 결과 | 브라우저 `localStorage` (공고 id + run id만) |
| 토큰·비용 누적 | `data/.judge_usage.json` — **프로세스를 새로 띄워도 리셋되지 않는다** |
| 파싱 캐시 | `ocr.json` · `header_roles.json` · `requirement_branches.json`. **커밋하지 않는다** |

**원본은 적재하지 않는다.** 공고 이미지·OCR 전사본·이력서 원문은 처리 후 파기하고
구조화된 결과만 남기는 것이 원칙이다 — 다만 **제출 레포에 한해 공고 이미지 2장은
예외로 넣었다**(ADR-011 개정).

## 프런트엔드

**정적 HTML 한 장**(`api/static/index.html`)이고 빌드 도구도 프레임워크도 없다.
`h()` 헬퍼로 DOM을 만들고 `go(n)`으로 화면을 바꾼다. 외부 CDN을 부르지 않는다 —
평가자 머신이 오프라인이어도 화면이 뜬다.

서버가 진행률을 내보내지 않으므로, 진행 표시는 **「경과 시간으로 찍은 추정」이라고
화면에 적는다.** 아는 척하지 않는 것이 규칙이다.
