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
| 갈래가 층과 기준점을 정한다 | 세 갈래 설계 전부. 깨지면 「예/아니오」가 다시 낱말 대조로 간다 |
| LLM을 못 부르면 옛 규칙으로 떨어진다 | `GET /trace`가 `client=None`으로 돈다 — 죽으면 안 된다 |
| 프롬프트가 바뀌면 캐시가 무효 | 지시문을 고쳤는데 옛 판정이 재사용되는 사고가 **실제로 났다** |
| 실측 공고 2건의 갈래 | 고친 것이 진짜 그 조건들을 고쳤는가 — **눈으로 확인한 기대값** |

**직군 교차가 가장 값지다** — 일반화 주장을 반증 가능한 형태로 만든다.
공고 두 개는 **합성 픽스처**로 둔다. `data/`의 제출용 데이터셋에 묶으면 데이터를 못 고친다.

> 예외가 하나 있다 — 맨 아래 「실측 공고」 절은 `data/postings/*/requirements.json`을
> **읽는다.** 고친 것이 실제로 그 공고의 그 조건들을 고쳤는지는 합성 픽스처로 말할 수
> 없기 때문이다. 읽는 것은 조건 **id와 문구**뿐이고 기대값은 이 파일에 손으로 적혀 있다.
> 파일이 없으면 skip한다.
"""

from __future__ import annotations

import json
from math import fsum
from pathlib import Path
from types import SimpleNamespace

import pytest

from matching.config import Settings
from matching.model import BBox, EvidenceGraph, Requirement, Span, check
from matching.rubric import (
    ANCHOR_TEMPLATE,
    SATISFACTION_TEMPLATE,
    TOTAL_POINTS,
    apply_approval,
    branch_of,
    build_rubric,
    fallback_branch,
    is_countable,
    make_anchors,
    pending,
    resolve_branches,
)
from matching.rubric import branch as branch_module


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


def _build(requirements, duties=None, settings=None, branches=None):
    graph = EvidenceGraph()
    criteria = build_rubric(
        requirements, settings or _settings(), graph, duties=duties, branches=branches
    )
    return criteria, graph


# --- 갈래 분류기 대역 -------------------------------------------------------


class _FakeBranchCompletions:
    """준비한 응답을 순서대로 준다. **더 부르면 터진다** — 호출 횟수가 계약이다."""

    def __init__(self, replies):
        self._replies = list(replies)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._replies:
            raise AssertionError(f"준비한 응답보다 많이 불렀다 ({len(self.calls)}회)")
        reply = self._replies.pop(0)
        body = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=body))],
            usage=SimpleNamespace(prompt_tokens=300, completion_tokens=60),
        )


def _branch_client(*replies):
    completions = _FakeBranchCompletions(replies)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions)), completions


def _labels(*branches, offset: int = 0):
    """번호 → 갈래. 실제 응답과 같은 모양이다."""
    return {
        "labels": [
            {"index": index, "branch": value}
            for index, value in enumerate(branches, start=1 + offset)
        ]
    }


class _NeverCalled:
    """부르면 터지는 대역. **「안 불렀다」를 세는 방식이 요점이다.**"""

    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        raise AssertionError("캐시가 맞는데 LLM을 불렀다")


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


# --- 세 갈래 --------------------------------------------------------------
#
# 두 갈래(사실/판단)로는 **「예/아니오인데 이력서가 다른 말로 적는」 조건**을 놓는 자리가
# 없었다. 사실 층으로 보내면 낱말이 안 맞아 0점에 가깝고, 판단 층으로 보내면 기준점이
# 행동·성과 서술을 요구해 만점이 구조적으로 불가능했다. 아래가 그 자리를 시험한다.

BRANCH_POSTING = [
    _req("R-01", "GA4 활용 경험", "preferred"),  # 이름이 있다 → term
    _req("R-02", "해당 절차를 마쳤거나 면제된 분", "required"),  # 예/아니오 → binary
    _req("R-03", "협업 과정의 어려움을 풀어낸 경험", "preferred"),  # 정도가 있다 → graded
]
BRANCHES = {"R-01": "term", "R-02": "binary", "R-03": "graded"}


def test_세_갈래가_층과_기준점을_동시에_정한다() -> None:
    """**이 파일에서 가장 중요한 케이스다.**

    깨지는 방향이 둘이고 둘 다 조용하다. 층만 옮기고 기준점을 안 바꾸면 충족형 조건이
    「행동·성과를 서술했는가」로 채점되어 만점이 안 나오고, 기준점만 바꾸고 층을 안
    옮기면 그 조건이 여전히 낱말 대조로 채점된다.
    """
    criteria, _ = _build(BRANCH_POSTING, branches=BRANCHES)
    by_req = {c.requirement_id: c for c in criteria}

    assert by_req["R-01"].layer == "fact"
    assert by_req["R-02"].layer == "judgment"
    assert by_req["R-03"].layer == "judgment"
    assert {req_id: item.branch for req_id, item in by_req.items()} == BRANCHES

    # 충족형만 다른 기준점을 받는다. 나머지는 지금까지의 문구 그대로다.
    tails = {
        req_id: {text.split("」 — ")[1] for text in item.anchors.values()}
        for req_id, item in by_req.items()
    }
    assert tails["R-02"] == set(SATISFACTION_TEMPLATE.values())
    assert tails["R-01"] == tails["R-03"] == set(ANCHOR_TEMPLATE.values())
    # 충족형 기준점은 **행동·성과 서술을 요구하지 않는다.** 그게 이 갈래의 존재 이유다.
    assert not any("성과" in text for text in SATISFACTION_TEMPLATE.values())


def test_담당업무는_갈래를_물어도_언제나_서술형이다() -> None:
    """담당업무는 「할 수 있는가」의 **정도**를 재는 자다. 충족/미충족을 묻는 항목이 아니다."""
    duties = [_req("D-01", "여러 직군과 협업하며 기능을 구현합니다", "duty")]
    criteria, _ = _build(
        BRANCH_POSTING, duties=duties, branches={**BRANCHES, "D-01": "binary"}
    )
    duty = next(c for c in criteria if c.requirement_id == "D-01")

    assert duty.branch == "graded"
    assert duty.layer == "judgment"
    assert {t.split("」 — ")[1] for t in duty.anchors.values()} == set(ANCHOR_TEMPLATE.values())


def test_갈래를_안_주면_옛_글자_모양_규칙_그대로다() -> None:
    """**기존 호출부가 갑자기 다른 층 배정을 받지 않는다.**

    `branches`를 안 주는 자리가 여럿이다(테스트·재조립·`--no-judge`). 그때 조용히 다른
    층으로 가면 승인 전후에 루브릭이 달라진다.
    """
    criteria, _ = _build(BRANCH_POSTING)
    by_req = {c.requirement_id: c.layer for c in criteria}

    assert by_req["R-01"] == "fact"  # 라틴 토큰이 있다
    assert by_req["R-02"] == "judgment"
    assert by_req["R-03"] == "judgment"
    assert branch_of(BRANCH_POSTING[1]) == "graded"  # 폴백은 binary를 만들지 않는다


def test_LLM을_못_부르면_옛_규칙으로_떨어지고_그_사실이_남는다() -> None:
    """`GET /trace`가 `client=None`으로 돈다. **거기서 죽으면 화면이 통째로 빈다.**

    떨어졌다는 사실을 `fell_back`에 남기는 것이 요점이다 — 결과가 같아 보여도 근거가
    다르다. 그리고 폴백은 **`binary`를 만들지 않는다**: 「표현이 갈린다」는 글자 모양으로
    알 수 없는 성질이고, 그게 이 갈래를 LLM에 묻는 이유다.
    """
    result = resolve_branches(BRANCH_POSTING, client=None, cache_path=None)

    assert result.llm_calls == 0
    assert result.fell_back == ["R-01", "R-02", "R-03"]
    assert result.branches == {
        req.id: fallback_branch(req.text) for req in BRANCH_POSTING
    }
    assert "binary" not in set(result.branches.values())


def test_공고당_한_번_묻고_보내는_것은_조건_문구뿐이다(tmp_path) -> None:
    """**이미지도 좌표도 안 간다.** 조건 11~19개를 한 호출에 담는다."""
    client, completions = _branch_client(_labels("term", "binary", "graded"))
    result = resolve_branches(
        BRANCH_POSTING, client=client, cache_path=tmp_path / "branches.json"
    )

    assert result.llm_calls == 1 and len(completions.calls) == 1
    assert result.branches == BRANCHES and result.fell_back == []

    sent = " ".join(
        str(message["content"]) for message in completions.calls[0]["messages"]
    )
    for req in BRANCH_POSTING:
        assert req.text in sent
    # 좌표·이미지·식별자는 프롬프트에 없다. 있으면 「텍스트만 보낸다」가 거짓말이 된다.
    for leaked in ("source_bbox", "img_w", "x1", "R-01", "png"):
        assert leaked not in sent


def test_캐시가_맞으면_다시_묻지_않는다(tmp_path) -> None:
    cache = tmp_path / "branches.json"
    client, _ = _branch_client(_labels("term", "binary", "graded"))
    first = resolve_branches(BRANCH_POSTING, client=client, cache_path=cache)

    second = resolve_branches(BRANCH_POSTING, client=_NeverCalled(), cache_path=cache)

    assert second.branches == first.branches
    assert second.llm_calls == 0 and second.fell_back == []


def test_지시문을_고치면_캐시가_무효가_된다(tmp_path, monkeypatch) -> None:
    """**이 프로젝트에서 실제로 난 사고다.** 프롬프트를 고쳤는데 옛 판정이 재사용됐다.

    캐시 키에 프롬프트 해시가 없으면 「고쳤는데 왜 안 바뀌지」를 찾느라 엉뚱한 데를 판다.
    """
    cache = tmp_path / "branches.json"
    client, _ = _branch_client(_labels("term", "binary", "graded"))
    resolve_branches(BRANCH_POSTING, client=client, cache_path=cache)

    monkeypatch.setattr(branch_module, "_SYSTEM", branch_module._SYSTEM + "\n한 줄 더.")
    again, completions = _branch_client(_labels("graded", "graded", "graded"))
    after = resolve_branches(BRANCH_POSTING, client=again, cache_path=cache)

    assert len(completions.calls) == 1  # 캐시가 안 맞아 다시 물었다
    assert after.branches == {"R-01": "graded", "R-02": "graded", "R-03": "graded"}


def test_답이_빠진_조건만_한_번_더_묻고_그래도_없으면_폴백한다(tmp_path) -> None:
    """모델은 목록이 길면 몇 줄을 조용히 흘린다 (`header_role`에서 실측한 것과 같은 실패).

    **되묻기는 1회다.** 그래도 안 오면 그 조건만 옛 규칙으로 정하고, 그 사실을 남긴다.
    그리고 **떨어진 항목이 있으면 캐시에 남기지 않는다** — 남기면 열화가 고착된다.
    """
    cache = tmp_path / "branches.json"
    client, completions = _branch_client(
        {"labels": [{"index": 1, "branch": "term"}, {"index": 99, "branch": "binary"}]},
        {"labels": [{"index": 1, "branch": "binary"}]},  # 되묻기: R-02만 답이 왔다
    )
    result = resolve_branches(BRANCH_POSTING, client=client, cache_path=cache)

    assert len(completions.calls) == 2 and result.llm_calls == 2
    assert result.branches == {"R-01": "term", "R-02": "binary", "R-03": "graded"}
    assert result.fell_back == ["R-03"]
    assert not cache.exists()


# --- 실측 공고 2건 ---------------------------------------------------------
#
# **눈으로 확인해 손으로 적은 기대값이다** (2026-09-01). LLM을 부르지 않는다 —
# 여기 적힌 것은 「분류기가 이렇게 답해야 한다」는 우리 판단이고, 이 절이 시험하는 것은
# 그 답이 **층과 기준점으로 옳게 흘러가는가**다. 판정 근거는 갈래마다 한 문장씩:
#
# - `term`   — 조건에 이름이 있고, 이력서가 그 이름을 **그대로** 쓸 것이라 기대할 수 있다
# - `binary` — 예/아니오인데 이력서는 그 사실을 **다른 말로** 적는다
# - `graded` — 잘하고 못하고의 **정도**가 있어 서술을 읽어야 안다

REPO_POSTINGS = Path(__file__).resolve().parents[1] / "data" / "postings"

EXPECTED_BRANCHES: dict[str, dict[str, str]] = {
    "kt-b2c": {
        # 이름(AX·AI 등)이 들어 있지만 묻는 것은 **관심과 이해의 정도**다
        "R-01": "graded",
        "R-02": "graded",
        # 아래 넷은 전부 예/아니오이고, 이력서는 넷 다 조건과 다른 말로 적는다
        "R-03": "binary",
        "R-04": "binary",
        "R-05": "binary",
        "R-06": "binary",
    },
    "nexon-game": {
        "R-01": "binary",
        "R-02": "binary",
        "R-03": "binary",
        "R-04": "binary",
        "R-05": "term",
        # 이름이 예로 들려 있지만 묻는 것은 「코드로 구현할 수 있는가」의 정도다
        "R-06": "graded",
        "R-07": "graded",
        "R-08": "graded",
        "R-09": "graded",
        "R-10": "graded",
        "R-11": "term",
        "R-12": "term",
        "R-13": "term",
        # 이름 셋이 나열돼 있다 — 커버리지로 잰다. 경계에 있는 판정이고, 그래서 적어 둔다
        "R-14": "term",
    },
}

# 옛 글자 모양 규칙이 위 기대값과 **어긋나는** 조건. 고친 것이 무엇이었는지의 목록이다.
OLD_RULE_MISMATCH: dict[str, set[str]] = {
    "kt-b2c": {"R-01", "R-03", "R-04", "R-05", "R-06"},
    "nexon-game": {"R-01", "R-02", "R-03", "R-04", "R-07", "R-14"},
}


def _load_posting(posting_id: str) -> list[Requirement]:
    path = REPO_POSTINGS / posting_id / "requirements.json"
    if not path.exists():
        pytest.skip(f"{posting_id}: 파싱 결과가 없다 — 파싱을 먼저 돌린다")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [
        Requirement.model_validate({k: v for k, v in raw.items() if k != "line_ids"})
        for raw in payload["requirements"]
    ]


@pytest.mark.parametrize("posting_id", sorted(EXPECTED_BRANCHES))
def test_실측_공고의_조건이_기대한_갈래대로_층에_간다(posting_id: str) -> None:
    """합성 픽스처로는 말할 수 없는 것 — **고친 것이 진짜 그 조건들을 고쳤는가.**"""
    requirements = _load_posting(posting_id)
    expected = EXPECTED_BRANCHES[posting_id]
    assert {req.id for req in requirements} == set(expected), (
        "공고를 다시 파싱해 조건 목록이 바뀌었다 — 기대값을 눈으로 다시 정해야 한다"
    )

    criteria, _ = _build(requirements, branches=expected)
    by_req = {c.requirement_id: c for c in criteria}

    for req in requirements:
        item = by_req[req.id]
        assert item.branch == expected[req.id]
        assert item.layer == ("fact" if expected[req.id] == "term" else "judgment")
        tail = {text.split("」 — ")[1] for text in item.anchors.values()}
        assert tail == (
            set(SATISFACTION_TEMPLATE.values())
            if expected[req.id] == "binary"
            else set(ANCHOR_TEMPLATE.values())
        )


@pytest.mark.parametrize("posting_id", sorted(EXPECTED_BRANCHES))
def test_옛_규칙이_실측_공고에서_어긋난_자리를_적어_둔다(posting_id: str) -> None:
    """**고치기 전에 무엇이 틀렸는지를 숫자로 남긴다.**

    이 목록이 줄어들면 폴백이 좋아진 것이 아니라 **공고가 다시 파싱된 것**이다.
    그때는 기대값을 눈으로 다시 정해야 하므로, 조용히 지나가지 않게 여기서 막는다.
    """
    requirements = _load_posting(posting_id)
    expected = EXPECTED_BRANCHES[posting_id]
    mismatched = {
        req.id
        for req in requirements
        if ("term" if is_countable(req.text) else "graded") != expected[req.id]
    }

    assert mismatched == OLD_RULE_MISMATCH[posting_id]
    # 필수 조건이 특히 많이 어긋났다 — 형식 요건이 전부 「예/아니오」인데 옛 규칙에는
    # 그 갈래가 없었다.
    required_wrong = {req.id for req in requirements if req.kind == "required"} & mismatched
    assert len(required_wrong) >= 3
