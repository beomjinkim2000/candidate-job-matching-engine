"""등수 뒤집기 최소 편집 (step 12-A) — **전부 산술이다. LLM이 없다.**

이 층은 결정적이어야 한다. 같은 결과 목록을 넣으면 언제나 같은 편집 집합이 나와야 하고,
아니면 「왜 저 사람이 아니라 이 사람인가」라는 문장이 실행마다 달라진다.

| 고른 케이스 | 깨지면 무엇이 거짓말이 되나 |
|---|---|
| 게이트 탈락자에게 최소 편집을 말하지 않는다 | **「이것만 있으면」이 거짓말이 되는 유일한 경우** |
| 게이트 항목이 편집 후보에 안 들어간다 | 위와 같은 이유. 배점 0이라 우연히 빠지는 것과 다르다 |
| 집합이 4개면 나열하지 않는다 | 격차가 크면 「이것만 있었으면」이 무의미해진다 |
| 전 항목 만점으로도 못 넘으면 구조적 미달 | 넘을 수 없는 격차를 「3개만 채우면」이라 못 쓴다 |
| 점수 차 0인 두 사람이 같은 동점권 | 0.4점 차로 1·2위를 확정하는 것이 **거짓 정밀도**다 |
| 동점권을 사슬로 잇지 않는다 | A−C가 ε보다 큰데 「구분 불가」라고 적는 것을 막는다 |
| 같은 입력에 같은 집합 | 반사실 설명의 **체리피킹은 출력만으로 탐지 불가능**하다 |
| LLM 클라이언트를 안 받는다 | 이 기능의 값어치가 「비용 0·결정적」이다 |

픽스처는 `CandidateResult`를 **직접 조립**한다. 파이프라인을 돌리지 않는 이유: 여기서
재는 것은 산술 하나이고, 채점을 태우면 그 산술이 심사위원 스텁의 값에 가려진다.
"""

from __future__ import annotations

import inspect

from matching.pipeline import contrast
from matching.pipeline.aggregate import AxisScore, CandidateResult
from matching.pipeline.contrast import (
    MAX_LISTED_EDITS,
    SEARCH_RULE,
    minimal_flip,
    render_flip,
    tie_bands,
)
from matching.pipeline.rank import rank
from matching.scorer.gate import GateResult


def _axis(criterion_id: str, weighted: float, max_weighted: float, layer: str = "judgment"):
    return AxisScore(
        criterion_id=criterion_id,
        label=f"{criterion_id} 항목",
        layer=layer,  # type: ignore[arg-type]
        raw=0.0,
        weighted=weighted,
        max_weighted=max_weighted,
        rationale="시험용",
        evidence_ids=[],
        evidence_grade="E2",
        review_status="draft",
    )


def _result(candidate_id: str, axes: list[AxisScore], *, gate: GateResult | None = None):
    passed = gate if gate is not None else GateResult(passed=True, failed_criteria=[], reasons=[])
    return CandidateResult(
        candidate_id=candidate_id,
        total=round(sum(axis.weighted for axis in axes), 6),
        rank=None,
        gate=passed,
        breakdown=axes,
        graph_ref="test-run",
    )


def _two_way():
    """1위 99점 / 2위 89점 (격차 10). 2위의 여유분은 C-01 4점 · C-02 7점 · C-03 20점."""
    top = _result("T-01", [_axis("C-01", 30.0, 30.0), _axis("C-02", 69.0, 70.0)])
    second = _result(
        "T-02",
        [
            _axis("C-01", 26.0, 30.0),  # 여유 4
            _axis("C-02", 63.0, 70.0),  # 여유 7
            _axis("C-03", 0.0, 20.0),  # 여유 20
        ],
    )
    return rank([top, second])


# --- 게이트 -----------------------------------------------------------------


def test_게이트_탈락자에게는_최소_편집을_말하지_않는다():
    """탈락자에게 「이것만 있으면 2위였습니다」는 **거짓말**이다.

    면허가 없으면 다른 항목을 만점으로 올려도 그 일을 못 한다. 그래서 편집 집합을 비우고
    `gate_blocked`로 구분한 뒤, 탈락 항목만 별도 문단용으로 넘긴다.
    """
    failed = GateResult(passed=False, failed_criteria=["C-00"], reasons=["면허 미보유"])
    results = rank(
        [
            _result("T-01", [_axis("C-01", 90.0, 100.0)]),
            _result(
                "T-09",
                [_axis("C-00", 0.0, 0.0, "gate"), _axis("C-01", 10.0, 100.0)],
                gate=failed,
            ),
        ]
    )
    flip = minimal_flip(results, "T-09")

    assert flip.gate_blocked is True
    assert flip.minimal_set == []
    assert flip.gate_criteria == ["C-00"]
    assert "넘을 수 있는 상태가 아닙니다" in render_flip(flip)


def test_게이트_항목은_편집_후보에_들어가지_않는다():
    """배점 0이라 **우연히** 빠지는 것과 규칙으로 빼는 것은 다르다.

    가중치 설정이 바뀌어 게이트 항목에 배점이 붙는 날, 우연에 기대고 있었다면 탈락 사유가
    조용히 「올리면 되는 항목」으로 둔갑한다. 그래서 `layer == "gate"`로 명시적으로 뺀다.
    """
    results = rank(
        [
            _result("T-01", [_axis("C-00", 5.0, 5.0, "gate"), _axis("C-01", 80.0, 95.0)]),
            _result("T-02", [_axis("C-00", 0.0, 5.0, "gate"), _axis("C-01", 70.0, 95.0)]),
        ]
    )
    flip = minimal_flip(results, "T-02")

    # 격차 15점. 게이트 항목의 여유분 5점을 후보에 넣으면 C-00만으로는 못 넘지만
    # 「C-00 + C-01」이 크기 2로 걸려 **탈락 사유가 편집 대상처럼 보이게 된다**.
    assert flip.gap == 15.0

    assert "C-00" not in flip.minimal_set
    assert flip.minimal_set == ["C-01"]


# --- 최소성과 나열 상한 ------------------------------------------------------


def test_바로_위_순위를_넘기는_최소_집합을_고른다():
    """격차 10점. C-03(여유 20) 하나로 넘어서므로 크기 1이 답이다.

    C-01+C-02(4+7=11)도 넘기지만 크기가 2다. **크기가 먼저**이고, 같은 크기 안에서만
    필요 상승폭이 작은 쪽을 본다.
    """
    flip = minimal_flip(_two_way(), "T-02")

    assert flip.target_rank == 1
    assert flip.target_candidate_id == "T-01"
    assert flip.gap == 10.0
    assert flip.minimal_set == ["C-03"]
    assert flip.structural is False


def test_편집_집합이_상한을_넘으면_나열하지_않는다():
    """항목 4개를 다 올려야 넘는 경우. `MAX_LISTED_EDITS`가 3이므로 나열하지 않는다.

    격차가 크면 「이것만 있었으면」이라는 문장이 무의미해진다. 네 가지를 나열하는 대신
    「구조적 미달」이라고 적는 편이 정직하다.
    """
    top = _result("T-01", [_axis("C-01", 100.0, 100.0)])
    low = _result(
        "T-02",
        [_axis(f"C-{index:02d}", 0.0, 26.0) for index in range(1, 5)],  # 여유 26 × 4
    )
    flip = minimal_flip(rank([top, low]), "T-02")

    assert flip.structural is True
    assert flip.minimal_set == []
    assert str(MAX_LISTED_EDITS) in render_flip(flip)


def test_전_항목을_만점으로_올려도_못_넘으면_구조적_미달이다():
    """넘을 수 없는 격차를 「몇 개만 채우면」으로 적지 않는다."""
    top = _result("T-01", [_axis("C-01", 100.0, 100.0)])
    low = _result("T-02", [_axis("C-01", 10.0, 40.0)])  # 만점 40 < 100
    flip = minimal_flip(rank([top, low]), "T-02")

    assert flip.structural is True
    assert flip.minimal_set == []


def test_1위에게는_넘어설_상대가_없다():
    flip = minimal_flip(_two_way(), "T-01")
    assert flip.target_rank is None
    assert flip.minimal_set == []
    assert flip.structural is False


# --- 동점권 ------------------------------------------------------------------


def test_점수_차가_0이면_같은_동점권이다():
    """0.4점 차로 1위와 2위를 확정하는 것은 **거짓 정밀도**다.

    같은 점수인데 순위 번호가 다르다는 사실만 화면에 나가면, 읽는 사람은 그 차이를
    실재하는 것으로 읽는다.
    """
    axes = [_axis("C-01", 50.0, 60.0)]
    results = rank([_result("T-01", axes), _result("T-02", list(axes))])

    assert tie_bands(results, 1.0) == [["T-01", "T-02"]]


def test_동점권을_사슬로_잇지_않는다():
    """A−B < ε, B−C < ε인데 A−C > ε인 배치. 셋을 한 밴드로 묶으면 거짓말이다.

    A와 C는 실제로 구별되는데 「구분 불가」라고 적는 셈이 된다. 공고당 6명뿐이라
    사슬이 전원을 삼키는 일이 실제로 일어날 수 있다.
    """
    results = rank(
        [
            _result("T-01", [_axis("C-01", 90.0, 100.0)]),
            _result("T-02", [_axis("C-01", 89.4, 100.0)]),
            _result("T-03", [_axis("C-01", 88.8, 100.0)]),
        ]
    )
    assert tie_bands(results, 1.0) == [["T-01", "T-02"], ["T-03"]]


def test_게이트_탈락자는_동점권에_들어가지_않는다():
    failed = GateResult(passed=False, failed_criteria=["C-00"], reasons=["면허 미보유"])
    results = rank(
        [
            _result("T-01", [_axis("C-01", 90.0, 100.0)]),
            _result("T-09", [_axis("C-01", 90.0, 100.0)], gate=failed),
        ]
    )
    assert tie_bands(results, 1.0) == [["T-01"]]


# --- 감사 가능성 -------------------------------------------------------------


def test_같은_입력에_같은_집합이_나온다():
    """**반사실 설명의 체리피킹은 출력만으로 탐지 불가능하다.**

    최소 집합이 여럿일 때 어느 걸 골랐는지는 결과만 봐서 감사할 수 없다. 남은 방어는
    ① 규칙을 상수로 고정하고 ② 그 규칙을 화면에 적는 것뿐이므로, 최소한 **같은 입력에
    같은 답**은 성립해야 한다.
    """
    first = minimal_flip(_two_way(), "T-02")
    second = minimal_flip(_two_way(), "T-02")
    assert first.minimal_set == second.minimal_set
    assert first.search_rule == SEARCH_RULE


def test_탐색_규칙이_결과에_실려_화면까지_간다():
    """규칙 공개가 유일한 방어다. 코드 상수로만 있고 결과에 안 실리면 아무도 못 본다."""
    flip = minimal_flip(_two_way(), "T-02")
    assert flip.search_rule == SEARCH_RULE
    assert "완전 탐색" in SEARCH_RULE
    assert "그리디" in SEARCH_RULE


def test_LLM_클라이언트를_받는_함수가_없다():
    """12-A의 값어치는 「추가 호출 0회·결정적」이다.

    이 모듈의 공개 함수 어디에도 `client` 인자가 없어야 한다. 인자가 생기는 순간
    누군가 거기에 실물 클라이언트를 꽂고, 「계산이 곧 이유」라는 성질이 사라진다.
    """
    for name, member in vars(contrast).items():
        if name.startswith("_") or not inspect.isfunction(member):
            continue
        parameters = inspect.signature(member).parameters
        assert "client" not in parameters, f"{name}()에 client 인자가 생겼다"

    source = inspect.getsource(contrast)
    assert "openai" not in source.lower(), "contrast.py가 OpenAI를 import한다"
