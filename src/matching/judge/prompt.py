"""심사위원 프롬프트 — **항목 하나, 지원자 한 명.**

## 왜 한 번에 하나인가

분석적 채점이다 (`docs/TRADEOFFS.md` B-4). 여러 항목을 한 응답에 몰면 항목 사이에
후광이 생겨 「개별 기준의 적용 편차가 가려진다」. 속도는 두 배 가까이 느려지고 그만큼
토큰이 더 들지만, 과제 요구가 「점수의 근거를 사람이 읽을 수 있는 형태로」다.

**한 호출에 지원자도 한 명이다.** 그래서 「먼저 제시된 쪽을 선호하는 편향」이 들어올
자리가 프롬프트에 아예 없다 — `bias.order_check()`가 그 사실을 확인한다.

## 지시문에 직군 어휘가 없다

기준점은 `rubric/anchors.py`가 만든 것을 그대로 싣고, 채점 예시도 **직무 내용이 아니라
서술의 구체성**만 보이는 문장으로 썼다. 이 파일에 직군명·기술명이 들어가는 순간
「직군 무관 일반화」가 깨진다.

## 오프셋을 어떻게 주나

인용을 `(start, end, text)`로 받으려면 모델이 문자 위치를 셀 수 있어야 한다. 그래서
**줄마다 그 줄의 시작 오프셋을 앞에 붙여** 보낸다. 줄 하나를 통째로 인용하면
`start = 줄 머리 숫자`, `end = start + 줄 글자 수`로 계산이 끝난다.

이 표기는 프롬프트에만 있다 — 오프셋의 기준은 언제나 **표기를 붙이기 전의 이력서 원문**
이고, `panel`이 그 원문에서 다시 잘라 대조한다.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from ..model.objects import Criterion
from ..scorer.mask import MASK_CHAR
from .schema import SCALE_MAX, SCALE_MIN

# 한 응답에서 받을 인용의 상한. 근거는 **몇 개인가가 아니라 어디를 짚었는가**이고,
# 인용이 열 개면 화면이 인용으로 뒤덮여 오히려 안 읽힌다 (`scorer/fact.py`와 같은 이유).
MAX_QUOTES = 3


class ScoringExample(BaseModel):
    """채점 예시 한 건. **루브릭 단독으로는 부족하다** (`src/CLAUDE.md` 심사위원 운영).

    `excerpt`에 직무 내용을 쓰지 않는다 — 여기 든 예시가 곧 채점 기준이 되므로,
    특정 직군의 서술을 예시로 주면 그 직군이 유리해진다.
    """

    model_config = ConfigDict(extra="forbid")

    excerpt: str
    score: int = Field(ge=SCALE_MIN, le=SCALE_MAX)
    why: str


# 기본 예시 3건. **재는 것은 「무엇을 했는가」가 아니라 「얼마나 구체적으로 썼는가」**다.
# 그래서 문장에서 직무를 지우고 자리표시자로 뒀다 — 어느 직군의 이력서에도 같이 적용된다.
GENERIC_EXAMPLES: tuple[ScoringExample, ...] = (
    ScoringExample(
        excerpt="관련 업무를 담당한 경험이 있습니다.",
        score=1,
        why="맡았다는 말만 있고 무엇을 했는지·본인 몫이 무엇이었는지가 없다.",
    ),
    ScoringExample(
        excerpt="팀에서 개선 과제를 진행해 좋은 성과를 냈습니다.",
        score=3,
        why="과제를 제시하지만 본인이 취한 행동과 성과의 크기가 없다.",
    ),
    ScoringExample(
        excerpt=(
            "6개월간 매주 두 차례 직접 운영하며 처리 절차를 둘로 나눴고, "
            "그 결과 재작업 비율이 12%에서 4%로 줄었습니다."
        ),
        score=5,
        why="기간·본인이 한 행동·결과의 변화량이 모두 있다.",
    ),
)


_SYSTEM = (
    "너는 채용 서류를 채점하는 심사위원이다. **주어진 항목 하나만** 채점한다.\n"
    "\n"
    "1. **인용을 먼저 쓴다.** 판단의 근거가 된 이력서 구간을 quotes에 담고, 그다음\n"
    "   reasoning을 쓰고, score는 **맨 마지막**에 쓴다. 점수를 먼저 정하면 근거는\n"
    "   사후 정당화가 된다.\n"
    "2. **인용을 지어내지 않는다.** start·end는 이력서 원문의 문자 위치이고 text는 그\n"
    "   구간의 글자 그대로다. 지어낼 바에는 비우는 게 낫다.\n"
    "   **다만 낮은 점수에도 인용이 필요하다.** 1점은 「인용할 것이 없다」가 아니라\n"
    "   「인용한 그 서술이 구체적이지 않다」 또는 「그 항목과 맞닿지 않는다」는 판정이다.\n"
    "   그러니 **이 항목과 가장 가까운 자리**를 인용하고 왜 모자란지를 쓴다.\n"
    "   경험이 없다고 적힌 문장도 인용 대상이다. **정말로 스칠 문장조차 하나도 없을 때만**\n"
    "   quotes를 비우고, 그때 이 응답은 채점에 쓰이지 않는다.\n"
    f"3. 인용은 최대 {MAX_QUOTES}개까지만 담는다. 많이 담는다고 좋은 채점이 아니다.\n"
    "4. **응답 길이가 평가에 영향을 주지 않게 하라.** 이력서가 길다고 높은 점수가\n"
    "   아니고, 네 답을 길게 쓴다고 좋은 채점이 되는 것도 아니다.\n"
    "5. **사실 확인은 네 일이 아니다.** 몇 년 일했는지·무엇을 보유했는지는 이미 코드가\n"
    "   셌다. 너는 **서술이 얼마나 구체적인가**와 **그 서술이 이 항목과 얼마나 맞닿는가**\n"
    "   만 본다.\n"
    "6. **총점을 계산하지 마라.** 이 항목의 "
    f"{SCALE_MIN}~{SCALE_MAX}점만 낸다. 합산은 코드가 한다.\n"
    f"7. `{MASK_CHAR}`는 개인정보를 가린 자리다. **그 자리는 인용하지 않는다.**\n"
)


def with_offsets(text: str) -> str:
    """줄마다 시작 오프셋을 앞에 붙인다. **프롬프트에만 있는 표기다.**"""
    lines: list[str] = []
    offset = 0
    for line in text.split("\n"):
        lines.append(f"[{offset}] {line}")
        offset += len(line) + 1  # 줄바꿈 한 글자
    return "\n".join(lines)


def build_prompt(
    criterion: Criterion,
    masked_resume: str,
    examples: Sequence[ScoringExample],
) -> list[dict]:
    """심사위원 한 명에게 보낼 메시지. **항목 하나만 들어간다.**

    `masked_resume`는 이미 마스킹된 글이어야 한다 — 마스킹 전 글을 넣으면 이름·나이·
    학교가 심사위원에게 그대로 간다. 마스킹을 거는 자리는 `panel.judge_criterion()`이다.
    """
    anchors = "\n".join(
        f"{level}점 — {criterion.anchors[level]}" for level in sorted(criterion.anchors)
    )
    shown = "\n".join(
        f"- {example.score}점: 「{example.excerpt}」 → {example.why}" for example in examples
    )

    body = [
        "[채점할 항목]",
        f"{criterion.id} · {criterion.label}",
        "",
        "[점수 기준점]",
        anchors,
        "기준점이 없는 점수는 사이값이다 — 1과 3 사이면 2점, 3과 5 사이면 4점.",
        "",
        "[채점 예시]",
        shown or "- (없음)",
        "",
        "[이력서]",
        "각 줄 앞의 [n]은 그 줄의 첫 글자가 이력서에서 몇 번째 글자인지다 (0부터 센다).",
        "줄 하나를 통째로 인용하면 start는 그 [n], end는 start + 그 줄의 글자 수다.",
        "",
        with_offsets(masked_resume),
        "",
        "이 항목 하나만 채점한다. quotes → reasoning → score 순서로 답한다.",
    ]

    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": "\n".join(body)},
    ]


def prompt_sha256(messages: list[dict]) -> str:
    """프롬프트 동일성 증명. **지시문을 고치면 값이 바뀐다.**

    `JudgeCall`에 실려 결과 JSON에 남는다 — 「같은 조건에서 쟀다」를 나중에 확인할 수
    있는 유일한 값이다.
    """
    payload = json.dumps(messages, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
