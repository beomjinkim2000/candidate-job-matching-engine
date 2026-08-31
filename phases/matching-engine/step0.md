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

**OCR을 선택 그룹으로 빼는 이유**: 파싱은 **데이터 준비 단계**이고, 레포에는 그 **산출물인
`requirements.json`**이 커밋된다 (`ocr.json`은 공고 본문이라 커밋하지 않는다 — 아래
「`.gitignore`」 절). 평가자는 `pip install -e .`만으로 **채점을 재현**할 수 있고,
이미지를 직접 다시 파싱하고 싶을 때만 `pip install -e ".[ocr]"`을 한다.
paddle 계열은 설치가 무겁다(수백 MB) — 그걸 채점 재현의 전제로 만들면 안 된다.

**단, 이미지를 재파싱하려면 이미지가 필요한데 그것도 레포에 없다.** 그래서 재파싱은
**공고 이미지를 직접 확보한 사람만** 할 수 있고, `provenance.json`의 해시로 **같은
이미지인지 확인**할 수 있다. 이게 「원문을 안 쌓으면서 재현 가능하게」의 한계선이다 —
**완전한 재현은 불가능하고, 그 사실을 숨기지 않는다.**
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
data/postings/*/img_*.png      # 공고 이미지 원본
data/postings/*/ocr.json       # 이미지의 전사본 = 공고 본문 그 자체
data/.saramin_quota.json       # 호출 기록 (로컬 상태)
data/.judge_usage.json         # 토큰·비용 누적 (로컬 상태)
```

#### 왜 `ocr.json`도 빼는가 — 그림과 글자를 다르게 취급하지 않는다

**이미지를 뺀 사유는 「그림 파일이라서」가 아니라 「그게 공고 본문이라서」다.**
`ocr.json`은 그 이미지를 **글자 그대로 옮긴 것**이므로 같은 사유가 그대로 걸린다.
이미지는 빼면서 전사본을 공개 레포에 올리면, 뺀 의미가 없을 뿐 아니라
**검색 가능한 형태로 만들어 더 나쁘다.**

세 문서가 이미 같은 말을 하고 있었고 이 줄만 어긋나 있었다:

| 문서 | 문장 |
|---|---|
| `CLAUDE.md` (과제 CRITICAL) | 「공고 원문 텍스트 **복사·붙여넣기 금지**」 |
| `src/CLAUDE.md` | 「원본을 적재하지 않는다 — 공고 이미지·이력서 원문·**OCR 텍스트**는 처리 후 파기하고, **구조화된 조건·루브릭·점수·Link만 남긴다**」 |
| `docs/LEGAL_ARCHITECTURE.md` | 「원문을 쌓으면 그게 곧 DB 복제」 |

#### 그러면 clone한 사람은 무엇으로 확인하나

| 파일 | 커밋 | 무엇인가 |
|---|---|---|
| `img_*.png` | ✗ | 공고 본문 (그림) |
| `ocr.json` | ✗ | 공고 본문 (글자). **같은 것이다** |
| `provenance.json` | ✅ | **해시만.** 원문이 아니다 — 이미지를 가진 사람이 동일성만 대조 |
| `requirements.json` | ✅ | **구조화된 조건** + 좌표 + `line_ids`. `src/CLAUDE.md`가 명시적으로 「남긴다」고 한 것 |
| `data/resumes/**` · `data/runs/**` | ✅ | 우리가 만든 것 |

**`requirements.json`은 전사본이 아니라 산출물이다.** 조건 단위로 잘리고 종류·근거등급·
좌표가 붙은 구조이며, 이게 없으면 「이미지에서 파싱했다」를 보여줄 방법이 사라진다.
`ocr.json`은 그 중간 산물이고 **로컬에만 있으면 된다** — UI도 로컬에서 돈다.

**경계는 「원문이냐 산출물이냐」다.** 그림/글자가 아니다.

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

#### 공개 레포에 무엇을 싣는가 — 이 결정을 여기서 한다

과제 제출물은 **「코드 + README + 목업 데이터셋 + 최소 테스트」**다. 그 범위를 정한다.

```gitignore
# --- 제작 과정 메타데이터. 제출물이 아니다 ---
phases/                        # 하네스 step 정의 (작업 지시서)
Plans.md                       # 진행 상황 추적
docs/HARNESS_SCOREBOARD.md     # 계획 심사 이력
docs/HARNESS_VALIDITY.md       # 실행기 검증 기록
scripts/                       # 하네스 실행기 — 외부 스켈레톤이지 우리 코드가 아니다
spike/.venv/                   # 스파이크 가상환경
spike/ocr_check/.vision_ocr    # 컴파일 산출물
```

**`spike/ocr_check/`의 소스는 남긴다** (`*.py` · `*.swift` · `requirements.txt`).
`docs/OCR_EVIDENCE.md` §6이 실측 재현 경로로 지목한 코드이고, **임계값 3개의 출처**다.

##### 왜 빼는가 — 은폐가 아니라 범위다

`.claude/`·`harness.toml`을 뺀 것과 **같은 이유**다. 이것들은 「이 프로젝트를 만드는 데
쓴 도구」이지 「이 프로젝트」가 아니다. 남기면 평가자가 제출물과 작업 로그를 구분해야 한다.

**동시에, 숨기는 것이 되면 안 된다.** 과제문은 「**AI 활용은 자유**이되 본인의 문제 정의와
판단이 드러나야 한다」이다 — AI를 썼다는 사실은 감출 것이 아니다. 그래서:

- **`docs/`의 설계 근거 문서는 전부 공개한다** — `TRADEOFFS.md`(결정/버린 것/틀리는 조건) ·
  `LEGAL_ARCHITECTURE.md` · `OCR_EVIDENCE.md` · `COST_BUDGET.md` · `SCHEDULE.md` ·
  `RUBRIC_DESIGN.md` · `DEMO.md` · `OPERATIONS.md`. **판단의 실물이 여기 있다**
- **심사를 6라운드 돌렸다는 사실은 README에 사용자가 자기 언어로 쓴다.**
  원본 로그를 그대로 올리는 것과, 「무엇을 배웠는지」를 본인이 정리하는 것은 다르다 —
  **후자가 「본인의 판단」이다**

##### 이 결정이 틀리는 조건

평가자가 **「AI에게 무엇을 어떻게 지시했는가」 자체를 보고 싶어 하는** 경우.
그때는 `phases/`를 공개하는 편이 낫다 — step 파일 14개가 **가장 구체적인 판단의 기록**이다.
**되돌리기 쉽다**: 위 무시 목록에서 `phases/` 한 줄만 지우면 된다.

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
