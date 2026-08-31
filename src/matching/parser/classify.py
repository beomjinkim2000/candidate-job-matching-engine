"""필수/우대 판정 사다리 — **코드가 판정한다. LLM에게 묻지 않는다.**

`src/CLAUDE.md`의 순서 그대로. **위에서부터, 결론이 나면 멈춘다.**

| 단계 | 조건 | 결과 | 근거 등급 |
|---|---|---|---|
| 1 | 소속 블록의 `header_role`이 `requirement`/`preferred` | 확정 | `E2` |
| 2 | 항목 텍스트에 강도 수식어가 있음 | 확정 | `E2` |
| 3 | `duty` 블록에 대응하는 일이 있음 | required 쪽 | `E1` |
| 4 | 2회 이상 반복 등장 | required 쪽 | `E1` |
| 5 | 시각 강조 | **판정을 바꾸지 않고** 등급만 | `E0` |

**5단계는 현재 경로에서 발동하지 않는다.** OCR이 굵기도 색도 주지 않기 때문이다
(`docs/OCR_EVIDENCE.md` §2). 자리는 남기되 `emphasized`는 항상 `False`이고,
그 사실을 `ParseReport.emphasis_available=False`로 결과에 적는다.
**「구현했는데 안 쓴다」가 아니라 「입력이 없다」**이고, 둘은 다르다.

**1~2단계에서 결론이 안 나면 기본값은 `preferred`.** 이유: 필수로 잘못 분류하면 게이트나
큰 감점으로 이어져 등수가 크게 흔들린다. 반대 방향의 오류가 덜 해롭다.

**등급을 점수에 곱하지 않는다** (`src/CLAUDE.md`). 곱하면 점수가 낮은 이유가 적합도
때문인지 근거 부족 때문인지 구분이 사라진다.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict

from ..model.objects import EvidenceGrade, Requirement, RequirementKind
from .ocr import OcrLine

# 강도 수식어. **직군 어휘가 아니라 요구 강도 어휘**다 — 어느 직군 공고에도 같은 말로
# 나온다. 목록을 길게 늘리지 않는다: 길어질수록 「어느 표현이 왜 들어갔나」가 흐려지고,
# 섹션 제목 사전과 같은 실패(목록 밖 표현에 조용히 실패)를 이 자리에서 반복하게 된다.
REQUIRED_MARKERS: tuple[str, ...] = ("필수", "반드시", "필히")
PREFERRED_MARKERS: tuple[str, ...] = ("우대", "있으면", "더욱 좋", "가산")

# 3단계 — 담당업무와 몇 개나 겹쳐야 「대응한다」고 보나. **임의값이다.**
# 1이면 흔한 낱말 하나로 붙어버리고, 3이면 문장이 거의 같아야 한다.
DUTY_OVERLAP_MIN = 2

# 토큰의 앞 n글자만 비교한다. 한국어는 조사가 붙어 완전 일치가 거의 안 난다 —
# 「개발이」와 「개발을」이 다른 토큰이 되면 이 단계가 영원히 발동하지 않는다.
_TOKEN_HEAD = 2

_PUNCT = re.compile(r"[^0-9A-Za-z가-힣+#]+")


class ParsedItem(BaseModel):
    """블록에서 잘라낸 항목 하나. 사다리의 입력이다."""

    model_config = ConfigDict(extra="forbid")

    text: str
    lines: list[OcrLine]
    header_role: str | None
    # OCR이 굵기·색을 안 주므로 **항상 False**다. 자리만 남긴다.
    emphasized: bool = False


class RequirementRecord(Requirement):
    """`requirements.json`에 실리는 형태 — `Requirement`에 **OCR 줄 역참조**를 더한 것.

    **`line_ids`를 `model/objects.py`의 `Requirement`에 넣지 않았다.** 그건 파이프라인
    전체가 주고받는 계약이고, 줄 id는 파서 안에서만 뜻이 있다 (루브릭·채점은 줄을 모른다).
    계약에 파서 사정을 섞으면 다음 단계들이 쓰지도 않는 필드를 들고 다니게 된다.

    대신 **여기서 상속으로 붙인다** — `RequirementRecord`는 그대로 `Requirement`라
    그래프에도 검산에도 그냥 들어간다. 그러면서 `requirements.json`에는 역참조가 남아,
    조건 하나하나가 `ocr.json`의 어느 줄에서 나왔는지 대조할 수 있다 (G1 검산의 두 번째 파일).
    """

    line_ids: list[str]


class PostingContext(BaseModel):
    """공고 전체를 봐야 알 수 있는 것 — 3·4단계가 쓴다."""

    model_config = ConfigDict(extra="forbid")

    duty_texts: list[str] = []
    occurrences: dict[str, int] = {}


def normalize(text: str) -> str:
    """반복 판정용 정규화. 불릿·공백·구두점을 지운다 —
    같은 조건이 다른 불릿으로 두 번 적힌 것을 다른 조건으로 세면 4단계가 죽는다.
    """
    return _PUNCT.sub("", text)


def _tokens(text: str) -> set[str]:
    """비교용 토큰. 2글자 이상 낱말의 **앞 2글자**만 남긴다 (조사 흡수)."""
    words = _PUNCT.sub(" ", text).split()
    return {word[:_TOKEN_HEAD] for word in words if len(word) >= _TOKEN_HEAD}


def corresponds_to_duty(text: str, duty_texts: list[str]) -> bool:
    """3단계 — 담당업무에 대응하는 일이 있는가.

    **의미 판정을 LLM에게 넘기지 않는다.** 여기서 물으면 「세는 것은 코드가, 판단하는
    것은 심사위원이」가 무너지고, 같은 질문에 판정이 흔들린다 (`src/CLAUDE.md`).
    낱말 겹침은 둔한 신호지만 **결정적**이고, 이 단계의 결과는 `E1`(추론)로 표시된다.
    """
    mine = _tokens(text)
    if not mine:
        return False
    return any(len(mine & _tokens(duty)) >= DUTY_OVERLAP_MIN for duty in duty_texts)


def classify(
    item: ParsedItem, context: PostingContext
) -> tuple[RequirementKind, EvidenceGrade, int]:
    """returns (kind, evidence_grade, ladder_step)"""
    # --- 1단계. 소속 섹션의 역할 — 가장 신뢰도 높다 ------------------------
    if item.header_role == "requirement":
        return "required", "E2", 1
    if item.header_role == "preferred":
        return "preferred", "E2", 1

    # --- 2단계. 항목 안의 강도 수식어 --------------------------------------
    if any(marker in item.text for marker in REQUIRED_MARKERS):
        return "required", "E2", 2
    if any(marker in item.text for marker in PREFERRED_MARKERS):
        return "preferred", "E2", 2

    # --- 3단계. 담당업무에 대응하는 일이 있는가 -----------------------------
    if corresponds_to_duty(item.text, context.duty_texts):
        return "required", "E1", 3

    # --- 4단계. 2회 이상 반복 ----------------------------------------------
    if context.occurrences.get(normalize(item.text), 0) >= 2:
        return "required", "E1", 4

    # --- 5단계. 시각 강조 — 판정을 **바꾸지 않는다** ------------------------
    # 강조는 디자인 관행이지 요구 강도가 아니다. 그리고 OCR이 굵기를 안 주므로
    # `emphasized`는 항상 False다. 두 사실이 겹쳐 이 분기는 죽어 있다.
    return "preferred", "E0", 5
