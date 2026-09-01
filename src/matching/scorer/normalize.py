"""표기 흔들림을 지우고, 대조할 표현을 뽑는다 — **사실 층 매처의 자.**

두 가지를 한다.

1. **정규화** — 대소문자·공백·하이픈·가운뎃점을 지운 문자열을 만든다. OCR이 읽은 공고는
   띄어쓰기가 무너져 있고(`·AX기술(AI, Cloud, Data 등)에기본적인관심과`) 이력서는
   정상 띄어쓰기라, 공백을 살려 두면 같은 낱말이 안 맞는다.
2. **표현 뽑기** — 조건 문구에서 「이력서에서 찾아볼 문자열」을 고른다.

## 정규화는 **문자 하나씩** 한다 — 오프셋을 되짚어야 하기 때문

`Evidence.span`은 **이력서 원문 오프셋**이어야 검산 G2를 통과한다. 정규화한 문자열에서
찾은 자리를 원문 자리로 되돌리려면 「정규화 문자 i가 원문 몇 번째에서 왔나」를 들고 있어야
한다. 그래서 통째로 `str.lower()`·`NFKC`를 걸지 않고 한 글자씩 처리하며 `index_map`을
같이 쌓는다.

**대가**: 여러 글자가 합쳐지는 정규화(자모 분리된 한글의 결합 등)는 일어나지 않는다.
합치려면 어느 원문 글자에서 왔는지가 모호해지고, 그러면 되짚기가 깨진다. 되짚기를
포기하느니 그 정규화를 포기한다.

## 표현을 뽑는 규칙 — 직군 어휘를 쓰지 않는다

보는 것은 **문자의 종류**뿐이다 (`rubric/build.py`의 `is_countable()`과 같은 성격).

| 순 | 뽑는 것 | 왜 |
|---|---|---|
| 1 | 라틴 토큰 2자 이상 | 한국어 공고에서 라틴 문자는 거의 **도구·표준·자격의 고유명**이다 |
| 2 | (1이 없을 때만) 한글 덩어리 + 숫자 토큰 | 라틴 토큰이 신호다. 산문을 섞으면 묽어진다 |

**이 규칙이 틀리는 곳을 적어 둔다.** 산문 조건에 연도가 하나 끼어 있으면
(`정규4년제 대학을 졸업했거나2027년2월까지…`) 그 조건은 사실 층으로 오고, 여기서
`4년`·`2027년`·`2월`과 한글 덩어리를 대조하게 된다 — **조건의 뜻을 재는 것이 아니다.**
`is_countable()`의 알려진 오분류가 점수에까지 이어지는 자리이고, 고치려면 상태 어휘 사전이
필요한데 사전은 목록 밖 표현에 **조용히** 실패한다. 지금은 틀리는 방향이 보이게 두고
`layer`를 결과에 실어 확인할 수 있게 한다.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from ..model.objects import Span

# 정규화에서 **버리는** 문자. 공백류·하이픈류·가운뎃점류다.
# 마침표·괄호·쉼표는 버리지 않는다 — `3.82/4.5`와 `C++`처럼 문자 자체가 뜻을 나르는 자리가
# 있어서, 넓게 버리면 없던 일치가 생긴다.
_DROPPED = frozenset(
    " \t\n\r\f\v 　"  # 공백류
    "-‐‑‒–—―−－"  # 하이픈·대시
    "·•‧∙⋅・･"  # 가운뎃점·불릿
    "※*"
)

# 라틴 토큰. `C++`·`C#`·`.NET`처럼 기호가 붙는 이름을 통째로 잡는다.
_LATIN_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9+#._/-]*")
# 한 글자짜리는 문장 부스러기일 확률이 높다 (`build.py`의 `_LATIN_MIN`과 같은 기준).
_LATIN_MIN = 2

# 숫자 + 뒤에 붙는 단위. `2026년`·`12월`·`3개월`·`1종`·`900점`.
# 단위를 함께 잡는 것이 요점이다 — 맨 숫자만 뽑으면 `3`이 아무 데나 걸린다.
_NUMERIC_TOKEN = re.compile(r"\d[\d,]*(?:\.\d+)?\s*(?:개월|년|월|일|급|종|점|회|주|명|건|%)")

# 한글 덩어리. 2자 미만은 조사·부스러기다.
_HANGUL_RUN = re.compile(r"[가-힣]{2,}")

# 덩어리 끝에서 떼는 조사. **문법 형태소지 직군 어휘가 아니다.**
# 긴 것부터 본다 — `으로`를 `로`보다 먼저 떼지 않으면 `으`가 남는다.
_JOSA_TAILS: tuple[str, ...] = (
    "으로서", "으로써", "에서의", "에게서", "이라는", "라는",
    "으로", "에서", "에게", "까지", "부터", "이나", "와의", "과의",
    "등의", "등을", "등에", "등과", "등",
    "을", "를", "이", "가", "은", "는", "의", "에", "와", "과", "도", "만", "및",
)

# 뜻을 나르지 않는 기능어. **문법·연결 어휘만 넣는다** — 직군·기술 낱말을 여기 넣기
# 시작하면 그게 곧 하드코딩이다 (과제 CRITICAL).
_FUNCTION_WORDS: frozenset[str] = frozenset({
    "그리고", "또는", "혹은", "이상", "이하", "미만", "초과", "경우", "기타", "해당",
    "여러", "각종", "다양한", "모든", "위한", "위해", "통한", "통해", "대한", "대해",
    "관련", "이런", "저런", "같은", "다른", "우리", "자신", "직접", "가능", "가능한",
    "있는", "없는", "하는", "하시는", "하실", "되는", "이며", "이고", "분들", "등등",
})

# 덩어리가 이 어미로 끝나면 서술어다. **어휘 목록이 아니라 어미 규칙**이라 직군을 안 탄다.
_PREDICATE_TAILS: tuple[str, ...] = (
    "하는", "하시는", "하시며", "되는", "있는", "없는", "한분", "하신", "하실",
    "합니다", "습니다", "가능한", "가능한분", "이신", "지는", "지고",
)

# 「N년 이상」처럼 **비교 기준이 붙은 수치**만 포화함수로 간다.
# `4년제`·`2027년2월`이 여기 안 걸리는 것이 이 정규식의 요점이다 — 비교어(`이상`·`+`)를
# 반드시 요구한다. 없으면 그건 요구 연차가 아니라 그냥 숫자다.
_REQUIRED_DURATION = re.compile(r"(\d+(?:\.\d+)?)\s*(년|개월)\s*(?:이상|\+|이상의)")

# 이력서에서 **기간**을 읽는다. `(14개월)`·`3년간`처럼 길이를 말하는 표현만 본다.
_HAVE_DURATION = re.compile(r"(\d+(?:\.\d+)?)\s*(년|개월)")
# 1900 이상은 달력 연도(`2026년`)다. 기간이 아니다.
_CALENDAR_YEAR_MIN = 1900

MONTHS_PER_YEAR = 12

DEFAULT_ALIASES_FILENAME = "aliases.json"


# --- 정규화 ---------------------------------------------------------------


@dataclass(frozen=True)
class Normalized:
    """정규화한 문자열과, 그 문자열의 자리를 원문 자리로 되돌리는 표."""

    raw: str
    text: str
    index_map: tuple[int, ...]

    def find_all(self, term: str, limit: int | None = None) -> list[Span]:
        """`term`이 나오는 자리를 **원문 오프셋**으로 돌려준다.

        겹치지 않게 훑는다. 반환한 `Span`으로 원문을 자르면 검산 G2가 대조할
        `Evidence.quote`가 그대로 나온다.
        """
        needle = normalize(term)
        if not needle or not self.text:
            return []

        spans: list[Span] = []
        cursor = self.text.find(needle)
        while cursor != -1:
            stop = cursor + len(needle)
            spans.append(
                Span(start=self.index_map[cursor], end=self.index_map[stop - 1] + 1)
            )
            if limit is not None and len(spans) >= limit:
                break
            cursor = self.text.find(needle, stop)
        return spans

    def contains(self, term: str) -> bool:
        return bool(self.find_all(term, limit=1))


def normalize_with_map(text: str) -> Normalized:
    """한 글자씩 정규화하며 원문 자리를 함께 기록한다."""
    chars: list[str] = []
    index_map: list[int] = []
    for position, char in enumerate(text):
        if char in _DROPPED:
            continue
        for folded in unicodedata.normalize("NFKC", char).lower():
            if folded in _DROPPED:
                continue
            chars.append(folded)
            index_map.append(position)
    return Normalized(raw=text, text="".join(chars), index_map=tuple(index_map))


def normalize(text: str) -> str:
    """되짚기가 필요 없을 때 쓰는 짧은 길."""
    return normalize_with_map(text).text


# --- 약어 사전 -------------------------------------------------------------


def default_aliases_path() -> Path:
    # src/matching/scorer/normalize.py → parents[3] 이 레포 루트
    return Path(__file__).resolve().parents[3] / "data" / DEFAULT_ALIASES_FILENAME


def load_aliases(path: Path | str | None = None) -> dict[str, tuple[str, ...]]:
    """`data/aliases.json`을 읽어 **양방향 묶음표**로 만든다.

    파일 형태는 `{"대표 표기": ["다른 표기", ...]}`다. 어느 표기로 들어와도 같은 묶음이
    나오도록 대표와 다른 표기를 모두 열쇠로 넣는다 — 공고가 `Cloud`라 쓰고 이력서가
    `클라우드`라 써도, 그 반대여도 같게 동작해야 한다.

    **파일이 없거나 비어 있어도 동작한다.** 없으면 빈 표를 돌려주고 매처는 표기 그대로만
    대조한다. 지금 저장소의 `aliases.json`은 **비어 있다** — 우리가 확보한 두 공고에
    맞는 항목을 채워 넣으면 그건 데이터가 아니라 그 공고에 맞춘 손질이 된다. 채우는 것은
    운영의 일이고, 그때 무엇을 잃고 있었는지는 `docs/TRADEOFFS.md`에 적는다.
    """
    target = Path(path) if path is not None else default_aliases_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}

    groups: dict[str, tuple[str, ...]] = {}
    for head, others in raw.items():
        members = [head, *(others if isinstance(others, list) else [])]
        cleaned = tuple(dict.fromkeys(str(m) for m in members if str(m).strip()))
        for member in cleaned:
            key = normalize(member)
            if key:
                groups[key] = cleaned
    return groups


def expand(term: str, aliases: dict[str, tuple[str, ...]] | None) -> tuple[str, ...]:
    """표기 하나를 같은 뜻의 표기 묶음으로 넓힌다. 사전이 없으면 자기 자신뿐."""
    if not aliases:
        return (term,)
    group = aliases.get(normalize(term))
    if not group:
        return (term,)
    return tuple(dict.fromkeys((term, *group)))


# --- 표현 뽑기 -------------------------------------------------------------


def _strip_josa(chunk: str) -> str:
    for tail in _JOSA_TAILS:
        if len(chunk) > len(tail) + 1 and chunk.endswith(tail):
            return chunk[: -len(tail)]
    return chunk


def hangul_terms(text: str) -> list[str]:
    """한글 덩어리에서 대조할 만한 것만 남긴다."""
    terms: list[str] = []
    for match in _HANGUL_RUN.finditer(text):
        chunk = _strip_josa(match.group())
        if len(chunk) < 2:
            continue
        if chunk in _FUNCTION_WORDS:
            continue
        if any(chunk.endswith(tail) for tail in _PREDICATE_TAILS):
            continue
        terms.append(chunk)
    return terms


def latin_terms(text: str) -> list[str]:
    return [
        match.group().rstrip("._-/")
        for match in _LATIN_TOKEN.finditer(text)
        if len(match.group().rstrip("._-/")) >= _LATIN_MIN
    ]


def numeric_terms(text: str) -> list[str]:
    return [re.sub(r"\s+", "", match.group()) for match in _NUMERIC_TOKEN.finditer(text)]


def key_terms(text: str) -> list[str]:
    """조건 문구에서 이력서와 대조할 표현을 뽑는다. 순서를 지키고 중복을 지운다.

    라틴 토큰이 하나라도 있으면 **그것만** 쓴다 (위 docstring 표 2행).
    """
    latin = latin_terms(text)
    if latin:
        return list(dict.fromkeys(latin))
    return list(dict.fromkeys([*hangul_terms(text), *numeric_terms(text)]))


# --- 수치 (연차) -----------------------------------------------------------


def required_years(text: str) -> float | None:
    """조건이 요구하는 연차. **「이상」류 비교어가 붙은 것만** 연차로 본다.

    없으면 `None`이고, 그때 그 항목은 포화함수를 타지 않는다 — `4년제`·`2027년2월`이
    연차 요구로 둔갑하는 것을 막는 자리다.
    """
    values = [
        float(amount) if unit == "년" else float(amount) / MONTHS_PER_YEAR
        for amount, unit in _REQUIRED_DURATION.findall(text)
    ]
    return max(values) if values else None


def duration_spans(text: str) -> list[tuple[Span, float]]:
    """이력서에서 읽은 **기간** 표현과 그 길이(년). 달력 연도는 뺀다.

    **알려진 실패**: 「총 5년 경력」 같은 요약 문장이 각 경력의 기간과 함께 있으면
    두 번 세어진다. 요약인지 항목인지는 문장 구조를 봐야 알 수 있고, 그건 판단이라
    2층의 일이다. 여기서 하는 것은 **세는 일**뿐이므로 겹침을 감수하고 세어 둔다.
    """
    found: list[tuple[Span, float]] = []
    for match in _HAVE_DURATION.finditer(text):
        amount = float(match.group(1))
        unit = match.group(2)
        if unit == "년":
            if amount >= _CALENDAR_YEAR_MIN:
                continue  # 달력 연도지 기간이 아니다
            years = amount
        else:
            years = amount / MONTHS_PER_YEAR
        found.append((Span(start=match.start(), end=match.end()), years))
    return found
