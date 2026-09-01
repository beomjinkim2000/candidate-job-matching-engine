"""0층 게이트 + 1층 사실 채점 — **여기에 OpenAI 호출이 하나도 없다.**

`src/CLAUDE.md`의 모듈 경계: 「scorer — 0~100점 + 근거. **결정적.** 같은 입력에 항상
같은 출력」. 판단이 필요한 것은 전부 2층(`judge/`)의 일이다.

- `mask` — 민감 속성 마스킹. 채점 전에 돈다
- `normalize` — 표기 흔들림 정규화 + 대조할 표현 뽑기
- `gate` — 0층. 면허·법정 자격증만
- `fact` — 1층. 보유/열거/수치(포화함수)
"""

from .fact import saturation, score_fact
from .gate import GateResult, run_gates
from .mask import MASK_CHAR, mask_sensitive
from .normalize import (
    Normalized,
    expand,
    key_terms,
    load_aliases,
    normalize,
    normalize_with_map,
    required_years,
)

__all__ = [
    "MASK_CHAR",
    "GateResult",
    "Normalized",
    "expand",
    "key_terms",
    "load_aliases",
    "mask_sensitive",
    "normalize",
    "normalize_with_map",
    "required_years",
    "run_gates",
    "saturation",
    "score_fact",
]
