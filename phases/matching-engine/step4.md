# Step 4: rubric-builder

## 읽어야 할 파일

- `src/CLAUDE.md` — 「절대 규칙」의 기준점 패턴, 3층 아키텍처 배점
- `docs/TRADEOFFS.md` — C-1 (루브릭 템플릿 고정, 항목은 공고에서 생성)
- `src/matching/model/objects.py` (step 1) — `Criterion`
- `src/matching/parser/__init__.py` (step 3) — `parse_posting` 출력

## 작업

**`Requirement` 목록 → `Criterion` 목록.** 공고마다 다른 루브릭이 여기서 만들어진다.

### `src/matching/rubric/anchors.py` — 기준점 패턴 (고정)

```python
ANCHOR_TEMPLATE: dict[int, str] = {
    1: "관련 경험이 없거나, 있어도 구체적 행동 서술이 없음",
    3: "제시하나 서술이 추상적이고 본인 역할·성과가 불명확함",
    5: "본인의 역할 · 취한 행동 · 달성한 성과가 명확히 서술됨",
}

def make_anchors(requirement: Requirement) -> dict[int, str]:
    """조건 문구를 앞에 붙여 항목별 기준점을 만든다.

    「<조건>」 — <패턴 문구>  형태. 템플릿 문장 안에 끼워 넣지 않는다.
    """
    return {
        level: f"「{requirement.text}」 — {text}"
        for level, text in ANCHOR_TEMPLATE.items()
    }
```

**조건 문구를 문장 안에 끼워 넣지 마라.** `"{req}을(를) 제시하나…"` 식으로 만들면
**한국어 조사를 코드가 골라야 한다** — 받침 유무로 을/를·이/가가 갈리고, 영문 스킬명
(`Kubernetes`, `GA4`)이 섞이면 규칙이 더 깨진다. 조사 처리는 이 과제의 문제가 아니다.
**앞에 붙이는 형태**면 조사가 아예 안 생긴다.

**고정하는 것은 이 패턴뿐이다.** 항목 자체는 조건에서 나온다. 2점·4점은 기준점 없이
"1과 3 사이" / "3과 5 사이"로 둔다 — 5개 기준점을 다 잘 쓰는 것보다 3개를 잘 쓰는 쪽이
낫다는 판단이다 (`docs/TRADEOFFS.md` B-3).

**패턴 문구에 직군 어휘가 없는 것이 일반화의 핵심이다.** 재는 것은 「무엇에 대한
경험인가」가 아니라 「얼마나 구체적으로 썼는가」다. 직군 정보는 기준점이 아니라
`「조건」` 부분으로만 들어온다.

### `src/matching/rubric/build.py`

```python
def build_rubric(
    requirements: list[Requirement],
    settings: Settings,
    graph: EvidenceGraph,
) -> list[Criterion]: ...
```

`build_rubric`은 `requirements.json`의 **두 목록을 다 받는다** — `requirements`(조건)와
`duties`(담당업무). step 3이 둘을 따로 쓴다.

층 배정 규칙:

| Requirement | 가는 층 | 이유 |
|---|---|---|
| `settings.gate_kinds`에 걸림 (면허·법정 자격증) | `gate` | 없으면 그 일을 법적으로 못 한다 |
| 보유 여부를 문자열로 셀 수 있음 (자격증명, 도구명, 연차 수치) | `fact` | 코드가 센다 |
| 그 외 (경험의 관련성·깊이, 성과 서술) | `judgment` | 심사위원이 판단한다 |
| **`kind == "duty"`** | **`judgment` 고정** | 아래 |

**`fact` / `judgment` 분류를 직군 어휘로 하지 마라.** 판단 기준은 *"이 조건의 충족 여부를
문자열 대조로 확인할 수 있는가"*이지 *"이게 Python인가"*가 아니다.

### 담당업무는 조건이 아니지만 버리지도 않는다

`kind == "duty"`인 항목은 **게이트와 사실 채점에 절대 들어가지 않는다.** 담당업무는
입사 후 할 일이지 지원자가 갖춰야 할 자격이 아니다. 조건으로 세면 **그 일을 이미
해본 사람만 점수를 받는데, 확보한 두 공고 다 신입·인턴 공고다** — 대상 자체가 뒤집힌다.

그렇다고 버리면 안 된다. 실측(`data/postings/kt-b2c/requirements.json`): KT의 조건 6건 중
**4건이 졸업·병역·해외여행·입사가능일**로 지원자 전원이 통과하는 형식 요건이고, 남는
변별력은 우대 2건뿐이다. **담당업무 5건을 빼면 그 공고는 마케터와 개발자를 구별하지
못한다.** 자격 요건이 형식적인 공고에서 담당업무는 유일한 직무 신호다.

그래서 담당업무는 **판단 축이 「무엇에 대한 관련성인가」를 재는 자**로 들어간다.
2층 배점의 「경험의 직무관련성」이 관련성을 재는 대상이 바로 이것이다.

- 기준점은 `make_anchors`를 **그대로** 쓴다. 새 문구를 만들지 마라 — 재는 것은 여전히
  「얼마나 구체적으로 썼는가」이고, 직군 정보는 `「담당업무 문구」` 부분으로만 들어온다
- `graph.link(criterion.id, "derived_from", duty.id)`를 **똑같이** 건다. 담당업무도
  `source_bbox`를 들고 있으므로 판단 점수의 근거 사슬이 이미지 좌표까지 이어진다
- 가중치는 `judgment` 층 총합 안에서 나눈다. **`duty`에서 나온 항목이 `preferred`에서
  나온 항목보다 크지 않게** 한다 — 담당업무는 명시된 요구가 아니라 직무 설명이다
- 공고에 담당업무 섹션이 없으면 `duties`가 빈 목록으로 온다. **그때도 동작해야 한다**

가중치 배분:

- 층별 총합은 `settings.weights` (기본 fact 35 / judgment 65)
- 층 안에서는 `kind`로 나눈다 — `required` 항목이 `preferred`보다 크다
- 층 총합을 항목 수로 나눠 정규화한다. **항목이 몇 개든 총점이 100이 되게** 한다

각 `Criterion` 생성 시 `graph.link(criterion.id, "derived_from", requirement.id)`를
반드시 호출한다. 안 하면 G3에서 차단된다.

### `src/matching/rubric/review.py` — 고객사 승인

```python
def apply_approval(criteria: list[Criterion], approvals: dict[str, bool]) -> list[Criterion]:
    """승인된 항목만 review_status를 human_validated로 올린다."""

def pending(criteria: list[Criterion]) -> list[Criterion]:
    """아직 draft인 항목. UI가 승인 화면에 띄운다."""
```

**승인이 점수를 바꾸지 않는다.** `review_status` 필드만 바꾼다. 이유: 승인 여부로 점수가
움직이면 "승인 전 결과"와 "승인 후 결과"가 다른 시스템이 된다. 승인은 **표시**를 바꾼다.

## Acceptance Criteria

```bash
ruff check src/matching/rubric
pytest tests/test_rubric.py -q
```

최소 테스트 케이스:

- 조건 3개든 12개든 가중치 총합이 100인지
- `gate_kinds`에 걸린 조건이 `gate` 층으로 가는지
- 모든 `Criterion`에 `derived_from` Link가 붙는지 (G3)
- **직군이 다른 두 공고에서 서로 다른 항목 목록이 나오는지** — 일반화의 최소 증명
- **`kind == "duty"`인 항목이 `gate`·`fact` 층에 하나도 없는지** — 담당업무를 자격으로
  세지 않는다는 것의 기계적 확인
- **`duties`가 빈 목록이어도 총합이 100인지** — 담당업무 섹션이 없는 공고
- **조건 문구가 받침으로 끝나든(`트래픽 처리`) 안 끝나든(`GA4`) 기준점 문장이 깨지지 않는지**
  — 조사를 안 쓰는 형태인지 확인하는 테스트
- `apply_approval` 후에도 `weight`와 `anchors`가 그대로인지

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트:
   - `ANCHOR_TEMPLATE` 외에 고정된 항목 목록이 코드에 없는가?
   - 스킬명·직군명이 하드코딩돼 있지 않은가? (`grep -rniE "python|마케팅|디자이너" src/matching/rubric` 이 비어야 한다)
   - 가중치가 `settings`에서 오는가?
3. `index.json`의 step 4를 업데이트한다.

## 금지사항

- **고정 항목 목록을 만들지 마라** (예: 직무관련경험 / 지식기술 / 성과달성력 / 문제해결 /
  직업윤리). 이유: 과제 CRITICAL을 정면으로 위반한다. 2차 문헌 검토가 이 5항목을
  제안했지만 **이 이유로 반려했다** (`docs/TRADEOFFS.md` C-1).
- **학력을 기본 항목으로 넣지 마라.** 공고가 명시적으로 요구할 때만 조건부로 생긴다.
  이유: 블라인드 채용 금지 항목이고 예측력도 낮다.
- `evidence_grade`를 `weight`에 곱하지 마라. 이유: 근거 수준과 적합도를 섞는다.
- 승인(`review_status`)으로 점수를 바꾸지 마라. 이유는 위에 적었다.
