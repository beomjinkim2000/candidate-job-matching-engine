"""등수 뒤집기 최소 편집 — **「왜 저 사람이 아니라 이 사람인가」에 답한다.**

## 왜 필요한가

우리 출력은 **순위**인데 근거는 **개별 카드**로 나온다. 3등 카드를 아무리 자세히 읽어도
**왜 2등이 아닌지는 안 나온다.** 그런데 채용담당자가 실제로 던지는 질문은 「이 사람 몇
점이에요?」가 아니라 **「얘가 왜 쟤보다 위예요?」**다 (`docs/EVIDENCE_IDEAS.md` 아이디어 3).

대조 설명 연구가 같은 곳을 가리킨다 — *「왜 A인가」보다 「왜 B가 아니라 A인가」*가 인간
수용성에서 우월하고, 단 **대조 상대를 아무거나 잡으면 효과가 없다.** 사람이 실제로
헷갈릴 상대여야 하고, 랭킹에서 그건 **바로 위 순위**다.

## LLM을 한 번도 부르지 않는다

축별 점수 벡터가 이미 있으므로 항목을 하나씩 켜고 끄며 총점을 다시 계산하면 된다.
**계산이 곧 이유다.** 그래서 이 기능은 「LLM이 말한 근거가 점수를 만든 이유가 아니다」라는
문제 자체를 우회한다 — 여기엔 말이 없고 산술만 있다.

## 탐색 규칙을 상수로 고정하고 화면에 적는다

최소 집합은 **여럿일 수 있다.** 여러 개 중 하나를 골라 보여주는 순간, 어느 걸 골랐는지는
출력만 봐서 감사할 수 없다 — 반사실 설명의 체리피킹은 이론적으로 탐지 불가능하다는 증명이
있다. **규칙 공개가 유일한 방어**이므로 `SEARCH_RULE`을 화면 하단에 그대로 띄운다.

> **명세와 한 곳이 다르다.** `step12.md`는 항목이 많을 때 「배점 내림차순 그리디」라고
> 적었는데, 여기서는 **여유분(만점 − 현재 점수) 내림차순**으로 담는다. 배점이 커도 이미
> 만점이면 올릴 여지가 없어 「배점 순」은 0점짜리 항목을 먼저 집는다. 바꾼 사실과 이유를
> 여기 적어 두고, 규칙 자체는 `SEARCH_RULE`로 공개한다.

## 게이트 항목은 최소 편집에서 뺀다

게이트에서 탈락한 지원자에게 **「이것만 있으면 됩니다」는 거짓말**이다. 면허가 없으면
다른 항목을 아무리 올려도 그 일을 못 한다. 그래서 게이트 항목은 편집 후보에서 빼고
`gate_criteria`에 따로 담아 **별도 문단**으로 적는다.

## 지원자에게 보여주지 않는다

최소 편집은 **이력서 gaming 유인**이 된다 — 「이 항목만 채우면 등수가 오른다」를 알려주는
것이기 때문이다. 담당자 화면 전용으로 묶는다 (`step12.md` 금지사항).
"""

from __future__ import annotations

from itertools import combinations
from math import fsum

from pydantic import BaseModel, ConfigDict

from .aggregate import CandidateResult

GATE_LAYER = "gate"

# 완전 탐색을 쓸 항목 수 상한. 넘으면 그리디로 내려간다.
# 실측 공고는 kt-b2c 11개 · nexon-game 19개라 **두 공고 다 완전 탐색 경로**를 탄다.
EXHAUSTIVE_LIMIT = 20

# 편집 집합이 이보다 크면 나열하지 않고 「구조적 미달」로 표시한다.
# 근거: 격차가 크면 「이것만 있었으면」이라는 문장이 무의미해진다 (`docs/EVIDENCE_IDEAS.md`).
# **임의값이다** — 3이라는 숫자에 근거는 없다.
MAX_LISTED_EDITS = 3

# 동점권 기본 폭. **임의값.** 만점 100 기준 1점이고, 근거는 「LLM 리더보드에서 1% 미만의
# 조작으로 1위가 바뀐다」는 보고 하나뿐이다. 호출부가 명시적으로 넘기는 것을 기본으로 둔다.
DEFAULT_TIE_EPSILON = 1.0

# 점수 비교 자릿수. `rank.py`의 `TIE_DIGITS`와 같아야 한다 —
# 다르면 「랭킹이 본 동점」과 「여기가 본 동점」이 갈린다.
COMPARE_DIGITS = 6

# **화면 하단에 이 문자열을 그대로 띄운다.** 고르는 규칙을 감추면 체리피킹을 감사할 수 없다.
SEARCH_RULE = (
    "최소 편집 탐색 규칙 — "
    f"① 게이트 항목은 후보에서 제외한다. "
    f"② 항목이 {EXHAUSTIVE_LIMIT}개 이하면 완전 탐색: 크기 1부터 키우며 바로 위 순위를 "
    "넘기는 집합을 찾고, 같은 크기가 여럿이면 (필요 상승폭 합이 작은 것 → 항목 번호 "
    "오름차순) 순으로 하나를 고른다. "
    f"③ {EXHAUSTIVE_LIMIT}개를 넘으면 여유분(만점 − 현재 점수) 내림차순으로 담는 그리디. "
    f"④ 고른 집합이 {MAX_LISTED_EDITS}개를 넘거나 전 항목을 만점으로 올려도 못 넘으면 "
    "나열하지 않고 「구조적 미달」로 표시한다."
)


class RankFlip(BaseModel):
    """지원자 한 명이 **바로 위 순위를 넘어서는 데 필요한 최소 조건**.

    `minimal_set`이 비어 있는 경우가 셋이다 — 1위여서 넘을 상대가 없거나,
    게이트 탈락자라 순위 자체가 없거나, `structural=True`(집합이 너무 커서 나열하지 않음)다.
    셋을 `structural`·`gate_blocked`로 구분한다. 빈 목록 하나로 뭉뚱그리면
    「올릴 것이 없다」와 「올려도 안 된다」가 화면에서 같아 보인다.
    """

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    target_rank: int | None  # 넘어서려는 순위. 1위·탈락자는 None
    target_candidate_id: str | None
    minimal_set: list[str]  # criterion_id 목록
    gap: float  # 현재 점수 격차 (위 순위 − 나)
    needed: float  # 그 집합을 만점으로 올렸을 때 실제 상승폭
    structural: bool  # True면 편집 집합이 너무 크거나 불가능해서 나열하지 않았다
    gate_blocked: bool  # 게이트 탈락자. 최소 편집을 말하지 않는다
    gate_criteria: list[str]  # 탈락한 게이트 항목. **별도 문단용**
    search_rule: str = SEARCH_RULE


def _ranked(results: list[CandidateResult]) -> list[CandidateResult]:
    """순위가 매겨진 지원자만, 순위 순으로. 게이트 탈락자는 빠진다."""
    return sorted(
        (item for item in results if item.rank is not None),
        key=lambda item: item.rank,  # type: ignore[arg-type,return-value]
    )


def _headroom(result: CandidateResult) -> dict[str, float]:
    """항목별 **올릴 수 있는 여유분**. 게이트 항목과 이미 만점인 항목은 뺀다."""
    room: dict[str, float] = {}
    for axis in result.breakdown:
        if axis.layer == GATE_LAYER:
            continue
        value = round(axis.max_weighted - axis.weighted, COMPARE_DIGITS)
        if value > 0:
            room[axis.criterion_id] = value
    return room


def _exhaustive(room: dict[str, float], gap: float) -> list[str] | None:
    """완전 탐색. 크기 1부터 키우며 처음 걸리는 것을 준다.

    같은 크기가 여럿일 때의 순서가 규칙이다 — **필요 상승폭 합이 작은 것**을 먼저 보고,
    그래도 같으면 항목 번호 오름차순이다. 「가장 적게 바뀌면 되는 편집」이라는 뜻을
    크기 다음에 한 겹 더 준 것이고, 마지막 자리는 재현 가능성을 위해 임의로 고정했다.
    """
    keys = sorted(room)
    for size in range(1, len(keys) + 1):
        candidates = [
            (round(fsum(room[key] for key in subset), COMPARE_DIGITS), list(subset))
            for subset in combinations(keys, size)
        ]
        reachable = [item for item in candidates if item[0] > gap]
        if reachable:
            reachable.sort(key=lambda item: (item[0], item[1]))
            return reachable[0][1]
    return None


def _greedy(room: dict[str, float], gap: float) -> list[str] | None:
    """여유분 내림차순으로 담는다. 항목이 `EXHAUSTIVE_LIMIT`을 넘을 때만 쓴다.

    **최소를 보장하지 않는다.** 보장하지 않는다는 사실이 `SEARCH_RULE`에 적혀 화면에
    나가므로, 읽는 사람이 「이게 유일한 답」으로 오해하지 않는다.
    """
    order = sorted(room, key=lambda key: (-room[key], key))
    picked: list[str] = []
    total = 0.0
    for key in order:
        picked.append(key)
        total = round(total + room[key], COMPARE_DIGITS)
        if total > gap:
            return sorted(picked)
    return None


def minimal_flip(results: list[CandidateResult], candidate_id: str) -> RankFlip:
    """`candidate_id`가 **바로 위 순위**를 넘어서는 데 필요한 최소 항목 집합.

    「넘어선다」는 **총점이 위 순위보다 커진다**는 뜻이다. 같아지는 것으로는 부족하다 —
    동점은 `rank.py`가 판단 층 → `candidate_id` 순으로 가르므로 순위가 바뀐다는 보장이
    없고, 보장 없는 것을 「이러면 올라갑니다」라고 쓸 수 없다.
    """
    by_id = {item.candidate_id: item for item in results}
    if candidate_id not in by_id:
        raise KeyError(f"{candidate_id}: 결과 목록에 없는 지원자다")
    me = by_id[candidate_id]

    empty = {
        "candidate_id": candidate_id,
        "minimal_set": [],
        "gap": 0.0,
        "needed": 0.0,
        "structural": False,
        "gate_criteria": list(me.gate.failed_criteria),
    }

    if me.rank is None:
        # 게이트 탈락자. **최소 편집을 말하지 않는다** — 「이것만 있으면 됩니다」가 거짓말이
        # 되는 유일한 경우다. 탈락 항목만 넘겨 별도 문단으로 적게 한다.
        return RankFlip(target_rank=None, target_candidate_id=None, gate_blocked=True, **empty)

    ordered = _ranked(results)
    above = next((item for item in ordered if item.rank == me.rank - 1), None)
    if above is None:
        return RankFlip(target_rank=None, target_candidate_id=None, gate_blocked=False, **empty)

    gap = round(above.total - me.total, COMPARE_DIGITS)
    room = _headroom(me)
    base = {
        "candidate_id": candidate_id,
        "target_rank": above.rank,
        "target_candidate_id": above.candidate_id,
        "gap": gap,
        "gate_blocked": False,
        "gate_criteria": list(me.gate.failed_criteria),
    }

    if round(fsum(room.values()), COMPARE_DIGITS) <= gap:
        # 전 항목을 만점으로 올려도 못 넘는다. 「구조적 미달」이고, 그 사실을 숨기지 않는다.
        return RankFlip(minimal_set=[], needed=0.0, structural=True, **base)

    search = _exhaustive if len(room) <= EXHAUSTIVE_LIMIT else _greedy
    picked = search(room, gap)
    if picked is None or len(picked) > MAX_LISTED_EDITS:
        return RankFlip(minimal_set=[], needed=0.0, structural=True, **base)

    needed = round(fsum(room[key] for key in picked), COMPARE_DIGITS)
    return RankFlip(minimal_set=picked, needed=needed, structural=False, **base)


def tie_bands(
    results: list[CandidateResult], epsilon: float = DEFAULT_TIE_EPSILON
) -> list[list[str]]:
    """점수 차가 `epsilon` 미만인 지원자를 같은 밴드로 묶는다.

    **정렬을 바꾸지 않는다.** 순위는 그대로 두고 화면에 배지만 붙인다 — 0.4점 차이로
    1위와 2위를 확정하는 것이 거짓 정밀도이지, 순서를 지우는 것이 답은 아니다.

    묶는 기준은 **밴드의 첫 사람과의 차**다. 바로 앞사람과 비교해 사슬처럼 이으면
    A−B < ε, B−C < ε인데 A−C > ε일 때 셋이 한 밴드가 된다 — A와 C는 실제로 구별되는데
    「구분 불가」라고 적는 셈이다. 공고당 6명뿐이라 그 사슬이 전원을 삼키는 일이 실제로
    일어날 수 있다.

    게이트 탈락자는 밴드에 넣지 않는다. 순위가 없으므로 「구분 불가」를 말할 자리가 없다.
    """
    ordered = _ranked(results)
    if not ordered:
        return []
    if epsilon <= 0:
        return [[item.candidate_id] for item in ordered]

    bands: list[list[str]] = []
    leader: CandidateResult | None = None
    for item in ordered:
        if leader is None or round(leader.total - item.total, COMPARE_DIGITS) >= epsilon:
            bands.append([item.candidate_id])
            leader = item
        else:
            bands[-1].append(item.candidate_id)
    return bands


def render_flip(flip: RankFlip, labels: dict[str, str] | None = None) -> str:
    """최소 편집을 **사람이 읽는 한 문장**으로. 담당자 화면 전용이다.

    `labels`는 `{criterion_id: 항목 이름}`. 안 주면 항목 번호를 그대로 쓴다.
    """
    names = labels or {}

    if flip.gate_blocked:
        listed = ", ".join(names.get(item, item) for item in flip.gate_criteria) or "해당 항목"
        return (
            f"{flip.candidate_id}는 게이트({listed})에서 분리되어 순위가 없습니다. "
            "다른 항목을 올려서 넘을 수 있는 상태가 아닙니다."
        )
    if flip.target_rank is None:
        return f"{flip.candidate_id} 위에 비교할 순위가 없습니다."
    if flip.structural:
        return (
            f"{flip.candidate_id}는 {flip.target_rank}위와 {flip.gap:.1f}점 차입니다. "
            f"항목 {MAX_LISTED_EDITS}개 안으로는 넘어설 수 없어 개별 항목으로 적지 "
            "않습니다(구조적 미달)."
        )

    listed = " · ".join(f"「{names.get(item, item)}」" for item in flip.minimal_set)
    return (
        f"{flip.candidate_id}는 {listed}에서 만점을 받았다면 "
        f"{flip.target_rank}위({flip.target_candidate_id})를 넘어섰습니다 "
        f"(현재 격차 {flip.gap:.1f}점, 그 항목들의 남은 배점 합 {flip.needed:.1f}점)."
    )
