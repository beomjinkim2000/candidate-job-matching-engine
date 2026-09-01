# Step 7: aggregate-rank

## 읽어야 할 파일

- `src/CLAUDE.md` — 3층 아키텍처, 검산 G1~G5
- `src/matching/model/graph.py`, `governance.py` (step 1)
- `src/matching/scorer/` (step 5), `src/matching/judge/` (step 6)

## 작업

층별 점수를 **0~100점 하나**로 합치고, 랭킹을 만들고, **검산을 통과시킨다.**

### `src/matching/pipeline/aggregate.py`

```python
class CandidateResult(BaseModel):
    candidate_id: str
    total: float                  # 0~100
    rank: int | None              # 탈락자는 None
    gate: GateResult
    breakdown: list[AxisScore]    # 항목별 점수·가중치·근거 문장
    graph_ref: str

class AxisScore(BaseModel):
    criterion_id: str
    label: str
    layer: ScoreLayer
    raw: float                    # fact 0~1 / judgment 1~5
    weighted: float               # 가중 적용 후 점수
    max_weighted: float
    rationale: str                # render_rationale()의 결과
    evidence_ids: list[str]
    evidence_grade: EvidenceGrade # 조건의 근거 등급 (표시용)
    review_status: ReviewStatus   # 표시용

def aggregate(scores: list[Score], criteria: list[Criterion], graph, settings) -> CandidateResult: ...
```

정규화:

- `fact` 층: `raw`(0~1) × `weight`
- `judgment` 층: `(raw - 1) / 4`로 0~1에 사상한 뒤 × `weight`

**1점이 0점이 되는 것에 주의한다.** 5점 척도의 1점은 "관련 경험 없음"이므로 0점이 맞다.
이걸 `raw/5`로 하면 최하점에도 20%가 붙는다.

### `src/matching/pipeline/rank.py`

```python
def rank(results: list[CandidateResult]) -> list[CandidateResult]: ...
```

- 게이트 탈락자는 **랭킹에서 분리**하고 `rank=None`, 사유와 함께 목록 끝에 붙인다
- 동점은 `judgment` 층 점수가 높은 쪽을 위로 둔다. 그래도 같으면 `candidate_id` 순
  (임의지만 **재현 가능해야** 한다)

### `src/matching/pipeline/run.py` — 파이프라인을 **두 동강** 낸다

**한 함수로 만들지 마라.** 중간에 사람이 멈춰 서야 한다.

```python
def prepare(posting_ref: PostingRef, settings: Settings) -> RubricProposal: ...
def score(proposal: RubricProposal, resumes: list[Resume], settings: Settings) -> RunResult: ...

class RubricProposal(BaseModel):
    posting_id: str
    source_kind: Literal["saramin_api", "local", "client_feed"]
    requirements: list[Requirement]     # 전부 review_status="draft"
    criteria: list[Criterion]           # 전부 draft
    graph: EvidenceGraph
    posting_revision: str | None        # API의 modification-timestamp
    approved_at: datetime | None = None
    approved_by: str | None = None
```

**`posting_revision`이 승인의 유효기간이다.** 승인은 **그 시점의 공고**에 대한 것이므로
공고가 수정되면 낡는다. `score()`는 시작할 때 `PostingRegistry.current()`로 현재 값을
다시 조회해서 다르면 **`ApprovalStale`을 던진다.** 공고가 `active=0`이거나 마감일이
지났어도 마찬가지다. 이게 검산 **G7**이다.

> 규칙: **`human_validated`는 사람이 「현재 revision」을 확인한 뒤에만 쓴다.**
> 이걸 안 하면 **낡은 루브릭으로 계속 채점하면서 「사람 확인함」 배지를 달고 있게 된다.**

```
prepare()                              score()
  1. parse_posting()                     4. mask_sensitive()
  2. build_rubric()          ⛔ 승인      5. run_gates()
  3. 전부 draft로 반환         게이트      6. score_fact()
     이력서를 받지 않는다                  7. judge_criterion()  + ② 소거 재채점
                                         8. extract_claims / reconcile  ← ① 판단유탈 대장
                                         9. governance.enforce()  ← 여기서 막힌다
                                        10. aggregate() → rank() → ③ 등수 뒤집기
```

**`score()`는 `proposal.approved_at`이 없으면 `ApprovalRequired`를 던진다.**

건너뛰려면 `settings.skip_approval=True`를 **명시적으로** 줘야 하고, 그때는
`RunResult.unapproved = True`가 되어 **결과 JSON과 UI 상단에 「미승인」 표시**가 붙는다.
조용히 지나갈 수 없게 만드는 것이 요점이다.

> **왜 멈추는가**: `review_status`가 코드 어딘가의 선택 함수로만 있으면
> `human_validated`에 **도달할 경로가 없다.** 그러면 배지는 영원히 "AI 초안"이고,
> 우리가 남의 채용 기준을 대신 정한 것이 된다. 근거는 `docs/LEGAL_ARCHITECTURE.md` §4.

**9번이 핵심이다.** 검산에 걸리면 결과를 내보내지 않고 `GovernanceError`를 던진다.
위반 목록을 예외에 담아 어느 Object가 왜 막혔는지 알 수 있게 한다.
단 **G6(미처리 주장)은 그 지원자만 랭킹 보류**이고 전체를 막지 않는다.

**원본 파기** — `score()`가 끝나면 이력서 원문·공고 이미지·OCR 텍스트를 메모리에서 버린다.
`RunResult`에 남는 건 구조화된 조건·루브릭·점수·Link뿐이다
(`docs/LEGAL_ARCHITECTURE.md` §3-③). 근거 문장은 원문이 있을 때만 렌더링되므로,
**결과 JSON만으로는 이력서 내용이 복원되지 않는다.**

`RunResult`는 `data/runs/{run_id}/result.json`에 저장한다. 그래프도 함께 저장해서
UI가 추적할 수 있게 한다.

### `src/matching/pipeline/explain.py`

```python
def explain(result: RunResult, candidate_id: str) -> str: ...
```

한 지원자의 점수를 사람이 읽는 텍스트로 만든다. CLI가 이걸 출력한다. 구성:

```
[3위] 지원자 C — 68.4 / 100

  게이트  통과

  사실 채점 (35점 만점 중 24.1)
    필수 스킬 커버리지   14.0 / 20   5개 중 3개 확인
      └ 근거: 이력서 412~487  "결제 API 설계·운영"
    ...

  판단 채점 (65점 만점 중 44.3)
    경험의 직무관련성·깊이  30.0 / 40   심사위원 평균 4.0/5  [E1 · AI 초안]
      └ 근거: 이력서 210~268  "..."
      └ 판단: 역할과 성과가 명확히 서술됨. 다만 규모 수치가 없음
    ...
```

**`[E1 · AI 초안]` 표시를 빼지 마라.** 근거 등급과 `review_status`가 보이는 것이
이 설계의 요점이다.

## Acceptance Criteria

```bash
ruff check src/matching/pipeline
pytest tests/test_aggregate.py -q
```

최소 테스트 케이스:

- 항목 수가 달라도 만점이 100인지
- judgment 1점이 0점으로 사상되는지
- 게이트 탈락자가 랭킹에서 분리되고 사유가 남는지
- **G1을 일부러 깨뜨린 그래프에서 `run()`이 결과를 내지 않고 예외를 던지는지**
- 같은 입력에 같은 랭킹이 나오는지 (심사위원 응답을 픽스처로 고정한 상태에서)

### 요구 ③의 「사람이 읽는 근거」를 검사한다

과제 요구는 **0~100점**만이 아니라 **「사람이 읽을 수 있는 근거를 함께 낸다」**이다.
점수 쪽만 AC가 있고 근거 쪽이 없으면 그 요구는 절반만 검사된 것이다.

```python
def test_explain_is_human_readable():
    """explain() 출력이 사람이 읽는 문장인지 — 필드 덤프가 아닌지."""
    out = explain(fixture_run_result())
    assert "점" in out and "위" in out                  # 점수·순위가 한국어로
    assert not re.search(r'[A-Z]-\d{2}(?![^\n]*[가-힣])', out)  # 벌거벗은 ID만 있는 줄 금지
    for line in out.splitlines():
        if line.strip().startswith("근거"):
            assert len(line) > 20, f"근거가 너무 짧다: {line}"
    assert "이력서" in out or "「" in out                # 인용이 보인다
```

- **`Score.rationale`을 그대로 이어붙인 것이 근거가 아니다.** `render_rationale()`이
  그래프를 따라가 만든 문장이어야 한다 (`step1.md`)
- **`C-03` 같은 ID만 있는 줄을 만들지 마라.** 사람이 읽는다는 것은 ID를 안 봐도
  뜻이 통한다는 뜻이다
- 게이트 탈락자의 출력에는 **탈락 사유가 문장으로** 있어야 한다. 「gate_failed: true」는 문장이 아니다

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트:
   - `enforce()`가 `aggregate()` **앞**에 있는가?
   - 점수 범위가 0~100인가?
   - `AxisScore`에 `evidence_grade`와 `review_status`가 실려 있는가?
   - `rationale`이 저장된 문장이 아니라 `render_rationale()` 결과인가?
3. `index.json`의 step 7을 업데이트한다.

## 금지사항

- **검산을 경고로 낮추지 마라.** 위반이 있으면 결과를 내보내지 않는다. 이유: 검산이
  경고가 되는 순간 근거 없는 점수가 화면에 나간다.
- judgment 원점수를 `raw/5`로 정규화하지 마라. 이유는 위에 적었다.
- 동점 처리를 무작위로 하지 마라. 이유: 재현 불가능해진다.
- LLM에게 최종 점수나 랭킹을 계산시키지 마라. 이유: 이 층은 결정적이어야 한다.
