"""집계·랭킹·승인 게이트의 계약 시험 — **실물 API를 부르지 않는다.**

이 층은 파이프라인에서 **결정적이어야 하는 마지막 마디**다. 심사위원이 무엇을 답하든
그 뒤의 산술과 순서는 언제나 같아야 하고, 검산에 걸리면 결과가 나가지 않아야 한다.

| 고른 케이스 | 깨지면 무엇이 거짓말이 되나 |
|---|---|
| 항목 수가 달라도 만점이 100 | 「0~100점」. 만점이 공고마다 다르면 두 점수를 못 나란히 놓는다 |
| 판단 1점이 0점으로 사상 | 5점 척도의 뜻. `raw/5`면 **관련 경험 없음에도 20%**가 붙는다 |
| 채점 안 된 항목이 있으면 예외 | 만점이 지원자마다 달라져 **등수 비교가 성립하지 않는다** |
| 게이트 탈락자가 분리되고 사유가 남는다 | 0층 설계. 「gate_failed: true」는 사유가 아니다 |
| 동점을 판단 층 → id 순으로 가른다 | **재현 가능성.** 무작위면 등수가 바뀐 이유를 못 댄다 |
| G1을 깬 그래프에서 결과가 안 나온다 | 검산이 **런타임 게이트**라는 것. 경고면 그냥 나간다 |
| 승인 없이는 채점하지 않는다 | `human_validated`에 도달할 경로. 없으면 영원히 「AI 초안」 |
| 공고가 수정·마감되면 승인이 낡는다 (G7) | 「사람이 **현재 revision**을 확인했다」 |
| 같은 입력에 같은 랭킹 | 이 층의 결정성 |
| `explain()`이 사람이 읽는 문장이다 | 과제 요구 ③의 **뒤쪽 절반** — 「근거를 함께 낸다」 |

심사위원 응답은 **고정 픽스처**다. 같은 이력서에는 언제나 같은 점수가 오므로, 랭킹이
흔들리면 그건 모델이 아니라 우리 코드다.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from matching.config import Settings
from matching.model import BBox, EvidenceGraph, GovernanceError, Requirement, Resume, Score, Span
from matching.pipeline import (
    AggregateError,
    ApprovalRequired,
    ApprovalStale,
    RubricProposal,
    aggregate,
    explain,
    layer_total,
    load_run,
    rank,
    score,
)
from matching.rubric import build_rubric
from matching.source.registry import PostingMeta

# --- 붙박이 입력 -----------------------------------------------------------

REVISION = "1756700000"

# 조건 문구에 직군 어휘를 넣지 않는다 — 층 배정은 **문자의 종류**로만 갈린다
# (`rubric/build.py`의 `is_countable`). 라틴 토큰이 있으면 사실 층, 없으면 판단 층이다.
FACT_TEXT = "Python 및 SQL 활용 경험"
JUDGMENT_TEXT = "여러 이해관계자를 조율해 본 경험이 있는 분"
GATE_TEXT = "국가자격 잠수산업기사 소지자"

# 지원자마다 하나씩. **마스킹에 걸리지 않는 표지**여야 한다 (`scorer/mask.py`) —
# 가짜 심사위원이 프롬프트에서 지원자를 알아보는 열쇠다.
MARKERS = {"A-01": "코드명 알파", "A-02": "코드명 베타", "A-03": "코드명 감마"}

RESUMES: dict[str, str] = {
    "A-01": (
        "프로젝트 코드명 알파\n"
        "Python과 SQL로 사내 정산 배치를 만들고 직접 운영했습니다.\n"
        "여러 부서와 매주 협의체를 열어 요구를 모으고 우선순위를 정했습니다.\n"
        "그 결과 마감 지연이 분기 4건에서 0건으로 줄었습니다.\n"
        "국가자격 잠수산업기사를 취득해 보유하고 있습니다.\n"
    ),
    "A-02": (
        "프로젝트 코드명 베타\n"
        "Python으로 리포트 생성을 자동화했습니다.\n"
        "협업 과정에서 의견이 갈릴 때 회의를 열어 조율한 적이 있습니다.\n"
        "국가자격 잠수산업기사를 취득해 보유하고 있습니다.\n"
    ),
    "A-03": (
        "프로젝트 코드명 감마\n"
        "맡은 일을 성실히 수행했고 팀에 잘 적응했습니다.\n"
        "앞으로도 열심히 배우겠습니다.\n"
    ),
}

# 가짜 심사위원이 인용할 조각. **이력서에 실재해야** `keep_quotes`를 통과한다.
QUOTES = {
    "A-01": "여러 부서와 매주 협의체를 열어 요구를 모으고 우선순위를 정했습니다",
    "A-02": "의견이 갈릴 때 회의를 열어 조율한 적이 있습니다",
    "A-03": "맡은 일을 성실히 수행했고 팀에 잘 적응했습니다",
}

# 판단 층 원점수. 1점이 0점으로 사상되는 것을 이 표로 확인한다.
PLAN = {"A-01": 5, "A-02": 3, "A-03": 1}


def _bbox() -> BBox:
    return BBox(page=1, x1=10, y1=20, x2=800, y2=48, img_w=860, img_h=2533)


def _req(req_id: str, text: str, kind: str = "required") -> Requirement:
    return Requirement(
        id=req_id,
        text=text,
        kind=kind,  # type: ignore[arg-type]
        evidence_grade="E2",
        ladder_step=1,
        source_bbox=_bbox(),
        source_span=Span(start=0, end=len(text)),
    )


def _settings(**overrides) -> Settings:
    base = {"judge_model": "fixed-model-2026-08-31", "max_total_calls": 200}
    base.update(overrides)
    return Settings(**base)


def _rubric(requirements: list[Requirement], settings: Settings | None = None):
    """조건 목록 → (그래프, 루브릭). 실제 경로와 같은 `build_rubric`을 쓴다."""
    graph = EvidenceGraph()
    active = settings if settings is not None else _settings()
    criteria = build_rubric(requirements, active, graph)
    return graph, criteria


def _proposal(requirements: list[Requirement], *, approved: bool = True) -> RubricProposal:
    graph, criteria = _rubric(requirements)
    return RubricProposal(
        posting_id="test-posting",
        source_kind="local",
        requirements=requirements,
        criteria=criteria,
        graph=graph,
        posting_revision=REVISION,
        approved_at=datetime(2026, 9, 1, 9, 0).astimezone() if approved else None,
        approved_by="고객사 담당자" if approved else None,
    )


def _resumes(*candidate_ids: str) -> list[Resume]:
    return [Resume(candidate_id=cid, text=RESUMES[cid]) for cid in candidate_ids]


# --- 가짜 심사위원 ---------------------------------------------------------


class _ScriptedClient:
    """이력서마다 **정해진 점수**를 준다. 같은 입력에 같은 응답 — 재현성 테스트의 전제.

    프롬프트에서 지원자를 알아내는 것이 요점이다. 호출 순서에 기대면 채점 순서를 바꿨을 때
    테스트가 조용히 다른 것을 재게 된다.
    """

    def __init__(self, plan: dict[str, int] | None = None) -> None:
        self.plan = dict(plan if plan is not None else PLAN)
        self.calls = 0
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        self.calls += 1
        body = " ".join(str(message.get("content", "")) for message in kwargs["messages"])
        for candidate_id, marker in MARKERS.items():  # noqa: B007 — break로 값을 쓴다
            if marker in body:
                break
        else:
            raise AssertionError("프롬프트에서 지원자를 알아보지 못했다")

        text = RESUMES[candidate_id]
        fragment = QUOTES[candidate_id]
        start = text.index(fragment)
        payload = {
            "quotes": [{"start": start, "end": start + len(fragment), "text": fragment}],
            "reasoning": "본인 역할과 결과가 얼마나 구체적으로 적혔는지만 보았다.",
            "score": self.plan[candidate_id],
        }
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps(payload, ensure_ascii=False)
                    )
                )
            ],
            usage=SimpleNamespace(prompt_tokens=1200, completion_tokens=180),
        )


class _FakeRegistry:
    """`PostingRegistry`의 최소 구현. G7만 시험한다."""

    def __init__(self, meta: PostingMeta | None) -> None:
        self._meta = meta

    def lookup(self, company: str, title: str) -> PostingMeta | None:
        return self._meta

    def current(self, posting_id: str) -> PostingMeta | None:
        return self._meta


def _meta(revision: str = REVISION, *, active: bool = True, expires: date | None = None):
    return PostingMeta(
        company="테스트회사",
        title="테스트 공고",
        posting_date=date(2026, 8, 31),
        modification_timestamp=revision,
        expiration_date=expires,
        active=active,
    )


def _run(tmp_path, *, requirements=None, candidates=("A-01", "A-02", "A-03"), **kwargs):
    """완주 1회. 파일은 전부 `tmp_path` 밑에만 쓴다."""
    items = requirements if requirements is not None else [
        _req("R-01", FACT_TEXT),
        _req("R-02", JUDGMENT_TEXT),
    ]
    proposal = kwargs.pop("proposal", None) or _proposal(items)
    settings = kwargs.pop("settings", None) or _settings()
    client = kwargs.pop("client", None) or _ScriptedClient()
    return score(
        proposal,
        _resumes(*candidates),
        settings,
        client=client,
        data_dir=tmp_path,
        now=datetime(2026, 9, 1, 12, 0).astimezone(),
        **kwargs,
    )


# --- 만점은 언제나 100 -------------------------------------------------------


@pytest.mark.parametrize("count", [2, 5, 9])
def test_항목_수가_달라도_만점은_100이다(count: int):
    """공고마다 항목 수가 다르다 — 실측으로 kt-b2c 11개, nexon-game 19개다.

    만점이 공고마다 다르면 「0~100점」이 거짓말이 되고, 두 공고의 점수를 나란히 놓을 수
    없다. 이 계약은 `build_rubric`이 세우고 `aggregate`가 **깨지 않는 것**으로 지킨다.
    """
    requirements = [_req("R-01", FACT_TEXT), _req("R-02", JUDGMENT_TEXT)]
    requirements += [
        _req(f"R-{index:02d}", f"{JUDGMENT_TEXT} (구분 {index})", "preferred")
        for index in range(3, count + 1)
    ]
    graph, criteria = _rubric(requirements)

    scores = []
    for criterion in criteria:
        value = 1.0 if criterion.layer == "fact" else 5.0
        item = Score(
            id=f"S-A-01-{criterion.id}",
            criterion_id=criterion.id,
            candidate_id="A-01",
            value=value,
            layer=criterion.layer,
            judge_id=None,
            rationale="시험용 점수",
        )
        graph.add(item)
        scores.append(item)

    result = aggregate(scores, criteria, graph, _settings())
    assert sum(axis.max_weighted for axis in result.breakdown) == pytest.approx(100.0)
    assert result.total == pytest.approx(100.0)


def test_판단_1점은_0점이_되고_3점은_절반이_된다():
    """5점 척도의 1점은 「관련 경험이 없음」이다. `raw/5`로 정규화하면 **최하점에도 20%**가
    붙어, 항목이 많을수록 아무 경험 없는 지원자의 총점이 올라간다.
    """
    requirements = [_req("R-01", JUDGMENT_TEXT)]
    graph, criteria = _rubric(requirements)
    criterion = criteria[0]
    assert criterion.layer == "judgment"
    assert criterion.weight == pytest.approx(100.0)

    totals = {}
    for raw in (1.0, 3.0, 5.0):
        working = graph.model_copy(deep=True)
        item = Score(
            id=f"S-A-{raw:g}",
            criterion_id=criterion.id,
            candidate_id="A-01",
            value=raw,
            layer="judgment",
            judge_id="panel-2",
            rationale="시험용 점수",
        )
        working.add(item)
        totals[raw] = aggregate([item], criteria, working, _settings()).total

    assert totals[1.0] == pytest.approx(0.0)
    assert totals[3.0] == pytest.approx(50.0)
    assert totals[5.0] == pytest.approx(100.0)


def test_채점되지_않은_항목이_있으면_부분_합계를_내지_않는다():
    """빼고 더하면 그 지원자의 만점만 줄어 **등수 비교가 성립하지 않는다.**
    조용히 빠지면 채점에 실패한 지원자가 오히려 높은 비율을 받는 일까지 생긴다.
    """
    graph, criteria = _rubric([_req("R-01", FACT_TEXT), _req("R-02", JUDGMENT_TEXT)])
    only = Score(
        id="S-A-01-C-01",
        criterion_id=criteria[0].id,
        candidate_id="A-01",
        value=1.0,
        layer=criteria[0].layer,
        judge_id=None,
        rationale="시험용 점수",
    )
    graph.add(only)

    with pytest.raises(AggregateError, match="점수가 없다"):
        aggregate([only], criteria, graph, _settings())


# --- 랭킹 -------------------------------------------------------------------


def test_게이트_탈락자는_랭킹에서_분리되고_사유가_문장으로_남는다(tmp_path):
    """탈락자를 목록에서 지우지 않는다. 지우면 **게이트가 틀렸을 때 확인할 방법이 없다.**

    `gate_failed: true`는 문장이 아니다 — 사유가 사람이 읽는 말로 남아야 한다.
    """
    requirements = [
        _req("R-01", FACT_TEXT),
        _req("R-02", JUDGMENT_TEXT),
        _req("R-03", GATE_TEXT),
    ]
    result = _run(tmp_path, requirements=requirements)

    by_id = {item.candidate_id: item for item in result.ranked}
    assert by_id["A-03"].rank is None  # 자격 표현이 이력서에 없다
    assert by_id["A-01"].rank == 1
    assert by_id["A-02"].rank == 2
    # 탈락자는 목록 **끝**에 붙는다
    assert result.ranked[-1].candidate_id == "A-03"

    reasons = by_id["A-03"].gate.reasons
    assert reasons and all(len(reason) > 20 for reason in reasons)
    assert "탈락" in reasons[0] and "찾" in reasons[0]
    assert by_id["A-03"].gate.failed_criteria


def test_동점은_판단_층이_높은_쪽이_위이고_그래도_같으면_id_순이다():
    """무작위로 가르지 않는다 — 재현 불가능한 순위는 「왜 등수가 바뀌었나」에 답할 수 없다.

    판단 층을 먼저 보는 이유: 타당도가 높은 축이다 (세는 방식 r≈0.15 / 판단 r≈0.48).
    총점이 같다면 그 점수가 어느 축에서 왔는지가 유일하게 남은 정보다.
    """
    requirements = [_req("R-01", FACT_TEXT), _req("R-02", JUDGMENT_TEXT)]
    graph, criteria = _rubric(requirements)
    fact = next(item for item in criteria if item.layer == "fact")
    judgment = next(item for item in criteria if item.layer == "judgment")

    def _make(candidate_id: str, fact_raw: float, judgment_raw: float):
        working = graph.model_copy(deep=True)
        made = []
        for criterion, raw in ((fact, fact_raw), (judgment, judgment_raw)):
            item = Score(
                id=f"S-{candidate_id}-{criterion.id}",
                criterion_id=criterion.id,
                candidate_id=candidate_id,
                value=raw,
                layer=criterion.layer,
                judge_id=None,
                rationale="시험용 점수",
            )
            working.add(item)
            made.append(item)
        return aggregate(made, criteria, working, _settings())

    # 총점(35 × raw + 65 × (raw-1)/4)은 32.5로 같고 **판단 층 몫만 다른** 두 명.
    # id는 일부러 거꾸로 뒀다 — id 순이었다면 `A-20`이 위로 올라온다.
    by_judgment = _make("Z-01", 0.0, 3.0)  # 판단 32.5 · 사실 0
    by_fact = _make("A-20", 16.25 / 35, 2.0)  # 판단 16.25 · 사실 16.25
    assert by_judgment.total == pytest.approx(by_fact.total)
    assert layer_total(by_judgment, "judgment") > layer_total(by_fact, "judgment")

    # 총점도 판단 층도 같은 두 명 — 여기서 id 순이 발동한다.
    same_late = _make("B-02", 0.5, 4.0)
    same_early = _make("A-09", 0.5, 4.0)
    assert same_late.total == pytest.approx(same_early.total)

    ordered = rank([by_fact, same_late, by_judgment, same_early])
    assert [item.candidate_id for item in ordered] == ["A-09", "B-02", "Z-01", "A-20"]
    assert [item.rank for item in ordered] == [1, 2, 3, 4]


def test_같은_입력에_같은_랭킹이_나온다(tmp_path):
    """심사위원 응답을 고정하면 그 뒤는 전부 산술이다. 흔들리면 그건 모델이 아니라 우리다."""
    first = _run(tmp_path)
    second = _run(tmp_path)

    assert [item.candidate_id for item in first.ranked] == ["A-01", "A-02", "A-03"]
    assert [item.candidate_id for item in second.ranked] == [
        item.candidate_id for item in first.ranked
    ]
    assert [item.total for item in second.ranked] == [item.total for item in first.ranked]
    # 사실 35 / 판단 65 · A-01은 두 축 만점
    assert first.ranked[0].total == pytest.approx(100.0)
    assert first.ranked[-1].total == pytest.approx(0.0)


# --- 검산이 집계 앞에 있다 ---------------------------------------------------


def test_G1을_깬_그래프에서는_결과가_나오지_않는다(tmp_path):
    """검산은 테스트가 아니라 **런타임 게이트**다. 경고로 내려가는 순간 근거 없는 점수가
    화면에 나간다. 그래서 예외가 나야 하고, 결과 파일도 남으면 안 된다.
    """
    proposal = _proposal([_req("R-01", FACT_TEXT), _req("R-02", JUDGMENT_TEXT)])
    # 근거 Link가 하나도 없는 점수를 심어 둔다 — 사실 층이지만 값이 0이 아니라
    # G1의 예외(「인용할 구간이 없는 0점」)에도 걸리지 않는다.
    proposal.graph.add(
        Score(
            id="S-BROKEN-01",
            criterion_id=proposal.criteria[0].id,
            candidate_id="A-01",
            value=0.7,
            layer="fact",
            judge_id=None,
            rationale="근거 없이 심어 둔 점수",
        )
    )

    with pytest.raises(GovernanceError) as caught:
        _run(tmp_path, proposal=proposal)

    assert [violation.rule for violation in caught.value.violations] == ["G1"]
    assert caught.value.violations[0].object_id == "S-BROKEN-01"
    assert not (tmp_path / "runs").exists()  # 결과를 내보내지 않았다


# --- 승인 게이트 (검산 G7) ---------------------------------------------------


def test_승인이_없으면_채점하지_않는다(tmp_path):
    """`review_status`는 필드가 아니라 절차다. 여기서 안 막으면 `human_validated`에
    도달할 경로가 없고, 배지는 영원히 「AI 초안」인 채로 남는다.
    """
    proposal = _proposal([_req("R-01", FACT_TEXT)], approved=False)
    with pytest.raises(ApprovalRequired, match="승인이 없다"):
        _run(tmp_path, proposal=proposal, candidates=("A-01",), client=_ScriptedClient())


def test_승인을_건너뛰면_미승인_표시가_붙는다(tmp_path):
    """건너뛰기는 **명시적으로만** 되고, 그 사실이 결과와 화면에 남는다.
    조용히 지나갈 수 없게 만드는 것이 이 설계의 요점이다.
    """
    proposal = _proposal([_req("R-01", FACT_TEXT)], approved=False)
    result = _run(
        tmp_path,
        proposal=proposal,
        candidates=("A-01",),
        settings=_settings(skip_approval=True),
    )

    assert result.unapproved is True
    assert "미승인" in explain(result)


def test_공고가_수정되면_승인이_낡아_채점을_막는다(tmp_path):
    """승인은 **그 시점의 공고**에 대한 것이다. 이걸 안 보면 낡은 루브릭으로 계속 채점하면서
    「사람 확인함」 배지를 달고 있게 된다.
    """
    with pytest.raises(ApprovalStale, match="수정"):
        _run(tmp_path, candidates=("A-01",), registry=_FakeRegistry(_meta("1756799999")))


def test_마감된_공고는_승인과_무관하게_채점하지_않는다(tmp_path):
    """마감 공고 채점 차단은 승인 여부와 별개다 — 승인이 있어도 지금 없는 공고다."""
    with pytest.raises(ApprovalStale, match="마감"):
        _run(tmp_path, candidates=("A-01",), registry=_FakeRegistry(_meta(active=False)))


def test_레지스트리가_없으면_G7을_검사하지_못했다고_적는다(tmp_path):
    """검사해서 통과한 것과 검사하지 못한 것은 다르다. 지금(사람인 키 미발급)이 후자다."""
    result = _run(tmp_path, candidates=("A-01",))
    assert result.revision_checked is False

    checked = _run(tmp_path, candidates=("A-01",), registry=_FakeRegistry(_meta()))
    assert checked.revision_checked is True


# --- 결과 저장 --------------------------------------------------------------


def test_결과는_파일로_남고_되읽을_수_있으며_이력서_원문은_없다(tmp_path):
    """`RunResult`에 원문을 싣지 않는다 (`docs/LEGAL_ARCHITECTURE.md` §3-③).
    남는 것은 **인용 조각**이라 결과 JSON만으로 이력서가 복원되지 않는다.

    되읽기까지 확인하는 이유: `GET /runs/{run_id}`(step 8)가 이 경로를 그대로 탄다.
    """
    result = _run(tmp_path)
    body = (tmp_path / "runs" / result.run_id / "result.json").read_text(encoding="utf-8")

    assert "앞으로도 열심히 배우겠습니다" not in body  # 인용되지 않은 문장은 안 실린다
    reloaded = load_run(result.run_id, tmp_path)
    assert [item.candidate_id for item in reloaded.ranked] == [
        item.candidate_id for item in result.ranked
    ]
    assert reloaded.cost.calls == result.cost.calls


# --- 요구 ③의 「사람이 읽는 근거」 ---------------------------------------------


def test_explain_is_human_readable(tmp_path):
    """`explain()` 출력이 사람이 읽는 문장인지 — 필드 덤프가 아닌지.

    과제 요구는 **0~100점**만이 아니라 「사람이 읽을 수 있는 근거를 함께 낸다」이다.
    점수 쪽만 검사하면 그 요구는 절반만 검사된 것이다.
    """
    out = explain(_run(tmp_path))

    assert "점" in out and "위" in out  # 점수·순위가 한국어로
    # 벌거벗은 ID만 있는 줄 금지 — ID를 안 봐도 뜻이 통해야 사람이 읽는 것이다
    assert not re.search(r"[A-Z]-\d{2}(?![^\n]*[가-힣])", out)
    for line in out.splitlines():
        # 실제 출력은 `└ 근거: …` 모양이라 트리 기호를 떼고 본다. 안 떼면 이 검사가
        # 한 줄도 안 걸려 **아무것도 확인하지 않는다.**
        stripped = line.strip().lstrip("└─· ")
        if stripped.startswith("근거"):
            assert len(stripped) > 20, f"근거가 너무 짧다: {line}"
    assert "이력서" in out or "「" in out  # 인용이 보인다


def test_근거_등급과_승인_상태가_출력에_함께_나온다(tmp_path):
    """`[E2 · AI 초안]`을 빼지 마라. **근거 등급과 `review_status`가 보이는 것**이
    이 설계의 요점이다 — 점수만 보여주면 그 점수가 무엇에 근거했고 누가 확인한 것인지
    사라진다.
    """
    result = _run(tmp_path)
    out = explain(result, "A-01")

    assert "[E2 · AI 초안]" in out
    assert "1위" in out and "100.0 / 100점" in out
    # 근거 문단은 `render_rationale()`이 그래프를 따라가 만든 것이다 — 저장된 문장이 아니다
    axis = result.ranked[0].breakdown[0]
    assert "공고 이미지" in axis.rationale and "판정 사다리" in axis.rationale


def test_게이트_탈락자의_출력에_탈락_사유가_문장으로_있다(tmp_path):
    requirements = [
        _req("R-01", FACT_TEXT),
        _req("R-02", JUDGMENT_TEXT),
        _req("R-03", GATE_TEXT),
    ]
    result = _run(tmp_path, requirements=requirements)
    out = explain(result, "A-03")

    assert "탈락" in out and "순위에서 뺐다" in out
    assert "찾지 못해" in out
