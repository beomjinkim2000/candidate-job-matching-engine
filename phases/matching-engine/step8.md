# Step 8: entrypoint-api

## 읽어야 할 파일

- `CLAUDE.md` — 「엔진」 절 (CLI 또는 API 엔드포인트 **하나**)
- `src/matching/pipeline/run.py`, `explain.py` (step 7)
- `src/matching/config.py` (step 0)

## 작업

과제 요구는 **단일 진입점**이다. CLI와 API 둘 다 만들되 **같은 함수 하나를 부른다.**
로직을 양쪽에 복제하지 마라.

### `run.py` — 레포 루트. **평가자가 실행하는 유일한 한 줄**

```bash
python run.py
```

윈도우·맥 공통. 하는 일 순서:

1. `.env`에 `OPENAI_API_KEY`가 있으면 **묻지 않고 넘어간다**
2. 없으면 터미널에서 **키를 붙여넣게 한다** — `getpass.getpass()`로 받는다.
   **입력이 화면에 찍히면 안 된다.** `input()`을 쓰지 마라
3. 받은 키를 `.env`에 쓴다 (`OPENAI_API_KEY=...`). 파일 권한을 `0o600`으로 준다.
   **기존 `.env`를 통째로 덮어쓰지 말고 해당 줄만 갱신한다**
4. `uvicorn`을 `127.0.0.1:8000`으로 띄운다. **`0.0.0.0`으로 바인딩하지 마라**
5. `webbrowser.open("http://127.0.0.1:8000")`

```python
def ensure_key() -> None: ...      # 2~3
def main() -> None: ...            # 1~5
```

**키를 화면에도 로그에도 찍지 마라.** 확인이 필요하면 `sk-...` 뒤 4자리만,
또는 「키가 설정됨」 한 줄이면 된다. 저장 후 **`.env`가 `.gitignore`에 걸리는지
`git check-ignore .env`로 확인**하고, 안 걸리면 저장하지 말고 중단한다.

### `src/matching/api/cli.py`

```bash
python -m matching score \
    --posting data/postings/{posting_id} \
    --resumes data/resumes/{set} \
    [--position "B2C마케팅&세일즈"] \
    [--source local|saramin] \
    [--ocr-engine paddle|vision] \
    [--json] \
    [--no-judge]
```

`--position`은 **한 공고에 직무가 여러 개일 때** 대상을 고른다 (KT 공고).
`step3.md` 3-B′가 이 값으로 표의 y 구간을 자른다. **직무명을 코드에 박지 마라** —
런타임 인자라서 직군 무관 일반화를 안 깬다.

- 기본 출력은 `explain()`의 사람이 읽는 텍스트 (랭킹 전체)
- `--json`이면 `RunResult`를 그대로 stdout에
- `--no-judge`는 판단 층을 건너뛴다 (개발용. **결과에 "판단 층 생략" 경고를 반드시 찍는다**)

### `src/matching/api/server.py`

FastAPI. **채점을 시작하는 엔드포인트는 하나다.**

```
POST /score
  body: { "posting_id": str, "resume_ids": [str], "options": {...} }
  resp: RunResult
```

나머지는 **읽기이거나 승인**이고, 채점을 일으키지 않는다.

```
GET  /runs/{run_id}          # 저장된 결과 조회 (재채점하지 않는다)
GET  /image/{posting_id}     # 공고 이미지 원본 — bbox 네모를 그릴 바탕
POST /prepare                # 공고 파싱 → RubricProposal (전부 draft). 채점 안 한다
POST /approve                # 승인 화면이 부르는 곳. 아래
GET  /                       # 정적 UI
```

**과제가 요구한 "하나"는 「실행 진입점이 하나」다.** 실행은 `run.py` 한 줄이고,
채점은 `POST /score` 하나다. 읽기와 승인을 거기 밀어 넣으면 오히려 한 엔드포인트가
세 가지 일을 하게 된다.

#### `POST /approve` — 이게 없으면 배지가 거짓말이 된다

```
POST /approve
  body: { "posting_id": str, "posting_revision": str,
          "decisions": [ {"criterion_id": str,
                          "action": "approve" | "flip" | "delete"} ] }
  resp: { "approved_at": iso8601,
          "criteria": [ { "criterion_id": str,
                          "requirement_id": str,
                          "kind": "required"|"preferred"|"gate",   # ← 뒤집힌 결과
                          "review_status": "human_validated",
                          "deleted": bool } ] }
```

**응답에 `kind`가 반드시 있어야 한다.** 필수/우대는 `Requirement.kind`에 있고 `Criterion`에는
없다. 그런데 화면이 뒤집기 버튼을 누르면 **그 줄을 「우대」로 고쳐 그려야 한다** —
응답이 `approved_at`과 항목 목록뿐이면 화면은 무엇을 보고 고쳐 그릴지 알 수 없고,
결국 전체를 다시 불러와야 한다. **`flip`은 `Requirement.kind`를 바꾸므로 그 값을 돌려준다.**

- `src/CLAUDE.md:63`이 승인 화면에서 할 수 있는 것을 셋으로 정했다 —
  **항목 승인 / 필수·우대 뒤집기 / 항목 삭제.** `action` 3종이 그것이다
- **가중치는 못 바꾼다.** body에 `weight`가 오면 400
- `flip`·`delete`는 `Link`로 원래 판정을 남긴다 — `C-03 ──contradicts──▶ R-03`
- `posting_revision`이 현재 공고의 `modification-timestamp`와 다르면
  **`ApprovalStale`(409)** — 검산 G7

**이 엔드포인트가 없으면 `review_status`가 영원히 `draft` 한 값이다.** 그러면
UI의 「AI 초안 / 사람 확인함」 배지는 절대 바뀌지 않는 장식이 된다.
**바뀌지 않는 배지를 화면에 두는 것은 거짓말이다.**

#### `GET /image/{posting_id}` — 저장한 좌표가 값을 갖는 유일한 이유

bbox를 저장해 놓고 이미지를 못 주면, 근거를 클릭했을 때 **네모를 그릴 바탕이 없다.**
이미지는 `.gitignore`라 레포에 없지만, 로컬에서 돌리는 사람에겐 `data/`에 있다.
파일이 없으면 **404와 함께 「이미지 없음 — 좌표 표시 불가」**를 명시한다. 조용히 빈 칸을
주지 마라.

#### 결과가 새로고침으로 바뀌면 안 된다

`POST /score`는 결과를 `data/runs/{run_id}/result.json`에 쓰고, **`GET /runs/{run_id}`는
읽기만 한다.** 화면을 새로고침할 때마다 다시 채점하면 심사위원이 비결정적이라
**순위가 뒤집히고, 그 순간 결과의 신뢰가 사라진다.**

### 에러 처리

- `GovernanceError` → HTTP 422 + 위반 목록. **부분 결과를 반환하지 마라**
- `SourceUnavailable` → HTTP 503 + "사람인 API 키 없음"
- 예산 초과 → HTTP 429

**어떤 경로에서도 API 키가 응답 본문·헤더·로그에 나가면 안 된다.** 예외 메시지를 그대로
클라이언트에 넘기지 말고 걸러라.

## Acceptance Criteria

```bash
ruff check src/matching/api run.py
pytest tests/test_api.py -q
python -m matching score --help
python -c "import ast,pathlib; s=pathlib.Path('run.py').read_text(); assert 'getpass' in s and 'input(' not in s, '키를 input()으로 받고 있다'"
grep -n "0.0.0.0" run.py && echo "외부 바인딩 — 위반" && exit 1 || echo "127.0.0.1 전용"
```

`tests/test_api.py`는 FastAPI `TestClient`로 돈다. 심사위원은 픽스처로 대체한다.
최소 케이스:

- `POST /score`가 랭킹을 반환하는지
- 검산 위반 시 422이고 **본문에 점수가 없는지**
- 키가 없을 때 503이고 응답에 키 문자열이 없는지
- **`POST /approve`가 `review_status`를 `human_validated`로 올리는지**
- **`POST /approve`에 `weight`를 실어 보내면 400인지**
- **`posting_revision`이 다르면 409(`ApprovalStale`)인지**
- **`GET /runs/{run_id}`를 두 번 불러 결과가 완전히 같은지** (재채점하지 않는다는 증명)
- **`GET /image/{id}`가 파일이 없을 때 404 + 사유 문자열인지**

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트:
   - CLI와 API가 같은 `run()`을 부르는가?
   - **채점을 일으키는 엔드포인트가 `POST /score` 하나인가?**
   - 응답 어디에도 키가 없는가? (`grep -rn "sk-" src/ run.py` 가 비어야 한다)
   - `run.py`가 키를 `getpass`로 받고 `.env`에 `0o600`으로 쓰는가?
   - `GET /runs/{id}`가 재채점하지 않는가?
3. `index.json`의 step 8을 업데이트한다. `summary`에 **평가자가 붙여넣을 한 줄**을 적는다 —
   `python run.py`. 두 줄이 되면 안 된다.

## 금지사항

- **로직을 CLI와 API에 각각 구현하지 마라.** 이유: 두 경로의 결과가 갈리면 어느 쪽이
  맞는지 알 수 없다.
- 검산 실패 시 부분 결과를 주지 마라. 이유: 근거 없는 점수가 화면에 나간다.
- 인증·사용자 관리·DB를 붙이지 마라. 이유: 과제 범위 밖이다.
- **키를 `input()`으로 받지 마라.** 이유: 화면에 찍히고 셸 히스토리에 남는다. `getpass`다.
- **서버를 `0.0.0.0`에 바인딩하지 마라.** 이유: 같은 네트워크의 다른 기기에 열린다.
  로컬 도구다.
- **`GET`이 채점을 일으키게 하지 마라.** 이유: 새로고침마다 순위가 바뀐다.
- **`README.md`를 만들지 마라.** 실행 방법은 `--help`와 `docs/`에만 적는다.
