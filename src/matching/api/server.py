"""HTTP 진입점. **채점을 시작하는 엔드포인트는 하나다 — `POST /score`.**

```
POST /score          채점한다. 결과를 data/runs/{run_id}/result.json에 쓴다
POST /prepare        공고 → 루브릭 초안 (전부 draft). 채점하지 않는다
POST /approve        승인 · 필수↔우대 뒤집기 · 삭제. 채점하지 않는다
GET  /postings       확보한 공고 목록 — 첫 화면이 무엇을 고를지 (step 9)
GET  /runs/{id}      저장된 결과를 **읽기만** 한다
GET  /image/{id}     공고 이미지 원본 — bbox 네모를 그릴 바탕
GET  /trace/{id}     OCR 줄·판정·헤더 역할 — 「이미지를 어떻게 읽었나」 (step 9 ②)
GET  /resume/{id}    마스킹된 이력서 원문 — 근거 구간을 하이라이트할 바탕 (step 9 ⑥)
GET  /               정적 UI (step 9)
```

## 왜 「하나」가 `POST /score`인가

과제가 요구한 「CLI 또는 API 엔드포인트 하나」는 **실행 진입점이 하나**라는 뜻이다.
실행은 `python run.py` 한 줄이고 채점은 `POST /score` 하나다. 읽기와 승인을 거기
밀어 넣으면 오히려 한 엔드포인트가 세 가지 일을 하게 된다.

## GET이 채점을 일으키지 않는다

`POST /score`가 결과를 파일에 쓰고, `GET /runs/{id}`는 그 파일을 읽는다.
새로고침마다 다시 채점하면 심사위원이 비결정적이라 **순위가 뒤집히고, 그 순간 결과의
신뢰가 사라진다.**

## 승인 없이는 채점하지 않는다

`POST /score`는 `POST /prepare` → `POST /approve`를 거친 제안을 쓴다. 승인이 없으면
`ApprovalRequired`가 409로 나간다. **이 경로가 없으면 `review_status`는 영원히
`draft` 한 값이고, 화면의 「사람 확인함」 배지는 절대 바뀌지 않는 장식이 된다.**
그래서 `skip_approval`을 HTTP로 열지 않았다 — 화면에는 승인 버튼이 있다.

## GET 두 개(`/trace`·`/resume`)가 LLM을 부르지 않는다

`GET /trace`는 **파싱을 다시 하되 심사위원도 헤더 분류기도 부르지 않는다** —
`parse_posting(client=None)`으로 부르므로 부를 클라이언트 자체가 없다. 캐시가 비어 있으면
조용히 LLM으로 넘어가는 대신 409로 멈춘다. 「캐시가 맞기를 바란다」가 아니라 **구조적으로
불가능**하게 만드는 쪽을 골랐다 (예산 $5, `docs/COST_BUDGET.md`).

## 키는 어떤 경로로도 나가지 않는다

예외 문자열을 그대로 클라이언트에 넘기지 않는다. 밖으로 나가는 모든 메시지는
`_safe()`를 지난다 — 사람인 인증이 쿼리 파라미터라 httpx 예외에 URL이 통째로 실린다.

## 모듈 수준에 `app`을 두지 않는다

`create_app()`을 부르면 `.env`를 읽고 OpenAI 클라이언트를 만든다. 그걸 import 시점에
하면 **이 모듈을 import하기만 해도** 키를 읽는다 — 테스트가 그렇다. 실행 경로는
`run.py`가 `create_app()`을 직접 부르고, uvicorn 명령으로 띄우려면 팩토리를 준다:
`uvicorn matching.api.server:create_app --factory --host 127.0.0.1`.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ..config import Settings, load_settings
from ..judge.panel import BudgetExceeded
from ..model.governance import GovernanceError
from ..model.objects import BBox
from ..parser import ParseReport, PositionBand
from ..parser.header_role import ROLES_FILENAME
from ..pipeline import ApprovalRequired, ApprovalStale, RubricProposal, RunResult, load_run
from ..scorer.mask import MASK_CHAR, mask_sensitive
from ..source import (
    LocalSource,
    ProvenanceError,
    SourceKind,
    SourceUnavailable,
    default_data_dir,
    image_paths,
    posting_dir,
    read_provenance,
    redact,
)
from ..source.provenance import OCR_FILENAME, sha256_file
from .service import (
    POSTINGS_SUBDIR,
    RESUMES_SUBDIR,
    ApprovedCriterion,
    Decision,
    EntryError,
    JudgeUnavailable,
    apply_decisions,
    load_posting_ref,
    load_resumes,
    make_client,
    prepare_posting,
    score_proposal,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
INDEX_FILENAME = "index.html"

# `GET /trace`가 두 번째 호출부터 읽는 파일. **커밋 금지** — `ocr.json`과 같은 이유로
# 공고 본문 그 자체다(줄 텍스트를 전부 담는다). `.gitignore`에 넣어 두었다.
TRACE_FILENAME = "trace.json"

# 마스킹 종류 코드 → 화면에 쓸 한국어. `scorer/mask.py`의 `_FIELD_LABELS` 키와 짝이다.
# **코드값을 그대로 화면에 내보내지 않는다** — 「무엇을 가렸는가」는 사람이 읽어야 한다.
_MASK_LABELS: dict[str, str] = {
    "name": "이름",
    "gender": "성별",
    "age": "나이",
    "birth": "생년월일",
    "origin": "출신지",
    "school": "학교명",
    "photo": "사진",
    "contact": "연락처",
    "personal": "개인정보",
}

# 키처럼 생긴 토큰. `redact()`가 잡는 것은 `access-key=…` 형태뿐이라 그 밖의 자리를
# 여기서 한 번 더 막는다. **접두사 문자열을 코드에 그대로 적지 않는다** — 적으면
# 「키가 코드에 있는가」를 문자열로 검사하는 쪽이 이 정규식에 걸린다.
_TOKEN_SHAPED = re.compile(r"(?i)\b(?:sk|rk|api)[-_][A-Za-z0-9_\-]{12,}")


@dataclass
class _State:
    """앱 한 벌이 들고 있는 것. **DB가 아니다** — 프로세스가 죽으면 함께 사라진다.

    `proposals`가 메모리에 사는 이유: 승인은 「지금 이 사람이 이 화면에서 확인했다」는
    행위이고, 그걸 디스크에 남기면 다음 실행이 **남의 승인을 물려받는다.**
    """

    settings: Settings
    data_dir: Path
    client: Any = None
    proposals: dict[str, RubricProposal] = field(default_factory=dict)


class ScoreOptions(BaseModel):
    """`POST /score`·`POST /prepare`가 받는 실행 옵션.

    **`no_judge`가 없다.** 그건 CLI 개발용 스위치이고(`--no-judge`), 응답 계약이
    `RunResult`인 이 경로로 흘리면 65점이 빠진 결과가 아무 표시 없이 화면에 간다.
    """

    model_config = ConfigDict(extra="forbid")

    position: str | None = None
    source: str = "local"
    ocr_engine: str | None = None


class PrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    posting_id: str
    options: ScoreOptions = Field(default_factory=ScoreOptions)


class ScoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    posting_id: str
    resume_ids: list[str] | None = None
    options: ScoreOptions = Field(default_factory=ScoreOptions)


class ApproveRequest(BaseModel):
    """승인 화면이 보내는 것. `extra="allow"`는 `weight`를 **감지**하기 위한 것이다."""

    model_config = ConfigDict(extra="allow")

    posting_id: str
    posting_revision: str | None = None
    decisions: list[Decision]
    approved_by: str | None = None


class ApproveResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved_at: str
    criteria: list[ApprovedCriterion]


class PostingSummary(BaseModel):
    """첫 화면의 한 줄. **회사명도 공고 제목도 여기 없다.**

    우리가 저장하지 않기 때문이다 — 남기는 것은 `provenance.json`의 해시와 대상 직무
    라벨뿐이고, `LocalSource`가 주는 `PostingRef.title`·`company`는 빈 문자열이다
    (`source/local.py`). 그래서 화면이 쓸 이름은 `posting_id`와 `target_position`이다.

    `source_kind`가 여기 있는 것이 요점이다. 「데모 데이터」 배지는 이 값에서 나오고,
    그게 **데모 결과와 프로덕션(`client_feed`) 결과를 가르는 유일한 표시**다.
    """

    model_config = ConfigDict(extra="forbid")

    posting_id: str
    source_kind: SourceKind
    target_position: str | None
    image_count: int
    api_verified: bool


class TraceLine(BaseModel):
    """OCR이 읽은 줄 하나 — **화면 왼쪽의 네모 하나와 오른쪽 목록의 한 행이 이것이다.**

    `scoped=False`인 줄도 뺴지 않고 준다. 화면에서 확인해야 하는 것은 「읽은 것」이
    아니라 **「읽고 무엇을 버렸나」**다 — 버린 줄이 안 보이면 잘못 버린 것을 영영 못 본다.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    text: str
    x0: int
    conf: float
    box: BBox
    role: str | None  # header | item | continuation | ambiguous | None
    scoped: bool  # False면 다른 직무의 칸이라 잘라낸 줄
    req: str | None  # 이 줄이 들어간 조건 id. 없으면 null


class TraceBlock(BaseModel):
    """섹션 하나. `scored=False`면 채점에 안 들어간 섹션이다(복리후생·전형절차 등)."""

    model_config = ConfigDict(extra="forbid")

    header: str | None
    role: str | None  # requirement | preferred | duty | context | excluded
    scored: bool
    items: list[str]


class SentToLlm(BaseModel):
    """**LLM에 실제로 보낸 문자열 전부.** 이미지도 좌표도 여기 없다.

    「이미지를 LLM에 보내지 않는다」가 이 시스템의 설계 결정인데, 그건 코드를 읽어야만
    확인되는 주장이다. 모델이 본 것이 이게 전부라는 걸 **화면이 보여줘야** 근거가 된다.
    """

    model_config = ConfigDict(extra="forbid")

    headers: list[str]
    ambiguous: list[str]


class TraceResponse(BaseModel):
    """`GET /trace/{posting_id}` — 「이미지를 어떻게 읽었나」 한 벌.

    `cached`가 여기 있는 이유: 이 응답이 파일에서 왔는지 방금 계산한 것인지가
    **LLM 호출 0회 주장의 일부**다. 어느 쪽이든 호출은 0이지만(아래 라우트 문서),
    화면이 그 사실을 말할 수 있어야 한다.
    """

    model_config = ConfigDict(extra="forbid")

    posting_id: str
    target_position: str | None
    img_w: int
    img_h: int
    avg_conf: float
    band: PositionBand | None  # 한 공고에 직무가 여럿일 때 채점 대상 y 구간. null이면 전체
    lines: list[TraceLine]
    sent_to_llm: SentToLlm
    header_roles: dict[str, str]
    blocks: list[TraceBlock]
    parse_report: ParseReport
    cached: bool


class MaskedSpan(BaseModel):
    """가린 구간 하나. **오프셋을 함께 준다** — 화면이 `███`를 어디에 그릴지 안다."""

    model_config = ConfigDict(extra="forbid")

    id: str  # "name-01". 한 구간이 여러 종류에 걸리면 "name+birth+age-01"
    label: str  # 사람이 읽는 이름 ("이름·생년월일·나이")
    categories: list[str]  # 코드값 ["name", "birth", "age"]
    start: int
    end: int


class ResumeResponse(BaseModel):
    """`GET /resume/{resume_id}` — **채점이 본 것과 글자 하나까지 같은 문자열.**

    원문이 아니라 **마스킹된 글**을 준다. 마스킹은 길이를 보존하므로(`scorer/mask.py`)
    `Evidence.span`의 오프셋이 이 문자열에 그대로 맞는다 — 화면은 문자열 검색이 아니라
    오프셋으로 하이라이트한다.
    """

    model_config = ConfigDict(extra="forbid")

    resume_id: str
    posting_id: str
    text: str  # 마스킹된 이력서 원문. span 오프셋의 기준이다
    length: int
    mask_char: str
    masked: list[MaskedSpan]
    masked_fields: list[str]  # 가린 종류 목록 — 화면 상단의 「무엇을 가리고 채점했나」


def _safe(text: str, settings: Settings) -> str:
    """밖으로 나가는 문자열에서 키를 지운다. **예외 메시지를 그대로 넘기지 않는다.**"""
    cleaned = redact(str(text))
    for secret in (settings.openai_api_key, settings.saramin_access_key):
        if secret:
            cleaned = cleaned.replace(secret, "***")
    return _TOKEN_SHAPED.sub("***", cleaned)


def _safe_id(value: str, what: str) -> str:
    """경로 조각으로 쓸 문자열. 구분자·상위 참조를 막는다 (`source/base.posting_dir`와 같다)."""
    if not value or value in {".", ".."} or {"/", "\\"} & set(value):
        raise HTTPException(status_code=400, detail=f"{what}로 쓸 수 없는 값이다: {value!r}")
    return value


def _safe_payload(payload: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """구조 전체를 JSON으로 한 번 돌려 **모든 문자열**을 `_safe()`에 통과시킨다.

    필드를 하나씩 훑지 않는 이유: `lines`·`blocks`처럼 중첩이 깊은 곳에서 한 자리를
    빠뜨리면 그게 정확히 새는 자리가 된다. `_safe()`는 문자를 지우기만 하고 따옴표·
    역슬래시를 만들지 않으므로 JSON이 깨지지 않는다.
    """
    return json.loads(_safe(json.dumps(payload, ensure_ascii=False), settings))


def _parse_fingerprint(settings: Settings) -> dict[str, Any]:
    """파싱 결과를 바꾸는 설정값만. **이게 바뀌면 캐시된 trace는 거짓말이 된다.**"""
    return {
        "ocr_engine": settings.ocr_engine,
        "header_x_threshold": settings.header_x_threshold,
        "continuation_tolerance": settings.continuation_tolerance,
        "continuation_max_indent": settings.continuation_max_indent,
        "ambiguous_fallback_ratio": settings.ambiguous_fallback_ratio,
    }


def _trace_cache_key(directory: Path, settings: Settings) -> str:
    """캐시가 언제 낡는가 — **입력 파일 둘과 파싱 설정 다섯.**

    `ocr.json`이 바뀌면 좌표가 바뀌고, `header_roles.json`이 바뀌면 섹션 역할이 바뀌고,
    임계값이 바뀌면 줄 판정이 바뀐다. 셋 중 하나라도 놓치면 화면이 **파이프라인이 실제로
    한 일과 다른 것**을 보여주게 되는데, 이 화면의 목적이 바로 그 대조다.
    """
    roles_path = directory / ROLES_FILENAME
    payload = json.dumps(
        {
            "ocr": sha256_file(directory / OCR_FILENAME),
            "roles": sha256_file(roles_path) if roles_path.is_file() else "",
            "settings": _parse_fingerprint(settings),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_trace_cache(path: Path, key: str) -> dict[str, Any] | None:
    """캐시가 **지금 입력에 대한 것일 때만** 쓴다. 낡았으면 없는 것으로 친다."""
    if not path.is_file():
        return None
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(stored, dict) or stored.get("cache_key") != key:
        return None
    trace = stored.get("trace")
    return trace if isinstance(trace, dict) else None


def _mask_label(category: str) -> str:
    """`name+birth+age` → `이름·생년월일·나이`. 모르는 코드는 그대로 둔다."""
    return "·".join(_MASK_LABELS.get(part, part) for part in category.split("+"))


def create_app(
    settings: Settings | None = None,
    *,
    data_dir: Path | str | None = None,
    client: Any = None,
    proposals: dict[str, RubricProposal] | None = None,
) -> FastAPI:
    """앱 한 벌. 인자는 **테스트가 심사위원과 데이터 위치를 갈아 끼우는 자리**다.

    실행 경로는 인자 없이 부른다 — 설정은 `.env`에서, 데이터는 `data/`에서 온다.
    """
    active = settings if settings is not None else load_settings()
    state = _State(
        settings=active,
        data_dir=Path(data_dir) if data_dir is not None else default_data_dir(),
        client=client if client is not None else make_client(active),
        proposals=dict(proposals or {}),
    )

    app = FastAPI(
        title="지원자-공고 매칭 스코어링 엔진",
        description="채점을 시작하는 엔드포인트는 POST /score 하나다.",
        version="0.1.0",
    )
    app.state.matching = state

    # --- 에러 처리. **부분 결과를 반환하지 않는다** -------------------------

    def _fail(status: int, message: str, extra: dict | None = None) -> JSONResponse:
        body: dict[str, Any] = {"detail": _safe(message, state.settings)}
        if extra:
            body.update(extra)
        return JSONResponse(status_code=status, content=body)

    async def _governance(_: Request, exc: Exception) -> JSONResponse:
        # 위반 목록은 주되 **점수는 한 톨도 주지 않는다.**
        violations = getattr(exc, "violations", [])
        return _fail(
            422,
            f"검산 위반 {len(violations)}건 — 근거 없는 점수를 내보내지 않는다",
            {
                "violations": [
                    {
                        "rule": item.rule,
                        "object_id": item.object_id,
                        "message": _safe(item.message, state.settings),
                    }
                    for item in violations
                ]
            },
        )

    async def _unavailable(_: Request, exc: Exception) -> JSONResponse:
        return _fail(503, str(exc))

    async def _budget(_: Request, exc: Exception) -> JSONResponse:
        return _fail(429, str(exc))

    async def _conflict(_: Request, exc: Exception) -> JSONResponse:
        return _fail(409, str(exc))

    async def _bad_request(_: Request, exc: Exception) -> JSONResponse:
        return _fail(400, str(exc))

    async def _unprocessable(_: Request, exc: Exception) -> JSONResponse:
        return _fail(422, str(exc))

    async def _internal(_: Request, exc: Exception) -> JSONResponse:
        # 예상 못 한 예외도 **메시지를 걸러서** 나간다. 키가 어느 경로로든 응답에
        # 실리는 것을 막는 마지막 그물이다 — httpx 예외에는 URL이 통째로 들어 있다.
        return _fail(500, f"{type(exc).__name__}: {exc}")

    app.add_exception_handler(GovernanceError, _governance)
    app.add_exception_handler(SourceUnavailable, _unavailable)
    app.add_exception_handler(JudgeUnavailable, _unavailable)
    app.add_exception_handler(BudgetExceeded, _budget)
    app.add_exception_handler(ApprovalRequired, _conflict)
    app.add_exception_handler(ApprovalStale, _conflict)
    app.add_exception_handler(EntryError, _bad_request)
    app.add_exception_handler(ProvenanceError, _unprocessable)
    app.add_exception_handler(Exception, _internal)

    # --- 준비 · 승인 · 채점 -------------------------------------------------

    def _prepare(posting_id: str, options: ScoreOptions) -> RubricProposal:
        directory = posting_dir(state.data_dir, _safe_id(posting_id, "공고 식별자"))
        return prepare_posting(
            directory,
            state.settings,
            source=options.source,
            position=options.position,
            ocr_engine=options.ocr_engine,
            client=state.client,
        )

    @app.post("/prepare", response_model=RubricProposal)
    def post_prepare(body: PrepareRequest) -> RubricProposal:
        """공고를 조건·루브릭 초안으로 만든다. **채점하지 않는다.**

        나오는 항목은 전부 `review_status="draft"`다 — 사람이 `POST /approve`로
        확인해야 `human_validated`가 된다.
        """
        proposal = _prepare(body.posting_id, body.options)
        state.proposals[body.posting_id] = proposal
        return proposal

    @app.post("/approve", response_model=ApproveResponse)
    def post_approve(body: ApproveRequest) -> ApproveResponse:
        """승인 화면이 부르는 곳 — **승인 / 필수↔우대 뒤집기 / 삭제 셋뿐이다.**

        `weight`가 실려 오면 400이다. 가중치를 손대게 하면 직군 무관 일반화가 무너진다
        (`src/CLAUDE.md`).

        `posting_revision`이 지금 공고의 것과 다르면 409(`ApprovalStale`) — 승인은 그
        시점의 공고에 대한 것이므로 공고가 수정되면 낡는다 (검산 G7).
        """
        if "weight" in (body.model_extra or {}):
            raise EntryError("가중치는 승인 화면에서 바꿀 수 없다 — 배점은 루브릭이 정한다")

        proposal = state.proposals.get(body.posting_id)
        if proposal is None:
            raise HTTPException(
                status_code=404,
                detail=f"{body.posting_id}: 승인할 루브릭이 없다 — POST /prepare를 먼저 부른다",
            )
        if body.posting_revision != proposal.posting_revision:
            raise ApprovalStale(
                f"{body.posting_id}: 승인 대상이 현재 공고가 아니다 "
                f"(보낸 revision {body.posting_revision!r} ≠ 현재 "
                f"{proposal.posting_revision!r}). 화면을 다시 불러온다 (검산 G7)"
            )

        approved, report = apply_decisions(
            proposal, body.decisions, state.settings, approved_by=body.approved_by
        )
        state.proposals[body.posting_id] = approved
        approved_at = approved.approved_at
        if approved_at is None:  # apply_decisions가 반드시 채운다. 비면 계약이 깨진 것이다
            raise EntryError("승인 시각이 비어 있다 — 승인이 성립하지 않았다")
        return ApproveResponse(approved_at=approved_at.isoformat(), criteria=report)

    @app.post("/score", response_model=RunResult)
    def post_score(body: ScoreRequest) -> RunResult:
        """**채점을 일으키는 유일한 엔드포인트.**

        결과는 `data/runs/{run_id}/result.json`에 저장되고, 같은 결과를 다시 볼 때는
        `GET /runs/{run_id}`가 그 파일을 읽는다 — 다시 채점하지 않는다.
        """
        posting_id = _safe_id(body.posting_id, "공고 식별자")
        proposal = state.proposals.get(posting_id)
        if proposal is None:
            # 승인 화면을 거치지 않고 바로 온 경우. 초안을 만들어 두고 승인 게이트에
            # 걸리게 한다 — **여기서 조용히 통과시키지 않는다.**
            proposal = _prepare(posting_id, body.options)
            state.proposals[posting_id] = proposal

        resumes = load_resumes(
            state.data_dir / RESUMES_SUBDIR / posting_id, body.resume_ids or None
        )
        result, _ = score_proposal(
            proposal,
            resumes,
            state.settings,
            client=state.client,
            data_dir=state.data_dir,
        )
        return result

    # --- 읽기 ---------------------------------------------------------------

    @app.get("/postings", response_model=list[PostingSummary])
    def get_postings() -> list[PostingSummary]:
        """첫 화면이 고를 수 있는 공고. **채점하지 않는다** — 디렉터리를 훑기만 한다.

        이게 없으면 화면이 공고 식별자를 코드에 박아야 하고, 그러면 「데모 데이터」
        배지도 `source_kind`가 아니라 화면이 스스로 정한 값이 된다. 배지가 데이터에서
        오지 않으면 그건 표시가 아니라 장식이다.

        이미지가 0장인 디렉터리도 뺴지 않고 `image_count=0`으로 준다 — 「자리는 있는데
        이미지가 안 놓였다」가 경로 B의 실제 상태이고, 감추면 화면이 그걸 못 보여준다.
        """
        summaries: list[PostingSummary] = []
        for ref in LocalSource(data_dir=state.data_dir).list_postings():
            try:
                provenance = read_provenance(posting_dir(state.data_dir, ref.posting_id))
            except (OSError, ValueError, ProvenanceError):
                # 출처 기록이 없으면 **출처를 아는 척하지 않는다.** 어댑터가 말한
                # `local`을 그대로 쓰고 나머지는 비운다.
                summaries.append(
                    PostingSummary(
                        posting_id=ref.posting_id,
                        source_kind=ref.source_kind,
                        target_position=None,
                        image_count=len(ref.image_paths),
                        api_verified=False,
                    )
                )
                continue
            summaries.append(
                PostingSummary(
                    posting_id=ref.posting_id,
                    source_kind=provenance.source_kind,
                    target_position=provenance.target_position,
                    image_count=len(ref.image_paths),
                    api_verified=provenance.api_verified,
                )
            )
        return summaries

    @app.get("/runs/{run_id}", response_model=RunResult)
    def get_run(run_id: str) -> RunResult:
        """저장된 결과를 읽는다. **재채점하지 않는다.**"""
        safe = _safe_id(run_id, "실행 식별자")
        try:
            return load_run(safe, state.data_dir)
        except (OSError, ValueError) as exc:
            raise HTTPException(
                status_code=404,
                detail=f"{safe}: 저장된 결과가 없다 — 먼저 POST /score를 부른다",
            ) from exc

    @app.get("/image/{posting_id}")
    def get_image(posting_id: str, page: int = 1) -> FileResponse:
        """공고 이미지 원본 — **저장한 좌표가 값을 갖는 유일한 이유다.**

        bbox를 저장해 놓고 이미지를 못 주면 근거를 클릭했을 때 네모를 그릴 바탕이 없다.
        이미지는 `.gitignore`라 레포에 없지만 로컬에서 돌리는 사람에겐 `data/`에 있다.
        **없으면 404와 사유를 준다** — 조용히 빈 칸을 주지 않는다.
        """
        safe = _safe_id(posting_id, "공고 식별자")
        directory = posting_dir(state.data_dir, safe)
        paths = image_paths(directory)
        if not paths:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"{safe}: 이미지 없음 — 좌표 표시 불가. 공고 이미지는 레포에 커밋되지 "
                    f"않는다 (공고 본문). {POSTINGS_SUBDIR}/{safe}/ 에 놓으면 네모가 그려진다"
                ),
            )
        if page < 1 or page > len(paths):
            raise HTTPException(
                status_code=404,
                detail=f"{safe}: {page}쪽이 없다 — 이 공고는 {len(paths)}쪽이다",
            )
        return FileResponse(paths[page - 1], media_type="image/png")

    @app.get("/trace/{posting_id}", response_model=TraceResponse)
    def get_trace(posting_id: str) -> TraceResponse:
        """OCR이 읽은 줄과 그 판정 — **화면 ②「이미지를 어떻게 읽었나」의 데이터.**

        ## LLM을 부르지 않는다. 「안 부르길 바란다」가 아니라 **못 부른다**

        `parse_posting(client=None)`으로 부른다. LLM이 들어오는 자리는 헤더 역할 분류
        하나뿐인데(`parser/header_role.py`), 그 자리는 캐시가 없고 클라이언트도 없으면
        **`ParseError`를 던진다.** 그래서 이 경로에서 호출이 새어 나갈 구멍이 없다 —
        여기서 409로 바꿔 「`POST /prepare`를 먼저 부르라」고 말한다. 캐시 적중에
        기대는 설계였다면 캐시가 어긋난 날 조용히 돈이 나갔을 것이다 (예산 $5).

        같은 이유로 **OCR도 새로 돌리지 않는다.** `ocr.json`이 없으면 409다 — 있으면
        `load_or_run_ocr`가 파일만 읽는다(공고당 3~40초짜리 작업을 GET이 일으키지 않는다).

        ## 캐시를 왜 파일로 떨어뜨리나 — 두 가지를 다 했다

        1차 방어는 위의 `client=None`이고, `trace.json` 캐시는 그 위에 얹은 **비용
        방어**다. 첫 호출은 파싱을 다시 하고(수백 ms), 그 결과를 `trace.json`에 쓴다.
        두 번째부터는 파일만 읽는다 — 화면 ②는 stepper로 몇 번이고 되돌아오는 자리라
        같은 계산을 반복하게 된다.

        캐시 키는 `ocr.json` · `header_roles.json` · 파싱 설정값이다. 하나라도 바뀌면
        다시 계산한다. **낡은 캐시를 보여주면 화면이 파이프라인과 다른 말을 하게 되고,
        그러면 이 화면을 믿고 규칙을 고치게 된다.**

        > 부수효과를 감춘다: 재계산 경로는 `parse_posting`이 하는 일을 그대로 하므로
        > `requirements.json`·`provenance.json`을 다시 쓴다. 같은 입력에 같은 결과라
        > 내용은 바뀌지 않지만, GET이 파일을 건드리는 것은 사실이다. 파싱 로직을 화면용으로
        > 한 벌 더 만드는 쪽이 **화면과 결과가 갈라지는** 더 큰 위험이라 이쪽을 골랐다.
        """
        safe = _safe_id(posting_id, "공고 식별자")
        directory = posting_dir(state.data_dir, safe)
        if not directory.is_dir():
            raise HTTPException(
                status_code=404,
                detail=f"{safe}: 공고가 없다 — {POSTINGS_SUBDIR}/{safe}/ 디렉터리가 없다",
            )
        if not (directory / OCR_FILENAME).is_file():
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{safe}: OCR 결과({OCR_FILENAME})가 없다 — 먼저 POST /prepare를 부른다. "
                    "이 엔드포인트는 OCR도 LLM도 새로 돌리지 않는다"
                ),
            )

        key = _trace_cache_key(directory, state.settings)
        payload = _read_trace_cache(directory / TRACE_FILENAME, key)
        cached = payload is not None

        if payload is None:
            from ..parser import ParseError, parse_posting

            ref = load_posting_ref(directory, state.settings, source="local")
            raw: dict[str, Any] = {}
            try:
                # **client=None이 이 엔드포인트의 계약이다.** 인자를 채우지 마라.
                parse_posting(ref, state.settings, client=None, data_dir=state.data_dir, trace=raw)
            except (ParseError, LookupError) as exc:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"{safe}: 파싱 결과를 다시 만들 수 없다 — "
                        f"{_safe(str(exc), state.settings)} "
                        "(이 엔드포인트는 LLM을 부르지 않는다. POST /prepare를 먼저 부른다)"
                    ),
                ) from exc
            # 화면이 쓰지 않는 둘은 싣지 않는다 — `images`는 **서버의 절대 경로**이고
            # (화면은 `GET /image/{id}`로 받는다), `requirements`·`duties`는 이미
            # `POST /prepare` 응답에 있다. 같은 값을 두 곳에서 주면 어긋날 자리가 생긴다.
            payload = {
                "posting_id": raw["posting_id"],
                "target_position": raw["target_position"],
                "img_w": raw["img_w"],
                "img_h": raw["img_h"],
                "avg_conf": raw["avg_conf"],
                "band": raw["band"],
                "lines": raw["lines"],
                "sent_to_llm": raw["sent_to_llm"],
                "header_roles": raw["header_roles"],
                "blocks": raw["blocks"],
                "parse_report": raw["report"],
            }
            body = json.dumps({"cache_key": key, "trace": payload}, ensure_ascii=False, indent=2)
            (directory / TRACE_FILENAME).write_text(body + "\n", encoding="utf-8")

        return TraceResponse.model_validate(
            {**_safe_payload(payload, state.settings), "cached": cached}
        )

    @app.get("/resume/{resume_id}", response_model=ResumeResponse)
    def get_resume(resume_id: str, posting_id: str | None = None) -> ResumeResponse:
        """마스킹된 이력서 원문 — **근거 구간을 하이라이트할 바탕.**

        ## 채점이 읽은 것과 같은 함수로 읽는다

        `service.load_resumes()`(채점 경로가 부르는 그 함수)로 읽고
        `scorer.mask.mask_sensitive()`(게이트·사실 채점·심사위원이 각자 부르는 그 함수)로
        가린다. **다른 문자열을 주면 `Evidence.span`이 어긋나고, 그건 근거 사슬이
        거짓말을 하는 것이다** — 화면은 인용 문자열을 검색하지 않고 오프셋으로 자른다.

        마스킹은 길이를 보존하므로(가린 만큼 같은 수의 `■`) 원문 기준 오프셋이 이
        문자열에 그대로 맞는다. 그래서 원문을 줄 이유가 없다 — **주지 않는다.**

        `posting_id`를 안 주면 이력서 디렉터리를 전부 훑는다. 두 공고에 같은 식별자가
        있으면 **아무거나 고르지 않고 409**다 — 조용히 고르면 다른 공고의 이력서 위에
        근거를 그리게 된다.
        """
        safe = _safe_id(resume_id, "지원자 식별자")
        root = state.data_dir / RESUMES_SUBDIR
        if posting_id is not None:
            names = [_safe_id(posting_id, "공고 식별자")]
        elif root.is_dir():
            names = sorted(path.name for path in root.iterdir() if path.is_dir())
        else:
            names = []

        hits: list[tuple[str, Any]] = []
        for name in names:
            try:
                found = load_resumes(root / name, [safe])
            except EntryError:
                continue  # 그 공고에 없는 지원자다. 다음 공고를 본다
            hits.append((name, found[0]))

        if not hits:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"{safe}: 이력서가 없다 — {RESUMES_SUBDIR}/<공고>/ 아래에 "
                    f"candidate_id가 {safe}인 JSON이 없다"
                ),
            )
        if len(hits) > 1:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{safe}: 공고 {len(hits)}건에 같은 지원자 식별자가 있다 "
                    f"({', '.join(name for name, _ in hits)}). "
                    "posting_id 쿼리로 공고를 지정한다 — 아무거나 고르지 않는다"
                ),
            )

        found_posting, resume = hits[0]
        masked, spans = mask_sensitive(resume.text)
        cleaned = _safe(masked, state.settings)
        if cleaned != masked:
            # 키처럼 생긴 문자열이 이력서에 있다. 지우면 **길이가 바뀌어 span이 어긋나고**,
            # 그대로 내보내면 키가 나간다. 둘 다 안 되므로 멈춘다.
            raise EntryError(
                f"{safe}: 이력서에 키처럼 보이는 문자열이 있다 — 지우면 오프셋이 어긋나므로 "
                "원문을 내보내지 않는다. 해당 문자열을 이력서에서 제거하고 다시 부른다"
            )

        masked_spans = [
            MaskedSpan(
                id=key,
                label=_mask_label(key.rsplit("-", 1)[0]),
                categories=key.rsplit("-", 1)[0].split("+"),
                start=span.start,
                end=span.end,
            )
            for key, span in spans.items()
        ]
        # 종류 목록은 **중복 없이 등장 순서대로**. 화면 상단에 한 줄로 나간다.
        fields = list(
            dict.fromkeys(
                _MASK_LABELS.get(part, part) for item in masked_spans for part in item.categories
            )
        )
        return ResumeResponse(
            resume_id=safe,
            posting_id=found_posting,
            text=masked,
            length=len(masked),
            mask_char=MASK_CHAR,
            masked=masked_spans,
            masked_fields=fields,
        )

    @app.get("/", response_class=HTMLResponse)
    def get_index() -> HTMLResponse:
        """정적 UI. 화면 자체는 step 9가 `static/index.html`에 놓는다."""
        index = STATIC_DIR / INDEX_FILENAME
        if index.is_file():
            return HTMLResponse(index.read_text(encoding="utf-8"))
        return HTMLResponse(
            "<!doctype html><meta charset='utf-8'>"
            "<title>지원자-공고 매칭 스코어링 엔진</title>"
            "<p>화면이 아직 없다 (step 9). API는 살아 있다 — "
            "<a href='/docs'>/docs</a>에서 <code>POST /score</code>를 부를 수 있다.</p>",
            status_code=200,
        )

    return app
