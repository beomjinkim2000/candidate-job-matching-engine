"""LLM이 들어오는 **유일한** 자리 — 섹션 제목이 무슨 역할인지.

## 규칙 세 줄

1. **이미지를 보내지 않는다.** 헤더 문자열 몇 개, 총 1,000토큰 미만이다
2. **공고당 1회.** 공고 2개면 완주 전체에서 2회 (`docs/COST_BUDGET.md`)
3. **필수/우대를 여기서 판정하지 않는다.** 그건 `classify.py`의 코드 사다리가 한다.
   여기서 정하는 것은 **섹션의 역할**뿐이다

## 왜 사전을 안 쓰나

`{자격요건, 지원자격, 필수사항}` 같은 목록을 코드에 박으면 목록 밖 표현에 **조용히
실패한다.** 확보한 공고 B의 섹션 제목이 정확히 그 경우다 — 「이런 분을 찾고 있습니다」·
「이런 경험이 있다면 더욱 좋습니다」는 어떤 사전에도 없다.

## 직군 무관 일반화와 충돌하지 않는다

여기 오가는 어휘는 전부 **문서 구조 어휘**이고 직군·스킬 어휘가 아니다.
「자격요건」은 어느 직군의 공고에도 같은 말로 나온다. 이 파일에 직군명·스킬명이
한 글자라도 들어가면 그건 위반이고, **AC가 grep으로 막는다** — 그래서 이 설명에도
직군 이름을 예시로 적지 않는다. 검사가 주석과 코드를 구분하지 못하는 편이,
구분하려다 진짜 하드코딩을 놓치는 것보다 낫다.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict

HeaderRole = Literal["requirement", "preferred", "duty", "context", "excluded"]

HEADER_ROLES: tuple[HeaderRole, ...] = get_args(HeaderRole)

# 「이 줄은 섹션 제목이 아니다」. `HeaderRole`에 넣지 않는다 — 역할이 아니라 역할 없음이고,
# 결과 dict에서는 **키가 빠지는 것**으로 표현된다 (`classify_headers`의 반환 계약).
NOT_A_HEADER = "none"

ROLES_FILENAME = "header_roles.json"

_SYSTEM = (
    "너는 채용공고의 **문서 구조**를 읽는다. 직무 내용을 판단하지 않는다.\n"
    "주어진 것은 공고에서 뽑은 줄들이고, 각 줄이 어떤 성격의 섹션 제목인지만 고른다.\n\n"
    "역할 6가지:\n"
    "- requirement: 지원자가 **갖춰야 하는** 조건이 나열되는 섹션의 제목\n"
    "- preferred: 있으면 **좋은** 조건이 나열되는 섹션의 제목\n"
    "- duty: **입사 후 하게 될 일**이 나열되는 섹션의 제목. 지원자 조건이 아니다\n"
    "- context: 회사·팀·인재상 소개. 조건도 업무도 아니다\n"
    "- excluded: 복리후생·근무조건·전형절차·제출서류·문의처 등 **채점과 무관한** 섹션의 제목\n"
    f"- {NOT_A_HEADER}: 섹션 제목이 **아니다**. 본문 한 줄이거나 표의 칸이다\n\n"
    "판단 기준은 **그 제목 아래에 무엇이 오는가**이다. 표현이 사전에 없는 말이어도\n"
    "(예: 완곡한 구어체 제목) 뜻으로 고른다.\n"
    "확실하지 않으면 context를 고른다 — 그 섹션은 표시만 되고 채점에 안 들어간다."
)

_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["labels"],
    "properties": {
        "labels": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text", "role"],
                "properties": {
                    "text": {"type": "string"},
                    "role": {"type": "string", "enum": [*HEADER_ROLES, NOT_A_HEADER]},
                },
            },
        }
    },
}


class HeaderRoleError(RuntimeError):
    """헤더 역할을 정할 수 없다. **기본값으로 넘어가지 않는다** —
    `excluded`를 못 가리면 복리후생의 조건이 지원자에게 요구되는 조건으로 들어간다.
    """


class RoleCache(BaseModel):
    """`data/postings/{id}/header_roles.json`. **같은 헤더 집합이면 다시 안 부른다.**"""

    model_config = ConfigDict(extra="forbid")

    fingerprint: str
    roles: dict[str, HeaderRole]


def fingerprint(headers: list[str], ambiguous: list[str]) -> str:
    """캐시 키. 줄 하나만 달라져도 다른 값이 나와야 한다."""
    payload = json.dumps({"h": headers, "a": ambiguous}, ensure_ascii=False, sort_keys=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_cached(path: Path | str, headers: list[str], ambiguous: list[str]) -> RoleCache | None:
    path = Path(path)
    if not path.exists():
        return None
    try:
        cache = RoleCache.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return cache if cache.fingerprint == fingerprint(headers, ambiguous) else None


def save_cache(
    path: Path | str, headers: list[str], ambiguous: list[str], roles: dict[str, HeaderRole]
) -> None:
    cache = RoleCache(fingerprint=fingerprint(headers, ambiguous), roles=roles)
    body = json.dumps(cache.model_dump(mode="json"), ensure_ascii=False, indent=2)
    Path(path).write_text(body + "\n", encoding="utf-8")


def build_prompt(headers: list[str], ambiguous: list[str]) -> str:
    """보내는 것이 **문자열뿐**임을 한눈에 보이게 따로 뺐다. 테스트가 이걸 검사한다."""
    lines = ["다음 줄들의 역할을 고른다.", "", "[섹션 제목으로 보이는 줄]"]
    lines += [f"- {text}" for text in headers] or ["- (없음)"]
    if ambiguous:
        lines += [
            "",
            "[제목인지 본문인지 모호한 줄 — 제목이 아니면 " + NOT_A_HEADER + "]",
            *[f"- {text}" for text in ambiguous],
        ]
    lines += ["", "입력에 준 줄을 **그대로** text에 담아 전부 답한다."]
    return "\n".join(lines)


def classify_headers(
    headers: list[str],
    ambiguous: list[str],
    client,
    model: str = "gpt-4o-mini",
) -> dict[str, HeaderRole]:
    """문자열만 보내고 역할을 받는다. **좌표도 이미지도 안 보낸다.**

    반환 dict에 **키가 없으면 「섹션 제목이 아니다」**이다. `HeaderRole`을 6종으로 늘리는
    대신 이렇게 한 이유: 역할 목록은 채점이 쓰는 분류이고, 「제목 아님」은 채점의 분류가
    아니라 이 단계의 부산물이다. 섞으면 `Block.header_role`에 채점이 모르는 값이 실린다.
    """
    if not headers and not ambiguous:
        return {}

    response = client.chat.completions.create(
        model=model,
        temperature=0,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": build_prompt(headers, ambiguous)},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "header_roles", "strict": True, "schema": _SCHEMA},
        },
    )
    content = response.choices[0].message.content
    try:
        payload = json.loads(content or "")
    except json.JSONDecodeError as exc:
        raise HeaderRoleError(f"역할 분류 응답을 읽을 수 없다: {exc}") from exc

    known = set(headers) | set(ambiguous)
    roles: dict[str, HeaderRole] = {}
    for entry in payload.get("labels", []):
        text, role = entry.get("text"), entry.get("role")
        # **모델이 지어낸 줄을 받지 않는다.** 우리가 보낸 줄만 통과시킨다 — 안 그러면
        # 존재하지 않는 섹션이 블록으로 생기고 그 아래 항목이 조건으로 올라간다.
        if text in known and role in HEADER_ROLES:
            roles[text] = role  # type: ignore[assignment]

    missing = [text for text in headers if text not in roles]
    if missing:
        # 헤더로 판정된 줄에 역할이 없으면 그 섹션의 항목들이 사다리 1단계를 못 탄다.
        # 조용히 두면 근거 등급이 이유 없이 낮아진다.
        raise HeaderRoleError(f"역할이 안 나온 섹션 제목 {len(missing)}개: {missing[:3]}")
    return roles
