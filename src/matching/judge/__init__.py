"""2층 판단 채점 — **파이프라인에서 유일하게 비결정적인 모듈이다.**

`src/CLAUDE.md`의 모듈 경계: 「judge — 심사위원 호출·집계. 여기만 비결정적」.
사실 확인(연차·보유 여부)은 `scorer/`가 이미 코드로 셌고, 여기서는 **서술의 구체성과
직무 관련성**만 본다.

- `schema` — 출력 계약. 필드 순서가 곧 생성 순서다 (인용 → 근거 → 점수)
- `prompt` — 항목 하나·지원자 한 명짜리 프롬프트. 직군 어휘가 없다.
  **지시문과 예시는 항목의 갈래를 따라 갈린다** — 충족형(`binary`)과 서술형(`graded`)
- `panel` — 2명 독립 호출, 이견 2점 이상이면 3번째, 산술평균. 비용 상한도 여기
- `bias` — 순서 편향 점검. 기본 실행에서 꺼져 있다
"""

from .bias import OrderCheckResult, order_check
from .panel import (
    JUDGE_IDS,
    JUDGE_TEMPERATURE,
    BudgetExceeded,
    CallBudget,
    JudgeError,
    NoGroundedResponse,
    UsageReport,
    judge_criterion,
    keep_quotes,
)
from .prompt import (
    EXAMPLES_BY_BRANCH,
    GENERIC_EXAMPLES,
    MAX_QUOTES,
    SATISFACTION_EXAMPLES,
    SYSTEM_BY_BRANCH,
    ScoringExample,
    build_prompt,
    examples_for,
    prompt_sha256,
    system_for,
)
from .schema import (
    RESPONSE_FORMAT,
    RESPONSE_SCHEMA,
    SCALE_MAX,
    SCALE_MIN,
    JudgeCall,
    JudgeOutput,
    QuoteRef,
)

__all__ = [
    "EXAMPLES_BY_BRANCH",
    "GENERIC_EXAMPLES",
    "JUDGE_IDS",
    "JUDGE_TEMPERATURE",
    "MAX_QUOTES",
    "RESPONSE_FORMAT",
    "RESPONSE_SCHEMA",
    "SATISFACTION_EXAMPLES",
    "SCALE_MAX",
    "SCALE_MIN",
    "SYSTEM_BY_BRANCH",
    "BudgetExceeded",
    "CallBudget",
    "JudgeCall",
    "JudgeError",
    "JudgeOutput",
    "NoGroundedResponse",
    "OrderCheckResult",
    "QuoteRef",
    "ScoringExample",
    "UsageReport",
    "build_prompt",
    "examples_for",
    "judge_criterion",
    "keep_quotes",
    "order_check",
    "prompt_sha256",
    "system_for",
]
