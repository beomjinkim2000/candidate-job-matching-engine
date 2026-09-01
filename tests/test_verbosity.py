"""장황함 불변성 — **같은 사실을 길게 쓰면 점수가 오르는가.**

LLM 채점자는 길고 상세한 답을 부당하게 선호한다는 보고가 반복해서 나온다
(`docs/RUBRIC_DESIGN.md`). 채용 채점에서 이게 왜 치명적인가 — **글을 길게 쓰는
훈련을 받은 사람이 유리해진다.** 그건 직무 능력이 아니다.

그래서 **사실 내용이 같고 서술 길이만 다른 이력서 두 통**을 넣어 점수 차를 본다.
아래 두 글의 사실은 같다 — 기간·본인 행동·결과 수치가 하나도 다르지 않고, 긴 쪽은
같은 말을 풀어 쓰고 배경 설명을 덧붙였을 뿐이다.

## 이 테스트만 `@pytest.mark.live`인 이유

**픽스처 심사위원으로는 검증이 안 된다.** 재려는 것이 실물 모델의 편향이므로,
스텁으로 돌리면 「우리가 짠 스텁이 길이에 안 흔들린다」를 확인하는 것이 되어
아무 말도 하지 않는다. 그래서 기본 실행에서 빠진다 (`pyproject.toml`의 `addopts`).

## 임계는 만점의 10%다. 실측으로 고치지 않는다

**임의값이다** (`tests/CLAUDE.md` 「임계값의 출처」). 한 번의 실행 결과로 임계를
옮기면 판정선을 판정 대상에서 뽑는 것이 되어 그 테스트는 무엇도 반증하지 못한다.
실측이 나오면 `index.json`의 `summary`에 **숫자만** 적는다.

> **2026-09-01 이 테스트는 아직 실행되지 않았다.** 과제 예산 $5를 이미 넘겼다
> (`data/.judge_usage.json` 645회 · $5.95). 안 돌렸다는 사실을 「통과」로 적지 않는다.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from conftest import make_proposal, make_settings

from matching.model import Resume
from matching.pipeline import score

# 만점 100의 10%. **임의값.**
VERBOSITY_THRESHOLD = 10.0

# 사실은 동일하다 — 「4년」·「GCP와 ICH-E6」·「EDC」·「9건에서 0건」이 양쪽에 그대로 있다.
SHORT = (
    "임상시험 모니터링 업무를 4년 수행했습니다.\n"
    "GCP와 ICH-E6 가이드라인에 맞춰 방문 점검 항목표를 다시 짰습니다.\n"
    "실시기관을 방문해 원자료와 증례기록서를 대조하고 이탈 사항을 정리했습니다.\n"
    "EDC 시스템으로 증례 자료를 입력하고 검증 질의를 처리했습니다.\n"
    "그 결과 방문 보고서 제출 지연이 9건에서 0건으로 줄었습니다.\n"
)

LONG = (
    "저는 임상시험 모니터링 업무를 4년 동안 수행해 왔습니다. 그 기간은 제게 매우 뜻깊은\n"
    "시간이었고, 업무를 대하는 태도를 다시 세우는 계기가 되었다고 생각합니다.\n"
    "먼저 GCP와 ICH-E6 가이드라인을 다시 정독하는 일부터 시작했습니다. 가이드라인은\n"
    "읽을 때마다 새롭게 보이는 부분이 있었고, 그 내용에 맞춰 방문 점검 항목표를 처음부터\n"
    "다시 짰습니다. 항목표를 다시 짜는 과정에서 기존 항목의 순서와 표현도 하나하나\n"
    "살펴보았습니다.\n"
    "그다음에는 실시기관을 방문하는 일정을 잡았습니다. 방문할 때마다 원자료와\n"
    "증례기록서를 나란히 놓고 대조했고, 대조 과정에서 발견한 이탈 사항은 그때그때\n"
    "정리해 두었습니다. 정리해 둔 내용은 나중에 다시 확인할 수 있도록 보관했습니다.\n"
    "자료 관리는 EDC 시스템으로 했습니다. EDC 시스템에 증례 자료를 입력하고, 입력한\n"
    "자료에 대해 올라온 검증 질의를 하나씩 처리했습니다. 질의를 처리하는 일은 반복적인\n"
    "면이 있었지만 자료의 신뢰도와 직결되는 일이라 소홀히 하지 않았습니다.\n"
    "이러한 노력의 결과로 방문 보고서 제출 지연이 9건에서 0건으로 줄었습니다. 숫자\n"
    "자체보다도 팀이 같은 기준으로 일하게 되었다는 점이 더 큰 성과라고 생각합니다.\n"
)


def test_live_마크는_기본_실행에서_빠진다(pytestconfig):
    """`pytest`를 그냥 치면 실물 API 호출 테스트가 **선택되지 않아야** 한다.

    이 파일의 아래 테스트는 실제로 OpenAI를 부른다. 기본 실행에 섞이면 예산이
    조용히 새고, 실제로 그 사고가 났다 (2026-09-01 세 시간에 496회).
    난간은 `pyproject.toml`의 `addopts = "-m 'not live'"` 한 줄뿐이므로, 그 한 줄이
    지워졌는지를 테스트가 지킨다.
    """
    addopts = pytestconfig.getini("addopts")
    joined = " ".join(addopts) if isinstance(addopts, list) else str(addopts)
    assert "not live" in joined, (
        f"addopts에서 live 제외가 사라졌다: {joined!r} — 기본 실행이 실물 API를 부른다"
    )


@pytest.mark.live
def test_장황함_불변성(tmp_path):
    """같은 사실을 길게 쓴 이력서와 짧게 쓴 이력서의 점수 차가 임계 이내인가.

    **실측치를 실패 메시지에 담는다.** 「통과했다」보다 「실측이 얼마였다」가 정직하고,
    다음 사람이 임계를 고칠 근거가 된다.
    """
    from openai import OpenAI

    from matching.config import load_settings

    settings = load_settings()
    if not settings.openai_api_key:
        pytest.skip("OPENAI_API_KEY가 없다 — 실물 호출 테스트는 건너뛴다")
    client = OpenAI(api_key=settings.openai_api_key)

    result = score(
        make_proposal("a", make_settings(judge_model=settings.judge_model)),
        [
            Resume(candidate_id="V-SHORT", text=SHORT),
            Resume(candidate_id="V-LONG", text=LONG),
        ],
        make_settings(judge_model=settings.judge_model),
        client=client,
        data_dir=tmp_path,
        now=datetime(2026, 9, 1, 12, 0).astimezone(),
    )
    totals = {item.candidate_id: item.total for item in result.ranked}
    gap = abs(totals["V-LONG"] - totals["V-SHORT"])

    assert gap <= VERBOSITY_THRESHOLD, (
        f"짧은 글 {totals['V-SHORT']:.2f}점 / 긴 글 {totals['V-LONG']:.2f}점, "
        f"차 {gap:.2f}점 > 임계 {VERBOSITY_THRESHOLD}점. "
        "사실이 같은데 길이로 갈렸다 — 글을 길게 쓰는 훈련이 점수가 된 것이다"
    )
