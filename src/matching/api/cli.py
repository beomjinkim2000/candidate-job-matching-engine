"""터미널 진입점 — `acquire` · `parse` · `score`.

**채점 로직은 여기 없다.** `score`가 하는 일은 인자를 읽어 `api/service.py`의 함수를
부르고 결과를 찍는 것뿐이고, 그 함수는 HTTP 쪽(`api/server.py`)이 부르는 것과 같은
함수다 — 두 경로의 결과가 갈리면 어느 쪽이 맞는지 확인할 방법이 없다.

평가자가 붙여넣는 한 줄은 레포 루트의 `python run.py`다 (브라우저 화면까지 함께 뜬다).
이 CLI는 그 아래를 조각으로 돌려 보고 싶을 때 쓴다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..config import load_settings
from ..source import ProvenanceError, write_provenance


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="matching",
        description="지원자-공고 매칭 스코어링 엔진 — 공고를 읽고(parse) 지원자를 채점한다(score)",
    )
    sub = parser.add_subparsers(dest="command")

    acquire = sub.add_parser(
        "acquire",
        help="공고 디렉터리에 출처 증거(provenance.json)를 쓴다",
        description=(
            "이미지를 data/postings/{id}/img_*.png 에 놓은 **직후** 부른다. "
            "하는 일은 해시·픽셀 크기를 재서 provenance.json을 쓰는 것 하나다. "
            "키가 있는 경로에서는 SaraminSource.fetch_images()가 같은 파일을 자동으로 만든다."
        ),
    )
    acquire.add_argument(
        "--posting", required=True, type=Path, help="공고 디렉터리 (예: data/postings/kt-b2c)"
    )
    acquire.add_argument(
        "--source",
        default="local",
        choices=["saramin_api", "local", "client_feed"],
        help="이미지를 어디서 얻었나. 결과 JSON과 화면에 그대로 실린다 (기본: local)",
    )
    acquire.add_argument(
        "--position", default=None, help="한 공고에 직무가 여럿일 때 대상 직무 라벨"
    )
    acquire.set_defaults(func=_cmd_acquire)

    parse = sub.add_parser(
        "parse",
        help="공고 이미지를 조건 목록(requirements.json)으로 바꾼다",
        description=(
            "acquire 다음에 부른다. OCR 결과(ocr.json)가 있으면 재사용하고 "
            "--reocr 로만 다시 돌린다. 대상 직무는 acquire 때 준 --position 값을 "
            "provenance.json에서 읽는다."
        ),
    )
    parse.add_argument("--posting", required=True, type=Path, help="공고 디렉터리")
    parse.add_argument(
        "--ocr-engine",
        default=None,
        choices=["paddle", "vision"],
        help="기본은 설정값(paddle). vision은 macOS 전용이고 빠르다",
    )
    parse.add_argument(
        "--reocr", action="store_true", help="ocr.json이 있어도 OCR을 다시 돌린다"
    )
    parse.add_argument(
        "--inspect",
        nargs="?",
        const="out/inspect",
        default=None,
        type=Path,
        help=(
            "이번 실행이 공고를 어떻게 읽었는지 점검용 HTML로 쓴다 "
            "(기본 out/inspect/{공고}.html). OCR 원문이 실리므로 커밋하지 않는다"
        ),
    )
    parse.set_defaults(func=_cmd_parse)

    scoring = sub.add_parser(
        "score",
        help="공고 하나로 지원자들을 채점해 랭킹과 근거를 낸다",
        description=(
            "parse 다음에 부른다. 공고를 조건→루브릭으로 만들고(prepare) 이력서를 "
            "채점해(score) 0~100점 랭킹과 근거를 낸다. 결과는 "
            "data/runs/{run_id}/result.json에도 저장된다."
        ),
    )
    scoring.add_argument(
        "--posting", required=True, type=Path, help="공고 디렉터리 (예: data/postings/kt-b2c)"
    )
    scoring.add_argument(
        "--resumes",
        required=True,
        type=Path,
        help="이력서 디렉터리 (예: data/resumes/kt-b2c). candidate_id·text를 가진 JSON을 읽는다",
    )
    scoring.add_argument(
        "--position",
        default=None,
        help=(
            "한 공고에 직무가 여럿일 때 대상 직무 라벨. 이 값으로 표의 y 구간을 자른다. "
            "안 주면 provenance.json에 적힌 값을 쓴다"
        ),
    )
    scoring.add_argument(
        "--source",
        default="local",
        choices=["local", "saramin"],
        help=(
            "공고를 어느 어댑터로 집는가 (기본: local). saramin은 키를 요구한다 — "
            "이미지를 다시 받지는 않는다"
        ),
    )
    scoring.add_argument(
        "--ocr-engine",
        default=None,
        choices=["paddle", "vision"],
        help="기본은 설정값(paddle). ocr.json이 이미 있으면 어느 쪽이든 재사용한다",
    )
    scoring.add_argument(
        "--json", action="store_true", help="사람이 읽는 글 대신 RunResult를 그대로 stdout에"
    )
    scoring.add_argument(
        "--no-judge",
        action="store_true",
        help="판단 층을 건너뛴다 (개발용). 65점 축이 통째로 빠지므로 제출물로 성립하지 않는다",
    )
    scoring.add_argument(
        "--skip-approval",
        action="store_true",
        help=(
            "고객사 승인 없이 채점한다. 결과와 화면 상단에 「미승인」이 붙는다 — "
            "조용히 지나갈 수 없다 (승인은 브라우저 화면의 POST /approve가 한다)"
        ),
    )
    scoring.set_defaults(func=_cmd_score)
    return parser


def _cmd_acquire(args: argparse.Namespace) -> int:
    try:
        provenance = write_provenance(args.posting, args.source, target_position=args.position)
    except ProvenanceError as exc:
        print(f"실패: {exc}")
        return 1

    print(f"{provenance.posting_id}: 이미지 {len(provenance.image_sha256)}장 → provenance.json")
    for index, (digest, (width, height)) in enumerate(
        zip(provenance.image_sha256, provenance.image_size, strict=True), start=1
    ):
        print(f"  img_{index}: {width}x{height}px  sha256 {digest[:16]}…")
    if provenance.target_position:
        print(f"  대상 직무: {provenance.target_position}")
    if not provenance.api_verified:
        # 조용히 넘어가지 않는다 (docs/SCHEDULE.md §2 경로 B).
        print("  ⚠️ API 미검증 — 이미지 수동 확보")
    return 0


def _cmd_parse(args: argparse.Namespace) -> int:
    # 무거운 의존성(OCR 엔진)을 `acquire`만 쓰는 사람에게 지우지 않는다.
    from ..parser import ParseError, parse_posting
    from ..source import LocalSource, read_provenance

    settings = load_settings()
    if args.ocr_engine:
        settings.ocr_engine = args.ocr_engine

    directory = Path(args.posting)
    try:
        provenance = read_provenance(directory)
    except (OSError, ValueError) as exc:
        print(f"실패: provenance.json을 읽을 수 없다 — 먼저 acquire를 부른다 ({exc})")
        return 1

    # `data/postings/{id}` → `data`. 어댑터가 이미지 목록과 확보 시각을 들고 온다.
    data_dir = directory.parent.parent
    refs = LocalSource(data_dir=data_dir).list_postings()
    ref = next((item for item in refs if item.posting_id == provenance.posting_id), None)
    if ref is None:
        print(f"실패: {provenance.posting_id}: 공고 디렉터리를 못 찾았다")
        return 1

    client = None
    if settings.openai_api_key:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key)

    trace: dict | None = {} if args.inspect else None
    try:
        requirements, _, report = parse_posting(
            ref, settings, client=client, data_dir=data_dir, reocr=args.reocr, trace=trace
        )
    except ParseError as exc:
        print(f"실패: {exc}")
        return 1

    print(f"{ref.posting_id}: 조건 {len(requirements)}건 → requirements.json")
    print(f"  OCR {report.ocr_engine} · {report.line_count}줄 · LLM {report.llm_calls}회")
    print(f"  역할 {report.role_counts}")
    if report.excluded_blocks:
        print(f"  채점에서 뺀 섹션: {', '.join(report.excluded_blocks)}")
    kinds = {"required": 0, "preferred": 0, "gate": 0}
    for req in requirements:
        kinds[req.kind] += 1
    print(f"  필수 {kinds['required']} · 우대 {kinds['preferred']}")

    if trace is not None:
        from .inspect_html import render

        target = Path(args.inspect)
        if target.suffix.lower() != ".html":
            target = target / f"{ref.posting_id}.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render(trace), encoding="utf-8")
        print(f"  점검 화면 → {target}")
    return 0


def _cmd_score(args: argparse.Namespace) -> int:
    """공고 1개 × 이력서 n명 → 랭킹. **로직은 `api/service.py`에 있다.**

    실패는 전부 `실패: <사유>` 한 줄로 나간다. 예외 문자열을 그대로 흘리지 않고
    `redact()`를 통과시킨다 — 사람인 인증이 쿼리 파라미터라 httpx 예외에는 URL이
    통째로 실리고, 거기 키가 들어 있다.
    """
    from dataclasses import replace

    from ..judge.panel import BudgetExceeded
    from ..model.governance import GovernanceError
    from ..parser import ParseError
    from ..pipeline import ApprovalRequired, ApprovalStale, explain
    from ..source import SourceUnavailable, redact
    from .service import (
        EntryError,
        JudgeUnavailable,
        load_resumes,
        make_client,
        prepare_posting,
        score_proposal,
    )

    settings = load_settings()
    if args.skip_approval:
        # 원본을 고치지 않는다 — 설정 객체가 이 프로세스 안에서 공유될 수 있다.
        settings = replace(settings, skip_approval=True)

    try:
        client = make_client(settings)
        proposal = prepare_posting(
            args.posting,
            settings,
            source=args.source,
            position=args.position,
            ocr_engine=args.ocr_engine,
            client=client,
        )
        resumes = load_resumes(args.resumes)
        result, dropped = score_proposal(
            proposal,
            resumes,
            settings,
            client=client,
            data_dir=Path(args.posting).parent.parent,
            no_judge=args.no_judge,
        )
    except GovernanceError as exc:
        # **부분 결과를 내지 않는다.** 근거 없는 점수가 화면에 나가는 것보다 낫다.
        print(f"실패: 검산 위반 {len(exc.violations)}건 — 결과를 내보내지 않는다", file=sys.stderr)
        for violation in exc.violations[:10]:
            print(f"  {violation.rule} {violation.object_id}: {violation.message}", file=sys.stderr)
        return 1
    except (
        ApprovalRequired,
        ApprovalStale,
        BudgetExceeded,
        EntryError,
        JudgeUnavailable,
        ParseError,
        ProvenanceError,
        SourceUnavailable,
        OSError,
        ValueError,
    ) as exc:
        print(f"실패: {redact(str(exc))}", file=sys.stderr)
        return 1

    if dropped:
        # 조용히 지나갈 수 없게 만든다. `--json`일 때만 stderr로 비킨다 —
        # stdout이 JSON 하나여야 결과를 파이프로 넘길 수 있다.
        print(
            f"⚠️ 판단 층 생략 — 항목 {len(dropped)}개를 빼고 채점했다 (--no-judge). "
            "65점을 담당하던 축이 없으므로 이 결과는 제출물로 성립하지 않는다.",
            file=sys.stderr if args.json else sys.stdout,
        )

    if args.json:
        print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
    else:
        print(explain(result), end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = getattr(args, "func", None)
    if command is None:
        parser.print_help()
        return 0
    return command(args)
