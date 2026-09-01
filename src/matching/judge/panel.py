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
from .prompt import MAX_QUOTES, ScoringExample, build_prompt, examples_for, prompt_sha256
from .schema import RESPONSE_FORMAT, JudgeCall, JudgeOutput

JUDGMENT_LAYER = "judgment"

# 심사위원 이름. **3명 고정이 아니다** — 세 번째는 이견이 있을 때만 부른다.
JUDGE_IDS: tuple[str, str, str] = ("judge-1", "judge-2", "judge-3")

# 재현 조건. 반복 안정성을 재려면 남는 분산이 **모델 자체의 것**이어야 한다.
JUDGE_TEMPERATURE = 0.0

USAGE_FILENAME = ".judge_usage.json"

# 사용량을 파일에 흘려 넣는 주기. **죽어도 잃는 양의 상한**이 이 값이다.
# 1로 두면 호출마다 파일을 다시 읽고 쓰게 되고, 크게 두면 사고 때 잃는 양이 는다.
# 완주 1회가 100~200회이므로 20이면 최악 손실이 전체의 10~20%다.
FLUSH_EVERY = 20


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
    """비용 한 벌. **앞의 여섯은 「이번 실행」, 뒤의 셋은 「누적」이다.**

    `RunResult.cost`에 실려 화면에 「이 결과: n회 호출 · $x · 모델 <이름>」으로 나간다.
    화면이 말하는 「이 결과」는 **이번 실행**이므로 `calls`·`usd`의 뜻을 바꾸지 않았다 —
    누적을 거기 담으면 한 번 채점한 결과에 지난 실행의 비용이 얹혀 화면이 거짓말을 한다.
    누적은 아래 세 필드로 따로 준다 (2026-09-01 예산 사고 이후 추가, 기본값 있음 —
    이전에 저장된 `result.json`도 그대로 읽힌다).
    """

    model_config = ConfigDict(extra="forbid")

    calls: int  # 이번 실행
    in_tokens: int  # 이번 실행
    out_tokens: int  # 이번 실행
    usd: float  # 이번 실행
    # 단가가 설정에 없으면 USD 환산이 0이다. **그 사실이 결과에 드러나야 한다** —
    # 안 그러면 「$0」이 「공짜였다」로 읽힌다.
    priced: bool
    # 화면의 「이 결과: n회 호출 · $x · 모델 <이름>」 마지막 칸 (`step9.md` 8번).
    # 호출수와 금액만으로는 **같은 값이 어느 모델에서 나온 것인지** 알 수 없다 —
    # 모델을 바꾸면 단가도 판정도 달라지는데 결과 JSON에는 흔적이 안 남는다.
    # 값은 `settings.judge_model`에서 온다. 코드에 박지 않는다 (§2).
    model: str = ""
    # --- 여기부터 누적. 상한이 걸리는 기준이 이쪽이다 ---
    total_calls: int = 0  # 이전 실행들 + 이번 실행
    total_usd: float = 0.0
    limit: int = 0  # `settings.max_total_calls`


def _read_usage(path: Path | None) -> dict[str, float]:
    """기록 파일을 읽는다. **없거나 깨졌으면 0이다 — 예외를 던지지 않는다.**

    여기서 던지면 상태 파일 하나가 깨졌다는 이유로 채점 전체가 죽는다. 0으로 읽는 쪽의
    대가는 「난간이 한 번 리셋된다」인데, 그건 파일이 실제로 깨졌을 때뿐이고 그 경우엔
    애초에 누적을 알 방법이 없다. 대신 **음수·불리언 같은 값은 안 받는다** — 그런 값이
    들어오면 상한을 우회하는 통로가 된다.
    """
    empty = {"calls": 0.0, "in_tokens": 0.0, "out_tokens": 0.0, "usd": 0.0}
    if path is None or not path.exists():
        return empty
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty
    if not isinstance(loaded, dict):
        return empty

    usage = dict(empty)
    for key in usage:
        value = loaded.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if value >= 0:
            usage[key] = float(value)
    return usage


class _Unset:
    """「인자를 안 줬다」와 「`None`을 줬다」를 가르는 표식. 두 뜻이 다르다."""


_UNSET = _Unset()


class CallBudget:
    """호출수·토큰·환산 USD **누적**. 상한은 `settings.max_total_calls`에서 온다.

    ## 2026-09-01 사고 — 난간이 세 번 리셋됐다

    이 클래스는 처음부터 「누적」이라고 적혀 있었지만 **`__init__`이 기록 파일을 읽지
    않았다.** `save()`만 누적하고 `spend()`의 상한 검사는 **그 프로세스가 이번에 쓴
    양**만 봤다. 그래서 하네스가 step 9를 재시도하며 새 프로세스를 띄울 때마다
    **각자 600회 한도를 새로 받았고**, 세 시간 만에 496회가 더 나가 누적 645회 ·
    약 $5.95가 됐다. 과제 예산은 $5다.

    **문서와 동작이 어긋나면 문서가 아니라 동작이 사고를 낸다.** 그래서 지금은
    `__init__`이 파일을 읽어 `prior_*`에 담고, 상한은 **누적 기준**으로 검사한다.
    난간은 프로세스가 아니라 **파일**에 붙어 있어야 한다 — 프로세스는 몇 개든 뜬다.

    ## 「이번 실행」과 「누적」을 섞지 않는다

    - `calls`·`in_tokens`·`out_tokens`·`usd()` — **이번 실행분.** 화면의 「이 결과:
      n회 호출」이 이 값이다. 여기에 누적을 담으면 한 번 채점한 결과에 지난 실행의
      비용이 얹혀 화면이 거짓말을 한다
    - `prior_*` · `total_calls()` · `total_usd()` — **누적분.** 상한은 이쪽으로 잰다

    ## `path=None`은 「기록하지 않는다」이다

    인자를 **안 주면** 기본 경로(`data/.judge_usage.json`)를 쓰고, **`None`을 주면**
    읽지도 쓰지도 않는다. 둘을 같게 두면 단위 테스트가 **실물 사용량 파일을 읽어**
    개발 기계의 상태에 따라 초록·빨강이 갈린다. 실제로 그렇게 돼 있었다.

    완주 1회 예상은 ≈117회다 (`docs/COST_BUDGET.md` §1). **상한에 닿으면 예외를
    던진다** — 심사위원을 조용히 1명으로 줄이면 그 결과가 어떤 설계로 나온 것인지
    아무 데도 안 남는다.
    """

    def __init__(self, settings: Settings, path: Path | str | None | _Unset = _UNSET) -> None:
        self.limit = settings.max_total_calls
        self.model = settings.judge_model
        self.price_in_per_1m = settings.price_in_per_1m
        self.price_out_per_1m = settings.price_out_per_1m
        if isinstance(path, _Unset):
            self.path: Path | None = default_data_dir() / USAGE_FILENAME
        elif path is None:
            self.path = None  # 기록하지 않는다 (테스트·일회성 측정)
        else:
            self.path = Path(path)

        # **여기서 파일을 읽는 것이 이 클래스의 전부다.** 안 읽으면 상한이 프로세스마다
        # 리셋된다 (위 사고).
        prior = _read_usage(self.path)
        self.prior_calls = int(prior["calls"])
        self.prior_in_tokens = int(prior["in_tokens"])
        self.prior_out_tokens = int(prior["out_tokens"])
        self.prior_usd = prior["usd"]

        self.calls = 0
        self.in_tokens = 0
        self.out_tokens = 0
        self.records: list[JudgeCall] = []

        # **이미 파일에 적어 넣은 이번 실행분.** `save()`가 델타만 더하게 하는 표식이고,
        # 그래서 `save()`를 몇 번 부르든 같은 호출이 두 번 세어지지 않는다.
        self._saved_calls = 0
        self._saved_in = 0
        self._saved_out = 0
        self._saved_usd = 0.0

    def total_calls(self) -> int:
        """이전 실행들 + 이번 실행. **상한이 걸리는 기준이다.**"""
        return self.prior_calls + self.calls

    def total_usd(self) -> float:
        return self.prior_usd + self.usd()

    def remaining(self) -> int:
        """남은 호출 수. 요청을 **보내기 전에** 본다. 누적 기준이다."""
        return max(0, self.limit - self.total_calls())

    def over_limit_message(self) -> str:
        """상한 초과 문구. **이번 실행분과 누적분을 함께 적는다.**

        「600을 넘겼다」만 뜨면 다음 사람이 그걸 **회차 상한**으로 읽고, 프로세스를
        새로 띄우면 되는 줄 안다 — 그게 이번 사고에서 실제로 일어난 일이다.
        경로 전체가 아니라 **파일 이름만** 적는다: 이 문구는 HTTP 429 본문으로 나가고,
        절대 경로에는 사용자 계정 이름이 들어 있다.
        """
        return (
            f"호출 상한 {self.limit}회 — 누적 {self.total_calls()}회 "
            f"(이전 실행들 {self.prior_calls}회 + 이번 실행 {self.calls}회). "
            f"누적은 {USAGE_FILENAME}에 남는다. **프로세스를 새로 띄워도 리셋되지 않는다.** "
            "정말 더 써야 하면 settings.max_total_calls를 사람이 올린다"
        )

    def spend(self, in_tokens: int, out_tokens: int, call: JudgeCall | None = None) -> None:
        """호출 1건을 기록한다. **누적이** 상한을 넘겼으면 예외를 던진다.

        기록을 먼저 하고 예외를 던진다 — 「몰래 줄이지 않는다」의 반대편은
        **「몰래 지우지 않는다」**다. 넘긴 그 호출도 사용량에 남는다.

        ## 2026-09-01 두 번째 사고 — 죽으면 쓴 만큼이 회계에서 사라졌다

        상한을 누적으로 고친 뒤에도 **`save()`는 실행이 정상 종료할 때만 불렸다.**
        넥슨 채점이 상한에 걸려 죽으면서 그 실행이 쓴 **55회가 파일에 안 남았고**,
        파일은 745회를 아는데 실제로는 800회를 쓴 상태가 됐다. 다음 실행이 그 55회를
        **다시 쓸 수 있는 몫으로 오해한다.**

        그래서 여기서 저장한다 — **`FLUSH_EVERY`회마다, 그리고 예외를 던지기 직전에.**
        「난간이 서 있다」는 상한을 지키는 것만이 아니라 **얼마를 썼는지 잃지 않는 것**
        까지다. 못 센 지출은 없는 지출이 아니다.
        """
        self.calls += 1
        self.in_tokens += max(0, in_tokens)
        self.out_tokens += max(0, out_tokens)
        if call is not None:
            self.records.append(call)
        if self.total_calls() > self.limit:
            self.save()  # 넘긴 그 호출까지 남기고 나서 던진다
            raise BudgetExceeded(self.over_limit_message())
        if self.calls - self._saved_calls >= FLUSH_EVERY:
            self.save()

    def usd(self) -> float:
        """**이번 실행**을 설정의 단가로 환산한 값. 단가를 코드에 박지 않는다 (§2)."""
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
            model=self.model,
            total_calls=self.total_calls(),
            total_usd=self.total_usd(),
            limit=self.limit,
        )

    def save(self) -> None:
        """`data/.judge_usage.json`에 **누적**한다. 로컬 상태라 커밋되지 않는다.

        `__init__`이 읽어 둔 `prior_*`를 쓰지 않고 **저장 시점에 파일을 다시 읽는다.**
        그 사이에 다른 프로세스가 쓴 양을 덮어쓰지 않기 위해서다 — 이번 사고가 정확히
        「여러 프로세스가 동시에 돈다」는 상황이었다.

        **여러 번 불러도 안전하다.** 파일에 이미 적은 몫(`_saved_*`)을 빼고 **델타만**
        더한다. 이게 없으면 실행 중간에 한 번, 끝나고 또 한 번 부를 때 같은 호출이
        두 번 세어져 **회계가 반대 방향으로 틀린다.**
        """
        if self.path is None:
            return  # 기록하지 않기로 하고 만든 예산이다

        previous = _read_usage(self.path)
        body = {
            "calls": int(previous["calls"]) + (self.calls - self._saved_calls),
            "in_tokens": int(previous["in_tokens"]) + (self.in_tokens - self._saved_in),
            "out_tokens": int(previous["out_tokens"]) + (self.out_tokens - self._saved_out),
            "usd": previous["usd"] + (self.usd() - self._saved_usd),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        self._saved_calls = self.calls
        self._saved_in = self.in_tokens
        self._saved_out = self.out_tokens
        self._saved_usd = self.usd()


class _Verdict:
    """심사위원 한 명의 결과. 모듈 밖으로 내보내지 않는다."""

    __slots__ = ("judge_id", "output", "spans")

    def __init__(self, judge_id: str, output: JudgeOutput, spans: list[Span]) -> None:
        self.judge_id = judge_id
        self.output = output
        self.spans = spans


def _strip_ws(text: str) -> str:
    return "".join(text.split())


def _ws_index(text: str) -> tuple[str, list[int]]:
    """공백을 뺀 글과, 그 글의 각 글자가 원문 몇 번째였는지의 표."""
    stripped: list[str] = []
    positions: list[int] = []
    for index, char in enumerate(text):
        if not char.isspace():
            stripped.append(char)
            positions.append(index)
    return "".join(stripped), positions


def _locate(quote_text: str, table: tuple[str, list[int]]) -> tuple[int, int] | None:
    """인용문을 **원문에서 다시 찾는다.** 정확히 한 번 나올 때만 위치를 준다.

    **여러 곳에 나오면 버린다** — 어느 쪽을 가리켰는지 모르는 채 하나를 고르면
    그건 근거가 아니라 추측이다. 없어도 버린다(지어낸 인용).
    """
    haystack, positions = table
    needle = _strip_ws(quote_text)
    if not needle:
        return None
    first = haystack.find(needle)
    if first < 0 or haystack.find(needle, first + 1) != -1:
        return None
    return positions[first], positions[first + len(needle) - 1] + 1


def keep_quotes(output: JudgeOutput, masked_resume: str) -> list[Span]:
    """받아들일 인용만 남긴다. **어긋난 것은 그 인용만 버린다.**

    대조는 심사위원이 실제로 본 글(마스킹된 글)로 한다. 마스킹은 길이를 보존하므로
    오프셋은 원문과 같고, 받아들인 구간에는 마스킹 문자가 없으므로 그 구간을 원문에서
    다시 잘라도 같은 문자열이 나온다.

    **모델이 준 `start`·`end`를 최종 권위로 두지 않는다.** 그 자리가 인용문과 안 맞으면
    인용문을 원문에서 **우리가 다시 찾는다**(`_locate`). 모델은 글자 수를 정확히 세지
    못한다 — 실측에서 인용 내용은 정확한데 `end`만 7글자 짧아 그 지원자의 채점이
    통째로 죽었고, 심사위원 2명이 다 그러면 **12명짜리 실행 전체가 죽는다.**

    이 완화가 검산 G2를 느슨하게 만들지 않는다. 오히려 반대다 —
    **위치를 모델의 주장에서 받는 대신 원문 대조로 정한다.** 원문에 없으면 여전히 버리고,
    **여러 곳에 나와도 버린다.** 「인용이 원문에 실재한다」는 불변식은 그대로다.
    """
    kept: list[Span] = []
    seen: set[tuple[int, int]] = set()
    table = _ws_index(masked_resume)
    for quote in output.quotes:
        if len(kept) >= MAX_QUOTES:
            break
        start, end = quote.start, quote.end
        span: tuple[int, int] | None = None
        if 0 <= start < end <= len(masked_resume) and _strip_ws(
            masked_resume[start:end]
        ) == _strip_ws(quote.text):
            span = (start, end)  # 모델이 맞게 짚었다
        else:
            span = _locate(quote.text, table)  # 어긋났다 — 원문에서 다시 찾는다
        if span is None:
            continue
        if MASK_CHAR in masked_resume[span[0] : span[1]]:
            continue  # 가린 자리를 인용했다. 원문에서 다시 자르면 가린 값이 되살아난다
        if span in seen:
            continue
        seen.add(span)
        kept.append(Span(start=span[0], end=span[1]))
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
        # **여기서 막으면 돈이 한 푼도 안 나간다.** 누적이 이미 상한이면 요청 자체를
        # 보내지 않는다 — `spend()`의 검사는 응답을 받은 뒤라 그때는 이미 과금됐다.
        raise BudgetExceeded(f"이미 다 썼다 — 요청을 보내지 않는다. {budget.over_limit_message()}")

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
    # 예시도 **항목의 갈래**를 따른다. 충족형 항목에 서술형 예시를 주면 지시문과 기준점이
    # 충족 여부를 물어도 예시가 「행동과 성과를 써야 5점」이라고 말한다.
    shown = list(examples) if examples is not None else list(examples_for(criterion))

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
        len(verdicts) == 2 and abs(verdicts[0].output.score - verdicts[1].output.score) >= threshold
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
        # **아무도 못 냈으면 딱 한 번 더 묻는다.** 무한 재시도가 아니라 1회다.
        #
        # 온도가 0인데도 응답이 매번 같지 않다 — `seed`를 안 주기 때문이고, 실제로
        # 같은 (지원자, 항목)이 한 번은 쓸 만한 인용을 내고 한 번은 못 냈다. 이 실패는
        # **「근거가 없다」가 아니라 「모델이 이번에 좌표를 흘렸다」**이고, 그 차이를
        # 구분하지 않으면 12명짜리 실행이 운 나쁜 한 쌍 때문에 통째로 죽는다. 실제로 죽었다.
        #
        # 되묻는 비용은 **실패한 항목당 1회**뿐이라 예산에 거의 안 걸린다. 그래도
        # 여전히 못 내면 예외를 던진다 — 「근거 없는 점수는 만들지 않는다」는 그대로다.
        retried = _ask(client, settings, messages, digest, active_budget)
        if retried is not None:
            spans = keep_quotes(retried, masked)
            if spans:
                verdicts.append(_Verdict(JUDGE_IDS[0], retried, spans))
        if not verdicts:
            discarded += 1

    if not verdicts:
        raise NoGroundedResponse(
            f"{candidate.candidate_id} / {criterion.id} — 응답 {discarded}건이 모두 "
            "쓸 만한 인용을 내지 못했다(되물음 1회 포함). 근거 없는 점수를 만들지 않는다"
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
