"""줄을 섹션과 항목으로 — **이 step의 핵심이고, LLM이 안 들어오는 자리다.**

쓰는 신호는 셋뿐이다: **불릿 · x 들여쓰기 · 직전 항목과의 상대 위치.**
직군 이름도 스킬 이름도 섹션 제목 사전도 여기 없다 (과제 CRITICAL — 직군 무관 일반화).

## 판정 순서를 바꿨다 — 실측이 명세를 반증했다

`step3.md`의 표는 ① `x0 < header_x_threshold` → `header`가 **맨 앞**이었다.
그 순서는 **확보한 공고 2건에서 거의 모든 줄을 `header`로 만든다.**

| 공고 | 섹션 헤더의 x0 | 불릿 항목의 x0 | 이어지는 줄의 x0 | x로 갈리나 |
|---|---|---|---|---|
| 공고 A (3열 표) | 44~57 | 46~50 | 60~71 | **아니다** |
| 공고 B (단일 직군) | 42~43 | 42~43 | 111~114 | **아니다** |

`docs/OCR_EVIDENCE.md`의 실측(헤더 38~60 / 항목 125~140)은 **공고 1건**에서 나왔고,
§5가 「다른 공고가 다른 들여쓰기 체계를 쓴다 → 본문을 헤더로 본다」를 **틀리는 조건으로
미리 적어 뒀다.** 그 조건이 실제로 걸렸다.

**그래서 x 판정을 맨 뒤로 밀었다.** 순서는 불릿 → 이어지는 줄 → x → 모호다.

- **불릿이 x보다 강하다**: 공고 A에서 헤더(44)와 불릿 항목(46)의 x0가 사실상 같다.
  x를 먼저 보면 조건이 전부 섹션 제목이 되어 **조건 0건**이 나온다
- **이어지는 줄이 x보다 강하다**: 공고 A의 「합격 또는 채용이 취소되며…」는 x0가 60이라
  임계값 100 아래다. x를 먼저 보면 **한 조건의 뒷부분이 새 섹션 제목**이 되고,
  그 아래 진짜 항목들이 역할 없는 블록에 갇혀 채점에서 빠진다

**두 엔진이 같은 결론을 냈다.** PaddleOCR과 macOS Vision이 서로 다른 회사의 다른
모델인데 두 공고에서 똑같이 「x0로는 안 갈리고 불릿으로는 갈린다」를 보였다.
한 엔진에서만 확인된 규칙은 그 엔진의 특성일 수 있다 (`docs/OCR_EVIDENCE.md` §4).

**원래 실측 공고에서도 결과가 안 바뀐다.** 그 공고의 섹션 헤더에는 불릿이 없고
(①을 통과) 항목보다 **덜** 들여써져 있어(②도 통과) x 판정으로 내려온다.
즉 이 개정은 **기존 실측을 깨지 않으면서** 새 실측을 살린다.

**글자 크기(`height`)로는 가르지 않는다.** 이 두 공고에서는 헤더가 더 크지만(25~35px 대
16~26px), 원래 실측 공고에서는 모든 줄이 12~14px로 같았다 (`docs/OCR_EVIDENCE.md` §2).
한 공고에서만 되는 신호를 쓰면 다음 공고에서 조용히 틀린다.
"""

from __future__ import annotations

from bisect import bisect_left
from typing import Literal

from pydantic import BaseModel, ConfigDict

from ..config import Settings
from .ocr import OcrLine, OcrResult

LineRole = Literal["header", "item", "continuation", "ambiguous"]

# 목록 항목을 여는 글자. **직군 어휘가 아니라 조판 기호**라서 하드코딩해도
# 직군 무관 일반화를 깨지 않는다. 섹션 제목 사전과는 성격이 다르다 (그건 3-C가 LLM에게 맡긴다).
BULLETS: tuple[str, ...] = ("·", "•", "-", "–", "—", "※", "▶", "√", "✓", "◦", "○", "*")


class Block(BaseModel):
    """섹션 하나 — 헤더 1줄과 그 아래 항목들.

    `items`의 원소가 **줄 목록**인 것이 핵심이다. 한 조건이 두 줄에 걸쳐 있을 때
    병합하지 않으면 **조건 하나가 조건 둘로 세어져 배점이 갈라진다.**
    표시 문제가 아니라 점수 문제다.
    """

    model_config = ConfigDict(extra="forbid")

    header: OcrLine | None
    header_role: str | None = None  # 3-C가 채운다
    items: list[list[OcrLine]]


class PositionBand(BaseModel):
    """한 공고에 직무가 여럿일 때, 관심 직무가 차지하는 세로 구간.

    **표를 표로 파싱하지 않는다.** 필요한 건 셀 격자가 아니라 y 구간 하나다.
    PP-StructureV3 같은 표 인식 모델은 로컬에서 맥을 멈췄고 모델 10여 개를 내려받는다
    (`docs/OCR_EVIDENCE.md` §1).
    """

    model_config = ConfigDict(extra="forbid")

    label: str  # 대상 직무명. **런타임 인자다** — 코드에 박으면 직군 무관 일반화 위반
    y_top: int
    y_bottom: int


class PositionNotFound(LookupError):
    """대상 직무를 공고에서 못 찾았다.

    **조용히 전체를 파싱하지 않는다.** 그러면 세 직무의 조건이 한 지원자에게 다 걸려
    점수가 틀린다. 표시 문제가 아니다.
    """


def _bullet(text: str) -> str | None:
    """줄 맨 앞의 불릿. 없으면 `None`."""
    stripped = text.lstrip()
    for mark in BULLETS:
        if stripped.startswith(mark):
            return mark
    return None


def classify_lines(result: OcrResult, settings: Settings) -> dict[str, LineRole]:
    """줄마다 역할을 매긴다. **위에서부터, 결론이 나면 멈춘다.**

    | # | 조건 | 판정 |
    |---|---|---|
    | 1 | 첫 글자가 불릿 | `item` |
    | 2 | 불릿 없음 + 직전 item보다 `tolerance`~`max_indent`만큼 들여씀 | `continuation` |
    | 3 | `x0 < settings.header_x_threshold` | `header` |
    | 4 | 그 외 | `ambiguous` (3-C가 문자열만 보고 판정) |

    `step3.md`는 3번을 맨 앞에 뒀다. **실측에서 뒤로 밀었다** — 이유는 모듈 문서.

    2번의 **상한도 실측에서 생겼다.** 원래는 하한만 있어서 「직전 항목보다 오른쪽」이면
    전부 이어지는 줄이었는데, 다단 표에서는 **오른쪽 열 전체가 그 조건을 만족한다.**
    공고 A에서 근무지 열(x0 720)이 수행업무 항목(x0 244)에 붙어 조건 문구에
    지명이 섞였다. 이어지는 줄의 들여쓰기는 글자 몇 칸이지 열 하나가 아니다.
    """
    roles: dict[str, LineRole] = {}
    last_item_x0: int | None = None

    for line in result.lines:
        # --- 1. 불릿이 있으면 항목이다. 가장 강한 신호 -----------------------
        if _bullet(line.text) is not None:
            roles[line.id] = "item"
            last_item_x0 = line.x0
            continue

        # --- 2. 직전 항목보다 더 들여썼으면 그 항목이 이어지는 것이다 ---------
        # **이 규칙을 빼지 마라.** 실측에서 한 조건이 두 줄에 걸쳐 있었고, 병합하지
        # 않으면 조건 하나가 조건 둘로 세어져 배점이 갈라진다. 점수 문제다.
        if (
            last_item_x0 is not None
            and last_item_x0 + settings.continuation_tolerance
            < line.x0
            <= last_item_x0 + settings.continuation_max_indent
        ):
            roles[line.id] = "continuation"
            continue

        # --- 3. 남은 것 중 왼쪽에 붙은 줄이 섹션 제목이다 ---------------------
        if line.x0 < settings.header_x_threshold:
            roles[line.id] = "header"
            # 새 섹션이 열리면 직전 항목과의 연결이 끊긴다. 안 끊으면 섹션을 건너뛴
            # 줄이 앞 섹션의 항목에 붙는다.
            last_item_x0 = None
            continue

        roles[line.id] = "ambiguous"

    return roles


def build_blocks(result: OcrResult, roles: dict[str, LineRole]) -> list[Block]:
    """역할이 매겨진 줄을 섹션으로 묶는다.

    `continuation`은 **직전 항목에 병합**된다. 붙일 항목이 없으면 버린다 —
    섹션 헤더 바로 뒤의 이어지는 줄은 앞 항목이 없으므로 조건이 될 수 없다.

    `ambiguous`는 항목으로 세지 않는다. 3-C가 헤더로 승격시킨 것만 `roles`에서
    `header`로 바뀌어 들어오고, 나머지는 여기서 조용히 빠진다 — 판정 근거가 없는 줄을
    조건으로 올리면 `evidence_grade`가 무엇을 뜻하는지가 흐려진다.
    """
    blocks: list[Block] = []
    current: Block | None = None

    for line in result.lines:
        role = roles.get(line.id, "ambiguous")
        if role == "header":
            current = Block(header=line, items=[])
            blocks.append(current)
            continue
        if role == "item":
            if current is None:
                # 헤더 없이 시작하는 항목. 소속을 모르는 채로 버리지 않는다 —
                # header_role이 None이라 사다리 1단계가 안 걸리고 2단계 이하로 간다.
                current = Block(header=None, items=[])
                blocks.append(current)
            current.items.append([line])
            continue
        if role == "continuation" and current is not None and current.items:
            current.items[-1].append(line)

    return blocks


def count_merged(roles: dict[str, LineRole]) -> int:
    """병합된 줄 수. `ParseReport.merged_continuations`가 된다 —
    항목 수보다 많으면 다단 레이아웃을 한 줄짜리 항목으로 잘못 읽고 있는 것이다
    (`docs/OCR_EVIDENCE.md` §5).
    """
    return sum(1 for role in roles.values() if role == "continuation")


# ------------------------------------------------- 3-B′. 직무가 여러 개일 때


def _normalize(text: str) -> str:
    """공백만 지운다. `&`·`/` 같은 글자는 직무명의 일부라 지우면 다른 직무와 겹친다."""
    return "".join(text.split())


def group_column(column: list[OcrLine]) -> list[list[OcrLine]]:
    """첫 열 줄을 라벨 단위로 묶는다.

    좁은 셀에서는 **직무명이 줄로 쪼개진다** — 확보한 공고에서 「NW」와 「인프라운용」이
    별개 줄로 나왔다. 묶지 않으면 그 직무를 영영 못 찾는다.

    묶는 기준은 **줄 사이 간격이 줄 높이 이내**인가다. 새 임계값을 만들지 않으려고
    글자 높이를 자로 쓴다 — 줄바꿈 간격은 글자 크기에 비례하므로 공고마다 안 재도 된다.
    """
    groups: list[list[OcrLine]] = []
    for line in column:
        previous = groups[-1][-1] if groups else None
        if previous is not None and line.bbox.y1 - previous.bbox.y2 <= previous.height:
            groups[-1].append(line)
        else:
            groups.append([line])
    return groups


def _center(group: list[OcrLine]) -> float:
    return (group[0].bbox.y1 + group[-1].bbox.y2) / 2


def split_positions(
    result: OcrResult,
    target: str | None,
    settings: Settings | None = None,
) -> PositionBand | None:
    """관심 직무의 y 구간을 찾는다. `target`이 `None`이면 분할하지 않는다.

    표의 **첫 열**(직무명이 있는 열)은 x0가 작고, 수행업무가 들어가는 오른쪽 열은 크다.
    그 경계로 `header_x_threshold`를 재사용한다 — 임계값을 새로 만들지 않는다.

    ## 구간의 경계는 라벨의 y가 아니라 **라벨 사이의 중점**이다

    `step3.md`는 「라벨의 y부터 같은 열 다음 라벨의 y까지」로 잡으라고 했다.
    그건 **라벨이 셀 위쪽에 붙은 표**를 가정한 것이고, 확보한 공고는 아니었다 —
    라벨이 **셀 세로 가운데**에 있다.

    실측(공고 A): 두 번째 직무의 셀 내용은 y=2127에서 시작하는데 그 라벨은 y=2260에 있다.
    라벨 y로 자르면 **2127~2258의 133px, 즉 다음 직무의 조건 6줄이 이 직무의 구간에
    들어온다.** 그중 불릿 항목은 앞 섹션(우대사항) 아래에 그대로 붙어 **다른 직무의 업무가
    이 지원자의 우대조건이 된다.** 표시 문제가 아니라 점수 문제다.

    중점으로 자르는 것은 **줄을 가장 가까운 라벨에 배정하는 것**과 같다. 라벨이 셀
    가운데 있다는 사실을 그대로 쓰는 셈이고, 새 임계값이 필요 없다.

    **완벽하지는 않다.** 표의 열 제목 행(「직무」 같은 줄)도 첫 열에 있어서 라벨로 세어지고,
    그 때문에 첫 직무 셀의 **윗부분 몇 줄이 잘린다.** 잘리는 것은 담당업무 줄이라
    조건에는 영향이 없지만(`duty`는 조건이 아니다), 다른 표에서는 다를 수 있다.
    잘렸는지는 `ParseReport.role_counts`와 조건 수로 드러난다.
    """
    if target is None:
        return None

    threshold = settings.header_x_threshold if settings else 100
    groups = group_column([line for line in result.lines if line.x0 < threshold])
    wanted = _normalize(target)

    for index, group in enumerate(groups):
        if _normalize("".join(line.text for line in group)) != wanted:
            continue
        here = _center(group)
        y_top = (_center(groups[index - 1]) + here) / 2 if index else 0.0
        y_bottom = (
            (here + _center(groups[index + 1])) / 2
            if index + 1 < len(groups)
            else float(result.img_h)
        )
        return PositionBand(label=target, y_top=int(y_top), y_bottom=int(y_bottom))

    raise PositionNotFound(
        f"공고에서 직무 「{target}」를 못 찾았다. "
        "--position 값이 공고에 적힌 직무명과 같은지 확인한다 "
        "(조용히 전체를 파싱하면 다른 직무의 조건이 지원자에게 걸린다)"
    )


def select_lines(
    result: OcrResult, band: PositionBand | None, settings: Settings
) -> list[OcrLine]:
    """채점 대상 줄만 남긴다.

    - 밴드 안: 전부 남긴다
    - 밴드 밖이면서 **들여쓴 줄**(`x0 >= header_x_threshold`): **뺀다.** 표의 오른쪽 열,
      즉 다른 직무의 수행업무·우대사항이다
    - 밴드 밖이면서 첫 열에 있는 줄: **남긴다.** 표 위/아래의 공통 섹션이다

    **공통 섹션을 살리는 것이 핵심이다.** 「지원자격」은 표 밖에 있고 전 직무에 걸린다.
    밴드만 잘라내면 **자격요건이 통째로 사라진다.**

    표의 격자를 복원하지 않고 이 규칙으로 가는 이유: 필요한 판정은 「이 줄이 다른 직무의
    칸에 있나」 하나뿐이고, 그건 **밴드 밖 + 오른쪽 열**로 충분히 갈린다.
    """
    if band is None:
        return list(result.lines)

    kept: list[OcrLine] = []
    for line in result.lines:
        inside = band.y_top <= line.bbox.y1 < band.y_bottom
        if inside or line.x0 < settings.header_x_threshold:
            kept.append(line)
    return kept


def _x_clusters(lines: list[OcrLine], gap: int) -> list[list[OcrLine]]:
    """x0가 `gap`보다 크게 벌어지면 다른 열로 본다.

    표의 격자를 복원하지 않는다. 필요한 판정은 **「같은 열인가」** 하나뿐이고,
    자는 `continuation_max_indent`를 그대로 쓴다 — 「이어지는 줄의 들여쓰기는
    이보다 작다」와 「이보다 벌어지면 다른 열이다」는 같은 문장이다.
    """
    clusters: list[list[OcrLine]] = []
    for line in sorted(lines, key=lambda item: item.x0):
        if clusters and line.x0 - clusters[-1][-1].x0 <= gap:
            clusters[-1].append(line)
        else:
            clusters.append([line])
    return clusters


def order_band_lines(
    lines: list[OcrLine],
    roles: dict[str, LineRole],
    band: PositionBand,
    settings: Settings,
) -> tuple[list[OcrLine], dict[str, LineRole]]:
    """밴드 안의 줄을 **읽는 순서**로 다시 세우고, 그 안의 역할을 다시 매긴다.

    ## 왜 필요한가 — 셀 라벨은 자기 내용의 한가운데 있다

    `split_positions`가 고친 것과 **같은 원인이 한 단계 아래에서 또 나온다.** 셀 라벨이
    세로 가운데 정렬이라 OCR의 y 순서에서 **자기 내용보다 뒤에 나온다.**

    실측(공고 A, B2C 밴드): 「우대사항」 라벨은 y=2723인데 그 첫 조건은 y=2699다.
    줄 순서대로 블록을 묶으면 그 조건이 **앞 섹션(수행업무)에 붙고**, 정작 우대사항
    블록은 **항목 0개**가 된다. 실제로 이 공고의 우대 조건이 통째로 사라졌다 —
    표시 문제가 아니라 **그 직무를 다른 직무와 구별하는 내용 전부**가 빠지는 문제다.

    ## 제목 판별을 LLM에서 **열 구조**로 옮긴다

    처음엔 3-C가 역할을 준 줄을 라벨로 썼다. **틀렸다.** LLM에게는 문자열만 가는데,
    조건 문장 「서로 다른 이해관계를 조율하며 …하시는 분」은 읽으면 영락없이 우대 조건이라
    `preferred`가 돌아온다. 그 줄이 라벨이 되자 **그 위의 수행업무 4건이 우대 조건으로
    넘어갔다** — 담당업무를 지원자의 자격으로 세는 것이라 점수가 통째로 틀린다.

    LLM은 「이 제목이 무슨 성격인가」에 답할 수 있지만 **「이 줄이 제목인가」에는 못 답한다.**
    그건 뜻이 아니라 배치의 문제이고, 배치는 OCR이 준다. 그래서 여기서는 좌표로 가른다.

    ## 규칙

    1. **행 라벨 열**(`x0 < header_x_threshold`, `split_positions`가 쓰는 그 열)은
       내용이 아니라 이 행의 이름이다. `band.label`에 이미 있으므로 뒤로 뺀다 —
       버리지는 않는다(줄 수가 달라지면 보고서가 거짓말을 한다)
    2. 남은 줄을 x0로 묶는다. **맨 왼쪽 묶음이 섹션 제목, 그 오른쪽 묶음이 내용**이다.
       더 오른쪽 묶음은 **다른 열**(근무지 등)이라 조건이 아니다
    3. 내용 줄을 **가장 가까운 제목**에 배정한다. 경계는 제목 사이의 중점 —
       `split_positions`와 같은 규칙이고 새 임계값이 없다
    4. 칸 안에서는 들여쓰기 기준을 **그 칸의 왼쪽 끝**으로 다시 잡는다. 공고 전체에
       하나인 `header_x_threshold`는 칸 안에서는 자가 되지 못한다 — 실제로 불릿이
       OCR에서 떨어져 나간 조건 한 건이 그 때문에 통째로 사라졌다

    묶음이 하나뿐이면(제목과 내용이 안 갈린다) **손대지 않고 그대로 돌려준다** —
    잘못 세우는 것보다 안 세우는 것이 낫다.
    """
    inside = [line for line in lines if band.y_top <= line.bbox.y1 < band.y_bottom]
    if not inside:
        return lines, roles

    row_label = [line for line in inside if line.x0 < settings.header_x_threshold]
    body = [line for line in inside if line.x0 >= settings.header_x_threshold]
    clusters = _x_clusters(body, settings.continuation_max_indent)
    if len(clusters) < 2:
        return lines, roles

    labels = sorted(clusters[0], key=lambda line: (line.bbox.y1 + line.bbox.y2) / 2)
    content = clusters[1]
    # 세 번째 묶음부터는 표의 다른 열이다. 조건도 제목도 아니므로 블록에서 빠진다.
    others = [line for cluster in clusters[2:] for line in cluster] + row_label

    centers = [(line.bbox.y1 + line.bbox.y2) / 2 for line in labels]
    bounds = [(centers[i] + centers[i + 1]) / 2 for i in range(len(centers) - 1)]

    buckets: list[list[OcrLine]] = [[] for _ in labels]
    for line in content:
        center = (line.bbox.y1 + line.bbox.y2) / 2
        buckets[bisect_left(bounds, center)].append(line)

    updated = dict(roles)
    for line in labels:
        updated[line.id] = "header"
    for line in others:
        updated[line.id] = "ambiguous"

    ordered: list[OcrLine] = []
    for label, bucket in zip(labels, buckets, strict=True):
        ordered.append(label)
        bucket.sort(key=lambda line: line.bbox.y1)
        baseline = min((line.x0 for line in bucket), default=0)
        last_item_x0: int | None = None
        for line in bucket:
            if _bullet(line.text) is not None:
                updated[line.id] = "item"
                last_item_x0 = line.x0
            elif (
                last_item_x0 is not None
                and last_item_x0 + settings.continuation_tolerance
                < line.x0
                <= last_item_x0 + settings.continuation_max_indent
            ):
                updated[line.id] = "continuation"
            elif line.x0 <= baseline + settings.continuation_tolerance:
                # 칸의 왼쪽 끝에 붙은 줄. 불릿이 OCR에서 떨어져 나갔어도 항목이다.
                updated[line.id] = "item"
                last_item_x0 = line.x0
            else:
                updated[line.id] = "ambiguous"
            ordered.append(line)
    ordered.extend(others)

    head = [line for line in lines if line.bbox.y1 < band.y_top]
    tail = [line for line in lines if line.bbox.y1 >= band.y_bottom]
    return head + ordered + tail, updated
