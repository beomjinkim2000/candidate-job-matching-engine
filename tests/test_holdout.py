"""홀드아웃 조건 — **라벨의 손이 닿지 않은 유일한 측정 지점.**

목업 이력서 12명은 `requirements.json`을 보고 썼다. 그래서 「완벽 매칭이 위로 왔다」는
우리가 만든 정답을 우리가 다시 확인한 것이다 (`tests/CLAUDE.md` 「순환」).

step 10이 그 순환에 구멍을 하나 냈다 — **여섯 명 누구의 설계에도 쓰지 않은 조건 2개**를
공고마다 골라 `data/resumes/{posting_id}/holdout.json`에 적어 뒀다. 충족시키려고 넣은
문장도, 일부러 비운 자리도 없는 조건이다.

**그 조건에서도 지원자별 점수가 갈린다면 그 차이는 우리 라벨이 만든 것이 아니다.**
반대로 안 갈리면 엔진이 우리가 설계한 축에서만 작동한다는 뜻이고, 그건 일반화가
아니라 우리 데이터에 맞춘 것이다.

## 판정선은 `sd > 2σ`다 — 「분산 > 0」이 아니다

인용한 실측이 「같은 질문 50회 반복 시 판정 13.6% 뒤집힘」이다. **그 노이즈만으로도
6명의 분산은 거의 확실히 0을 넘는다.** 「분산 > 0」으로 두면 엔진이 홀드아웃 조건을
전혀 변별하지 못해도 통과한다. 그래서 채점 노이즈 σ의 2배를 넘는지로 본다.

`σ`는 step 6이 실측한 반복 안정성이고 저장 주소는 `index.json`의 step 6
`summary_data["repeat_sigma"]` **하나**다. **없으면 skip한다** — 기본값 0.5를 넣지
않는다. 0.5는 임계값이지 실측 노이즈가 아니고, 임계를 노이즈 추정치 자리에 넣으면
그 비교는 아무 말도 하지 않는다.

## 양변 모두 raw 1~5다

`σ`가 5점 척도에서 정의됐다. 좌변을 가중 점수(0~100)로 재면 척도가 다른 두 수를
2배로 비교하는 것이 되고, 그때는 **엔진이 아무것도 변별하지 못해도 통과**한다.

## 이 테스트가 데이터를 읽는 이유

`tests/CLAUDE.md`는 「테스트가 `data/`의 제출용 이력서를 읽게 하지 마라」고 적는다.
이 파일은 그 규칙의 **유일한 예외**이고, 예외인 이유가 케이스의 정의 자체다 —
홀드아웃은 **그 데이터셋에서** 라벨이 안 닿은 지점을 재는 것이라 픽스처로 옮기는
순간 잴 대상이 사라진다.

대신 **이력서 원문은 읽지 않는다.** 읽는 것은 `holdout.json`(조건 번호)과 이미 저장된
완주 결과 `data/runs/*/result.json`뿐이다. 결과에는 이력서 원문이 없다
(`pipeline/run.py`의 원본 파기). 그래서 이 테스트는 **채점을 다시 하지 않고**,
따라서 OpenAI를 한 번도 부르지 않는다.
"""

from __future__ import annotations

import json
from pathlib import Path
from statistics import fmean, stdev

import pytest
from conftest import REPO_ROOT

from matching.model.objects import Criterion
from matching.pipeline import RunResult

# **임의값이다.** 「노이즈의 몇 배여야 신호인가」에 근거가 없어서 정한 값이다.
HOLDOUT_K = 2.0

DATA_DIR = REPO_ROOT / "data"
JUDGMENT_LAYER = "judgment"


def _postings_with_holdout() -> list[tuple[str, list[str]]]:
    """`(posting_id, 홀드아웃 조건 번호들)`. 없으면 빈 목록."""
    found = []
    for path in sorted(DATA_DIR.glob("resumes/*/holdout.json")):
        body = json.loads(path.read_text("utf-8"))
        found.append((body["posting_id"], list(body["requirement_ids"])))
    return found


def _latest_run(posting_id: str) -> Path | None:
    """그 공고의 가장 최근 완주 결과. 없으면 `None`."""
    runs = sorted(DATA_DIR.glob(f"runs/{posting_id}-*/result.json"))
    return runs[-1] if runs else None


def _holdout_axes(result: RunResult, requirement_ids: list[str]) -> list[str]:
    """홀드아웃 조건에서 나온 **판단 층** 항목의 criterion_id.

    사실 층 항목은 뺀다 — `σ`가 5점 척도의 값이라 0~1 커버리지 값과 나란히 놓을 수 없다.
    """
    wanted = set(requirement_ids)
    return [
        item.id
        for item in result.graph.criteria
        if isinstance(item, Criterion)
        and item.requirement_id in wanted
        and item.layer == JUDGMENT_LAYER
    ]


def test_홀드아웃_조건에서도_지원자별_점수가_갈린다(repeat_sigma):
    """라벨이 안 건드린 조건 2개에서 6명의 raw 점수(1~5)가 노이즈보다 크게 갈리는가."""
    if repeat_sigma is None:
        pytest.skip(
            "step 6의 반복 안정성 σ 실측이 없다 — 노이즈 기준선 없이 판정하지 않는다. "
            "`pytest -m live -k repeat_stability tests/test_judge.py`를 1회 돌리고 "
            "index.json step 6 summary_data.repeat_sigma에 적어야 이 테스트가 산다"
        )

    postings = _postings_with_holdout()
    if not postings:
        pytest.skip("data/resumes/*/holdout.json이 없다 — 측정할 지점이 정의되지 않았다")

    measured: dict[str, float] = {}
    skipped: list[str] = []
    for posting_id, requirement_ids in postings:
        path = _latest_run(posting_id)
        if path is None:
            skipped.append(f"{posting_id}: 저장된 완주 결과(data/runs/)가 없다")
            continue
        result = RunResult.model_validate_json(path.read_text("utf-8"))
        axes = _holdout_axes(result, requirement_ids)
        if not axes:
            skipped.append(
                f"{posting_id}: 홀드아웃 조건 {requirement_ids}가 전부 판단 층이 아니다 "
                "— raw 1~5 척도로 잴 항목이 없다"
            )
            continue

        per_candidate = []
        for candidate in result.ranked:
            raw = [axis.raw for axis in candidate.breakdown if axis.criterion_id in axes]
            if raw:
                per_candidate.append(fmean(raw))
        if len(per_candidate) < 2:
            skipped.append(f"{posting_id}: 홀드아웃 항목이 채점된 지원자가 2명 미만이다")
            continue
        measured[posting_id] = stdev(per_candidate)

    if not measured:
        pytest.skip(
            "홀드아웃을 잴 수 있는 공고가 없다 — " + " / ".join(skipped)
        )

    threshold = HOLDOUT_K * repeat_sigma
    for posting_id, sd_holdout in measured.items():
        assert sd_holdout > threshold, (
            f"{posting_id}: 홀드아웃 조건의 지원자 간 표준편차 {sd_holdout:.4f} ≤ "
            f"{HOLDOUT_K} × σ({repeat_sigma}) = {threshold:.4f}. "
            "라벨이 안 건드린 조건에서 점수가 채점 노이즈만큼밖에 안 갈렸다 — "
            "엔진이 우리가 설계한 축에서만 작동한다는 뜻이다"
        )
