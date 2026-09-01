"""근거 모델의 계약 시험 — 검산이 **실제로 위반을 잡는가**만 본다.

케이스 선정 근거 (`tests/CLAUDE.md`): 결정적 코어는 테스트 우선으로 쓴다. 여기서 고른
것은 커버리지가 아니라 **검산 G1~G5 각각을 일부러 깨뜨린 그래프 5개**다. 검산이 못 잡는
위반이 하나라도 있으면 「근거를 코드가 대조한다」는 이 프로젝트의 주장이 무너진다.

G2가 두 번 나오는 이유: 「없는 이력서」는 사고고 **「한 글자 다른 인용」이 진짜 위험**이다.
그럴듯한데 원문에 없는 문장이 정확히 LLM이 만들어내는 실패 모드다.
"""

from __future__ import annotations

import pytest

from matching.model import (
    RELATIONS,
    BBox,
    Criterion,
    Evidence,
    EvidenceGraph,
    GovernanceError,
    Link,
    Requirement,
    Score,
    Span,
    check,
    enforce,
    render_rationale,
)

RESUME_ID = "CAND-01"
RESUME_TEXT = (
    "결제 API 서버를 설계하고 일 300만 건 트래픽을 처리했다. "
    "장애 대응 절차를 문서로 남겨 팀에 공유했다."
)
QUOTE = "일 300만 건 트래픽을 처리했다"
QUOTE_START = RESUME_TEXT.index(QUOTE)
QUOTE_END = QUOTE_START + len(QUOTE)

RESUME_TEXTS = {RESUME_ID: RESUME_TEXT}


def _bbox(**overrides: int) -> BBox:
    values = {"page": 1, "x1": 120, "y1": 340, "x2": 880, "y2": 392, "img_w": 1131, "img_h": 4200}
    values.update(overrides)
    return BBox(**values)


def _requirement(**overrides) -> Requirement:
    values = {
        "id": "R-01",
        "text": "대규모 트래픽 처리 경험",
        "kind": "required",
        "evidence_grade": "E2",
        "ladder_step": 1,
        "source_bbox": _bbox(),
        "source_span": Span(start=0, end=13),
    }
    values.update(overrides)
    return Requirement(**values)


def clean_graph() -> EvidenceGraph:
    """검산을 전부 통과하는 그래프. 위반 테스트는 여기서 한 군데씩만 깨뜨린다."""
    graph = EvidenceGraph()
    graph.add(_requirement())
    graph.add(
        Criterion(
            id="C-01",
            requirement_id="R-01",
            label="대규모 트래픽 처리 경험",
            anchors={1: "관련 경험 없음", 3: "제시하나 역할이 불명확", 5: "역할·행동·성과가 명확"},
            weight=1.0,
            layer="judgment",
        )
    )
    graph.add(
        Evidence(
            id="E-01",
            resume_id=RESUME_ID,
            span=Span(start=QUOTE_START, end=QUOTE_END),
            quote=QUOTE,
        )
    )
    graph.add(
        Score(
            id="S-01",
            criterion_id="C-01",
            candidate_id=RESUME_ID,
            value=5,
            layer="judgment",
            judge_id="judge-a",
            rationale="처리 규모를 숫자로 제시하고 본인이 설계했다고 밝혔다.",
        )
    )
    graph.link("C-01", "derived_from", "R-01")
    graph.link("E-01", "supports", "C-01")
    graph.link("S-01", "grounded_in", "E-01")
    graph.link("R-01", "extracted_from", "POSTING_IMG#1")
    return graph


def rules(graph: EvidenceGraph, texts: dict[str, str] | None = None) -> list[str]:
    return [v.rule for v in check(graph, RESUME_TEXTS if texts is None else texts)]


# --- 정상 -----------------------------------------------------------------


def test_clean_graph_has_no_violations():
    assert check(clean_graph(), RESUME_TEXTS) == []


# --- G1 -------------------------------------------------------------------


def test_g1_catches_score_without_grounding():
    graph = clean_graph()
    graph.links = [link for link in graph.links if link.rel != "grounded_in"]
    assert rules(graph) == ["G1"]


def test_g1_allows_gate_score_linked_by_derived_from():
    """게이트 Score는 이력서 인용이 아니라 조건 자체에서 나온다 — G1의 예외 ①.

    예외 ②(사실 층 0점)는 step 5에서 열렸고 `tests/test_scorer.py`가 본다.
    """
    graph = clean_graph()
    graph.add(
        Score(
            id="S-02",
            criterion_id="C-01",
            candidate_id=RESUME_ID,
            value=0,
            layer="gate",
            judge_id=None,
            rationale="공고가 요구한 면허가 이력서에 없다.",
        )
    )
    graph.link("S-02", "derived_from", "R-01")
    assert check(graph, RESUME_TEXTS) == []


def test_g1_catches_gate_score_without_any_link():
    graph = clean_graph()
    graph.add(
        Score(
            id="S-02",
            criterion_id="C-01",
            candidate_id=RESUME_ID,
            value=0,
            layer="gate",
            judge_id=None,
            rationale="탈락.",
        )
    )
    assert rules(graph) == ["G1"]


# --- G2 -------------------------------------------------------------------


def test_g2_catches_one_character_changed_in_quote():
    """한 글자만 바꾼다. 유사도 비교였다면 통과했을 것이고, 그게 이 검산의 존재 이유다."""
    graph = clean_graph()
    graph.evidence[0].quote = QUOTE[:-1] + "요"
    assert rules(graph) == ["G2"]


def test_g2_catches_span_pointing_elsewhere():
    graph = clean_graph()
    graph.evidence[0].span = Span(start=0, end=len(QUOTE))
    assert rules(graph) == ["G2"]


def test_g2_catches_empty_quote():
    """빈 인용은 어떤 span과도 맞아떨어진다 — 통과시키면 검산이 무력해진다."""
    graph = clean_graph()
    graph.evidence[0].quote = ""
    assert rules(graph) == ["G2"]


def test_g2_catches_unknown_resume():
    assert rules(clean_graph(), texts={}) == ["G2"]


# --- G3 -------------------------------------------------------------------


def test_g3_catches_criterion_without_requirement():
    graph = clean_graph()
    graph.links = [link for link in graph.links if link.rel != "derived_from"]
    assert rules(graph) == ["G3"]


def test_g3_catches_link_disagreeing_with_field():
    """필드와 Link가 다른 조건을 가리키면 근거 문단이 실제와 다른 조건을 보여준다."""
    graph = clean_graph()
    graph.add(_requirement(id="R-02", text="장애 대응 경험"))
    graph.links = [link for link in graph.links if link.rel != "derived_from"]
    graph.link("C-01", "derived_from", "R-02")
    assert rules(graph) == ["G3"]


# --- G4 -------------------------------------------------------------------


def test_g4_catches_requirement_without_bbox():
    """좌표 없는 조건 = 이미지에서 나오지 않은 조건. 과제 CRITICAL의 기계적 증명이다."""
    graph = clean_graph()
    graph.requirements[0] = Requirement.model_construct(
        id="R-01",
        text="대규모 트래픽 처리 경험",
        kind="required",
        evidence_grade="E2",
        ladder_step=1,
        source_span=None,
        review_status="draft",
    )
    assert rules(graph) == ["G4"]


def test_g4_catches_zero_area_bbox():
    """필드만 0으로 채운 가짜 좌표. 「있다/없다」만 봤다면 통과했을 것이다."""
    graph = clean_graph()
    graph.requirements[0].source_bbox = _bbox(x2=120, y2=340)
    assert rules(graph) == ["G4"]


# --- G5 -------------------------------------------------------------------


def test_g5_catches_requirement_without_evidence_grade():
    graph = clean_graph()
    graph.requirements[0] = Requirement.model_construct(
        id="R-01",
        text="대규모 트래픽 처리 경험",
        kind="required",
        ladder_step=1,
        source_bbox=_bbox(),
        source_span=None,
        review_status="draft",
    )
    assert rules(graph) == ["G5"]


# --- enforce --------------------------------------------------------------


def test_enforce_raises_and_carries_violations():
    graph = clean_graph()
    graph.evidence[0].quote = "쿠팡에서 결제 시스템을 총괄했다"  # 원문에 없는 문장
    with pytest.raises(GovernanceError) as excinfo:
        enforce(graph, RESUME_TEXTS)
    assert [v.rule for v in excinfo.value.violations] == ["G2"]


def test_enforce_passes_on_clean_graph():
    enforce(clean_graph(), RESUME_TEXTS)


# --- trace ----------------------------------------------------------------


def test_trace_reaches_posting_bbox_from_score():
    """점수 하나에서 공고 이미지 좌표까지 한 번에 간다. UI가 이 경로를 쓴다."""
    graph = clean_graph()
    trail = graph.trace("S-01")
    assert [(link.src, link.rel, link.dst) for link in trail] == [
        ("S-01", "grounded_in", "E-01"),
        ("E-01", "supports", "C-01"),
        ("C-01", "derived_from", "R-01"),
        ("R-01", "extracted_from", "POSTING_IMG#1"),
    ]
    requirement = graph.get(trail[2].dst)
    assert isinstance(requirement, Requirement)
    assert requirement.source_bbox.y1 == 340


def test_trace_skips_to_derived_from_for_gate_score():
    graph = clean_graph()
    graph.add(
        Score(
            id="S-02",
            criterion_id="C-01",
            candidate_id=RESUME_ID,
            value=0,
            layer="gate",
            judge_id=None,
            rationale="면허 미보유.",
        )
    )
    graph.link("S-02", "derived_from", "R-01")
    assert [link.rel for link in graph.trace("S-02")] == ["derived_from", "extracted_from"]


# --- 계약 --------------------------------------------------------------


def test_relations_stay_at_five():
    """늘리면 검산 규칙이 따라 늘어난다 (`docs/KAIREN_OS_ANALYSIS.md` §5)."""
    assert set(RELATIONS) == {
        "extracted_from",
        "derived_from",
        "supports",
        "grounded_in",
        "contradicts",
    }
    with pytest.raises(ValueError):
        Link(src="A", rel="explains", dst="B")


def test_ai_output_starts_as_draft():
    """AI 초안과 사람 확정을 섞지 않는다 — 승인 전에는 전부 draft다."""
    assert _requirement().review_status == "draft"
    assert clean_graph().criteria[0].review_status == "draft"


def test_add_rejects_duplicate_id():
    graph = clean_graph()
    with pytest.raises(ValueError):
        graph.add(_requirement())


# --- render ---------------------------------------------------------------


def test_render_rationale_is_built_from_the_graph():
    text = render_rationale(clean_graph(), "S-01")
    assert QUOTE in text  # 이력서 인용
    assert "대규모 트래픽 처리 경험" in text  # 공고 조건
    assert "E2" in text  # 근거 등급
    assert "(120,340)-(880,392)" in text  # 공고 이미지 좌표
    assert "AI 초안" in text  # review_status
    assert "역할·행동·성과가 명확" in text  # 5점 기준점


def test_render_rationale_follows_the_graph_not_the_stored_sentence():
    """조건 문구를 고치면 문단이 따라 바뀐다 — 저장된 문장이었다면 안 바뀐다."""
    graph = clean_graph()
    graph.requirements[0].text = "일 100만 건 이상 트래픽 처리 경험"
    assert "일 100만 건 이상 트래픽 처리 경험" in render_rationale(graph, "S-01")


def test_render_rationale_rejects_unknown_score():
    with pytest.raises(ValueError):
        render_rationale(clean_graph(), "S-99")
