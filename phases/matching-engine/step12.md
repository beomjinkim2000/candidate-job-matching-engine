# Step 12: evidence-extensions

> **3가지 모두 채택됐다** (2026-08-31 사용자 승인). 근거 문제의 해법이고,
> `docs/EVIDENCE_IDEAS.md`가 정본이다.

## 읽어야 할 파일

- `docs/EVIDENCE_IDEAS.md` — **이 step의 명세다. 근거·비용·틀리는 조건이 전부 여기 있다**
- `src/matching/pipeline/aggregate.py`, `rank.py` (step 7)
- `src/matching/judge/panel.py` (step 6)

## 작업

**순서가 정해져 있다.** 12-A → 12-B → 12-C. 예산이 모자라면 뒤에서부터 줄인다.

---

### 12-A. 등수 뒤집기 최소 편집 (비용 0, 먼저 한다)

`src/matching/pipeline/contrast.py`

```python
class RankFlip(BaseModel):
    target_rank: int              # 넘어서려는 순위
    minimal_set: list[str]        # criterion_id 목록
    gap: float                    # 현재 점수 격차
    structural: bool              # True면 편집 집합이 너무 커서 나열 안 함

def minimal_flip(results: list[CandidateResult], candidate_id: str) -> RankFlip: ...
def tie_bands(results: list[CandidateResult], epsilon: float) -> list[list[str]]: ...
```

- 항목을 하나씩 켜고 끄며 총점을 재계산해 **바로 위 순위를 넘기는 최소 부분집합**을 찾는다
- 항목 20개 이하면 완전 탐색, 넘으면 **배점 내림차순 그리디**
- **탐색 규칙을 코드 상수로 고정하고 UI 하단에 그 규칙을 적는다.** 이유: 최소 집합이
  여러 개일 때 어느 걸 골랐는지는 출력만 봐서 감사할 수 없다(반사실 설명의 체리피킹은
  이론적으로 탐지 불가능하다는 증명이 있다). **규칙 공개가 유일한 방어다**
- 편집 집합이 **3개를 넘으면** 나열하지 말고 `structural=True`로 표시한다
- **게이트 항목은 최소 편집에서 제외**한다. 이유: 게이트에서 탈락한 지원자에게
  "이것만 있으면 됩니다"는 거짓말이다. 별도 문단으로 적는다
- `tie_bands`는 점수 차가 `epsilon` 미만인 지원자를 같은 밴드로 묶는다.
  **정렬을 바꾸지 않고 배지로만** 표시한다

**LLM을 부르지 마라.** 이 기능은 전부 산술이다.

---

### 12-B. 판단유탈 대장

`src/matching/pipeline/ledger.py`

```python
ClaimStatus = Literal["adopted", "rejected", "irrelevant", "unaddressed"]

class Claim(BaseModel):
    id: str                       # "CL-01"
    span: Span                    # 이력서 원문 위치
    text: str
    status: ClaimStatus = "unaddressed"
    criterion_id: str | None      # adopted일 때
    reason_tag: str | None        # rejected/irrelevant일 때

class Ledger(BaseModel):
    claims: list[Claim]
    @property
    def unaddressed_rate(self) -> float: ...

def extract_claims(resume_text: str, client) -> list[Claim]: ...
def reconcile(ledger: Ledger, scores: list[Score], graph: EvidenceGraph) -> Ledger: ...
```

- 채점 **전에** 이력서에서 주장을 원자 단위로 추출한다 (LLM 1회, 이력서당)
- 채점 프롬프트에 `claim_id` 반환을 추가한다 → **추가 호출 0**
- 채점 후 `reconcile()`이 모든 claim에 상태를 매긴다
- `무기재`(언급 자체가 없음)와 `부재 확인`(언급은 있으나 수준 미달)을 **`reason_tag`로
  구분**한다. 이유: 무기재를 곧바로 불리로 읽으면 경력 단절·비전형 이력이 구조적으로
  손해 본다

#### 보류 규칙 — 임계가 있다

> **2026-09-01 02:40 KST 개정.** 이전 판은 「`unaddressed`가 **하나라도** 남으면
> `rank=None`」이었다. 그건 **과제 요구 ③(0~100점 + 랭킹)을 통째로 날릴 수 있는 규칙**이다 —
> 주장 추출은 LLM 1회 호출이고, 12명 전원에 대해 미처리가 **하나도** 안 남을 것을 전제한
> 설계였다. 하나라도 남으면 랭킹이 빈다. 임계도 상한도 없었다.

```python
class Ledger(BaseModel):
    claims: list[Claim]
    @property
    def unaddressed_rate(self) -> float: ...
    @property
    def incomplete(self) -> bool:        # settings.unaddressed_tolerance 초과
        ...
```

| 조건 | 결과 |
|---|---|
| `unaddressed_rate <= settings.unaddressed_tolerance` (기본 **0.15**) | **랭킹에 올린다.** 점수 그대로 |
| 초과 | `incomplete=True` + **랭킹에는 올리되** 「미처리 n건」 배지. `rank`는 준다 |
| 보류 대상이 전체의 `settings.ledger_degraded_ratio` (기본 **0.5**)를 넘음 | `RunResult.ledger_degraded=True`. **대장 자체를 못 믿는 상황**이므로 배지를 전원에서 내리고 그 사실을 결과 상단에 적는다 |

**`rank=None`을 없앴다.** 미처리 주장은 **우리 대장의 결함**이지 지원자의 결격이 아니다.
지원자를 랭킹에서 빼는 것으로 우리 도구의 불완전성을 표현하면, 그 대가를 지원자가 치른다.
배지로 표시하고 순위는 준다.

**임계 0.15와 0.5는 실측이 아니라 임의값이다.** `settings`에 있고 주석에 「임의」라고
적혀 있다. 정하는 근거가 없어서 정한 값이다.

**실측 기록 시각: 2026-09-01 13:30** — 이 step을 완료하며 `index.json` step 12의
`summary`에 **12명 전원의 `unaddressed_rate` 값**을 적는다 (평균이 아니라 전부).
`docs/SCHEDULE.md` §5에 같은 시각이 적혀 있다. **임계를 오늘 고치지는 않는다** —
한 번의 실측으로 임계를 옮기면 그 임계는 그 실행에 맞춘 것이 된다. 숫자만 남기고
판단은 다음 사람에게 넘긴다.

**AC가 이걸 검사한다** — 두 공고 모두 **랭킹에 오른 인원이 6명씩**이고
`unaddressed_rate` 실측치가 결과에 기록됐는지.

**주장 추출 시 "직무 관련성 후보"만 승격하는 필터를 둔다.** 취미·자기소개 서술이
대량이면 지표가 노이즈로 채워진다. 단, **그 필터 자체가 누락의 진짜 원인이 될 수 있다는
것을 `RunResult`에 필터 통과율로 남긴다.**

---

### 12-C. 근거 소거 재채점 (가장 비싸다. 표본으로만)

`src/matching/judge/ablation.py`

```python
class Faithfulness(BaseModel):
    contribution: float           # s_placebo - s_ablate
    sufficiency: float            # |s_only - s_full|
    decorative: bool              # contribution < tau
    hidden_evidence: bool         # sufficiency > eps

def measure(criterion, candidate, resume_text, cited_spans, client, settings) -> Faithfulness: ...
```

네 번 채점한다.

| 조건 | 입력 |
|---|---|
| `s_full` | 이력서 전체 |
| `s_ablate` | 이력서 − 인용 구간 |
| `s_placebo` | 이력서 − **같은 길이의 무작위 구간** (3회 평균) |
| `s_only` | 인용 구간만 |

**플라시보 대조군을 빼지 마라.** 문서에 구멍을 내는 행위 자체가 점수를 떨어뜨린다
(짧아진 이력서 = 빈약해 보임). 대조군 없이 `s_full - s_ablate`를 기여도로 쓰면 **전부
"근거가 있다"로 나온다.**

제약 두 가지:

1. **재채점 때는 0~100 연속 점수로 받는다.** 1/3/5 이산 척도면 작은 기여가 Δ=0으로
   묻혀 진짜 근거가 "장식"으로 오판된다
2. **표본으로만 돌린다** — 상위 3명 × 전 축. 전수(12명 × 5항목 × 5조건 ≈ 300회)는
   예산을 넘는다. `settings.ablation_sample_size`로 조절

`temperature=0` 필수. 남는 분산은 플라시보 3회로 추정해 `tau`에 반영한다.

---

### UI 반영 (step 9 수정)

**인용 유효성과 근거 충실도를 하나의 배지로 합치지 마라.**

| 배지 | 무엇을 보증하나 | 출처 |
|---|---|---|
| **인용 유효** | 그 문장이 이력서에 실재한다 | 검산 G2 |
| **근거 충실도 0.1/6** | 그 문장이 점수를 만들었다 | 12-C |

전자가 100%여도 후자는 0일 수 있다. 합치는 순간 거짓말이 된다.

## Acceptance Criteria

```bash
ruff check src/matching/pipeline src/matching/judge
pytest tests/test_contrast.py tests/test_ledger.py -q
python3 -c "
import json,glob,pathlib
runs=sorted(glob.glob('data/runs/*/result.json'))
assert len(runs)>=2, f'공고 2개의 결과가 있어야 한다 (현재 {len(runs)})'
for f in runs:
    d=json.loads(pathlib.Path(f).read_text())
    cands=d['candidates']
    assert len(cands)==6, (f, len(cands))
    ranked = [c for c in cands if c.get('rank') is not None]
    gated  = [c for c in cands if c.get('gate_failed')]
    # 랭킹에서 빠질 수 있는 사유는 **게이트 탈락 하나뿐**이다 (step7).
    # 대장(12-B)이 사람을 빼면 안 된다 — 그게 V4가 지적한 것이다.
    assert len(ranked) + len(gated) == 6, (f, len(ranked), len(gated))
    assert len(ranked) >= 5, f'랭킹 {len(ranked)}명 — 게이트 탈락 1명 외에 더 빠졌다'
    for c in cands:
        assert not (c.get('rank') is None and not c.get('gate_failed')), \\
            f\"{c['candidate_id']}: 게이트 탈락이 아닌데 랭킹에서 빠졌다\"
    print(f, 'ranked', len(ranked), 'gate_failed', len(gated),
          'unaddressed_rate', [round(c['ledger']['unaddressed_rate'],3) for c in cands])
print('ok')
"
```

**마지막 두 assert가 V4의 반증 조건이다.**

「랭킹이 무력화될 수 있다」는 지적은 **「랭킹에서 빠지는 사유가 게이트 탈락 하나뿐」**임이
실행 결과에서 확인돼야 철회된다. 대장(12-B)이 사람을 빼면 그 순간 요구 ③이 무너진다.

> **`==6`이 아니라 `+ gated == 6`인 이유**: `step10.md`가 미스매칭 1명을 **게이트 조건도
> 미충족**으로 설계한다. 게이트 탈락자는 `step7.md`의 0층 설계상 **정당하게** 랭킹에서
> 분리되어 사유와 함께 목록 끝에 붙는다. `==6`으로 쓰면 **설계대로 동작할 때 AC가 실패한다.**

최소 테스트:

- **12-A**: 점수 차 0인 두 지원자에서 `tie` 판정이 나오는지 / 게이트 탈락자의 최소 편집에
  게이트 항목이 안 들어가는지 / 편집 집합 4개면 `structural=True`인지
- **12-B**: 「이력서에만 있고 공고에 대응 요건이 없는 주장」 픽스처에서 `irrelevant`
  태그가 붙는지 / **`unaddressed_rate`가 임계 이하면 `rank`가 부여되는지** /
  **임계 초과면 `incomplete=True`이면서도 `rank`는 여전히 부여되는지** /
  **보류자가 절반을 넘으면 `ledger_degraded=True`가 서는지**
- **12-C**: 플라시보 조건이 실제로 호출되는지 (픽스처로) / 기여도 계산식이 맞는지.
  실물 호출은 `@pytest.mark.live`

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트:
   - 12-A가 LLM을 부르지 않는가?
   - 탐색 규칙이 UI에 노출되는가?
   - 12-C에 플라시보 대조군이 있는가?
   - 두 배지가 분리돼 있는가?
3. `index.json`의 step 12를 업데이트한다.

## 금지사항

- **승인 없이 실행하지 마라.**
- 12-C를 전수로 돌리지 마라. 이유: $5 예산.
- 플라시보 대조군을 생략하지 마라. 이유는 위에 적었다.
- 최소 편집 결과를 지원자에게 노출되는 화면에 넣지 마라. 이유: 이력서 gaming 유인이 된다.
  담당자 화면 전용이다.
- **절사평균(최고·최저 제외)을 넣지 마라.** 이유: 절사평균은 담합 억제 장치이지 정확도
  장치가 아니다. 우리 심사위원은 같은 모델의 복수 호출이라 오차가 독립이 아니어서,
  분산만 줄고 편향은 남는다 — **"여러 명이 합의했다"는 잘못된 안정감만 준다.**
