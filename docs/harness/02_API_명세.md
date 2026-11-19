# jobMate 하네스 MVP API 명세 (FastAPI, 임시)

| 항목 | 값 |
|---|---|
| 문서 버전 | 0.2 |
| 기준일 | 2026-11-19 |
| API 기준 | `/api/v1`, JSON |
| 구현 | `src/jobmate/api/app.py` |
| 상위 문서 | [01_하네스_MVP_개발문서.md](01_하네스_MVP_개발문서.md), [06_API_설계서](../jobMate_v1.0.0/06_API_설계서_v1.0.0.md) |

> 이 API는 상용 전 Django 전환 전 임시 제공층이다. 인증·조직 권한·OAuth 관리는 포함하지 않는다.
> 오류 형식·상태 코드 원칙은 v1.0.0 API 설계서를 따른다.

## 문서 이력

| 버전 | 기준일 | 수정 내용 |
|---|---|---|
| 0.1 | 2026-11-19 | 최초 작성(파이프라인 단일 구조 기준) |
| 0.2 | 2026-11-19 | 엔진 중심 재구성에 맞춰 엔드포인트 확정(runs/승인/event-links/gmail-calendar) |

## 1. 공통 규칙

- 성공 응답은 JSON. 오류 응답: `{"error": {"code": "...", "message": "...", "request_id": "..."}}`.
- 비동기 실행은 `202 Accepted`. `request_id`는 `x-request-id` 헤더 또는 생성 UUID.
- `limit` 기본 50.

## 2. 엔드포인트

### 2.1 GET `/` 

상태 페이지(`api/static/index.html`) 서빙. run 제출·목록·상세·승인 UI.

### 2.2 GET `/api/v1/health`

`200`: `{"status": "ok"}`

### 2.3 GET `/api/v1/workflows`

`200`: `[{"key": "dev_review", "version": "0.1.0"}, {"key": "gmail_calendar", "version": "0.1.0"}]`

### 2.4 POST `/api/v1/runs` — `202`

본문: `{"workflow": "dev_review", "task": "...", "metadata": {}}`
- `workflow`는 등록된 키만 허용 — 아니면 `404 unknown_workflow`.
- `task`는 1~20000자 — 위반 시 `422`.
- run 생성 후 백그라운드 스레드에서 `WorkflowRunner.invoke` 실행.

응답 `202`: `{"id": "...", "status": "queued"}`

### 2.5 GET `/api/v1/runs?limit=50`

run 목록(최신순): `[{"id","workflow","status","created_at"}]`

### 2.6 GET `/api/v1/runs/{run_id}`

run 상세:
```json
{
  "id": "...", "workflow": "dev_review", "version": "0.1.0",
  "status": "waiting_approval", "trace_id": "...", "error": "",
  "input": {}, "metadata": {"pr_summary": "..."},
  "created_at": "...", "updated_at": "...",
  "stages": [{"stage_key": "...", "attempt": 1, "status": "succeeded",
              "input": {}, "output": {}, "error": "", "model": "...",
              "tokens_in": 0, "tokens_out": 0, "started_at": "...", "finished_at": "..."}],
  "approvals": [{"decision": "approve", "comment": "...", "decided_at": "..."}],
  "audit": [{"actor": "...", "action": "...", "object_ref": "...", "detail": {}, "created_at": "..."}]
}
```
없으면 `404 not_found`.

### 2.7 POST `/api/v1/runs/{run_id}/approve`

본문: `{"decision": "approve|reject", "comment": "..."}`
- run이 `waiting_approval`이 아니면 `409 invalid_state`.
- `runner.resume(run_id, {decision, comment})`로 LangGraph interrupt 재개.
- 완료/거부 후 최신 run 상세 반환(`200`).

### 2.8 GET `/api/v1/notifications?limit=50`

알림 목록: `[{"id","run_id","link_id","title","body","source_url","status","created_at","sent_at"}]`

### 2.9 GET `/api/v1/event-links?limit=50`

이벤트 링크 이력: `[{"id","idempotency_key","gmail_message_id","status","calendar_event_id","notification_id","source_url","attempts","last_error","created_at","updated_at"}]`

### 2.10 GET `/api/v1/gmail-calendar`

자동화 상태: `{"enabled": bool, "last_poll_at": ..., "last_poll_result": "..."}`

### 2.11 POST `/api/v1/gmail-calendar/toggle`

본문 `{"enabled": bool}` → `{"enabled": bool}`. `automation_state` 테이블에 저장.

### 2.12 POST `/api/v1/gmail-calendar/poll`

`202`: 수동 폴링 요청. MVP는 어댑터 미구성 시 안내 응답 —
실제 폴링은 gmail_calendar 워크플로 run을 통해 엔진에서 실행된다.

## 3. 오류 코드

| HTTP | code | 상황 |
|---|---|---|
| 404 | `unknown_workflow` | 미등록 workflow 키 |
| 404 | `not_found` | run 없음 |
| 409 | `invalid_state` | waiting_approval이 아닌 run에 승인 요청 |
| 422 | (FastAPI 기본) | 요청 본문 검증 실패 |
| 500 | `resume_failed` | resume 실행 예외 |

## 4. 내부 상태 코드

`runs.status`: `queued | running | waiting_approval | waiting_review | completed |
failed | rejected | cancelled | budget_exhausted | needs_human`
`stage_runs.status`: `pending | running | succeeded | failed | skipped`
`event_links.status`: `discovered | extracted | validated | calendar_created |
notification_queued | notified | needs_review | duplicate | connection_error | retry_wait`
`notifications.status`: `pending | sent | failed`

## 5. 구현 노트

- `create_app(settings=..., store=..., llm=...)`로 fake 주입 테스트 가능 —
  `tests/api/test_api.py`가 `FakeLLM`으로 전체 HTTP 흐름을 검증한다.
- run 실행은 `threading.Thread` 데몬 — MVP 단일 프로세스 전제.
  다중 워커 환경에서는 Django 전환 시 task queue로 교체한다.
- 승인 resume은 동기 실행 — LLM 재호출 없이 interrupt 이후 단계만 진행한다.
