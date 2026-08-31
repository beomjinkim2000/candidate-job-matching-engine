"""설정 로더 — 가중치·게이트 조건·임계값·모델·단가를 한곳에서 준다.

`src/CLAUDE.md`: 「가중치·게이트 조건은 설정값으로 외부화한다. 코드에 상수로 박지 않는다.」
기본값은 여기 있고, `.env` 또는 `load_settings()` 인자로 덮어쓴다.

각 값 옆에 **출처**를 적는다. 실측에서 나온 값과 임의로 정한 값을 섞지 않는다 —
어느 쪽인지 적어 두면 다음 사람이 무엇부터 다시 재야 하는지가 분명해진다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

# --- 배점. docs/RUBRIC_DESIGN.md — 세는 방식 r≈0.15 / 판단 방식 r≈0.48 ---
DEFAULT_WEIGHTS: dict[str, float] = {"fact": 35.0, "judgment": 65.0}

# --- 0층 게이트로 취급할 조건 종류. 면허·법정 자격증만 (src/CLAUDE.md) ---
DEFAULT_GATE_KINDS: list[str] = ["license"]


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    return default if raw is None else int(raw)


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    return default if raw is None else float(raw)


def _env_opt_int(name: str) -> int | None:
    raw = _env(name)
    return None if raw is None else int(raw)


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name)
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = _env(name)
    return default if raw is None else [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(repr=False)
class Settings:
    """실행 설정 한 벌. **로깅 함수에 이 객체를 통째로 넘기지 마라.**"""

    # --- 비밀키. 값은 .env에서만 온다 ---
    openai_api_key: str = ""
    saramin_access_key: str | None = None

    # --- 배점·게이트 ---
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    gate_kinds: list[str] = field(default_factory=lambda: list(DEFAULT_GATE_KINDS))
    judge_disagreement_threshold: int = 2  # 5점 척도에서 2점 이상 벌어지면 3번째 심사위원
    experience_saturation_k: float = 1.0  # **임의값.** 함수 형태는 step 5에서 확정

    # --- 파싱 (step 3). 값의 출처는 docs/OCR_EVIDENCE.md ---
    ocr_engine: str = "paddle"  # "paddle" | "vision". EasyOCR은 신뢰도 0.396으로 탈락 (§1)
    header_x_threshold: int = 100  # **실측.** x0 띠 사이 빈 구간(60~125)의 중앙 (§3)
    continuation_tolerance: int = 4  # **실측.** 띠 2·3 간격(~15px)보다 훨씬 작게 (§3)
    ambiguous_fallback_ratio: float = 0.5  # **임의값.** 넘으면 VLM 폴백 조건 — 지금은 예외

    # --- 모델·단가 (docs/COST_BUDGET.md) ---
    # 모델명을 다른 파일에 박지 않는다. 바꿀 자리는 여기와 .env 둘뿐이다.
    header_model: str = "gpt-4o-mini"  # 헤더 역할 분류. 문자열 4~8개를 5종으로 가른다
    judge_model: str = "gpt-4o"  # 심사위원 채점. 여기서 아끼면 채점이 무너진다
    # 단가는 기본값을 0으로 둔다. **공식 가격표가 바뀌므로 코드에 숫자를 박지 않는다**
    # (COST_BUDGET.md §2). .env에 넣기 전까지 USD 환산은 0이고, 그 사실이 결과에 드러나야 한다.
    price_in_per_1m: float = 0.0  # 입력 100만 토큰당 USD
    price_out_per_1m: float = 0.0  # 출력 100만 토큰당 USD
    max_total_calls: int = 200  # 완주 1회 최악 추정 134회 위 (§1). 넘으면 step 6 CallBudget이 예외

    # --- 심사위원 재현 (step 6) ---
    judge_seed: int | None = None  # 지원되면 고정, 안 되면 None
    judge_repeat_n: int = 11  # **인용된 실측.** docs/RUBRIC_DESIGN.md:109 — 95% 안정에 11회

    # --- 대장·소거 (step 12) ---
    unaddressed_tolerance: float = 0.15  # **임의값**
    ledger_degraded_ratio: float = 0.5  # **임의값**
    ablation_sample_size: int = 3  # 12-C 표본. 공고당 상위 n명

    # --- 승인 게이트 (step 7) ---
    skip_approval: bool = False  # True면 RunResult.unapproved=True로 결과·UI에 표시된다

    def __repr__(self) -> str:
        """키를 절대 찍지 않는다.

        마스킹 문자열에 실제 키 접두사(`sk-`)를 흉내 내지 않는다 — step 0 AC가
        `repr(s)`에 그 접두사가 없는 것으로 누출을 검사하므로, 가짜 접두사를 쓰면
        **검사 자체가 무력해진다.**
        """
        shown = {
            "openai_api_key": "***set***" if self.openai_api_key else None,
            "saramin_access_key": "***set***" if self.saramin_access_key else None,
            "weights": self.weights,
            "gate_kinds": self.gate_kinds,
            "ocr_engine": self.ocr_engine,
            "header_model": self.header_model,
            "judge_model": self.judge_model,
            "max_total_calls": self.max_total_calls,
            "judge_repeat_n": self.judge_repeat_n,
            "skip_approval": self.skip_approval,
        }
        body = ", ".join(f"{k}={v!r}" for k, v in shown.items())
        return f"Settings({body})"

    __str__ = __repr__


def load_settings(path: str | None = None) -> Settings:
    """`.env`를 읽어 설정을 만든다. 키가 없어도 예외를 던지지 않는다.

    키가 필요한 지점(step 3 헤더 분류·step 6 심사위원)에서 확인한다 — 로더가 막으면
    키 없이 돌 수 있는 결정적 경로(사실 채점·랭킹)까지 같이 죽는다.
    """
    load_dotenv(dotenv_path=path, override=False)

    return Settings(
        openai_api_key=_env("OPENAI_API_KEY", "") or "",
        saramin_access_key=_env("SARAMIN_ACCESS_KEY"),
        weights={
            "fact": _env_float("MATCHING_WEIGHT_FACT", DEFAULT_WEIGHTS["fact"]),
            "judgment": _env_float("MATCHING_WEIGHT_JUDGMENT", DEFAULT_WEIGHTS["judgment"]),
        },
        gate_kinds=_env_list("MATCHING_GATE_KINDS", list(DEFAULT_GATE_KINDS)),
        judge_disagreement_threshold=_env_int("MATCHING_JUDGE_DISAGREEMENT_THRESHOLD", 2),
        experience_saturation_k=_env_float("MATCHING_EXPERIENCE_SATURATION_K", 1.0),
        ocr_engine=_env("MATCHING_OCR_ENGINE", "paddle") or "paddle",
        header_x_threshold=_env_int("MATCHING_HEADER_X_THRESHOLD", 100),
        continuation_tolerance=_env_int("MATCHING_CONTINUATION_TOLERANCE", 4),
        ambiguous_fallback_ratio=_env_float("MATCHING_AMBIGUOUS_FALLBACK_RATIO", 0.5),
        header_model=_env("MATCHING_HEADER_MODEL", "gpt-4o-mini") or "gpt-4o-mini",
        judge_model=_env("MATCHING_JUDGE_MODEL", "gpt-4o") or "gpt-4o",
        price_in_per_1m=_env_float("MATCHING_PRICE_IN_PER_1M", 0.0),
        price_out_per_1m=_env_float("MATCHING_PRICE_OUT_PER_1M", 0.0),
        max_total_calls=_env_int("MATCHING_MAX_TOTAL_CALLS", 200),
        judge_seed=_env_opt_int("MATCHING_JUDGE_SEED"),
        judge_repeat_n=_env_int("MATCHING_JUDGE_REPEAT_N", 11),
        unaddressed_tolerance=_env_float("MATCHING_UNADDRESSED_TOLERANCE", 0.15),
        ledger_degraded_ratio=_env_float("MATCHING_LEDGER_DEGRADED_RATIO", 0.5),
        ablation_sample_size=_env_int("MATCHING_ABLATION_SAMPLE_SIZE", 3),
        skip_approval=_env_bool("MATCHING_SKIP_APPROVAL", False),
    )
