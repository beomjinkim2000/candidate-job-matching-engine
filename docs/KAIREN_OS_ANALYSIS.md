# Kairen OS를 이 엔진에 적용하기

원본: `docs/refs/Kairen_OS_Concept.html` (Concept Document v0.8, 2026-07-06)

이 문서는 Kairen OS를 요약하지 않는다. **우리 스코어링 엔진의 미해결 문제 하나에
Kairen OS가 답을 주는지**만 따진다. 그 문제는 이것이다.

> 과제 요구: *"점수의 근거를 사람이 읽을 수 있는 형태로 제시"*
> 지금까지 우리 답: "축별 점수 + 이유 문장 + 이력서 원문 인용"
> 문제: **그 이유 문장이 진짜인지 검증할 방법이 없다.**

---

## 1. Kairen OS가 실제로 하는 말

Kairen OS는 업무 관리 도구가 아니라 **판단과 학습을 보존하는 운영 체계**다.
핵심 주장은 한 문장이다.

> `idea → task → done` 만 기록하면 **왜 시작했는지 · 어떤 근거였는지 ·
> 무엇을 버렸는지 · 실제로 어땠는지**가 사라진다.

이걸 막으려고 세 단어로 쪼갠다.

| 구성 요소 | Kairen OS의 정의 | 예시 |
|---|---|---|
| **Object** | 따로 보존할 가치가 있는 기록 | Evidence note, Decision note |
| **Link** | 두 기록이 **왜** 연결되는지 말하는 관계 | `Evidence supports Decision` |
| **Action** | 누가 무엇을 해서 기록을 만들거나 바꿨는지 | `capture_evidence`, `make_decision` |

그리고 운영 원칙 7개 중 우리에게 직접 걸리는 것이 넷이다.

- **Evidence over Opinion** — 의견은 출발점이지만 중요한 결정은 근거와 연결한다
- **Link over Memory** — 사람이 기억하지 않아도 관계가 보이게 한다
- **Result before Done** — 의미 있는 일은 결과와 학습을 남겨야 닫힌다
- **AI Platform assists, Human Platform decides** — AI는 후보를 만들고 누락을 찾고,
  사람은 중요한 link·decision·approval을 확정한다

마지막으로, 이 문서가 정의한 **잘 돌아갈 때의 신호**가 우리 검산 규칙 그대로다.

> "Decision에 Evidence 또는 **E0 판단 표시**가 있다 → 선택의 근거 수준이 보인다"
> "AI가 만든 내용은 **reviewStatus**로 구분된다 → 사람 판단과 AI 초안이 섞이지 않는다"
> "Work만 많고 Evidence, Decision, Result가 없다면 시스템은 아직 학습하지 못하고 있다"

---

## 2. 이 프로젝트에 옮겨온 것 — 가장 큰 하나

### 근거는 **문장**이 아니라 **Link**다

지금까지 우리는 근거를 이렇게 생각했다.

```
score: 4
rationale: "쿠팡에서 결제 API를 설계한 경험이 공고의 '대규모 트래픽 처리'와 직접 대응됨"
```

이 서술은 **검증할 수 없다.** 저 문장이 참인지 확인하려면 사람이 이력서를 다시 읽어야 한다.
LLM이 없는 경험을 지어내도 문장만 봐서는 모른다.

Kairen OS의 어휘로 다시 쓰면 이렇게 된다.

```
Object   R-03  Requirement   "대규모 트래픽 처리 경험"      ← 공고 이미지에서 추출
Object   C-03  Criterion     루브릭 항목 (1/3/5점 기준점)   ← R-03에서 생성
Object   E-11  Evidence      이력서 span [412..487]        ← 이력서 원문의 위치
Object   S-03  Score         4점                           ← 심사위원 판정

Link  C-03  derived_from   R-03
Link  E-11  supports       C-03
Link  S-03  grounded_in    E-11
Link  R-03  extracted_from POSTING_IMG#1 bbox(120,340,880,392)
```

**차이가 결정적이다.**

| | 문장으로 된 근거 | Link로 된 근거 |
|---|---|---|
| 진위 확인 | 사람이 읽어야 함 | `E-11`의 span이 이력서에 실재하는지 **코드가 대조** |
| 누락 탐지 | 불가능 | `supports` link가 0개인 Criterion을 **세면 됨** |
| 추적 | 불가능 | 점수 → 근거 → 이력서 원문 → 공고 원문 좌표까지 **끝까지 따라감** |
| UI | 글자 덩어리 | 클릭하면 원문 위치가 하이라이트됨 |

> [!example] 손으로 따라가기
> 지원자 3번이 C-03에서 4점을 받았다. UI에서 그 4점을 클릭하면
> `S-03 → grounded_in → E-11` 을 따라가 이력서 412~487번째 글자가 켜지고,
> 옆에는 `C-03 → derived_from → R-03 → extracted_from → 공고 이미지 (120,340)` 을 따라가
> 공고 이미지의 그 줄에 네모가 쳐진다. **점수 하나에서 공고 원본 픽셀까지 한 번에 간다.**

이건 우리가 이미 갖고 있던 재료(evidence span, 축별 점수)를 **다시 조립한 것**이다.
새 데이터를 안 만들고 구조만 바꾼다. 그래서 비용이 거의 0이다.

---

## 3. 그대로 가져오는 규칙 3개

### 3-1. `reviewStatus` — AI 초안과 사람 확정을 섞지 않는다

> Kairen OS: *"AI가 만든 내용은 기본적으로 draft다. `human_validated`는 사람이 현재
> revision을 확인한 뒤에만 쓴다."*

우리 파이프라인에서 사람의 확인 없이 만들어지는 것이 셋이다.

1. 공고 이미지에서 뽑은 **조건**(Requirement)
2. 조건에서 만든 **루브릭 항목**(Criterion) — 특히 **필수/우대 판정**
3. 심사위원이 준 **점수**(Score)

지금까지 우리 UI 설계는 이 셋을 다 똑같은 확신으로 보여줬다. Kairen OS 규칙을 적용하면
**전부 `draft`로 시작**하고, 고객사가 루브릭을 승인한 항목만 `human_validated`가 된다.

화면에서 이게 배지 하나로 갈린다.

```
필수  Python 3년 이상            [사람 확인함]
필수  대규모 트래픽 처리 경험     [AI 초안 · 섹션 없이 추론]
우대  Kubernetes                 [AI 초안]
```

**이건 이미 결정한 「고객사 승인」 절차에 데이터 모델을 붙여준다.** 승인이 무엇을 바꾸는지가
지금까지 말뿐이었는데, 이제 필드 하나가 뒤집히는 일이 된다.

### 3-2. `E0` — 근거 수준을 점수와 분리해 표시한다

> Kairen OS: *"Decision에 Evidence 또는 **E0 판단 표시**가 있다."*

E0는 **근거 없이 판단했음을 숨기지 않고 적는 표식**이다. 우리에게 이게 필요한 자리가 있다.

`src/CLAUDE.md`의 필수/우대 판정 사다리는 5단계다. 1~2단계(섹션·수식어)는 공고에 적힌
대로지만, 3~5단계(담당업무 대응·반복·시각 강조)는 **우리가 추론한 것**이다. 지금 규칙은
"모호함으로 표시한다"인데, 이걸 E0 계열의 **근거 등급**으로 바꾼다.

| 등급 | 뜻 | 우리 사다리 |
|---|---|---|
| **E2** | 공고에 명시됨 | 1단계(섹션) · 2단계(수식어) |
| **E1** | 공고 구조에서 추론함 | 3단계(담당업무 대응) · 4단계(반복) |
| **E0** | 근거가 약함. 판정을 바꾸지 않고 표시만 함 | 5단계(시각 강조) |

**등급을 점수에 곱하지 않는다.** Kairen OS가 근거 수준을 판단과 분리해 표기하듯, 우리도
등급은 표시만 하고 점수는 건드리지 않는다. 곱하면 두 가지가 섞여 무엇 때문에 점수가
낮은지 알 수 없게 된다.

> 이 분리는 임상의 **GRADE 체계**(근거 수준과 권고 강도를 따로 매김)와 같은 발상이다.
> 우리는 Kairen OS 쪽 어휘를 쓴다.

### 3-3. Governance check — 검산을 기본값으로

> Kairen OS: *"Governance by Default — source, review, approval 기준을 가볍게 유지한다"*
> *"Work만 많고 Evidence, Decision, Result가 없다면 시스템은 아직 학습하지 못하고 있다"*

Link 구조가 생기면 검산이 **코드로 셀 수 있는 것**이 된다. 결과를 내보내기 전에 다음을
전부 통과해야 한다.

| 검산 | 규칙 | 위반하면 |
|---|---|---|
| G1 | 모든 `Score`는 최소 1개의 `grounded_in` Link를 가진다 | 근거 없는 점수 → 차단 |
| G2 | 모든 `Evidence`의 span이 원문 문자열에 실재한다 | 지어낸 인용 → 차단 |
| G3 | 모든 `Criterion`은 `derived_from` Requirement가 있다 | 직군 하드코딩 흔적 → 차단 |
| G4 | 모든 `Requirement`는 `extracted_from` 이미지 좌표가 있다 | 원문 복붙 의심 → 차단 |
| G5 | 모든 `Requirement`에 근거 등급(E2/E1/E0)이 있다 | 표시 누락 → 차단 |

**G4가 과제 CRITICAL 규칙("공고 원문 텍스트 복사·붙여넣기 금지")의 기계적 증명이다.**
좌표 없는 조건이 하나라도 있으면 그건 이미지에서 나온 게 아니다.

---

## 4. 가져오지 않은 것

Kairen OS는 조직 운영 체계다. 24시간짜리 과제에 그대로 얹으면 과잉이다.

| 안 가져온 것 | 이유 |
|---|---|
| Action(누가 무엇을 했는지 기록)을 1급 Object로 | 실행자가 파이프라인 하나뿐이라 구분할 대상이 없다. Kairen OS 자신도 *"Action Log는 별도 조회가 필요할 때 만든다"*고 미뤄뒀다 |
| Direction / Test / Issue / Task Type | Decision Cycle workflow용 Type이다. 우리 workflow는 채점이지 의사결정 cycle이 아니다 |
| Outcome / Learning Loop (결과가 다음 판단을 개선) | **채용 결과 데이터가 없다.** 이 계층은 실제 입사 후 성과가 돌아와야 도는데 우리는 그걸 못 받는다. `docs/TRADEOFFS.md` D-2(사후 보정 불가)와 같은 한계다 |
| 폴더 경계 · Operating Map · Playbook | 문서 운영 규약이라 코드 프로젝트에 대응물이 없다 |

**Learning Loop를 못 돌린다는 게 이 과제의 가장 정직한 한계다.** Kairen OS 기준으로 보면
우리 시스템은 `Signal → Meaning → Action → Result`까지만 있고 `Learning → Better
Judgment`가 비어 있다. README에 이걸 적는다.

---

## 5. 왜 이 틀을 골랐는가 (Kairen OS가 아니어도 됐는가)

같은 일을 하는 다른 어휘가 있다. 지식그래프(RDF triple), provenance 표준(W3C PROV),
XAI의 attribution graph. 셋 다 근거를 그래프로 본다는 점에서 같다.

Kairen OS를 쓴 이유는 **셋이 안 주는 것을 주기 때문**이다.

1. **`reviewStatus`(draft / human_validated)** — 다른 틀들은 사람이 확인했는지를 1급
   개념으로 안 둔다. 우리 설계의 핵심(AI가 후보를 내고 고객사가 확정)이 정확히 이 축이다
2. **E0 표기** — "근거가 없음"을 데이터로 남기라는 규칙. 보통은 근거가 없으면 그냥 빈칸이다
3. **작게 유지하라는 규칙**(§7 What Stays Small) — *"분리 기준은 개념적으로 멋진가가
   아니다. 실제 운영에서 같은 불편이 반복되는가다."* 24시간 과제에서 이 문장이 브레이크다

---

## 6. 이 문서가 바꾸는 것 — 요약

| 대상 | 이전 | 이후 |
|---|---|---|
| 근거의 형태 | 자유 서술 문장 | **Object + Link 그래프**, 문장은 그 위에 렌더링 |
| 근거 검증 | 불가능 | **검산 G1~G5**로 코드가 차단 |
| 필수/우대 모호함 | "모호함" 한 글자 | **근거 등급 E2 / E1 / E0** |
| 고객사 승인 | 절차 설명뿐 | `reviewStatus: draft → human_validated` 필드 전이 |
| 원문 복붙 안 했음의 증명 | 말로 주장 | **G4 검산** — 좌표 없는 조건이 0개 |
| UI | 점수와 이유 나열 | 점수 → 근거 → 이력서 span → 공고 이미지 bbox **추적** |

반영 위치: `src/CLAUDE.md`(데이터 모델·검산) · `phases/matching-engine/`(구현 step) ·
`docs/TRADEOFFS.md`(Learning Loop 부재를 한계로 추가).
