"""단일 진입점 — 지금은 `acquire`와 `parse`. 채점 서브커맨드는 step 8에서 붙인다."""

from __future__ import annotations

import argparse
from pathlib import Path

from ..config import load_settings
from ..source import ProvenanceError, write_provenance


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="matching",
        description="지원자-공고 매칭 스코어링 엔진 (채점 서브커맨드는 step 8에서 추가된다)",
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


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = getattr(args, "func", None)
    if command is None:
        parser.print_help()
        return 0
    return command(args)
