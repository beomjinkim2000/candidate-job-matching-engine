"""단일 진입점 — 지금은 `acquire` 하나. 채점 서브커맨드는 step 8에서 붙인다."""

from __future__ import annotations

import argparse
from pathlib import Path

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
        "--posting", required=True, type=Path, help="공고 디렉터리 (예: data/postings/kt-nw)"
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


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = getattr(args, "func", None)
    if command is None:
        parser.print_help()
        return 0
    return command(args)
