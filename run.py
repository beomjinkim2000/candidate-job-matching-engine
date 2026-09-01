"""평가자가 실행하는 유일한 한 줄 — `python run.py`.

맥·윈도우 공통. 하는 일 순서:

1. 파이썬 버전 확인 (3.11 미만이면 무엇을 깔아야 하는지 알리고 멈춘다)
2. 의존성 확인 — 없으면 `.venv`를 만들어 설치하고 그 파이썬으로 다시 실행한다
3. `.env`에 `OPENAI_API_KEY`가 있으면 **묻지 않고 넘어간다**. 없으면 받아서 쓴다
4. 공고 이미지가 없으면 `provenance.json`의 URL에서 받고 **sha256을 대조한다** (아래)
5. 데이터 점검 — 무엇이 되고 무엇이 안 되는지 **먼저 말한다**
6. OCR은 **필요할 때만** 받고 돌린다 (아래)
7. `127.0.0.1`에 서버를 띄우고, **응답이 온 뒤에** 브라우저를 연다

## 로직을 셸 스크립트에 두지 않는다

`start.command`(맥)·`start.bat`(윈도우)는 **`python run.py` 한 줄짜리 껍데기**다.
zsh와 배치의 문법이 다르므로 로직을 양쪽에 두면 두 벌을 고쳐야 하고, 한 벌은
반드시 뒤처진다. 분기는 전부 여기 파이썬에 있다.

## 레포엔 URL만, 그림은 실행할 때 — 그리고 **해시를 대조한다**

이미지와 그 전사본(`ocr.json`)은 **공고 본문 그 자체**라 커밋하지 않는다
(원본 미적재 원칙, `docs/LEGAL_ARCHITECTURE.md`). 대신 `image_source.json`에
**받는 곳(`image_url`)** 을 한 줄 남긴다 — URL은 본문이 아니라 출처다.

받은 파일은 `provenance.json`에 이미 적혀 있던 `image_sha256`과 대조하고,
**다르면 저장하지 않고 지운다.** 이게 이 설계의 핵심이다 —
커밋된 `requirements.json`의 bbox 좌표는 **특정 그림 위에서만** 뜻이 있다.
그림이 바뀌었는데 그려 주면 **틀린 근거를 그럴듯하게** 보여주는 꼴이고,
그건 아무것도 안 보여주는 것보다 나쁘다.

**받기에 실패해도 서버는 뜬다.** 조건은 이미 커밋돼 있어 채점·랭킹·근거가 그대로 된다.
못 쓰게 되는 것은 파싱 확인 화면 하나뿐이고, 그 사실을 **화면이 비기 전에** 적는다.

## OCR을 기본으로 받지 않는다

PaddleOCR·paddlepaddle은 휠만 수백 MB이고 모델 가중치를 첫 실행에 또 받는다.
그런데 **랭킹을 보는 데는 OCR이 한 톨도 필요 없다** — `requirements.json`(파싱 결과)과
`data/resumes/**`가 저장소에 있어 채점이 그대로 재현된다. 랭킹 한 번 보려는 사람에게
수백 MB를 강제하면 「한 줄로 실행」이 거짓말이 된다.

받는 경우는 둘뿐이다.

- `data/postings/<공고>/`에 이미지가 있는데 `ocr.json`이 없을 때 —
  방금 받았거나 사용자가 직접 넣었다는 뜻이다. 그때만 묻고 받는다
- `--with-ocr`로 명시했을 때

**OCR은 오래 걸린다** (실측: 4920px 376초 · 2533px 232초). 그래서 시작하기 전에
어림한 소요를 먼저 말하고, 돌릴지 묻는다. 말없이 10분 멈춰 있으면 죽은 줄 안다.

## 키를 화면에 찍지 않는다

입력은 `getpass`로 받는다. 화면에 안 보이고 셸 히스토리에도 안 남는다. 저장한 뒤에도
「키가 설정됨」 한 줄만 찍는다 — 뒤 네 자리도 안 찍는다. 확인이 필요하면 파일을 보면
되고, 화면 캡처·터미널 로그·화면 공유에 키 조각이 남는 쪽이 더 비싸다.

`getpass`가 **에코 없는 입력을 못 만들면 경고만 하고 그냥 받는 폴백**이 있다.
그 폴백은 키를 화면에 찍는다. 그래서 경고를 예외로 승격시켜 **폴백을 막는다.**

## 저장 전에 `.gitignore`를 확인한다

`.env`가 무시 목록에 안 걸리면 **저장하지 않고 멈춘다.** 키를 파일에 쓰는 순간
다음 `git add .`가 그걸 커밋한다. 레포가 아니면(zip으로 받은 경우) 커밋될 곳이 없으므로
그대로 진행한다 — 「확인 못 함」과 「걸리지 않음」을 같게 취급하지 않는다.

## 바깥에 열지 않는다

`127.0.0.1`에만 바인딩한다. 같은 네트워크의 다른 기기에서 접근할 수 없다.
이력서를 다루는 로컬 도구다.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import warnings
import webbrowser
from pathlib import Path

# 3.10 이하에서도 이 파일이 **파싱은 되어야** 버전 안내를 띄울 수 있다.
# 그래서 walrus·match·런타임 제네릭 첨자를 쓰지 않는다.
MIN_PYTHON = (3, 11)

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
ENV_PATH = ROOT / ".env"
PYPROJECT = ROOT / "pyproject.toml"
VENV = ROOT / ".venv"

KEY_NAME = "OPENAI_API_KEY"

HOST = "127.0.0.1"
DEFAULT_PORT = 8000
# 8000이 막혀 있으면 옆으로 옮긴다. 「포트가 쓰이는 중」으로 멈추면 원클릭이 아니다.
PORT_SEARCH = 20

# 서버가 실제로 응답할 때까지 기다렸다 연다. 포트만 잡고 열면 빈 화면이 뜬다.
READY_TIMEOUT_SECONDS = 40.0
READY_POLL_SECONDS = 0.3

# 재실행 루프 방지. 이 값이 켜져 있는데도 의존성이 없으면 그냥 멈춘다.
BOOTSTRAP_FLAG = "MATCHING_BOOTSTRAPPED"

# `src/matching/source/base.py`의 IMAGE_GLOB·OCR_FILENAME과 같은 값이다.
# 여기서 다시 적는 이유는 **의존성이 깔리기 전에** 점검을 해야 하기 때문이다
# (matching을 import하면 pydantic·PIL이 필요하다).
IMAGE_GLOB = "img_*.png"
OCR_FILENAME = "ocr.json"
REQUIREMENTS_FILENAME = "requirements.json"
PROVENANCE_FILENAME = "provenance.json"
# 「어디서 받는가」. `provenance.json`과 나눠 둔 이유는 `fetch_images()` 설명에 있다.
IMAGE_SOURCE_FILENAME = "image_source.json"

# 이미지 내려받기. 수 MB짜리라 진행을 보여 준다.
DOWNLOAD_TIMEOUT_SECONDS = 30
DOWNLOAD_CHUNK = 1 << 16

# OCR 소요 어림. **이 숫자를 미리 말하지 않으면 사람이 죽은 줄 안다.**
#
# 공고 id로 표를 만들지 않는다 — 그러면 새 공고를 넣는 사람이 코드를 고쳐야 하고,
# 「직군 무관 일반화」와 어긋난다. 세로 픽셀에 비례한다고 보고 실측 둘로 눈금을 잡았다:
# 4920px→376초, 2533px→232초 (각각 0.076·0.092 초/px). 중간값을 쓴다.
# 정확할 필요는 없다. **자리를 뜨지 않고 기다릴지 판단할 수 있으면 된다.**
OCR_SECONDS_PER_PIXEL = 0.084
OCR_SECONDS_FLOOR = 60

# import 이름 → 배포 이름. 배포 이름은 pyproject에서 읽으므로 여기 목록은
# **「무엇이 없으면 못 뜬다」**를 판정하는 데만 쓴다.
RUNTIME_MODULES = {
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "pydantic": "pydantic",
    "openai": "openai",
    "httpx": "httpx",
    "dotenv": "python-dotenv",
    "PIL": "pillow",
}
OCR_MODULES = {"paddle": "paddlepaddle", "paddleocr": "paddleocr"}

# 이모지·박스문자를 쓰지 않는다. 윈도우 기본 콘솔에서 깨진다.
RULE = "-" * 62


# ------------------------------------------------------------------ 콘솔


def harden_console() -> None:
    """콘솔 출력이 인코딩 때문에 죽지 않게 한다.

    윈도우에서 stdout이 **진짜 콘솔**이면 파이썬이 WriteConsoleW로 쓰므로 코드페이지와
    무관하게 한글이 나온다. 이때 인코딩을 utf-8로 바꾸면 오히려 손해라 `errors`만 건드린다.

    **리다이렉트된 경우**(`python run.py > log.txt`)에는 로캘 인코딩(영문 윈도우면
    cp1252)이 잡혀 한글에서 UnicodeEncodeError가 난다. 그때만 utf-8로 바꾼다.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            if stream.isatty():
                reconfigure(errors="replace")
            else:
                reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):  # pragma: no cover - 콘솔 종류에 따라 갈린다
            pass


def say(message: str = "") -> None:
    print(message, flush=True)


# ------------------------------------------------------------ 파이썬 버전


def require_python() -> None:
    if sys.version_info >= MIN_PYTHON:
        return
    want = f"{MIN_PYTHON[0]}.{MIN_PYTHON[1]}"
    have = ".".join(str(part) for part in sys.version_info[:3])
    say(f"멈춘다: 파이썬 {want} 이상이 필요하다. 지금 실행 중인 것은 {have}이다.")
    say(f"  맥    : brew install python@{want}   또는 python.org 설치본")
    say("  윈도우: python.org 설치본 (설치할 때 'Add python.exe to PATH'를 켠다)")
    say("설치한 뒤 다시 `python run.py`를 실행한다.")
    raise SystemExit(1)


# -------------------------------------------------------------- 의존성


def missing_modules(mapping: dict) -> list:
    """import 가능한지만 본다. **실제로 import하지 않는다** — 무거운 패키지가 섞여 있다."""
    import importlib.util

    absent = []
    for module, distribution in mapping.items():
        try:
            found = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):  # 깨진 설치도 '없음'으로 친다
            found = False
        if not found:
            absent.append(distribution)
    return absent


def declared_dependencies(extra: str | None = None) -> list:
    """`pyproject.toml`이 정본이다. 목록을 여기 베껴 두면 반드시 어긋난다.

    `tomllib`은 3.11 표준 라이브러리다 — 우리 최소 버전이 3.11이라 추가 설치가 없다.
    """
    import tomllib

    with open(PYPROJECT, "rb") as handle:
        table = tomllib.load(handle)
    project = table.get("project", {})
    if extra is None:
        return list(project.get("dependencies", []))
    return list(project.get("optional-dependencies", {}).get(extra, []))


def in_managed_env() -> bool:
    """이미 격리된 환경 안인가.

    시스템 파이썬에 `pip install`을 하면 최근 맥·리눅스에서는 PEP 668
    (`externally-managed-environment`)로 **거부당한다.** 그래서 격리 환경이 아니면
    우리가 `.venv`를 만든다. conda는 `sys.prefix` 비교로 안 잡혀 환경변수도 본다.
    """
    return sys.prefix != sys.base_prefix or bool(os.environ.get("CONDA_PREFIX"))


def venv_python(venv: Path) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def ensure_venv() -> Path:
    """`.venv`를 만들고 그 안의 파이썬 경로를 준다. 이미 있으면 그대로 쓴다."""
    python = venv_python(VENV)
    if python.is_file():
        return python

    say("가상환경을 만든다: .venv  (처음 한 번만 걸린다)")
    done = subprocess.run([sys.executable, "-m", "venv", str(VENV)])
    if done.returncode != 0 or not python.is_file():
        say("멈춘다: 가상환경을 만들지 못했다.")
        say("  직접 만들고 다시 실행한다:")
        say("    python -m venv .venv")
        say("    맥    : .venv/bin/python run.py")
        say("    윈도우: .venv\\Scripts\\python.exe run.py")
        raise SystemExit(1)
    return python


def pip_install(python: Path, packages: list, label: str) -> bool:
    """설치. **출력을 가로채지 않는다** — 수백 MB짜리가 섞여 있어 진행이 보여야 한다."""
    if not packages:
        return True
    say(f"설치: {label}")
    command = [str(python), "-m", "pip", "install"] + list(packages)
    try:
        done = subprocess.run(command, cwd=str(ROOT))
    except OSError as exc:
        say(f"설치를 실행하지 못했다: {exc}")
        return False
    return done.returncode == 0


def ready_elsewhere(python: Path) -> bool:
    """**저쪽 파이썬**에는 이미 다 깔려 있는가.

    두 번째 실행부터가 이 경우다 — 시스템 파이썬으로 `python run.py`를 쳤지만
    `.venv`는 이미 완성돼 있다. 확인 없이 pip을 부르면 「already satisfied」를 얻자고
    네트워크를 한 번 타는데, 비행기 안이나 사내망에서는 그게 실패로 돌아온다.
    """
    code = (
        "import importlib.util as u, sys; "
        f"sys.exit(0 if all(u.find_spec(m) for m in {list(RUNTIME_MODULES)!r}) else 1)"
    )
    try:
        return subprocess.run([str(python), "-c", code], capture_output=True).returncode == 0
    except OSError:
        return False


def bootstrap(argv: list) -> None:
    """의존성이 없으면 깔고, 필요하면 다른 파이썬으로 **다시 실행한다.**

    설치 뒤에는 항상 새 프로세스로 넘긴다. 방금 깐 패키지를 같은 프로세스에서 import하면
    경로·메타데이터 캐시가 낡아 있을 수 있다 — 원클릭에서 그 실패는 원인을 알기 어렵다.
    """
    absent = missing_modules(RUNTIME_MODULES)
    if not absent:
        return

    if os.environ.get(BOOTSTRAP_FLAG):
        say("멈춘다: 설치 뒤에도 의존성이 없다 — {}".format(", ".join(absent)))
        say("  네트워크가 막혀 있거나 설치가 실패했다. 직접 깔고 다시 실행한다:")
        say("    python -m pip install -e .")
        raise SystemExit(1)

    managed = in_managed_env()

    # 이미 완성된 `.venv`가 있으면 **아무것도 설치하지 않고** 그리로 넘어간다.
    prepared = venv_python(VENV)
    if not managed and prepared.is_file() and ready_elsewhere(prepared):
        say(f"준비된 환경으로 넘어간다: {prepared}")
        raise SystemExit(reexec(prepared, argv))

    say(RULE)
    say("처음 실행이다. 필요한 패키지를 받는다: {}".format(", ".join(absent)))
    say("OCR은 여기 포함하지 않는다 — 채점과 랭킹에는 필요 없다.")
    say(RULE)

    python = Path(sys.executable) if managed else ensure_venv()
    if not pip_install(python, declared_dependencies(), "실행 의존성"):
        say("멈춘다: 설치가 실패했다. 위 pip 출력을 본다.")
        raise SystemExit(1)

    raise SystemExit(reexec(python, argv))


def reexec(python: Path, argv: list) -> int:
    """새 파이썬으로 자기 자신을 다시 실행한다.

    `os.execv`를 쓰지 않는다 — 윈도우에는 실제 exec이 없어 콘솔 소유권과 Ctrl+C 처리가
    어긋난다. `subprocess`는 양쪽에서 같게 돈다.
    """
    environment = dict(os.environ)
    environment[BOOTSTRAP_FLAG] = "1"
    command = [str(python), str(Path(__file__).resolve())] + list(argv)
    try:
        return subprocess.run(command, env=environment).returncode
    except KeyboardInterrupt:
        return 130


# ------------------------------------------------------------------ 키


def has_key() -> bool:
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


def gitignored() -> bool:
    """`.env`가 `.gitignore`에 걸리는가.

    `git check-ignore`는 걸리면 0, 안 걸리면 1, 저장소가 아니거나 git이 없으면 128이다.
    **128은 「안전」이다** — 커밋될 저장소 자체가 없다. 1만 위험이다.
    """
    try:
        done = subprocess.run(
            ["git", "check-ignore", "-q", ENV_PATH.name],
            cwd=str(ROOT),
            capture_output=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return True  # git이 없다. 커밋될 곳이 없으므로 막을 이유도 없다
    return done.returncode != 1


def write_key(key: str) -> None:
    """`.env`의 `OPENAI_API_KEY` 줄만 갈아 끼운다. **파일을 통째로 덮어쓰지 않는다.**

    `os.open`으로 권한을 **만들 때** 준다. 먼저 만들고 나중에 `chmod`하면 그 사이에
    키가 세상에 열린 파일로 잠깐 존재한다.

    윈도우에는 POSIX 권한 비트가 없다 — `os.chmod`가 읽기전용 플래그 하나로만 번역되어
    「소유자만 읽기」가 안 된다. 예외를 내지 않고 조용히 무시되므로 **실패하지는
    않지만**, 보호가 약하다는 사실을 그때 알린다.
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
    try:
        os.chmod(ENV_PATH, 0o600)
    except OSError:  # pragma: no cover - 파일시스템에 따라 갈린다
        pass


def read_secret(prompt: str) -> str:
    """에코 없이 받는다. **에코되는 폴백으로 새지 않게 막는다.**

    `getpass`는 무에코 입력을 못 만들면 `GetPassWarning`만 남기고 `input()`으로 넘어간다.
    그러면 키가 화면에 찍힌다. 경고를 예외로 올려 그 경로를 끊고, TTY가 아니면 아예 묻지
    않는다(파이프로 넘긴 키는 셸 히스토리와 로그에 남는다).
    """
    if not sys.stdin or not sys.stdin.isatty():
        raise SystemExit(
            "멈춘다: 터미널이 아니라 키를 안전하게 받을 수 없다.\n"
            f"  터미널에서 `python run.py`를 직접 실행하거나, .env에 {KEY_NAME}=... 한 줄을 넣는다."
        )
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            return getpass.getpass(prompt).strip()
        except getpass.GetPassWarning as exc:
            raise SystemExit(
                f"멈춘다: 이 터미널은 입력을 가려 주지 못한다 ({exc}). "
                "키가 화면에 찍히므로 받지 않는다. .env에 직접 넣는다."
            ) from None


def ensure_key() -> None:
    """키가 없으면 받아서 `.env`에 쓴다. 있으면 아무 말 없이 넘어간다."""
    if has_key():
        return

    if not gitignored():
        raise SystemExit(
            f"멈춘다: {ENV_PATH.name}이 .gitignore에 걸리지 않는다. "
            "키를 여기 쓰면 다음 커밋에 그대로 들어간다. "
            f".gitignore에 `{ENV_PATH.name}`을 추가한 뒤 다시 실행한다."
        )

    say(RULE)
    say("OpenAI API 키가 없다. 공고 파싱(헤더 분류)과 심사위원 채점에 필요하다.")
    say(f"입력한 값은 화면에 표시되지 않고 {ENV_PATH.name}에만 저장된다.")
    say(RULE)
    try:
        key = read_secret(f"{KEY_NAME}: ")
    except (EOFError, KeyboardInterrupt):
        raise SystemExit("\n멈춘다: 키를 받지 못했다.") from None

    if not key:
        raise SystemExit("멈춘다: 빈 값이다. 키 없이는 판단 층을 채점할 수 없다.")

    write_key(key)
    # 키의 어느 조각도 찍지 않는다.
    say(f"키가 설정됨 — {ENV_PATH.name}에 저장했다.")
    if os.name == "nt":
        say("참고: 윈도우에는 POSIX 권한이 없어 '소유자만 읽기'를 걸지 못했다.")
        say(f"      공용 PC라면 다 쓴 뒤 {ENV_PATH.name}를 지운다.")


# ----------------------------------------------------------- 공고 이미지 확보


def read_json(path: Path) -> dict:
    """작은 JSON 하나를 **표준 라이브러리로** 읽는다. 없거나 깨졌으면 빈 표다.

    `matching.source.read_provenance`를 쓰지 않는 이유는 하나다 — 이 단계는
    pydantic이 있든 없든 같은 답을 내야 하고, 여기서 필요한 건 두 필드뿐이다.
    """
    if not path.is_file():
        return {}
    try:
        table = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return table if isinstance(table, dict) else {}


def encoded_url(url: str) -> str:
    """비-ASCII가 섞인 URL을 퍼센트 인코딩한다.

    `urllib`은 한글·공백이 든 주소를 그대로 못 보내고 `UnicodeEncodeError`를 던진다.
    사람인 주소는 전부 ASCII라 지금은 안 걸리지만, **새 공고를 넣는 사람**이 한글
    파일명을 쓸 수 있다. 그때 「받지 못했다: UnicodeEncodeError」만 보면 원인을 못 찾는다.

    ASCII면 손대지 않는다 — 이미 인코딩된 주소를 다시 인코딩해 `%`가 `%25`가 되는
    사고를 원천 차단한다.
    """
    if url.isascii():
        return url
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            urllib.parse.quote(parts.path, safe="/%"),
            urllib.parse.quote(parts.query, safe="=&%"),
            parts.fragment,
        )
    )


def download_and_verify(url: str, expected: str, target: Path, label: str) -> bool:
    """받아서 **해시가 맞을 때만** 저장한다.

    ## 해시 대조가 이 함수의 존재 이유다

    커밋돼 있는 것은 조건과 **좌표**(`requirements.json`의 `source_bbox`)다. 좌표는
    특정 이미지 위에서만 뜻이 있다. 그림이 한 픽셀이라도 다르면 근거를 클릭했을 때
    엉뚱한 곳에 네모가 그려지고, 그건 **틀린 근거를 그럴듯하게 보여주는 것**이라
    아무것도 안 보여주는 것보다 나쁘다.

    그래서 다르면 **조용히 쓰지 않고 지운다.** 해시가 다르다는 건 공고가 바뀌었다는
    뜻이고, 그러면 커밋된 조건이 낡은 것이다 — 검산 G7이 승인에 대해 하는 말과 같다.

    받는 동안 해시를 같이 계산한다. 다 받고 나서 다시 읽으면 수 MB를 두 번 읽는다.
    """
    # 최종 파일 이름으로 바로 받지 않는다. 중간에 끊기면 **반쪽짜리 그림**이 남고,
    # 다음 실행은 그걸 「이미 있다」로 보고 넘어간다.
    staging = target.with_name(target.name + ".part")
    digest = hashlib.sha256()
    received = 0
    show_progress = bool(sys.stdout and sys.stdout.isatty())

    request = urllib.request.Request(
        encoded_url(url),
        # 기본 `Python-urllib/3.x`를 거르는 CDN이 있다. 신분을 속이는 게 아니라
        # 브라우저가 보내는 것과 같은 수준을 보낸다.
        headers={"User-Agent": "Mozilla/5.0 (compatible; matching-engine/0.1)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
            total = int(response.headers.get("Content-Length") or 0)
            with staging.open("wb") as handle:
                while True:
                    chunk = response.read(DOWNLOAD_CHUNK)
                    if not chunk:
                        break
                    handle.write(chunk)
                    digest.update(chunk)
                    received += len(chunk)
                    if show_progress:
                        size = f"{received / 1048576:.1f} MB"
                        of = f" / {total / 1048576:.1f} MB" if total else ""
                        print(f"\r  {label}: {size}{of}   ", end="", flush=True)
    except Exception as exc:
        # 네트워크·DNS·HTTP·디스크 — 무엇이든 여기서 끝낸다. 서버는 그대로 뜬다.
        staging.unlink(missing_ok=True)
        if show_progress:
            print("\r", end="")
        say(f"  {label}: 받지 못했다 — {type(exc).__name__}: {exc}")
        return False

    if show_progress:
        print("\r", end="")

    actual = digest.hexdigest()
    if actual != expected:
        staging.unlink(missing_ok=True)
        say(f"  {label}: 해시가 다르다 — 저장하지 않는다")
        say(f"    기록 {expected[:16]}… · 받은 것 {actual[:16]}…")
        say("    공고가 바뀌었다는 뜻이다. 커밋된 조건과 좌표는 예전 그림의 것이라,")
        say("    이 그림 위에 네모를 그리면 엉뚱한 곳을 가리킨다. 그래서 버린다.")
        return False

    staging.replace(target)
    say(f"  {label}: 받았다 ({received / 1048576:.1f} MB · 해시 일치)")
    return True


def fetch_images() -> None:
    """이미지가 없는 공고를 `image_source.json`의 URL에서 받는다.

    ## 왜 레포에 URL만 두는가

    그림 자체는 **공고 본문**이라 커밋하지 않는다. URL은 본문이 아니라 **가져오는 곳**
    이다. 그래서 저장소에는 한 줄만 남기고, 실행하는 사람이 자기 머신으로 받는다.
    「고객사가 이미지를 보낸다」는 프로덕션 구조의 데모 대역이기도 하다.

    ## 왜 `provenance.json`이 아니라 별도 파일인가

    `provenance.json`은 **「우리가 무엇을 파싱했는가」의 증거**이고, 거기 URL을 넣지
    않는 것이 명시된 계약이다 — `tests/test_provenance.py::test_provenance_file_leaks_nothing`
    이 `https?://`와 키 목록을 직접 검사한다(`docs/SCHEDULE.md` §3 ·
    `docs/LEGAL_ARCHITECTURE.md` §②도 같은 말이다). 게다가 `write_provenance()`는
    그 파일을 **처음부터 다시 만들기 때문에** `acquire`를 한 번 더 부르면 URL이 사라진다.

    그래서 둘을 나눴다. **증거(해시)는 `provenance.json`, 가져오는 곳은
    `image_source.json`.** 대조는 두 파일을 맞춰 본다 — 순서가 같다는 것이 계약이다.

    ## 실패해도 서버는 뜬다

    망이 없거나 URL이 죽었으면 그 사실만 적고 넘어간다. `requirements.json`이 커밋돼
    있어 **채점·랭킹·근거는 그대로 된다.** 못 쓰게 되는 것은 파싱 확인 화면 하나다.
    """
    postings = data_dir() / "postings"
    if not postings.is_dir():
        return

    jobs = []
    for directory in sorted(postings.iterdir()):
        if not directory.is_dir() or sorted(directory.glob(IMAGE_GLOB)):
            continue  # 이미 있으면 건드리지 않는다
        urls = read_json(directory / IMAGE_SOURCE_FILENAME).get("image_url") or []
        hashes = read_json(directory / PROVENANCE_FILENAME).get("image_sha256") or []
        if not urls:
            continue
        if len(urls) != len(hashes):
            # 대조할 수 없으면 **받지 않는다.** 해시 없는 그림은 좌표의 근거가 못 된다.
            say(
                f"{directory.name}: {IMAGE_SOURCE_FILENAME}의 URL {len(urls)}개와 "
                f"{PROVENANCE_FILENAME}의 해시 {len(hashes)}개가 안 맞는다 — 받지 않는다"
            )
            continue
        jobs.append((directory, urls, hashes))

    if not jobs:
        return

    say(RULE)
    say("공고 이미지를 받는다. 저장소에는 URL만 있고 그림은 없다 —")
    say("그림은 공고 본문이라 커밋하지 않는다 (원본 미적재 원칙).")
    say("받은 뒤 provenance.json의 sha256과 대조하고, 다르면 저장하지 않는다.")
    say(RULE)

    for directory, urls, hashes in jobs:
        # 길이가 같은 것은 위에서 이미 걸렀다. `strict`로 그 전제를 코드에 박아 둔다.
        for index, (url, expected) in enumerate(zip(urls, hashes, strict=True), start=1):
            target = directory / f"img_{index}.png"
            download_and_verify(url, expected, target, f"{directory.name} {index}쪽")
    say(RULE)


# --------------------------------------------------------------- 데이터 점검


def data_dir() -> Path:
    """`src/matching/source/base.py::default_data_dir`과 같은 규칙."""
    override = os.environ.get("MATCHING_DATA_DIR")
    return Path(override) if override else ROOT / "data"


def posting_state() -> list:
    """공고마다 (id, 조건 있음, 이미지 장수, ocr 있음)."""
    postings = data_dir() / "postings"
    if not postings.is_dir():
        return []
    rows = []
    for directory in sorted(postings.iterdir()):
        if not directory.is_dir():
            continue
        rows.append(
            (
                directory.name,
                (directory / REQUIREMENTS_FILENAME).is_file(),
                len(sorted(directory.glob(IMAGE_GLOB))),
                (directory / OCR_FILENAME).is_file(),
            )
        )
    return rows


def resume_counts() -> list:
    resumes = data_dir() / "resumes"
    if not resumes.is_dir():
        return []
    rows = []
    for directory in sorted(resumes.iterdir()):
        if not directory.is_dir():
            continue
        count = len([p for p in directory.glob("*.json") if p.stem not in {"index", "holdout"}])
        rows.append((directory.name, count))
    return rows


def preflight() -> None:
    """무엇이 되고 무엇이 안 되는지 **서버를 띄우기 전에** 적는다.

    저장소에는 공고 이미지도 `ocr.json`도 없다. 그러면 파싱 확인 화면이 비는데,
    그걸 화면에서 처음 알게 하면 「고장났다」로 읽힌다. 사유와 복구 방법을 같이 준다.
    """
    postings = posting_state()
    resumes = resume_counts()

    say(RULE)
    say("데이터 점검")
    if not postings:
        say(f"  공고가 없다: {data_dir() / 'postings'}")
    for posting_id, has_requirements, images, has_ocr in postings:
        matched = dict(resumes).get(posting_id, 0)
        conditions = "있음" if has_requirements else "없음"
        ocr = "있음" if has_ocr else "없음"
        say(
            f"  {posting_id:<12} 조건 {conditions} · 이력서 {matched}명 · "
            f"이미지 {images}장 · OCR {ocr}"
        )

    scorable = [row[0] for row in postings if row[1]]
    verdict = f"가능 ({len(scorable)}개 공고)" if scorable else "불가"
    say("")
    say(f"  채점·랭킹·근거: {verdict}")

    traceable = [row[0] for row in postings if row[2] and row[3]]
    imageless = [row[0] for row in postings if not row[2]]
    if traceable:
        say("  파싱 확인 화면: 가능 ({})".format(", ".join(traceable)))
    if imageless:
        say("  파싱 확인 화면: 불가 ({}) — 그림이 없다".format(", ".join(imageless)))
        say("    그림은 공고 본문이라 커밋하지 않는다 (원본 미적재 원칙 ·")
        say("    docs/LEGAL_ARCHITECTURE.md). 대신 image_source.json에 받는 곳을 적어 두고")
        say("    실행할 때 내려받아 provenance.json의 sha256과 대조한다. 방금 그게 안 됐다 —")
        say("    망이 막혔거나, URL이 죽었거나, 공고가 바뀌어 해시가 달라졌다.")
        say("    직접 넣어도 된다: data/postings/<공고>/img_1.png")
    pending = [row[0] for row in postings if row[2] and not row[3]]
    if pending:
        say("  파싱 확인 화면: 그림은 있고 OCR 결과가 아직 없다 ({})".format(", ".join(pending)))
    say("")
    say("  채점·랭킹은 위 어느 경우에도 영향받지 않는다 — 조건이 이미 커밋돼 있다.")
    say(RULE)


# ------------------------------------------------------------------- OCR


def postings_awaiting_ocr() -> list:
    """이미지는 있는데 `ocr.json`이 없는 공고. **사용자가 직접 넣었다는 신호**다."""
    return [row[0] for row in posting_state() if row[2] and not row[3]]


def ask_yes(question: str, default: bool = True) -> bool:
    if not sys.stdin or not sys.stdin.isatty():
        return False
    suffix = "[Y/n]" if default else "[y/N]"
    try:
        answer = input(f"{question} {suffix} ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    if not answer:
        return default
    return answer[0] in ("y", "1")


def ocr_estimate_seconds(posting_id: str) -> int:
    """이 공고를 OCR하는 데 얼마나 걸릴지. 세로 픽셀에서 어림한다."""
    table = read_json(data_dir() / "postings" / posting_id / PROVENANCE_FILENAME)
    heights = [size[1] for size in table.get("image_size") or [] if len(size) == 2]
    if not heights:
        return OCR_SECONDS_FLOOR * 5
    return max(OCR_SECONDS_FLOOR, int(sum(heights) * OCR_SECONDS_PER_PIXEL))


def run_ocr_for(posting_id: str) -> bool:
    """`ocr.json`을 만든다. **새 프로세스에서 돌린다.**

    방금 깐 paddle을 같은 프로세스에서 import하면 캐시가 낡아 엉뚱한 실패가 난다.
    그리고 이 호출이 모델 가중치를 받는 시점이기도 하다 — 「필요할 때만 받는다」의
    「필요할 때」가 바로 여기다.

    **LLM을 부르지 않는다.** `load_or_run_ocr`은 그림에서 줄과 좌표만 뽑는다.
    헤더 역할 분류(공고당 1회 LLM)는 화면에서 파싱을 누를 때 일어난다.
    """
    seconds = ocr_estimate_seconds(posting_id)
    say(f"  {posting_id}: OCR 시작 — 약 {seconds // 60}분 {seconds % 60}초 걸린다. 기다린다.")
    code = (
        "import sys; from pathlib import Path; "
        "from matching.source import image_paths; "
        "from matching.parser.ocr import load_or_run_ocr; "
        "d = Path(sys.argv[1]); "
        "result, fresh = load_or_run_ocr(d, image_paths(d)); "
        "print(f'  줄 {len(result.lines)}개 · 평균 신뢰도 {result.avg_conf} "
        "· {result.elapsed_sec}초')"
    )
    directory = data_dir() / "postings" / posting_id
    environment = dict(os.environ)
    existing = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = str(SRC) + (os.pathsep + existing if existing else "")
    try:
        done = subprocess.run(
            [sys.executable, "-c", code, str(directory)], cwd=str(ROOT), env=environment
        )
    except OSError as exc:  # pragma: no cover
        say(f"  {posting_id}: OCR을 실행하지 못했다 ({exc})")
        return False
    if done.returncode != 0:
        say(f"  {posting_id}: OCR이 실패했다. 화면에서 파싱을 누르면 다시 시도한다.")
        return False
    return True


def setup_ocr(force: bool) -> None:
    """OCR을 **필요할 때만** 깔고 돌린다.

    채점 경로는 여기서 무슨 일이 나든 살아 있어야 한다 — 설치가 깨져도, 망이 없어도,
    사용자가 거절해도 서버는 뜬다.
    """
    waiting = postings_awaiting_ocr()
    absent = missing_modules(OCR_MODULES)

    if not waiting and not force:
        return

    if absent:
        say(RULE)
        if waiting:
            say("이미지는 있는데 OCR 결과가 없는 공고: {}".format(", ".join(waiting)))
            say("이 그림에서 줄과 좌표를 뽑으려면 OCR이 필요하다.")
        say("받을 것: {} — 수백 MB이고 모델 가중치를 또 받는다.".format(", ".join(absent)))
        say("건너뛰어도 채점·랭킹·근거는 그대로 된다.")
        say(RULE)

        if not force and not ask_yes("지금 받을까?", default=True):
            say("건너뛴다. 나중에 받으려면 `python run.py --with-ocr`.")
            return

        if not pip_install(Path(sys.executable), declared_dependencies("ocr"), "OCR (선택)"):
            # 윈도우·특정 파이썬 버전에서 paddlepaddle 휠이 없을 수 있다.
            # **여기서 멈추지 않는다** — OCR이 없어도 채점은 된다.
            say("OCR 설치가 실패했다. 서버는 그대로 띄운다 — 채점·랭킹은 영향받지 않는다.")
            say('  파싱 확인 화면만 못 쓴다. 직접 깔려면: python -m pip install "paddlepaddle"')
            return

    if not waiting:
        say("OCR 준비됨 — 지금 돌릴 공고는 없다.")
        return

    total = sum(ocr_estimate_seconds(posting_id) for posting_id in waiting)
    say(RULE)
    say(f"OCR을 돌려 둘 수 있다. 공고 {len(waiting)}개, 다 합쳐 약 {total // 60}분이다.")
    say("**지금 안 돌려도 된다.** 대신 화면에서 파싱을 누를 때 같은 시간이 걸리고,")
    say("그때는 브라우저가 아무 말 없이 멈춰 보인다. 여기서 돌리면 진행이 보인다.")
    say(RULE)

    if not ask_yes("지금 돌릴까?", default=True):
        say("건너뛴다. 화면에서 파싱을 누르면 그때 돈다 (공고당 수 분).")
        return

    for posting_id in waiting:
        run_ocr_for(posting_id)


# ------------------------------------------------------------------ 서버


def pick_port(preferred: int) -> int:
    """빈 포트를 고른다.

    **`SO_REUSEADDR`를 걸지 않는다.** 윈도우에서 그 옵션은 「이미 쓰는 포트에도
    붙게 해 준다」는 뜻이라, 걸어 두면 점검이 항상 통과해 버린다. 리눅스·맥과 의미가
    정반대인 몇 안 되는 소켓 옵션이다.
    """
    for port in range(preferred, preferred + PORT_SEARCH):
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind((HOST, port))
            return port
        except OSError:
            continue
        finally:
            probe.close()
    last = preferred + PORT_SEARCH - 1
    raise SystemExit(f"멈춘다: {preferred}~{last} 사이에 빈 포트가 없다. `--port`로 지정한다.")


def open_when_ready(url: str) -> None:
    """서버가 **실제로 응답한 뒤에** 연다.

    프록시를 태우지 않는다 — 회사 PC에 `http_proxy`가 걸려 있으면 `127.0.0.1`행
    요청까지 프록시로 나가 영원히 실패한다.
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            with opener.open(url, timeout=2) as response:
                if response.status < 500:
                    webbrowser.open(url)
                    return
        except Exception:
            time.sleep(READY_POLL_SECONDS)
    say(f"서버 응답을 못 받았다. 브라우저에서 직접 연다: {url}")


def serve(port: int, open_browser: bool) -> int:
    # `pip install -e .` 없이도 돌게 한다. 평가자가 붙여넣는 줄이 하나여야 한다.
    if SRC.is_dir() and str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))

    try:
        import uvicorn

        from matching.api.server import create_app
    except ModuleNotFoundError as exc:
        say(f"멈춘다: 의존성이 없다 ({exc.name}). `python -m pip install -e .`를 실행한다.")
        return 1

    url = f"http://{HOST}:{port}"
    say("")
    say(f"채점 화면: {url}   (멈추려면 Ctrl+C)")
    say("")

    if open_browser:
        watcher = threading.Thread(target=open_when_ready, args=(url,), daemon=True)
        watcher.start()

    try:
        # 바깥에 열지 않는다. 로컬 전용이다.
        uvicorn.run(create_app(), host=HOST, port=port, log_level="info")
    except KeyboardInterrupt:  # pragma: no cover - uvicorn이 대개 먼저 잡는다
        pass
    say("서버를 내렸다.")
    return 0


# ------------------------------------------------------------------ 진입


def parse_args(argv: list):
    parser = argparse.ArgumentParser(
        prog="run.py",
        description="지원자-공고 매칭 스코어링 엔진을 로컬에서 띄운다.",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"기본 {DEFAULT_PORT}")
    parser.add_argument("--no-browser", action="store_true", help="브라우저를 열지 않는다")
    parser.add_argument(
        "--with-ocr",
        action="store_true",
        help="OCR(수백 MB)을 지금 받는다. 새 공고 이미지를 직접 파싱할 때만 필요하다",
    )
    parser.add_argument(
        "--no-ocr",
        action="store_true",
        help="OCR을 묻지도 받지도 않는다",
    )
    parser.add_argument(
        "--no-fetch",
        action="store_true",
        help="공고 이미지를 내려받지 않는다 (망이 없을 때)",
    )
    return parser.parse_args(argv)


def main(argv: list | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    harden_console()
    require_python()
    options = parse_args(raw)

    bootstrap(raw)  # 여기서 재실행되면 돌아오지 않는다
    ensure_key()
    # 점검보다 **먼저** 받는다. 순서를 뒤집으면 방금 받은 그림을 「없다」고 적는다.
    if not options.no_fetch:
        fetch_images()
    preflight()
    if not options.no_ocr:
        setup_ocr(force=options.with_ocr)

    return serve(pick_port(options.port), open_browser=not options.no_browser)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        say("")
        say("중단했다.")
        raise SystemExit(130) from None
