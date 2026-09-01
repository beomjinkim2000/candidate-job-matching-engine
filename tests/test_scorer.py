"""결정적 코어의 경계 시험 — **입력과 기대값이 전부 이 파일 안에 있다.**

`tests/CLAUDE.md`가 1차 지표로 올린 「스코어러 경계」와 2차의 「게이트 정확성」이 여기다.
1차인 이유: 목업 이력서의 라벨(완벽/부분/미스)을 **전혀 쓰지 않는다.** 아래 케이스는
전부 코드에 박힌 입력에 대해 코드에 박힌 값을 요구하므로, 우리가 정답도 만들고 채점도
하는 순환 밖에 있다.

고른 케이스와 이유:

| 케이스 | 왜 이것인가 |
|---|---|
| 만점 / 부분 / 0점 | 세 갈래가 **각각** 나오는지. 하나라도 안 나오면 이 층은 상수 함수다 |
| 포화함수 손계산 | `step5.md`가 손으로 따라간 세 숫자와 같은지. **식을 바꾸면 터진다** |
| 면허 미보유 분리 | 0층 설계 그 자체 |
| 「필수」 비법정 조건 | **게이트가 넓어지는 것을 막는 자물쇠.** 넓히면 여기서 터진다 |
| 마스킹 후 span | 마스킹이 오프셋을 밀면 근거가 **조용히** 딴 데를 가리킨다. G2로 잡는다 |
| 마스킹된 값의 점수 누출 | 가린 학교명이 점수에 새면 마스킹은 장식이다 |
| 결정성 | 같은 입력에 같은 출력. 이 층의 존재 이유 |
"""

from __future__ import annotations

import json
from math import exp

import pytest

from matching.config import Settings
from matching.model import (
    BBox,
    Criterion,
    EvidenceGraph,
    Requirement,
    Resume,
    Score,
    check,
)
from matching.rubric.build import build_rubric
from matching.scorer import (
    MASK_CHAR,
    key_terms,
    load_aliases,
    mask_sensitive,
    normalize,
    normalize_with_map,
    run_gates,
    saturation,
    score_fact,
)

# --- 붙박이 입력 -----------------------------------------------------------

RESUME_TEXT = (
    "■ 인적사항\n"
    "성명: 강유리 (여 / 1999.04.11 / 만 26세)\n"
    "연락처: 010-0000-0101 / yuri.kang@example.com\n"
    "거주지: 경기도 성남시\n"
    "병역: 해당 없음        해외여행 결격사유: 없음\n"
    "\n"
    "■ 학력사항\n"
    "2018.03 ~ 2026.02  한빛대학교 경영학과 학사 졸업\n"
    "\n"
    "■ 보유 기술\n"
    "Git · Unity · 1종 보통 운전면허\n"
    "\n"
    "■ 경력\n"
    "2024.07 ~ 2025.08 (14개월)  가맹 대리점 상담 파트타임\n"
    "2025.09 ~ 2026.02 (6개월)  영업기획팀 인턴\n"
)

BBOX = BBox(page=1, x1=10, y1=20, x2=300, y2=44, img_w=860, img_h=4920)


def make_requirement(req_id: str, text: str, kind: str = "preferred") -> Requirement:
    return Requirement(
        id=req_id,
        text=text,
        kind=kind,  # type: ignore[arg-type]
        evidence_grade="E2",
        ladder_step=1,
        source_bbox=BBOX,
        source_span=None,
    )


def make_criterion(
    criterion_id: str,
    requirement: Requirement,
    layer: str,
    weight: float = 10.0,
) -> Criterion:
    return Criterion(
        id=criterion_id,
        requirement_id=requirement.id,
        label=requirement.text[:40],
        anchors={1: "1점", 3: "3점", 5: "5점"},
        weight=weight,
        layer=layer,  # type: ignore[arg-type]
    )


def graph_with(*requirements: Requirement) -> EvidenceGraph:
    graph = EvidenceGraph()
    for requirement in requirements:
        graph.add(requirement)
    return graph


def resume() -> Resume:
    return Resume(candidate_id="A-01", text=RESUME_TEXT)


def score_of(scores: list[Score], criterion_id: str) -> Score:
    return next(score for score in scores if score.criterion_id == criterion_id)


# --- 정규화 ---------------------------------------------------------------


def test_normalize_folds_case_space_and_hyphen():
    """대소문자·공백·하이픈·가운뎃점만 지운다. OCR 공고는 띄어쓰기가 무너져 있다."""
    assert normalize("Git · SVN") == normalize("git·svn") == "gitsvn"
    assert normalize("데이터 분석") == normalize("데이터분석")
    assert normalize("E-Commerce") == "ecommerce"


def test_find_all_returns_original_offsets():
    """정규화한 자리를 **원문 자리로 되짚는다** — 여기가 깨지면 근거가 딴 데를 가리킨다."""
    document = normalize_with_map("보유 기술: Git · SVN")
    span = document.find_all("git·svn")[0]
    assert document.raw[span.start : span.end] == "Git · SVN"


def test_key_terms_prefers_latin_tokens():
    """라틴 토큰이 있으면 그것만 쓴다. 산문 낱말을 섞으면 신호가 묽어진다."""
    assert key_terms("-C++또는C#을 이용한 개발이 가능한 분") == ["C++", "C#"]


def test_key_terms_falls_back_to_hangul_and_numbers():
    terms = key_terms("·2026년12월 중 입사 가능한 분")
    assert "입사" in terms
    assert "2026년" in terms and "12월" in terms


def test_aliases_file_tolerates_absence(tmp_path):
    """`data/aliases.json`이 없거나 비어 있어도 매처가 돈다."""
    assert load_aliases(tmp_path / "없는파일.json") == {}
    empty = tmp_path / "aliases.json"
    empty.write_text("{}", encoding="utf-8")
    assert load_aliases(empty) == {}


def test_alias_lets_korean_spelling_match_latin_condition(tmp_path):
    """공고가 `Cloud`, 이력서가 `클라우드`여도 같은 것으로 본다."""
    path = tmp_path / "aliases.json"
    path.write_text(json.dumps({"Cloud": ["클라우드"]}, ensure_ascii=False), encoding="utf-8")
    aliases = load_aliases(path)

    requirement = make_requirement("R-01", "Cloud 경험이 있는 분")
    criterion = make_criterion("C-01", requirement, "fact")
    graph = graph_with(requirement)
    candidate = Resume(candidate_id="A-99", text="클라우드 환경에서 배포를 담당했습니다.")

    scores = score_fact(candidate, [criterion], graph, aliases=aliases)
    assert scores[0].value == 1.0


# --- 만점 / 부분 / 0점 -----------------------------------------------------


def test_full_partial_zero_are_all_reachable():
    """세 갈래가 각각 나오는지. 하나라도 안 나오면 이 층은 상수 함수다."""
    full = make_requirement("R-01", "Git·Unity 활용 경험이 있는 분")
    partial = make_requirement("R-02", "Git·Unreal 활용 경험이 있는 분")
    zero = make_requirement("R-03", "Kubernetes·Terraform 경험이 있는 분")
    criteria = [
        make_criterion("C-01", full, "fact"),
        make_criterion("C-02", partial, "fact"),
        make_criterion("C-03", zero, "fact"),
    ]
    graph = graph_with(full, partial, zero)

    scores = score_fact(resume(), criteria, graph)

    assert score_of(scores, "C-01").value == 1.0
    assert score_of(scores, "C-02").value == 0.5
    assert score_of(scores, "C-03").value == 0.0


def test_single_term_criterion_is_boolean():
    """표현이 하나면 보유/미보유다 — 0.5가 나올 자리가 없다."""
    have = make_requirement("R-01", "Unity 사용 경험")
    lack = make_requirement("R-02", "Kubernetes 사용 경험")
    graph = graph_with(have, lack)
    criteria = [make_criterion("C-01", have, "fact"), make_criterion("C-02", lack, "fact")]

    scores = score_fact(resume(), criteria, graph)
    assert {score.criterion_id: score.value for score in scores} == {"C-01": 1.0, "C-02": 0.0}


def test_zero_score_carries_a_reason():
    """0점에도 사람이 읽을 근거가 붙는다. 「근거 없는 0점」과 구별돼야 한다."""
    zero = make_requirement("R-01", "Kubernetes 경험이 있는 분")
    graph = graph_with(zero)
    scores = score_fact(resume(), [make_criterion("C-01", zero, "fact")], graph)
    assert "찾지 못했다" in scores[0].rationale


# --- 포화함수 -------------------------------------------------------------


@pytest.mark.parametrize(
    ("have", "expected"),
    [(3.0, 0.865), (10.0, 0.999), (1.0, 0.487)],
)
def test_saturation_matches_hand_calculation(have, expected):
    """`step5.md`가 손으로 따라간 세 숫자. **식을 바꾸면 여기서 터진다.**"""
    assert saturation(have, 3.0, 2.0) == pytest.approx(expected, abs=0.001)


def test_saturation_is_not_linear():
    """10년이 3년의 3배가 아니라 1.15배 — 그게 포화의 뜻이다 (docs/TRADEOFFS.md A-4)."""
    ratio = saturation(10.0, 3.0, 2.0) / saturation(3.0, 3.0, 2.0)
    assert 1.1 < ratio < 1.2


def test_experience_criterion_uses_saturation():
    """이력서의 `(14개월)+(6개월)` = 20개월을 읽어 요구 3년에 포화함수를 건다."""
    requirement = make_requirement("R-01", "관련 분야 3년 이상 경력이 있는 분", kind="required")
    graph = graph_with(requirement)
    settings = Settings(experience_saturation_k=2.0)

    criteria = [make_criterion("C-01", requirement, "fact")]
    scores = score_fact(resume(), criteria, graph, settings=settings)

    have = 20 / 12
    assert scores[0].value == pytest.approx(1 - exp(-2.0 * have / 3.0), abs=1e-9)


def test_calendar_years_are_not_read_as_duration():
    """`4년제`·`2027년2월`이 요구 연차로 둔갑하면 안 된다 — 비교어가 붙은 것만 연차다."""
    requirement = make_requirement("R-01", "정규4년제 대학을 졸업했거나2027년2월까지 졸업 가능한분")
    graph = graph_with(requirement)
    scores = score_fact(resume(), [make_criterion("C-01", requirement, "fact")], graph)
    assert "포화함수" not in scores[0].rationale


# --- 게이트 ---------------------------------------------------------------


def test_gate_separates_candidate_without_license():
    """면허 없는 지원자가 **사유와 함께** 분리된다."""
    requirement = make_requirement("R-01", "2종 소형 운전면허 소지자", kind="required")
    criterion = make_criterion("C-01", requirement, "gate", weight=0.0)
    graph = graph_with(requirement)
    candidate = Resume(candidate_id="A-99", text="■ 보유 기술\nPython · SQL\n")

    result = run_gates(candidate, [criterion], graph)

    assert result.passed is False
    assert result.failed_criteria == ["C-01"]
    assert len(result.reasons) == 1 and "찾지 못해" in result.reasons[0]


def test_gate_passes_when_license_is_present():
    requirement = make_requirement("R-01", "1종 보통 운전면허 소지자", kind="required")
    criterion = make_criterion("C-01", requirement, "gate", weight=0.0)
    graph = graph_with(requirement)

    result = run_gates(resume(), [criterion], graph)

    assert result.passed is True
    assert result.failed_criteria == []


def test_required_but_not_statutory_condition_does_not_reject():
    """공고가 「필수」라고 쓴 비법정 조건은 **탈락시키지 않는다** (docs/TRADEOFFS.md A-2).

    루브릭 생성부터 태워 본다 — 층 배정이 게이트를 넓히는 순간 여기서 터진다.
    """
    settings = Settings()
    must_have = make_requirement("R-01", "Kubernetes 운영 경험 필수", kind="required")
    graph = EvidenceGraph()
    criteria = build_rubric([must_have], settings, graph)

    assert criteria[0].layer != "gate"

    result = run_gates(resume(), criteria, graph)
    assert result.passed is True


def test_gate_result_is_empty_when_no_gate_criteria():
    """게이트 항목이 없는 공고는 아무도 떨어지지 않는다."""
    requirement = make_requirement("R-01", "Unity 사용 경험")
    graph = graph_with(requirement)
    result = run_gates(resume(), [make_criterion("C-01", requirement, "fact")], graph)
    assert result.passed is True and result.reasons == []


# --- 마스킹 ---------------------------------------------------------------


def test_mask_hides_blind_hiring_fields_and_keeps_length():
    masked, spans = mask_sensitive(RESUME_TEXT)

    assert len(masked) == len(RESUME_TEXT)
    for hidden in ("강유리", "한빛대학교", "1999.04.11", "만 26세", "yuri.kang"):
        assert hidden not in masked
    assert spans  # 무엇을 가렸는지 목록으로 돌려준다


def test_mask_keeps_fields_that_scoring_needs():
    """병역·경력 기간·전공은 남는다. 가리면 채점할 수 없다."""
    masked, _ = mask_sensitive(RESUME_TEXT)
    for kept in ("병역: 해당 없음", "2024.07 ~ 2025.08", "(14개월)", "경영학과"):
        assert kept in masked


def test_mask_is_idempotent():
    """이미 가린 글에 다시 걸어도 같다 — 채점기가 스스로 걸어도 안전해야 한다."""
    once, _ = mask_sensitive(RESUME_TEXT)
    twice, _ = mask_sensitive(once)
    assert once == twice


def test_masked_value_cannot_score():
    """가린 학교명이 점수에 새면 마스킹은 장식이다."""
    requirement = make_requirement("R-01", "한빛대학교 졸업자")
    graph = graph_with(requirement)
    candidate = Resume(candidate_id="A-98", text="■ 학력\n2018.03 ~ 2026.02 한빛대학교 경영학과\n")

    scores = score_fact(candidate, [make_criterion("C-01", requirement, "fact")], graph)
    assert scores[0].value == 0.0


def test_mask_char_never_leaks_into_a_quote():
    """근거 인용에 `■`가 섞이면 그 인용은 이력서가 아니라 마스킹을 보여주는 것이다."""
    requirement = make_requirement("R-01", "Git·Unity 활용 경험")
    graph = graph_with(requirement)
    score_fact(resume(), [make_criterion("C-01", requirement, "fact")], graph)
    assert all(MASK_CHAR not in evidence.quote for evidence in graph.evidence)


# --- 검산 -----------------------------------------------------------------


def test_evidence_span_points_at_original_text_after_masking():
    """마스킹은 오프셋을 밀지 않는다 — 원문·마스킹본 **둘 다**에서 G2를 통과한다."""
    requirement = make_requirement("R-01", "Git·Unity 활용 경험")
    graph = graph_with(requirement)
    candidate = resume()
    score_fact(candidate, [make_criterion("C-01", requirement, "fact")], graph)

    masked, _ = mask_sensitive(candidate.text)
    assert check(graph, {candidate.candidate_id: candidate.text}) == []
    assert check(graph, {candidate.candidate_id: masked}) == []


def test_zero_score_passes_g1_through_derived_from():
    """0점은 인용할 구간이 없다. `derived_from`으로 항목·조건에 이어 G1을 통과한다."""
    requirement = make_requirement("R-01", "Kubernetes 경험이 있는 분")
    graph = graph_with(requirement)
    candidate = resume()
    scores = score_fact(candidate, [make_criterion("C-01", requirement, "fact")], graph)

    assert scores[0].value == 0.0
    assert check(graph, {candidate.candidate_id: candidate.text}) == []


def test_g1_still_catches_a_nonzero_score_without_evidence():
    """예외는 **0점에만** 열려 있다. 넓히면 G1이 아무것도 막지 않는다."""
    requirement = make_requirement("R-01", "Unity 사용 경험")
    criterion = make_criterion("C-01", requirement, "fact")
    graph = graph_with(requirement)
    graph.add(criterion)
    graph.link(criterion.id, "derived_from", requirement.id)
    graph.add(
        Score(
            id="S-01",
            criterion_id="C-01",
            candidate_id="A-01",
            value=0.5,
            layer="fact",
            judge_id=None,
            rationale="근거 없이 준 점수",
        )
    )
    graph.link("S-01", "derived_from", "C-01")

    assert [violation.rule for violation in check(graph, {"A-01": RESUME_TEXT})] == ["G1"]


def test_gate_score_is_recorded_on_the_graph():
    """탈락 판정도 그래프에 남아 근거를 따라갈 수 있어야 한다."""
    requirement = make_requirement("R-01", "2종 소형 운전면허 소지자", kind="required")
    graph = graph_with(requirement)
    graph.add(make_criterion("C-01", requirement, "gate", weight=0.0))
    graph.link("C-01", "derived_from", "R-01")  # 평소엔 build_rubric이 건다
    candidate = Resume(candidate_id="A-99", text="■ 보유 기술\nPython · SQL\n")

    run_gates(candidate, graph.criteria, graph)

    gate_scores = [score for score in graph.scores if score.layer == "gate"]
    assert len(gate_scores) == 1 and gate_scores[0].value == 0.0
    assert check(graph, {candidate.candidate_id: candidate.text}) == []


# --- 결정성 ---------------------------------------------------------------


def test_scoring_is_deterministic():
    """같은 입력에 같은 출력. 이 층의 존재 이유다 (`src/CLAUDE.md` 모듈 경계)."""
    requirement = make_requirement("R-01", "Git·Unity·Unreal 활용 경험")
    criteria = [make_criterion("C-01", requirement, "fact")]

    first = score_fact(resume(), criteria, graph_with(requirement))
    second = score_fact(resume(), criteria, graph_with(requirement))

    assert [score.model_dump() for score in first] == [score.model_dump() for score in second]
