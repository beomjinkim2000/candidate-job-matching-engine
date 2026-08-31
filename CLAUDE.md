# 지원자-공고 매칭 스코어링 엔진 (그룹바이 채용 과제)

과제 원문: `/Users/apple/Downloads/지원자-공고_매칭_스코어링_엔진_과제 (2).html`
마감: 과제 수령 후 **24시간**. 진행 상황은 `Plans.md` (Status 컬럼만 신뢰).

---

## 절대 지켜야 할 것

### 데이터
- CRITICAL: 사람인(saramin.co.kr) 공고를 **API로 크롤링**해서 확보한다. **공고 내용이 이미지인 공고**를 고른다.
- CRITICAL: **공고 원문 텍스트 복사·붙여넣기 금지.** 이미지에서 파싱한 결과만이 파이프라인의 입력이다.
- CRITICAL: 공고 **2개, 서로 다른 직군**. 각 공고마다 목업 이력서 **6명** — 완벽 매칭 2 / 부분 매칭 3 / 미스매칭 1.

### 엔진
- CRITICAL: 점수는 **0~100점**, **랭킹으로 정렬**, **사람이 읽을 수 있는 근거**를 함께 낸다.
- CRITICAL: **직군 무관 일반화.** 특정 스킬셋·직군을 하드코딩하지 않는다.
- CLI 또는 API 엔드포인트 **하나**로 실행 가능해야 한다. 결과 확인용 **간단한 UI**를 만든다.
- CRITICAL: **법적 우회 구조는 구현하지 않는다.** README에 아키텍처로만 정리한다.

### 제출
- GitHub **public** 레포 (코드 + README + 목업 데이터셋 + 최소 테스트).
- CRITICAL: **`README.md`를 만들지 않는다.** 초안도, 임시본도, "나중에 지우면 되는" 버전도 만들지 않는다.
  README는 **지원자 본인이 직접 쓴다.** Claude가 하는 일은 `docs/`에 **소재만** 쌓는 것뿐이다
  (`docs/TRADEOFFS.md` 등). 다른 문서에서 README를 언급할 때도 파일을 생성하지 않는다.
- 테스트는 커버리지가 아니라 **어떤 케이스를 골랐는지**가 평가 대상이다.
- **본인의 문제 정의와 판단**이 드러나야 한다. AI 산출물을 그대로 내지 않는다.

### 보안
- CRITICAL: OpenAI API 키는 **`.env`로만** 관리한다. 코드·문서·로그·화면 출력 어디에도 키 원문을 노출하지 않는다.
- CRITICAL: push 전 `git ls-files`로 `.env` 미포함을 확인한다.

---

## 기술 스택

Python 3.x / FastAPI (단일 엔드포인트) + 정적 HTML UI · OpenAI API (공고 이미지 파싱) · pytest / ruff

## 작업 규칙

- 커밋 메시지는 conventional commits (`feat:` `fix:` `docs:` `refactor:`).
- 로컬 테스트는 **변경한 파일만** (`pytest tests/test_<대상>.py -q`). 전체 검증은 push 후 CI.
- 커밋 전 `ruff check <변경 파일>`.
- 사용자용 설명·브리프는 **`explain-at-my-level` 스킬 기준**으로 쓴다.

---

## 참조

| 위치 | 내용 |
|---|---|
| `docs/CLAUDE.md` | 문서 지도 · 문헌 검토 결과 · 인용 시 주의사항 |
| `docs/SARAMIN_API.md` | **사람인 API 사양과 우리가 쓰는 범위.** 조건성 필드(`experience-level`·`required-education-level` 등)는 **읽지 않는다** — 원문 복붙 금지. crawler·registry 구현 전 필독 |
| `~/Documents/그룹바이_볼트/` | **옵시디언 볼트 — 기준을 어떻게 숫자로 바꾸는가만** 다룬다. `점수 기준 한눈에.canvas`가 진입점 |
| `src/CLAUDE.md` | 확정된 아키텍처와 스코어링 규칙 (**구현 전 필독**) |
| `tests/CLAUDE.md` | 테스트 케이스 선정 근거 |
| `scripts/CLAUDE.md` | Harness 실행 스크립트 |
| `.claude/commands/harness.md` | Harness 워크플로우 (탐색 → 논의 → Step 설계 → 실행) |
| `phases/matching-engine/` | 실행 step 파일 (하네스가 순차 실행) |

### 외부 의존성 상태
- **사람인 API**: 즉시 발급 아님. 이용신청 → 승인 메일 → 앱 등록 → access-key. **1일 500회 제한.** 현재 승인 대기 중.
