"""`POST /postings` — 평가자가 **자기 공고 그림으로** 돌려보는 입구.

여기서 재는 것은 「파일이 저장되나」가 아니다. 저장은 쉽고, 틀리면 시끄럽게 틀린다.
**조용히 틀리는 자리 넷**을 골랐다.

**정상 업로드가 기존 스키마 그대로 만든다** — 스키마가 갈리면 `read_provenance()`가
못 읽고, 업로드본만 파이프라인 밖에 남는다.

**있는 공고는 409이고 파일이 그대로다** — 그림만 갈아 끼우면 커밋된 좌표가 엉뚱한
자리를 가리킨다. 틀린 근거를 그럴듯하게 보여주는 쪽이 빈 화면보다 나쁘다.

**PNG인 척하는 파일은 거부되고 자리도 안 남는다** — 확장자만 보면 아무 파일이나
들어온다. 게다가 반쪽 디렉터리가 남으면 다음 시도가 409로 막힌다.

**못 쓸 `posting_id`는 400** — 경로 조각으로 쓰이는 값이다. `../`가 지나가면 남의
디렉터리에 쓴다.

**JPEG가 정말 PNG로 저장된다** — `IMAGE_GLOB`이 `img_*.png` 하나뿐이다. 이름만
`.png`면 파일명이 거짓말이고, `.jpg`면 `.gitignore`를 빠져나가 공고 본문이 커밋된다.

**올린 공고가 `GET /postings`에 뜬다** — `source_kind`를 잘못 고르면 여기서 터진다.
「local을 골랐다」가 코드에서 확인되는 자리다.

**직군 어휘를 픽스처에 넣지 않는다.** 이 시험에 나오는 그림은 아무 뜻 없는 색면이고,
직무명은 「대상 직무 라벨」이라는 자리를 확인하는 데만 쓴다.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from matching.api import server, upload
from matching.config import Settings
from matching.source import PROVENANCE_FILENAME, image_paths, read_provenance

POSTING_ID = "uploaded-posting"

# `tests/test_provenance.py::test_provenance_file_leaks_nothing`이 못 박은 키 집합.
# **여기서 다시 적는 것이 요점이다** — 업로드 경로가 그 파일과 같은 스키마를 만드는지가
# 시험 대상이고, 한쪽에서 import해 오면 둘이 함께 틀려도 통과한다.
PROVENANCE_KEYS = {
    "posting_id",
    "source_kind",
    "acquired_at",
    "target_position",
    "image_sha256",
    "image_size",
    "ocr_engine",
    "ocr_sha256",
    "api_verified",
    "api_verified_at",
}


def _png(width: int = 40, height: int = 30, color: str = "white") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _jpeg(width: int = 40, height: int = 30) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="JPEG")
    return buffer.getvalue()


def _client(tmp_path: Path) -> TestClient:
    # 키 없이 만든다. 이 엔드포인트는 LLM도 OCR도 부르지 않는다 — 부르면 시험이 멈춘다.
    app = server.create_app(Settings(), data_dir=tmp_path, client=None)
    return TestClient(app, raise_server_exceptions=False)


def _upload(client: TestClient, posting_id: str, images: list[tuple[str, bytes]], **data):
    files = [("files", (name, body, "image/png")) for name, body in images]
    return client.post("/postings", data={"posting_id": posting_id, **data}, files=files)


# --- 1. 정상 업로드 ---------------------------------------------------------


def test_업로드가_기존_스키마대로_공고_자리를_만든다(tmp_path):
    """`prepare_posting()`이 그대로 받아 갈 수 있는 디렉터리여야 한다.

    확인하는 것은 셋이다 — 파일명 규칙(`IMAGE_GLOB`) · `provenance.json`의 키 집합과
    해시 · `image_source.json`의 빈 `image_url`.
    """
    client = _client(tmp_path)
    first, second = _png(color="white"), _png(color="black")

    response = _upload(
        client, POSTING_ID, [("a.png", first), ("b.png", second)], position="대상 직무"
    )
    assert response.status_code == 201

    body = response.json()
    assert body == {
        "posting_id": POSTING_ID,
        "source_kind": "local",
        "target_position": "대상 직무",
        "image_count": 2,
        "api_verified": False,
    }

    directory = tmp_path / "postings" / POSTING_ID
    # 파일명 규칙은 `source/base.py`의 `IMAGE_GLOB`이 정본이다. 그 함수로 찾아본다 —
    # 여기서 이름을 손으로 적으면 규칙이 바뀌어도 이 시험은 통과한다.
    assert [path.name for path in image_paths(directory)] == ["img_1.png", "img_2.png"]
    assert (directory / "img_1.png").read_bytes() == first

    raw = json.loads((directory / PROVENANCE_FILENAME).read_text(encoding="utf-8"))
    assert set(raw) == PROVENANCE_KEYS
    # 출처 증거에 원문도 URL도 들어가지 않는다 (`test_provenance.py`와 같은 규칙).
    assert "http" not in (directory / PROVENANCE_FILENAME).read_text(encoding="utf-8")

    provenance = read_provenance(directory)
    assert provenance.image_sha256 == [
        hashlib.sha256(first).hexdigest(),
        hashlib.sha256(second).hexdigest(),
    ]
    assert provenance.image_size == [(40, 30), (40, 30)]
    # 아직 파싱 전이다. 「어느 OCR 결과에서 나왔나」는 `POST /prepare`가 채운다.
    assert provenance.ocr_engine is None and provenance.ocr_sha256 is None

    source = json.loads((directory / upload.IMAGE_SOURCE_FILENAME).read_text(encoding="utf-8"))
    assert set(source) == {"posting_id", "note", "image_url"}
    # **빈 배열이 계약이다.** 업로드본은 받아올 곳이 없고, 없는데 있는 척하면
    # `run.py`의 `fetch_images()`가 엉뚱한 데서 받아 해시 대조에 실패한다.
    assert source["image_url"] == []
    assert "받아올 곳이 없다" in source["note"]


def test_prepare가_받아_갈_수_있는_디렉터리다(tmp_path):
    """`prepare_posting()`이 제일 먼저 하는 일이 `load_posting_ref()`다.

    OCR·LLM은 그 뒤라 여기서 부르지 않는다(부르면 몇 분이 걸리고 돈이 나간다). 대신
    **이어받는 지점까지** 확인한다 — 여기까지 통과하면 남은 것은 기존 경로다.
    """
    from matching.api.service import load_posting_ref

    client = _client(tmp_path)
    assert _upload(client, POSTING_ID, [("a.png", _png())], position="대상 직무").status_code == 201

    ref = load_posting_ref(tmp_path / "postings" / POSTING_ID, Settings(), source="local")
    assert ref.posting_id == POSTING_ID
    assert [path.name for path in ref.image_paths] == ["img_1.png"]
    # 출처는 플래그가 아니라 `provenance.json`이 정한다 (`load_posting_ref` 마지막 줄).
    assert ref.source_kind == "local"


def test_올린_공고가_목록에_뜬다(tmp_path):
    """`source_kind` 선택이 기존 분기를 깨지 않는가 — 그게 이 시험의 전부다.

    `GET /postings`는 `LocalSource.list_postings()` + `read_provenance()`를 지난다.
    값을 잘못 고르면 pydantic이 여기서 막는다(`SourceKind`는 Literal 셋이다).
    화면의 「데모 데이터」 배지도 이 값에서 나온다.
    """
    client = _client(tmp_path)
    assert _upload(client, POSTING_ID, [("a.png", _png())]).status_code == 201

    listed = client.get("/postings")
    assert listed.status_code == 200
    rows = {row["posting_id"]: row for row in listed.json()}
    assert rows[POSTING_ID]["source_kind"] == "local"
    assert rows[POSTING_ID]["image_count"] == 1


# --- 2. 덮어쓰기 금지 -------------------------------------------------------


def test_이미_있는_공고는_409이고_그림이_그대로다(tmp_path):
    """**이 기능의 핵심 안전장치.**

    409만 확인하면 부족하다. 「거절했다고 말하면서 파일은 바꿨다」가 정확히 우리가
    무서워하는 실패이므로, **바이트가 그대로인지**까지 본다.
    """
    client = _client(tmp_path)
    original = _png(color="white")
    assert _upload(client, POSTING_ID, [("a.png", original)]).status_code == 201

    directory = tmp_path / "postings" / POSTING_ID
    before = (directory / PROVENANCE_FILENAME).read_bytes()

    second = _upload(client, POSTING_ID, [("b.png", _png(width=80, height=90, color="black"))])
    assert second.status_code == 409
    assert "덮어쓰지 않는다" in second.json()["detail"]

    assert (directory / "img_1.png").read_bytes() == original
    assert not (directory / "img_2.png").exists()
    assert (directory / PROVENANCE_FILENAME).read_bytes() == before


def test_그림이_아직_없는_공고_자리도_덮지_않는다(tmp_path):
    """clone 직후의 상태다 — `provenance.json`·`requirements.json`은 커밋돼 있고
    `img_*.png`는 `.gitignore`라 없다.

    「그림이 없으니 비어 있다」로 보고 받아 주면, 커밋된 좌표 밑의 그림이 **남의 그림**
    으로 바뀐다. 여기서 막지 않으면 덮어쓰기 금지가 clone한 사람에게는 없는 것과 같다.
    """
    client = _client(tmp_path)
    directory = tmp_path / "postings" / POSTING_ID
    directory.mkdir(parents=True)
    (directory / "requirements.json").write_text("{}", encoding="utf-8")

    response = _upload(client, POSTING_ID, [("a.png", _png())])
    assert response.status_code == 409
    assert not image_paths(directory)


# --- 3. 확장자를 믿지 않는다 -------------------------------------------------


def test_png인_척하는_파일은_거부되고_자리도_안_남는다(tmp_path):
    """머리 바이트 검사가 실제로 도는가. 그리고 **반쪽 디렉터리를 남기지 않는가.**

    두 장을 보내고 **뒤엣것**만 가짜로 만든다. 한 장씩 쓰면서 검사하는 구현이면 첫 장이
    남고, 그 디렉터리가 다음 시도를 409로 막아 사용자가 영영 못 올리게 된다.
    """
    client = _client(tmp_path)

    response = _upload(
        client,
        POSTING_ID,
        [("real.png", _png()), ("fake.png", "PNG가 아니다. 그냥 글자다.".encode())],
    )
    assert response.status_code == 400
    assert "PNG도 JPEG도 아니다" in response.json()["detail"]
    assert not (tmp_path / "postings" / POSTING_ID).exists()


def test_머리_바이트만_맞춘_파일도_거부한다(tmp_path):
    """1겹만으로는 부족하다. 앞 8바이트를 PNG로 맞춰 놓은 파일은 머리 검사를 지난다.

    여기서 안 잡히면 `write_provenance()`가 PIL로 크기를 재다가 죽고, 그때는 이미
    디렉터리가 만들어진 뒤다 — 500과 함께 반쪽 자리가 남는다.
    """
    client = _client(tmp_path)

    response = _upload(client, POSTING_ID, [("head.png", b"\x89PNG\r\n\x1a\n" + b"0" * 256)])
    assert response.status_code == 400
    assert "그림으로 읽을 수 없다" in response.json()["detail"]
    assert not (tmp_path / "postings" / POSTING_ID).exists()


def test_jpeg는_정말_png로_바뀌어_저장된다(tmp_path):
    """이름만 `.png`로 두면 파일명이 거짓말을 하고, `.jpg`로 두면 두 가지가 한꺼번에
    깨진다 — `image_paths()`가 못 찾고(`IMAGE_GLOB`), `.gitignore`가 못 막아
    **공고 본문이 커밋된다.**
    """
    client = _client(tmp_path)

    response = _upload(client, POSTING_ID, [("photo.jpg", _jpeg())])
    assert response.status_code == 201

    stored = (tmp_path / "postings" / POSTING_ID / "img_1.png").read_bytes()
    assert stored.startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(io.BytesIO(stored)) as image:
        assert image.format == "PNG"
        assert image.size == (40, 30)  # 픽셀이 그대로여야 좌표도 그대로다


# --- 4. 못 쓸 입력 ----------------------------------------------------------


@pytest.mark.parametrize("bad", ["../evil", "a/b", "..", ".", "back\\slash"])
def test_경로가_되는_posting_id는_400이다(tmp_path, bad):
    """`_safe_id()`를 재사용한다 — 검증 함수가 둘이면 언젠가 한쪽만 고쳐진다."""
    client = _client(tmp_path)

    response = _upload(client, bad, [("a.png", _png())])
    assert response.status_code == 400
    # 상위 디렉터리로 새어 나가지 않았다. tmp_path **바깥**은 볼 수 없으므로,
    # 여기서 확인할 수 있는 것은 「어디에도 안 생겼다」다.
    assert not list(tmp_path.rglob("img_*.png"))


def test_빈_posting_id로는_아무것도_안_생긴다(tmp_path):
    """빈 값만 400이 아니라 422다. **우리 코드에 닿기 전에 걸리기 때문**이다 —
    multipart에서 빈 문자열 칸은 칸 자체가 없는 것으로 전달돼 FastAPI의 필수 검사에
    먼저 잡힌다. `_safe_id()`도 같은 값을 거부하므로 막는 자리가 둘이고, 여기서 재는
    것은 상태 코드가 아니라 **아무것도 안 생긴다**는 쪽이다.
    """
    client = _client(tmp_path)

    response = _upload(client, "", [("a.png", _png())])
    assert response.status_code in {400, 422}
    assert not list(tmp_path.rglob("img_*.png"))


def test_이미지가_없으면_400이다(tmp_path):
    """파일 없이 온 요청. 조용히 빈 공고 자리를 만들지 않는다."""
    client = _client(tmp_path)

    response = client.post("/postings", data={"posting_id": POSTING_ID}, files=[])
    assert response.status_code in {400, 422}
    assert not (tmp_path / "postings" / POSTING_ID).exists()


def test_빈_파일을_조용히_건너뛰지_않는다(tmp_path):
    """브라우저가 빈 칸을 보내는 일이 있다. 건너뛰면 세 장을 올렸는데 두 장이 저장되고
    **그 사실이 아무 데도 안 남는다.**
    """
    client = _client(tmp_path)

    response = _upload(client, POSTING_ID, [("a.png", _png()), ("empty.png", b"")])
    assert response.status_code == 400
    assert "비어 있다" in response.json()["detail"]
    assert not (tmp_path / "postings" / POSTING_ID).exists()


def test_장당_상한을_넘으면_400이다(tmp_path, monkeypatch):
    """상한 **값**을 시험하지 않는다 — 20 MB짜리를 만들어 보내는 것은 이 시험이 재려는
    것과 무관하게 느리다. 재는 것은 **상한이 걸리는가**뿐이다.
    """
    client = _client(tmp_path)
    monkeypatch.setattr(upload, "MAX_IMAGE_BYTES", len(_png()) - 1)

    response = _upload(client, POSTING_ID, [("a.png", _png())])
    assert response.status_code == 400
    assert "한 장 상한" in response.json()["detail"]
    assert not (tmp_path / "postings" / POSTING_ID).exists()


def test_합계_상한을_넘으면_400이다(tmp_path, monkeypatch):
    """장당 상한만으로는 못 막는 쪽. 한 장씩은 작은데 **여러 장이 모여** 넘는다."""
    client = _client(tmp_path)
    monkeypatch.setattr(upload, "MAX_TOTAL_BYTES", len(_png()) + 1)

    response = _upload(client, POSTING_ID, [("a.png", _png()), ("b.png", _png())])
    assert response.status_code == 400
    assert "합계 상한" in response.json()["detail"]
    assert not (tmp_path / "postings" / POSTING_ID).exists()


def test_장수_상한을_넘으면_400이다(tmp_path):
    """한 자리 수인 데는 이유가 있다 — `image_paths()`의 정렬이 문자열 순서라
    `img_10.png`이 `img_2.png` 앞에 온다. 쪽 순서가 뒤집히면 `BBox.page`가 다른 쪽을
    가리킨다. 그래서 상한이 곧 **좌표가 틀리지 않는 범위**다.
    """
    client = _client(tmp_path)
    images = [(f"{index}.png", _png()) for index in range(upload.MAX_IMAGE_COUNT + 1)]

    response = _upload(client, POSTING_ID, images)
    assert response.status_code == 400
    assert not (tmp_path / "postings" / POSTING_ID).exists()
    # 상한 자체가 두 자리로 늘어나면 위의 이유가 무너진다. 그 사실을 여기 박아 둔다.
    assert upload.MAX_IMAGE_COUNT < 10


# --- 5. HTTP를 모르는 채로도 같은 규칙이 돈다 --------------------------------


def test_저장_함수가_HTTP를_모른다(tmp_path):
    """검사가 라우트가 아니라 `upload.py`에 있다는 것을 코드로 확인한다.

    라우트에 검사가 섞이면 「서버를 띄워야만 확인되는 규칙」이 되고, 그러면 CLI로 같은
    일을 하게 될 때 검사가 통째로 빠진다 (`service.py`가 argparse도 HTTP도 들이지 않는
    것과 같은 이유).
    """
    source = Path(upload.__file__).read_text(encoding="utf-8")
    assert "fastapi" not in source

    provenance = upload.store_posting_images(
        tmp_path, POSTING_ID, [("a.png", io.BytesIO(_png()))], position="  대상 직무  "
    )
    assert provenance.source_kind == "local"
    # 앞뒤 공백은 지운다 — 화면에 그대로 실리고 파서가 이 값으로 y 구간을 찾는다.
    assert provenance.target_position == "대상 직무"

    with pytest.raises(upload.UploadConflict):
        upload.store_posting_images(tmp_path, POSTING_ID, [("a.png", io.BytesIO(_png()))])
