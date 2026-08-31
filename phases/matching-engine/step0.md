# Step 0: project-setup

## 읽어야 할 파일

- `CLAUDE.md` — 과제 CRITICAL 규칙 (특히 보안·데이터)
- `src/CLAUDE.md` — 아키텍처와 모듈 경계
- `tests/CLAUDE.md` — 테스트 실행 규칙

## 작업

Python 프로젝트 뼈대를 만든다. **코드 로직은 아직 쓰지 않는다.**

### 디렉터리

```
src/matching/
    __init__.py
    model/       __init__.py       # step1
    source/      __init__.py       # step2
    parser/      __init__.py       # step3
    rubric/      __init__.py       # step4
    scorer/      __init__.py       # step5
    judge/       __init__.py       # step6
    pipeline/    __init__.py       # step7
    api/         __init__.py       # step8
data/
    postings/    .gitkeep          # 공고 이미지 + 파싱 결과
    resumes/     .gitkeep          # 목업 이력서 (제출용)
tests/
    fixtures/    .gitkeep          # 테스트 전용 픽스처 (data/와 분리)
```

### `pyproject.toml`

- `[project]` name `matching-engine`, requires-python `>=3.11`
- 의존성: `pydantic>=2`, `fastapi`, `uvicorn`, `openai`, `httpx`, `python-dotenv`, `pillow`
- **OCR 의존성은 `[project.optional-dependencies]`의 `ocr` 그룹으로 분리한다** —
  `paddlepaddle`, `paddleocr`
- dev 의존성: `pytest`, `ruff`
- `[project.scripts]` — `matching = "matching.api.cli:main"`

**OCR을 선택 그룹으로 빼는 이유**: 파싱은 **데이터 준비 단계**이고 레포에는 그 결과물
(`ocr.json` · `requirements.json`)이 커밋된다. 평가자는 `pip install -e .`만으로 채점을
재현할 수 있고, 이미지를 직접 다시 파싱하고 싶을 때만 `pip install -e ".[ocr]"`을 한다.
paddle 계열은 설치가 무겁다(수백 MB) — 그걸 채점 재현의 전제로 만들면 안 된다.
- `[tool.ruff]` — line-length 100, `select = ["E", "F", "I", "UP", "B"]`
- `[tool.pytest.ini_options]` — `markers = ["live: 실제 OpenAI API를 호출하는 테스트"]`,
  `addopts = "-m 'not live'"`

**`markers`와 `addopts`가 중요하다.** `tests/CLAUDE.md`가 실물 API 호출 테스트를 기본
실행에서 빼라고 요구한다. 예산이 $5뿐이다.

### `.env.example`

```
OPENAI_API_KEY=
SARAMIN_ACCESS_KEY=
```

**값을 채우지 마라.** 빈 문자열만 둔다.

### `.gitignore` — `data/`에 넣을 규칙을 **파일 단위로** 못박는다

기존 `.gitignore`에 아래를 **정확히 이 형태로** 덧붙인다.

```gitignore
# data/ 는 통째로 무시하지 않는다. 무시할 파일만 지정한다.
data/postings/*/img_*.png      # 공고 이미지 원본 — 저작권·DB권 문제로 레포에 안 넣는다
data/.saramin_quota.json       # 호출 기록 (로컬 상태)
data/.judge_usage.json         # 토큰·비용 누적 (로컬 상태)
```

> ⛔ **`data/`나 `data/postings/`를 통째로 무시하지 마라.** 그러면 다음이 전부 사라진다 —
> `provenance.json`(출처 증거) · `ocr.json`(파싱 입력) · `requirements.json`(파싱 결과) ·
> `data/resumes/**`(목업 데이터셋, **과제 제출물**) · `data/runs/**`(실행 결과).
>
> **이것들이 없으면 clone한 사람이 확인할 수 있는 게 아무것도 없다.** 이미지를 뺀 대가로
> 남긴 유일한 증거가 해시인데, 그 해시가 든 파일까지 빠지면 이미지를 뺀 의미가 사라진다.
> 「이미지는 `.gitignore`」라는 문장을 `data/`로 확대 해석하는 것이 이 프로젝트에서
> **가장 조용하고 가장 치명적인 실수**다.

`data/resumes/`와 `data/runs/`는 **무시 목록에 아예 넣지 않는다.** 전자는 과제가 요구한
제출물이고, 후자는 「이 점수가 어떻게 나왔는가」의 유일한 기록이다.

### `src/matching/config.py`

`python-dotenv`로 `.env`를 읽는 설정 로더. 다음을 노출한다:

```python
class Settings:
    openai_api_key: str
    saramin_access_key: str | None
    weights: dict[str, float]      # 배점. 기본값 fact=35, judgment=65
    gate_kinds: list[str]          # 게이트로 취급할 조건 종류. 기본 ["license"]
    judge_disagreement_threshold: int  # 기본 2
    experience_saturation_k: float     # 경력 포화함수 파라미터

    # --- 파싱 (step 3). 값의 출처는 docs/OCR_EVIDENCE.md ---
    ocr_engine: str                # "paddle" | "vision". 기본 "paddle"
    header_x_threshold: int        # 기본 100. 실측 x0 띠 사이 빈 구간(60~125)의 중앙
    continuation_tolerance: int    # 기본 4 (px)
    ambiguous_fallback_ratio: float  # 기본 0.5. 넘으면 VLM 폴백 조건 — 지금은 예외

    # --- 모델·단가 (docs/COST_BUDGET.md) ---
    header_model: str              # 헤더 역할 분류용
    judge_model: str               # 심사위원 채점용
    price_in_per_1m: float         # 입력 100만 토큰당 USD
    price_out_per_1m: float
    max_total_calls: int           # 기본 200. 넘으면 예외 (step 6 CallBudget)

    # --- 심사위원 재현 (step 6) ---
    judge_seed: int | None         # 지원되면 고정. 안 되면 None
    judge_repeat_n: int            # 반복 안정성 N. 기본 11 (docs/RUBRIC_DESIGN.md:109)

    # --- 대장·소거 (step 12) ---
    unaddressed_tolerance: float   # 기본 0.15. **임의값**
    ledger_degraded_ratio: float   # 기본 0.5.  **임의값**
    ablation_sample_size: int      # 12-C 표본. 기본 3 (공고당 상위 n명)

    # --- 승인 게이트 (step 7) ---
    skip_approval: bool            # 기본 False. True면 RunResult.unapproved=True

def load_settings(path: str | None = None) -> Settings: ...
```

**임계값에 출처를 주석으로 박는다.** 실측에서 나온 값(`header_x_threshold` ·
`continuation_tolerance`)과 **임의로 정한 값**(`unaddressed_tolerance` ·
`ledger_degraded_ratio`)을 주석에서 구분한다. 임의값을 실측인 척하지 마라 —
어느 쪽인지 적어두면 나중에 무엇부터 재야 하는지가 분명해진다.

**가중치·게이트 조건·임계값은 전부 여기 있어야 한다.** `src/CLAUDE.md`가 "코드에 상수로
박지 않는다"고 요구한다. 기본값은 코드에 두되 `.env` 또는 인자로 덮어쓸 수 있게 한다.

키를 다루는 규칙:
- `Settings.__repr__`와 `__str__`은 `openai_api_key`를 **반드시 마스킹**한다 (`sk-***`).
- 로깅 함수에 Settings 객체를 통째로 넘기는 코드를 쓰지 마라.

### `.github/workflows/ci.yml`

`push`·`pull_request`에서 `ruff check .` + `pytest -q` 실행. Python 3.11.

## Acceptance Criteria

```bash
python3 -c "import tomllib,pathlib; tomllib.loads(pathlib.Path('pyproject.toml').read_text())"
ruff check src tests
pip install -e .
python -c "from matching.config import load_settings; s=load_settings(); assert 'sk-' not in repr(s) and 'sk-' not in str(s)"
python -m matching --help
```

**임포트 경로는 `matching`이지 `src.matching`이 아니다.** `[project.scripts]`와
`python -m matching`이 같은 이름을 쓰게 `pyproject.toml`에 패키지 루트를 `src`로 지정한다
(`[tool.setuptools.packages.find] where = ["src"]`). 이유: 평가자가 터미널에 붙여넣을 줄이
**하나여야 한다.** 문서마다 다른 임포트 경로가 나오면 그 줄이 두 개가 된다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트:
   - `src/CLAUDE.md`의 모듈 경계와 디렉터리가 일치하는가?
   - `.gitignore`가 `.env`를 막고 있는가? (`git check-ignore .env`가 성공해야 한다)
   - `CLAUDE.md` CRITICAL 규칙을 위반하지 않았는가?
3. `phases/matching-engine/index.json`의 step 0을 업데이트한다.

## 금지사항

- **`README.md`를 만들지 마라.** 이유: 과제 요구상 README는 지원자 본인이 직접 쓴다.
  초안·임시본도 금지다.
- **`.env` 파일을 만들지 마라.** `.env.example`만 만든다. 이유: 실수로 커밋될 위험.
- **로직을 구현하지 마라.** 이유: 이 step은 뼈대만이다. 빈 `__init__.py`로 둔다.
- **기본 의존성에 무거운 것(torch, transformers, paddleocr)을 넣지 마라.**
  `[ocr]` 선택 그룹에만 넣는다. 이유: 채점 재현의 전제를 가볍게 유지한다.
  ~~「OCR 도입은 아직 결정되지 않았다」~~ — **2026-08-31에 OCR로 확정됐다**
  (`src/CLAUDE.md:159`). 이 금지의 원래 이유는 소멸했고, 남은 이유는 설치 무게뿐이다.
