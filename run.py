"""평가자가 실행하는 유일한 한 줄 — `python run.py`.

윈도우·맥 공통. 하는 일은 넷이다.

1. `.env`에 `OPENAI_API_KEY`가 있으면 **묻지 않고 넘어간다**
2. 없으면 터미널에서 키를 받아 `.env`에 쓴다 (권한 `0600`)
3. `127.0.0.1:8000`으로 서버를 띄운다
4. 브라우저를 연다

## 키를 화면에 찍지 않는다

입력은 `getpass`로 받는다. 화면에 안 보이고 셸 히스토리에도 안 남는다. 저장한 뒤에도
「키가 설정됨」 한 줄만 찍는다 — 뒤 네 자리도 안 찍는다. 확인이 필요하면 파일을 보면
되고, 화면 캡처·터미널 로그·화면 공유에 키 조각이 남는 쪽이 더 비싸다.

## 저장 전에 `.gitignore`를 확인한다

`.env`가 무시 목록에 안 걸리면 **저장하지 않고 멈춘다.** 키를 파일에 쓰는 순간
다음 `git add .`가 그걸 커밋한다. 레포가 아니면(zip으로 받은 경우) 커밋될 곳이 없으므로
그대로 진행한다 — 「확인 못 함」과 「걸리지 않음」을 같게 취급하지 않는다.

## 바깥에 열지 않는다

`127.0.0.1`에만 바인딩한다. 같은 네트워크의 다른 기기에서 접근할 수 없다.
이력서를 다루는 로컬 도구다.
"""

from __future__ import annotations

import getpass
import os
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
KEY_NAME = "OPENAI_API_KEY"

HOST = "127.0.0.1"
PORT = 8000
URL = f"http://{HOST}:{PORT}"

# 브라우저를 서버보다 먼저 열면 빈 화면이 뜬다. uvicorn이 소켓을 잡을 시간만 준다.
BROWSER_DELAY_SECONDS = 1.5


def _has_key() -> bool:
    """키가 이미 있는가. **값을 돌려주지 않는다** — 있으면 있다고만 한다."""
    if os.environ.get(KEY_NAME):
        return True
    if not ENV_PATH.is_file():
        return False
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        name, separator, value = line.partition("=")
        if separator and name.strip() == KEY_NAME and value.strip():
            return True
    return False


def _gitignored() -> bool:
    """`.env`가 `.gitignore`에 걸리는가.

    `git check-ignore`는 걸리면 0, 안 걸리면 1, 저장소가 아니거나 git이 없으면 128이다.
    **128은 「안전」이다** — 커밋될 저장소 자체가 없다. 1만 위험이다.
    """
    try:
        done = subprocess.run(
            ["git", "check-ignore", "-q", ENV_PATH.name],
            cwd=ROOT,
            capture_output=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return True  # git이 없다. 커밋될 곳이 없으므로 막을 이유도 없다
    return done.returncode != 1


def _write_key(key: str) -> None:
    """`.env`의 `OPENAI_API_KEY` 줄만 갈아 끼운다. **파일을 통째로 덮어쓰지 않는다.**

    `os.open`으로 권한을 **만들 때** 준다. 먼저 만들고 나중에 `chmod`하면 그 사이에
    키가 세상에 열린 파일로 잠깐 존재한다.
    """
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.is_file() else []
    replaced = False
    for index, line in enumerate(lines):
        name, separator, _ = line.partition("=")
        if separator and name.strip() == KEY_NAME:
            lines[index] = f"{KEY_NAME}={key}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{KEY_NAME}={key}")

    handle = os.open(ENV_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(handle, "w", encoding="utf-8") as target:
        target.write("\n".join(lines) + "\n")
    # 파일이 이미 있었으면 O_CREAT의 mode가 안 먹는다. 한 번 더 못박는다.
    os.chmod(ENV_PATH, 0o600)


def ensure_key() -> None:
    """키가 없으면 받아서 `.env`에 쓴다. 있으면 아무 말 없이 넘어간다."""
    if _has_key():
        return

    if not _gitignored():
        raise SystemExit(
            f"멈춘다: {ENV_PATH.name}이 .gitignore에 걸리지 않는다. "
            "키를 여기 쓰면 다음 커밋에 그대로 들어간다. "
            f".gitignore에 `{ENV_PATH.name}`을 추가한 뒤 다시 실행한다."
        )

    print("OpenAI API 키가 없다. 공고 파싱과 심사위원 채점에 필요하다.")
    print(f"입력한 값은 화면에 표시되지 않고 {ENV_PATH.name}에만 저장된다 (권한 0600).")
    try:
        key = getpass.getpass("OPENAI_API_KEY: ").strip()
    except (EOFError, KeyboardInterrupt):
        raise SystemExit("\n멈춘다: 키를 받지 못했다.") from None

    if not key:
        raise SystemExit("멈춘다: 빈 값이다. 키 없이는 판단 층을 채점할 수 없다.")

    _write_key(key)
    # 키의 어느 조각도 찍지 않는다.
    print(f"키가 설정됨 — {ENV_PATH.name}에 저장했다.")


def main() -> int:
    ensure_key()

    # `pip install -e .` 없이도 돌게 한다. 평가자가 붙여넣는 줄이 하나여야 한다.
    src = ROOT / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))

    try:
        import uvicorn

        from matching.api.server import create_app
    except ModuleNotFoundError as exc:
        print(f"멈춘다: 의존성이 없다 ({exc.name}). 먼저 `pip install -e .`를 실행한다.")
        return 1

    print(f"채점 화면: {URL}  (멈추려면 Ctrl+C)")
    timer = threading.Timer(BROWSER_DELAY_SECONDS, webbrowser.open, args=(URL,))
    timer.daemon = True
    timer.start()

    # 바깥에 열지 않는다. 로컬 전용이다.
    uvicorn.run(create_app(), host=HOST, port=PORT, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
