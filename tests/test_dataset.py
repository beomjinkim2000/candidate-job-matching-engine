"""목업 데이터셋(step 10)이 지켜야 할 계약.

`tests/CLAUDE.md`의 원칙대로 커버리지가 아니라 **케이스 선정**이 이 파일의 내용이다.
여기서 고른 케이스는 전부 「이 데이터셋이 무너지면 뒤의 측정이 전부 무의미해지는 지점」이다.

- 유형 분포 · 직군 분리 → 과제 요구 그 자체
- 분량 통제 → LLM 채점자의 길이 선호 편향 (`docs/TRADEOFFS.md` E-2)
- read_fields · design_note → 게이트 G3. 라벨이 배점을 보고 만들어졌는지
- 홀드아웃 → 라벨의 손이 닿지 않은 측정 지점이 실제로 남아 있는지
- 원문 대조 → 「공고 원문을 복붙하지 않는다」의 기계적 증명

점수의 옳고 그름은 여기서 재지 않는다. 잴 수 없다 (`tests/CLAUDE.md` 「알려진 한계」).
"""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RESUMES = ROOT / "data" / "resumes"
POSTINGS = ROOT / "data" / "postings"

POSTING_IDS = ["kt-b2c", "nexon-game"]

# 라벨이 읽어도 되는 필드. weight·criterion_id·anchors·evidence_grade·ladder_step은 배점과
# 이어져 있어, 라벨이 그걸 보면 유형 분리 테스트가 자기 자신을 검사하게 된다 (step10.md).
ALLOWED_READ_FIELDS = {"text", "kind"}

# design_note가 배점을 근거로 들었는지 보는 패턴. AC와 같은 식이며 조건 ID(R-\d)를 더했다 —
# 항목 번호를 근거로 서술했다면 그것도 조건 목록을 배점처럼 다뤘다는 뜻이다.
FORBIDDEN_IN_NOTE = re.compile(r"C-\d|R-\d|가중치|배점|weight|\d+\s*점")

# 홀드아웃으로 뺀 조건의 핵심 낱말. 이 낱말이 design_note에 있으면 그 조건을 겨냥해
# 설계했다는 뜻이고, 홀드아웃인 이유가 사라진다.
HOLDOUT_KEYWORDS = {
    "kt-b2c": ["AX", "클라우드", "기술 트렌드", "이해관계", "갈등", "협상"],
    "nexon-game": ["생성형", "MySQL", "데이터베이스"],
}

# 공고 원문과 이력서 본문이 공백 제거 후 연속으로 겹쳐도 되는 최대 길이.
# 실측 최댓값은 8자("2026년12월" — 날짜)와 7자("Git·SVN" — 도구 이름)다. 문장이 아니라
# 고유명사·날짜 수준에서만 겹친다는 뜻이고, 임계 10은 거기에 둔 여유다.
MAX_VERBATIM_RUN = 10


def load_candidates(posting_id: str) -> list[dict]:
    d = RESUMES / posting_id
    files = sorted(f for f in d.glob("*.json") if f.stem not in ("index", "holdout"))
    return [json.loads(f.read_text(encoding="utf-8")) for f in files]


def load_holdout(posting_id: str) -> dict:
    return json.loads((RESUMES / posting_id / "holdout.json").read_text(encoding="utf-8"))


def load_posting(posting_id: str) -> dict:
    return json.loads((POSTINGS / posting_id / "requirements.json").read_text(encoding="utf-8"))


def squeeze(s: str) -> str:
    return re.sub(r"\s+", "", s)


def longest_common_run(a: str, b: str) -> tuple[int, str]:
    """a와 b에서 연속으로 일치하는 가장 긴 조각의 길이와 그 조각."""
    row = [0] * (len(b) + 1)
    best = 0
    end = 0
    for i, ca in enumerate(a):
        prev = 0
        for j, cb in enumerate(b, 1):
            carry = row[j]
            row[j] = prev + 1 if ca == cb else 0
            if row[j] > best:
                best = row[j]
                end = i
            prev = carry
    return best, a[end - best + 1 : end + 1]


@pytest.mark.parametrize("posting_id", POSTING_IDS)
def test_공고당_여섯명이고_유형이_2_3_1로_갈린다(posting_id: str) -> None:
    """과제 요구 그대로다 — 공고마다 완벽 2 / 부분 3 / 미스 1.

    이 분포가 깨지면 유형 분리 측정(step 11)의 그룹이 성립하지 않는다.
    """
    docs = load_candidates(posting_id)
    assert len(docs) == 6
    counts = {
        t: sum(1 for d in docs if d["intended_type"] == t)
        for t in ("perfect", "partial", "mismatch")
    }
    assert counts == {"perfect": 2, "partial": 3, "mismatch": 1}
    assert len({d["candidate_id"] for d in docs}) == 6


def test_두_공고의_직군이_서로_다르다() -> None:
    """「직군 무관 일반화」 주장의 전제.

    두 데이터셋의 직군이 같으면 직군 교차 측정(1차 지표)이 아무것도 반증하지 못한다.
    """
    labels = [
        json.loads((RESUMES / p / "index.json").read_text(encoding="utf-8"))["target_position"]
        for p in POSTING_IDS
    ]
    assert all(labels), labels
    assert len(set(labels)) == 2, labels


@pytest.mark.parametrize("posting_id", POSTING_IDS)
def test_여섯명의_분량이_통제돼_있다(posting_id: str) -> None:
    """LLM 채점자는 길고 상세한 답을 부당하게 선호한다 (`docs/TRADEOFFS.md` E-2).

    완벽 매칭형만 길게 쓰면 점수가 갈린 이유가 적합도인지 길이인지 구분할 수 없다.
    미스매칭형도 같은 분량으로 쓰되, 통제 자체가 비현실적이라는 사실은 design_note에 남긴다.
    """
    lengths = [len(d["text"]) for d in load_candidates(posting_id)]
    assert max(lengths) / min(lengths) <= 1.35, lengths


@pytest.mark.parametrize("posting_id", POSTING_IDS)
def test_라벨이_배점을_보지_않았다(posting_id: str) -> None:
    """게이트 G3 — 자기충족 순환을 좁혔다는 것의 기계 검사.

    배점을 알고 라벨을 정하면 유형 분리 테스트가 자기 자신을 검사하게 된다. 다만 이
    검사는 **자기신고**를 볼 뿐이라 우회 가능하다 — 진짜 방어는 이 step이 step 4보다
    먼저 돌아 `rubric.json`이 아예 없다는 순서 쪽이다 (`step10.md`).
    """
    for doc in load_candidates(posting_id):
        assert set(doc["read_fields"]) <= ALLOWED_READ_FIELDS, doc["candidate_id"]
        hit = FORBIDDEN_IN_NOTE.search(doc["design_note"])
        assert hit is None, (doc["candidate_id"], hit.group(0) if hit else "")


@pytest.mark.parametrize("posting_id", POSTING_IDS)
def test_rubric을_한_번도_열_수_없었다(posting_id: str) -> None:
    """G3의 구조적 방어 — 이 step이 돌 때 `rubric.json`은 존재하지 않는다.

    step 4가 뒤에 만든다. 순서가 다시 바뀌어 이 파일이 먼저 생기면 「읽지 마라」는
    규칙으로만 남고 구조적 강제가 사라지므로, 그때 조용히 넘어가지 않도록 여기서 잡는다.
    """
    assert not (POSTINGS / posting_id / "rubric.json").exists()


@pytest.mark.parametrize("posting_id", POSTING_IDS)
def test_홀드아웃_조건이_설계에_안_들어갔다(posting_id: str) -> None:
    """홀드아웃은 라벨의 손이 닿지 않은 유일한 측정 지점이다.

    조건 두 개가 실재하는 ID여야 하고, 여섯 명 누구의 design_note에도 그 조건을 겨냥한
    흔적이 없어야 한다. 본문(text)은 검사하지 않는다 — 지원서 서식에서 자연히 스치는
    것까지 막으면 그건 「안 다룬다」가 아니라 「일부러 피한다」가 되어 홀드아웃이 오염된다.
    """
    holdout = load_holdout(posting_id)
    ids = holdout["requirement_ids"]
    assert len(ids) == 2, ids

    known = {r["id"] for r in load_posting(posting_id)["requirements"]}
    assert set(ids) <= known, (ids, sorted(known))

    for doc in load_candidates(posting_id):
        for word in HOLDOUT_KEYWORDS[posting_id]:
            assert word not in doc["design_note"], (doc["candidate_id"], word)


@pytest.mark.parametrize("posting_id", POSTING_IDS)
def test_공고_원문을_베껴_넣지_않았다(posting_id: str) -> None:
    """CLAUDE.md CRITICAL — 조건 문구를 그대로 옮기면 문자열 매칭이 100%로 나온다.

    그러면 스코어러가 「같은 뜻을 다른 표현으로 쓴 것」을 잡아내는지 검증할 수 없다.
    공백을 지우고 연속 일치 최댓값을 재며, 임계 위에서 걸리면 어느 조각인지 함께 알린다.
    """
    posting = load_posting(posting_id)
    items = [(r["id"], squeeze(r["text"])) for r in posting["requirements"] + posting["duties"]]
    for doc in load_candidates(posting_id):
        body = squeeze(doc["text"])
        for rid, item in items:
            run, piece = longest_common_run(body, item)
            assert run < MAX_VERBATIM_RUN, (doc["candidate_id"], rid, run, piece)


@pytest.mark.parametrize("posting_id", POSTING_IDS)
def test_민감_속성이_일부러_들어_있다(posting_id: str) -> None:
    """마스킹이 실제로 도는지 확인하려면 마스킹할 것이 데이터에 있어야 한다.

    이름·생년월일·학교가 하나라도 빠지면 마스킹 테스트가 통과해도 아무 말을 하지 않는다.
    """
    for doc in load_candidates(posting_id):
        text = doc["text"]
        assert re.search(r"(이름|성명):\s*\S+", text), doc["candidate_id"]
        assert re.search(r"\d{4}\.\d{2}\.\d{2}", text), doc["candidate_id"]
        assert re.search(r"만 \d+세", text), doc["candidate_id"]
        assert "대학" in text, doc["candidate_id"]


def test_제출_데이터셋과_테스트_픽스처가_분리돼_있다() -> None:
    """테스트가 제출 데이터에 의존하면 데이터를 못 고친다 (`tests/CLAUDE.md`).

    이 파일은 **계약**만 검사하므로 본문이 바뀌어도 깨지지 않는다. 반대로 픽스처 쪽에
    이력서 사본이 생기면 두 벌이 갈라져 어느 쪽이 제출물인지 모르게 된다.
    """
    fixtures = ROOT / "tests" / "fixtures"
    if not fixtures.exists():
        return
    submitted = {squeeze(d["text"]) for p in POSTING_IDS for d in load_candidates(p)}
    for path in fixtures.rglob("*.json"):
        assert squeeze(path.read_text(encoding="utf-8")) not in submitted, path
