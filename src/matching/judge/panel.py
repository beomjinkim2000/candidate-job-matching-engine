"""2층 판단 채점 — **파이프라인에서 유일하게 비결정적인 곳이다.**

## 운영 규칙 (`src/CLAUDE.md` 「심사위원 운영」)

- **인원**: 2명 독립. 5점 척도에서 **2점 이상 이견**인 항목만 3번째를 부른다.
  같은 제품군 LLM은 9개를 써도 독립 투표 2명분의 정보량만 나온다 (상관오차).
- **집계**: 산술평균. **토론·합의 금지** — 한쪽이 동조하는 실패 모드가 있고,
  합의의 정확도 이득은 실무적으로 미미했다.
- **근거**: 인용을 **먼저** 쓰게 한다. 점수를 먼저 내면 근거가 사후 정당화가 된다.

## 인용을 코드가 다시 자른다

`Evidence.quote`는 모델이 준 `text`가 아니라 **`resume_text[start:end]`**다. 모델의
문자열을 그대로 믿으면 검산 G2가 자기 자신을 검사하는 꼴이 된다 — 지어낸 인용과 지어낸
대조값이 서로 맞아떨어진다.

받아들이는 조건은 셋이다.

1. 오프셋이 원문 범위 안이고 `start < end`
2. 모델의 `text`가 **공백을 무시하면** 그 구간과 같다. 공백만 봐주는 이유: 모델은 줄바꿈을
   칸으로 바꿔 옮기는 일이 잦은데 그건 위치를 틀린 게 아니다. 공백을 지운 뒤에도 글자가
   다르면 그건 **다른 곳을 가리킨 것**이라 버린다
3. 그 구간에 마스킹 문자가 **없다**. 있으면 버린다 — 가린 자리를 인용하면 원문에서 다시
   자를 때 가린 값이 근거 문단으로 되살아난다. 그러면 마스킹이 장식이 된다

**어긋난 인용은 그 인용만 버린다.** 응답 전체를 버리지 않는다. 다만 남은 인용이 하나도
없으면 그 응답은 쓰지 않는다 — 근거 없는 점수는 검산 G1에서 어차피 막힌다.

## 사실 확인을 시키지 않는다 — 코드로 막는다

`layer != "judgment"`인 항목이 오면 `ValueError`를 던진다. 「심사위원에게 연차를 묻지
않는다」를 규칙으로만 두면 호출부 한 곳이 어기는 것을 아무도 못 본다.

## temperature=0에서 2명이 갖는 뜻

두 심사위원은 **같은 프롬프트를 받는다.** `temperature=0`이므로 둘의 응답은 사실상 같은
추출이고, 이견 경로는 모델에 남은 잔여 비결정성에서만 발동한다. 그 잔여량이 얼마인지가
곧 반복 안정성 σ이고, σ가 0이면 「2명」은 비용만 두 배인 셈이다. **이 사실을 숨기지
않는다** — σ 실측이 이 step의 산출물인 이유가 여기에도 있다.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from statistics import fmean

from pydantic import BaseModel, ConfigDict, ValidationError

from ..config import Settings
from ..model.graph import EvidenceGraph
from ..model.objects import Criterion, Evidence, Resume, Score, Span
from ..scorer.mask import MASK_CHAR, mask_sensitive
from ..source.base import default_data_dir
from .prompt import GENERIC_EXAMPLES, MAX_QUOTES, ScoringExample, build_prompt, prompt_sha256
from .schema import RESPONSE_FORMAT, JudgeCall, JudgeOutput

JUDGMENT_LAYER = "judgment"

# 심사위원 이름. **3명 고정이 아니다** — 세 번째는 이견이 있을 때만 부른다.
JUDGE_IDS: tuple[str, str, str] = ("judge-1", "judge-2", "judge-3")

# 재현 조건. 반복 안정성을 재려면 남는 분산이 **모델 자체의 것**이어야 한다.
JUDGE_TEMPERATURE = 0.0

USAGE_FILENAME = ".judge_usage.json"


class JudgeError(RuntimeError):
    """심사위원 층에서 점수를 만들 수 없다."""


class NoGroundedResponse(JudgeError):
    """모든 응답이 쓸 만한 인용을 내지 못했다.

    **0점이나 1점으로 대신하지 않는다.** 그러면 「근거를 못 낸 것」과 「관련 경험이 없는
    것」이 같은 점수가 되어 둘을 구별할 방법이 사라진다.
    """


class BudgetExceeded(JudgeError):
    """호출 상한에 닿았다. **조용히 줄이지 않는다** (`docs/COST_BUDGET.md` §1)."""


class UsageReport(BaseModel):
    """이 실행이 쓴 것. `RunResult.cost`에 실려 **화면에 「n회 / $x」로 표시된다.**"""

    model_config = ConfigDict(extra="forbid")

    calls: int
    in_tokens: int
    out_tokens: int
    usd: float
    # 단가가 설정에 없으면 USD 환산이 0이다. **그 사실이 결과에 드러나야 한다** —
    # 안 그러면 「$0」이 「공짜였다」로 읽힌다.
    priced: bool


class CallBudget:
    """호출수·토큰·환산 USD 누적. 상한은 `settings.max_total_calls`에서 온다.

    완주 1회 예상은 ≈117회이고 기본 상한은 200회다 (`docs/COST_BUDGET.md` §1).
    **상한에 닿으면 예외를 던진다** — 심사위원을 조용히 1명으로 줄이면 그 결과가 어떤
    설계로 나온 것인지 아무 데도 안 남는다.
    """

    def __init__(self, settings: Settings, path: Path | str | None = None) -> None:
        self.limit = settings.max_total_calls
        self.price_in_per_1m = settings.price_in_per_1m
        self.price_out_per_1m = settings.price_out_per_1m
        self.path = Path(path) if path is not None else default_data_dir() / USAGE_FILENAME
        self.calls = 0
        self.in_tokens = 0
        self.out_tokens = 0
        self.records: list[JudgeCall] = []

    def remaining(self) -> int:
        """남은 호출 수. 요청을 **보내기 전에** 본다."""
        return max(0, self.limit - self.calls)

    def spend(self, in_tokens: int, out_tokens: int, call: JudgeCall | None = None) -> None:
        """호출 1건을 기록한다. 상한을 넘겼으면 예외를 던진다.

        기록을 먼저 하고 예외를 던진다 — 「몰래 줄이지 않는다」의 반대편은
        **「몰래 지우지 않는다」**다. 넘긴 그 호출도 사용량에 남는다.
        """
        self.calls += 1
        self.in_tokens += max(0, in_tokens)
        self.out_tokens += max(0, out_tokens)
        if call is not None:
            self.records.append(call)
        if self.calls > self.limit:
            raise BudgetExceeded(
                f"호출 상한 {self.limit}회를 넘겼다 (지금 {self.calls}회). "
                "settings.max_total_calls로 조절한다"
            )

    def usd(self) -> float:
        """설정의 단가로 환산한 값. **단가를 코드에 박지 않는다** (§2)."""
        return (
            self.in_tokens * self.price_in_per_1m + self.out_tokens * self.price_out_per_1m
        ) / 1_000_000

    def report(self) -> UsageReport:
        return UsageReport(
            calls=self.calls,
            in_tokens=self.in_tokens,
            out_tokens=self.out_tokens,
            usd=self.usd(),
            priced=bool(self.price_in_per_1m or self.price_out_per_1m),
        )

    def save(self) -> None:
        """`data/.judge_usage.json`에 **누적**한다. 로컬 상태라 커밋되지 않는다."""
        previous = {"calls": 0, "in_tokens": 0, "out_tokens": 0, "usd": 0.0}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                loaded = {}
            if isinstance(loaded, dict):
                for key in previous:
                    value = loaded.get(key)
                    if isinstance(value, (int, float)):
                        previous[key] = value

        body = {
            "calls": previous["calls"] + self.calls,
            "in_tokens": previous["in_tokens"] + self.in_tokens,
            "out_tokens": previous["out_tokens"] + self.out_tokens,
            "usd": previous["usd"] + self.usd(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


class _Verdict:
    """심사위원 한 명의 결과. 모듈 밖으로 내보내지 않는다."""

    __slots__ = ("judge_id", "output", "spans")

    def __init__(self, judge_id: str, output: JudgeOutput, spans: list[Span]) -> None:
        self.judge_id = judge_id
        self.output = output
        self.spans = spans


def _strip_ws(text: str) -> str:
    return "".join(text.split())


def keep_quotes(output: JudgeOutput, masked_resume: str) -> list[Span]:
    """받아들일 인용만 남긴다. **어긋난 것은 그 인용만 버린다.**

    대조는 심사위원이 실제로 본 글(마스킹된 글)로 한다. 마스킹은 길이를 보존하므로
    오프셋은 원문과 같고, 받아들인 구간에는 마스킹 문자가 없으므로 그 구간을 원문에서
    다시 잘라도 같은 문자열이 나온다.
    """
    kept: list[Span] = []
    seen: set[tuple[int, int]] = set()
    for quote in output.quotes:
        if len(kept) >= MAX_QUOTES:
            break
        start, end = quote.start, quote.end
        if start < 0 or end <= start or end > len(masked_resume):
            continue
        actual = masked_resume[start:end]
        if MASK_CHAR in actual:
            continue  # 가린 자리를 인용했다. 원문에서 다시 자르면 가린 값이 되살아난다
        if _strip_ws(actual) != _strip_ws(quote.text):
            continue  # 위치가 어긋났다
        if (start, end) in seen:
            continue
        seen.add((start, end))
        kept.append(Span(start=start, end=end))
    return kept


def _ask(
    client,
    settings: Settings,
    messages: list[dict],
    digest: str,
    budget: CallBudget,
) -> JudgeOutput | None:
    """호출 1회. 응답을 읽을 수 없으면 `None`을 돌려주고 **재시도하지 않는다.**

    무한 재시도를 막는 것이 여기 규칙이다 ($5 예산). 판정은 남은 심사위원으로 하고,
    아무도 못 내면 `judge_criterion`이 예외를 던진다.
    """
    if budget.remaining() <= 0:
        raise BudgetExceeded(
            f"호출 상한 {budget.limit}회를 이미 다 썼다 — 요청을 보내지 않는다"
        )

    kwargs: dict = {
        "model": settings.judge_model,
        "temperature": JUDGE_TEMPERATURE,
        "messages": messages,
        "response_format": RESPONSE_FORMAT,
    }
    if settings.judge_seed is not None:
        kwargs["seed"] = settings.judge_seed

    response = client.chat.completions.create(**kwargs)

    usage = getattr(response, "usage", None)
    budget.spend(
        int(getattr(usage, "prompt_tokens", 0) or 0),
        int(getattr(usage, "completion_tokens", 0) or 0),
        JudgeCall(
            model=settings.judge_model,
            temperature=JUDGE_TEMPERATURE,
            seed=settings.judge_seed,
            prompt_sha256=digest,
        ),
    )

    content = response.choices[0].message.content
    try:
        return JudgeOutput.model_validate_json(content or "")
    except ValidationError:
        # 척도 밖 점수·형식 위반. **클램프하지 않는다** — 6점을 5점으로 깎으면
        # 「모델이 척도를 안 지켰다」는 사실이 사라진다.
        return None


def _rationale(
    verdicts: list[_Verdict],
    discarded: int,
    threshold: int,
    value: float,
    third_called: bool,
) -> str:
    """사람이 읽는 채점자 서술. **근거 자체가 아니다** — 근거는 Link다.

    `third_called`를 따로 받는 이유: 3번째를 불렀는데 그 응답이 버려지면 심사위원은 다시
    2명이 된다. 인원 수로만 판단하면 그때 「부르지 않았다」고 **거짓말을 하게 된다.**
    """
    heads = " · ".join(f"{v.judge_id} {v.output.score}점" for v in verdicts)
    lines = [f"심사위원 {len(verdicts)}명이 매긴 점수({heads})의 평균 {value:.2f}점."]

    if third_called:
        lines.append(f"앞의 두 심사위원이 {threshold}점 이상 갈려 3번째를 불렀다.")
    elif len(verdicts) == 2:
        gap = abs(verdicts[0].output.score - verdicts[1].output.score)
        lines.append(f"이견 {gap}점이 임계 {threshold}점 미만이라 3번째는 부르지 않았다.")

    if discarded:
        # 조용히 넘어가지 않는다. 몇 명이 근거를 못 냈는지가 결과에 남는다.
        lines.append(f"근거로 쓸 인용을 내지 못한 응답 {discarded}건은 평균에서 뺐다.")

    for verdict in verdicts:
        lines.append(f"[{verdict.judge_id}] {verdict.output.reasoning}")
    return " ".join(lines)


def judge_criterion(
    criterion: Criterion,
    candidate: Resume,
    resume_text: str,
    graph: EvidenceGraph,
    settings: Settings,
    client,
    *,
    examples: Sequence[ScoringExample] | None = None,
    budget: CallBudget | None = None,
) -> Score:
    """판단 항목 하나를 채점한다. **호출 2회, 이견이 크면 3회.**

    `resume_text`가 오프셋의 기준이다 — `candidate`에서는 `candidate_id`만 읽는다.
    둘이 어긋나면 검산 G2가 잡는다. 마스킹은 **여기서 스스로 건다** (`scorer`와 같다).

    `budget`을 안 주면 이 항목 하나짜리 예산을 새로 만든다. **완주에서는 반드시
    넘겨라** — 안 넘기면 상한이 항목마다 따로 세어져 전체 상한이 의미를 잃는다.
    """
    if criterion.layer != JUDGMENT_LAYER:
        # 「세는 것은 코드가, 판단하는 것은 심사위원이」를 규칙이 아니라 사실로 만든다.
        raise ValueError(
            f"판단 층 항목이 아니다: {criterion.id} (layer={criterion.layer}). "
            "사실 확인을 심사위원에게 묻지 않는다"
        )

    active_budget = budget if budget is not None else CallBudget(settings)
    shown = list(examples) if examples is not None else list(GENERIC_EXAMPLES)

    masked, _ = mask_sensitive(resume_text)
    messages = build_prompt(criterion, masked, shown)
    digest = prompt_sha256(messages)

    verdicts: list[_Verdict] = []
    discarded = 0
    for judge_id in JUDGE_IDS[:2]:
        # **서로의 응답을 보여주지 않는다.** 같은 메시지를 각각 따로 보낸다.
        output = _ask(client, settings, messages, digest, active_budget)
        if output is None:
            discarded += 1
            continue
        spans = keep_quotes(output, masked)
        if not spans:
            discarded += 1  # 근거 없는 점수는 쓰지 않는다
            continue
        verdicts.append(_Verdict(judge_id, output, spans))

    threshold = settings.judge_disagreement_threshold
    third_called = (
        len(verdicts) == 2
        and abs(verdicts[0].output.score - verdicts[1].output.score) >= threshold
    )
    if third_called:
        output = _ask(client, settings, messages, digest, active_budget)
        if output is None:
            discarded += 1
        else:
            spans = keep_quotes(output, masked)
            if spans:
                verdicts.append(_Verdict(JUDGE_IDS[2], output, spans))
            else:
                discarded += 1

    if not verdicts:
        raise NoGroundedResponse(
            f"{candidate.candidate_id} / {criterion.id} — 응답 {discarded}건이 모두 "
            "쓸 만한 인용을 내지 못했다. 근거 없는 점수를 만들지 않는다"
        )

    value = fmean(verdict.output.score for verdict in verdicts)

    score = Score(
        id=f"S-{candidate.candidate_id}-{criterion.id}",
        criterion_id=criterion.id,
        candidate_id=candidate.candidate_id,
        value=value,
        layer=JUDGMENT_LAYER,
        judge_id=f"panel-{len(verdicts)}",
        rationale=_rationale(verdicts, discarded, threshold, value, third_called),
    )
    graph.add(score)

    # 인용 → Evidence → Link. 같은 구간을 두 심사위원이 짚으면 Evidence는 하나다.
    made: dict[tuple[int, int], Evidence] = {}
    for verdict in verdicts:
        for span in verdict.spans:
            key = (span.start, span.end)
            evidence = made.get(key)
            if evidence is None:
                evidence = Evidence(
                    id=f"E-{candidate.candidate_id}-{criterion.id}-{len(made) + 1:02d}",
                    resume_id=candidate.candidate_id,
                    span=span,
                    # **모델이 준 text가 아니라 원문 슬라이스다.** 이유는 모듈 docstring.
                    quote=resume_text[span.start : span.end],
                )
                graph.add(evidence)
                graph.link(evidence.id, "supports", criterion.id)
                graph.link(score.id, "grounded_in", evidence.id)
                made[key] = evidence

    return score
