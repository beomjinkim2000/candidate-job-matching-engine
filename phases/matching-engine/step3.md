# Step 3: posting-parser

> **2026-09-01 02:40 KST 개정.** 이전 판은 `parser/vision.py`에서 **OpenAI Vision에
> 이미지를 보내 bbox를 받으라**고 지시했다. 그건 `src/CLAUDE.md:161`(「이미지를 LLM에
> 보내지 않는다. 좌표의 출처는 OCR 하나뿐이다」)과 `:174-176`(「VLM은 bbox를 지어낸다 →
> **G4가 지어낸 좌표를 검사하는 꼴**」)을 정면으로 뒤집는 지시였다.
> **폐기했다.** 이 파일이 정본이고, `parser/vision.py`는 더 이상 존재하지 않는다.

## 읽어야 할 파일

- `src/CLAUDE.md` — 「파싱 — OCR이 주, LLM은 헤더 분류만」 · 「필수/우대 판정」 5단계 사다리 · 「LLM 사용 범위」
- `docs/OCR_EVIDENCE.md` — **엔진 선정의 실측 근거. 임계값 3개가 전부 여기서 나온다**
- `docs/KAIREN_OS_ANALYSIS.md` — 근거 등급 E2/E1/E0
- `src/matching/model/objects.py` (step 1) — `Requirement`, `BBox`, `Span`
- `src/matching/source/base.py` (step 2) — `PostingRef`

## 전제조건 — 없으면 이 step은 `blocked`다

**시작 전에 확인한다.**

```bash
ls data/postings/*/img_*.png | wc -l          # 2 이상 (공고 2개)
ls data/postings/*/provenance.json | wc -l    # 2 — step2의 acquire가 돌았는가
```

**`provenance.json`이 없으면 이미지를 놓기만 하고 `python -m matching acquire`를 안 부른
것이다.** 그것도 `blocked` 사유다 — 출처 증거 없이 파싱하면, 나중에 그 조건들이 어느
이미지에서 나왔는지 증명할 방법이 사라진다.

**0장이면 `index.json`의 step 3을 `"status": "blocked"`,
`"blocked_reason": "공고 이미지 미확보 — 요구 ①②의 입력이 없다"`로 기록하고 즉시 중단한다.**
픽스처로 우회하지 마라. 이 step은 파이프라인에서 **유일하게 실제 공고를 읽는 곳**이고,
여기가 비면 요구 ①②가 통째로 미충족인데 뒤 step들은 픽스처로 초록이 된다.
`harness.md:124`의 규약(외부 자원 부재 → `blocked` 후 중단)이 이 자리에 적용된다.

이미지가 없을 때 무엇을 하는지는 `docs/SCHEDULE.md` §2 「이미지 확보 분기」에 시각과 함께 적혀 있다.

## 작업

**공고 이미지 → `Requirement` 목록.** 파이프라인에서 유일하게 이미지를 다루는 곳이다.

산출물을 **파일 3개로 쪼갠다.** 합치지 마라.

| 파일 | 무엇 | 누가 만드나 | 다시 만드는 계기 |
|---|---|---|---|
| `data/postings/{id}/img_{n}.png` | 원본 이미지 | step 2 | 공고가 바뀔 때 |
| `data/postings/{id}/ocr.json` | **엔진이 뱉은 줄+좌표 그대로.** 손대지 않는다 | 3-A | **엔진을 바꿀 때만** |
| `data/postings/{id}/requirements.json` | 조립된 조건 목록 | 3-B~3-D | **규칙을 바꿀 때마다** |

**왜 쪼개나**: 엔진 교체와 규칙 수정은 원인이 다르다. 합치면 들여쓰기 임계값 하나 바꿀
때마다 OCR을 다시 돌려야 하고(공고당 3~40초), 무엇보다 **G1 검산이 대조할 두 번째 파일이
사라진다.** `requirements.json`의 모든 조건은 `ocr.json`의 줄을 `line_ids`로 역참조하고,
`ocr_sha256`으로 어느 OCR 결과에서 나왔는지 못박는다.

---

### 3-A. `src/matching/parser/ocr.py` — 줄과 좌표

```python
class OcrLine(BaseModel):
    id: str                  # "L-001"
    text: str
    conf: float
    bbox: BBox               # img_w/img_h 포함
    x0: int                  # bbox.x1 별칭. 들여쓰기 판정이 이것만 본다
    height: int

class OcrResult(BaseModel):
    engine: Literal["paddle", "vision"]
    engine_version: str
    image_path: str
    img_w: int; img_h: int
    lines: list[OcrLine]     # 읽는 순서: y 오름차순, 같으면 x 오름차순
    avg_conf: float
    elapsed_sec: float

def run_ocr(image_path: Path, engine: str = "paddle") -> OcrResult: ...
```

엔진은 **둘 다 지원하고 기본은 `paddle`**이다.

| 엔진 | 어디서 도나 | 실측 평균 신뢰도 | 소요 | 왜 |
|---|---|---|---|---|
| `paddle` | 윈도우·맥·리눅스 (`pip` 하나) | **0.936** | 첫 회 모델 다운로드 후 ~40초 | **기본.** 평가자가 어느 OS든 재현 가능 |
| `vision` | macOS만 (내장 Vision) | 0.802 | ~3초 | 띄어쓰기가 살아 있다. 개발 중 빠른 반복용 |

**EasyOCR은 후보에서 뺐다.** 같은 이미지에서 평균 신뢰도 **0.396**이고, 2배·3배로 키워도
0.382 / 0.396이라 해상도가 아니라 모델 문제였다. 근거는 `docs/OCR_EVIDENCE.md` §1.

**PaddleOCR을 기본값 그대로 쓰지 마라.** 전 코어를 쓴다 — 2026-09-01 00:50에 실제로
맥이 멈췄다. 반드시:

```python
# paddle import 보다 먼저 걸어야 먹는다
for v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ.setdefault(v, "2")
PaddleOCR(lang="korean", cpu_threads=2,
          use_doc_orientation_classify=False, use_doc_unwarping=False,
          use_textline_orientation=False)
```

결과를 `ocr.json`에 쓰고, **같은 이미지에 대해 파일이 이미 있으면 다시 돌리지 않는다.**
`--reocr` 플래그로만 재실행한다.

---

### 3-B. `src/matching/parser/layout.py` — 줄을 섹션과 항목으로

**여기가 이 step의 핵심이고, LLM이 안 들어오는 자리다.**

```python
LineRole = Literal["header", "item", "continuation", "ambiguous"]

class Block(BaseModel):
    header: OcrLine | None
    header_role: str | None      # 3-C가 채운다
    items: list[list[OcrLine]]   # 항목 하나 = 줄 1개 이상 (이어지는 줄 병합)

def classify_lines(result: OcrResult, settings: Settings) -> dict[str, LineRole]: ...
def build_blocks(result: OcrResult, roles: dict[str, LineRole]) -> list[Block]: ...
```

**판정 순서 — 위에서부터, 결론이 나면 멈춘다.**

| # | 조건 | 판정 | 왜 이 신호인가 |
|---|---|---|---|
| 1 | `x0 < settings.header_x_threshold` | `header` | 실측: 한 공고의 x0가 4개 띠로 갈리고 60과 125 사이에 **65~70px 빈 구간**이 있다. 임계 100은 그 빈 구간 한가운데다 |
| 2 | 첫 글자가 불릿 (`·` `•` `-` `–` `※` `▶` `√` `✓` `◦`) | `item` | |
| 3 | 불릿 없음 **그리고** `x0 > 직전 item의 x0 + settings.continuation_tolerance` | `continuation` | 앞 항목에 **병합한다** |
| 4 | 그 외 | `ambiguous` | 3-C가 문자열만 보고 판정 |

**글자 크기(`height`)로 헤더를 가르지 마라.** 확정된 규칙인 줄 알았는데 실측에서 틀렸다 —
테스트 공고의 모든 줄이 12~14px였고, 헤더는 크기가 아니라 **굵기와 색**으로 구분돼 있었다.
OCR은 굵기·색을 안 준다. 그래서 **x 들여쓰기가 유일하게 남은 기하 신호**다.
(`docs/OCR_EVIDENCE.md` §2)

**3번 병합 규칙을 빼지 마라.** 실측에서 한 조건이 두 줄에 걸쳐 있었고, 병합하지 않으면
**조건 하나가 조건 둘로 세어져 배점이 갈라진다.** 이건 표시 문제가 아니라 점수 문제다.

임계값 2개(`header_x_threshold=100`, `continuation_tolerance=4`)는 **`settings`에서 온다.**
코드에 박지 마라. 값의 출처는 위 표에 적힌 실측이고, **다른 공고에서 안 맞을 수 있다** —
그때 무엇을 보고 고치는지는 3-E의 진단 출력이 답한다.

**두 엔진이 독립적으로 같은 4개 띠를 만들었다.** 그게 이 임계값이 우연이 아니라는
유일한 근거다 (`docs/OCR_EVIDENCE.md` §3). 한 엔진에서만 확인된 임계값은 쓰지 마라.

---

### 3-B′. 한 공고에 직무가 여러 개일 때 — **KT 공고가 이 경우다**

확보한 공고 A(KT)는 `직무 | 수행업무 및 우대사항 | 근무지` **3열 표**에 직무 3개
(NW인프라운용 · B2B컨설팅&세일즈 · B2C마케팅&세일즈)가 들어 있다. 줄 단위 OCR은
**셀 소속을 잃는다** — 「B2B 솔루션·서비스를 제안하고 프로세스를 수행합니다」가 어느 직무
것인지 모르면 **점수가 틀린다.** 표시 문제가 아니다.

```python
class PositionBand(BaseModel):
    label: str          # 대상 직무명. **런타임 인자다**
    y_top: int
    y_bottom: int

def split_positions(result: OcrResult, target: str | None) -> PositionBand | None: ...
```

**표를 표로 파싱하지 않는다.** 필요한 건 셀 격자가 아니라 **관심 직무의 y 구간** 하나다.

1. `target`(예: `"NW인프라운용"`)과 일치하는 줄을 찾아 그 `y_top`을 잡는다
2. **같은 x 띠에 있는 다음 줄**(= 다음 직무 라벨. 첫 열은 x0가 같다)의 `y`를 `y_bottom`으로 잡는다.
   없으면 표의 끝
3. 그 구간 **밖**의 줄은 3-B의 대상에서 **제외**하되, **표 자체보다 위/아래에 있는 공통 섹션**
   (KT의 「지원자격」·「채용절차」)은 **살린다**

**공통 섹션을 같이 살리는 것이 핵심이다.** KT의 「정규 4년제 대학을 졸업했거나…」는 표 밖에
있고 전 직무에 걸린다. 표 구간만 잘라내면 **자격요건이 통째로 사라진다.**

- `target`이 `None`이면 분할하지 않는다 (공고 B/넥슨처럼 단일 직군인 경우)
- `target`은 **CLI 인자 `--position`으로 들어온다.** 코드에 직무명을 박지 마라 —
  박는 순간 직군 무관 일반화 위반이다. 값은 `PostingRef.target_position`에 저장돼
  `requirements.json`과 결과 JSON에 함께 실린다
- **PP-StructureV3 같은 표 인식 모델을 쓰지 않는다.** 로컬 실행에서 맥이 멈췄고
  (2026-09-01 00:40), 모델 10여 개를 내려받는다. y 구간 분할로 충분한데 그 위험을 살 이유가 없다
- `split_positions`가 `target`을 **못 찾으면 예외**를 던진다. 조용히 전체를 파싱하면
  세 직무의 조건이 한 지원자에게 다 걸린다

**AC에 이 케이스를 넣는다** — 3직무 표 픽스처에서 2번째 직무를 지정했을 때
1·3번째 직무의 항목이 `Requirement`에 **한 건도** 안 들어오는지.

---

### 3-C. `src/matching/parser/header_role.py` — LLM이 들어오는 유일한 자리

```python
HeaderRole = Literal["requirement", "preferred", "duty", "context", "excluded"]

def classify_headers(
    headers: list[str],          # 문자열만. 좌표도 이미지도 안 보낸다
    ambiguous: list[str],
    client,
) -> dict[str, HeaderRole]: ...
```

- **이미지를 보내지 마라.** 헤더 문자열 4~8개 + 모호한 줄 몇 개, 총 **1,000토큰 미만**이다
- **공고당 1회.** 공고 2개면 완주 전체에서 **2회**다
- `temperature=0`, structured output. 모델은 `settings.header_model`에서 온다
- 결과를 `data/postings/{id}/header_roles.json`에 캐시한다. **같은 헤더 집합이면 다시 안 부른다**

역할 5종이 하는 일:

| 역할 | 예 | 채점에서 |
|---|---|---|
| `requirement` | 자격요건 · 지원자격 · 필수사항 | 필수 조건. 배점 높음 |
| `preferred` | 우대사항 · 우대조건 | 우대 조건. 배점 낮음 |
| `duty` | 담당업무 · 주요업무 | 조건 아님. **사다리 3단계의 대조군**으로만 쓴다 |
| `context` | 인재상 · 팀 소개 | 조건 아님. 표시만 |
| `excluded` | 복리후생 · 근무조건 · 전형절차 · 제출서류 | **채점에서 뺀다** |

**`excluded`가 없으면 조용히 틀린다.** 복리후생의 「재택근무 가능」이 지원자에게 요구되는
조건으로 들어간다. 이건 가상의 위험이 아니라 실제 공고에서 확인한 것이다.

**사전을 코드에 하드코딩하지 마라.** 위 표의 예시는 **역할의 뜻을 설명하는 것이지 매칭
목록이 아니다.** 사전을 쓰면 「이런 분을 찾고 있어요」 같은 표현에 조용히 실패한다.
LLM에게 문자열만 주는 이유가 이것이다.

> **직군 무관 일반화와 충돌하지 않는다.** 여기 등장하는 어휘는 전부 **문서 구조 어휘**이고
> 직군·스킬 어휘가 아니다. 「자격요건」은 개발·마케팅·간호 공고에 다 나온다.
> `header_role.py`에 직군명·스킬명이 한 글자라도 들어가면 그건 위반이다 — AC가 grep으로 막는다.

---

### 3-D. `src/matching/parser/classify.py` — 필수/우대 판정 사다리

`src/CLAUDE.md`의 순서 그대로. **위에서부터, 결론이 나면 멈춘다.**

| 단계 | 조건 | 결과 | 근거 등급 |
|---|---|---|---|
| 1 | 소속 블록의 `header_role`이 `requirement`/`preferred` | 확정 | `E2` |
| 2 | 항목 텍스트에 수식어가 있음 (「필수」「반드시」「우대」「있으면」) | 확정 | `E2` |
| 3 | `duty` 블록에 대응하는 항목이 있음 | required 쪽 | `E1` |
| 4 | `occurrences >= 2` | required 쪽 | `E1` |
| 5 | 시각 강조 | **판정을 바꾸지 않고** 등급만 | `E0` |

```python
def classify(item: ParsedItem, context: PostingContext) -> tuple[RequirementKind, EvidenceGrade, int]:
    """returns (kind, evidence_grade, ladder_step)"""
```

- 1~2단계에서 결론이 안 나면 기본값은 `preferred`. 이유: 필수로 잘못 분류하면 게이트나
  큰 감점으로 이어져 등수가 크게 흔들린다. 반대 방향의 오류가 덜 해롭다
- **5단계는 OCR이 굵기·색을 안 주므로 현재 경로에서는 발동하지 않는다.** 자리는 남기되
  `emphasized`는 항상 `False`다. **「구현했는데 안 쓴다」가 아니라 「입력이 없다」**이므로
  그 사실을 `parse_report`에 적는다
- **`gate` 종류는 여기서 정하지 않는다.** `settings.gate_kinds`에 걸리는 조건만 step 5에서 승격

---

### 3-E. `src/matching/parser/__init__.py` — 조립과 진단

```python
ParseMode = Literal["ocr", "ocr+vlm_fallback"]

class ParseReport(BaseModel):
    parse_mode: ParseMode
    ocr_engine: str
    ocr_sha256: str                  # ocr.json의 해시
    line_count: int
    role_counts: dict[str, int]      # header/item/continuation/ambiguous 각 몇 줄
    merged_continuations: int
    excluded_blocks: list[str]
    llm_calls: int                   # 이 공고를 파싱하며 부른 LLM 횟수
    emphasis_available: bool         # 항상 False (OCR이 굵기를 안 준다)

def parse_posting(ref: PostingRef, settings: Settings) -> tuple[list[Requirement], EvidenceGraph, ParseReport]: ...
```

- 모든 `Requirement`는 `source_bbox`(그 항목을 이룬 줄들의 합집합 박스)와
  `line_ids`(역참조), `source_span`(`ocr.json` 안 문자 오프셋)을 **셋 다** 갖는다.
  좌표를 못 만드는 항목은 **버린다** — G4에서 어차피 차단된다
- **`provenance.json`의 `ocr_engine`·`ocr_sha256`을 채운다.** step2가 만든 파일에 이
  두 칸이 비어 있고, 파싱이 끝나야 알 수 있다. 안 채우면 AC가 막는다
- `ParseReport`는 `requirements.json` 안에 함께 저장되고 **UI 하단에 그대로 표시된다.**
  이유: 임계값 2개가 실측에서 나온 임의값이라, 다른 공고에서 빗나갔을 때
  **`role_counts`만 보면 어디가 틀어졌는지 보인다** (`ambiguous`가 절반을 넘으면 레이아웃이 다른 것이다)

**VLM 폴백 — 지금은 만들지 않는다.**

`src/CLAUDE.md:178`이 「2단계가 섹션을 못 찾으면 그때만 VLM 1회」를 허용한다. 발동 조건은
**`headers`가 0개이거나 `ambiguous`가 전체의 50%를 넘을 때**다. 그러나 이 step에서는
`parse_mode` 필드와 그 판정 코드까지만 만들고 **`ocr+vlm_fallback` 경로는 `NotImplementedError`로
둔다.** 이유가 둘이다.

1. 지금 확보한 공고에서 발동하지 않는다. 안 도는 코드를 24시간 안에 넣는 건 위험을 늘린다
2. **발동하면 그 조건들의 좌표는 지어낸 값**이므로, 폴백을 쓰는 순간 `verify.py`가
   OCR 줄과 대조해야 하고 매칭 실패한 조건은 G4가 차단해야 한다. 그 대조 로직까지가 한 묶음이다

발동 조건에 걸리면 **예외를 던지고 멈춘다.** 조용히 넘어가지 않는다.

---

### 3-F. `src/matching/parser/verify.py` — 코드 검증 (LLM 아님)

```python
def verify(requirements: list[Requirement], ocr: OcrResult) -> list[Violation]: ...
```

기본 경로에서는 조건이 **OCR이 읽은 줄에서만** 나오므로 검증이 거의 자명하다. 그래도 짠다:

- 각 `Requirement.text`가 `line_ids`가 가리키는 줄들을 이어붙인 문자열의 **부분 문자열인지**
- `source_span`으로 자른 결과가 `text`와 **정확히 일치**하는지 (유사도 아님)
- `source_bbox`가 그 줄들의 합집합 박스와 일치하는지

**검증을 LLM에게 시키지 마라.** 같은 모델에게 「네가 읽은 게 맞냐」고 물으면 같은 실수를
두 번 한다 (`docs/TRADEOFFS.md` E-1).

## 예산 (완주 1회 기준)

| 항목 | 호출 | 토큰 | 근거 |
|---|---|---|---|
| 헤더 역할 분류 | 공고 2개 × 1회 = **2회** | 입력 <1,000 / 출력 <300 | 문자열만 보낸다 |
| OCR | **0회** (로컬) | — | |
| 검증 | **0회** (코드) | — | |
| **step 3 합계** | **2회** | | 완주 전체 예산표는 `docs/COST_BUDGET.md` |

**이 step은 이 파이프라인에서 가장 싼 축이다.** 이미지 토큰을 안 쓰기 때문이다 —
이전 판(VLM 경로)이었으면 이미지 2장 × 고해상도로 이 자리에서만 수천 토큰을 썼다.

## Acceptance Criteria

```bash
ruff check src/matching/parser
pytest tests/test_parser.py -q
grep -rniE "python|java|마케팅|디자이너|간호|영업" src/matching/parser && echo "직군 어휘 발견 — 위반" && exit 1 || echo "직군 어휘 없음"
python3 -c "
import json,pathlib,glob,hashlib
reqs=sorted(glob.glob('data/postings/*/requirements.json'))
# ⚠ 개수를 먼저 센다. 0건이면 아래 루프가 안 돌아 조용히 통과한다
assert len(reqs)==2, f'requirements.json이 2개여야 한다 (현재 {len(reqs)})'
for f in reqs:
    d=json.loads(pathlib.Path(f).read_text()); r=d['parse_report']
    dirp=pathlib.Path(f).parent
    assert d['requirements'], f'{f}: 조건 0건'
    assert r['parse_mode']=='ocr', r['parse_mode']
    assert r['llm_calls']<=1, r['llm_calls']
    assert all(x['line_ids'] for x in d['requirements']), 'line_ids 빈 조건 있음'
    # --- provenance 대조 (step2가 만든 파일) ---
    prov=json.loads((dirp/'provenance.json').read_text())
    assert prov['ocr_sha256']==r['ocr_sha256'], 'provenance와 requirements의 OCR 해시 불일치'
    real=hashlib.sha256((dirp/'ocr.json').read_bytes()).hexdigest()
    assert real==r['ocr_sha256'], 'ocr.json 실물 해시가 기록과 다르다'
    for p,h in zip(sorted(dirp.glob('img_*.png')), prov['image_sha256']):
        assert hashlib.sha256(p.read_bytes()).hexdigest()==h, f'{p} 해시 불일치'
    print(f, 'ambiguous', r['role_counts'].get('ambiguous',0), '/', r['line_count'])
print('ok')
"
```

**첫 줄의 `assert len(reqs)==2`가 없으면 이 AC는 파일 0건일 때 조용히 통과한다.**
R1에서 정확히 그 지적을 받았다 — 「AC가 전부 픽스처 전용이라 `requirements.json`이 안
생겨도 통과한다」. **비어 있는 glob으로 도는 루프는 검사가 아니다.**

`tests/test_parser.py`는 **실물 API도 실물 OCR도 부르지 않는다.** 고정 `OcrResult`
픽스처(직접 지어낸 가상 공고)로 검증한다:

- **3-B 판정 4가지가 각각 발동하는지** — `x0<100`인 줄이 `header`, 불릿 줄이 `item`,
  더 들여쓴 무불릿 줄이 `continuation`, 나머지가 `ambiguous`
- **이어지는 줄이 앞 항목에 병합되어 조건 수가 1개인지** (병합 안 하면 2개가 된다)
- **`excluded` 블록의 항목이 `Requirement`로 안 올라오는지** — 「재택근무 가능」 케이스
- 사다리 5단계 각각 — 헤더가 있으면 1단계에서 멈추고 `E2`인지 / 아무 신호도 없으면
  `preferred` + `E0`인지 / `emphasized`가 판정을 안 바꾸는지
- **`ambiguous`가 50%를 넘는 픽스처에서 폴백 조건이 감지되고 예외가 나는지**
- `line_ids`가 빈 조건이 하나도 없는지 (G4의 선행 조건)

## 검증 절차

1. 위 AC 커맨드를 전부 실행한다.
2. 아키텍처 체크리스트:
   - **이미지가 LLM에 간 곳이 한 군데도 없는가?** (`grep -rn "image_url\|b64_json\|base64" src/matching/parser` 가 비어야 한다)
   - 모든 `Requirement`에 `source_bbox`·`line_ids`가 있는가? (G4)
   - 모든 `Requirement`에 `evidence_grade`가 있는가? (G5)
   - `review_status`가 전부 `"draft"`인가?
   - LLM이 필수/우대를 판정하고 있지 않은가? (헤더 **역할**만 분류한다)
   - 임계값 2개가 `settings`에서 오는가?
3. `index.json`의 step 3을 업데이트한다. `summary`에 **`role_counts` 실측치와
   `ambiguous` 비율**을 남긴다 — 다음 step이 파싱 품질을 알아야 한다.
4. **`index.json`의 step 2에 있는 `requirement_status.req2_image_parse`를 갱신한다.**
   위 AC가 전부 통과했으면 `"verified"`, 아니면 `"pending"`으로 둔다.
   **`req2`의 실증은 이 step에서만 판정할 수 있다** — 파싱을 실제로 하는 곳이 여기다.
   step 2는 코드만 만들었으므로 그 칸을 채울 자격이 없다.

   ```bash
   python3 -c "
   import json,pathlib,glob
   p=pathlib.Path('phases/matching-engine/index.json'); d=json.loads(p.read_text())
   s2=next(s for s in d['steps'] if s['step']==2)
   ok = len(glob.glob('data/postings/*/requirements.json'))==2
   s2['requirement_status']['req2_image_parse'] = 'verified' if ok else 'pending'
   p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+chr(10))
   print('req2_image_parse =', s2['requirement_status']['req2_image_parse'])
   "
   ```

## 금지사항

- **이미지를 LLM에 보내지 마라.** 이유: VLM은 bbox를 지어내고, 그러면 G4가 지어낸 좌표를
  검사하게 되어 「원문을 복붙하지 않았다」의 유일한 기계적 증명이 무효가 된다
  (`src/CLAUDE.md:174-176`). **이것이 이 프로젝트의 검증 가능한 차별점 전체다.**
- **공고 원문 텍스트를 코드·픽스처·테스트에 붙여넣지 마라.** 이유: 과제 CRITICAL.
  테스트 픽스처는 **직접 지어낸 가상 공고**로 만든다.
- **섹션 제목 사전을 코드에 하드코딩하지 마라.** 이유: 사전은 목록 밖 표현에 조용히
  실패한다. 3-C가 LLM에 문자열만 보내는 이유가 이것이다.
- 직군 이름·스킬 이름을 `parser/` 어디에도 넣지 마라. 이유: 과제 CRITICAL — 직군 무관
  일반화. 쓰는 신호는 **좌표·불릿·반복·헤더 역할**뿐이다.
- **글자 크기로 헤더를 가르지 마라.** 이유: 실측에서 모든 줄이 12~14px였다. 확정됐다고
  믿었던 규칙이고 틀렸다.
- 같은 이미지를 두 번 OCR하지 마라. `ocr.json`을 먼저 확인한다.
- 시각 강조(5단계)로 kind를 바꾸지 마라. 이유: 강조는 디자인 관행이지 요구 강도가 아니다.
- **VLM 폴백을 구현하지 마라.** 이유는 3-E에 적었다. 조건 감지와 예외까지만이다.
