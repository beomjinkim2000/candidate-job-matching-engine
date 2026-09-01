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

from ..model.objects import Requirement

ANCHOR_TEMPLATE: dict[int, str] = {
    1: "관련 경험이 없거나, 있어도 구체적 행동 서술이 없음",
    3: "제시하나 서술이 추상적이고 본인 역할·성과가 불명확함",
    5: "본인의 역할 · 취한 행동 · 달성한 성과가 명확히 서술됨",
}

# 기준점이 붙는 수준. 2·4는 일부러 비운다 (위 docstring).
ANCHOR_LEVELS: tuple[int, ...] = tuple(sorted(ANCHOR_TEMPLATE))


def make_anchors(requirement: Requirement) -> dict[int, str]:
    """조건 문구를 앞에 붙여 항목별 기준점을 만든다.

    `「<조건>」 — <패턴 문구>` 형태다. **템플릿 문장 안에 끼워 넣지 않는다.**

    안에 끼워 넣으면(`"{req}을(를) 제시하나…"`) 한국어 조사를 코드가 골라야 한다.
    받침 유무로 을/를·이/가가 갈리고, 영문 이름이 섞이면 규칙이 더 깨진다. 조사 처리는
    이 과제의 문제가 아니다 — **앞에 붙이는 형태면 조사가 아예 안 생긴다.**
    """
    return {
        level: f"「{requirement.text}」 — {text}"
        for level, text in ANCHOR_TEMPLATE.items()
    }
