"""심사위원 출력 계약 — **필드 순서가 곧 생성 순서다.**

## 왜 인용을 삼중항으로 받나

인용을 자유 문장으로 받으면 지어냈는지 알 방법이 없다. `(start, end, text)`로 받으면
`resume[start:end] == text`인지를 **코드가 대조**할 수 있다. 검산 G2가 성립하는 이유가
여기다 — 모델의 성실성이 아니라 슬라이스 연산이 인용을 보증한다.

## 순서가 계약인 이유

`quotes → reasoning → score`. 언어모델은 앞에 쓴 것을 조건으로 뒤를 쓰므로, **점수를
먼저 내면 근거가 사후 정당화**가 된다. 그래서 필드 순서를 지시문으로만 부탁하지 않고
JSON 스키마의 `properties`·`required` 순서로 못 박는다 — 구조화 출력은 이 순서대로
생성한다.

## `score`에 범위를 건다

1~5 밖의 값이 오면 pydantic이 여기서 막고, `panel`이 그 응답을 **버린다**. 클램프하지
않는 이유: 6점을 5점으로 조용히 깎으면 「모델이 척도를 안 지켰다」는 사실이 사라진다.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# 5점 척도. `docs/TRADEOFFS.md` B-3 — 문헌은 7~10점이 낫다고 하지만, 기준점 문구를
# 잘 쓰는 쪽이 점수 개수보다 영향이 크다고 판단해 5점으로 갔다.
SCALE_MIN = 1
SCALE_MAX = 5
SCALE_VALUES: tuple[int, ...] = tuple(range(SCALE_MIN, SCALE_MAX + 1))


class QuoteRef(BaseModel):
    """이력서 원문의 한 구간. **오프셋이 본체이고 `text`는 대조용이다.**"""

    model_config = ConfigDict(extra="forbid")

    start: int  # 이력서 원문 문자 오프셋
    end: int
    text: str  # 그 구간의 실제 문자열이라고 모델이 주장하는 것


class JudgeOutput(BaseModel):
    """심사위원 한 명의 응답 한 건."""

    model_config = ConfigDict(extra="forbid")

    quotes: list[QuoteRef]  # 1. 먼저 이력서에서 인용
    reasoning: str  # 2. 그다음 판단 근거
    score: int = Field(ge=SCALE_MIN, le=SCALE_MAX)  # 3. 마지막에 점수


class JudgeCall(BaseModel):
    """호출 1건의 **재현 조건.** 결과 JSON만 보고 같은 조건을 다시 만들 수 있어야 한다.

    `model`은 `settings.judge_model`에서 온다 — 코드에 박지 않는다. 별칭(`latest` 계열)을
    쓰면 어제 결과와 오늘 결과가 다른 이유를 알 수 없으므로 **버전을 고정한 문자열**을
    설정에 넣는다.
    """

    model_config = ConfigDict(extra="forbid")

    model: str
    temperature: float  # 0.0 고정
    seed: int | None  # 지원되면 settings.judge_seed
    prompt_sha256: str  # 프롬프트 동일성 증명


# 구조화 출력 스키마. **`properties`의 순서가 생성 순서다** (위 docstring).
# `minimum`/`maximum` 대신 `enum`을 쓴다 — strict 모드가 받는 키워드가 제한적이다.
RESPONSE_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["quotes", "reasoning", "score"],
    "properties": {
        "quotes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["start", "end", "text"],
                "properties": {
                    "start": {"type": "integer"},
                    "end": {"type": "integer"},
                    "text": {"type": "string"},
                },
            },
        },
        "reasoning": {"type": "string"},
        "score": {"type": "integer", "enum": list(SCALE_VALUES)},
    },
}

RESPONSE_FORMAT: dict = {
    "type": "json_schema",
    "json_schema": {"name": "judge_score", "strict": True, "schema": RESPONSE_SCHEMA},
}
