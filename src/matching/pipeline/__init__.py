"""파이프라인 — 층별 점수를 0~100점 하나로 합치고, 랭킹을 만들고, **검산을 통과시킨다.**

- `aggregate` — 층별 정규화·가중합산. 판단 층은 `(raw-1)/4`다 (**`raw/5`가 아니다**)
- `rank` — 게이트 탈락자 분리 · 동점은 판단 층 → `candidate_id` 순으로 **재현 가능하게**
- `contrast` — 「왜 저 사람이 아니라 이 사람인가」. 등수 뒤집기 최소 편집 · 동점권.
  **LLM 호출 0회**, 전부 산술이다 (step 12-A)
- `run` — `prepare()` ⛔승인게이트 `score()`. 검산이 집계 **앞**에 있다
- `explain` — 사람이 읽는 결과. 과제 요구 ③의 뒤쪽 절반

**LLM에게 최종 점수나 랭킹을 계산시키지 않는다.** 이 층은 전부 산술이다.
"""

from .aggregate import AggregateError, AxisScore, CandidateResult, aggregate, layer_max, layer_total
from .contrast import SEARCH_RULE, RankFlip, minimal_flip, render_flip, tie_bands
from .explain import explain
from .rank import rank
from .run import (
    ApprovalRequired,
    ApprovalStale,
    RubricProposal,
    RunResult,
    load_run,
    prepare,
    save_run,
    score,
)

__all__ = [
    "SEARCH_RULE",
    "AggregateError",
    "ApprovalRequired",
    "ApprovalStale",
    "AxisScore",
    "CandidateResult",
    "RankFlip",
    "RubricProposal",
    "RunResult",
    "aggregate",
    "explain",
    "layer_max",
    "layer_total",
    "load_run",
    "minimal_flip",
    "prepare",
    "rank",
    "render_flip",
    "save_run",
    "score",
    "tie_bands",
]
