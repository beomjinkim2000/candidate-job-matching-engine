# Step 5: fact-scorer

## 읽어야 할 파일

- `src/CLAUDE.md` — 3층 아키텍처, 「절대 규칙」(포화함수, 마스킹)
- `docs/TRADEOFFS.md` — A-2 (게이트는 법정 자격만), A-4 (포화함수)
- `src/matching/model/` (step 1), `src/matching/rubric/` (step 4)

## 작업

**0층 게이트 + 1층 사실 채점.** 전부 코드다. LLM을 부르지 않는다.

### `src/matching/scorer/mask.py` — 민감 속성 마스킹

```python
def mask_sensitive(resume_text: str) -> tuple[str, dict[str, Span]]: ...
```

이름·성별·나이·생년월일·출신지·학교명·사진 경로를 마스킹한다. **채점 전에 돈다.**
반환하는 `dict`는 마스킹한 위치 기록이다 (UI가 "무엇을 가렸는지" 보여줄 수 있게).

마스킹은 **패턴 기반**이다 (이력서 구조의 필드명, 날짜 형식, `대학교`·`고등학교`로
끝나는 토큰). LLM을 쓰지 마라 — 이유: 마스킹 실패가 조용히 일어나면 안 된다.

**주의: 마스킹은 span 오프셋을 바꾼다.** 마스킹된 텍스트가 아니라 **원문 오프셋 기준**으로
`Evidence.span`을 기록해야 G2 검산을 통과한다. 마스킹 함수는 길이를 보존하는 치환
(같은 길이의 `■`)을 쓴다.

### `src/matching/scorer/gate.py` — 0층

```python
def run_gates(resume: Resume, criteria: list[Criterion], graph: EvidenceGraph) -> GateResult: ...

class GateResult(BaseModel):
    passed: bool
    failed_criteria: list[str]
    reasons: list[str]      # 사람이 읽는 탈락 사유
```

`layer == "gate"`인 항목만 본다. 탈락자는 **랭킹에서 분리**하되 사유와 함께 결과에 남긴다
(숨기지 않는다).

**게이트를 넓히지 마라.** 공고가 "필수"라고 쓴 조건도 게이트가 아니다. 게이트는
`settings.gate_kinds`(기본: 면허·법정 자격증)뿐이다. 이유: 문턱식으로 자르면 특정 집단의
탈락률이 유의미하게 오른다는 실측이 있고, 합산식이 대부분 조건에서 더 나은 선발 효용을
냈다 (`docs/TRADEOFFS.md` A-2).

### `src/matching/scorer/fact.py` — 1층

```python
def score_fact(
    resume: Resume,
    criteria: list[Criterion],
    graph: EvidenceGraph,
) -> list[Score]: ...
```

`layer == "fact"`인 항목만 채점한다. 항목 유형별 매처:

| 유형 | 매칭 | 점수 |
|---|---|---|
| 보유/미보유 (자격증, 도구) | 정규화 후 문자열 포함 + 약어 사전 | 1.0 / 0.0 |
| 수치 (연차) | 정규식으로 숫자 추출 → **포화함수** | 아래 |
| 열거 (복수 항목 중 N개) | 커버리지 비율 | 0.0~1.0 |

포화함수 — 선형 비례 금지:

```
score = min(1.0, have / required) 가 아니라
score = 1 - exp(-k * have / required)     # k = settings.experience_saturation_k
```

> [!example] 손으로 따라가기
> 요구 3년, k=2.0일 때.
> 3년 보유: `1 - exp(-2.0 * 3/3)` = `1 - exp(-2)` = **0.865**
> 10년 보유: `1 - exp(-2.0 * 10/3)` = `1 - exp(-6.67)` = **0.999**
> 1년 보유: `1 - exp(-2.0 * 1/3)` = `1 - exp(-0.667)` = **0.487**
>
> 10년이 3년의 3배가 아니라 **1.15배**로 평가된다. 이게 포화의 뜻이다.
> `k`는 임의값이다 — 정할 근거가 없다는 것을 `docs/TRADEOFFS.md` A-4에 적어뒀다.

**모든 `Score`에 `Evidence`와 `grounded_in` Link를 만든다.** 문자열이 매칭된 이력서 위치가
그대로 `Evidence.span`이 된다. 매칭이 실패해 0점을 준 경우에도 근거가 필요하다 —
"이력서 전체에서 이 조건에 해당하는 표현을 찾지 못함"이라는 `Evidence`를 span 없이 만들 수
없으므로, **0점은 `derived_from`으로 Criterion에 연결**하고 `rationale`에 미발견 사실을
적는다. G1의 예외를 이 경우까지 넓힌다 (step 1의 `governance.py`를 수정해야 하면 수정한다).

### `src/matching/scorer/normalize.py`

대소문자·공백·하이픈·한영 표기 흔들림을 정규화한다. **약어 사전은 데이터 파일로 둔다**
(`data/aliases.json`), 코드에 박지 않는다. 비어 있어도 동작해야 한다.

## Acceptance Criteria

```bash
ruff check src/matching/scorer
pytest tests/test_scorer.py -q
```

최소 테스트 케이스 (`tests/CLAUDE.md`의 「스코어러 경계」·「게이트 정확성」):

- 만점 / 부분 / 0점이 각각 의도대로 나오는지
- 포화함수가 위 「손으로 따라가기」 숫자와 일치하는지
- 면허 없는 지원자가 탈락 사유와 함께 분리되는지
- 공고가 "필수"라고 쓴 비법정 조건이 **탈락시키지 않는지**
- 마스킹 후에도 `Evidence.span`이 원문 오프셋을 가리켜 G2를 통과하는지

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트:
   - OpenAI 호출이 하나도 없는가? (`grep -rn "openai" src/matching/scorer` 가 비어야 한다)
   - 같은 입력에 항상 같은 출력인가? (결정적)
   - 모든 `Score`가 그래프에 연결됐는가?
3. `index.json`의 step 5를 업데이트한다.

## 금지사항

- **심사위원(LLM)에게 사실 확인을 시키지 마라.** 이유: 같은 질문 50회 반복 시 판정이
  13.6% 뒤집힌다. 연차가 5년인지 3년인지를 그 확률로 틀리게 만들 이유가 없다.
- **연차를 선형으로 주지 마라.** 이유는 포화함수 절에 적었다.
- 게이트를 `gate_kinds` 밖으로 넓히지 마라.
- 스킬명·직군명을 코드에 넣지 마라. 약어는 `data/aliases.json`에.
- 마스킹을 건너뛰지 마라. 이유: 블라인드 채용 금지 항목이 점수에 새면 안 된다.
