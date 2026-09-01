"""HTTP 진입점. **채점을 시작하는 엔드포인트는 하나다 — `POST /score`.**

```
POST /score          채점한다. 결과를 data/runs/{run_id}/result.json에 쓴다
POST /prepare        공고 → 루브릭 초안 (전부 draft). 채점하지 않는다
POST /approve        승인 · 필수↔우대 뒤집기 · 삭제. 채점하지 않는다
GET  /runs/{id}      저장된 결과를 **읽기만** 한다
GET  /image/{id}     공고 이미지 원본 — bbox 네모를 그릴 바탕
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
from ..pipeline import ApprovalRequired, ApprovalStale, RubricProposal, RunResult, load_run
from ..source import (
    ProvenanceError,
    SourceUnavailable,
    default_data_dir,
    image_paths,
    posting_dir,
    redact,
)
from .service import (
    POSTINGS_SUBDIR,
    RESUMES_SUBDIR,
    ApprovedCriterion,
    Decision,
    EntryError,
    JudgeUnavailable,
    apply_decisions,
    load_resumes,
    make_client,
    prepare_posting,
    score_proposal,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
INDEX_FILENAME = "index.html"

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
