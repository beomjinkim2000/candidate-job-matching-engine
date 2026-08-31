# scripts/ — Harness 실행 스크립트

`jha0313/harness_framework`에서 가져온 스켈레톤이다. **직접 작성한 코드가 아니다.**

| 파일 | 용도 |
|---|---|
| `execute.py` | `phases/{task-name}/step{N}.md`를 순차 실행. 브랜치 생성, 가드레일 주입, 컨텍스트 누적, 실패 시 3회 재시도, 2단계 커밋 |
| `test_execute.py` | 위의 테스트 |

## 사용법

```bash
python3 scripts/execute.py {task-name}          # 순차 실행
python3 scripts/execute.py {task-name} --push   # 실행 후 push
```

워크플로우 전체(탐색 → 논의 → Step 설계 → `phases/` 파일 생성 → 실행)는
`.claude/commands/harness.md`를 따른다.

## 주의

- **이 디렉터리는 우리 과제 코드가 아니다.** 매칭 엔진 구현은 `src/`에 둔다.
- `execute.py`의 AC 예시가 `npm run build` / `npm test`로 되어 있다 (원본이 Next.js 기준).
  step 파일을 쓸 때 **`pytest` / `ruff`로 바꿔 쓴다.**
- 에러 복구: `phases/{task}/index.json`에서 해당 step의 `status`를 `"pending"`으로 되돌리고
  `error_message`를 지운 뒤 재실행한다. `blocked`면 `blocked_reason`을 해결한 뒤 같은 방식.

## Harness 프로젝트 설정

`harness.toml`이 정본이다. 수정 후 `harness sync`를 돌려 `.claude-plugin/` 하위 파일을
재생성한다. 상태 점검은 `harness doctor` (전 항목 통과 상태를 유지한다).
