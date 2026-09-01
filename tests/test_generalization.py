"""직군 교차 — **「직군 무관 일반화」를 반증 가능한 형태로 만드는 유일한 테스트다.**

과제 CRITICAL이 「특정 스킬셋·직군을 하드코딩하지 않는다」인데, 그건 보통 **말로만**
주장된다. 코드에 직군 어휘가 없다는 것은 grep으로 보일 수 있어도, 그것이 곧
「루브릭이 공고에서 나왔다」는 아니다.

그래서 반대로 묻는다 — **공고 A의 루브릭으로 공고 B의 지원자를 채점하면 점수가
떨어지는가.** 안 떨어지면 루브릭은 공고를 안 보고 있는 것이고, 그때 이 파일이 실패한다.

## 왜 이 케이스가 1차 지표인가 (`tests/CLAUDE.md`)

우리는 정답 라벨도 만들고 채점도 한다. 「완벽 매칭 2명이 위로 왔다」는 우리가 정한
라벨을 우리가 다시 확인한 것이라 순환이다. **직군 교차는 라벨을 쓰지 않는다** —
두 공고의 파싱 차이가 만드는 신호만 본다. FA-01이 「완벽 매칭」이라는 사실은 이
테스트의 판정에 들어가지 않고, 쓰는 것은 **같은 사람의 두 점수 차**뿐이다.

## 심사위원을 상수로 고정했다 — 그게 이 테스트의 힘이다

판단 층은 LLM이고 예산이 없다 (`tests/conftest.py`). 스텁으로 갈아 끼우는데,
**내용에 반응하는 스텁을 쓰면 하락이 스텁의 재주인지 엔진의 성질인지 구별되지
않는다.** 그래서 `ConstantJudge`를 쓴다 — 항목·이력서와 무관하게 언제나 같은 점수다.

그러면 판단 층 65점은 두 공고에서 **완전히 같은 값**이 되고, 남는 차이는 전부
게이트·사실 층과 루브릭 가중치에서 나온다. 그 층은 LLM이 없는 결정적 코드다.
**즉 여기서 재는 하락은 「LLM이 잘 판단해서」 생긴 것이 아니다.**

> **이 테스트가 증명하지 않는 것**: 판단 층이 직군을 구별하는가는 여기서 말하지
> 않는다. 그건 실물 심사위원으로만 잴 수 있고 예산이 없다. 대신 이 테스트는
> **판단 층을 빼고도** 일반화 주장이 서는지를 본다.

임계 15점은 **임의값**이다 (`tests/CLAUDE.md` 「임계값의 출처」). 실측으로 되맞추지
않는다 — 판정선을 판정 대상의 실행 결과에서 뽑으면 그 테스트는 아무것도 반증하지 못한다.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from conftest import ConstantJudge, OverlapJudge, make_proposal, make_settings, resumes_of

from matching.pipeline import score

# 하락 임계. **임의값이다.** 5점 척도 절반 칸(σ≤0.5)이나 만점의 10%(장황함)처럼
# 근거가 있는 값이 아니라, 「우연으로 보기 어려운 폭」을 만점 100의 15%로 잡은 것뿐이다.
CROSS_JOB_DROP = 15.0


def _totals(posting: str, resume_set: str, candidates, tmp_path, judge) -> dict[str, float]:
    """공고 하나의 루브릭으로 지원자들을 채점하고 `{지원자: 총점}`을 준다.

    `data_dir=tmp_path` — 실제 `data/`를 건드리지 않고, 무엇보다 호출 기록
    (`.judge_usage.json`)을 오염시키지 않는다.
    """
    settings = make_settings()
    result = score(
        make_proposal(posting, settings),
        resumes_of(resume_set, *candidates),
        settings,
        client=judge,
        data_dir=tmp_path,
        now=datetime(2026, 9, 1, 12, 0).astimezone(),
    )
    return {item.candidate_id: item.total for item in result.ranked}


@pytest.mark.parametrize(
    ("own", "other", "resume_set", "candidates"),
    [
        ("a", "b", "a", ("FA-01", "FA-02")),
        ("b", "a", "b", ("FB-01", "FB-02")),
    ],
    ids=["임상→설비", "설비→임상"],
)
def test_직군_교차_루브릭은_점수를_떨어뜨린다(own, other, resume_set, candidates, tmp_path):
    """자기 공고에서 잘 맞은 지원자를 **다른 직군 공고의 루브릭**으로 채점한다.

    두 방향 다 본다. 한 방향만 보면 「공고 B의 루브릭이 그냥 짜다」로도 설명이 되는데,
    양방향에서 대칭으로 떨어지면 그 설명이 남지 않는다.
    """
    mine = _totals(own, resume_set, candidates, tmp_path / own, ConstantJudge())
    theirs = _totals(other, resume_set, candidates, tmp_path / other, ConstantJudge())

    for candidate in candidates:
        drop = mine[candidate] - theirs[candidate]
        assert drop >= CROSS_JOB_DROP, (
            f"{candidate}: 자기 공고 {mine[candidate]:.2f}점 → 다른 직군 공고 "
            f"{theirs[candidate]:.2f}점, 하락 {drop:.2f}점. "
            "임계 미달이면 루브릭이 공고를 보고 만들어진 것이 아니다"
        )


def test_상수_심사위원에서_판단_층은_두_공고에_같은_값을_낸다(tmp_path):
    """위 테스트의 전제 확인 — **하락이 스텁에서 나오지 않았다.**

    `ConstantJudge`는 항목·이력서와 무관하게 같은 점수를 내므로 판단 층의 **정규화된
    비율**이 두 공고에서 같아야 한다. 이게 깨지면 위 테스트의 하락 폭에 판단 층이
    섞인 것이고, 그러면 「결정적 층만으로 잰 값」이라는 말이 거짓이 된다.

    비율로 보는 이유: 두 공고의 판단 항목 수가 같아도 배점 총합은 루브릭이 정하므로,
    비교 가능한 것은 점수 자체가 아니라 **만점 대비 비율**이다.
    """
    from matching.pipeline import layer_max, layer_total

    ratios = []
    for posting in ("a", "b"):
        settings = make_settings()
        result = score(
            make_proposal(posting, settings),
            resumes_of("a", "FA-01"),
            settings,
            client=ConstantJudge(),
            data_dir=tmp_path / posting,
            now=datetime(2026, 9, 1, 12, 0).astimezone(),
        )
        item = result.ranked[0]
        ratios.append(layer_total(item, "judgment") / layer_max(item, "judgment"))

    assert ratios[0] == pytest.approx(ratios[1]), (
        f"판단 층 비율이 공고마다 다르다 {ratios} — 상수 심사위원인데 값이 갈렸다면 "
        "직군 교차 테스트의 하락 폭을 결정적 층만의 것으로 읽을 수 없다"
    )


def test_내용에_반응하는_심사위원에서도_하락은_남는다(tmp_path):
    """대역 심사위원(`OverlapJudge`)으로 바꿔도 방향이 같은지 본다.

    **이 테스트는 보조다.** 겹침 비율은 우리가 정한 식이고 좋은 채점자라는 근거가 없다.
    다만 판단 층을 상수로 묶어 둔 위 테스트가 「판단 층에서는 오히려 올라갈 수도 있다」는
    반론을 남기므로, 내용에 반응하는 대역으로도 부호가 뒤집히지 않는 것만 확인한다.
    임계는 걸지 않는다 — 스텁의 숫자에 임계를 걸면 스텁을 시험하는 것이 된다.
    """
    mine = _totals("a", "a", ("FA-01", "FA-02"), tmp_path / "own", OverlapJudge())
    theirs = _totals("b", "a", ("FA-01", "FA-02"), tmp_path / "other", OverlapJudge())

    for candidate in ("FA-01", "FA-02"):
        assert theirs[candidate] < mine[candidate], (
            f"{candidate}: 대역 심사위원에서 하락이 사라졌다 "
            f"({mine[candidate]:.2f} → {theirs[candidate]:.2f})"
        )
