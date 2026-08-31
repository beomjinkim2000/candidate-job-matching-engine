# Step 1: core-model

## 읽어야 할 파일

- `src/CLAUDE.md` — **「근거 모델 — Object + Link」 절을 정확히 따른다**
- `docs/KAIREN_OS_ANALYSIS.md` — 왜 근거를 그래프로 두는지
- `src/matching/config.py` (step 0 산출물)

## 작업

`src/matching/model/` 에 파이프라인 전체가 쓰는 데이터 모델을 만든다. **여기가 시스템의
계약이다.** 이후 모든 step이 이 타입만 주고받는다.

### `src/matching/model/objects.py` — pydantic v2 모델

```python
ReviewStatus = Literal["draft", "human_validated"]
EvidenceGrade = Literal["E2", "E1", "E0"]
RequirementKind = Literal["required", "preferred", "gate"]
ScoreLayer = Literal["gate", "fact", "judgment"]

class BBox(BaseModel):        # 공고 이미지 안의 위치
    page: int
    x1: int; y1: int; x2: int; y2: int
    img_w: int                # 이 좌표가 기준으로 삼은 이미지의 픽셀 폭
    img_h: int                #                              픽셀 높이

class Span(BaseModel):        # 텍스트 안의 위치
    start: int
    end: int

class Requirement(BaseModel):
    id: str                       # "R-01"
    text: str                     # 조건 문구 (파싱 결과. 원문 복붙 아님)
    kind: RequirementKind
    evidence_grade: EvidenceGrade
    ladder_step: int              # 1~5. 어느 판정 단계에서 결론이 났나
    source_bbox: BBox             # 필수. 없으면 G4 위반
    source_span: Span | None      # OCR 텍스트가 있을 때만
    review_status: ReviewStatus = "draft"

class Criterion(BaseModel):
    id: str                       # "C-01"
    requirement_id: str
    label: str
    anchors: dict[int, str]       # {1: "...", 3: "...", 5: "..."}
    weight: float
    layer: ScoreLayer
    review_status: ReviewStatus = "draft"

class Evidence(BaseModel):
    id: str                       # "E-01"
    resume_id: str
    span: Span                    # 이력서 원문 문자 오프셋
    quote: str                    # span으로 잘라낸 실제 문자열

class Score(BaseModel):
    id: str                       # "S-01"
    criterion_id: str
    candidate_id: str
    value: float
    layer: ScoreLayer
    judge_id: str | None          # judgment 층만. fact 층은 None
    rationale: str                # 사람이 읽을 문장

class Link(BaseModel):
    src: str
    rel: Literal["extracted_from", "derived_from", "supports", "grounded_in", "contradicts"]
    dst: str
```

**`rel`을 5개보다 늘리지 마라.** 이유: 종류가 늘면 검산 규칙이 따라 늘고, 24시간 안에
관리가 안 된다 (`docs/KAIREN_OS_ANALYSIS.md` §5).

### `src/matching/model/graph.py` — 근거 그래프

```python
class EvidenceGraph(BaseModel):
    requirements: list[Requirement]
    criteria: list[Criterion]
    evidence: list[Evidence]
    scores: list[Score]
    links: list[Link]

    def add(self, obj) -> None: ...
    def link(self, src: str, rel: str, dst: str) -> None: ...
    def out(self, src: str, rel: str | None = None) -> list[Link]: ...
    def trace(self, score_id: str) -> list[Link]: ...
```

`trace()`는 `Score`에서 시작해 `grounded_in → supports → derived_from → extracted_from`
경로를 따라가 **공고 이미지 좌표까지 도달하는 링크 목록**을 반환한다. UI가 이걸 쓴다.

### `src/matching/model/governance.py` — 검산 G1~G5

```python
class Violation(BaseModel):
    rule: str        # "G1"
    object_id: str
    message: str

def check(graph: EvidenceGraph, resume_texts: dict[str, str]) -> list[Violation]: ...

class GovernanceError(Exception): ...

def enforce(graph: EvidenceGraph, resume_texts: dict[str, str]) -> None:
    """위반이 하나라도 있으면 GovernanceError를 던진다."""
```

규칙은 `src/CLAUDE.md`의 표 그대로다.

| | 규칙 |
|---|---|
| G1 | 모든 `Score`에 `grounded_in` Link가 1개 이상 |
| G2 | 모든 `Evidence`의 `quote`가 `resume_texts[resume_id][span.start:span.end]`와 **정확히 일치** |
| G3 | 모든 `Criterion`에 `derived_from` Requirement 존재 |
| G4 | 모든 `Requirement`에 `source_bbox` 존재 |
| G5 | 모든 `Requirement`에 `evidence_grade` 존재 |
| G6 | 모든 `Claim`이 `채택`/`배척`/`무관` 중 하나 — **그 지원자만** 랭킹 보류 (step12) |
| G7 | 승인이 **현재 공고 revision**에 대한 것 · 공고가 `active` — `ApprovalStale` (step7) |

**G2는 문자열 동일성 비교다.** 유사도·부분일치를 쓰지 마라. 이유: 이 검산의 목적이
"LLM이 인용을 지어냈는지" 잡는 것인데, 느슨하게 비교하면 지어낸 인용이 통과한다.

**G1의 예외 하나** — `layer == "gate"`인 `Score`는 탈락 판정이라 `grounded_in` 대신
`derived_from`으로 Requirement에 연결돼 있으면 통과시킨다.

### `src/matching/model/render.py` — 근거 문장 렌더링

```python
def render_rationale(graph: EvidenceGraph, score_id: str) -> str: ...
```

`trace()` 결과를 사람이 읽는 한 문단으로 바꾼다. **문장을 저장하지 않고 그래프에서
만들어낸다.** 이유: 저장된 문장은 그래프와 어긋날 수 있다.

## Acceptance Criteria

```bash
ruff check src/matching/model
pytest tests/test_model.py -q
```

`tests/test_model.py`를 이 step에서 함께 작성한다 (`tests/CLAUDE.md`: 결정적 코어는
테스트 우선). 최소 케이스:

- G1~G5 각각이 **위반을 실제로 잡는지** (일부러 깨뜨린 그래프 5개)
- G2가 quote를 한 글자 바꿨을 때 잡는지
- 정상 그래프가 위반 0개인지
- `trace()`가 Score에서 BBox까지 도달하는지

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트:
   - `src/CLAUDE.md`의 Object 표와 필드가 일치하는가?
   - `rel`이 5종인가?
   - `review_status` 기본값이 `"draft"`인가?
3. `phases/matching-engine/index.json`의 step 1을 업데이트한다. `summary`에 만든 타입
   이름과 파일 경로를 남긴다.

## 금지사항

- **`evidence_grade`를 점수 계산에 쓰지 마라.** 이유: 근거 수준과 적합도가 섞이면 점수가
  낮은 이유를 구분할 수 없다 (`docs/KAIREN_OS_ANALYSIS.md` §3-2).
- **직군·스킬 이름을 타입 안에 넣지 마라** (예: `SkillRequirement`, `PythonLevel`).
  이유: 과제 CRITICAL — 직군 무관 일반화.
- G2를 유사도 비교로 구현하지 마라. 이유는 위에 적었다.
- OpenAI를 호출하지 마라. 이유: 이 step은 순수 데이터 모델이다.
