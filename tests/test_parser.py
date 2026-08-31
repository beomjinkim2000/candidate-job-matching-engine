"""파서 테스트 — **실물 API도 실물 OCR도 부르지 않는다.**

픽스처는 **직접 지어낸 가상 공고**다. 실제 공고 문구를 붙여넣지 않는다 (과제 CRITICAL —
원문 복사·붙여넣기 금지). 그래서 이 파일의 「가직무」·「나직무」 같은 말은 아무 데도
없는 말이고, 그게 의도다.

## 고른 케이스와 근거

| 무엇 | 왜 이 케이스인가 |
|---|---|
| 판정 4가지가 **각각** 발동 | 규칙 하나가 다른 규칙을 가려도 전체는 초록이 된다. 갈라서 본다 |
| 이어지는 줄 병합 | 병합 안 하면 **조건 하나가 조건 둘로 세어져 배점이 갈라진다.** 점수 문제다 |
| `excluded` 블록 제외 | 복리후생의 조건이 지원자 요구조건으로 들어가는 실패. **조용히 틀린다** |
| 사다리 5단계 각각 | 등급 `E2`/`E1`/`E0`이 무엇을 뜻하는지가 여기서 정해진다 |
| 다른 직무의 항목 배제 | 한 공고 3직무 표. 셀 소속을 잃으면 **점수가 틀린다** |
| span·좌표 대조 | 「원문을 복붙하지 않았다」의 기계적 증명 (`tests/CLAUDE.md` 1차 지표) |
| 폴백 조건 감지 | 규칙이 안 맞는 공고에서 **조용히 조건 0건**이 나오는 것을 막는다 |
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from matching.config import Settings
from matching.model.objects import BBox
from matching.parser import ParseError, VlmFallbackRequired, parse_posting
from matching.parser.classify import ParsedItem, PostingContext, RequirementRecord, classify
from matching.parser.header_role import build_prompt
from matching.parser.layout import (
    PositionNotFound,
    build_blocks,
    classify_lines,
    select_lines,
    split_positions,
)
from matching.parser.ocr import OcrLine, OcrResult
from matching.parser.verify import verify
from matching.source.base import PostingRef
from matching.source.provenance import Provenance

IMG_W, IMG_H = 800, 2000


def line(line_id: str, text: str, x0: int, y: int, height: int = 20, width: int = 400) -> OcrLine:
    return OcrLine(
        id=line_id,
        text=text,
        conf=0.95,
        bbox=BBox(
            page=1, x1=x0, y1=y, x2=x0 + width, y2=y + height, img_w=IMG_W, img_h=IMG_H
        ),
        x0=x0,
        height=height,
    )


def result(lines: list[OcrLine]) -> OcrResult:
    return OcrResult(
        engine="paddle",
        engine_version="test",
        image_path="img_1.png",
        img_w=IMG_W,
        img_h=IMG_H,
        lines=lines,
        avg_conf=0.95,
        elapsed_sec=0.0,
    )


SETTINGS = Settings()


# --------------------------------------------------------------- 3-B. 역할 판정


def test_판정_네가지가_각각_발동한다():
    """규칙 하나씩 갈라서 본다. 뭉쳐서 보면 한 규칙이 죽어도 초록이 된다."""
    ocr = result(
        [
            line("L-001", "모집 조건", 40, 100),  # 불릿 없음·왼쪽 → header
            line("L-002", "· 첫째 조건이다", 60, 140),  # 불릿 → item
            line("L-003", "이어지는 설명이다", 90, 170),  # 직전 item보다 들여씀 → continuation
            line("L-004", "붙을 곳 없는 줄", 300, 400),  # 앞이 header라 붙을 항목이 없다
        ]
    )
    # L-004 앞에 header를 하나 더 둬서 「직전 item」을 끊는다.
    ocr.lines.insert(3, line("L-003b", "다른 섹션", 40, 300))

    roles = classify_lines(ocr, SETTINGS)
    assert roles["L-001"] == "header"
    assert roles["L-002"] == "item"
    assert roles["L-003"] == "continuation"
    assert roles["L-004"] == "ambiguous"


def test_x0가_같아도_불릿이_있으면_항목이다():
    """확보한 공고 2건이 정확히 이 모양이다 — 헤더와 불릿 항목의 x0가 사실상 같다.

    `step3.md`의 원래 순서(x 먼저)로는 **조건이 0건**이 된다.
    """
    ocr = result(
        [
            line("L-001", "모집 조건", 46, 100),
            line("L-002", "· 헤더와 같은 자리에서 시작하는 조건", 46, 140),
        ]
    )
    roles = classify_lines(ocr, SETTINGS)
    assert roles["L-001"] == "header"
    assert roles["L-002"] == "item", "불릿이 x 들여쓰기보다 강해야 한다"


def test_이어지는_줄이_임계값_아래여도_헤더가_되지_않는다():
    """한 조건의 뒷부분이 새 섹션 제목이 되면, 그 아래 항목이 역할 없는 블록에 갇힌다."""
    ocr = result(
        [
            line("L-001", "모집 조건", 46, 100),
            line("L-002", "· 조건의 앞부분", 50, 140),
            line("L-003", "조건의 뒷부분", 60, 170),  # x0=60 < 100 이지만 이어지는 줄이다
        ]
    )
    roles = classify_lines(ocr, SETTINGS)
    assert roles["L-003"] == "continuation"


def test_이어지는_줄이_병합되어_조건이_하나다():
    """병합하지 않으면 조건 하나가 **조건 둘**로 세어진다. 표시가 아니라 배점 문제다."""
    ocr = result(
        [
            line("L-001", "모집 조건", 40, 100),
            line("L-002", "· 두 줄에 걸친 조건의", 60, 140),
            line("L-003", "나머지 절반", 90, 170),
        ]
    )
    blocks = build_blocks(ocr, classify_lines(ocr, SETTINGS))
    assert len(blocks) == 1
    assert len(blocks[0].items) == 1, "이어지는 줄이 새 항목이 되면 안 된다"
    assert [ln.id for ln in blocks[0].items[0]] == ["L-002", "L-003"]


# --------------------------------------------------------------- 3-D. 사다리


def _item(text: str, header_role: str | None, emphasized: bool = False) -> ParsedItem:
    return ParsedItem(
        text=text,
        lines=[line("L-001", text, 60, 100)],
        header_role=header_role,
        emphasized=emphasized,
    )


def test_사다리_1단계_섹션_역할이_있으면_E2로_확정된다():
    kind, grade, step = classify(_item("아무 수식어도 없는 문구", "requirement"), PostingContext())
    assert (kind, grade, step) == ("required", "E2", 1)

    kind, grade, step = classify(_item("아무 수식어도 없는 문구", "preferred"), PostingContext())
    assert (kind, grade, step) == ("preferred", "E2", 1)


def test_사다리_2단계_수식어가_섹션_없이도_확정한다():
    kind, grade, step = classify(_item("이 항목은 필수다", None), PostingContext())
    assert (kind, grade, step) == ("required", "E2", 2)

    kind, grade, step = classify(_item("이 항목은 우대한다", None), PostingContext())
    assert (kind, grade, step) == ("preferred", "E2", 2)


def test_사다리_3단계_담당업무에_대응하면_필수쪽_E1이다():
    context = PostingContext(duty_texts=["가상의 절차를 설계하고 가상의 지표를 관리한다"])
    kind, grade, step = classify(_item("가상의 절차를 다뤄 본 사람", None), context)
    assert (kind, grade, step) == ("required", "E1", 3)


def test_사다리_4단계_두_번_나오면_필수쪽_E1이다():
    text = "같은 말이 두 번 나온다"
    context = PostingContext(occurrences={"같은말이두번나온다": 2})
    kind, grade, step = classify(_item(text, None), context)
    assert (kind, grade, step) == ("required", "E1", 4)


def test_아무_신호도_없으면_우대_기본값에_E0다():
    """필수로 잘못 분류하면 게이트·큰 감점으로 등수가 크게 흔들린다.
    **반대 방향의 오류가 덜 해롭다.**
    """
    kind, grade, step = classify(_item("판정할 단서가 하나도 없는 문구", None), PostingContext())
    assert (kind, grade, step) == ("preferred", "E0", 5)


def test_시각강조는_판정을_바꾸지_않는다():
    """강조는 디자인 관행이지 요구 강도가 아니다.
    그리고 OCR이 굵기·색을 안 주므로 `emphasized`는 실행 경로에서 항상 False다.
    """
    plain = classify(_item("같은 문구", None), PostingContext())
    marked = classify(_item("같은 문구", None, emphasized=True), PostingContext())
    assert plain == marked


# ------------------------------------------------- 3-B′. 한 공고에 직무가 여럿


def _three_position_ocr() -> OcrResult:
    """3열 표 픽스처. 첫 열에 직무 라벨, 오른쪽 열에 그 직무의 조건."""
    return result(
        [
            line("L-001", "직무", 80, 100),
            line("L-002", "가직무", 60, 200),
            line("L-003", "· 가직무에만 걸리는 조건", 200, 240),
            line("L-004", "나직무", 60, 400),
            line("L-005", "· 나직무에만 걸리는 조건", 200, 440),
            line("L-006", "다직무", 60, 600),
            line("L-007", "· 다직무에만 걸리는 조건", 200, 640),
            line("L-008", "모집 조건", 40, 800),
            line("L-009", "· 모든 직무에 걸리는 공통 조건", 40, 840),
        ]
    )


def test_구간의_경계는_라벨_사이의_중점이다():
    """라벨이 **셀 세로 가운데**에 있어서 라벨 y로 자르면 다음 직무의 조건이 들어온다.
    확보한 공고에서 실제로 133px, 6줄이 넘어왔다.
    """
    band = split_positions(_three_position_ocr(), "나직무", SETTINGS)
    assert band is not None
    # 가직무 중심 210 · 나직무 중심 410 · 다직무 중심 610 → 중점 310, 510
    assert (band.y_top, band.y_bottom) == (310, 510)


def test_다른_직무의_항목은_대상에서_빠지고_공통섹션은_남는다():
    """셀 소속을 잃으면 세 직무의 조건이 한 지원자에게 다 걸린다. **점수가 틀린다.**"""
    ocr = _three_position_ocr()
    band = split_positions(ocr, "나직무", SETTINGS)
    kept = {ln.id for ln in select_lines(ocr, band, SETTINGS)}

    assert "L-005" in kept, "대상 직무의 조건은 남아야 한다"
    assert "L-003" not in kept and "L-007" not in kept, "다른 직무의 조건이 들어왔다"
    assert "L-008" in kept and "L-009" in kept, "표 밖 공통 섹션이 통째로 사라졌다"


def test_직무명이_두_줄로_쪼개져도_찾는다():
    """OCR이 좁은 셀의 라벨을 줄로 자른다. 확보한 공고에서 실제로 그랬다."""
    ocr = result(
        [
            line("L-001", "가나", 60, 200),
            line("L-002", "다라마", 62, 225),
            line("L-003", "· 그 직무의 조건", 200, 260),
            line("L-004", "다음직무", 60, 400),
        ]
    )
    band = split_positions(ocr, "가나다라마", SETTINGS)
    assert band is not None
    # 앞 라벨이 없으니 위로 열어 두고, 아래는 다음 라벨과의 중점이다
    assert (band.y_top, band.y_bottom) == (0, 316)


def test_대상_직무를_못_찾으면_예외다():
    """조용히 전체를 파싱하면 세 직무의 조건이 한 지원자에게 다 걸린다."""
    with pytest.raises(PositionNotFound):
        split_positions(_three_position_ocr(), "없는직무", SETTINGS)


def test_직무를_안_주면_분할하지_않는다():
    assert split_positions(_three_position_ocr(), None, SETTINGS) is None


# ----------------------------------------------------------- 3-C. 헤더 역할 분류


def test_헤더분류_프롬프트에_문자열_말고는_아무것도_없다():
    """**이미지를 LLM에 보내지 않는다.** 좌표도 안 보낸다 — 그게 G4를 지어낸 좌표
    검사로 만들지 않는 유일한 방법이다.
    """
    prompt = build_prompt(["모집 조건", "복리후생"], ["애매한 줄"])
    assert "모집 조건" in prompt and "애매한 줄" in prompt
    for banned in ("image_url", "b64_json", "base64", "bbox", "x0", "y1"):
        assert banned not in prompt


class _FakeCompletions:
    def __init__(self, roles: dict[str, str]):
        self._roles = roles
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        prompt = kwargs["messages"][1]["content"]
        labels = [{"text": t, "role": r} for t, r in self._roles.items() if t in prompt]
        body = json.dumps({"labels": labels}, ensure_ascii=False)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=body))]
        )


def fake_client(roles: dict[str, str]):
    completions = _FakeCompletions(roles)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return client, completions


# ------------------------------------------------------------- 3-E. 조립 전체


def _write_posting(tmp_path: Path, posting_id: str, ocr: OcrResult, target: str | None = None):
    directory = tmp_path / "postings" / posting_id
    directory.mkdir(parents=True)
    (directory / "ocr.json").write_text(
        json.dumps(ocr.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    provenance = Provenance(
        posting_id=posting_id,
        source_kind="local",
        acquired_at="2026-09-01T00:00:00+09:00",
        target_position=target,
        image_sha256=["0" * 64],
        image_size=[(IMG_W, IMG_H)],
    )
    (directory / "provenance.json").write_text(
        json.dumps(provenance.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    ref = PostingRef(
        posting_id=posting_id,
        title="",
        company="",
        image_paths=[],
        fetched_at="2026-09-01T00:00:00+09:00",
        source_kind="local",
    )
    return ref, directory


def _mixed_ocr() -> OcrResult:
    """조건 섹션 · 담당업무 섹션 · 제외 섹션이 한 공고에 있는 픽스처."""
    return result(
        [
            line("L-001", "모집 조건", 40, 100),
            line("L-002", "· 두 줄에 걸친 조건의", 60, 140),
            line("L-003", "나머지 절반", 90, 170),
            line("L-004", "· 한 줄짜리 조건", 60, 210),
            line("L-005", "하는 일", 40, 300),
            line("L-006", "· 어떤 절차를 설계한다", 60, 340),
            line("L-007", "받는 것", 40, 420),
            line("L-008", "· 자유롭게 일할 수 있다", 60, 460),
        ]
    )


ROLES = {
    "모집 조건": "requirement",
    "하는 일": "duty",
    "받는 것": "excluded",
}


def test_제외_섹션의_항목은_조건이_되지_않는다(tmp_path):
    """**`excluded`가 없으면 조용히 틀린다** — 복리후생의 「자유롭게 일할 수 있다」가
    지원자에게 요구되는 조건으로 들어간다. 실제 공고에서 확인한 실패다.
    """
    ref, _ = _write_posting(tmp_path, "fixture", _mixed_ocr())
    client, _ = fake_client(ROLES)
    requirements, _, report = parse_posting(ref, SETTINGS, client=client, data_dir=tmp_path)

    texts = " ".join(req.text for req in requirements)
    assert "자유롭게" not in texts, "제외 섹션의 항목이 조건으로 올라왔다"
    assert "어떤 절차를 설계한다" not in texts, "담당업무는 조건이 아니다"
    assert report.excluded_blocks == ["받는 것"]


def test_조립된_조건이_병합되어_두_건이다(tmp_path):
    ref, _ = _write_posting(tmp_path, "fixture", _mixed_ocr())
    client, _ = fake_client(ROLES)
    requirements, _, _ = parse_posting(ref, SETTINGS, client=client, data_dir=tmp_path)
    assert len(requirements) == 2
    assert all(req.kind == "required" for req in requirements)
    assert all(req.evidence_grade == "E2" for req in requirements)


def test_모든_조건에_line_ids와_좌표가_있다(tmp_path):
    """G4의 선행 조건. 좌표를 못 만드는 항목은 버린다 — 어차피 G4가 차단한다."""
    ref, _ = _write_posting(tmp_path, "fixture", _mixed_ocr())
    client, _ = fake_client(ROLES)
    requirements, graph, _ = parse_posting(ref, SETTINGS, client=client, data_dir=tmp_path)

    for req in requirements:
        assert req.line_ids, f"{req.id}: line_ids가 비었다"
        assert req.source_bbox.x2 > req.source_bbox.x1
        assert req.source_bbox.y2 > req.source_bbox.y1
        assert req.review_status == "draft"
    assert graph.out(requirements[0].id, "extracted_from"), "좌표까지 이어지는 Link가 없다"


def test_span으로_자른_글자가_조건_문구와_정확히_같다(tmp_path):
    """유사도가 아니라 **동일성**이다. 느슨하게 비교하면 지어낸 문구가 그대로 통과한다."""
    ref, directory = _write_posting(tmp_path, "fixture", _mixed_ocr())
    client, _ = fake_client(ROLES)
    requirements, _, _ = parse_posting(ref, SETTINGS, client=client, data_dir=tmp_path)

    ocr = OcrResult.model_validate_json((directory / "ocr.json").read_text(encoding="utf-8"))
    document = ocr.document()
    for req in requirements:
        assert document[req.source_span.start : req.source_span.end] == req.text
    assert verify(requirements, ocr) == []


def test_좌표를_망가뜨리면_검증이_잡는다():
    """검증이 실제로 무언가를 잡는지 본다. 통과만 확인하면 검증이 죽어도 초록이다."""
    ocr = _mixed_ocr()
    good = RequirementRecord(
        id="R-01",
        text=ocr.lines[3].text,
        kind="required",
        evidence_grade="E2",
        ladder_step=1,
        source_bbox=ocr.lines[3].bbox,
        source_span={"start": 0, "end": len(ocr.lines[3].text)},
        line_ids=["L-004"],
    )
    # span은 L-004의 자리가 아니라 문서 맨 앞을 가리킨다 → P2가 잡아야 한다.
    assert any(v.rule == "P2" for v in verify([good], ocr))

    zeroed = good.model_copy(
        update={"source_bbox": BBox(page=1, x1=0, y1=0, x2=0, y2=0, img_w=IMG_W, img_h=IMG_H)}
    )
    assert any(v.rule == "P3" for v in verify([zeroed], ocr))


def test_헤더_역할은_캐시되어_LLM을_두_번_안_부른다(tmp_path):
    """공고당 1회. 완주 전체에서 2회다 (`docs/COST_BUDGET.md`)."""
    ref, _ = _write_posting(tmp_path, "fixture", _mixed_ocr())
    client, completions = fake_client(ROLES)

    _, _, first = parse_posting(ref, SETTINGS, client=client, data_dir=tmp_path)
    _, _, second = parse_posting(ref, SETTINGS, client=client, data_dir=tmp_path)

    assert (first.llm_calls, second.llm_calls) == (1, 0)
    assert len(completions.calls) == 1


def test_LLM_호출에_이미지가_실리지_않는다(tmp_path):
    """이 프로젝트의 검증 가능한 차별점 전체가 여기 걸려 있다 —
    VLM은 bbox를 지어내고, 그러면 G4가 지어낸 좌표를 검사하는 꼴이 된다.
    """
    ref, _ = _write_posting(tmp_path, "fixture", _mixed_ocr())
    client, completions = fake_client(ROLES)
    parse_posting(ref, SETTINGS, client=client, data_dir=tmp_path)

    payload = json.dumps(completions.calls[0], ensure_ascii=False, default=str)
    for banned in ("image_url", "b64_json", "base64", "img_1.png"):
        assert banned not in payload


def test_다른_직무의_조건은_한_건도_안_들어온다(tmp_path):
    """3-B′의 AC. 1·3번째 직무의 항목이 `Requirement`에 **한 건도** 없어야 한다."""
    ref, _ = _write_posting(tmp_path, "table", _three_position_ocr(), target="나직무")
    client, _ = fake_client(
        {
            "직무": "context",
            "가직무": "requirement",
            "나직무": "requirement",
            "다직무": "requirement",
            "모집 조건": "requirement",
        }
    )
    requirements, _, _ = parse_posting(ref, SETTINGS, client=client, data_dir=tmp_path)

    texts = " ".join(req.text for req in requirements)
    assert "가직무에만" not in texts and "다직무에만" not in texts
    assert "나직무에만" in texts
    assert "공통 조건" in texts, "표 밖 공통 섹션이 사라졌다"


# ------------------------------------------------------- VLM 폴백 — 감지까지만


def test_모호한_줄이_절반을_넘으면_멈춘다(tmp_path):
    """이 규칙 체계가 그 공고에 안 맞는다는 뜻이다. 조용히 진행하면
    **조건 0건이거나 근거 없는 조건 더미**가 나오고 뒤 step들이 초록불을 켠다.
    """
    ocr = result(
        [
            line("L-001", "모집 조건", 40, 100),
            line("L-002", "· 조건 하나", 60, 140),
            line("L-003", "붙을 데 없는 줄", 300, 300),
            line("L-004", "붙을 데 없는 줄 둘", 300, 340),
            line("L-005", "붙을 데 없는 줄 셋", 300, 380),
            line("L-006", "붙을 데 없는 줄 넷", 300, 420),
        ]
    )
    # L-003~005 앞에 header를 끼워 「직전 item」을 끊는다 → 전부 ambiguous
    ocr.lines.insert(2, line("L-002b", "구분", 40, 200))

    ref, _ = _write_posting(tmp_path, "vague", ocr)
    client, _ = fake_client({"모집 조건": "requirement", "구분": "context"})
    with pytest.raises(VlmFallbackRequired, match="모호한 줄"):
        parse_posting(ref, SETTINGS, client=client, data_dir=tmp_path)


def test_섹션_제목이_하나도_없으면_멈춘다(tmp_path):
    ocr = result(
        [
            line("L-001", "· 조건 하나", 300, 100),
            line("L-002", "· 조건 둘", 300, 140),
        ]
    )
    ref, _ = _write_posting(tmp_path, "noheader", ocr)
    client, _ = fake_client({})
    with pytest.raises(VlmFallbackRequired, match="섹션 제목"):
        parse_posting(ref, SETTINGS, client=client, data_dir=tmp_path)


def test_키가_없으면_조용히_넘어가지_않는다(tmp_path):
    """캐시도 없고 클라이언트도 없으면 멈춘다. 기본값으로 진행하면
    `excluded`를 못 가려 복리후생이 조건이 된다.
    """
    ref, _ = _write_posting(tmp_path, "nokey", _mixed_ocr())
    with pytest.raises(ParseError, match="OpenAI"):
        parse_posting(ref, SETTINGS, client=None, data_dir=tmp_path)
