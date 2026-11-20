# jobMate — 작업 기준

## 필수 참고 문서 (작업 전/작업 시 항상 확인)

- `docs/harness/05_내부기능_통합.md` — 현재 아키텍처·엔드포인트·설정 전체
- `docs/harness/06_호스팅_필수_로깅.md` — 호스팅 시 필수 로그 목록.
  **새 기능/수정 시 이 문서의 체크리스트를 적용하고, 새 비동기·외부 호출 경로에는
  필수 로그를 추가한다. 토큰·이메일 본문·push endpoint 원문은 절대 로깅 금지.**

## 검증 명령

- 패키지 매니저: `uv` (Python >=3.12)
- 테스트: `uv run pytest` (UI 테스트는 Playwright chromium 필요 — `uv run playwright install chromium`)
- 린트/포맷: `uv run ruff check src/jobmate tests` / `uv run ruff format --check src/jobmate tests`
- 타입: `uv run mypy src/jobmate`
- 코드 수정 후: `graphify update .` (graphify-out/ 갱신)

## 주요 구조

- `src/jobmate/api/app.py` — FastAPI 대시보드 API + `_automation_loop` + `_sync_cronjob`
- `src/jobmate/coding_worker.py` — 코딩 job 큐/lease/재시도/워커 (`jobmate coding-worker` 독립 실행 가능)
- `src/jobmate/api/static/index.html` — 단일 파일 대시보드 UI
- `src/jobmate/store/db.py` — SQLite/PostgreSQL 공용 Store (스키마 이중 정의)
- `src/jobmate/security.py` — API 키 인증 + Fernet 암호화

## 주의

- 비밀값·토큰은 로그/커밋 금지. `.env` 미추적.
- `scheduled_events.status`: candidate/needs_review/needs_confirmation/registered/rejected
- 코딩 job write는 `lease_owner` 펜싱 필수 — stale worker가 상태를 덮어쓰면 안 됨.
