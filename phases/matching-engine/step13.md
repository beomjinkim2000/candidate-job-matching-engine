# Step 13: handoff

> **2026-09-01 02:40 KST 신설.** R1 심사에서 두 축이 「담당 step이 없어 완주해도 빈 칸으로
> 끝난다」는 이유로 상한이 잘렸다 — **F축(유지·확장) 상한 4/6, G축(전달력) 상한 8/10.**
> 문서를 더 다듬어서 오르는 점수가 아니라 **일을 하는 자리가 없어서** 안 오르는 점수였다.
> 이 step이 그 자리다.

## 읽어야 할 파일

- `docs/COST_BUDGET.md` — §4 실측 칸. **완주 후 채워져 있어야 한다**
- `docs/SCHEDULE.md` — §1 시각표. 실제 소요와 대조한다
- `docs/HARNESS_SCOREBOARD.md` — 심사에서 나온 「가장 아픈 질문 3개」
- `phases/matching-engine/index.json` — 전 step의 `summary`와 `requirement_status`
- `data/runs/*/result.json` — 완주 결과

## 전제조건

**완주가 끝난 뒤에만 실행한다.** `data/runs/`에 결과가 없으면 이 step은 `blocked`다 —
실측 없이 쓰면 이 step이 만드는 문서가 또 하나의 미래시제 문서가 된다.

## 작업

### 13-A. `docs/COST_BUDGET.md` §4 실측 채우기

`data/.judge_usage.json`에서 옮겨 적는다. **추정치를 지우고 실측으로 바꾼다.**

| 채울 것 | 출처 |
|---|---|
| 실제 호출수 | `.judge_usage.json` |
| 실제 입력/출력 토큰 | 〃 |
| 실제 USD | 〃 (단가 × 토큰) |
| 벽시계 소요 | step별 `started_at` ~ `completed_at` 차 |

**13-A와 13-D는 합쳐 20분이고 「숫자를 옮겨 적는 일」이다. 시간이 없어서 못 하는 게
아니라 잊어서 못 한다.** 13-B·13-C보다 **먼저** 한다.

§3 추정표 옆에 **실측을 나란히 놓고 어긋난 항목에 이유를 적는다.**
「추정이 맞았다」보다 「어디서 얼마나 틀렸다」가 다음 사람에게 쓸모 있다.

### 13-B. `docs/OPERATIONS.md` — 유지 주체와 운영 비용

**F축이 비었다고 지적한 칸이다.** 다음 넷을 채운다.

1. **누가 유지하나** — 이건 채용 과제 제출물이다. 유지 주체가 「없음」이면 그렇게 적는다.
   프로덕션으로 갈 때 필요한 역할을 나열하고 **지금은 아무도 없다**를 명시한다.
   있는 척하는 것보다 낫다
2. **월 운영 비용** — `docs/COST_BUDGET.md` §6의 규모 계산(고객사 3곳 × 월 500명 = 채점만
   월 6,000회)을 실측 단가로 환산한다
3. **규모의 경제가 없다는 사실** — 이력서마다 토큰이 새로 들어가므로 **1건당 단가가
   안 내려간다.** 캐시가 먹는 것은 공고 파싱뿐이고 그건 원래 싸다.
   **이건 이 설계의 구조적 한계이지 튜닝으로 없앨 수 있는 게 아니다**
4. **버리는 순서 3번째까지** — `docs/COST_BUDGET.md` §5 표를 여기서도 참조한다.
   1번 12-C, 2번 12-B, **3번 3번째 심사위원**(이견 임계를 올린다).
   4번부터는 「줄인다」가 아니라 **다른 시스템이 된다**

**프로덕션 경로(`ClientFeedSource`)의 운영 비용은 별도로 적는다** — 고객사가 이미지를
push하므로 크롤링 비용이 0이고, 대신 **수신 인터페이스 유지**가 생긴다.
데모 경로와 프로덕션 경로의 비용 구조가 다르다는 것을 표로 나란히 놓는다.

#### 「다음 사람이 할 일」 절 — 이번 24시간에 못 닫은 것을 넘긴다

`docs/SCHEDULE.md` §5가 이 자리를 지목한다. 넘길 것:

| 넘기는 것 | 지금 상태 | 다음 사람이 할 일 |
|---|---|---|
| **반복 안정성 σ 실측 → 배점 재검토** | `index.json` step 6의 `summary`에 **N=11 실측 σ가 적혀 있다** | σ > 0.5면 판단 층 65점을 사실 층으로 옮기는 논의. **문헌을 다시 봐야 하는 일이라 24시간에 못 한다** |
| 임의 임계 3개 확정 | `unaddressed_tolerance` 0.15 · `ledger_degraded_ratio` 0.5 · 장황함 10% | 실측 분포가 `index.json` step 11·12 `summary`에 있다 |
| 순환(G3)을 끊는 것 | 좁혔고 못 끊었다 (`tests/CLAUDE.md`) | 사람이 채점한 외부 정답 데이터 |
| VLM 폴백 | `NotImplementedError` (`step3.md` 3-E) | OCR이 못 읽는 공고를 만나면 필요해진다 |

**σ 값을 여기 옮겨 적어라.** 그게 이 표의 첫 행이 존재하는 이유다.

> ⚠️ **13-B를 버리면 이 절도 사라진다.** 그때 σ는 `index.json` step 6의 `summary`에만
> 남는다 — **지워지지는 않는다.** `docs/SCHEDULE.md` 「step 13이 밀릴 때」가 13-B를
> 1번으로 버리게 해 뒀고, 그 대가가 이것이다. 알고 버리는 것과 모르고 잃는 것은 다르다.

### 13-C. `docs/DEMO.md` — 심사위원이 5분 안에 확인하는 경로

**G축이 지적한 「실행 한 줄이 두 갈래」의 해소.** 한 줄은 `python run.py`다.

적을 것:

1. **클론 후 붙여넣을 줄 3개** — `git clone` / `pip install -e .` / `python run.py`.
   **4개가 되면 안 된다**
2. **화면에서 볼 순서** — 공고 선택 → 조건 승인 → 채점 → 랭킹 → 근거 클릭 → 공고 이미지 네모
3. **예상 질문과 답** — R1·R2 심사에서 나온 「가장 아픈 질문」을 그대로 쓴다.
   질문을 고르지 말고 **아픈 것부터** 적는다. 대표 3개:
   - 요구 ②의 정본이 어느 문서인가 (→ OCR. VLM 경로는 폐기했고 그 기록이 `step3.md` 상단에 있다)
   - 키가 없으면 「API로 확보했다」를 무엇으로 확인하나 (→ `provenance.json`의 해시)
   - 완주 1회에 몇 회 · 얼마인가 (→ `docs/COST_BUDGET.md` §4 실측)
4. **못 하는 것 목록** — 표 셀 소속·시각 강조 신호 부재·순환을 못 끊은 것·
   외부 정답 데이터 없음. **묻기 전에 먼저 적는다**

### 13-D. 요구 ①~⑧ 상태 표 — **5분. 절대 버리지 않는다**

전 step의 `requirement_status`와 AC 결과를 모아 **요구 ①~⑨가 각각 어디까지 갔는지 한 표로**
만들어 `docs/DEMO.md`에 넣는다.

**13-C(`DEMO.md`)를 버렸으면** 이 표는 `phases/matching-engine/index.json`의 **phase 레벨에
`requirement_summary` 키**로 넣는다. 파일을 새로 만들지 마라 (`docs/SCHEDULE.md` 「step 13이
밀릴 때」). **이 표가 없으면 제출물이 요구 9개 중 무엇을 충족했는지가 아무 데도 없다.**

**요구는 9개다.** 과제문에서 「CLI 또는 API 엔드포인트 하나로 실행」과 「결과를 확인할 수
있는 간단한 UI」는 **나란한 독립 불릿**이다. ⑥에 묶어 세면 UI가 회계에서 사라진다.

```
① API 크롤링      verified | unverified   ← data/.saramin_quota.json 의 200 응답
② 이미지 파싱      verified                ← requirements.json 2개 (step3이 갱신)
③ 0~100 + 근거     verified                ← result.json + explain() 출력 AC (step7)
④ 랭킹 정렬        verified                ← ranked + gate_failed == 6 (step12 AC)
⑤ 직군 무관       verified                ← 직군 교차 테스트 실측 하락폭
⑥ 단일 진입점     verified                ← python run.py (step8)
⑦ 결과 확인 UI    verified                ← step9 AC. 화면 구현은 사용자 담당
⑧ 공고 2 × 6명    verified                ← step10 AC
⑨ 법적 구조 설계만 verified                ← ClientFeedSource가 NotImplementedError
```

**⑦의 판정 기준을 흐리지 마라.** 화면의 디자인은 사용자가 하지만, **`step9.md`의 AC가
통과하는 최소 화면**은 하네스가 만든다. AC가 안 돌면 `⑦`은 `verified`가 아니다.

**`unverified`를 `verified`로 올려 적지 마라.** 그게 R1에서 veto를 받은 바로 그 행동이다.

## Acceptance Criteria

```bash
python3 -c "
import pathlib,re
c=pathlib.Path('docs/COST_BUDGET.md').read_text()
assert '(미측정)' not in c, 'COST_BUDGET §4 실측이 안 채워졌다'
for f in ('docs/OPERATIONS.md','docs/DEMO.md'):
    assert pathlib.Path(f).exists(), f
d=pathlib.Path('docs/DEMO.md').read_text()
fence=chr(96)*3
assert d.count(fence)>=2, '붙여넣을 명령 블록이 없다'
assert 'python run.py' in d, '실행 한 줄이 없다'
assert '못 하는 것' in d, '한계 목록이 없다'
print('ok')
"
# README는 **사용자가 직접 쓴다**. 하네스가 만들지 않았는지만 본다.
# 존재 자체는 위반이 아니다 — 과제 제출물이 「코드 + README」다.
python3 - <<'PYEOF'
import subprocess, pathlib
p = pathlib.Path("README.md")
if not p.exists():
    print("README.md 없음 — 하네스는 만들지 않는다. 17:00 전까지 사용자가 작성한다")
else:
    log = subprocess.run(["git","log","--format=%an|%s","--","README.md"],
                         capture_output=True, text=True).stdout
    bad = [l for l in log.splitlines() if "Claude" in l]
    assert not bad, f"하네스가 README를 만들었다 — 위반: {bad[:2]}"
    print(f"README.md 존재 ({len(p.read_text())}자) — 사용자 작성. 정상")
PYEOF
git ls-files | grep -E "^\.env$|\.env\." && echo "키 파일 커밋됨 — 위반" && exit 1 || echo ".env 미포함"

# --- 제출물이 실제로 레포에 들어갔는가 (clone한 사람이 보는 것) ---
python3 - <<'PYEOF'
import subprocess
tracked = set(subprocess.run(["git","ls-files"], capture_output=True, text=True).stdout.split())
def n(pat): return sum(1 for f in tracked if __import__("fnmatch").fnmatch(f, pat))
must_be_tracked = {
    "data/postings/*/provenance.json":  2,   # 해시만. 원문 아님
    "data/postings/*/requirements.json":2,   # 구조화된 조건 (= 산출물)
    "data/resumes/*/*.json":           14,   # 이력서 12 + index 2
    "data/runs/*/result.json":          1,   # 실행 결과
}
bad = {p:(n(p),k) for p,k in must_be_tracked.items() if n(p) < k}
assert not bad, f"레포에 안 들어간 제출물: {bad}"

# 공고 본문은 그림이든 글자든 들어가면 안 된다
leaked = [f for f in tracked if "/img_" in f or f.endswith("/ocr.json")]
assert not leaked, f"공고 원문이 커밋됐다: {leaked[:3]}"
print("산출물 전부 tracked · 공고 원문(이미지·전사본) 미포함")
PYEOF
```

**이 검사가 V6의 반증 조건이다.** 「이미지는 `.gitignore`」를 `data/` 통째로 확대 해석하면
`provenance.json`·`ocr.json`·`requirements.json`·목업 이력서·실행 결과가 전부 빠지고,
**clone한 사람이 확인할 수 있는 게 아무것도 없어진다.** 패턴은 `step0.md`에서 파일 단위로
못박았고, 여기서 **실제로 tracked인지**를 확인한다. 두 자리가 다 있어야 막힌다.

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 체크리스트:
   - `docs/COST_BUDGET.md` §4에 **실측**이 들어갔는가? (추정치를 옮겨 적은 게 아니라)
   - `docs/OPERATIONS.md`에 유지 주체가 **「없다」라도 적혀** 있는가?
   - `docs/DEMO.md`의 명령이 **3줄 이하**인가?
   - 요구 표에 `unverified`가 정직하게 남아 있는가?
3. `index.json`의 step 13을 업데이트한다.

## 금지사항

- **`README.md`를 만들지 마라.** 이 step이 만드는 건 `docs/` 아래 세 파일이다.
  README는 지원자 본인이 이 소재로 직접 쓴다. 「초안을 만들어 두면 편하겠다」는
  생각이 드는 자리가 정확히 여기다 — **만들지 마라.**
- **동시에, 사용자가 쓴 `README.md`가 있다고 해서 실패시키지 마라.**
  이유: 과제 제출물이 **「GitHub public 레포 링크 (코드 + README)」**이고
  「README — 실행 방법과 함께, **설계 결정과 트레이드오프를 반드시 서술**해주세요」다.
  **README가 없는 제출물이 오히려 요구 위반이다.** 금지 대상은 「하네스가 쓰는 것」이지
  「파일이 존재하는 것」이 아니다 — 위 AC가 `git log`의 작성자로 그 둘을 가른다.
- **`unverified`를 올려 적지 마라.** 이유: R1에서 veto 2건이 정확히 이 행동에서 나왔다.
- **실측 없이 13-A를 채우지 마라.** 이유: 추정치를 실측 칸에 넣으면 그게 실측으로 읽힌다.
  완주 전이면 이 step은 `blocked`다.
- **「못 하는 것」 목록을 짧게 만들지 마라.** 이유: 심사위원은 어차피 찾아낸다.
  먼저 적은 한계와 들킨 한계는 무게가 다르다.
