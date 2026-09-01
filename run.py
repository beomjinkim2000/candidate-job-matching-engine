"""평가자가 실행하는 유일한 한 줄 — `python run.py`.

맥·윈도우 공통. 하는 일 순서:

1. 파이썬 버전 확인 (3.11 미만이면 무엇을 깔아야 하는지 알리고 멈춘다)
2. 의존성 확인 — 없으면 `.venv`를 만들어 설치하고 그 파이썬으로 다시 실행한다
3. `.env`에 `OPENAI_API_KEY`가 있으면 **묻지 않고 넘어간다**. 없으면 받아서 쓴다
4. 데이터 점검 — 무엇이 되고 무엇이 안 되는지 **먼저 말한다**
5. OCR은 **필요할 때만** 받는다 (아래)
6. `127.0.0.1`에 서버를 띄우고, **응답이 온 뒤에** 브라우저를 연다

## 로직을 셸 스크립트에 두지 않는다

`start.command`(맥)·`start.bat`(윈도우)는 **`python run.py` 한 줄짜리 껍데기**다.
zsh와 배치의 문법이 다르므로 로직을 양쪽에 두면 두 벌을 고쳐야 하고, 한 벌은
반드시 뒤처진다. 분기는 전부 여기 파이썬에 있다.

## OCR을 기본으로 받지 않는다

PaddleOCR·paddlepaddle은 휠만 수백 MB이고 모델 가중치를 첫 실행에 또 받는다.
그런데 **랭킹을 보는 데는 OCR이 한 톨도 필요 없다** — `requirements.json`(파싱 결과)과
`data/resumes/**`가 저장소에 있어 채점이 그대로 재현된다. 랭킹 한 번 보려는 사람에게
수백 MB를 강제하면 「한 줄로 실행」이 거짓말이 된다.

받는 경우는 둘뿐이다.

- `data/postings/<공고>/`에 이미지가 있는데 `ocr.json`이 없을 때 —
  **사용자가 직접 이미지를 넣었다는 뜻**이다. 그때만 묻고 받는다
- `--with-ocr`로 명시했을 때

## 저장소에 공고 이미지가 없다 — 조용히 비우지 않는다

이미지와 그 전사본(`ocr.json`)은 **공고 본문 그 자체**라 커밋하지 않는다
(원본 미적재 원칙, `docs/LEGAL_ARCHITECTURE.md`). 그래서 평가자 머신에서는
파싱 확인 화면이 빈다. 그걸 화면이 비고 나서 알게 하지 않는다 — 서버를 띄우기 전에
**무엇이 없고 왜 없는지, 넣으면 무엇이 되는지**를 터미널에 먼저 적는다.

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
import os
import socket
import subprocess
import sys
import threading
import time
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
    if traceable:
        say("  파싱 확인 화면: 가능 ({})".format(", ".join(traceable)))
    else:
        say("  파싱 확인 화면: 불가 — 공고 이미지와 OCR 결과가 저장소에 없다")
        say("    공고 이미지와 그 전사본(ocr.json)은 공고 본문 그 자체라 커밋하지 않는다")
        say("    (원본 미적재 원칙 · docs/LEGAL_ARCHITECTURE.md).")
        say("    직접 보려면 data/postings/<공고>/img_1.png 로 이미지를 두고 다시 실행한다.")
        say("    그때 OCR 패키지를 받을지 물어본다 — 지금은 받지 않는다.")
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


def warm_ocr_models() -> None:
    """모델 가중치를 미리 받는다.

    **새 프로세스에서 한다.** 방금 깐 paddle을 같은 프로세스에서 import하면 캐시가 낡아
    엉뚱한 실패가 난다. 그리고 이걸 안 해 두면 첫 파싱 요청이 브라우저에서 몇 분 동안
    아무 말 없이 멈춰 보인다 — 그 침묵이 「고장」으로 읽힌다.
    """
    say("OCR 모델 가중치를 받는다. 처음 한 번이고 몇 분 걸린다.")
    # `_paddle()`이 우리가 쓰는 모델 조합을 정하는 **단 한 곳**이다. 생성자 인자를
    # 여기 베끼면 두 곳이 갈라져 미리 받은 모델과 실제로 쓰는 모델이 달라진다.
    code = "from matching.parser.ocr import _paddle; _paddle()"
    environment = dict(os.environ)
    existing = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = str(SRC) + (os.pathsep + existing if existing else "")
    try:
        done = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=environment)
    except OSError as exc:  # pragma: no cover
        say(f"모델을 미리 받지 못했다 ({exc}). 첫 파싱 때 받는다.")
        return
    if done.returncode != 0:
        say("모델을 미리 받지 못했다. 첫 파싱 때 다시 시도한다.")
    else:
        say("OCR 준비 완료.")


def setup_ocr(force: bool) -> None:
    """OCR을 **필요할 때만** 깐다. 채점 경로는 여기서 무슨 일이 나든 살아 있어야 한다."""
    absent = missing_modules(OCR_MODULES)
    if not absent:
        if force:
            say("OCR은 이미 설치돼 있다.")
        return

    waiting = postings_awaiting_ocr()
    if not force and not waiting:
        return

    say(RULE)
    if waiting:
        say("이미지는 있는데 OCR 결과가 없는 공고: {}".format(", ".join(waiting)))
        say("이걸 파싱하려면 OCR이 필요하다.")
    say("받을 것: {} — 수백 MB이고 모델 가중치를 첫 실행에 또 받는다.".format(", ".join(absent)))
    say("건너뛰어도 채점·랭킹·근거는 그대로 된다.")
    say(RULE)

    if not force and not ask_yes("지금 받을까?", default=True):
        say("건너뛴다. 나중에 받으려면 `python run.py --with-ocr`.")
        return

    python = Path(sys.executable)
    if not pip_install(python, declared_dependencies("ocr"), "OCR (선택)"):
        # 윈도우·특정 파이썬 버전에서 paddlepaddle 휠이 없을 수 있다.
        # **여기서 멈추지 않는다** — OCR이 없어도 채점은 된다.
        say("OCR 설치가 실패했다. 서버는 그대로 띄운다 — 채점·랭킹은 영향받지 않는다.")
        say('  파싱 확인 화면만 못 쓴다. 직접 깔려면: python -m pip install "paddlepaddle"')
        return

    warm_ocr_models()


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
    return parser.parse_args(argv)


def main(argv: list | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    harden_console()
    require_python()
    options = parse_args(raw)

    bootstrap(raw)  # 여기서 재실행되면 돌아오지 않는다
    ensure_key()
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
