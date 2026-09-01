"""1층 사실 채점 — **코드가 센다. LLM을 부르지 않는다.**

같은 이력서를 몇 번 넣어도 같은 점수가 나와야 하는 층이다. 심사위원에게 「경력이 5년
맞나」를 묻지 않는 이유: 같은 질문을 되풀이하면 판정이 13.6% 뒤집힌다는 실측이 있고,
**셀 수 있는 것을 그 확률로 틀릴 이유가 없다** (`docs/TRADEOFFS.md` A-1).

## 항목 유형은 조건 문구가 정한다 — 직군이 아니라

| 유형 | 언제 | 점수 |
|---|---|---|
| 수치 | 「N년 이상」처럼 **비교어가 붙은 기간**이 조건에 있을 때 | 포화함수 |
| 열거 | 대조할 표현이 **둘 이상** 뽑혔을 때 | 커버리지 비율 0.0~1.0 |
| 보유/미보유 | 표현이 **하나** 뽑혔을 때 | 1.0 / 0.0 |

표현을 뽑는 규칙은 `normalize.key_terms()`에 있고, 거기에도 직군 어휘가 없다.

## 연차는 포화함수다 — 선형 금지

```
score = 1 - exp(-k · 보유 / 요구)
```

요구 3년·k=2.0에서 3년 보유가 0.865, 10년 보유가 0.999다. **10년이 3년의 3배가 아니라
1.15배로 평가된다.** Point Method의 타당도가 낮은 이유로 문헌이 직접 지목한 것이
「경력 연수가 성과와 선형이라는 가정」이었다 (`docs/TRADEOFFS.md` A-4).
`k`는 임의값이고, 임의값이라는 사실이 그 문서에 적혀 있다.

## 0점에도 근거를 붙인다

못 찾아서 0점을 준 경우 인용할 이력서 구간이 없다. 그렇다고 근거를 비우면 「근거 없는
점수」와 구별이 안 된다. 그래서 **`derived_from`으로 항목과 조건에 잇고** 무엇을 못 찾았는지
`rationale`에 적는다. 검산 G1이 이 경우를 예외로 인정한다 (`model/governance.py`).
"""

from __future__ import annotations

from math import exp

from ..config import Settings
from ..model.graph import EvidenceGraph
from ..model.objects import Criterion, Evidence, Requirement, Resume, Score, Span
from .mask import mask_sensitive
from .normalize import (
    Normalized,
    duration_spans,
    expand,
    key_terms,
    load_aliases,
    normalize_with_map,
    required_years,
)

FACT_LAYER = "fact"

# 한 항목에서 만드는 근거의 상한. 같은 낱말이 이력서에 열 번 나와도 근거는 한 번이면
# 충분하고, 더 담으면 화면이 인용으로 뒤덮인다.
MAX_EVIDENCE_PER_TERM = 1
MAX_DURATION_EVIDENCE = 3


def saturation(have: float, required: float, k: float) -> float:
    """포화함수. 요구를 채운 지점이 완만해지는 지점이다.

    `required`가 0이면 나눌 수 없다 — 요구가 없는 항목이므로 보유 여부만 본다.
    """
    if required <= 0:
        return 1.0 if have > 0 else 0.0
    if have <= 0:
        return 0.0
    return min(1.0, 1.0 - exp(-k * have / required))


def _source_text(criterion: Criterion, requirement: Requirement | None) -> str:
    """대조할 표현을 뽑을 원본. 조건 문구가 있으면 그것을, 없으면 항목 이름을 쓴다.

    항목 이름(`label`)은 40자에서 잘린 요약이라 조건 문구가 언제나 낫다.
    """
    return requirement.text if requirement is not None else criterion.label


def _requirement_of(graph: EvidenceGraph, criterion: Criterion) -> Requirement | None:
    found = graph.get(criterion.requirement_id)
    return found if isinstance(found, Requirement) else None


def _find_term(document: Normalized, term: str, aliases) -> tuple[str, Span] | None:
    """표기 묶음 중 **처음 걸리는 것**의 자리. 없으면 `None`."""
    for spelling in expand(term, aliases):
        spans = document.find_all(spelling, limit=MAX_EVIDENCE_PER_TERM)
        if spans:
            return spelling, spans[0]
    return None


def _add_evidence(
    graph: EvidenceGraph,
    resume: Resume,
    criterion: Criterion,
    span: Span,
    ordinal: int,
) -> Evidence:
    """근거 하나를 그래프에 담고 항목에 잇는다.

    `quote`는 **주어진 이력서 글에서 그 자리를 다시 잘라** 만든다. 매칭은 마스킹된 글에서
    했지만 `■`는 어떤 표현과도 안 맞으므로 잘린 구간에 `■`가 들어갈 수 없고, 따라서
    마스킹 전·후 어느 쪽으로 대조해도 검산 G2를 통과한다.
    """
    evidence = Evidence(
        id=f"E-{resume.candidate_id}-{criterion.id}-{ordinal:02d}",
        resume_id=resume.candidate_id,
        span=span,
        quote=resume.text[span.start : span.end],
    )
    graph.add(evidence)
    graph.link(evidence.id, "supports", criterion.id)
    return evidence


def _score_numeric(
    document: Normalized,
    required: float,
    k: float,
) -> tuple[float, list[Span], str]:
    durations = duration_spans(document.raw)
    if not durations:
        return (
            0.0,
            [],
            f"요구 기간 {required:g}년 — 이력서에서 기간을 말하는 표현을 찾지 못했다.",
        )
    have = sum(years for _, years in durations)
    value = saturation(have, required, k)
    spans = [span for span, _ in durations[:MAX_DURATION_EVIDENCE]]
    return (
        value,
        spans,
        f"요구 기간 {required:g}년, 이력서에서 읽은 기간 합 {have:.2g}년 — "
        f"포화함수(k={k:g})로 {value:.3f}.",
    )


def _score_terms(
    document: Normalized,
    terms: list[str],
    aliases,
) -> tuple[float, list[Span], str]:
    found: list[tuple[str, Span]] = []
    missing: list[str] = []
    for term in terms:
        hit = _find_term(document, term, aliases)
        if hit is None:
            missing.append(term)
        else:
            found.append(hit)

    value = len(found) / len(terms)
    hit_text = ", ".join(spelling for spelling, _ in found) or "없음"
    miss_text = ", ".join(missing) or "없음"

    if len(terms) == 1:
        # 보유/미보유. 커버리지 식이 그대로 1.0 또는 0.0을 준다.
        head = (
            f"조건에서 뽑은 표현 「{terms[0]}」 — 이력서에서 찾았다."
            if found
            else f"조건에서 뽑은 표현 「{terms[0]}」 — 이력서 전체에서 찾지 못했다."
        )
    else:
        head = (
            f"조건에서 뽑은 표현 {len(terms)}개 중 {len(found)}개를 이력서에서 찾았다."
        )
    return value, [span for _, span in found], f"{head} 찾음: {hit_text} / 못 찾음: {miss_text}."


def score_fact(
    resume: Resume,
    criteria: list[Criterion],
    graph: EvidenceGraph,
    *,
    settings: Settings | None = None,
    aliases: dict[str, tuple[str, ...]] | None = None,
) -> list[Score]:
    """`layer == "fact"`인 항목을 채점한다. 결정적이다 — 같은 입력에 같은 출력.

    `settings`·`aliases`는 선택 인자다. 안 주면 기본 설정과 `data/aliases.json`을 쓴다.
    `k`(포화 계수)를 코드에 박지 않기 위한 자리이므로 **이 함수 안에 숫자를 쓰지 않는다.**

    마스킹은 **여기서 스스로 건다.** 호출자가 걸어 둔 글을 받아도 결과가 같다.
    """
    active = settings if settings is not None else Settings()
    table = aliases if aliases is not None else load_aliases()
    masked, _ = mask_sensitive(resume.text)
    document = normalize_with_map(masked)

    scores: list[Score] = []
    for criterion in criteria:
        if criterion.layer != FACT_LAYER:
            continue

        requirement = _requirement_of(graph, criterion)
        text = _source_text(criterion, requirement)

        required = required_years(text)
        if required is not None:
            value, spans, rationale = _score_numeric(
                document, required, active.experience_saturation_k
            )
        else:
            terms = key_terms(text)
            if terms:
                value, spans, rationale = _score_terms(document, terms, table)
            else:
                # 사실 층에 온 항목은 숫자든 라틴 토큰이든 하나는 갖고 있어야 한다
                # (`rubric/build.py`의 `is_countable`). 여기 오면 그 가정이 깨진 것이다.
                value, spans, rationale = (
                    0.0,
                    [],
                    "조건 문구에서 이력서와 대조할 표현을 뽑지 못했다 — 채점하지 못한 항목이다.",
                )

        score = Score(
            id=f"S-{resume.candidate_id}-{criterion.id}",
            criterion_id=criterion.id,
            candidate_id=resume.candidate_id,
            value=value,
            layer=FACT_LAYER,
            judge_id=None,  # 사실 층에는 채점자가 없다. 코드가 셌다
            rationale=rationale,
        )
        graph.add(score)

        for ordinal, span in enumerate(spans, start=1):
            evidence = _add_evidence(graph, resume, criterion, span, ordinal)
            graph.link(score.id, "grounded_in", evidence.id)

        if not spans:
            # 근거로 인용할 구간이 없다. 항목과 조건에 직접 이어 「무엇에 대한 0점인가」를
            # 남긴다 — 검산 G1이 인정하는 예외이고, 조건까지 이어야 `trace()`가 공고
            # 이미지 좌표에 닿는다 (항목에만 이으면 사슬이 거기서 끊긴다).
            graph.link(score.id, "derived_from", criterion.id)
            if requirement is not None:
                graph.link(score.id, "derived_from", requirement.id)

        scores.append(score)

    return scores
