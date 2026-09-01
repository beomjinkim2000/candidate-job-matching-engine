#!/bin/sh
# macOS 껍데기 — 더블클릭하면 이 파일이 돈다.
#
# 로직은 여기 두지 않는다. 전부 run.py에 있다. zsh와 배치의 문법이 달라서 로직을
# 양쪽에 두면 두 벌을 고쳐야 하고, 한 벌은 반드시 뒤처진다.
#
# 여기 남는 일은 하나뿐이다 — **어느 파이썬으로 run.py를 부를지 고르는 것.**
# 그건 run.py가 자기 자신에 대해 할 수 없는 판단이라 셸에 남길 수밖에 없다.
#
# 맥에 기본으로 깔린 `python3`는 Xcode 명령줄 도구가 주는 3.9다(2026-09 기준).
# 그걸 그대로 부르면 run.py가 "3.11 이상이 필요하다"만 찍고 멈춘다 — 정작 홈브루로
# 깐 3.13이 옆에 있는데도 그렇다. 그래서 이름이 붙은 것부터 훑는다.
#
# 3.11이라는 숫자의 정본은 run.py의 MIN_PYTHON이다. 여기 것은 후보를 고르는 데만 쓰고,
# 최종 판정은 run.py가 한다 — 못 찾으면 아래에서 그냥 넘겨 안내를 띄우게 한다.
#
# 더블클릭으로 열린 터미널의 작업 디렉터리는 홈이다. 그래서 cd가 필요하다.
# 경로에 공백과 한글이 있으므로 반드시 따옴표로 감싼다.

cd "$(dirname "$0")" || exit 1

for candidate in python3.14 python3.13 python3.12 python3.11 python3 python; do
    command -v "$candidate" >/dev/null 2>&1 || continue
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' \
        >/dev/null 2>&1; then
        exec "$candidate" run.py "$@"
    fi
done

# 쓸 만한 게 하나도 없다. 안내는 run.py가 한다 — 메시지를 두 곳에 두지 않는다.
command -v python3 >/dev/null 2>&1 && exec python3 run.py "$@"
command -v python >/dev/null 2>&1 && exec python run.py "$@"

echo "파이썬을 찾지 못했다. python.org에서 3.11 이상을 설치한 뒤 다시 실행한다."
exit 1
