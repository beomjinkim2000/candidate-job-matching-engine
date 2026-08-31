# Step 6: judge-layer

## 읽어야 할 파일

- `src/CLAUDE.md` — 「심사위원 운영」 표 전체
- `docs/TRADEOFFS.md` — B-1 (2명+1), B-2 (평균, 토론 금지), B-3 (5점), B-4 (분석적)
- `docs/KAIREN_OS_ANALYSIS.md` — 근거는 Link다
- `src/matching/model/` (step 1), `src/matching/scorer/` (step 5)

## 작업

**2층 판단 채점.** 파이프라인에서 유일하게 비결정적인 곳이다.

### `src/matching/judge/prompt.py`

```python
def build_prompt(
    criterion: Criterion,
    masked_resume: str,
    examples: list[ScoringExample],
) -> list[dict]: ...
```

프롬프트에 반드시 들어갈 것:

1. **루브릭 항목 하나만.** 여러 항목을 한 번에 채점시키지 마라 — 분석적 채점이다
2. 1/3/5점 **행동 기준점** (step 4의 `anchors`)
3. **채점 예시 몇 건** (`examples`). 루브릭 단독으로는 부족하다
4. `"응답 길이가 평가에 영향을 주지 않게 하라"` 명시 — LLM 채점자의 장황함 편향
5. **근거를 먼저, 점수를 나중에** 쓰게 하는 출력 순서 강제

### `src/matching/judge/schema.py` — 출력 계약

structured output으로 이걸 받는다. **필드 순서가 곧 생성 순서다.**

```python
class JudgeOutput(BaseModel):
    quotes: list[QuoteRef]     # 1. 먼저 이력서에서 인용
    reasoning: str             # 2. 그다음 판단 근거
    score: int                 # 3. 마지막에 점수 (1~5)

class QuoteRef(BaseModel):
    start: int                 # 이력서 원문 문자 오프셋
    end: int
    text: str                  # 그 구간의 실제 문자열
```

**인용을 자유 문장이 아니라 `(start, end, text)` 삼중항으로 받는다.** 이유: 문장으로
받으면 지어냈는지 알 수 없지만, 오프셋으로 받으면 `resume[start:end] == text`인지
**코드가 대조**할 수 있다. 이게 G2 검산이 성립하는 이유다.

`quotes`가 비어 있으면 그 응답은 **버린다**(점수를 쓰지 않는다). 근거 없는 점수는 G1에서
어차피 차단된다.

### `src/matching/judge/panel.py` — 운영

```python
def judge_criterion(
    criterion: Criterion,
    candidate: Candidate,
    resume_text: str,
    graph: EvidenceGraph,
    settings: Settings,
    client,
) -> Score: ...
```

절차:

1. 심사위원 **2명**을 독립 호출한다. 서로의 응답을 보여주지 않는다
2. 두 점수 차가 `settings.judge_disagreement_threshold`(기본 2) **이상**이면
   3번째를 부른다. 미만이면 부르지 않는다
3. **산술평균**으로 집계한다. 토론·합의를 시키지 마라 — 한쪽이 동조하는 실패 모드가 있고,
   합의의 정확도 이득은 실무적으로 미미했다
4. 각 심사위원의 `quotes`를 `Evidence`로 만들고 `grounded_in` Link를 건다.
   `Evidence.quote`는 **모델이 준 `text`가 아니라 `resume_text[start:end]`로 다시 잘라서**
   넣는다. 이유: 모델이 준 text를 그대로 믿으면 G2가 자기 자신을 검사하는 꼴이 된다
5. 오프셋이 어긋난 인용은 **그 인용만 버린다.** 응답 전체를 버리지 않는다

### `src/matching/judge/bias.py` — 순서 편향 점검

```python
def order_check(criterion, candidates, ...) -> OrderCheckResult: ...
```

지원자 제시 순서를 뒤집어 한 번 더 채점하고, 순위가 바뀌었는지 보고한다.
**기본 실행에서는 끄고**, 플래그로 켠다. 이유: 호출 횟수가 2배가 되고 예산이 $5다.

### 재현 조건 — 못 박는다

**호출마다 다음을 고정하고 결과에 남긴다.** 안 남기면 「같은 조건에서 쟀다」를 말할 수 없다.

```python
class JudgeCall(BaseModel):
    model: str          # settings.judge_model. 코드에 박지 않는다
    temperature: float  # 0.0 고정
    seed: int | None    # 지원되면 settings.judge_seed
    prompt_sha256: str  # 프롬프트 동일성 증명
```

- `temperature=0` **필수.** 반복 안정성을 재려면 남는 분산이 모델 자체의 것이어야 한다
- 모델명은 `settings.judge_model`에서 온다. **버전을 고정한 문자열**을 쓴다 —
  별칭(`latest` 계열)을 쓰면 어제 결과와 오늘 결과가 다른 이유를 알 수 없다
- `RunResult`에 이 셋이 실린다. **결과 JSON만 보고 재현 조건을 알 수 있어야 한다**

### 반복 안정성 — 숫자가 있다

> `docs/TRADEOFFS.md:100`은 「반복 안정성 테스트가 임계값을 못 넘으면 배점을 사실 층으로
> 옮긴다」고 썼는데 **N도 임계도 어디에도 없었다.** 임계가 없으면 그 철회 조건은 영원히
> 발동하지 않는다. 그래서 정한다.

| 항목 | 값 | 성격 |
|---|---|---|
| 반복 횟수 | **N = 11** | **인용된 실측.** `docs/RUBRIC_DESIGN.md:109` — 「95% 확률로 안정적인 판정을 얻으려면 **11회 이상** 반복이 필요했다」 |
| 표본 | 상위 2명 × 2항목 × 11회 = **44회** | `@pytest.mark.live`. 완주 예산(≈117회)에 안 들어간다 |
| 임계 | **표준편차 σ ≤ 0.5** (5점 척도) | **임의값.** 5점 척도에서 절반 칸 |
| 넘으면 | **오늘은 숫자만 남긴다.** 배점 재검토는 `docs/OPERATIONS.md`의 「다음 사람이 할 일」로 넘긴다 | 자동으로 바꾸지 않는다 |

**「논의를 시작한다」로 열어두지 않는다.** 배점 근거는 문헌 검토(판단 방식 r≈0.48 vs
세는 방식 r≈0.15)이고, 그걸 뒤집으려면 문헌을 다시 봐야 한다 — **24시간 안에 못 하는
일이다.** 열어두면 영원히 안 닫힌다. **오늘 하는 것은 σ를 재서 `summary`에 적는 것까지**
(**2026-09-01 14:30**, `docs/SCHEDULE.md` §5).

**N을 예산 때문에 깎지 마라.** 우리가 인용한 문단이 11을 말한다. 5로 줄이면
「11회 필요하다」는 근거 위에서 5회를 재는 것이 되어 **측정이 아무 말도 안 하게 된다.**
예산이 모자라면 이 테스트를 **통째로 버린다** (`docs/COST_BUDGET.md` §5).

**「논의를 시작한다」가 자동 전환이 아닌 이유**: 배점 근거는 문헌 검토(판단 방식 r≈0.48 vs
세는 방식 r≈0.15)이고, 우리 실측 한 번이 그걸 뒤집지 않는다. 다만 **σ가 크면 그 r≈0.48을
우리 구현에서 못 얻고 있다는 뜻**이므로 재검토 사유는 된다.

**실측치를 반드시 `summary`에 남긴다.** 「임계를 통과했다」가 아니라 **「σ가 얼마였다」**를 쓴다.

### 비용 관리

```python
class CallBudget:
    def spend(self, in_tokens: int, out_tokens: int) -> None: ...  # 상한 초과 시 예외
    def usd(self) -> float: ...
```

호출 수·토큰·환산 USD를 누적 기록한다(`data/.judge_usage.json`). 상한은
`settings.max_total_calls`(기본 **200**)에서 온다. 완주 1회 예상은 **≈117회**이고
계산 근거는 `docs/COST_BUDGET.md` §1.

**상한에 닿으면 조용히 줄이지 말고 예외를 던진다.** 그리고 `RunResult.cost`에
실측 호출수·토큰·USD가 실려 **결과 화면에 「이 결과를 만드는 데 n회 / $x」로 표시된다.**

## Acceptance Criteria

```bash
ruff check src/matching/judge
pytest tests/test_judge.py -q
```

기본 테스트는 **실물 API를 부르지 않는다.** 심사위원 응답을 고정 픽스처로 두고 검증한다:

- 두 심사위원 차이가 1이면 3번째를 안 부르는지
- 차이가 2면 3번째를 부르고 3명 평균이 나오는지
- 오프셋이 어긋난 인용이 버려지고 나머지는 살아남는지
- `quotes`가 빈 응답이 점수로 쓰이지 않는지
- `Evidence.quote`가 모델 응답이 아니라 원문 슬라이스인지

- **`JudgeCall`에 `temperature=0`과 고정 모델명이 실리는지**
- **`CallBudget`이 상한 초과 시 조용히 줄이지 않고 예외를 던지는지**

실물 호출이 필요한 「반복 안정성」(**N=11**, σ≤0.5)·「순서 불변성」은 `@pytest.mark.live`로
분리한다 (기본 실행에서 제외된다). **임계값은 `tests/CLAUDE.md`의 표가 정본이다.**

**σ는 이 step이 소유한다.** 실측값을 `index.json`의 **step 6** `summary`에
`"repeat_sigma": <값>` 형태로 남긴다. step 11의 홀드아웃 테스트가 **이 자리에서 읽는다** —
다른 곳에 적으면 그 테스트가 skip된다.

### σ를 스칼라 하나로 만드는 법

측정 단위는 **셀 4개**다 (상위 2명 × 판단항목 2개). 각 셀에서 N=11회 채점해 셀별 표준편차를
구한 뒤, **합동 표준편차**(pooled SD)로 합친다.

```
σ = sqrt( Σ sd_cell² / 4 )
```

평균이 아니라 합동인 이유: 표준편차의 산술평균은 분산 정보를 왜곡한다.
**셀별 값도 함께 `summary`에 남긴다** — 한 셀만 크면 그건 그 항목이 불안정한 것이지
전체가 불안정한 게 아니다.

> **이 σ를 6명 × 홀드아웃 2조건에 그대로 적용하는 것은 근사다.** 잰 곳(상위 2명·판단항목 2개)과
> 쓰는 곳(6명·홀드아웃 2조건)이 다르다. 전수로 재려면 12명 × 전 항목 × 11회가 되어
> 예산을 넘는다. **근사라는 사실을 `summary`에 적는다** — 홀드아웃 판정이 그 근사 위에 선다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트:
   - 심사위원이 **사실 확인**을 하고 있지 않은가? (연차·자격증 여부를 묻지 않는다)
   - 한 호출에 항목이 하나뿐인가?
   - 토론·합의 코드가 없는가?
   - API 키가 로그·예외에 안 찍히는가?
   - **모델명이 코드에 박히지 않고 `settings`에서 오는가?**
     (`grep -rniE "gpt-|claude-|o[0-9]-" src/matching/judge` 가 비어야 한다)
3. `index.json`의 step 6을 업데이트한다. `summary`에 **모델명 · 이 step에서 쓴 호출수**를 남긴다.

## 금지사항

- **심사위원을 3명 고정으로 부르지 마라.** 이유: 같은 제품군 LLM 패널은 9개를 써도
  독립 투표 2명분의 정보량밖에 안 나온다(상관오차). 인원을 늘려도 사는 게 적다.
- **모델이 준 인용 문자열을 그대로 `Evidence.quote`에 넣지 마라.** 이유는 위에 적었다.
- 심사위원에게 최종 100점 점수를 계산시키지 마라. 합산은 step 7의 코드가 한다.
- 점수를 먼저, 근거를 나중에 쓰게 하지 마라. 이유: 점수를 먼저 내면 근거가 사후 정당화가
  된다.
- 실패 시 무한 재시도하지 마라. 이유: $5 예산.
