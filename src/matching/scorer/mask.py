"""민감 속성 마스킹 — **채점 전에 돈다. 건너뛸 경로를 두지 않는다.**

블라인드 채용에서 못 보게 돼 있는 것(이름·성별·나이·생년월일·출신지·학교명·사진)이
점수에 새면 안 된다. 그래서 `gate.run_gates()`와 `fact.score_fact()`가 **스스로**
이 함수를 부른다 — 호출자가 깜빡할 자리를 없앤다. 이미 마스킹된 글에 다시 걸어도
결과가 같도록 만들어 두었으므로(`■`는 어느 패턴에도 안 걸린다) 두 번 불려도 안전하다.

## LLM을 쓰지 않는다

마스킹 실패는 **조용히** 일어난다. 이름 하나가 안 가려져도 출력은 멀쩡해 보이고,
그게 점수에 새는지는 아무도 모른다. LLM은 「대체로」 가려 주지만 언제 놓쳤는지 말해 주지
않는다. 패턴은 못 잡으면 **잡은 자리를 세어 보여 준다** — 두 번째 반환값이 그 목록이다.

## 길이를 보존한다 — 이게 span의 전제다

`Evidence.span`은 **원문 오프셋**이어야 검산 G2를 통과한다. 마스킹이 길이를 바꾸면
매칭한 자리가 원문의 다른 자리를 가리킨다. 그래서 가린 만큼 같은 수의 `■`를 채운다
(줄바꿈은 그대로 둔다 — 줄이 붙으면 「줄 단위 필드」 규칙이 무너진다).

**매칭은 `■`에 걸리지 않으므로**, 마스킹된 글에서 찾은 구간을 원문에서 잘라도 같은
문자열이 나온다. 그 성질 덕에 근거 인용이 마스킹 여부와 무관하게 성립한다.

## 무엇을 가리고 무엇을 남기나

| 가린다 | 왜 |
|---|---|
| 이름·성별·나이·생년월일·출신지·학교명·사진 경로 | 블라인드 채용 금지 항목 |
| 연락처·이메일 | 이메일 아이디가 **이름을 그대로 나른다** (`yuri.kang@…`) |

| 남긴다 | 왜 |
|---|---|
| 병역 사항 | 확보한 공고 두 건 다 **명시된 지원 조건**이다. 가리면 채점할 수 없다 |
| `YYYY.MM` 두 토막 날짜 | 경력 기간이다. **이걸 가리면 연차를 못 센다** |
| 전공·학점·이수 과목 | 학교명이 금지 항목이지 전공이 아니다 |

**세 토막 날짜(`1999.04.11`)만 생년월일로 보고 가린다.** 두 토막(`2024.07`)은 경력
기간이라 남긴다. 이 구분이 틀리는 경우가 있다 — 프로젝트 마감일을 `2025.03.15`로 적으면
가려진다. 반대 방향(생년월일을 두 토막으로 적음)보다 이쪽이 낫다고 봤다: **못 가린
개인정보**와 **더 가린 날짜** 중 후자가 덜 나쁘다.
"""

from __future__ import annotations

import re

from ..model.objects import Span

MASK_CHAR = "■"

# 값 전체를 가릴 필드 이름. **줄 단위**로 본다 (`성명: 강유리 (여 / … / 만 26세)`).
# 이름 하나를 가리면 그 줄에 같이 적힌 성별·생년월일·나이까지 함께 사라진다.
_FIELD_LABELS: dict[str, tuple[str, ...]] = {
    "name": ("성명", "이름", "성함", "지원자명"),
    "gender": ("성별",),
    "age": ("나이", "연령"),
    "birth": ("생년월일", "생일", "출생"),
    "origin": ("출신지", "출신지역", "본적", "고향", "거주지", "주소"),
    "school": ("출신학교", "학교", "출신교"),
    "photo": ("사진", "증명사진"),
    "contact": ("연락처", "전화", "휴대전화", "휴대폰", "이메일", "메일", "e-mail", "email"),
}

_LABEL_ALTERNATION = "|".join(
    re.escape(label)
    for labels in _FIELD_LABELS.values()
    for label in sorted(labels, key=len, reverse=True)
)
_LABEL_TO_CATEGORY = {
    label: category for category, labels in _FIELD_LABELS.items() for label in labels
}

# 줄머리(들여쓰기·불릿 허용) + 라벨 + 콜론 + 값.
# 값은 **같은 줄에 붙은 다음 필드 앞에서 멈춘다** — `병역: … 　　해외여행 결격사유: 없음`
# 처럼 한 줄에 두 필드가 있을 때 뒤엣것까지 먹으면 채점에 필요한 조건이 사라진다.
_FIELD = re.compile(
    r"(?m)^[ \t]*(?:[■□▪▶●◆*·\-]\s*)?"
    rf"(?P<label>{_LABEL_ALTERNATION})[ \t]*[:：][ \t]*"
    r"(?P<value>.*?)(?=[ \t]{2,}\S{1,12}[ \t]*[:：]|[ \t]*$)"
)

# 학교명. **접미사로 잡는다** — 학교 이름 목록을 코드에 넣으면 그게 하드코딩이다.
# 앞에 두 글자 이상이 붙어 있어야 이름으로 본다 (`대학생`·`대학원생`은 안 걸린다).
_SCHOOL = re.compile(
    r"[가-힣A-Za-z0-9]{2,}(?:대학교|대학원|대학|고등학교|중학교|초등학교|고교)(?![가-힣])"
)

# 생년월일. **세 토막만.** 두 토막(`2024.07`)은 경력 기간이라 건드리지 않는다.
_BIRTH_DATE = re.compile(
    r"(?:19|20)\d{2}[ \t]*[.\-/][ \t]*(?:0?[1-9]|1[0-2])[ \t]*[.\-/][ \t]*(?:0?[1-9]|[12]\d|3[01])"
    r"(?![\d.\-/])"
    r"|(?:19|20)\d{2}[ \t]*년[ \t]*(?:0?[1-9]|1[0-2])[ \t]*월[ \t]*(?:0?[1-9]|[12]\d|3[01])[ \t]*일"
)

# 나이. **`만`이 붙은 것만.** 맨 `12세`까지 잡으면 게임 이용등급(`12세 이용가`)이 걸린다.
_AGE = re.compile(r"만[ \t]*\d{1,3}[ \t]*세")

# 괄호 안에 홀로 선 성별 표기 — `(여 / 1999…`.
_GENDER_PAREN = re.compile(r"(?<=\()[ \t]*[남여][ \t]*(?=[/,)])")

# 사진 경로.
_PHOTO_PATH = re.compile(r"\S+\.(?:jpg|jpeg|png|gif|bmp|webp|heic)\b", re.IGNORECASE)

_PATTERN_CATEGORIES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("school", _SCHOOL),
    ("birth", _BIRTH_DATE),
    ("age", _AGE),
    ("gender", _GENDER_PAREN),
    ("photo", _PHOTO_PATH),
)


def _field_hits(text: str) -> list[tuple[str, int, int]]:
    hits: list[tuple[str, int, int]] = []
    for match in _FIELD.finditer(text):
        start, end = match.span("value")
        if end <= start:
            continue  # 값이 빈 필드. 가릴 것이 없다
        category = _LABEL_TO_CATEGORY.get(match.group("label").lower(), "personal")
        hits.append((category, start, end))
    return hits


def _merge(hits: list[tuple[str, int, int]]) -> list[tuple[str, int, int]]:
    """겹치는 구간을 하나로 합친다. 종류 이름은 `+`로 잇는다.

    합치지 않으면 `성명:` 줄의 값과 그 안의 생년월일이 두 항목으로 잡혀, UI가
    「무엇을 가렸나」를 보여줄 때 같은 자리를 두 번 세게 된다.
    """
    merged: list[tuple[str, int, int]] = []
    for category, start, end in sorted(hits, key=lambda hit: (hit[1], -hit[2])):
        if merged and start <= merged[-1][2]:
            prev_category, prev_start, prev_end = merged[-1]
            names = dict.fromkeys([*prev_category.split("+"), category])
            merged[-1] = ("+".join(names), prev_start, max(prev_end, end))
        else:
            merged.append((category, start, end))
    return merged


def mask_sensitive(resume_text: str) -> tuple[str, dict[str, Span]]:
    """민감 속성을 같은 길이의 `■`로 덮는다.

    돌려주는 것은 `(가린 글, {자리 이름: 구간})`이다. 두 번째 값은 UI가 **「무엇을
    가렸는지」**를 보여주는 데 쓴다 — 가린 사실을 감추면 마스킹이 있었는지조차 알 수 없다.

    자리 이름은 `종류-일련번호`다 (`name-01`). 한 구간이 여러 종류에 걸리면
    `name+birth+age-01`처럼 붙는다.
    """
    hits = _field_hits(resume_text)
    for category, pattern in _PATTERN_CATEGORIES:
        hits.extend(
            (category, match.start(), match.end()) for match in pattern.finditer(resume_text)
        )

    merged = _merge(hits)
    if not merged:
        return resume_text, {}

    chars = list(resume_text)
    spans: dict[str, Span] = {}
    counter: dict[str, int] = {}
    for category, start, end in merged:
        counter[category] = counter.get(category, 0) + 1
        spans[f"{category}-{counter[category]:02d}"] = Span(start=start, end=end)
        for position in range(start, end):
            if chars[position] != "\n":
                chars[position] = MASK_CHAR

    return "".join(chars), spans
