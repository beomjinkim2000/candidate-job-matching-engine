"""인용을 원문에 붙이는 규칙 — `keep_quotes`.

**왜 이 파일이 따로 있나.** 2026-09-01 완주 중 실행 전체가 죽었다. 심사위원 모델이
인용 **내용은 정확히** 냈는데 `end` 오프셋을 7글자 짧게 줬고, 그래서 그 인용이 버려졌고,
심사위원 2명이 다 그러자 「근거 없는 점수는 만들지 않는다」가 발동해 **12명짜리 채점이
통째로 중단**됐다. 온도가 0.0이라 다시 돌려도 같은 자리에서 죽는다.

여기서 고른 케이스는 **「모델을 얼마나 믿는가」의 경계**다. 커버리지가 아니라 그 경계가
어디인지를 적어 두는 것이 목적이다:

- 모델이 맞게 짚으면 그대로 쓴다 (기존 동작을 깨지 않았다는 확인)
- **어긋나면 원문에서 우리가 다시 찾는다** ← 이번 사고
- 원문에 없으면 버린다 (지어낸 인용)
- **여러 곳에 나와도 버린다** ← 완화가 여기서 멈춘다는 선
- 가린 자리를 인용하면 버린다 (마스킹 우회 차단)
"""

from matching.judge.panel import keep_quotes
from matching.judge.schema import JudgeOutput, QuoteRef
from matching.scorer.mask import MASK_CHAR

RESUME = (
    "머리말입니다.\n매대 개편안을 냈고 대리점주가 반대했습니다.\n"
    "기준을 함께 정한 뒤 시범 적용을 시작했습니다.\n맺음말."
)


def _out(*quotes: QuoteRef) -> JudgeOutput:
    return JudgeOutput(quotes=list(quotes), reasoning="근거", score=3)


def _at(fragment: str) -> tuple[int, int]:
    start = RESUME.index(fragment)
    return start, start + len(fragment)


def test_모델이_맞게_짚으면_그_위치를_그대로_쓴다():
    """기존 동작. 완화를 넣으면서 이쪽이 바뀌지 않았는지 본다."""
    start, end = _at("기준을 함께 정한 뒤 시범 적용을 시작했습니다.")
    spans = keep_quotes(_out(QuoteRef(start=start, end=end, text=RESUME[start:end])), RESUME)
    assert [(s.start, s.end) for s in spans] == [(start, end)]


def test_줄바꿈만_다르면_받아들인다():
    """모델은 줄바꿈을 공백으로 적어 온다. 공백 차이로 근거를 버리지 않는다."""
    start, end = _at("매대 개편안을 냈고 대리점주가 반대했습니다.\n기준을 함께 정한 뒤")
    quoted = RESUME[start:end].replace("\n", " ")
    spans = keep_quotes(_out(QuoteRef(start=start, end=end, text=quoted)), RESUME)
    assert [(s.start, s.end) for s in spans] == [(start, end)]


def test_end가_짧아도_원문에서_다시_찾아_붙인다():
    """**이번 사고 그 자체.** 내용은 맞고 끝 위치만 모자란 경우.

    받아들이되 **모델이 준 (start, end)가 아니라 원문에서 찾은 위치**를 쓴다 —
    그래야 그 span으로 원문을 다시 잘랐을 때 인용문과 같은 글이 나온다.
    """
    text = "기준을 함께 정한 뒤 시범 적용을 시작했습니다."
    start, end = _at(text)
    truncated = end - 7  # 모델이 「시작했습니다.」를 못 세었다
    spans = keep_quotes(_out(QuoteRef(start=start, end=truncated, text=text)), RESUME)
    assert [(s.start, s.end) for s in spans] == [(start, end)]
    assert RESUME[spans[0].start : spans[0].end] == text


def test_원문에_없는_인용은_버린다():
    """지어낸 인용. 완화해도 여기는 안 통과해야 한다."""
    fake = QuoteRef(start=0, end=10, text="한 번도 쓴 적 없는 문장입니다.")
    assert keep_quotes(_out(fake), RESUME) == []


def test_여러_곳에_나오는_인용은_버린다():
    """**완화가 멈추는 선.** 어느 쪽을 가리켰는지 모르는 채 하나를 고르면 추측이다."""
    doc = "같은 문장이다.\n중간 글.\n같은 문장이다."
    spans = keep_quotes(_out(QuoteRef(start=99, end=120, text="같은 문장이다.")), doc)
    assert spans == []


def test_가린_자리를_인용하면_버린다():
    """마스킹 우회 차단. 원문에서 다시 자르면 가린 값이 되살아난다."""
    doc = f"성명: {MASK_CHAR * 3}\n매대 개편안을 냈습니다."
    fragment = f"성명: {MASK_CHAR * 3}"
    quote = QuoteRef(start=0, end=len(fragment), text=fragment)
    assert keep_quotes(_out(quote), doc) == []
