"""조건 하나가 **이력서에서 어떻게 확인되는가.** 층 배정의 입력이 여기서 나온다.

## 두 갈래로 갈랐더니 틀렸다

`build.py`의 `is_countable()`은 **글자 모양**으로 층을 정한다 — 숫자나 라틴 문자가 있으면
사실 층, 없으면 판단 층. 그 규칙이 실측 공고에서 이렇게 틀렸다.

| 조건의 성격 | 옛 규칙이 보낸 곳 | 무엇이 깨졌나 |
|---|---|---|
| 예/아니오인데 이력서가 **다른 말**로 적는 것 | 숫자가 끼면 사실 층 | 낱말이 안 맞아 0점에 가깝다 |
| 정도가 있는 것에 이름이 하나 낀 경우 | 사실 층 | 이름만 세고 정도를 안 본다 |
| 예/아니오인데 라틴 문자·숫자가 없는 것 | 판단 층 | 기준점이 **성과 서술**을 요구해 만점 불가 |

**「예/아니오」를 전부 사실 층으로 보내면 안 된다**는 것이 요점이다. 사실 층은 *이력서에서
같은 낱말을 찾는* 방식이라, 이력서가 조건과 **다른 말**로 적는 조건에서는 성립하지 않는다.

## 그래서 세 갈래다

| 갈래 | 뜻 | 층 | 채점 |
|---|---|---|---|
| `term` | 이름이 있어 문자열로 찾을 수 있다 | fact | 코드가 센다 (`scorer/fact.py`) |
| `binary` | 예/아니오인데 **표현이 갈린다** | judgment | **충족형 기준점** (`anchors.py`) |
| `graded` | 잘하고 못하고의 **정도**가 있다 | judgment | 기존 기준점 |

## 사전을 만들지 않는다 — LLM에 묻는다

`parser/header_role.py`가 **같은 문제를 이미 이렇게 풀었다.** 섹션 제목을 사전으로 가르려다
사전이 목록 밖 표현에 **조용히** 실패하는 것을 확인하고 LLM 1회 분류로 바꿨다.

여기서 사전을 쓰면 두 가지가 동시에 깨진다. 하나는 같은 실패다 — 어떤 상태 어휘를 목록에
박아도 다른 공고가 **다른 낱말**로 쓰는 순간 조용히 틀린다. 다른 하나가 더 나쁘다:
그런 낱말을 코드에 박는 것 자체가 **직군 무관 일반화 위반**이다 (과제 CRITICAL).

그래서 이 파일에는 조건의 **내용 어휘가 한 글자도 없다.** 오가는 것은 「이름으로 찾히는가 ·
예/아니오인가 · 정도가 있는가」라는 **확인 방법의 어휘**뿐이고, 그건 어느 직군의 공고에도
같은 말로 쓰인다.

## 규칙 넷

1. **이미지를 보내지 않는다.** 조건 문구 문자열뿐이다
2. **공고당 1회.** 조건 11~19개를 한 번에 보낸다 (답이 빠진 조건이 있을 때만 1회 더)
3. **캐시 키에 프롬프트 해시를 넣는다.** 안 넣으면 지시문을 고쳐도 옛 판정이 조용히
   재사용된다 — 이 프로젝트에서 실제로 한 번 났던 사고다 (`header_role.fingerprint`)
4. **LLM을 못 부르면 옛 `is_countable`로 떨어진다.** 파싱 추적 화면(`GET /trace`)이
   `client=None`으로 도는데 거기서 죽으면 안 된다. 떨어진 조건은 `fell_back`에 남는다 —
   조용히 열화되지 않는 것이 요점이다
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from ..model.objects import REQUIREMENT_BRANCHES, Requirement, RequirementBranch
from .build import is_countable

BRANCHES_FILENAME = "requirement_branches.json"

_SYSTEM = (
    "너는 채용공고에서 뽑은 조건이 **이력서에서 어떻게 확인되는지**를 고른다.\n"
    "조건의 내용이 어느 분야의 것인지는 보지 않는다. 보는 것은 **확인 방법**뿐이다.\n"
    "\n"
    "갈래 3가지:\n"
    "- term: 도구·기술·자격·제품의 **이름**이 조건에 들어 있고, 이력서에서 그 이름을\n"
    "  그대로 찾으면 확인된다\n"
    "- binary: 갖췄는가 아닌가로 답이 갈리는데, 이력서는 그 사실을 **조건과 다른 말로**\n"
    "  적는다. 같은 낱말을 찾는 방식으로는 확인되지 않는다\n"
    "- graded: 잘하고 못하고의 **정도**가 있다. 서술을 읽어야 어느 정도인지 알 수 있다\n"
    "\n"
    "가르는 질문은 둘이고 순서가 있다.\n"
    "1. 이 조건은 **예/아니오**로 답이 나는가, 아니면 **정도**가 있는가.\n"
    "   정도가 있으면 graded다.\n"
    "2. 예/아니오라면, 이력서가 조건과 **같은 낱말**을 쓸 것이라고 기대할 수 있는가.\n"
    "   그렇다면 term, 아니면 binary다.\n"
    "\n"
    "**이름이 하나 들어 있다고 무조건 term이 아니다.** 그 이름이 예로 든 것이고 묻는 것이\n"
    "관심·이해·역량의 정도라면 graded다.\n"
    "**숫자가 들어 있다고 무조건 term이 아니다.** 기한·연도·햇수는 조건을 한정하는 값일\n"
    "뿐이고, 그 조건이 여전히 예/아니오라면 갈래는 숫자가 아니라 그 답이 정한다.\n"
    "확실하지 않으면 graded를 고른다 — 사람이 읽고 판단하는 쪽으로 보내는 편이 안전하다."
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
                "required": ["index", "branch"],
                "properties": {
                    "index": {"type": "integer"},
                    "branch": {"type": "string", "enum": list(REQUIREMENT_BRANCHES)},
                },
            },
        }
    },
}


class BranchError(RuntimeError):
    """갈래 분류 응답을 읽을 수 없다."""


class BranchCache(BaseModel):
    """`data/postings/{id}/requirement_branches.json`. 같은 조건 집합이면 다시 안 부른다."""

    model_config = ConfigDict(extra="forbid")

    fingerprint: str
    branches: dict[str, RequirementBranch]


class BranchResult(BaseModel):
    """분류 한 벌. **떨어진 항목을 숨기지 않는다.**

    `fell_back`이 비어 있지 않으면 그 조건들은 LLM 판정이 아니라 옛 글자 모양 규칙으로
    정해진 것이다. 결과가 같아 보여도 근거가 다르므로 구분해서 들고 다닌다.
    """

    model_config = ConfigDict(extra="forbid")

    branches: dict[str, RequirementBranch]  # 조건 id → 갈래
    llm_calls: int
    fell_back: list[str]  # 옛 규칙으로 정한 조건 id


def flatten(text: str) -> str:
    """조건 문구를 한 줄로. **캐시 키와 프롬프트가 같은 형태를 쓴다.**

    파서가 이어붙인 조건에는 줄바꿈이 들어 있어 그대로 보내면 목록의 한 항목이 여러 줄로
    보인다. 공백만 접으므로 글자는 하나도 잃지 않는다.
    """
    return " ".join(text.split())


def fingerprint(texts: Sequence[str]) -> str:
    """캐시 키. 조건 하나만 달라져도 다른 값이 나와야 한다.

    **프롬프트 해시를 넣는다.** 안 넣으면 지시문을 고쳐도 캐시가 맞아떨어져 옛 판정이
    조용히 재사용된다 — 고쳤는데 왜 안 바뀌는지를 찾느라 엉뚱한 데를 판다.
    """
    payload = json.dumps(
        {"t": list(texts), "p": hashlib.sha256(_SYSTEM.encode()).hexdigest()[:12]},
        ensure_ascii=False,
        sort_keys=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_cached(path: Path | str, texts: Sequence[str]) -> BranchCache | None:
    path = Path(path)
    if not path.exists():
        return None
    try:
        cache = BranchCache.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return cache if cache.fingerprint == fingerprint(texts) else None


def save_cache(
    path: Path | str, texts: Sequence[str], branches: dict[str, RequirementBranch]
) -> None:
    cache = BranchCache(fingerprint=fingerprint(texts), branches=branches)
    body = json.dumps(cache.model_dump(mode="json"), ensure_ascii=False, indent=2)
    Path(path).write_text(body + "\n", encoding="utf-8")


def build_prompt(texts: Sequence[str]) -> str:
    """보내는 것이 **문자열뿐**임을 한눈에 보이게 따로 뺐다. 테스트가 이걸 검사한다.

    번호를 매겨 보내고 번호로 받는다. 조건 문구는 헤더와 달리 길어서, 문구를 그대로
    돌려받는 방식은 모델이 한 글자만 고쳐도 대응이 끊긴다.
    """
    lines = ["다음 조건이 각각 어느 갈래인지 고른다.", ""]
    lines += [f"{index}. {text}" for index, text in enumerate(texts, start=1)] or ["(없음)"]
    lines += ["", "번호를 그대로 돌려주고, **빠뜨리지 말고 전부** 답한다."]
    return "\n".join(lines)


def _ask(client, model: str, texts: Sequence[str]) -> dict:
    """호출 1회. 보내는 것은 **조건 문구 문자열뿐**이다 — 좌표도 이미지도 안 간다."""
    response = client.chat.completions.create(
        model=model,
        temperature=0,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": build_prompt(texts)},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "requirement_branches", "strict": True, "schema": _SCHEMA},
        },
    )
    content = response.choices[0].message.content
    try:
        return json.loads(content or "")
    except json.JSONDecodeError as exc:
        raise BranchError(f"갈래 분류 응답을 읽을 수 없다: {exc}") from exc


def _collect(payload: dict, texts: Sequence[str]) -> dict[str, RequirementBranch]:
    """응답에서 `{조건 문구: 갈래}`를 뽑는다.

    **범위 밖 번호와 모르는 갈래는 버린다.** 지어낸 번호를 받으면 엉뚱한 조건의 층이
    바뀌는데, 그건 조용히 틀리는 종류의 사고다. 버려진 조건은 답이 안 온 것으로 취급되어
    되묻기나 폴백으로 간다.
    """
    found: dict[str, RequirementBranch] = {}
    for entry in payload.get("labels", []):
        index, branch = entry.get("index"), entry.get("branch")
        if isinstance(index, bool) or not isinstance(index, int):
            continue
        if not 1 <= index <= len(texts):
            continue
        if branch in REQUIREMENT_BRANCHES:
            found[texts[index - 1]] = branch  # type: ignore[assignment]
    return found


def classify_branches(
    texts: Sequence[str], client, model: str = "gpt-4o-mini"
) -> tuple[dict[str, RequirementBranch], int]:
    """조건 문구들 → `{문구: 갈래}`와 **실제 호출 횟수**.

    답이 안 온 조건이 있으면 **그것들만** 한 번 더 묻는다 (`header_role`과 같은 이유 —
    모델은 목록이 길면 몇 줄을 조용히 흘린다). 두 번 물어도 안 오면 결과에서 빠지고,
    호출부가 폴백으로 채운다.
    """
    if not texts:
        return {}, 0

    branches = _collect(_ask(client, model, texts), texts)
    calls = 1

    missing = [text for text in texts if text not in branches]
    if missing:
        branches.update(_collect(_ask(client, model, missing), missing))
        calls += 1
    return branches, calls


def fallback_branch(text: str) -> RequirementBranch:
    """LLM 없이 정하는 갈래. **옛 규칙 그대로다.**

    `is_countable()`이 참이면 `term`, 아니면 `graded`. **`binary`는 여기서 나오지 않는다** —
    「예/아니오인데 표현이 갈린다」는 글자 모양으로 알 수 없는 성질이고, 그 사실이 이
    파일이 생긴 이유다. 폴백은 고친 것을 되돌리는 자리이지 흉내 내는 자리가 아니다.
    """
    return "term" if is_countable(text) else "graded"


def resolve_branches(
    requirements: Sequence[Requirement],
    *,
    client=None,
    cache_path: Path | str | None = None,
    model: str = "gpt-4o-mini",
) -> BranchResult:
    """조건 목록 → `{조건 id: 갈래}`. **캐시 → LLM → 폴백** 순이다.

    `duty`는 묻지 않는다 — 담당업무는 언제나 판단 층이고 정도를 재는 항목이라
    (`build.py`의 층 배정) 갈래를 물을 이유가 없다. 물으면 비용만 는다.
    """
    items = [req for req in requirements if req.kind != "duty"]
    if not items:
        return BranchResult(branches={}, llm_calls=0, fell_back=[])

    texts = [flatten(req.text) for req in items]
    cached = load_cached(cache_path, texts) if cache_path is not None else None

    calls = 0
    if cached is not None:
        by_text = dict(cached.branches)
    elif client is not None:
        by_text, calls = classify_branches(texts, client, model=model)
    else:
        by_text = {}

    branches: dict[str, RequirementBranch] = {}
    fell_back: list[str] = []
    for req, text in zip(items, texts, strict=True):
        branch = by_text.get(text)
        if branch is None:
            branch = fallback_branch(req.text)
            fell_back.append(req.id)
        branches[req.id] = branch

    # **떨어진 항목이 있으면 캐시에 남기지 않는다.** 남기면 그 열화가 고착되어, 다음
    # 실행에서 LLM을 부를 수 있는데도 옛 판정을 계속 쓴다.
    if cache_path is not None and cached is None and not fell_back and by_text:
        save_cache(cache_path, texts, by_text)

    return BranchResult(branches=branches, llm_calls=calls, fell_back=fell_back)
