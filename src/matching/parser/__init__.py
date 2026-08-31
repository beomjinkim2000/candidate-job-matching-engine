"""공고 이미지 → `Requirement` 목록. **파이프라인에서 유일하게 이미지를 다루는 곳이다.**

## 산출물을 파일 3개로 쪼갠다

| 파일 | 무엇 | 커밋 | 다시 만드는 계기 |
|---|---|---|---|
| `img_{n}.png` | 원본 이미지 | ✗ | 공고가 바뀔 때 |
| `ocr.json` | 엔진이 뱉은 줄+좌표 그대로 | ✗ | **엔진을 바꿀 때만** |
| `requirements.json` | 조립된 조건 목록 | **✅** | **규칙을 바꿀 때마다** |

**엔진 교체와 규칙 수정은 원인이 다르다.** 합치면 들여쓰기 임계값 하나 바꿀 때마다
OCR을 다시 돌려야 하고(공고당 3~40초), 무엇보다 **대조할 두 번째 파일이 사라진다.**
`requirements.json`의 모든 조건은 `line_ids`로 `ocr.json`의 줄을 역참조하고,
`ocr_sha256`으로 어느 OCR 결과에서 나왔는지 못박는다.

⛔ **`ocr.json`은 커밋하지 않는다.** 이미지를 글자로 옮긴 것이라 **공고 본문 그 자체**다.
이미지를 뺀 사유가 전사본에도 그대로 걸린다 — **그림과 글자를 다르게 취급하지 않는다.**
오히려 글자가 검색 가능해서 더 나쁘다. 커밋되는 것은 `requirements.json`이고,
그건 조건 단위로 잘리고 종류·근거등급·좌표가 붙은 **산출물**이다.
**경계는 「원문이냐 산출물이냐」이지 「그림이냐 글자냐」가 아니다.**

## VLM 폴백은 만들지 않는다

`src/CLAUDE.md`가 「2단계가 섹션을 못 찾으면 그때만 VLM 1회」를 허용하고, 발동 조건은
**헤더 0개이거나 `ambiguous`가 절반을 넘을 때**다. 여기서는 **조건 감지와 예외까지만**
만든다. 이유가 둘이다.

1. 확보한 공고에서 발동하지 않는다. 안 도는 코드를 24시간 안에 넣는 건 위험을 늘린다
2. 발동하면 그 조건들의 좌표는 **지어낸 값**이므로, 폴백을 쓰는 순간 `verify.py`가 OCR
   줄과 대조해야 하고 매칭 실패한 조건은 G4가 차단해야 한다. 그 대조 로직까지가 한 묶음이다

발동 조건에 걸리면 **예외를 던지고 멈춘다.** 조용히 넘어가지 않는다.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from ..config import Settings
from ..model.graph import EvidenceGraph
from ..model.objects import Span
from ..source.base import PostingRef, default_data_dir, posting_dir
from ..source.provenance import (
    PROVENANCE_FILENAME,
    REQUIREMENTS_FILENAME,
    Provenance,
    read_provenance,
    sha256_file,
)
from .classify import ParsedItem, PostingContext, RequirementRecord, classify, normalize
from .header_role import (
    ROLES_FILENAME,
    HeaderRole,
    classify_headers,
    load_cached,
    save_cache,
)
from .layout import (
    Block,
    LineRole,
    PositionBand,
    PositionNotFound,
    build_blocks,
    classify_lines,
    count_merged,
    select_lines,
    split_positions,
)
from .ocr import OCR_FILENAME, OcrLine, OcrResult, load_or_run_ocr, run_ocr
from .verify import union_bbox, verify

__all__ = [
    "Block",
    "LineRole",
    "OcrLine",
    "OcrResult",
    "ParseError",
    "ParseReport",
    "PositionBand",
    "PositionNotFound",
    "RequirementRecord",
    "VlmFallbackRequired",
    "build_blocks",
    "classify",
    "classify_headers",
    "classify_lines",
    "parse_posting",
    "run_ocr",
    "select_lines",
    "split_positions",
    "verify",
]

ParseMode = Literal["ocr", "ocr+vlm_fallback"]

# 이 역할의 섹션만 조건이 된다. `duty`는 사다리 3단계의 대조군, `context`는 표시만,
# `excluded`는 채점에서 뺀다 — **`excluded`가 없으면 조용히 틀린다.** 복리후생의
# 「재택근무 가능」이 지원자에게 요구되는 조건으로 들어간다. 실제 공고에서 확인한 것이다.
SCORED_ROLES: tuple[str, ...] = ("requirement", "preferred")


class ParseError(RuntimeError):
    """파싱을 끝낼 수 없다."""


class VlmFallbackRequired(ParseError):
    """OCR만으로 섹션 구조를 못 찾았다. **조용히 진행하지 않는다.**

    여기서 넘어가면 조건이 0건이거나 근거 없는 조건 더미가 나오는데, 뒤 step들은
    그걸 정상 입력으로 받아 초록불을 켠다.
    """


class ParseReport(BaseModel):
    """파싱이 어떻게 됐는지. **UI 하단에 그대로 표시된다.**

    임계값 2개가 실측에서 나온 값이라 다른 공고에서 빗나갈 수 있다. 그때
    **`role_counts`만 보면 어디가 틀어졌는지 보인다** — `ambiguous`가 절반을 넘으면
    레이아웃이 다른 것이고, `merged_continuations`가 항목 수보다 많으면 다단 레이아웃을
    한 줄짜리 항목으로 잘못 읽고 있는 것이다 (`docs/OCR_EVIDENCE.md` §5).

    숫자를 숨기고 「잘 동작합니다」라고 쓰는 것보다 이게 정직하다.
    """

    model_config = ConfigDict(extra="forbid")

    parse_mode: ParseMode
    ocr_engine: str
    ocr_sha256: str  # ocr.json의 해시. provenance의 같은 값과 일치해야 한다
    line_count: int
    role_counts: dict[str, int]
    merged_continuations: int
    excluded_blocks: list[str]
    llm_calls: int  # 이 공고를 파싱하며 부른 LLM 횟수. 캐시가 맞으면 0
    emphasis_available: bool  # 항상 False — OCR이 굵기·색을 주지 않는다


def _item_lines(ocr: OcrResult, group: list[OcrLine]) -> list[OcrLine]:
    """항목을 이루는 줄을 **원본에서 연속 구간으로** 되돌린다.

    병합된 항목의 `source_span`은 `ocr.document()`의 연속 구간이어야 한다. 그런데
    `select_lines`가 중간 줄(다른 직무의 칸)을 빼면, 남은 줄만 이어붙인 것과 원본
    슬라이스가 어긋난다 — 그러면 `verify`의 P1·P2가 정당하게 위반을 낸다.

    그래서 **첫 줄과 마지막 줄 사이의 원본 줄을 전부 넣는다.** 그러면 「span으로 자른
    것이 텍스트와 글자까지 같다」가 구조적으로 보장된다. 조건 문구가 조금 길어지는 쪽이,
    좌표와 글자가 어긋나는 쪽보다 낫다.
    """
    order = {line.id: index for index, line in enumerate(ocr.lines)}
    first, last = order[group[0].id], order[group[-1].id]
    return ocr.lines[first : last + 1]


def _build_context(blocks: list[Block], candidates: list[str]) -> PostingContext:
    """사다리 3·4단계가 쓰는 공고 전체 정보."""
    duty_texts = [
        " ".join(line.text for line in item)
        for block in blocks
        if block.header_role == "duty"
        for item in block.items
    ]
    return PostingContext(
        duty_texts=duty_texts,
        occurrences=dict(Counter(normalize(text) for text in candidates)),
    )


def parse_posting(
    ref: PostingRef,
    settings: Settings,
    client=None,
    data_dir: Path | str | None = None,
    reocr: bool = False,
) -> tuple[list[RequirementRecord], EvidenceGraph, ParseReport]:
    """공고 하나를 조건 목록으로. `requirements.json`을 쓰고 `provenance.json`을 채운다.

    `client`는 3-C(헤더 역할 분류)에만 쓰인다. **이미지는 절대 안 간다** — 가는 것은
    섹션 제목 문자열 몇 개뿐이고, 캐시가 맞으면 아예 안 부른다.
    """
    root = data_dir if data_dir is not None else default_data_dir()
    directory = posting_dir(root, ref.posting_id)
    if not directory.is_dir():
        raise ParseError(f"{directory}: 공고 디렉터리가 없다")

    # --- 3-A. 줄과 좌표 ---------------------------------------------------
    ocr, _ = load_or_run_ocr(directory, ref.image_paths, engine=settings.ocr_engine, reocr=reocr)
    if not ocr.lines:
        raise ParseError(f"{ref.posting_id}: OCR이 줄을 하나도 못 읽었다")

    provenance = read_provenance(directory)

    # --- 3-B′. 직무가 여럿이면 관심 직무의 y 구간만 --------------------------
    band = split_positions(ocr, provenance.target_position, settings)
    scoped = ocr.model_copy(update={"lines": select_lines(ocr, band, settings)})

    # --- 3-B. 줄 → 역할 ---------------------------------------------------
    roles: dict[str, LineRole] = classify_lines(scoped, settings)
    header_texts = [line.text for line in scoped.lines if roles[line.id] == "header"]
    ambiguous_texts = [line.text for line in scoped.lines if roles[line.id] == "ambiguous"]

    # --- VLM 폴백 조건. 감지까지만 하고 멈춘다 ------------------------------
    ambiguous_ratio = len(ambiguous_texts) / len(scoped.lines)
    if not header_texts:
        raise VlmFallbackRequired(
            f"{ref.posting_id}: 섹션 제목을 한 줄도 못 찾았다 "
            f"(header_x_threshold={settings.header_x_threshold}). "
            "VLM 폴백은 구현하지 않았다 — 좌표를 지어내면 G4가 지어낸 값을 검사하게 된다"
        )
    if ambiguous_ratio > settings.ambiguous_fallback_ratio:
        raise VlmFallbackRequired(
            f"{ref.posting_id}: 모호한 줄이 {ambiguous_ratio:.0%}로 "
            f"{settings.ambiguous_fallback_ratio:.0%}를 넘었다 — "
            "이 규칙 체계가 그 공고에 안 맞는다. VLM 폴백은 구현하지 않았다"
        )

    # --- 3-C. 헤더 역할 분류. **LLM이 들어오는 유일한 자리** ------------------
    cache_path = directory / ROLES_FILENAME
    cached = load_cached(cache_path, header_texts, ambiguous_texts)
    if cached is not None:
        header_roles: dict[str, HeaderRole] = cached.roles
        llm_calls = 0
    else:
        if client is None:
            raise ParseError(
                "헤더 역할 분류에 OpenAI 클라이언트가 필요하다. "
                "키는 .env의 OPENAI_API_KEY에서만 온다 (과제 CRITICAL — 코드·로그에 두지 않는다)"
            )
        header_roles = classify_headers(
            header_texts, ambiguous_texts, client, model=settings.header_model
        )
        save_cache(cache_path, header_texts, ambiguous_texts, header_roles)
        llm_calls = 1

    # 모호했던 줄 중 역할을 받은 것만 섹션 제목으로 승격한다. 나머지는 그대로 두면
    # `build_blocks`가 항목으로 세지 않고 흘려보낸다.
    for line in scoped.lines:
        if roles[line.id] == "ambiguous" and line.text in header_roles:
            roles[line.id] = "header"

    blocks = build_blocks(scoped, roles)
    for block in blocks:
        if block.header is not None:
            block.header_role = header_roles.get(block.header.text)

    # --- 3-D. 사다리로 필수/우대 판정 ---------------------------------------
    scored_blocks = [block for block in blocks if (block.header_role or "") in SCORED_ROLES]
    candidate_texts: list[str] = []
    groups: list[tuple[Block, list[OcrLine]]] = []
    for block in scored_blocks:
        for item in block.items:
            lines = _item_lines(ocr, item)
            groups.append((block, lines))
            candidate_texts.append(" ".join(line.text for line in lines))

    context = _build_context(blocks, candidate_texts)
    offsets = ocr.offsets()
    document = ocr.document()

    graph = EvidenceGraph()
    requirements: list[RequirementRecord] = []
    for index, (block, lines) in enumerate(groups, start=1):
        start = offsets[lines[0].id][0]
        end = offsets[lines[-1].id][1]
        text = document[start:end]
        if not text.strip():
            continue
        item = ParsedItem(text=text, lines=lines, header_role=block.header_role)
        kind, grade, step = classify(item, context)
        record = RequirementRecord(
            id=f"R-{index:02d}",
            text=text,
            kind=kind,
            evidence_grade=grade,
            ladder_step=step,
            source_bbox=union_bbox([line.bbox for line in lines]),
            source_span=Span(start=start, end=end),
            line_ids=[line.id for line in lines],
        )
        requirements.append(record)
        graph.add(record)
        for line in lines:
            # 사슬의 마지막 마디 — 조건이 **어느 줄에서** 나왔는가.
            # dst는 그래프 밖 식별자다. 원문도 URL도 아니라 커밋해도 된다.
            graph.link(record.id, "extracted_from", f"{ref.posting_id}:{line.id}")

    # --- 3-F. 코드 검증 (LLM 아님) ------------------------------------------
    violations = verify(requirements, ocr)
    if violations:
        head = "; ".join(f"{v.rule} {v.object_id}: {v.message}" for v in violations[:3])
        raise ParseError(f"파싱 검증 실패 {len(violations)}건 — {head}")

    role_counts = dict(Counter(roles.values()))
    report = ParseReport(
        parse_mode="ocr",
        ocr_engine=ocr.engine,
        ocr_sha256=sha256_file(directory / OCR_FILENAME),
        line_count=len(scoped.lines),
        role_counts=role_counts,
        merged_continuations=count_merged(roles),
        excluded_blocks=[
            block.header.text
            for block in blocks
            if block.header is not None and block.header_role == "excluded"
        ],
        llm_calls=llm_calls,
        # OCR이 굵기·색을 주지 않는다. **「구현했는데 안 쓴다」가 아니라 「입력이 없다」**이다.
        emphasis_available=False,
    )

    _write_requirements(directory, ref, provenance, band, requirements, report)
    _fill_provenance(directory, provenance, report)
    return requirements, graph, report


def _write_requirements(
    directory: Path,
    ref: PostingRef,
    provenance: Provenance,
    band: PositionBand | None,
    requirements: list[RequirementRecord],
    report: ParseReport,
) -> None:
    """`requirements.json` — **레포에 남는 유일한 파싱 산출물.**

    `ocr_sha256`을 최상위에도 둔다. `verify_provenance()`가 거기를 읽어
    「이 조건들이 그 OCR 결과에서 나왔는가」를 대조한다.
    """
    payload = {
        "posting_id": ref.posting_id,
        "source_kind": ref.source_kind,
        "target_position": provenance.target_position,
        "position_band": band.model_dump(mode="json") if band else None,
        "ocr_sha256": report.ocr_sha256,
        "parse_report": report.model_dump(mode="json"),
        "requirements": [record.model_dump(mode="json") for record in requirements],
    }
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    (directory / REQUIREMENTS_FILENAME).write_text(body + "\n", encoding="utf-8")


def _fill_provenance(directory: Path, provenance: Provenance, report: ParseReport) -> None:
    """step 2가 비워 둔 두 칸을 채운다 — **파싱이 끝나야 알 수 있는 값이다.**

    안 채우면 `verify_provenance()`가 「`ocr.json`이 있는데 `ocr_sha256`이 비어 있다」로
    막는다. 그게 맞다 — 그 상태는 어느 OCR 결과에서 나온 조건인지 증명되지 않는다.
    """
    filled = provenance.model_copy(
        update={"ocr_engine": report.ocr_engine, "ocr_sha256": report.ocr_sha256}
    )
    body = json.dumps(filled.model_dump(mode="json"), ensure_ascii=False, indent=2)
    (directory / PROVENANCE_FILENAME).write_text(body + "\n", encoding="utf-8")
