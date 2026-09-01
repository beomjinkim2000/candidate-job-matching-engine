"""사람이 읽는 결과 — **과제 요구 ③의 뒤쪽 절반이다.**

요구는 「0~100점」만이 아니라 **「사람이 읽을 수 있는 근거를 함께 낸다」**이다. 점수만
내면 요구의 절반만 한 것이다.

## ID만 있는 줄을 만들지 않는다

`C-03`·`E-07` 같은 식별자는 사람에게 아무 말도 하지 않는다. **ID를 안 봐도 뜻이 통해야**
사람이 읽는 것이다. 그래서 여기서는 항목 이름(`label`)과 조건 문구, 인용 구간을 쓴다.
지원자 번호만 예외인데, 그건 목업 데이터셋의 이름이라 이름 자리에 놓아야 한다.

## 근거는 그래프에서 온다

`└ 근거:` 줄은 `AxisScore.evidence_ids`를 따라 그래프에서 `Evidence`를 찾아 만든다.
**`Score.rationale`을 이어붙인 것이 아니다** — 그건 채점자가 쓴 서술이고, `└ 판단:` 줄에
그렇게 표시해 따로 붙인다. 둘을 섞으면 무엇이 검증된 것인지 사라진다
(`model/render.py`의 같은 원칙).

`AxisScore.rationale`(=`render_rationale()`의 문단)은 좌표까지 포함한 전문이라 화면에는
길다. 결과 JSON과 UI가 그걸 쓰고, 터미널은 여기서 줄인 형태를 쓴다 —
**둘 다 같은 그래프에서 나온다.**

## 게이트 탈락자

`gate_failed: true`는 문장이 아니다. 탈락자의 출력에는 **왜 떨어졌는지가 문장으로**
있어야 한다. `GateResult.reasons`가 이미 문장이므로 그대로 싣는다.
"""

from __future__ import annotations

from .aggregate import AxisScore, CandidateResult
from .run import RunResult

_LAYER_TITLE = {"gate": "게이트", "fact": "사실 채점", "judgment": "판단 채점"}
_LAYER_DETAIL = {"gate": "판정", "fact": "채점", "judgment": "판단"}
_STATUS_LABEL = {"draft": "AI 초안", "human_validated": "사람 확인함"}

# 화면에서 항목 이름이 차지하는 폭. 한글이 섞이면 자릿수와 글자폭이 다르지만,
# 열을 대충 맞춰 두는 편이 안 맞춘 것보다 낫다.
_LABEL_WIDTH = 30
# 인용을 화면에 실을 때의 상한. 넘으면 뒤를 줄인다 — 근거가 화면을 덮으면 안 읽힌다.
_QUOTE_MAX = 60


def _shorten(text: str, limit: int = _QUOTE_MAX) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _raw_text(axis: AxisScore) -> str:
    """원점수를 사람 말로. **층마다 자가 다르다는 것이 보여야 한다.**"""
    if axis.layer == "judgment":
        return f"심사위원 평균 {axis.raw:.1f} / 5점"
    if axis.layer == "gate":
        return "통과" if axis.raw > 0 else "미충족"
    return f"충족도 {axis.raw:.2f} (0~1)"


def _axis_lines(axis: AxisScore, index: dict, rationale: str | None) -> list[str]:
    badge = f"[{axis.evidence_grade} · {_STATUS_LABEL.get(axis.review_status, axis.review_status)}]"
    head = (
        f"    {axis.label[:_LABEL_WIDTH].ljust(_LABEL_WIDTH)} "
        f"{axis.weighted:6.1f} / {axis.max_weighted:5.1f}점   "
        f"{_raw_text(axis)}  {badge}"
    )
    lines = [head]

    for evidence_id in axis.evidence_ids:
        evidence = index.get(evidence_id)
        span = getattr(evidence, "span", None)
        if span is None:
            continue
        quote = _shorten(getattr(evidence, "quote", ""))
        lines.append(f"      └ 근거: 이력서 {span.start}~{span.end}번째 글자 「{quote}」")
    if not axis.evidence_ids:
        # 근거로 인용할 구간이 없는 점수다 (사실 층 0점 · 게이트 탈락). **비워 두지 않는다** —
        # 「근거가 없다」와 「근거를 안 보여줬다」는 다르다.
        lines.append("      └ 근거: 이력서에서 인용할 구간을 찾지 못해 조건 자체로 판정했다")

    if rationale:
        label = _LAYER_DETAIL.get(axis.layer, "판단")
        lines.append(f"      └ {label}: {' '.join(rationale.split())}")
    return lines


def _candidate_block(result: RunResult, candidate: CandidateResult) -> str:
    if candidate.rank is None:
        head = (
            f"[탈락] 지원자 {candidate.candidate_id} — 게이트 조건을 못 넘어 순위에서 뺐다 "
            f"(참고 합계 {candidate.total:.1f} / 100점)"
        )
    else:
        head = (
            f"[{candidate.rank}위] 지원자 {candidate.candidate_id} — "
            f"총점 {candidate.total:.1f} / 100점"
        )

    # 그래프를 한 번만 훑는다 — 축마다 다시 index()를 만들면 지원자 수 × 항목 수만큼
    # 같은 표를 다시 짓게 된다.
    index = result.graph.index()
    rationales = {
        score.criterion_id: score.rationale
        for score in result.graph.scores
        if score.candidate_id == candidate.candidate_id
    }

    lines = [head, ""]

    if candidate.gate.passed:
        gate_axes = [axis for axis in candidate.breakdown if axis.layer == "gate"]
        detail = (
            f"게이트 항목 {len(gate_axes)}건 충족" if gate_axes else "게이트 항목이 없는 공고다"
        )
        lines.append(f"  게이트  통과 — {detail}")
    else:
        lines.append(f"  게이트  탈락 — 사유 {len(candidate.gate.reasons)}건")
        for reason in candidate.gate.reasons:
            lines.append(f"    · {' '.join(reason.split())}")
    lines.append("")

    for layer in ("fact", "judgment"):
        axes = [axis for axis in candidate.breakdown if axis.layer == layer]
        if not axes:
            continue
        got = sum(axis.weighted for axis in axes)
        top = sum(axis.max_weighted for axis in axes)
        lines.append(f"  {_LAYER_TITLE[layer]} ({top:.1f}점 만점 중 {got:.1f}점)")
        for axis in axes:
            lines.extend(_axis_lines(axis, index, rationales.get(axis.criterion_id)))
        lines.append("")

    return "\n".join(lines).rstrip()


def _header(result: RunResult) -> str:
    lines = [
        f"공고 {result.posting_id} — 지원자 {len(result.ranked)}명을 채점했다. "
        f"총점은 100점 만점이고 순위는 아래와 같다.",
        f"출처: {result.source_kind} · 실행 {result.run_id}",
    ]
    if result.unapproved:
        # 조용히 지나갈 수 없게 만드는 것이 승인 게이트의 요점이다.
        lines.append("⚠️ 미승인 — 고객사가 확인하지 않은 AI 초안 루브릭으로 채점했다.")
    if not result.revision_checked:
        lines.append(
            "⚠️ 공고 수정 여부를 확인하지 못했다 (공고 조회 API 미연결) — "
            "승인이 현재 공고에 대한 것인지 검사되지 않았다."
        )
    cost = result.cost
    priced = f"${cost.usd:.4f}" if cost.priced else "$0 (단가 미설정 — 비용 환산 없음)"
    lines.append(
        f"비용: 심사위원 호출 {cost.calls}회 · 입력 {cost.in_tokens:,}토큰 · "
        f"출력 {cost.out_tokens:,}토큰 · {priced}"
    )
    return "\n".join(lines)


def explain(result: RunResult, candidate_id: str | None = None) -> str:
    """결과를 사람이 읽는 텍스트로. CLI가 그대로 출력한다.

    `candidate_id`를 주면 그 지원자만, 안 주면 랭킹 전체를 낸다. 전체 출력의 머리에는
    미승인·G7 미검사·비용이 붙는다 — **결과가 어떤 조건에서 나왔는지가 점수보다 먼저
    보여야 한다.**
    """
    if candidate_id is not None:
        found = next(
            (item for item in result.ranked if item.candidate_id == candidate_id), None
        )
        if found is None:
            raise ValueError(f"결과에 없는 지원자다: {candidate_id}")
        return _candidate_block(result, found)

    blocks = [_header(result)]
    blocks.extend(_candidate_block(result, item) for item in result.ranked)
    return "\n\n".join(blocks) + "\n"
