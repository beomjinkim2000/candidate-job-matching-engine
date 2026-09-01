"""유형 분리 — 완벽 2 · 부분 3 · 미스 1이 **그룹으로** 갈리는가.

과제가 목업 이력서를 「완벽 매칭 2 / 부분 매칭 3 / 미스매칭 1」로 구성하라고 했으므로
이 확인은 요구 그 자체다. 다만 `tests/CLAUDE.md`가 이걸 **2차 지표**로 내렸다.

## 왜 2차인가 — 순환

**우리가 정답도 만들고 채점도 한다.** 같은 조건 목록을 보고 이력서를 설계했고, 같은
조건 목록으로 루브릭을 만들어 채점한다. 「의도한 대로 갈렸다」는 그래서 채점이 옳다는
증거가 아니라 **설계와 채점이 어긋나지 않았다**는 확인이다.

그 한계를 지우지 않는 장치가 둘 있다.

1. `intended_type`은 `tests/fixtures/`의 픽스처 파일에만 있고 `Resume`에 안 실린다.
   채점 경로가 라벨을 볼 수 있으면 이 테스트는 자기 자신을 검사하게 된다
   (`model/objects.py`의 `Resume` docstring)
2. **개별 등수를 검사하지 않는다.** 완벽 2명 중 누가 1등인지는 우리가 정한 것이 아니라
   그냥 정하지 않은 것이다. 거기에 기대값을 걸면 없는 정답을 만들어 내는 것이 된다

## 심사위원

`OverlapJudge` — 항목 이름과 이력서의 글자 겹침으로 점수를 내는 결정적 대역이다
(`tests/conftest.py`). 상수 심사위원을 쓰면 65점이 전원 동일해져 **판단 층이 유형에
반응하는지를 이 테스트가 전혀 못 본다.** 대역의 한계는 conftest에 적어 뒀다.
"""

from __future__ import annotations

from datetime import datetime
from statistics import fmean

from conftest import OverlapJudge, load_resumes, make_proposal, make_settings, resumes_of

from matching.pipeline import score

GROUP_ORDER = ("완벽", "부분", "미스")


def _run(tmp_path):
    settings = make_settings()
    return score(
        make_proposal("a", settings),
        resumes_of("a"),
        settings,
        client=OverlapJudge(),
        data_dir=tmp_path,
        now=datetime(2026, 9, 1, 12, 0).astimezone(),
    )


def _by_group(result) -> dict[str, list[float]]:
    labels = {item["candidate_id"]: item["intended_type"] for item in load_resumes("a")}
    grouped: dict[str, list[float]] = {name: [] for name in GROUP_ORDER}
    for candidate in result.ranked:
        grouped[labels[candidate.candidate_id]].append(candidate.total)
    return grouped


def test_유형_그룹의_평균이_완벽_부분_미스_순이다(tmp_path):
    """**그룹 평균**만 본다. 개별 등수는 검사 대상이 아니다."""
    grouped = _by_group(_run(tmp_path))
    means = [fmean(grouped[name]) for name in GROUP_ORDER]

    assert means == sorted(means, reverse=True), (
        f"그룹 평균이 완벽 > 부분 > 미스 순이 아니다 — "
        f"{dict(zip(GROUP_ORDER, [round(value, 2) for value in means], strict=True))}"
    )


def test_그룹끼리_겹치지_않는다(tmp_path):
    """완벽의 최저점이 부분의 최고점보다 위인가. **평균만 보면 겹침이 안 보인다.**

    평균 순서는 그룹 안 분산이 크면 우연히도 맞는다. 「완벽 2명 중 한 명이 부분 3명
    사이에 끼어 있다」면 그건 유형이 갈린 것이 아닌데, 평균 검사만으로는 통과한다.
    """
    grouped = _by_group(_run(tmp_path))
    for upper, lower in zip(GROUP_ORDER, GROUP_ORDER[1:], strict=False):
        assert min(grouped[upper]) > max(grouped[lower]), (
            f"{upper} 최저 {min(grouped[upper]):.2f} ≤ {lower} 최고 "
            f"{max(grouped[lower]):.2f} — 두 그룹이 점수대에서 겹친다"
        )
