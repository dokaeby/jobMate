# Feature Specification: jobMate 에이전트 하네스 엔진 MVP

**Feature Branch**: `001-agent-harness-engine`

**Created**: 2026-11-19

**Status**: Draft

**Input**: 개발팀 업무자동화 하네스 — head 에이전트가 분석→구현→자동검사→보안검토→코드리뷰→QA→사람승인 단계를 배정·분기하는 멀티 에이전트 실행 엔진. Gmail→Calendar 자동화는 동일 엔진 위의 두 번째 워크플로로 이식한다. 리뷰·보안·QA 단계는 gstack(garrytan/gstack)의 역할·판정 구조를 참조하고, 화면은 ui-ux-pro-max 기준 미니멀 디자인을 적용한다.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 개발팀 리뷰 파이프라인 실행 (Priority: P1)

사용자가 자연어 작업 지시(예: "버그 수정 제안")를 제출하면, 엔진이 분석→구현(변경 제안)→결정적 자동 검사→보안검토→코드리뷰→QA 순으로 에이전트를 실행한다. 각 단계는 버전 고정된 지시와 출력 계약을 가지며, 리뷰/보안/QA 판정이 request_changes이면 구현 단계로 되돌아간다(상한 내). 최종 pass 시 사람 승인 대기 상태가 되고, 승인하면 완료·PR 요약 산출물이 생성된다.

**Why this priority**: 이 파이프라인이 하네스 엔진의 존재 이유이며, 가이드·센서·루프·메모리·권한·관찰 6계층을 모두 검증하는 대표 시나리오다.

**Independent Test**: fake LLM/도구로 전체 흐름을 실행해 단계별 상태·분기·승인 게이트를 검증할 수 있다.

**Acceptance Scenarios**:

1. **Given** 작업 지시와 fake 에이전트 응답, **When** 파이프라인 실행, **Then** 분석→구현→자동검사→보안검토→리뷰→QA→승인대기 순으로 stage_runs가 기록되고 각 출력이 스키마 검증을 통과한다.
2. **Given** 리뷰어가 blocker/major finding과 함께 request_changes 반환, **When** 분기 규칙 적용, **Then** 구현 단계로 회귀하고 review_round가 증가한다.
3. **Given** 리뷰 승인 + QA pass, **When** 사람 승인, **Then** run이 completed가 되고 PR 요약 artifact가 생성된다.
4. **Given** 사람이 승인 거부, **When** 거부 결정, **Then** run이 rejected/cancelled로 종료되고 사유가 audit에 남는다.

---

### User Story 2 - Gmail 일정 자동화 (엔진 이식) (Priority: P2)

연결된 Gmail 계정에서 일정 메일을 폴링해, LLM 추출 후보를 결정적 센서가 검증하고 통과한 일정만 Calendar에 멱등 등록한 뒤 알림을 발행한다. 이 흐름은 dev_review와 동일한 엔진의 stage/run/audit 구조로 기록된다.

**Why this priority**: 기존 문서의 핵심 자동화 기능이지만, 엔진 재구성의 수혜 사례일 뿐 독립 가치는 P2다.

**Independent Test**: fake Gmail/Calendar/LLM으로 메시지 1건의 discovered→notified 전체 상태와 중복 방지를 검증할 수 있다.

**Acceptance Scenarios**:

1. **Given** 명확한 일정 메일, **When** 폴링 실행, **Then** Calendar 이벤트 1건 + 알림 레코드 + 원본 메일 URL이 생성된다.
2. **Given** 같은 메시지 재처리, **When** 재폴링, **Then** 신규 이벤트 없이 duplicate로 기록된다.
3. **Given** 모호/반복 일정, **When** 센서 검증, **Then** needs_review 상태로 남고 Calendar 호출이 없다.

---

### User Story 3 - 실행 관찰과 안전 정지 (Priority: P3)

사용자가 FastAPI로 run/stage 상태·알림·이벤트 이력을 조회하고, 예산 소진·루프 상한 초과 시 실행이 완료로 위장하지 않고 안전 정지함을 확인한다.

**Why this priority**: 운영 신뢰성은 중요하지만 MVP 데모 가치는 P3.

**Independent Test**: 예산을 인위적으로 작게 설정해 소진 시나리오를 재현한다.

**Acceptance Scenarios**:

1. **Given** 도구 호출 상한 초과 상황, **When** 실행, **Then** run이 budget_exhausted로 종료되고 부분 stage 결과가 보존된다.
2. **Given** 리뷰 회귀 3회째, **When** 상한 도달, **Then** 추가 LLM 호출 없이 needs_human으로 에스컬레이션된다.

---

### Edge Cases

- LLM이 verdict JSON 대신 자유 텍스트/깨진 JSON 반환 → 스키마 검증 실패, 재시도 후 실패 처리(approve로 간주하지 않음)
- 리뷰어가 approve인데 findings에 blocker/major 포함 → 센서가 request_changes로 강등
- worker/프로세스 중단 후 재시작 → 완료 stage 반복 없이 checkpoint부터 재개, 외부 쓰기 중복 없음
- Calendar 생성 API 타임아웃(결과 불명) → marker(event ID) 조회 후에만 채택/재시도
- 구현 단계가 diff를 생성하지 못함 → 자동 검사에서 실패 처리, 리뷰로 진행하지 않음
- 프롬프트 인젝션이 포함된 입력/메일 → 데이터로만 취급, 도구 권한·정책 변경 불가

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 시스템은 버전이 고정된 워크플로 정의(단계 목록·분기 규칙·에이전트 스펙)로 실행을 구성해야 한다(MUST).
- **FR-002**: 각 에이전트 단계는 역할별 모델·프롬프트·출력 스키마·허용 도구 목록을 가져야 하며, 설정은 env/파일로 주입 가능해야 한다. 모델 라우팅은 2단계여야 한다: 작업 중요도에 따른 기본 티어(일반 작업=경량 모델, 중요 작업=강한 모델)와 역할별 서브모델 오버라이드(분석/구현/보안/리뷰/QA 각각 독립 지정 가능). 각 모델 설정은 model·base_url·api_key_env·temperature·max_tokens를 포함해야 한다.
- **FR-003**: 분석·구현·보안검토·코드리뷰·QA 단계의 구조화 출력은 스키마 검증을 통과해야 하며, 실패 시 재시도 후에도 실패하면 run을 실패 처리해야 한다. 판정형 출력은 gstack식 findings 스키마(severity, confidence, path, line, category, summary, fix, fingerprint, specialist)를 포함해야 한다.
- **FR-004**: 리뷰/보안/QA 판정이 request_changes/fail이면 구현 단계로 회귀해야 하며, 회귀 사유(판정·findings)가 다음 구현 입력에 포함되어야 한다.
- **FR-005**: 리뷰어가 approve를 반환해도 findings에 blocker/major가 있으면 시스템이 request_changes로 강등해야 한다(결정적 센서).
- **FR-006**: 사람 승인 단계는 LangGraph interrupt로 구현되어 승인/거부 전까지 후속 단계를 실행하지 않아야 한다.
- **FR-007**: 루프 상한 — 리뷰성 단계 회귀 최대 2회, 구현 단계 총 실행 최대 3회 — 초과 시 needs_human으로 에스컬레이션하고 LLM 호출을 중단해야 한다. 단계별 실패는 3회 재시도 후 에스컬레이션한다.
- **FR-008**: 각 run은 단계별 입력·출력·상태·시도·오류·토큰 사용량을 영속 기록해야 하며, run 단위 trace_id로 추적 가능해야 한다. 리뷰 지적과 그 해소 여부(수정됨/기각/보류)도 감사 로그에 남겨야 한다.
- **FR-009**: 도구 호출은 에이전트별 allowlist로 서버에서 강제해야 한다(리뷰어=읽기 전용, 보안검토=읽기+diff 범위 한정, QA=읽기+테스트 실행, 구현=변경 제안만). 각 단계의 완료 상태는 DONE/DONE_WITH_CONCERNS/BLOCKED/NEEDS_CONTEXT 프로토콜로 보고되어야 한다.
- **FR-010**: 자동 검사 단계는 모델 없이 결정적 검사(diff 적용 가능성, 시크릿 패턴, 설정된 lint/테스트 명령)만 수행해야 하며, 저장소가 선언한 검사만 실행하고 테스트 약화·스킵으로 통과시키지 않아야 한다. FAIL 시 수정 루프는 최대 5회다.
- **FR-011**: Gmail→Calendar 워크플로는 메시지 단위 run으로 동일 엔진에서 실행되고, 이벤트 링크 멱등 키·pending 레코드·marker 조회 규칙을 유지해야 한다.
- **FR-012**: FastAPI 제공층은 run 생성·상태 조회·승인/거부·알림/이벤트 이력·자동화 토글·수동 폴링·최소 상태 화면 엔드포인트를 제공해야 한다(임시, Django 전환 예정).
- **FR-013**: 모든 외부 LLM·Google 호출은 Protocol 뒤에 두어 fake 주입 테스트가 가능해야 한다.
- **FR-014**: 토큰·메일 본문·비밀값은 로그·audit·관찰 이벤트에 기록하지 않아야 한다. 보안 findings의 상세(경로·라인·재현)는 공개 산출물(PR 요약)에서 제외하고 내부 기록으로만 남긴다.
- **FR-015**: 코드리뷰 단계는 스페셜리스트 리뷰 구조를 따라야 한다 — 체크리스트 기반 다중 관점(보안·테스트·유지보수·성능·API 계약·상태 머신·레드팀) findings를 수집하고, `NO FINDINGS`를 정상 결과로 인정하며, confidence 게이트(7+ 표시/5-6 주의/3-4 부록/1-2 제외)와 다중 스페셜리스트 확인 부스트를 적용해야 한다. 리뷰어는 구현 대화 컨텍스트를 보지 않는 독립 입력(diff+명세+검사 결과)만 받아야 한다.
- **FR-016**: 보안검토 단계는 증거 우선 방식을 따라야 한다 — severity·confidence·evidence(후보 가설/정적 근거/재현)를 분리 판정하고, 공격자·경계·영향·반증 검토를 거친 supported findings만 보고하며, 각 후보를 독립적으로 재검증(challenge)해야 한다.
- **FR-017**: QA 판정은 증거 기반이어야 한다 — 자동 검사 결과·리뷰 지적 해소 여부·요구사항 충족을 종합해 pass/fail/needs_human을 판정하고, 불충분한 증거를 pass로 처리하지 않아야 한다.
- **FR-018**: 완료 시 PR 요약 산출물(gstack ship 방식: What changed/Blast radius/Verification Results/Tests 구조)을 생성하고, 사람 승인자가 확인할 수 있도록 run 상세에 첨부해야 한다.
- **FR-019**: 제공되는 화면(상태·알림·실행 이력)은 ui-ux-pro-max 기준의 미니멀·스위스 스타일을 따라야 한다 — 단일 액센트, 그리드·여백 중심, 그림자·그라데이션·AI 전형 장식 배제, 상태는 색상 외 텍스트/아이콘 병행.

### Key Entities

- **WorkflowSpec**: 워크플로 키·버전·단계 목록·분기 규칙·루프 상한
- **StageSpec/AgentSpec**: 단계 키·종류(llm/function/human)·역할·모델 설정·프롬프트 참조·입출력 스키마·도구 allowlist
- **Run**: workflow 참조·입력·상태(queued/running/waiting_approval/completed/failed/rejected/budget_exhausted/needs_human)·trace_id·시각
- **StageRun**: run 소속·단계·시도·입출력·오류·사용량
- **Verdict**: 리뷰/보안/QA 판정 구조체(approve|request_changes|pass|fail|needs_human, findings, summary)
- **Finding**: gstack식 리뷰 발견(severity, confidence, path, line, category, summary, fix, fingerprint, specialist, disposition)
- **Approval**: 승인 대상 run·단계·결정·의견·시각
- **EventLink/Notification**(gmail 워크플로): 멱등 키·calendar_event_id·알림 상태·source_url
- **AuditEvent**: 행위자·행위·대상·시각 (본문/토큰 제외)

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: fake 에이전트로 dev_review 전체 흐름(승인 포함)이 결정적으로 1회 실행에 완료된다.
- **SC-002**: request_changes 판정 시 구현 단계로의 회귀가 정확히 발생하고, 상한(리뷰 2회/전체 3회) 도달 시 추가 LLM 호출 없이 needs_human이 된다.
- **SC-003**: 스키마 위반 verdict·깨진 JSON이 approve로 처리되는 사례가 0건이다(센서 강등 포함).
- **SC-004**: 동일 Gmail 메시지 재처리 시 Calendar 이벤트가 2건 이상 생성되지 않는다.
- **SC-005**: worker 재시작 후 완료 stage가 재실행되지 않고 run이 재개된다.
- **SC-006**: 모든 단계 출력·판정·분기 결정이 DB에서 run_id 하나로 추적된다.
- **SC-007**: FastAPI로 run 제출→상태 폴링→승인까지 HTTP로 완결된다.
- **SC-008**: 상태 화면이 단일 페이지로 렌더링되고 디자인 토큰 가이드(색·타이포·간격)와 일치한다.

## Assumptions

- LLM은 OpenAI 호환 chat/completions 엔드포인트를 사용한다. 일반 작업 기본 모델은 `gemini-3.5-flash-lite`, 중요 작업 기본은 codex 계열이며, 역할별 model/base_url/api_key_env로 재정의 가능하다(미설정 시 기본값).
- implementer는 unified diff/변경 제안 텍스트를 출력할 뿐 실제 파일 적용은 하지 않는다(후속 샌드박스 연동).
- 자동 검사의 테스트 실행은 현재 저장소 대상 명령(예: pytest)이며, 변경 미적용 상태의 baseline 검사다.
- Gmail/Calendar 연동은 토큰 파일 기반이며 OAuth 동의 흐름·Web Push는 범위 외.
- 단일 프로세스 SQLite 전제. 멀티 워커·PostgreSQL은 상용 전환 시 store 계층 교체로 대응한다.
- 문서 업무 템플릿 3종은 이번 스펙 범위 외(후속 워크플로 추가).
- 사람 승인자는 MVP에서 단일 로컬 사용자로 가정하며 역할 구분은 Django 전환 시 추가한다.
- 화면은 FastAPI가 제공하는 단일 정적 페이지이며, 프레임워크 프론트엔드는 후속이다.

## Out of Scope

- Django 인증·조직 RBAC·관리자 UI, 브라우저 Web Push(SW/VAPID)
- implementer의 실제 코드 적용(샌드박스/worktree), PR 자동 생성(요약 산출물까지만)
- Gmail history cursor 증분 동기화, 일정 수정·삭제 동기화
- Langfuse 실험·회귀 게이트(트레이싱 연결 지점만 마련)
- 문서 업무 템플릿(회의록·문의·주간보고)
- 다중 스페셜리스트의 실제 병렬 서브에이전트 프로세스 — MVP는 순차 호출로 동일 계약을 구현하고 병렬화는 후속
