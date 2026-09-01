"""심사위원 층의 계약 시험 — **실물 API를 부르지 않는다.**

응답을 고정 픽스처로 두고 **우리 코드가 그 응답을 어떻게 다루는가**만 본다. 모델이 잘
채점하는지는 여기서 잴 수 없고(사람이 채점한 정답이 없다 — `tests/CLAUDE.md`), 잴 수
있는 것은 「모델이 이렇게 답했을 때 우리가 무엇을 하는가」다.

| 고른 케이스 | 깨지면 무엇이 거짓말이 되나 |
|---|---|
| 이견 1점에 3번째를 안 부른다 | 「2명 + 이견 시 3번째」 — 3명 고정과 구분이 안 된다 |
| 이견 2점에 3번째를 부르고 셋을 평균한다 | 위와 같음. 그리고 **평균**(토론 아님) |
| 어긋난 인용만 버린다 | 「그 인용만 버린다」 — 응답째 버리면 근거가 통째로 사라진다 |
| 인용 없는 응답은 점수에 안 쓴다 | 근거 없는 점수. 검산 G1이 어차피 막는 것을 앞에서 막는다 |
| 인용은 **원문 슬라이스**로 저장한다 | 모델 문자열을 믿으면 **G2가 자기 자신을 검사**하게 된다 |
| 가린 자리를 인용하면 버린다 | 마스킹. 원문에서 다시 자르면 가린 값이 근거 문단으로 되살아난다 |
| `JudgeCall`에 temperature 0·설정의 모델명 | 「같은 조건에서 쟀다」의 유일한 증거 |
| 상한을 넘으면 예외 | **조용히 심사위원을 줄이면** 그 결과가 어떤 설계로 나왔는지 안 남는다 |
| 사실 층 항목은 거부 | 「세는 것은 코드가, 판단하는 것은 심사위원이」 |

실물 호출이 필요한 **반복 안정성(N=11, σ≤0.5)**과 **순서 불변성**은 `@pytest.mark.live`로
분리한다. 임계값의 정본은 `tests/CLAUDE.md`의 표다.
"""

from __future__ import annotations

import json
from pathlib import Path
from statistics import fmean, stdev
from types import SimpleNamespace

import pytest

from matching.config import Settings
from matching.judge import (
    GENERIC_EXAMPLES,
    MAX_QUOTES,
    RESPONSE_SCHEMA,
    BudgetExceeded,
    CallBudget,
    JudgeOutput,
    NoGroundedResponse,
    build_prompt,
    judge_criterion,
    keep_quotes,
    order_check,
    prompt_sha256,
)
from matching.model import BBox, Criterion, EvidenceGraph, Requirement, Resume, Span, check
from matching.rubric import make_anchors
from matching.scorer import mask_sensitive

# --- 붙박이 입력 -----------------------------------------------------------

# 1번째 줄에 **가릴 것**이 있고(이름·성별·생년월일·나이), 2~4번째 줄은 가려지지 않는다.
# 가릴 것이 없으면 마스킹 관련 케이스가 통과해도 아무 말을 하지 않는다.
RESUME_TEXT = (
    "성명: 강유리 (여 / 1999.04.11 / 만 26세)\n"
    "여러 이해관계자와 조율하며 6개월간 주 2회 정기 점검 자리를 직접 운영했습니다.\n"
    "그 결과 재작업 비율이 12%에서 4%로 줄었습니다.\n"
    "협업에서 겪은 갈등을 문서로 남겨 다음 담당자에게 넘겼습니다.\n"
)

MASKED_TEXT, _MASK_SPANS = mask_sensitive(RESUME_TEXT)

CANDIDATE = Resume(candidate_id="A-01", text=RESUME_TEXT)


def _at(fragment: str) -> tuple[int, int]:
    """이력서에서 그 조각의 자리. **오프셋을 손으로 세지 않는다** — 세면 틀린다."""
    start = RESUME_TEXT.index(fragment)
    return start, start + len(fragment)


F_RUN = "6개월간 주 2회 정기 점검 자리를 직접 운영했습니다"
F_RESULT = "재작업 비율이 12%에서 4%로 줄었습니다"
F_HANDOVER = "문서로 남겨 다음 담당자에게 넘겼습니다"
# 줄바꿈을 걸치는 조각. 모델은 이걸 옮길 때 줄바꿈을 칸으로 바꾼다.
F_ACROSS_LINES = "직접 운영했습니다.\n그 결과"
F_MASKED = "강유리 (여 / 1999.04.11 / 만 26세)"


def _requirement() -> Requirement:
    return Requirement(
        id="R-01",
        text="여러 이해관계를 조율해 본 경험이 있는 분",
        kind="preferred",
        evidence_grade="E2",
        ladder_step=2,
        source_bbox=BBox(page=1, x1=10, y1=20, x2=800, y2=48, img_w=860, img_h=2533),
        source_span=Span(start=0, end=20),
    )


def _criterion(layer: str = "judgment") -> Criterion:
    requirement = _requirement()
    return Criterion(
        id="C-01",
        requirement_id=requirement.id,
        label="여러 이해관계를 조율해 본 경험이 있는 분",
        anchors=make_anchors(requirement),
        weight=20.0,
        layer=layer,  # type: ignore[arg-type]
    )


def _graph_with(criterion: Criterion) -> EvidenceGraph:
    """검산이 통과할 수 있는 최소 그래프 — 조건·항목과 `derived_from` Link까지."""
    graph = EvidenceGraph()
    requirement = _requirement()
    graph.add(requirement)
    graph.add(criterion)
    graph.link(criterion.id, "derived_from", requirement.id)
    return graph


def _settings(**overrides) -> Settings:
    base = {"judge_model": "fixed-model-2026-08-31", "max_total_calls": 200}
    base.update(overrides)
    return Settings(**base)


# --- 가짜 심사위원 ---------------------------------------------------------


def _reply(score: int, quotes, reasoning: str = "서술이 구체적인지만 보았다.") -> dict:
    return {
        "quotes": [{"start": s, "end": e, "text": t} for s, e, t in quotes],
        "reasoning": reasoning,
        "score": score,
    }


def _quote(fragment: str, text: str | None = None) -> tuple[int, int, str]:
    """이력서 조각 하나를 인용 삼중항으로. `text`를 주면 **모델이 그렇게 옮겼다**는 뜻."""
    start, end = _at(fragment)
    return start, end, text if text is not None else fragment


class _FakeCompletions:
    """준비한 응답을 순서대로 준다. **더 부르면 터진다** — 호출 횟수가 계약이다."""

    def __init__(self, replies):
        self._replies = list(replies)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._replies:
            raise AssertionError(f"준비한 응답보다 많이 불렀다 ({len(self.calls)}회)")
        reply = self._replies.pop(0)
        body = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=body))],
            usage=SimpleNamespace(prompt_tokens=1200, completion_tokens=180),
        )


def fake_client(*replies):
    completions = _FakeCompletions(replies)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions)), completions


def _judge(client, criterion=None, settings=None, graph=None):
    criterion = criterion if criterion is not None else _criterion()
    graph = graph if graph is not None else _graph_with(criterion)
    score = judge_criterion(
        criterion,
        CANDIDATE,
        RESUME_TEXT,
        graph,
        settings if settings is not None else _settings(),
        client,
    )
    return score, graph


# --- 인원 운영 -------------------------------------------------------------


def test_이견이_임계_미만이면_세번째_심사위원을_부르지_않는다():
    """2점 이상 벌어진 항목만 3번째를 부른다. **3명 고정이 아니다** —
    같은 제품군 LLM은 인원을 늘려도 독립 투표 2명분의 정보량밖에 안 나온다.
    """
    client, completions = fake_client(
        _reply(3, [_quote(F_RUN)]),
        _reply(4, [_quote(F_RESULT)]),
    )
    score, _ = _judge(client)

    assert len(completions.calls) == 2
    assert score.value == pytest.approx(3.5)
    assert score.judge_id == "panel-2"


def test_이견이_임계_이상이면_세번째를_부르고_셋의_평균이_나온다():
    """집계는 **평균**이다. 토론·합의를 시키지 않는다 — 한쪽이 동조하는 실패 모드가 있고
    합의의 정확도 이득은 실무적으로 미미했다 (`docs/TRADEOFFS.md` B-2).
    """
    client, completions = fake_client(
        _reply(2, [_quote(F_RUN)]),
        _reply(4, [_quote(F_RESULT)]),
        _reply(3, [_quote(F_HANDOVER)]),
    )
    score, _ = _judge(client)

    assert len(completions.calls) == 3
    assert score.value == pytest.approx(3.0)
    assert score.judge_id == "panel-3"
    # 서로의 응답을 보여주지 않는다 — 세 호출의 메시지가 전부 같아야 한다.
    assert len({json.dumps(c["messages"], ensure_ascii=False) for c in completions.calls}) == 1


def test_세번째를_불렀는데_버려지면_불렀다고_적는다():
    """인원 수로만 서술을 만들면 **「부르지 않았다」고 거짓말을 하게 된다** —
    3번째를 부르고 그 응답이 버려지면 심사위원은 다시 2명이기 때문이다.
    """
    client, completions = fake_client(
        _reply(2, [_quote(F_RUN)]),
        _reply(5, [_quote(F_RESULT)]),
        _reply(4, []),  # 불렀지만 근거를 못 냈다
    )
    score, _ = _judge(client)

    assert len(completions.calls) == 3
    assert score.value == pytest.approx(3.5)  # 버린 4점이 평균에 안 섞였다
    assert score.judge_id == "panel-2"
    assert "3번째를 불렀다" in score.rationale
    assert "부르지 않았다" not in score.rationale


def test_심사위원에게_사실_확인_항목을_주지_않는다():
    """`layer != judgment`면 호출 전에 거부한다. 규칙으로만 두면 호출부 한 곳이
    어기는 것을 아무도 못 본다.
    """
    client, completions = fake_client()
    with pytest.raises(ValueError, match="판단 층 항목이 아니다"):
        _judge(client, criterion=_criterion(layer="fact"))
    assert completions.calls == []


# --- 인용 다루기 -----------------------------------------------------------


def test_어긋난_인용만_버리고_나머지는_남는다():
    """응답째 버리지 않는다. 인용 하나가 틀렸다고 판단 전체를 버리면
    **근거가 통째로 사라지고** 그 자리는 아무도 안 채운다.
    """
    bad_start, bad_end = _at(F_RUN)
    client, _ = fake_client(
        _reply(
            3,
            [
                _quote(F_RUN),
                # 자리는 F_RUN인데 옮겨 적은 글자가 다르다 = 다른 곳을 가리킨 것이다.
                (bad_start, bad_end, "이력서에 없는 말을 지어냈습니다"),
            ],
        ),
        _reply(3, [_quote(F_RESULT)]),
    )
    score, graph = _judge(client)

    quotes = [ev.quote for ev in graph.evidence]
    assert F_RUN in quotes and F_RESULT in quotes
    assert "이력서에 없는 말을 지어냈습니다" not in quotes
    assert len(graph.evidence) == 2
    assert len(graph.out(score.id, "grounded_in")) == 2


def test_인용이_빈_응답은_점수에_쓰이지_않는다():
    """근거 없는 점수는 검산 G1이 어차피 막는다. 여기서 앞당겨 막고,
    **몇 건을 버렸는지 채점자 서술에 남긴다** — 조용히 넘어가지 않는다.
    """
    client, completions = fake_client(
        _reply(1, [], reasoning="근거를 못 찾았지만 낮게 주겠다."),
        _reply(5, [_quote(F_RUN)]),
    )
    score, _ = _judge(client)

    assert len(completions.calls) == 2
    assert score.value == pytest.approx(5.0)  # 버린 1점이 평균에 안 섞였다
    assert score.judge_id == "panel-1"
    assert "1건" in score.rationale


def test_모든_응답이_근거를_못_내면_점수를_만들지_않는다():
    """0점이나 1점으로 대신하지 않는다. 그러면 「근거를 못 낸 것」과
    「관련 경험이 없는 것」이 같은 점수가 되어 둘을 구별할 방법이 사라진다.
    """
    client, _ = fake_client(_reply(2, []), _reply(4, []))
    with pytest.raises(NoGroundedResponse):
        _judge(client)


def test_인용은_모델_문자열이_아니라_원문_슬라이스로_저장된다():
    """**이게 G2가 성립하는 이유다.** 모델이 준 text를 그대로 넣으면 검산이 자기 자신을
    검사하는 꼴이 된다 — 지어낸 인용과 지어낸 대조값이 서로 맞아떨어진다.

    모델은 줄바꿈을 칸으로 바꿔 옮기는 일이 잦다. 그건 자리를 틀린 게 아니므로 받아들이되,
    저장하는 것은 **원문에서 다시 자른 글자**여야 한다.
    """
    collapsed = F_ACROSS_LINES.replace("\n", " ")
    client, _ = fake_client(
        _reply(4, [_quote(F_ACROSS_LINES, collapsed)]),
        _reply(4, [_quote(F_ACROSS_LINES, collapsed)]),
    )
    _, graph = _judge(client)

    (evidence,) = graph.evidence  # 두 심사위원이 같은 구간을 짚으면 Evidence는 하나다
    assert evidence.quote == RESUME_TEXT[evidence.span.start : evidence.span.end]
    assert evidence.quote != collapsed  # 모델 문자열을 그대로 넣지 않았다
    assert "\n" in evidence.quote


def test_가린_자리를_인용하면_버린다():
    """마스킹된 구간을 원문에서 다시 자르면 **가린 값이 근거 문단으로 되살아난다.**
    그러면 마스킹은 장식이다.
    """
    start, end = _at(F_MASKED)
    client, _ = fake_client(
        _reply(5, [(start, end, MASKED_TEXT[start:end])]),
        _reply(3, [_quote(F_RUN)]),
    )
    score, graph = _judge(client)

    assert score.value == pytest.approx(3.0)
    assert all("강유리" not in ev.quote for ev in graph.evidence)
    assert len(graph.evidence) == 1


def test_인용_개수에_상한이_있다():
    """근거는 몇 개인가가 아니라 어디를 짚었는가다. 열 개면 화면이 인용으로 뒤덮인다."""
    output = JudgeOutput.model_validate(
        _reply(
            5,
            [
                _quote(F_RUN),
                _quote(F_RESULT),
                _quote(F_HANDOVER),
                _quote(F_ACROSS_LINES),
            ],
        )
    )
    assert len(keep_quotes(output, MASKED_TEXT)) == MAX_QUOTES


def test_판단_점수의_근거_사슬이_검산을_통과한다():
    """G1(근거 Link)·G2(인용 실재)·G3(조건까지 이어짐)를 한 번에 본다.
    이게 통과해야 step 7이 결과를 내보낼 수 있다.
    """
    client, _ = fake_client(_reply(3, [_quote(F_RUN)]), _reply(4, [_quote(F_RESULT)]))
    score, graph = _judge(client)

    assert check(graph, {CANDIDATE.candidate_id: RESUME_TEXT}) == []
    # 점수 → 근거 → 항목 → 조건까지 이어진다 (`graph.trace`).
    traced = {link.dst for link in graph.trace(score.id)}
    assert "R-01" in traced


# --- 재현 조건 -------------------------------------------------------------


def test_JudgeCall에_temperature_0과_설정의_모델명이_실린다():
    """결과 JSON만 보고 재현 조건을 알 수 있어야 한다. 반복 안정성을 재려면
    **남는 분산이 모델 자체의 것**이어야 하므로 temperature는 0으로 고정한다.
    """
    settings = _settings(judge_model="fixed-model-2026-08-31", judge_seed=7)
    budget = CallBudget(settings, path=None)
    client, completions = fake_client(
        _reply(3, [_quote(F_RUN)]), _reply(3, [_quote(F_RESULT)])
    )
    criterion = _criterion()
    judge_criterion(
        criterion,
        CANDIDATE,
        RESUME_TEXT,
        _graph_with(criterion),
        settings,
        client,
        budget=budget,
    )

    assert [call["temperature"] for call in completions.calls] == [0.0, 0.0]
    assert [call["model"] for call in completions.calls] == [settings.judge_model] * 2
    assert [call["seed"] for call in completions.calls] == [7, 7]

    record = budget.records[0]
    assert record.temperature == 0.0
    assert record.model == "fixed-model-2026-08-31"
    assert record.seed == 7
    # 프롬프트를 고치면 이 값이 바뀐다 — 「같은 조건에서 쟀다」의 유일한 증거다.
    assert record.prompt_sha256 == prompt_sha256(
        build_prompt(criterion, MASKED_TEXT, GENERIC_EXAMPLES)
    )


def test_모델명이_코드가_아니라_설정에서_온다():
    """`.env`에서 바꾼 값이 그대로 호출에 실린다. 코드에 박으면 버전을 못 고정한다."""
    settings = _settings(judge_model="다른-모델-이름")
    client, completions = fake_client(
        _reply(3, [_quote(F_RUN)]), _reply(3, [_quote(F_RESULT)])
    )
    _judge(client, settings=settings)
    assert completions.calls[0]["model"] == "다른-모델-이름"


# --- 프롬프트 계약 ---------------------------------------------------------


def test_프롬프트에_항목이_하나뿐이고_길이_편향_문구가_들어간다():
    """분석적 채점이다 — 여러 항목을 한 응답에 몰면 항목 사이에 후광이 생긴다.
    그리고 LLM 채점자는 길고 상세한 답을 부당하게 선호하므로 명시로 막는다.
    """
    criterion = _criterion()
    messages = build_prompt(criterion, MASKED_TEXT, GENERIC_EXAMPLES)
    system, user = messages[0]["content"], messages[1]["content"]

    assert user.count("[채점할 항목]") == 1
    assert criterion.id in user
    assert "응답 길이가 평가에 영향을 주지 않게 하라" in system
    # 기준점 1/3/5가 전부 실린다 (2·4는 일부러 비운 자리다).
    for level in (1, 3, 5):
        assert criterion.anchors[level] in user
    # 채점 예시. 루브릭 단독으로는 부족하다.
    for example in GENERIC_EXAMPLES:
        assert example.excerpt in user
    # 총점 계산을 시키지 않는다 — 합산은 step 7의 코드가 한다.
    assert "총점을 계산하지 마라" in system


def test_출력_순서가_스키마에_박혀_있다():
    """지시문으로만 부탁하지 않는다. **점수를 먼저 내면 근거가 사후 정당화**가 되므로
    구조화 출력의 필드 순서로 못 박는다.
    """
    assert list(RESPONSE_SCHEMA["properties"]) == ["quotes", "reasoning", "score"]
    assert RESPONSE_SCHEMA["required"] == ["quotes", "reasoning", "score"]
    assert list(JudgeOutput.model_fields) == ["quotes", "reasoning", "score"]


def test_프롬프트가_마스킹된_글만_들고_간다():
    """마스킹 전 글을 넣으면 이름·나이·학교가 심사위원에게 그대로 간다."""
    messages = build_prompt(_criterion(), MASKED_TEXT, GENERIC_EXAMPLES)
    assert "강유리" not in messages[1]["content"]


# --- 비용 -----------------------------------------------------------------


def test_상한을_넘으면_조용히_줄이지_않고_예외를_던진다():
    """심사위원을 말없이 1명으로 줄이면 **그 결과가 어떤 설계로 나왔는지 아무 데도
    안 남는다.** 예산이 모자라면 무엇을 버릴지는 사람이 정한다
    (`docs/COST_BUDGET.md` §5).
    """
    settings = _settings(max_total_calls=1)
    budget = CallBudget(settings, path=None)
    client, completions = fake_client(
        _reply(3, [_quote(F_RUN)]), _reply(3, [_quote(F_RESULT)])
    )
    criterion = _criterion()
    with pytest.raises(BudgetExceeded):
        judge_criterion(
            criterion,
            CANDIDATE,
            RESUME_TEXT,
            _graph_with(criterion),
            settings,
            client,
            budget=budget,
        )
    assert len(completions.calls) == 1  # 두 번째 요청은 보내지도 않았다


def test_예산이_호출수와_토큰과_USD를_누적한다(tmp_path):
    """「이 결과를 만드는 데 n회 / $x」가 화면에 뜨려면 실측이 있어야 한다.
    단가가 설정에 없으면 환산이 0이고, **그 사실이 결과에 드러나야 한다.**
    """
    budget = CallBudget(
        _settings(price_in_per_1m=2.5, price_out_per_1m=10.0), path=tmp_path / "usage.json"
    )
    budget.spend(1_000_000, 200_000)
    assert budget.usd() == pytest.approx(2.5 + 2.0)
    assert budget.report().priced is True

    free = CallBudget(_settings(), path=tmp_path / "free.json")
    free.spend(1_000_000, 200_000)
    assert free.usd() == 0.0
    assert free.report().priced is False  # 「공짜였다」가 아니라 「단가를 모른다」

    budget.save()
    budget.save()  # 두 번 저장하면 두 번 더해진다 — 누적 기록이다
    saved = json.loads((tmp_path / "usage.json").read_text(encoding="utf-8"))
    assert saved["calls"] == 2
    assert saved["in_tokens"] == 2_000_000


# --- 순서 편향 -------------------------------------------------------------


def test_순서를_뒤집어도_각자의_점수가_같다():
    """**한 호출에 지원자가 한 명뿐이라 순서가 프롬프트에 존재하지 않는다.**
    그래서 이 점검이 실제로 재는 것은 「같은 프롬프트에 같은 답이 오는가」다.
    그 구별을 지우지 않는다 — `judge/bias.py` docstring에 적어 뒀다.
    """
    other = Resume(candidate_id="A-02", text=RESUME_TEXT)
    high = [_reply(5, [_quote(F_RUN)]), _reply(5, [_quote(F_RESULT)])]
    low = [_reply(2, [_quote(F_RUN)]), _reply(2, [_quote(F_RESULT)])]
    client, completions = fake_client(*high, *low, *low, *high)  # 정방향 → 역방향
    result = order_check(_criterion(), [CANDIDATE, other], _settings(), client)

    assert result.forward == {"A-01": 5.0, "A-02": 2.0}
    assert result.forward == result.reverse
    assert result.forward_order == ["A-01", "A-02"]
    assert result.stable is True and result.rank_changes == 0
    assert result.calls == len(completions.calls) == 8


def test_순서를_뒤집었을_때_등수가_바뀌면_잡아낸다():
    """**흔들리지 않는다를 확인하려면 흔들렸을 때 잡는지부터 확인해야 한다.**
    이 케이스가 없으면 위 테스트는 `stable=True`를 늘 돌려주는 함수로도 통과한다.
    """
    other = Resume(candidate_id="A-02", text=RESUME_TEXT)
    high = [_reply(5, [_quote(F_RUN)]), _reply(5, [_quote(F_RESULT)])]
    low = [_reply(2, [_quote(F_RUN)]), _reply(2, [_quote(F_RESULT)])]
    client, _ = fake_client(*high, *low, *high, *low)  # 역방향에서 A-02가 높게 나왔다
    result = order_check(_criterion(), [CANDIDATE, other], _settings(), client)

    assert result.stable is False
    assert result.rank_changes == 2


# --- 실물 호출이 필요한 것 (기본 실행에서 빠진다) ---------------------------
#
# `pytest -m live`로만 돈다. 44회 + 8회는 완주 예산(≈117회)과 **별개**이고,
# 예산이 모자라면 이 두 개를 통째로 버린다 (`docs/COST_BUDGET.md` §5).

LIVE_POSTING = "kt-b2c"
LIVE_CANDIDATES = 2  # 상위 2명
LIVE_CRITERIA = 2  # 판단 항목 2개
SIGMA_THRESHOLD = 0.5  # **임의값** — 5점 척도의 절반 칸 (`tests/CLAUDE.md`)


def _live_cells():
    """측정 셀 4개(상위 2명 × 판단항목 2개)와 실행에 필요한 것들.

    **라벨(완벽/부분/미스)을 읽지 않는다.** 상위 2명은 사실 층 점수(결정적)로 고른다.

    항목 2개는 **`kind`가 서로 다른 것 중 배점 순**으로 고른다. 배점 순으로만 고르면
    kt-b2c에서 형식 요건 두 건(병역·해외여행)이 뽑히는데, 그 둘은 step 4가 이미
    `is_countable` **오분류**로 적어 둔 항목이고 (「step 6에서 심사위원 점수가 전원
    동일하게 나오면 그게 이 오분류의 신호다」 — `index.json` step 4) 실제로 그렇게 나왔다.
    **판단할 서술이 없는 항목에서 나온 분산 0은 「안정적이다」가 아니라 「잴 것이
    없다」는 뜻**이고, 그 0을 σ에 넣으면 step 11의 잡음 바닥이 실제보다 낮아져
    홀드아웃 판정이 너무 쉽게 통과한다.
    """
    from openai import OpenAI

    from matching.config import load_settings
    from matching.rubric import build_rubric
    from matching.scorer import score_fact

    settings = load_settings()
    if not settings.openai_api_key:
        pytest.skip("OPENAI_API_KEY가 없다 — 실물 호출 테스트는 건너뛴다")

    root = _repo_data_dir()
    posting = root / "postings" / LIVE_POSTING / "requirements.json"
    resume_dir = root / "resumes" / LIVE_POSTING
    if not posting.exists() or not resume_dir.exists():
        pytest.skip(f"{LIVE_POSTING}의 파싱 결과나 이력서가 없다")

    payload = json.loads(posting.read_text(encoding="utf-8"))
    requirements = [_requirement_from(raw) for raw in payload["requirements"]]
    duties = [_requirement_from(raw) for raw in payload.get("duties", [])]

    graph = EvidenceGraph()
    criteria = build_rubric(requirements, settings, graph, duties)
    by_id = {criterion.id: criterion for criterion in criteria}

    resumes = []
    for path in sorted(resume_dir.glob("[AB]-*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        resumes.append(Resume(candidate_id=raw["candidate_id"], text=raw["text"]))

    totals = {
        resume.candidate_id: sum(
            score.value * by_id[score.criterion_id].weight
            for score in score_fact(resume, criteria, graph, settings=settings)
        )
        for resume in resumes
    }
    top = sorted(resumes, key=lambda r: (-totals[r.candidate_id], r.candidate_id))

    heaviest_of_kind: dict[str, Criterion] = {}
    for criterion in sorted(
        (c for c in criteria if c.layer == "judgment"), key=lambda c: (-c.weight, c.id)
    ):
        kind = getattr(graph.get(criterion.requirement_id), "kind", "unknown")
        heaviest_of_kind.setdefault(kind, criterion)
    picked_criteria = sorted(
        heaviest_of_kind.values(), key=lambda c: (-c.weight, c.id)
    )[:LIVE_CRITERIA]

    # **상위 2명으로 재면 안 된다.** 처음엔 그렇게 했고 네 셀이 전부 [1,1,…] 아니면
    # [5,5,…]로 나와 σ=0이었다. 상위권은 판단 항목에서 척도의 **양 끝**에 붙는다 —
    # 흔들릴 자리가 없는 곳에서 잰 0은 「안정적이다」가 아니라 「잴 것이 없다」이고,
    # 그 0을 잡음 바닥으로 쓰면 step 11 홀드아웃이 무엇이든 통과시킨다.
    # 위·중위를 한 명씩 섞어 **척도 가운데가 표본에 들어오게** 한다. 여기서도 라벨은
    # 안 본다 — 순위는 사실 층 점수(결정적)로만 매긴다.
    picked = [top[0], top[len(top) // 2]] if len(top) > 2 else top

    client = OpenAI(api_key=settings.openai_api_key)
    return settings, client, picked[:LIVE_CANDIDATES], picked_criteria


def _repo_data_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "data"


def _requirement_from(raw: dict) -> Requirement:
    """`line_ids`는 파서만 아는 역참조라 `Requirement`가 받지 않는다 (`extra=forbid`)."""
    return Requirement.model_validate({k: v for k, v in raw.items() if k != "line_ids"})


@pytest.mark.live
def test_반복_안정성_시그마():
    """**N=11.** 「95% 확률로 안정적인 판정을 얻으려면 11회 이상 반복이 필요했다」는
    인용된 실측이다 (`docs/RUBRIC_DESIGN.md:109`). 예산이 모자라면 이 테스트를
    **통째로 버린다** — N을 깎으면 「11회 필요하다」는 근거 위에서 5회를 재는 것이 되어
    측정이 아무 말도 안 하게 된다.

    재는 것은 **심사위원 한 명의 잔여 분산**이다 (호출 1회 = 표본 1개, 4셀 × 11회 = 44회).
    패널 평균의 분산은 이보다 작으므로 이 σ는 **보수적인 쪽**이다.

    임계 σ ≤ 0.5는 **임의값**이고, 실측치는 `index.json` step 6의 `summary`에 남긴다.
    **임계를 실측으로 되맞추지 않는다** — 판정선을 판정 대상의 실행 결과에서 뽑으면
    그 테스트는 아무것도 반증하지 못한다.
    """
    from matching.judge.panel import _ask  # 심사위원 1명분. 44회 예산이 정확히 이 경로다

    settings, client, candidates, criteria = _live_cells()
    budget = CallBudget(settings)

    cells: list[tuple[str, list[int]]] = []
    for candidate in candidates:
        masked, _ = mask_sensitive(candidate.text)
        for criterion in criteria:
            messages = build_prompt(criterion, masked, GENERIC_EXAMPLES)
            digest = prompt_sha256(messages)
            scores = []
            for _ in range(settings.judge_repeat_n):
                output = _ask(client, settings, messages, digest, budget)
                if output is not None:
                    scores.append(output.score)
            cells.append((f"{candidate.candidate_id}/{criterion.id}", scores))
    budget.save()

    # 합동 표준편차. 표준편차의 산술평균은 분산 정보를 왜곡한다.
    cell_sds = [stdev(scores) if len(scores) > 1 else 0.0 for _, scores in cells]
    sigma = (fmean(sd**2 for sd in cell_sds)) ** 0.5
    detail = " / ".join(
        f"{name} sd={sd:.3f} {scores}" for (name, scores), sd in zip(cells, cell_sds, strict=True)
    )
    print(f"\n[반복 안정성] 합동 σ={sigma:.4f} · {detail} · {budget.report()}")

    assert sigma <= SIGMA_THRESHOLD, f"합동 σ={sigma:.4f} — {detail}"


@pytest.mark.live
def test_순서_불변성():
    """지원자 제시 순서를 뒤집어도 각자의 점수가 같은가.

    **우리 설계에서는 구조적으로 같아야 한다** — 한 호출에 지원자가 한 명뿐이라 순서가
    프롬프트에 없다. 그래서 여기서 흔들리면 그건 순서 편향이 아니라 모델의 잔여
    비결정성이고, 그 크기는 위 σ가 잰다.
    """
    settings, client, candidates, criteria = _live_cells()
    budget = CallBudget(settings)
    result = order_check(criteria[0], candidates, settings, client, budget=budget)
    budget.save()

    print(f"\n[순서 불변성] {result.model_dump()} · {budget.report()}")
    assert result.stable, f"순위가 바뀌었다: {result.forward_order} → {result.reverse_order}"
