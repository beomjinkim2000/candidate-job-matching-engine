"""`Requirement` 목록 → `Criterion` 목록. **공고마다 다른 루브릭이 여기서 만들어진다.**

## 층 배정

| 조건 | 가는 층 | 이유 |
|---|---|---|
| `settings.gate_kinds`에 걸림 | `gate` | 없으면 그 일을 법적으로 못 한다 |
| 보유 여부를 문자열로 셀 수 있음 | `fact` | 코드가 센다 |
| 그 외 | `judgment` | 심사위원이 판단한다 |
| `kind == "duty"` | `judgment` **고정** | 아래 |

**분류를 직군 어휘로 하지 않는다.** 판단 기준은 *「이 조건의 충족 여부를 문자열 대조로
확인할 수 있는가」*이지 *「이게 어느 직군의 기술인가」*가 아니다. 그래서 이 파일에는
직군명도 기술명도 없고, `is_countable()`이 보는 것은 **문자의 종류**뿐이다.

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
from math import fsum

from ..config import Settings
from ..model.graph import EvidenceGraph
from ..model.objects import Criterion, Requirement, ScoreLayer
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

# 「문자열로 셀 수 있는가」의 두 신호. **둔한 신호지만 결정적이다.**
# 의미 판정을 LLM에게 넘기면 「세는 것은 코드가, 판단하는 것은 심사위원이」가 무너진다.
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
    """충족 여부를 문자열 대조로 확인할 수 있는가.

    보는 것은 **문자의 종류**뿐이다 — 라틴 문자로 된 이름(도구·자격증·표준 이름)이나
    수치(연차·기한·점수)가 있으면 이력서에서 같은 문자열을 찾아 셀 수 있다.

    **이 규칙이 틀리는 곳을 적어 둔다.** 「병역필 또는 면제된 분」처럼 라틴 문자도 숫자도
    없지만 사실상 이분법인 조건은 판단 층으로 간다. 반대로 서술형 조건에 연도가 하나
    끼어 있으면 사실 층으로 온다. 고치려면 상태 어휘 사전이 필요한데, 사전은 목록 밖
    표현에 **조용히** 실패한다 — 파서에서 섹션 제목 사전을 뺀 이유와 같다.
    지금은 틀리는 방향이 보이게 두고, `layer`를 결과에 실어 확인할 수 있게 한다.
    """
    if _DIGIT.search(text):
        return True
    return any(len(match.group()) >= _LATIN_MIN for match in _LATIN_TOKEN.finditer(text))


def assign_layer(requirement: Requirement, settings: Settings) -> ScoreLayer:
    """조건 하나가 어느 층으로 가는가. **순서가 규칙이다.**"""
    # 담당업무는 자격이 아니다. 게이트·사실 채점에 **절대** 들어가지 않는다.
    if requirement.kind == "duty":
        return "judgment"
    if is_gate(requirement, settings):
        return "gate"
    return "fact" if is_countable(requirement.text) else "judgment"


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
) -> list[Criterion]:
    """조건 목록과 담당업무 목록을 루브릭 항목으로 바꾼다.

    `requirements.json`의 **두 목록을 다 받는다** — `requirements`(조건)와
    `duties`(담당업무). 담당업무 섹션이 없는 공고에서는 `duties`가 빈 목록으로 오고,
    **그때도 총합은 100이다.**

    각 항목에 `derived_from` Link를 반드시 건다. 안 걸면 검산 G3이 차단한다 —
    그 Link가 「이 항목이 공고에서 나왔다」의 유일한 증거이기 때문이다.
    """
    items = list(requirements)
    seen = {req.id for req in items}
    for duty in duties or []:
        if duty.id not in seen:
            items.append(duty)
            seen.add(duty.id)

    layers = {req.id: assign_layer(req, settings) for req in items}
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
            anchors=make_anchors(req),
            weight=weights.get(req.id, 0.0),
            layer=layers[req.id],
        )
        # 조건이 아직 그래프에 없으면 함께 담는다 — 없으면 `derived_from`이 허공을 가리켜
        # G3이 「이어진 조건이 없다」로 막는다. 루브릭만 그래프에 넣는 경로를 막아 둔다.
        if graph.get(req.id) is None:
            graph.add(req)
        graph.add(criterion)
        graph.link(criterion.id, "derived_from", req.id)
        criteria.append(criterion)

    return criteria
