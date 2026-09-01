"""루브릭 조립의 계약 시험 — **「직군을 하드코딩하지 않았다」를 기계로 증명한다.**

케이스 선정 근거 (`tests/CLAUDE.md`): 커버리지가 아니라 **무엇을 골랐는가**가 평가 대상이다.
여기서 고른 것은 이 단계가 깨지면 시스템의 **주장이 무너지는** 지점들이다.

| 고른 케이스 | 깨지면 무엇이 거짓말이 되나 |
|---|---|
| 조건 3개 / 12개 총합 100 | 「0~100점」 — 항목 수에 따라 만점이 달라진다 |
| 담당업무 0건에서도 총합 100 | 위와 같음. 담당업무 섹션이 없는 공고에서 조용히 65점 만점이 된다 |
| 직군이 다른 두 공고 → 다른 항목 | **「직군 무관 일반화」** — 고정 루브릭과 구분이 안 된다 |
| 모든 항목에 `derived_from` | 「근거는 Link다」 — 검산 G3이 실제로 만족되는가 |
| `duty`가 게이트·사실 층에 0건 | 「담당업무를 자격으로 세지 않는다」 |
| 게이트 종류가 `gate` 층으로 | 0층 설계. 그리고 **우대 자격증은 게이트가 아니다** |
| 받침 있는/없는 조건의 기준점 | 조사를 코드가 고르지 않는다 — 안 고르면 깨질 일이 없다 |
| 승인 후 `weight`·`anchors` 불변 | 「승인이 점수를 바꾸지 않는다」 |

**직군 교차가 가장 값지다** — 일반화 주장을 반증 가능한 형태로 만든다.
공고 두 개는 **합성 픽스처**로 둔다. `data/`의 제출용 데이터셋에 묶으면 데이터를 못 고친다.
"""

from __future__ import annotations

from math import fsum

import pytest

from matching.config import Settings
from matching.model import BBox, EvidenceGraph, Requirement, Span, check
from matching.rubric import (
    ANCHOR_TEMPLATE,
    TOTAL_POINTS,
    apply_approval,
    build_rubric,
    make_anchors,
    pending,
)


def _req(req_id: str, text: str, kind: str = "required") -> Requirement:
    """조건 하나. 좌표는 G4를 통과하는 아무 값이면 된다 (여기서 보는 건 루브릭이다)."""
    return Requirement(
        id=req_id,
        text=text,
        kind=kind,  # type: ignore[arg-type]
        evidence_grade="E2",
        ladder_step=1,
        source_bbox=BBox(page=1, x1=10, y1=20, x2=900, y2=48, img_w=1131, img_h=4200),
        source_span=Span(start=0, end=len(text)),
    )


def _settings(**overrides) -> Settings:
    return Settings(**overrides)


# 직군이 서로 다른 두 공고. **조건 문구가 겹치지 않는다** — 겹치면 「다른 항목이 나온다」가
# 우연히 성립할 수 있다.
POSTING_A = [
    _req("R-01", "대규모 트래픽 처리 경험", "required"),
    _req("R-02", "장애 대응 절차를 문서로 남긴 경험", "preferred"),
    _req("R-03", "관계형 데이터베이스 운영 3년 이상", "required"),
]
DUTIES_A = [_req("D-01", "결제 시스템의 서버를 설계하고 운영합니다", "duty")]

POSTING_B = [
    _req("R-01", "브랜드 캠페인 기획 및 집행 경험", "required"),
    _req("R-02", "제휴사와의 협상을 주도한 경험", "preferred"),
]
DUTIES_B = [_req("D-01", "신규 채널을 발굴하고 판매 전략을 세웁니다", "duty")]


def _build(requirements, duties=None, settings=None):
    graph = EvidenceGraph()
    criteria = build_rubric(
        requirements, settings or _settings(), graph, duties=duties
    )
    return criteria, graph


# --- 총점 -----------------------------------------------------------------


@pytest.mark.parametrize("count", [3, 12])
def test_weights_sum_to_100_whatever_the_item_count(count: int) -> None:
    """**항목이 몇 개든 만점이 100이다.**

    이게 깨지면 「0~100점」이 항목 수에 따라 흔들린다 — 조건이 많은 공고의 지원자가
    구조적으로 낮은 점수를 받는다.
    """
    requirements = [
        _req(f"R-{i:02d}", f"조건 {i} 관련 경험", "required" if i % 2 else "preferred")
        for i in range(1, count + 1)
    ]
    criteria, _ = _build(requirements)

    assert len(criteria) == count
    assert fsum(c.weight for c in criteria) == pytest.approx(TOTAL_POINTS)


def test_empty_duties_still_sum_to_100() -> None:
    """담당업무 섹션이 없는 공고. **그때도 총합은 100이어야 한다.**"""
    criteria, _ = _build(POSTING_A, duties=[])
    assert fsum(c.weight for c in criteria) == pytest.approx(TOTAL_POINTS)


def test_missing_layer_does_not_lose_its_points() -> None:
    """사실 층 조건이 하나도 없어도 65점 만점이 되지 않는다.

    조건이 전부 서술형(숫자·라틴 문자 없음)인 공고가 실제로 있다. 그때 사실 층의 35점이
    갈 곳을 잃으면 **그 공고의 지원자만 만점이 65점**이 된다.
    """
    requirements = [
        _req("R-01", "이해관계를 조율하며 원활하게 소통하시는 분", "required"),
        _req("R-02", "새로운 기술 흐름을 유연하게 받아들이시는 분", "preferred"),
    ]
    criteria, _ = _build(requirements)

    assert {c.layer for c in criteria} == {"judgment"}
    assert fsum(c.weight for c in criteria) == pytest.approx(TOTAL_POINTS)


# --- 층 배정 ---------------------------------------------------------------


def test_gate_kind_goes_to_the_gate_layer() -> None:
    """`settings.gate_kinds`에 걸린 조건은 `gate` 층으로 가고 배점을 갖지 않는다."""
    requirements = [
        _req("R-01", "해당 업무 수행에 필요한 면허를 보유한 분", "required"),
        _req("R-02", "관련 분야 경험 3년 이상", "required"),
    ]
    criteria, _ = _build(requirements)
    by_id = {c.requirement_id: c for c in criteria}

    assert by_id["R-01"].layer == "gate"
    assert by_id["R-01"].weight == 0.0
    # 게이트가 배점을 가져가지 않으므로 남은 항목이 100점을 다 갖는다
    assert by_id["R-02"].weight == pytest.approx(TOTAL_POINTS)


def test_preferred_credential_is_not_a_gate() -> None:
    """**우대 자격은 게이트가 아니다.**

    게이트는 「없으면 그 일을 법적으로 못 하는 것」만이다 (`docs/TRADEOFFS.md` A-2).
    우대 조건까지 빨려 들어가면 「있으면 좋은 것」이 「없으면 탈락」이 된다.
    """
    criteria, _ = _build([_req("R-01", "관련 면허 보유자 우대", "preferred")])
    assert criteria[0].layer != "gate"


def test_countable_and_narrative_conditions_split_by_layer() -> None:
    """**분류 기준은 「문자열 대조로 확인되는가」이지 「어느 직군인가」가 아니다.**

    같은 직군의 조건 두 개가 서로 다른 층으로 갈린다 — 직군으로 갈랐다면 불가능하다.
    """
    requirements = [
        _req("R-01", "GA4 활용 경험", "preferred"),
        _req("R-02", "협업 과정에서 겪은 어려움을 풀어낸 경험", "preferred"),
    ]
    criteria, _ = _build(requirements)
    by_id = {c.requirement_id: c.layer for c in criteria}

    assert by_id["R-01"] == "fact"
    assert by_id["R-02"] == "judgment"


# --- 담당업무 --------------------------------------------------------------


def test_duty_never_reaches_the_gate_or_fact_layer() -> None:
    """담당업무를 **자격으로 세지 않는다**는 것의 기계적 확인.

    담당업무 문구에는 도구 이름과 숫자가 흔히 들어가서, `is_countable`만 보면 사실 층으로
    빨려 들어간다. 그러면 **그 일을 이미 해본 사람만 점수를 받는데 두 공고 다 신입 공고다.**
    """
    duties = [
        _req("D-01", "C++로 게임 콘텐츠를 구현합니다", "duty"),
        _req("D-02", "면허가 필요한 장비를 3년간 운영합니다", "duty"),
    ]
    criteria, _ = _build(POSTING_A, duties=duties)
    duty_layers = {c.layer for c in criteria if c.requirement_id.startswith("D-")}

    assert duty_layers == {"judgment"}


def test_duty_weight_never_exceeds_a_preferred_item() -> None:
    """담당업무는 **명시된 요구가 아니라 직무 설명**이다. 우대 조건보다 무거우면 안 된다."""
    requirements = [_req("R-01", "협업 경험을 구체적으로 서술한 분", "preferred")]
    duties = [_req("D-01", "여러 직군과 협업하며 기능을 구현합니다", "duty")]
    criteria, _ = _build(requirements, duties=duties)
    by_id = {c.requirement_id: c.weight for c in criteria}

    assert by_id["D-01"] <= by_id["R-01"]


def test_required_outweighs_preferred_in_the_same_layer() -> None:
    requirements = [
        _req("R-01", "고객 응대 경험이 있는 분", "required"),
        _req("R-02", "고객 응대 경험이 있으면 좋은 분", "preferred"),
    ]
    criteria, _ = _build(requirements)
    by_id = {c.requirement_id: c.weight for c in criteria}

    assert by_id["R-01"] > by_id["R-02"]


# --- 일반화 (가장 값진 케이스) ----------------------------------------------


def test_two_postings_of_different_jobs_produce_different_criteria() -> None:
    """**일반화의 최소 증명.** 항목 목록이 공고마다 달라야 한다.

    같은 코드에 다른 공고를 넣었는데 항목이 같다면, 항목은 공고가 아니라 코드에서 나온
    것이다 — 그게 정확히 과제가 금지한 하드코딩이다.
    """
    criteria_a, _ = _build(POSTING_A, duties=DUTIES_A)
    criteria_b, _ = _build(POSTING_B, duties=DUTIES_B)

    labels_a = {c.label for c in criteria_a}
    labels_b = {c.label for c in criteria_b}

    assert labels_a & labels_b == set()
    assert len(criteria_a) != len(criteria_b)
    # 기준점의 **패턴**은 같고, 앞에 붙은 조건 문구만 다르다 — 고정한 것이 패턴뿐이라는 뜻
    tails_a = {a.split("」 — ")[1] for c in criteria_a for a in c.anchors.values()}
    tails_b = {a.split("」 — ")[1] for c in criteria_b for a in c.anchors.values()}
    assert tails_a == tails_b == set(ANCHOR_TEMPLATE.values())


# --- 근거 사슬 (G3) --------------------------------------------------------


def test_every_criterion_is_linked_to_its_requirement() -> None:
    """검산 G3 — `derived_from` Link가 「이 항목이 공고에서 나왔다」의 유일한 증거다."""
    criteria, graph = _build(POSTING_A, duties=DUTIES_A)

    for criterion in criteria:
        links = graph.out(criterion.id, "derived_from")
        assert [link.dst for link in links] == [criterion.requirement_id]

    assert [v for v in check(graph, {}) if v.rule == "G3"] == []


# --- 기준점 문구 ------------------------------------------------------------


def test_anchors_never_choose_a_korean_particle() -> None:
    """받침이 있든 없든 기준점 문장이 깨지지 않는다.

    **조사를 안 쓰는 형태인지**를 본다 — 조건 문구를 떼어내면 남는 문장이 완전히 같아야
    한다. 같다면 코드가 을/를·이/가를 고른 적이 없다는 뜻이다.
    """
    with_batchim = make_anchors(_req("R-01", "대규모 트래픽 처리"))
    without_batchim = make_anchors(_req("R-02", "GA4"))

    for level, pattern in ANCHOR_TEMPLATE.items():
        assert with_batchim[level] == f"「대규모 트래픽 처리」 — {pattern}"
        assert without_batchim[level] == f"「GA4」 — {pattern}"
        assert with_batchim[level].split("」 — ")[1] == without_batchim[level].split("」 — ")[1]


def test_anchor_levels_are_one_three_five() -> None:
    """2·4점은 일부러 비워 둔다 (`docs/TRADEOFFS.md` B-3)."""
    criteria, _ = _build(POSTING_A)
    assert all(sorted(c.anchors) == [1, 3, 5] for c in criteria)


# --- 승인 -----------------------------------------------------------------


def test_approval_changes_status_only() -> None:
    """**승인이 점수를 바꾸지 않는다.** 바꾸면 승인 전후가 다른 시스템이 된다."""
    criteria, _ = _build(POSTING_A, duties=DUTIES_A)
    approvals = {criteria[0].id: True, criteria[1].id: False}

    approved = apply_approval(criteria, approvals)

    assert approved[0].review_status == "human_validated"
    assert approved[1].review_status == "draft"
    for before, after in zip(criteria, approved, strict=True):
        assert after.weight == before.weight
        assert after.anchors == before.anchors
        assert after.layer == before.layer
    assert fsum(c.weight for c in approved) == pytest.approx(TOTAL_POINTS)


def test_pending_lists_only_drafts() -> None:
    criteria, _ = _build(POSTING_A)
    approved = apply_approval(criteria, {criteria[0].id: True})

    assert [c.id for c in pending(criteria)] == [c.id for c in criteria]
    assert [c.id for c in pending(approved)] == [c.id for c in criteria[1:]]
