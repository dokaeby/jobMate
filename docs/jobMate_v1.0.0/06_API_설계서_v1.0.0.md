# jobMate API 설계서

| 항목 | 값 |
|---|---|
| 문서 버전 | 1.0.0 |
| 기준일 | 2026-11-19 |
| API 기준 | `/api/v1`, JSON, Django REST 계층 |
| 기준 문서 | 기본기획서, 시스템상세설계서, 기능별 상세설계서, DB 설계서 |

## 문서 이력

| 버전 | 기준일 | 수정 내용 |
|---|---|---|
| 1.0.0 | 2026-11-19 | 최초 작성 |

## 1. 공통 규칙

- 모든 업무 API는 인증이 필요하며 조직 멤버십을 검사한다.
- 날짜/시간은 ISO 8601 형식과 명시적 timezone을 사용한다.
- 오류 응답: `{"error":{"code":"...","message":"...","request_id":"..."}}`
- 생성 응답은 서버 생성 ID와 상태를 반환한다. 백그라운드 작업은 `202 Accepted`를 반환한다.
- 외부 부작용 API는 idempotency key를 요구하거나 서버 측 키를 계산한다.

## 2. 주요 엔드포인트

| Method | Path | 목적 |
|---|---|---|
| GET | `/api/v1/workflows/templates` | 활성 템플릿 목록 |
| POST | `/api/v1/workflows/runs` | 업무 실행 생성 |
| GET | `/api/v1/workflows/runs` | 사용자 실행 목록 |
| GET | `/api/v1/workflows/runs/{run_id}` | 실행 및 단계 조회 |
| POST | `/api/v1/workflows/runs/{run_id}/cancel` | 실행 취소 |
| GET | `/api/v1/integrations/google` | 연결 상태와 scope 조회 |
| GET | `/api/v1/integrations/google/oauth/start` | OAuth 시작 URL |
| GET | `/api/v1/integrations/google/oauth/callback` | OAuth 완료 처리 |
| DELETE | `/api/v1/integrations/google` | 연결 해제 및 토큰 폐기 |
| PATCH | `/api/v1/automations/gmail-calendar` | 일정 자동화 활성화/설정 |
| GET | `/api/v1/automations/gmail-calendar/status` | 감지 상태·마지막 실행 조회 |
| GET | `/api/v1/calendar-events` | 등록 이벤트 이력 |
| GET | `/api/v1/notifications` | 앱 내 알림 조회 |
| POST | `/api/v1/notifications/push-subscriptions` | Web Push 구독 등록 |
| DELETE | `/api/v1/notifications/push-subscriptions/{id}` | 구독 해제 |
| POST | `/api/v1/notifications/{id}/open` | 알림 클릭 감사·허용 URL 반환 |

## 3. 업무 실행 계약

`POST /workflows/runs` 요청은 `template_id`, `template_version`, `input`을 받는다. 응답은 `run_id`, `status=queued`, `created_at`을 반환한다. 클라이언트는 run 상세를 polling한다. MVP 실시간 스트리밍은 선택 후속 기능이다.

## 4. Google 자동화 설정 계약

설정 필드: `enabled`, `mail_query`, `poll_interval_minutes`, `timezone`, `confidence_threshold`. 허용 query는 서버에서 보안 검증한다. `enabled=true`는 유효한 Google 연결과 필요한 OAuth scope가 있을 때만 가능하다.

## 5. 내부 처리 이벤트

Gmail/Calendar/푸시 작업은 외부에서 직접 공개 호출하지 않고 task queue 메시지로 실행한다. Queue payload에는 조직 ID, 연결 ID, 실행 ID만 넣고 토큰이나 메일 본문은 포함하지 않는다. 중복 전달은 작업 멱등 처리로 흡수한다.

## 6. 보안·오류

401 인증 실패, 403 권한 부족, 404 비공개 대상 없음, 409 중복/상태 충돌, 422 입력 검증, 429 제한, 502 외부 API 실패, 503 일시 불가. OAuth callback은 state와 PKCE를 검증한다. 원본 Gmail URL은 저장된 Google message ID로 서버가 생성하고 임의 URL 리다이렉트는 금지한다.
