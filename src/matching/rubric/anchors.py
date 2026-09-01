"""기준점 패턴 — **루브릭에서 유일하게 고정된 것.**

항목 목록을 고정하면 과제 CRITICAL(「특정 스킬셋·직군을 하드코딩하지 않는다」)을 정면으로
위반한다 (`docs/TRADEOFFS.md` C-1). 그래서 고정하는 것은 **기준점의 패턴**뿐이고,
항목 자체는 파싱된 공고 조건에서 나온다.

**패턴 문구에 직군 어휘가 하나도 없는 것이 일반화의 핵심이다.** 여기서 재는 것은
「무엇에 대한 경험인가」가 아니라 **「얼마나 구체적으로 썼는가」**다. 직군 정보는
기준점 문장이 아니라 `「조건」` 자리로만 들어온다.

2점·4점에는 기준점을 두지 않는다 — "1과 3 사이" / "3과 5 사이"로 남긴다.
5개를 어중간하게 쓰는 것보다 **3개를 잘 쓰는 쪽**이 낫다는 판단이다
(`docs/TRADEOFFS.md` B-3: 같은 뜻으로 문구만 바꿔도 점수가 6점 만점에 1.2점 이동했다).
"""

from __future__ import annotations

from ..model.objects import Requirement, RequirementBranch

ANCHOR_TEMPLATE: dict[int, str] = {
    1: "관련 경험이 없거나, 있어도 구체적 행동 서술이 없음",
    3: "제시하나 서술이 추상적이고 본인 역할·성과가 불명확함",
    5: "본인의 역할 · 취한 행동 · 달성한 성과가 명확히 서술됨",
}

# 충족형 기준점 — **예/아니오인데 이력서가 다른 말로 적는 조건**에 붙는다
# (`rubric/branch.py`의 `binary`).
#
# 위 패턴을 그대로 쓰면 **구조적으로 만점이 안 나온다.** 「행동·성과가 명확히 서술됨」을
# 요구하는데, 충족 여부만 묻는 조건에는 애초에 서술할 행동도 성과도 없다. 실측에서
# 그런 항목이 15점 만점에 2.5점을 받았고, 그건 지원자가 못 갖춘 것이 아니라
# **자가 잘못 놓인 것**이다.
#
# 그래서 여기서는 **무엇을 얼마나 잘했는가를 묻지 않는다.** 묻는 것은 「충족한다는 것이
# 이력서에서 확인되는가」뿐이고, 확신의 정도가 1·3·5로 갈린다. 인용 규칙은 그대로다 —
# 근거 없는 점수를 만들지 않는 것이 이 프로젝트의 핵심이라 완화 대상이 아니다.
SATISFACTION_TEMPLATE: dict[int, str] = {
    1: "충족한다고 볼 근거가 이력서에 없거나, 충족하지 않는다고 적혀 있음",
    3: "충족하는 것으로 보이나 이력서만으로 단정할 수 없음",
    5: "충족한다는 것이 이력서에 분명히 드러남 (표현이 조건과 달라도 됨)",
}

# 갈래마다 어느 패턴을 쓰는가. `term`은 사실 층이라 기준점이 화면 표시용으로만 남지만,
# 층이 뒤집혀도(설정·승인) 문장이 깨지지 않도록 자리를 비워 두지 않는다.
ANCHOR_TEMPLATES: dict[RequirementBranch, dict[int, str]] = {
    "term": ANCHOR_TEMPLATE,
    "graded": ANCHOR_TEMPLATE,
    "binary": SATISFACTION_TEMPLATE,
}

# 기준점이 붙는 수준. 2·4는 일부러 비운다 (위 docstring).
ANCHOR_LEVELS: tuple[int, ...] = tuple(sorted(ANCHOR_TEMPLATE))


def make_anchors(
    requirement: Requirement, branch: RequirementBranch = "graded"
) -> dict[int, str]:
    """조건 문구를 앞에 붙여 항목별 기준점을 만든다.

    `「<조건>」 — <패턴 문구>` 형태다. **템플릿 문장 안에 끼워 넣지 않는다.**

    안에 끼워 넣으면(`"{req}을(를) 제시하나…"`) 한국어 조사를 코드가 골라야 한다.
    받침 유무로 을/를·이/가가 갈리고, 영문 이름이 섞이면 규칙이 더 깨진다. 조사 처리는
    이 과제의 문제가 아니다 — **앞에 붙이는 형태면 조사가 아예 안 생긴다.**

    `branch`가 고르는 것은 **패턴이지 항목이 아니다.** 패턴은 둘 다 코드에 고정돼 있고
    직군 어휘가 한 글자도 없다 — 일반화가 깨지는 자리가 아니다.
    """
    template = ANCHOR_TEMPLATES.get(branch, ANCHOR_TEMPLATE)
    return {level: f"「{requirement.text}」 — {text}" for level, text in template.items()}
