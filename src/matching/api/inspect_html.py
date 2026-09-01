"""파싱 중간 산출물을 **원본 이미지 위에** 펼치는 점검용 페이지.

제출용 결과 화면(step 9)이 아니다. 저것은 **점수와 근거**를 보여주고, 이것은
**규칙이 이미지를 어떻게 읽었는지**를 보여준다. 목적이 다르니 파일도 다르다.

## 왜 표가 아니라 이미지인가

줄 목록만 봐서는 **틀린 것을 못 본다.** 좌표가 한 칸 밀렸는지, 두 열이 한 줄로
붙었는지, 밴드가 엉뚱한 데서 잘렸는지는 **박스를 원본 위에 얹어야** 보인다.
실제로 이 프로젝트에서 잡은 결함 4건 중 3건이 「글자는 맞는데 배치가 틀린」 것이었다.

## 커밋하지 않는다

원본 이미지를 data URI로 **그대로 품는다.** 그래서 이 HTML은 공고 이미지 자체이고,
`ocr.json`과 같은 이유로 커밋 대상이 아니다. `out/`(무시 대상)에만 쓴다.

보여주는 것은 파이프라인이 남긴 `trace` 하나뿐이다. 여기서 다시 계산하지 않는다 —
화면이 결과와 어긋나면 화면을 믿고 규칙을 고치게 된다.
"""

from __future__ import annotations

import base64
import html
import json
from pathlib import Path

# 줄 역할의 색. 「무엇이 조건이 됐나」가 한눈에 보이는 것이 이 화면의 전부다.
ROLE_STYLE: dict[str, tuple[str, str, str]] = {
    "header": ("#7c3aed", "섹션 제목", "왼쪽 열에 있다"),
    "item": ("#0d9488", "항목", "불릿이 있거나 칸의 왼쪽 끝이다"),
    "continuation": ("#0284c7", "이어지는 줄", "직전 항목보다 조금 더 들여썼다"),
    "ambiguous": ("#ca8a04", "모호", "규칙으로 못 가른다 — 채점에 안 들어간다"),
    "dropped": ("#71717a", "구간 밖", "다른 직무의 칸이라 잘라냈다"),
}

_CSS = """
:root { color-scheme: light dark; --bg:#fff; --fg:#18181b; --mut:#71717a;
        --line:#e4e4e7; --card:#fafafa; --accent:#4f46e5; --hit:#f43f5e; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#0b0b0f; --fg:#e4e4e7; --mut:#a1a1aa; --line:#27272a;
          --card:#141419; --accent:#818cf8; }
}
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font-size:14px; line-height:1.55;
       font-family:-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo",Pretendard,sans-serif; }
.wrap { max-width:1400px; margin:0 auto; padding:28px 20px 80px; }
h1 { font-size:20px; margin:0 0 4px; }
h2 { font-size:15px; margin:36px 0 4px; padding-top:18px; border-top:1px solid var(--line); }
h2 .n { color:var(--mut); font-weight:400; margin-left:6px; font-size:13px; }
p.note { color:var(--mut); margin:4px 0 14px; font-size:13px; }
.stats { display:flex; flex-wrap:wrap; gap:8px; margin:14px 0 0; }
.stat { background:var(--card); border:1px solid var(--line); border-radius:8px;
        padding:7px 11px; font-size:11.5px; color:var(--mut); }
.stat b { display:block; font-size:16px; color:var(--fg);
          font-variant-numeric:tabular-nums; }

/* --- 1절. 이미지 + 줄 목록 --------------------------------------------- */
.split { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr); gap:14px; }
@media (max-width:900px) { .split { grid-template-columns:1fr; } }
.pane { border:1px solid var(--line); border-radius:10px; background:var(--card);
        overflow:auto; max-height:78vh; position:relative; }
.figure { position:relative; line-height:0; }
.figure img { width:100%; height:auto; display:block; }
.bx { position:absolute; border:1.5px solid; border-radius:2px; cursor:pointer; }
.bx.on { background:var(--hit); border-color:var(--hit); opacity:.55; z-index:5; }
.band { position:absolute; left:0; right:0; border-top:2px dashed var(--accent);
        border-bottom:2px dashed var(--accent); background:rgba(79,70,229,.09);
        pointer-events:none; }
.band span { position:absolute; top:2px; left:4px; font-size:10px; line-height:1.4;
             background:var(--accent); color:#fff; padding:1px 5px; border-radius:3px; }
.rows { width:100%; border-collapse:collapse; font-size:12.5px; }
.rows th { position:sticky; top:0; z-index:2; background:var(--card); text-align:left;
           font-size:11px; color:var(--mut); padding:6px 8px;
           border-bottom:1px solid var(--line); }
.rows td { padding:4px 8px; border-bottom:1px solid var(--line); vertical-align:top; }
.rows tr { cursor:pointer; }
.rows tr:hover td { background:rgba(127,127,127,.09); }
.rows tr.on td { background:rgba(244,63,94,.16); }
.rows tr.off { opacity:.45; }
.rows td.num { font-variant-numeric:tabular-nums; color:var(--mut); text-align:right;
               white-space:nowrap; font-size:11px; }
.rows td.txt { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; word-break:break-all; }
.dot { display:inline-block; width:8px; height:8px; border-radius:2px; margin-right:5px;
       vertical-align:middle; }
.rid { display:inline-block; background:var(--accent); color:#fff; border-radius:3px;
       padding:0 4px; font-size:10px; margin-left:4px; vertical-align:middle; }
.bar { display:flex; gap:6px; flex-wrap:wrap; align-items:center; margin:0 0 10px; }
.chip { border:1px solid var(--line); background:var(--card); color:var(--fg);
        border-radius:999px; padding:4px 10px; font-size:11.5px; cursor:pointer;
        font-family:inherit; }
.chip[aria-pressed="false"] { opacity:.4; }
.chip .dot { margin-right:4px; }
.legend { color:var(--mut); font-size:11.5px; margin-left:auto; }

/* --- 3·4절 --------------------------------------------------------------- */
.blk { border:1px solid var(--line); border-radius:10px; margin:8px 0;
       background:var(--card); overflow:hidden; }
.blk > .hd { padding:8px 12px; display:flex; gap:8px; align-items:center; flex-wrap:wrap;
             border-bottom:1px solid var(--line); }
.blk.off { opacity:.48; }
.blk ul { margin:0; padding:8px 12px 10px 28px; }
.blk li { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12.5px;
          word-break:break-all; }
.blk .none { padding:8px 12px; color:var(--mut); font-size:12px; }
.tag { display:inline-block; padding:1px 7px; border-radius:999px; font-size:11px;
       font-weight:600; color:#fff; white-space:nowrap; }
.req { border:1px solid var(--line); border-left:3px solid var(--accent); border-radius:8px;
       padding:9px 12px; margin:7px 0; background:var(--card); cursor:pointer; }
.req .meta { color:var(--mut); font-size:11.5px; margin-bottom:3px;
             font-variant-numeric:tabular-nums; }
.req .body { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:13px;
             white-space:pre-wrap; word-break:break-all; }
.scroll { overflow-x:auto; border:1px solid var(--line); border-radius:10px;
          background:var(--card); }
table.plain { border-collapse:collapse; width:100%; font-size:12.5px; }
table.plain th { text-align:left; font-size:11px; color:var(--mut); padding:6px 10px;
                 border-bottom:1px solid var(--line); }
table.plain td { padding:5px 10px; border-bottom:1px solid var(--line);
                 vertical-align:top; }
table.plain td.txt { font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
                     word-break:break-all; }
.rows td.txt .rid { font-family:inherit; }
"""

_JS = """
(function () {
  var boxes = {}, rows = {};
  document.querySelectorAll('.bx').forEach(function (el) { boxes[el.dataset.id] = el; });
  document.querySelectorAll('.rows tr[data-id]').forEach(
    function (el) { rows[el.dataset.id] = el; });
  var current = null;

  function pick(id, from) {
    if (current && boxes[current]) boxes[current].classList.remove('on');
    if (current && rows[current]) rows[current].classList.remove('on');
    if (current === id) { current = null; return; }
    current = id;
    var box = boxes[id], row = rows[id];
    if (box) {
      box.classList.add('on');
      if (from !== 'box') box.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
    if (row) {
      row.classList.add('on');
      if (from !== 'row') row.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
  }

  Object.keys(boxes).forEach(function (id) {
    boxes[id].addEventListener('click', function () { pick(id, 'box'); });
  });
  Object.keys(rows).forEach(function (id) {
    rows[id].addEventListener('click', function () { pick(id, 'row'); });
  });
  document.querySelectorAll('.req[data-lines]').forEach(function (el) {
    el.addEventListener('click', function () {
      var first = el.dataset.lines.split(',')[0];
      if (first) pick(first, 'req');
    });
  });

  document.querySelectorAll('.chip[data-role]').forEach(function (chip) {
    chip.addEventListener('click', function () {
      var on = chip.getAttribute('aria-pressed') === 'true';
      chip.setAttribute('aria-pressed', on ? 'false' : 'true');
      var role = chip.dataset.role;
      document.querySelectorAll('[data-role="' + role + '"]').forEach(function (el) {
        if (el === chip) return;
        if (el.classList.contains('bx')) el.style.display = on ? 'none' : '';
        else el.style.display = on ? 'none' : '';
      });
    });
  });
})();
"""


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _role_of(line: dict) -> str:
    return line["role"] if line.get("scoped") and line.get("role") else "dropped"


def _tag(role: str) -> str:
    color, label, _ = ROLE_STYLE.get(role, ("#71717a", role, ""))
    return f'<span class="tag" style="background:{color}">{_esc(label)}</span>'


def _kind_tag(kind: str) -> str:
    color = {"required": "#b91c1c", "preferred": "#1d4ed8", "gate": "#334155"}.get(kind, "#71717a")
    label = {"required": "필수", "preferred": "우대", "gate": "게이트"}.get(kind, kind)
    return f'<span class="tag" style="background:{color}">{_esc(label)}</span>'


def _data_uri(path: Path) -> str | None:
    """이미지를 그대로 품는다. 파일이 없으면 박스만 그린다 — 화면이 죽지는 않는다."""
    try:
        blob = path.read_bytes()
    except OSError:
        return None
    suffix = path.suffix.lower().lstrip(".") or "png"
    kind = "jpeg" if suffix in {"jpg", "jpeg"} else suffix
    return f"data:image/{kind};base64,{base64.b64encode(blob).decode('ascii')}"


def _stats(trace: dict) -> str:
    report = trace.get("report", {})
    lines = trace.get("lines", [])
    kinds: dict[str, int] = {}
    for req in trace.get("requirements", []):
        kinds[req["kind"]] = kinds.get(req["kind"], 0) + 1
    cells = [
        ("OCR이 읽은 줄", len(lines)),
        ("채점 구간 안", sum(1 for line in lines if line.get("scoped"))),
        ("평균 신뢰도", f"{trace.get('avg_conf', 0):.3f}"),
        ("LLM 호출", report.get("llm_calls", 0)),
        ("조건", len(trace.get("requirements", []))),
        ("필수", kinds.get("required", 0)),
        ("우대", kinds.get("preferred", 0)),
    ]
    body = "".join(f"<div class='stat'><b>{_esc(v)}</b>{_esc(k)}</div>" for k, v in cells)
    return f"<div class='stats'>{body}</div>"


def _figure(trace: dict, page: int, src: str | None) -> str:
    width = trace.get("img_w") or 1
    height = trace.get("img_h") or 1
    parts = []
    band = trace.get("band")
    if band:
        top = band["y_top"] / height * 100
        tall = (band["y_bottom"] - band["y_top"]) / height * 100
        parts.append(
            f"<div class='band' style='top:{top:.4f}%;height:{tall:.4f}%'>"
            f"<span>{_esc(band['label'])} 구간</span></div>"
        )
    for line in trace.get("lines", []):
        box = line["box"]
        if box.get("page", 1) != page:
            continue
        role = _role_of(line)
        color = ROLE_STYLE.get(role, ("#71717a",))[0]
        left = box["x1"] / width * 100
        top = box["y1"] / height * 100
        wide = (box["x2"] - box["x1"]) / width * 100
        tall = (box["y2"] - box["y1"]) / height * 100
        title = f"{line['id']} · {role}"
        if line.get("req"):
            title += f" · {line['req']}"
        title += f"\n{line['text']}"
        parts.append(
            f"<div class='bx' data-role='{_esc(role)}' data-id='{_esc(line['id'])}' "
            f"title='{_esc(title)}' "
            f"style='left:{left:.4f}%;top:{top:.4f}%;width:{wide:.4f}%;height:{tall:.4f}%;"
            f"border-color:{color}'></div>"
        )
    missing = "<div style='padding:40px;color:var(--mut)'>이미지 파일을 못 읽었다</div>"
    shown = f"<img src='{src}' alt='공고 이미지' width='{width}' height='{height}'>"
    img = shown if src else missing
    return f"<div class='figure'>{img}{''.join(parts)}</div>"


def _rows(trace: dict) -> str:
    body = []
    for line in trace.get("lines", []):
        role = _role_of(line)
        color = ROLE_STYLE.get(role, ("#71717a", role, ""))[0]
        label = ROLE_STYLE.get(role, ("", role, ""))[1]
        req = f"<span class='rid'>{_esc(line['req'])}</span>" if line.get("req") else ""
        klass = "" if line.get("scoped") else " off"
        body.append(
            f"<tr data-id='{_esc(line['id'])}' data-role='{_esc(role)}' class='{klass}'>"
            f"<td class='num'>{_esc(line['id'])}</td>"
            f"<td class='num'>{_esc(line['box']['y1'])}</td>"
            f"<td class='num'>{_esc(line['x0'])}</td>"
            f"<td class='num'>{_esc(line['conf'])}</td>"
            f"<td><span class='dot' style='background:{color}'></span>{_esc(label)}</td>"
            f"<td class='txt'>{_esc(line['text'])}{req}</td>"
            "</tr>"
        )
    head = (
        "<tr><th>줄</th><th>y</th><th>x0</th><th>신뢰도</th><th>판정</th>"
        "<th>읽은 글자</th></tr>"
    )
    return f"<table class='rows'>{head}{''.join(body)}</table>"


def _chips() -> str:
    parts = []
    for role, (color, label, why) in ROLE_STYLE.items():
        parts.append(
            f"<button class='chip' type='button' aria-pressed='true' data-role='{role}' "
            f"title='{_esc(why)}'><span class='dot' style='background:{color}'></span>"
            f"{_esc(label)}</button>"
        )
    parts.append(
        "<span class='legend'>박스나 줄을 누르면 서로 짚어준다 · "
        "칩을 누르면 그 판정만 숨긴다</span>"
    )
    return f"<div class='bar'>{''.join(parts)}</div>"


def _llm_section(trace: dict) -> str:
    sent = trace.get("sent_to_llm", {})
    roles = trace.get("header_roles", {})
    rows = []
    for group, texts in (
        ("제목으로 보냄", sent.get("headers", [])),
        ("모호해서 같이", sent.get("ambiguous", [])),
    ):
        for text in texts:
            role = roles.get(text)
            shown = _esc(role) if role else "<i style='color:var(--mut)'>제목 아님</i>"
            rows.append(
                f"<tr><td class='num'>{_esc(group)}</td><td>{shown}</td>"
                f"<td class='txt'>{_esc(text)}</td></tr>"
            )
    head = "<tr><th>보낸 묶음</th><th>돌아온 역할</th><th>글자</th></tr>"
    return f"<div class='scroll'><table class='plain'>{head}{''.join(rows)}</table></div>"


def _blocks_section(trace: dict) -> str:
    parts = []
    for block in trace.get("blocks", []):
        header = block["header"] or "(제목 없이 시작한 묶음)"
        role = block["role"] or "제목 아님"
        cls = "blk" if block["scored"] else "blk off"
        mark = "채점에 들어감" if block["scored"] else "채점에서 빠짐"
        items = "".join(f"<li>{_esc(text)}</li>" for text in block["items"])
        body = f"<ul>{items}</ul>" if items else "<div class='none'>항목 없음</div>"
        parts.append(
            f"<div class='{cls}'><div class='hd'><b>{_esc(header)}</b>"
            f"<span class='tag' style='background:#52525b'>{_esc(role)}</span>"
            f"<span style='color:var(--mut);font-size:12px'>{_esc(mark)}</span>"
            f"</div>{body}</div>"
        )
    return "".join(parts)


def _requirements_section(trace: dict) -> str:
    parts = []
    for req in trace.get("requirements", []):
        box = req.get("source_bbox") or {}
        span = req.get("source_span") or {}
        ids = req.get("line_ids", [])
        meta = (
            f"{req['id']} · 근거등급 {req.get('evidence_grade', '?')} · "
            f"사다리 {req.get('ladder_step', '?')}단계 · "
            f"이미지 y {box.get('y1', '?')}~{box.get('y2', '?')} · "
            f"글자 {span.get('start', '?')}~{span.get('end', '?')} · 줄 {', '.join(ids)}"
        )
        parts.append(
            f"<div class='req' data-lines='{_esc(','.join(ids))}'>"
            f"<div class='meta'>{_kind_tag(req['kind'])} {_esc(meta)}</div>"
            f"<div class='body'>{_esc(req['text'])}</div></div>"
        )
    return "".join(parts)


def render(trace: dict) -> str:
    """`parse_posting(trace=...)`이 채운 것을 그대로 펼친다."""
    band = trace.get("band")
    where = (
        f"직무 「{band['label']}」 · 이미지 y {band['y_top']}~{band['y_bottom']}만 채점 대상"
        if band
        else "직무 분할 없음 — 공고 전체가 채점 대상"
    )
    images = trace.get("images") or []
    src = _data_uri(Path(images[0])) if images else None
    calls = trace.get("report", {}).get("llm_calls", 0)
    return (
        f"<style>{_CSS}</style>"
        f"<div class='wrap'>"
        f"<h1>{_esc(trace.get('posting_id', '?'))} 파싱 점검</h1>"
        f"<p class='note'>{_esc(where)} · <b>이미지는 LLM에 보내지 않는다.</b> "
        f"모델이 본 것은 2절의 문자열이 전부다.</p>"
        f"{_stats(trace)}"
        f"<h2>1. 원본 위에 얹은 인식 결과<span class='n'> · 왼쪽 이미지, 오른쪽 같은 줄</span></h2>"
        f"<p class='note'>흐린 줄은 다른 직무의 칸이라 잘라낸 것이다. "
        f"조건이 된 줄에는 <span class='rid'>R-00</span> 표가 붙는다.</p>"
        f"{_chips()}"
        f"<div class='split'>"
        f"<div class='pane'>{_figure(trace, 1, src)}</div>"
        f"<div class='pane'>{_rows(trace)}</div>"
        f"</div>"
        f"<h2>2. LLM에 보낸 것과 돌아온 것<span class='n'> · 호출 {calls}회</span></h2>"
        f"<p class='note'>보내는 것은 문자열뿐이고, 정하는 것은 「이 제목이 무슨 성격인가」 "
        f"하나다. 필수·우대 판정과 좌표는 코드가 한다.</p>"
        f"{_llm_section(trace)}"
        f"<h2>3. 섹션으로 묶인 결과</h2>"
        f"<p class='note'>흐린 묶음은 조건이 아니다 — 담당업무·회사 소개·전형 절차는 "
        f"지원자에게 요구되는 조건이 아니므로 채점에서 뺀다.</p>"
        f"{_blocks_section(trace)}"
        f"<h2>4. 최종 조건<span class='n'> · requirements.json에 그대로 들어간다</span></h2>"
        f"<p class='note'>누르면 1절에서 그 조건이 나온 자리를 짚어준다. "
        f"좌표 없는 조건은 이미지에서 나온 것이 아니므로 검산이 차단한다.</p>"
        f"{_requirements_section(trace)}"
        f"</div>"
        f"<script>{_JS}</script>"
    )


def dump_json(trace: dict) -> str:
    """같은 내용을 기계가 읽는 형태로. 화면과 대조할 때 쓴다."""
    return json.dumps(trace, ensure_ascii=False, indent=2)
