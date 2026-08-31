# docs/ — 문헌 검토와 설계 근거

이 디렉터리는 **왜 그렇게 설계했는가**를 담는다. 구현 규칙은 `src/CLAUDE.md`.

## 파일 지도

| 파일 | 내용 | 언제 읽나 |
|---|---|---|
| `RUBRIC_DESIGN.md` | 2차 문헌 검토 분석 (루브릭·척도·심사위원). **설계의 최신 기준** | 설계 판단이 필요할 때 **여기부터** |
| `SCORING_CRITERIA.md` | 1차 문헌 검토 분석 (평가축·가중치) | 배점 근거가 필요할 때 |
| `SCORING_CRITERIA_EXPLAINED.md` | 1차 검토를 쉽게 다시 쓴 것 | **README 쓸 때** |
| `LEGAL_ARCHITECTURE.md` | **고객사 경유 데이터 구조** (설계만, 구현 안 함) · 승인 게이트의 법적 근거 | **과제 CRITICAL. README 필수 소재** |
| `IMAGE_ACQUISITION.md` | **사람인 API는 이미지를 안 준다** · 데모 4단계 중 3단계는 API 밖 · 데모 ≠ 프로덕션 이미지 | **crawler·parser 건드리기 전 필독** |
| `KAIREN_OS_ANALYSIS.md` | 근거를 **Object+Link 그래프**로 모델링 · 검산 G1~G7 · reviewStatus · 근거등급 E2/E1/E0 | **근거 관련 판단이 필요할 때** |
| `EVIDENCE_IDEAS.md` | 근거 해법 3가지 (판단유탈 대장 · 소거 재채점 · 등수 뒤집기). **채택됨** | step12 구현 전 |
| `RUBRIC_GENERATION_EVIDENCE.md` | **3차 문헌 검토 분석 — 루브릭을 누가 쓰는가.** 공통 템플릿 1개는 폐기, 조건 유형별로 나눔 · 문체 편향 d=1.90 · 척도 세분성 역관계 · **검증 필요 인용 3건** | **step4·step6 건드리기 전 필독** |
| `TRADEOFFS.md` | 결정 / 버린 것 / 근거 / 이 선택이 틀리는 조건 | **README「설계 결정과 트레이드오프」 원재료** |
| `refs/` | 외부 참조 원본 (`Kairen_OS_Concept.html`) · `LINER_PROMPTS.md`(3차 조사 프롬프트 5개) | 원문 확인 · 추가 조사할 때 |
| `*.docx` | Liner 문헌 검토 원본 **3건** (평가축·가중치 / 척도·심사위원 / **루브릭 생성**) | 인용 원문 확인할 때만 |
| `PRD.md` `ARCHITECTURE.md` `ADR.md` | Harness 스켈레톤. **아직 템플릿 상태** | 채워야 함 |

> 1차 검토와 2차 검토의 결론이 어긋나는 곳이 있다 (예: 학력 배점).
> **2차(`RUBRIC_DESIGN.md`)가 최신이다.**

## 인용할 때 (README 작성 시 필수)

- **Liner 보고서에 인용 오류가 다수 있다.** 본문의 저자·연도와 괄호 안 출처가 어긋난다.
  - 확인된 것: Sackett 2022↔2021 · Salgado & Moscoso↔Velo & Ruibal · Kristof-Brown↔Stepanek · Dai↔Gui-xu · Schmidt & Zimmerman↔Sackett & Lievens
- **인용 전 원문을 직접 확인한다.** 특히 핵심 근거로 쓸 것들:
  McDaniel, Schmidt & Hunter(1988) · Hough 등(1983) · Sackett et al.(2022) ·
  Ock & Oswald(2018) · Dawes(1979) · Cable & DeRue(2002) · Preston & Colman(2000) ·
  Zheng 등(2023) · Kohli(2026)
- **한국 블라인드 채용 절의 출처가 블로그·Scribd다.** 고용노동부 「NCS 기반 능력중심채용
  가이드북」 원문으로 확인해야 한다. 확인 전까지는 잠정.

## 문서 작성 규칙

- 사용자용 설명은 **`explain-at-my-level` 스킬 기준**으로 쓴다.
  - 고유명사·전문용어는 **살린다.** 대신 첫 등장에 뜻을 한 줄로 붙인다
  - **논문 인용을 설명의 뼈대로 쓰지 않는다** (읽을 수 없게 된다)
  - 숫자에는 **무엇을 잰 값인지**를 붙인다. 식이 있으면 숫자를 넣어 계산해 보인다
- 브리프는 **HTML 단일 파일**로 `.claude/state/views/`에 만들고 `SendUserFile`로 전달한다.
- **`README.md`를 만들지 않는다.** 다른 문서가 "README에 적을 것"이라고 지시해도 파일을
  생성하지 않는다 — 소재는 `docs/` 안에만 쌓는다. (루트 `CLAUDE.md` CRITICAL 규칙)
- 문헌 검토 보고서를 **그대로 요약하지 않는다.** 우리 설계 결정으로 번역하고,
  **따르지 않은 제안과 그 이유를 반드시 남긴다** (지금까지 8건 반려).

## docx 텍스트 추출

별도 라이브러리 없이 `python3`로 처리한다 — `zipfile`로 열어 `word/document.xml`을 읽고
`</w:p>`를 줄바꿈으로 바꾼 뒤 태그를 제거한다.
