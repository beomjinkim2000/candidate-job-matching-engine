"""랭킹 — **결정적이다. 같은 입력에 같은 순서.**

## 게이트 탈락자를 지우지 않는다

탈락자는 `rank=None`으로 **목록 끝에 붙는다.** 사유(`GateResult.reasons`)를 달고 나가므로
「왜 안 보이는가」를 화면에서 답할 수 있다. 목록에서 빼면 게이트가 틀렸을 때 그 사실을
확인할 방법이 사라진다 — 게이트가 틀리는 방향 중 가장 비싼 쪽이다 (`scorer/gate.py`).

## 동점을 무작위로 가르지 않는다

무작위는 재현 불가능하고, 재현 불가능한 순위는 「어제 3등이던 사람이 오늘 4등인 이유」에
답할 수 없다. 그래서 두 단계로 가른다.

1. **판단 층 점수가 높은 쪽이 위.** 타당도가 높은 축을 우선한다 (`docs/RUBRIC_DESIGN.md` —
   세는 방식 r≈0.15 / 판단 방식 r≈0.48). 총점이 같다면 그 점수가 어느 축에서 왔는지가
   유일하게 남은 정보다
2. 그래도 같으면 **`candidate_id` 오름차순.** 임의지만 **재현 가능하다.**
   임의라는 사실을 숨기지 않는다 — 2번까지 온 동점은 「우리 자로는 구별되지 않는다」가
   정답이고, 그때 순서를 지어내는 대신 항상 같은 순서를 내는 쪽을 골랐다

## 부동소수점 비교

`total`은 `aggregate()`가 소수점 6자리로 반올림해 둔 값이다. 여기서 다시 반올림해
비교하는 이유: `88.400000000000006`과 `88.4`가 갈리면 **정렬 순서가 계산 순서에 달린다.**
"""

from __future__ import annotations

from .aggregate import JUDGMENT_LAYER, CandidateResult, layer_total

# 동점으로 볼 자릿수. `aggregate()`의 반올림 자릿수와 같아야 한다.
TIE_DIGITS = 6


def _sort_key(result: CandidateResult) -> tuple[float, float, str]:
    return (
        -round(result.total, TIE_DIGITS),
        -round(layer_total(result, JUDGMENT_LAYER), TIE_DIGITS),
        result.candidate_id,
    )


def rank(results: list[CandidateResult]) -> list[CandidateResult]:
    """총점 내림차순으로 순위를 매긴다. **원본을 고치지 않고 새 목록을 만든다.**

    게이트 통과자만 1위부터 번호를 받고, 탈락자는 `rank=None`으로 목록 끝에 붙는다.
    탈락자끼리는 `candidate_id` 순이다 — 순위가 없으므로 점수로 줄 세울 이유가 없고,
    출력이 실행마다 흔들리면 안 된다.
    """
    passed = sorted((r for r in results if r.gate.passed), key=_sort_key)
    failed = sorted((r for r in results if not r.gate.passed), key=lambda r: r.candidate_id)

    ordered = [
        result.model_copy(update={"rank": position})
        for position, result in enumerate(passed, start=1)
    ]
    ordered.extend(result.model_copy(update={"rank": None}) for result in failed)
    return ordered
