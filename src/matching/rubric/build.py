"""`Requirement` 목록 → `Criterion` 목록. **공고마다 다른 루브릭이 여기서 만들어진다.**

## 층 배정

| 조건 | 가는 층 | 이유 |
|---|---|---|
| `settings.gate_kinds`에 걸림 | `gate` | 없으면 그 일을 법적으로 못 한다 |
| 갈래가 `term` | `fact` | 이름이 있어 코드가 센다 |
| 갈래가 `binary`·`graded` | `judgment` | 낱말 대조로는 확인되지 않는다 |
| `kind == "duty"` | `judgment` **고정** | 아래 |

**갈래는 `rubric/branch.py`가 정한다** — 조건 문구만 LLM에 보내 `term`/`binary`/`graded`로
받고, 못 부르면 옛 `is_countable()`로 떨어진다. 세 갈래로 나눈 이유와 두 갈래가 틀린
지점은 그 파일의 docstring에 있다.

**분류를 직군 어휘로 하지 않는다.** 판단 기준은 *「이 조건의 충족 여부를 어떻게
확인하는가」*이지 *「이게 어느 직군의 기술인가」*가 아니다. 그래서 이 파일에는 직군명도
기술명도 없고, `is_countable()`이 보는 것은 **문자의 종류**뿐이다.

## 담당업무는 조건이 아니지만 버리지도 않는다

`kind == "duty"`는 **게이트와 사실 채점에 절대 들어가지 않는다.** 담당업무는 입사 후 할
일이지 지원자가 갖춰야 할 자격이 아니다. 조건으로 세면 **그 일을 이미 해본 사람만 점수를
받는데, 확보한 두 공고 다 신입·인턴 공고다** — 대상 자체가 뒤집힌다.

그렇다고 버리면 안 된다. 실측(`data/postings/kt-b2c/requirements.json`): 조건 6건 중
**4건이 졸업·병역·해외여행·입사가능일**로 지원자 전원이 통과하는 형식 요건이고, 남는
변별력은 우대 2건뿐이다. **담당업무 5건을 빼면 그 공고는 직군을 구별하지 못한다.**
자격 요건이 형식적인 공고에서 담당업무는 유일한 직무 신호다.

그래서 담당업무는 **판단 축이 「무엇에 대한 관련성인가」를 재는 자**로 들어간다.
기준점은 `make_anchors`를 그대로 쓰고, `derived_from` Link도 똑같이 건다 — 담당업무도
`source_bbox`를 들고 있으므로 판단 점수의 근거 사슬이 이미지 좌표까지 이어진다.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from math import fsum

from ..config import Settings
from ..model.graph import EvidenceGraph
from ..model.objects import Criterion, Requirement, RequirementBranch, ScoreLayer
from .anchors import make_anchors

# 총점. 과제 요구가 「0~100점」이다.
TOTAL_POINTS = 100.0

# 점수를 나눠 갖는 층. `gate`는 통과/탈락이라 배점이 없다.
SCORED_LAYERS: tuple[ScoreLayer, ...] = ("fact", "judgment")

# `settings.gate_kinds`의 종류 이름 → 조건 문구에서 찾을 표지.
#
# **직군 어휘가 아니라 자격의 성격 어휘**다 — 어느 직군 공고에도 같은 말로 나온다
# (`parser/classify.py`의 `REQUIRED_MARKERS`와 같은 성격). 목록을 길게 늘리지 않는다:
# 넓히면 「우대 자격증」까지 게이트로 빨려 들어가 지원자가 탈락한다. 게이트는 **좁게**
# 둔다는 것이 설계 결정이다 (`docs/TRADEOFFS.md` A-2).
#
# 표에 없는 종류 이름을 `.env`에 넣으면 **그 문자열 자체가 표지**가 된다. 코드를 고치지
# 않고 게이트를 늘릴 수 있는 자리다.
GATE_MARKERS: dict[str, tuple[str, ...]] = {
    "license": ("면허", "국가자격", "법정자격", "법정 자격"),
}

# 갈래 → 층. **이 표가 세 갈래 설계의 전부다.**
# `binary`와 `graded`가 같은 층으로 가는데도 갈래를 합치지 않는 이유는 기준점이 다르기
# 때문이다 (`anchors.ANCHOR_TEMPLATES`) — 층은 「누가 채점하나」이고 갈래는
# 「무엇을 묻나」라서, 하나로 접으면 충족형 질문을 만들 자리가 사라진다.
BRANCH_LAYERS: dict[RequirementBranch, ScoreLayer] = {
    "term": "fact",
    "binary": "judgment",
    "graded": "judgment",
}

# 「문자열로 셀 수 있는가」의 두 신호. **둔한 신호지만 결정적이다.**
# LLM을 못 부를 때 떨어지는 자리이고(`branch.fallback_branch`), 그때도 파이프라인은 돈다.
_LATIN_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9+#._-]*")
_DIGIT = re.compile(r"\d")
# 한 글자짜리 라틴 토큰은 문장 부스러기일 확률이 높다. 두 글자부터 이름으로 본다.
_LATIN_MIN = 2

_LEADING_BULLET = re.compile(r"^[\s·\-•※*]+")
_WHITESPACE = re.compile(r"\s+")
# 화면과 심사위원 프롬프트에 함께 나가는 짧은 이름. 조건 문구 전문은 기준점에 있다.
LABEL_MAX = 40


def gate_markers(settings: Settings) -> tuple[str, ...]:
    """설정에 켜진 게이트 종류가 찾을 표지 전부."""
    markers: list[str] = []
    for kind in settings.gate_kinds:
        markers.extend(GATE_MARKERS.get(kind, (kind,)))
    return tuple(markers)


def is_gate(requirement: Requirement, settings: Settings) -> bool:
    """0층 게이트인가 — **면허·법정 자격증만.**

    `kind`가 `required`인 것만 본다. 우대 조건에 자격증 이름이 있다고 탈락시키면
    「있으면 좋은 것」이 「없으면 안 되는 것」이 된다.
    """
    if requirement.kind != "required":
        return False
    return any(marker in requirement.text for marker in gate_markers(settings))


def is_countable(text: str) -> bool:
    """충족 여부를 문자열 대조로 확인할 수 있는가. **폴백 규칙이다.**

    보는 것은 **문자의 종류**뿐이다 — 라틴 문자로 된 이름(도구·자격증·표준 이름)이나
    수치(연차·기한·점수)가 있으면 이력서에서 같은 문자열을 찾아 셀 수 있다.

    **이 규칙이 어디서 틀리는지는 이미 안다.** 라틴 문자도 숫자도 없지만 사실상 이분법인
    조건이 판단 층으로 가고, 서술형 조건에 연도가 하나 끼면 사실 층으로 온다. 실측 공고
    두 건에서 필수 조건 여러 개가 그렇게 어긋났고, 그래서 판정을 `rubric/branch.py`로
    옮겼다. 여기 남은 것은 **LLM을 못 부를 때 떨어질 자리**다 — 파싱 추적 화면이
    `client=None`으로 도는데 거기서 죽으면 안 된다.
    """
    if _DIGIT.search(text):
        return True
    return any(len(match.group()) >= _LATIN_MIN for match in _LATIN_TOKEN.finditer(text))


def branch_of(
    requirement: Requirement,
    branches: Mapping[str, RequirementBranch] | None = None,
) -> RequirementBranch:
    """조건 하나의 갈래. **담당업무는 언제나 `graded`다.**

    담당업무는 「이 일을 할 수 있는가」의 정도를 재는 자이지 충족/미충족을 묻는 조건이
    아니다. 분류기에도 안 보내고(`branch.resolve_branches`) 여기서도 되묻지 않는다.

    `branches`에 없으면 옛 규칙으로 정한다 — 인자를 안 주는 호출부(테스트·재조립)가
    갑자기 다른 층 배정을 받지 않게 하는 자리이기도 하다.
    """
    if requirement.kind == "duty":
        return "graded"
    given = branches.get(requirement.id) if branches else None
    if given is not None:
        return given
    return "term" if is_countable(requirement.text) else "graded"


def assign_layer(
    requirement: Requirement,
    settings: Settings,
    branches: Mapping[str, RequirementBranch] | None = None,
) -> ScoreLayer:
    """조건 하나가 어느 층으로 가는가. **순서가 규칙이다.**"""
    # 담당업무는 자격이 아니다. 게이트·사실 채점에 **절대** 들어가지 않는다.
    if requirement.kind == "duty":
        return "judgment"
    if is_gate(requirement, settings):
        return "gate"
    return BRANCH_LAYERS[branch_of(requirement, branches)]


def make_label(text: str) -> str:
    """항목의 짧은 이름. 불릿을 떼고 줄바꿈을 접는다.

    조건 문구 전문은 기준점(`anchors`)에 그대로 남아 있으므로 여기서 잘라도 잃는 것이 없다.
    """
    cleaned = _WHITESPACE.sub(" ", _LEADING_BULLET.sub("", text)).strip()
    if len(cleaned) <= LABEL_MAX:
        return cleaned
    return cleaned[: LABEL_MAX - 1].rstrip() + "…"


def _kind_shares(settings: Settings) -> dict[str, float]:
    """층 안에서 `kind`가 갖는 상대 몫. **순서를 코드가 강제한다.**

    `required ≥ preferred ≥ duty`로 눌러 담는다 — 설정이 뒤집힌 값을 줘도 담당업무가
    명시된 우대 조건보다 무거워지지 않는다. 담당업무는 **요구가 아니라 직무 설명**이다.
    """
    shares = settings.kind_shares
    required = max(shares.get("required", 0.0), 0.0)
    preferred = min(max(shares.get("preferred", 0.0), 0.0), required)
    duty = min(max(shares.get("duty", 0.0), 0.0), preferred)
    return {"required": required, "preferred": preferred, "duty": duty, "gate": 0.0}


def _layer_totals(
    by_layer: dict[str, list[Requirement]], settings: Settings
) -> dict[str, float]:
    """층별 총합. **항목이 없는 층의 배점은 남은 층이 가져간다.**

    안 그러면 사실 층 조건이 하나도 없는 공고에서 35점이 갈 곳을 잃어 총점이 65점이 된다.
    「항목이 몇 개든 총점이 100」이 이 함수의 계약이다.
    """
    present = [layer for layer in SCORED_LAYERS if by_layer.get(layer)]
    if not present:
        return {}
    raw = {layer: max(settings.weights.get(layer, 0.0), 0.0) for layer in present}
    denom = fsum(raw.values())
    if denom <= 0:
        return {layer: TOTAL_POINTS / len(present) for layer in present}
    return {layer: TOTAL_POINTS * raw[layer] / denom for layer in present}


def _settle(weights: dict[str, float]) -> None:
    """반올림하고 남은 잔돈을 가장 무거운 항목에 몰아 총합을 정확히 100으로 만든다.

    「총합 100」을 근사가 아니라 **사실**로 둔다 — 화면에 배점을 표시할 때 합이 99.9999로
    보이면 그게 계산 오류인지 설계인지 아무도 구분하지 못한다.
    """
    if not weights:
        return
    for key, value in weights.items():
        weights[key] = round(value, 6)
    residual = TOTAL_POINTS - fsum(weights.values())
    if residual:
        weights[max(weights, key=lambda key: weights[key])] += residual


def build_rubric(
    requirements: list[Requirement],
    settings: Settings,
    graph: EvidenceGraph,
    duties: list[Requirement] | None = None,
    branches: Mapping[str, RequirementBranch] | None = None,
) -> list[Criterion]:
    """조건 목록과 담당업무 목록을 루브릭 항목으로 바꾼다.

    `requirements.json`의 **두 목록을 다 받는다** — `requirements`(조건)와
    `duties`(담당업무). 담당업무 섹션이 없는 공고에서는 `duties`가 빈 목록으로 오고,
    **그때도 총합은 100이다.**

    `branches`는 `rubric/branch.resolve_branches()`가 만든 `{조건 id: 갈래}`다.
    **안 주면 옛 글자 모양 규칙으로 떨어진다** — 루브릭을 다시 짓는 호출부
    (`api/service.py`의 승인·`--no-judge`)는 제안서에 저장된 갈래를 도로 넘겨야
    층 배정이 승인 전후로 흔들리지 않는다.

    각 항목에 `derived_from` Link를 반드시 건다. 안 걸면 검산 G3이 차단한다 —
    그 Link가 「이 항목이 공고에서 나왔다」의 유일한 증거이기 때문이다.
    """
    items = list(requirements)
    seen = {req.id for req in items}
    for duty in duties or []:
        if duty.id not in seen:
            items.append(duty)
            seen.add(duty.id)

    branch_by_id = {req.id: branch_of(req, branches) for req in items}
    layers = {req.id: assign_layer(req, settings, branches) for req in items}
    by_layer: dict[str, list[Requirement]] = {}
    for req in items:
        by_layer.setdefault(layers[req.id], []).append(req)

    totals = _layer_totals(by_layer, settings)
    shares_by_kind = _kind_shares(settings)

    weights: dict[str, float] = {}
    for layer, members in by_layer.items():
        if layer not in totals:
            continue  # 게이트 층. 통과/탈락이라 배점이 없다
        shares = [shares_by_kind.get(req.kind, 0.0) for req in members]
        denom = fsum(shares)
        if denom <= 0:
            # 설정이 몫을 전부 0으로 만든 경우. 조용히 0점짜리 루브릭을 내지 않는다
            shares = [1.0] * len(members)
            denom = float(len(members))
        for req, share in zip(members, shares, strict=True):
            weights[req.id] = totals[layer] * share / denom
    _settle(weights)

    criteria: list[Criterion] = []
    for index, req in enumerate(items, start=1):
        criterion = Criterion(
            id=f"C-{index:02d}",
            requirement_id=req.id,
            label=make_label(req.text),
            # 기준점이 갈래를 따라 갈린다. `binary`는 충족형, 나머지는 서술형이다.
            anchors=make_anchors(req, branch_by_id[req.id]),
            weight=weights.get(req.id, 0.0),
            layer=layers[req.id],
            branch=branch_by_id[req.id],
        )
        # 조건이 아직 그래프에 없으면 함께 담는다 — 없으면 `derived_from`이 허공을 가리켜
        # G3이 「이어진 조건이 없다」로 막는다. 루브릭만 그래프에 넣는 경로를 막아 둔다.
        if graph.get(req.id) is None:
            graph.add(req)
        graph.add(criterion)
        graph.link(criterion.id, "derived_from", req.id)
        criteria.append(criterion)

    return criteria
