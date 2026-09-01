"""`POST /resumes`의 계약 시험 — **이력서를 넣는 길이 채점 경로와 이어졌는지만 본다.**

평가자가 자기 지원자들을 넣어 돌려 보려면 `data/resumes/` 밑에 파일을 손으로 복사해야
했다. 그 길을 뚫는 엔드포인트이므로, 여기서 확인하는 것은 「점수가 맞나」가 아니라
**「넣은 것이 채점이 읽는 그 자리에 그 모양으로 들어갔나」**다.

고른 케이스와 **깨지면 무엇이 거짓말이 되는가**:

- 여러 명을 한 번에 넣으면 `load_resumes()`가 읽는다 —
  **채점 경로가 부르는 그 함수다.** 스키마만 흉내 내면 런타임에서 죽는다
- 저장된 파일에 원문이 그대로 있다 —
  미리 가리면 `Evidence.span`을 대조할 원문이 사라져 검산 G2가 헛돈다
- `GET /resume`이 가려서 주고 **길이가 같다** —
  이중 마스킹이 아니라는 것. 길이가 바뀌면 근거 오프셋이 전부 어긋난다
- 넷 중 셋이 실패하면 **한 통도 저장되지 않는다** —
  「되는 것만 저장」이면 남은 인원으로 채점이 돌고 순위가 그대로 뜬다
- 실패한 통이 **사유와 함께 전부** 응답에 있다 — 조용히 빠지면 무엇을 고칠지 알 수 없다
- 공고가 없으면 404 · 중복이면 409 —
  이력서만 있으면 채점 대상이 없다 · 덮어쓰면 먼저 올린 것이 사라진다
- 잘못된 식별자면 400이고 **디렉터리 밖에 쓰지 않는다** — 경로 조각이 새면 엉뚱한 자리에 쓴다
- 너무 짧은 본문은 막되 **섹션이 없는 통짜 텍스트는 통과한다** —
  붙여넣기 실패가 채점 결과의 모습으로 나오면 안 되고, 양식을 강제하면 일반화가 깨진다

**심사위원도 OpenAI도 부르지 않는다.** 이 엔드포인트는 채점을 일으키지 않는다.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from matching.api import server, service
from matching.config import Settings

POSTING_ID = "test-posting"

# 이력서 본문. **직군·기술명을 넣지 않는다** — 이 테스트가 재는 것은 저장과 읽기이고,
# 픽스처에 직군 어휘를 박으면 「직군 무관 일반화」를 검사하는 쪽과 어긋난다.
# 대신 **마스킹할 것**은 반드시 넣는다 (이름·성별·생년월일·나이·학교명) — 없으면
# 「가려서 준다」는 검사가 통과해도 아무 말을 하지 않는다.
TEXT_A = (
    "성명: 강유리 (여 / 1999.04.11 / 만 26세)\n"
    "연락처: 010-0000-0001 / yuri.kang@example.com\n"
    "한국대학교 산업공학과 졸업 (2018.03 ~ 2022.02)\n"
    "맡은 일의 진행 상황을 매주 정리해 팀에 공유했습니다.\n"
)
TEXT_B = (
    "성명: 노지환 (남 / 2000.12.05 / 만 25세)\n"
    "해울대학교 통계학과 졸업 (2019.03 ~ 2023.02)\n"
    "지연이 생긴 원인을 기록으로 남겨 절차를 한 단계 늘리자고 제안했습니다.\n"
)


def _settings(**overrides) -> Settings:
    base = {"judge_model": "fixed-model-2026-09-01", "max_total_calls": 200}
    base.update(overrides)
    return Settings(**base)


def _client(tmp_path: Path, *, seed_posting: bool = True) -> TestClient:
    """앱 한 벌. **심사위원 자리에 `None`을 넣는다** — 이 경로는 LLM을 부르지 않는다.

    공고 디렉터리는 **비워 둔 채로** 만든다. `POST /resumes`가 보는 것은 「디렉터리가
    있는가」뿐이고, `ocr.json`까지 깔면 이 테스트가 파싱에 매인다.
    """
    if seed_posting:
        (tmp_path / service.POSTINGS_SUBDIR / POSTING_ID).mkdir(parents=True, exist_ok=True)
    app = server.create_app(_settings(), data_dir=tmp_path, client=None)
    return TestClient(app)


def _resume_dir(tmp_path: Path) -> Path:
    return tmp_path / service.RESUMES_SUBDIR / POSTING_ID


def _payload(*entries: dict) -> dict:
    return {"posting_id": POSTING_ID, "resumes": list(entries)}


# --- 넣은 것이 채점이 읽는 자리에 들어간다 -------------------------------------


def test_여러_명을_한_번에_넣으면_채점이_읽는_함수가_읽는다(tmp_path):
    """**6명을 6번 올리게 하지 않는다**가 이 엔드포인트의 존재 이유다.

    그리고 「저장됐다」의 기준을 파일 존재가 아니라 `service.load_resumes()`로 잡았다 —
    그게 `POST /score`가 실제로 부르는 함수다. 스키마를 흉내만 내면 여기서 죽는다.
    """
    client = _client(tmp_path)

    response = client.post(
        "/resumes",
        json=_payload(
            {"candidate_id": "A-01", "text": TEXT_A, "intended_type": "perfect"},
            {"candidate_id": "A-02", "text": TEXT_B},
        ),
    )
    assert response.status_code == 201  # 만들었다 — `POST /postings`와 같은 코드다
    body = response.json()
    assert body["saved"] == ["A-01", "A-02"]  # 준 순서를 지킨다
    assert body["failed"] == []

    loaded = service.load_resumes(_resume_dir(tmp_path))
    assert [item.candidate_id for item in loaded] == ["A-01", "A-02"]
    assert loaded[0].text == TEXT_A

    # 기존 데이터셋과 같은 필드로 남는다 (`data/resumes/*/A-*.json`).
    stored = json.loads((_resume_dir(tmp_path) / "A-01.json").read_text(encoding="utf-8"))
    assert set(stored) == {
        "candidate_id",
        "intended_type",
        "design_note",
        "format_ref",
        "read_fields",
        "text",
    }
    # 라벨은 준 것만 쓰고, 안 준 것은 지어내지 않는다.
    assert stored["intended_type"] == "perfect"
    second = json.loads((_resume_dir(tmp_path) / "A-02.json").read_text(encoding="utf-8"))
    assert second["intended_type"] == "unlabeled"
    # 독립성 신고를 우리가 대신 하지 않는다 — 업로드본에는 그 절차가 없다.
    assert stored["read_fields"] == []


def test_저장은_원문_그대로이고_읽을_때_가려서_나온다(tmp_path):
    """**이 테스트가 이 작업에서 제일 틀리기 쉬운 곳을 지킨다.**

    저장 시점에 가리면 두 가지가 깨진다. (a) `Evidence.span`이 가리키는 자리를 원문에서
    대조할 수 없어 검산 G2가 우리가 만든 문자열을 우리가 검사하는 꼴이 된다.
    (b) 마스킹은 `■`를 다시 잡지 않으므로 두 번째 마스킹은 **더 가리는 것이 아니라
    근거를 잃는 것**이다 (`scorer/mask.py`).

    그래서 세 방향을 함께 본다 — 디스크에는 원문이 있고, 응답에는 없고, **길이가 같다.**
    """
    client = _client(tmp_path)
    first = client.post("/resumes", json=_payload({"candidate_id": "A-01", "text": TEXT_A}))
    assert first.status_code == 201

    stored = json.loads((_resume_dir(tmp_path) / "A-01.json").read_text(encoding="utf-8"))
    assert stored["text"] == TEXT_A
    assert "강유리" in stored["text"] and "■" not in stored["text"]

    body = client.get("/resume/A-01").json()
    assert "강유리" not in body["text"] and "한국대학교" not in body["text"]
    # 길이 보존. 이게 깨지면 근거 하이라이트가 전부 엉뚱한 자리를 가리킨다.
    assert body["length"] == len(body["text"]) == len(TEXT_A)
    # 경력 기간(`2018.03`)은 남는다 — 가리면 연차를 못 센다.
    assert "2018.03" in body["text"]
    assert "이름" in body["masked_fields"] and "학교명" in body["masked_fields"]
    # 오프셋이 원문과 같은 자리를 가리킨다 — 이중 마스킹이면 여기서 어긋난다.
    offset = TEXT_A.index("맡은 일의")
    assert body["text"][offset : offset + 5] == "맡은 일의"[:5]


# --- 하나라도 실패하면 아무것도 저장하지 않는다 ---------------------------------


def test_하나라도_실패하면_한_통도_저장되지_않고_사유가_전부_나온다(tmp_path):
    """「되는 것만 저장」을 고르지 않은 이유를 그대로 시험한다.

    랭킹은 **비교**다. 6명을 올렸는데 3명만 들어가면 채점은 성공하고 화면에는 「3명 중
    1위」가 뜬다 — 빠진 셋은 어디에도 안 남는다. 그래서 전부 물린다.

    그 값은 **첫 실패에서 멈추지 않는 것**으로 갚는다. 사유가 하나씩 나오면 왕복이
    실패한 통 수만큼 늘어난다. 여기서 실패 세 통의 사유가 **서로 다르다**는 것이 요점이다.
    """
    client = _client(tmp_path)
    # 미리 한 통을 넣어 둔다 — 중복 사유를 만들기 위해서다.
    first = client.post("/resumes", json=_payload({"candidate_id": "A-01", "text": TEXT_A}))
    assert first.status_code == 201

    response = client.post(
        "/resumes",
        json=_payload(
            {"candidate_id": "A-02", "text": TEXT_B},  # 멀쩡한 통
            {"candidate_id": "A-01", "text": TEXT_B},  # 이미 있다
            {"candidate_id": "A-03", "text": "   \n"},  # 본문이 비었다
            {"candidate_id": "A-04", "text": "가" * (server.MAX_RESUME_CHARS + 1)},  # 너무 길다
        ),
    )
    # 중복이 섞였으므로 409다 — 사용자가 고칠 방법이 다른 유일한 사유다.
    assert response.status_code == 409
    body = response.json()
    assert body["saved"] == []
    reasons = {item["candidate_id"]: item["reason"] for item in body["failed"]}
    assert set(reasons) == {"A-01", "A-03", "A-04"}  # 실패한 셋이 전부 있다
    assert "이미 있다" in reasons["A-01"]
    assert reasons["A-03"] != reasons["A-04"]  # 사유가 뭉개지지 않는다

    # **멀쩡했던 A-02도 저장되지 않았다.** 이 줄이 이 테스트의 전부다.
    assert not (_resume_dir(tmp_path) / "A-02.json").exists()
    assert sorted(path.name for path in _resume_dir(tmp_path).glob("*.json")) == ["A-01.json"]


def test_같은_요청_안에_같은_식별자가_둘이면_거부한다(tmp_path):
    """조용히 마지막 것으로 덮어쓰면 **올린 사람이 모르는 사이에 한 명이 사라진다.**

    어느 쪽을 남길지는 우리가 정할 문제가 아니다.
    """
    client = _client(tmp_path)
    response = client.post(
        "/resumes",
        json=_payload(
            {"candidate_id": "A-01", "text": TEXT_A},
            {"candidate_id": "A-01", "text": TEXT_B},
        ),
    )
    assert response.status_code == 400
    assert response.json()["failed"][0]["candidate_id"] == "A-01"
    assert not (_resume_dir(tmp_path) / "A-01.json").exists()


def test_중복이면_409이고_이미_있는_것을_덮어쓰지_않는다(tmp_path):
    """덮어쓰기를 허용하면 같은 식별자를 두 번 올린 순간 **먼저 올린 이력서가 사라진다.**

    지운 뒤 다시 올리는 것은 사용자가 결정할 일이고, 그 결정을 우리가 대신하지 않는다.
    """
    client = _client(tmp_path)
    first = client.post("/resumes", json=_payload({"candidate_id": "A-01", "text": TEXT_A}))
    assert first.status_code == 201

    again = client.post("/resumes", json=_payload({"candidate_id": "A-01", "text": TEXT_B}))
    assert again.status_code == 409

    stored = json.loads((_resume_dir(tmp_path) / "A-01.json").read_text(encoding="utf-8"))
    assert stored["text"] == TEXT_A  # 먼저 올린 것이 그대로 남아 있다


# --- 넣을 자리가 성립하는가 ----------------------------------------------------


def test_공고가_없으면_404이고_이력서를_만들지_않는다(tmp_path):
    """**이력서만 떠 있으면 채점할 대상이 없다.** 넣을 자리부터 있어야 한다."""
    client = _client(tmp_path, seed_posting=False)
    response = client.post("/resumes", json=_payload({"candidate_id": "A-01", "text": TEXT_A}))

    assert response.status_code == 404
    assert POSTING_ID in response.json()["detail"]
    assert not _resume_dir(tmp_path).exists()


def test_경로_조각이_되는_식별자를_막고_디렉터리_밖에_쓰지_않는다(tmp_path):
    """식별자가 그대로 파일 경로가 된다. 상위 참조가 새면 **엉뚱한 자리에 파일을 만든다.**

    공고 식별자와 지원자 식별자 **양쪽**을 본다 — 한쪽만 막으면 다른 쪽이 우회로다.
    """
    client = _client(tmp_path)

    inner = client.post("/resumes", json=_payload({"candidate_id": "../바깥", "text": TEXT_A}))
    assert inner.status_code == 400
    assert inner.json()["failed"][0]["candidate_id"] == "../바깥"

    outer = client.post(
        "/resumes",
        json={"posting_id": "../바깥", "resumes": [{"candidate_id": "A-01", "text": TEXT_A}]},
    )
    assert outer.status_code == 400

    # tmp_path 밖에도, 공고 디렉터리 밖에도 새 파일이 없다.
    assert not (tmp_path.parent / "바깥").exists()
    assert not list(tmp_path.rglob("바깥*"))


def test_한_번에_받는_인원에_상한이_있다(tmp_path):
    """이력서 전문은 판단 항목마다 심사위원 프롬프트에 통째로 실린다 — 인원이 곧 비용이다.

    **상한값 자체는 임의값이다.** 여기서 재는 것은 「상한이 실제로 작동하는가」뿐이고,
    상한이 없으면 요청 하나가 남은 예산을 통째로 먹을 수 있다.
    """
    client = _client(tmp_path)
    many = [
        {"candidate_id": f"A-{index:03d}", "text": TEXT_B}
        for index in range(server.MAX_RESUMES_PER_REQUEST + 1)
    ]
    response = client.post("/resumes", json={"posting_id": POSTING_ID, "resumes": many})

    assert response.status_code == 400
    assert not _resume_dir(tmp_path).exists()

    empty = client.post("/resumes", json={"posting_id": POSTING_ID, "resumes": []})
    assert empty.status_code == 400


def test_키처럼_보이는_문자열이_있으면_저장하지_않는다(tmp_path):
    """저장은 되는데 **읽을 수 없는** 이력서를 만들지 않는다.

    `GET /resume`은 키처럼 생긴 문자열이 있으면 거부한다 — 지우면 길이가 바뀌어 근거
    오프셋이 어긋나고, 그대로 내보내면 키가 나가기 때문이다. 그 사실을 저장 시점에
    알려 주지 않으면 사용자는 채점 화면에서야 막힌다.
    """
    client = _client(tmp_path)
    # 키 문자열을 코드에 적지 않는다 — 「키가 코드에 있나」를 grep하는 쪽이 걸린다.
    token_shaped = "sk" + "-" + "abcdefghijklmnop"
    response = client.post(
        "/resumes",
        json=_payload({"candidate_id": "A-01", "text": f"{TEXT_A}발급받은 값: {token_shaped}\n"}),
    )

    assert response.status_code == 400
    assert "키" in response.json()["failed"][0]["reason"]
    assert not (_resume_dir(tmp_path) / "A-01.json").exists()


def test_너무_짧은_본문은_막되_양식은_보지_않는다(tmp_path):
    """하한을 두는 이유는 **품질이 아니라 붙여넣기 실패**다. 몇 글자만 들어와도 채점은
    그대로 돌아 심사위원 호출을 쓰고 「0점」이 멀쩡한 결과처럼 랭킹에 오른다.

    **동시에 이 테스트는 반대 방향도 잰다** — 섹션이 하나도 없는 통짜 텍스트가 통과해야
    한다. 양식을 검사하면 다른 서식의 이력서가 거부되어 일반화가 깨지고, 목업 12명조차
    섹션명이 서로 달라 **기존 데이터가 자기 검증을 통과하지 못한다.**
    """
    client = _client(tmp_path)

    short = client.post("/resumes", json=_payload({"candidate_id": "A-01", "text": "짧다"}))
    assert short.status_code == 400
    assert str(server.MIN_RESUME_CHARS) in short.json()["failed"][0]["reason"]
    assert not (_resume_dir(tmp_path) / "A-01.json").exists()

    # 머리글도 항목 표시도 없는 한 문단. 하한만 넘으면 들어간다.
    flat = "저는 맡은 일의 기록을 남기고 어긋난 지점을 찾아 절차를 고치는 일을 해 왔습니다. " * 2
    assert len(flat.strip()) >= server.MIN_RESUME_CHARS
    ok = client.post("/resumes", json=_payload({"candidate_id": "A-02", "text": flat}))
    assert ok.status_code == 201
    assert service.load_resumes(_resume_dir(tmp_path))[0].text == flat


def test_쓰기_도중_막히면_앞서_쓴_것을_되감는다(tmp_path, monkeypatch):
    """존재 검사와 쓰기 **사이**에 다른 요청이 끼어들 수 있다. 검사만 믿으면 그 틈에
    남의 이력서를 덮어쓰므로 `"x"` 모드로 쓴다 — 그 틈을 막는 것이 첫째다.

    둘째가 이 테스트의 대상이다. 막힌 뒤 되감지 않으면 앞의 몇 통만 남아 「전부 아니면
    아무것도」가 **바로 여기서만** 깨지고, 응답은 실패라 사용자는 남은 통을 볼 이유가 없다.
    두 번째 통에서 막히게 해 첫 통이 사라지는지 본다.
    """
    client = _client(tmp_path)
    real_open = Path.open

    def blocked(self: Path, *args, **kwargs):
        if self.name == "A-02.json":
            raise FileExistsError(17, "다른 요청이 먼저 만들었다")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", blocked)
    response = client.post(
        "/resumes",
        json=_payload(
            {"candidate_id": "A-01", "text": TEXT_A},
            {"candidate_id": "A-02", "text": TEXT_B},
        ),
    )
    monkeypatch.undo()

    assert response.status_code == 409
    assert response.json()["saved"] == []
    # A-01은 실제로 디스크에 쓰였다가 되감겼다.
    assert list(_resume_dir(tmp_path).glob("*.json")) == []
