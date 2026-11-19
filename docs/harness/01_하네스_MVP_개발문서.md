# jobMate 하네스 MVP 개발문서

| 항목 | 값 |
|---|---|
| 문서 버전 | 0.2 |
| 기준일 | 2026-11-19 |
| 범위 | 6계층 에이전트 하네스 엔진 + dev_review/gmail_calendar 워크플로 + FastAPI 제공층 |
| 스펙 | [specs/001-agent-harness-engine](../../specs/001-agent-harness-engine/) (spec/plan/tasks) |
| 상위 문서 | [기본기획서](../jobMate_v1.0.0/01_기본기획서_v1.0.0.md), [시스템상세설계서](../jobMate_v1.0.0/02_시스템상세설계서_v1.0.0.md) |

## 문서 이력

| 버전 | 기준일 | 수정 내용 |
|---|---|---|
| 0.1 | 2026-11-19 | 최초 작성(단일 gmail_calendar 파이프라인 기준) |
| 0.2 | 2026-11-19 | 엔진 중심 재구성: WorkflowSpec/AgentSpec + dev_review(분석→구현→자동검사→보안검토→코드리뷰→QA→사람승인) + gmail_calendar 동일 엔진 이식, 모델 라우팅, gstack 참조 리뷰 구조 반영 |

## 0. 범위 결정 사항

- **제공층은 FastAPI로 시작한다.** 상용 단계에서는 Django(인증·조직 권한·관리자 기능)로 전환한다.
  도메인 계층(`engine/`, `workflows/`, `models/`, `store/`, `sensors/`, `llm/`, `tools/`, `adapters/`)은
  HTTP 프레임워크에 의존하지 않으며, 전환 시 영향 범위는 `api/`와 `cli.py`로 한정된다.
- **영속 저장소는 SQLite(WAL)로 시작한다.** `store/db.py`가 유일한 접근 지점이며,
  상용 전환 시 PostgreSQL store 구현체 교체로 대응한다.
- **LLM은 OpenAI 호환 chat/completions(httpx)로 호출한다.** 구조화 출력은 프롬프트 JSON 지시 +
  Pydantic 검증(센서)으로 수행한다. 테스트는 `FakeLLM`으로 결정적으로 검증한다.
- **모델 라우팅은 2단계다.** 작업 중요도 기본 티어(`JOBMATE_DEFAULT_MODEL`=경량,
  `JOBMATE_IMPORTANT_MODEL`=강한)와 역할별 서브모델 오버라이드(`JOBMATE_ROLE_MODELS`,
  JSON `{"reviewer": "important"}` 형태)를 `Settings.model_for()`가 해석한다.
  각 `ModelConfig`는 model/base_url/api_key_env/temperature/max_tokens를 가진다.
- **implementer는 변경 제안만 출력한다.** unified diff 텍스트(`ChangeProposal`)를 산출할 뿐
  실제 파일 적용은 하지 않는다(후속 샌드박스 연동). 자동 검사는 baseline 명령 중심이다.
- **Web Push·OAuth 동의 흐름·Gmail history cursor·문서 템플릿은 범위 외**(spec Out of Scope 참조).

## 1. 목표와 완료 조건

head가 배정·분기만 담당하는 멀티 에이전트 실행 엔진을 만들고, 그 위에
개발팀 리뷰 파이프라인(dev_review)과 Gmail→Calendar 자동화(gmail_calendar)를 올린다.
모델이 "완료했다"고 말하는 것이 아니라 DB 레코드·외부 ID·검사 결과가 완료 증거다.

## 2. 아키텍처

```
FastAPI (api/app.py) / CLI (cli.py)
   │  runs 생성·조회·승인 / gmail 자동화 토글·폴링
   ▼
WorkflowRunner (engine/runner.py) — WorkflowSpec을 LangGraph StateGraph로 컴파일
   │  stage node: llm(FunctionContext 경유) / function / human(interrupt)
   ▼
6계층 하네스
  가이드   guide/prompts/*.md (버전 주석) + AgentSpec
  센서     sensors/ — verdict 강등·스키마·diff·시크릿·URL·timezone 검증
  루프     budgets — 토큰/도구/시간/비용 + 리뷰2·전체3·수정5 상한
  메모리   store/db.py SQLite(WAL) + LangGraph SqliteSaver checkpoint
  권한     tools/executor.py allowlist + adapters Protocol 분리
  관찰     stage_runs/audit_events, run_id·trace_id 추적
   │
LLMClient Protocol (OpenAICompatLLM / FakeLLM)
GmailClient·CalendarClient·Notifier Protocol (Fake*/real 후속)
```

### 2.1 계층별 구현 매핑

| 하네스 계층 | 구현 | 위치 |
|---|---|---|
| 가이드 | `WorkflowSpec`/`AgentSpec`/`ModelConfig`(버전·프롬프트·출력 스키마·모델·도구 allowlist) + `guide/prompts/*.md` | `models/spec.py`, `guide/` |
| 센서 | verdict 스키마 검증·approve+blocker 강등·confidence 필터, diff 형식/git apply --check, 시크릿 패턴, Gmail URL 허용목록, 일정 검증(tz/DST/모호/반복) | `sensors/` |
| 에이전틱 루프 | LangGraph StateGraph, 노드 재시도(스키마 실패 자기루프), 시간·토큰·도구·비용 예산, 루프 상한, 소진 시 안전 정지 | `engine/runner.py`, `budgets.py` |
| 메모리 | SQLite: runs, stage_runs, approvals, audit_events, event_links, notifications, automation_state + SqliteSaver checkpoint | `store/db.py` |
| 권한·예산 | `ToolExecutor`가 AgentSpec.tools allowlist를 서버에서 강제(리뷰어=read_diff, QA=read_diff+run_tests). Gmail 읽기/Calendar 쓰기/Notifier는 별도 Protocol | `tools/`, `adapters/` |
| 관찰가능성 | stage_runs(입출력·시도·사용량), audit_events(판정·분기·승인·외부쓰기), trace_id. Langfuse는 연결 지점만 | `store/db.py`, `engine/runner.py` |

### 2.2 dev_review 상태 흐름

```
analysis → implement → auto_checks ──FAIL──→ implement (fix 루프 ≤5)
                │ PASS
                ▼
           security ──request_changes──→ implement (review 루프 ≤2)
                │ approve
                ▼
           code_review ──request_changes──→ implement
                │ approve
                ▼
              qa ──fail──→ implement / ──needs_human──→ finalize
                │ pass
                ▼
           approval (human interrupt) ──approve/reject──→ finalize
```

- 상한: 리뷰성 단계 회귀 `max_review_rounds=2`, 구현 총 실행 `max_implement_rounds=3`,
  자동검사 실패 회귀 `max_fix_rounds=5`. 초과 시 `needs_human`, 예산 소진 시 `budget_exhausted`.
- `finalize`는 run.status를 completed/rejected/failed/needs_human/budget_exhausted로 확정하고
  PR 요약(What changed/Blast radius/Verification Results/Tests 구조)을 run.metadata에 저장한다.

### 2.3 gmail_calendar 상태 흐름

메시지 단위 run(`gmail_message_id` 컨텍스트). 동일 엔진의 stage/run/audit 구조를 사용한다.

```
extract(LLM) → validate(결정적) → register(멱등) → notify
                    │ needs_review/invalid → finalize
                    │ created/recovered/duplicate → notify
                    │ duplicate_pending/retry_wait → finalize
```

- 멱등키: `sha256(connection_id:message_id)[:32]` → `event_links.idempotency_key` UNIQUE.
- `reserve_link`가 pending 행을 원자적으로 확보 — 중복이면 marker 조회로 복구하거나 duplicate.
- Calendar 타임아웃(결과 불명) 시 `find_by_marker` 조회 후에만 채택/재시도 — 재생성 금지.
- 알림 실패는 Calendar 이벤트를 롤백하지 않고 `notification_queued`로 남긴다.
- `source_url`은 서버가 `https://mail.google.com/mail/u/0/#all/{id}`로만 생성,
  `is_safe_source_url`이 https+mail.google.com을 재검증한다.

## 3. 모듈 구조

```
src/jobmate/
  config.py            Settings: DB 경로, 모델 티어, 역할별 오버라이드, 예산, 검사 명령
  models/              enums, spec(Workflow/Agent/Model), outputs(LLM 스키마), records(DB)
  store/db.py          SQLite Store — runs/stage_runs/approvals/audit/event_links/notifications
  sensors/             verdicts, secrets, diffcheck, gmail_url, event_validation
  llm/client.py        LLMClient Protocol, OpenAICompatLLM, FakeLLM, extract_json
  guide/               loader.py + prompts/{analysis,implementer,security_review,
                       code_review,qa,event_extract}.md (# vX.Y 버전 주석)
  tools/executor.py    ToolExecutor — allowlist 강제, 경로 제한, 결과 크기 제한
  budgets.py           BudgetTracker(참조 구현; runner는 state 누적치로 동일 판정)
  engine/
    spec 해석은 models/spec.py, registry.py(Workflow/Agent 등록),
    state.py(HarnessState — JSON 직렬화 가능 필드만),
    runner.py(그래프 빌드·노드·분기·interrupt·finalize),
    functions.py(auto_checks/gmail_* function 단계), schemas.py(출력 스키마 맵)
  workflows/           dev_review.py, gmail_calendar.py, registry_builder.py
  adapters/            protocols.py(Gmail/Calendar/Notifier), fakes.py
  api/app.py           FastAPI 앱 + 라우터 + 정적 페이지 마운트
  api/static/index.html 상태 페이지(미니멀/스위스)
  cli.py               jobmate run/status/approve/workflows
tests/{unit,integration,api}/
```

## 4. 핵심 설계 결정

### 4.1 리뷰·보안·QA 역할 분리 (gstack 참조)

- **코드 리뷰**: "이 코드를 어떻게 더 좋게 할까?" — diff+명세+검사 결과 독립 입력,
  읽기 전용, gstack findings 스키마(severity/confidence/path/line/category/summary/fix/
  fingerprint/specialist). `NO FINDINGS`는 정상 결과. confidence 게이트(very_low 제외).
- **보안 검토**: 증거 우선 — attack_surface/trust_boundary/evidence 필드로
  공격자·경계·영향을 분리 판정.
- **QA**: "이 결과를 내보내도 되는가?" — 검사 결과+findings 해소+요구사항 종합,
  증거 불충분 시 pass 금지(pass/fail/needs_human).
- **결정적 강등**: approve + blocker/major(confidence≠very_low) finding → request_changes
  (`sensors/verdicts.downgrade_approve`). 깨진 JSON·스키마 위반은 재시도 후 실패 —
  approve로 해석하지 않는다.

### 4.2 멱등성 (gmail_calendar)

`reserve_link` UNIQUE 제약 + marker 조회 복구 + Calendar event ID 선저장 후 알림.
상세는 2.3절.

### 4.3 사람 승인

LangGraph `interrupt` — `waiting_approval` 상태에서 프로세스가 멈추고,
`POST /api/v1/runs/{id}/approve`(또는 CLI resume)가 `Command(resume=...)`로 재개한다.
SqliteSaver checkpoint(thread_id=run_id)로 완료 stage는 재실행되지 않는다.

### 4.4 예산과 안전 정지

state에 누적되는 tokens_in/out·tool_calls·cost_usd·started_at과
review/implement/fix_rounds를 매 LLM 단계 진입 전 검사한다.
소진·상한 초과 시 LLM 호출 없이 `budget_exhausted`/`needs_human`으로 finalize.

### 4.5 관찰가능성·비밀 관리

- 모든 stage 입출력·판정·분기·승인·외부 쓰기를 run_id/trace_id로 추적.
- audit detail에는 본문·토큰·비밀값을 넣지 않는다(호출자 책임 + 테스트로 검증).
- `api_key_env`는 환경변수 이름만 저장한다.

## 5. FastAPI 제공층 (임시)

상세는 [02_API_명세.md](02_API_명세.md). 요약:

| Method | Path | 목적 |
|---|---|---|
| GET | `/` | 상태 페이지(정적 HTML) |
| GET | `/api/v1/health` | 상태 확인 |
| GET | `/api/v1/workflows` | 등록 워크플로 목록 |
| POST | `/api/v1/runs` | run 생성·백그라운드 실행(202) |
| GET | `/api/v1/runs`, `/api/v1/runs/{id}` | run 목록/상세(단계·승인·audit 포함) |
| POST | `/api/v1/runs/{id}/approve` | 승인/거부(resume) |
| GET | `/api/v1/notifications` | 알림 목록 |
| GET | `/api/v1/event-links` | 이벤트 링크 이력 |
| GET/POST | `/api/v1/gmail-calendar[/toggle|/poll]` | 자동화 상태·토글·수동 폴링 |

인증·조직 권한은 Django 전환 시 추가한다. 그 전까지는 로컬 바인딩 전제의 MVP API다.

## 6. 비기능·보안

- 외부 호출 타임아웃 필수(OpenAICompatLLM timeout_seconds, subprocess timeout).
- 도구 allowlist는 `AgentSpec.tools` + `ToolExecutor` 이중 강제.
- 프롬프트 인젝션: 메일 본문·작업 지시는 "데이터"로만 취급 — 프롬프트에 명시,
  도구 권한은 allowlist로만 결정(모델 출력이 권한을 바꾸지 못함).
- `source_url` 허용 호스트 재검증, 알림 본문에 메일 본문 미포함.

## 7. 후속 마일스톤 (범위 외)

- Django 전환: 인증·조직·RBAC·관리자 화면, OAuth 동의 흐름, Web Push(SW/VAPID)
- implementer 실제 코드 적용(샌드박스/worktree), PR 자동 생성
- PostgreSQL 이전, Langfuse 실험·회귀 게이트, Gmail history cursor 증분 동기화
- 문서 업무 템플릿 3종, 다중 스페셜리스트 병렬 서브에이전트
- real Gmail/Calendar/Notifier 어댑터(googleapiclient) — Protocol은 준비됨

## 8. 참고 구현

- `calendar-agent`: googleapiclient 클라이언트, 멱등 등록, SQLite WAL 패턴
- `skillRadar`: OpenAI 호환 호출·재시도, LangGraph + SqliteSaver, pydantic-settings
- `gstack`(github.com/garrytan/gstack): 리뷰 스페셜리스트·findings 스키마·confidence 게이트,
  CSO 증거 우선 보안 검토, verify(저장소 선언 검사만 실행), ship(PR 요약 구조)
- `ui-ux-pro-max`: 상태 페이지 디자인 토큰(미니멀/스위스, 단일 액센트)
