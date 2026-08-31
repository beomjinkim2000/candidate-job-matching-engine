"""단일 진입점 — 뼈대만. 서브커맨드는 step 8에서 붙인다."""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog="matching",
        description="지원자-공고 매칭 스코어링 엔진 (서브커맨드는 step 8에서 추가된다)",
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)
    parser.print_help()
    return 0
