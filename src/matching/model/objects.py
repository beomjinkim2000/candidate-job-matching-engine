"""파이프라인 전체가 주고받는 Object — **여기가 시스템의 계약이다.**

근거는 문장이 아니라 Link다. 점수 하나에서
`Score → Evidence → Criterion → Requirement → 공고 이미지 좌표`까지 끝까지 이어지고,
그 사슬을 코드가 대조할 수 있어야 한다. 이 파일은 그 사슬의 마디를 정의한다.

**검증자를 여기 달지 않는다.** 값이 성립하는지는 `governance.check()`가 본다.
모델이 막아버리면 「0으로 채운 가짜 좌표」 같은 실패 모드를 재현할 방법이 없어져
검산이 무엇을 잡는지 시험할 수 없다. 모델은 형태만, 검산은 내용을.
"""

from __future__ import annotations

from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict

ReviewStatus = Literal["draft", "human_validated"]
EvidenceGrade = Literal["E2", "E1", "E0"]
# `duty`는 **지원자에게 요구되는 조건이 아니다** — 입사 후 할 일이다. 그래서 게이트에도
# 사실 채점에도 들어가지 않고, 판단 채점에서 「이 경험이 이 일과 맞는가」의 자로만 쓰인다.
# 조건으로 세면 이미 그 일을 해본 사람만 점수를 받는데, 두 공고 다 신입·인턴 공고다.
RequirementKind = Literal["required", "preferred", "gate", "duty"]
ScoreLayer = Literal["gate", "fact", "judgment"]
# 조건이 **이력서에서 어떻게 확인되는가.** 층 배정과 기준점 선택이 둘 다 이 값에서 나온다
# (`rubric/branch.py`). 층으로 갈음할 수 없다 — `binary`와 `graded`는 같은 판단 층인데
# 기준점이 다르고, 그 차이가 「충족 여부만 묻는 항목에 성과 서술을 요구하지 않는다」이다.
RequirementBranch = Literal["term", "binary", "graded"]

# `rel`을 5개보다 늘리지 않는다 — 종류가 늘면 검산 규칙이 따라 늘고,
# 24시간 안에 관리가 안 된다.
Relation = Literal[
    "extracted_from",
    "derived_from",
    "supports",
    "grounded_in",
    "contradicts",
]

RELATIONS: tuple[Relation, ...] = get_args(Relation)
EVIDENCE_GRADES: tuple[EvidenceGrade, ...] = get_args(EvidenceGrade)
REQUIREMENT_BRANCHES: tuple[RequirementBranch, ...] = get_args(RequirementBranch)


class _Object(BaseModel):
    """모든 Object의 공통 설정.

    `extra="forbid"` — 필드명을 잘못 적으면 조용히 버려지는 대신 즉시 터진다.
    계약이 조용히 어긋나는 것보다 시끄럽게 깨지는 편이 낫다.
    """

    model_config = ConfigDict(extra="forbid")


class BBox(_Object):
    """공고 이미지 안의 위치. 좌표의 출처는 OCR 하나뿐이다 (`src/CLAUDE.md`).

    `img_w`·`img_h`를 함께 들고 다니는 이유: 좌표는 어떤 해상도에서 읽었는지를 모르면
    다시 그릴 수 없다. UI가 이미지를 축소해 보여줄 때 이 값으로 환산한다.
    """

    page: int
    x1: int
    y1: int
    x2: int
    y2: int
    img_w: int  # 이 좌표가 기준으로 삼은 이미지의 픽셀 폭
    img_h: int  # 픽셀 높이


class Span(_Object):
    """텍스트 안의 위치. 반열린 구간 `[start, end)` — 파이썬 슬라이스와 같다."""

    start: int
    end: int


class Requirement(_Object):
    """공고에서 뽑은 조건 1개.

    `source_bbox`가 필수인 것이 「공고 원문을 복붙하지 않았다」의 기계적 증명이다 —
    좌표가 없는 조건은 이미지에서 나온 것이 아니다 (검산 G4).
    """

    id: str  # "R-01"
    text: str  # 조건 문구 (파싱 결과. 원문 복붙 아님)
    kind: RequirementKind
    evidence_grade: EvidenceGrade
    ladder_step: int  # 1~5. 필수/우대 판정 사다리의 어느 단계에서 결론이 났나
    source_bbox: BBox  # 필수. 없으면 G4 위반
    source_span: Span | None  # OCR 텍스트가 있을 때만
    review_status: ReviewStatus = "draft"


class Criterion(_Object):
    """루브릭 항목 1개. 항목은 공고 조건에서 생성되고, 기준점의 패턴만 고정된다."""

    id: str  # "C-01"
    requirement_id: str
    label: str
    anchors: dict[int, str]  # {1: "...", 3: "...", 5: "..."}
    weight: float
    layer: ScoreLayer
    # 이 항목의 조건이 어떻게 확인되는가. **심사위원 프롬프트가 이 값으로 갈린다** —
    # `binary`는 충족 여부만 묻고, `graded`는 서술의 구체성을 본다. 기본값이 있는 것은
    # 이 필드가 생기기 전에 저장된 `result.json`도 그대로 읽히게 하기 위해서다.
    branch: RequirementBranch = "graded"
    review_status: ReviewStatus = "draft"


class Evidence(_Object):
    """이력서 원문의 한 구간.

    `quote`를 따로 들고 있는 것은 편의가 아니라 검산 장치다 — `span`으로 잘라낸 문자열과
    글자 하나까지 같아야 한다(G2). 어긋나면 인용을 지어낸 것이다.
    """

    id: str  # "E-01"
    resume_id: str
    span: Span  # 이력서 원문 문자 오프셋
    quote: str  # span으로 잘라낸 실제 문자열


class Score(_Object):
    """항목 1개의 점수.

    `rationale`은 채점자가 쓴 문장이다. **근거 자체가 아니다** — 근거는 Link이고,
    사람이 읽는 근거 문단은 `render.render_rationale()`이 그래프에서 만들어낸다.
    """

    id: str  # "S-01"
    criterion_id: str
    candidate_id: str
    value: float
    layer: ScoreLayer
    judge_id: str | None  # judgment 층만. fact 층은 None
    rationale: str  # 사람이 읽을 문장


class Link(_Object):
    """Object 사이의 관계. 근거의 저장 단위다."""

    src: str
    rel: Relation
    dst: str


class Resume(_Object):
    """지원자 이력서 한 통. **그래프 Object가 아니다.**

    `GraphObject` 합집합에 넣지 않았다 — 그래프에 담기는 것은 「우리가 만든 판단의
    마디」이고 이력서 원문은 **입력**이다. 담으면 `score()`가 끝나고 원본을 버릴 때
    (`docs/LEGAL_ARCHITECTURE.md` §3-③) 그래프에서 이력서를 도로 파내야 한다.

    `intended_type`(완벽/부분/미스)을 여기 두지 않은 것도 같은 이유다. 그건 목업
    데이터셋의 **라벨**이지 채점 입력이 아니고, 채점 경로가 볼 수 있는 자리에 두면
    유형 분리 테스트가 자기 자신을 검사하게 된다 (`tests/CLAUDE.md` 「순환」).
    """

    candidate_id: str
    text: str


GraphObject = Requirement | Criterion | Evidence | Score
