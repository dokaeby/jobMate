# Specification Quality Checklist: jobMate 에이전트 하네스 엔진 MVP

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-11-19
**Feature**: [spec.md](../spec.md)

## Completeness

- [x] User stories are prioritized (P1/P2/P3) with independent test descriptions
- [x] Acceptance scenarios are Given/When/Then and testable
- [x] Edge cases are enumerated (verdict 파싱 실패, approve+blocker 강등, 재시작 재개, Calendar 타임아웃, 인젝션)
- [x] Functional requirements are numbered (FR-001~FR-019) and mandatory-tagged
- [x] Key entities are defined
- [x] Success criteria are measurable (SC-001~SC-008)
- [x] Assumptions and out-of-scope are explicit

## Consistency & Clarity

- [x] 코드리뷰(개선 의견)와 QA(최종 판정) 역할이 분리되어 있다
- [x] 루프 상한(리뷰 2회/전체 3회)과 에스컬레이션 경로가 명시됨
- [x] 모델 라우팅(기본 티어 + 역할별 서브모델)이 FR-002에 정의됨
- [x] FastAPI 임시/Django 전환이 명시됨
- [x] gstack 참조 구조(스페셜리스트 findings, CSO 증거 우선, verify, ship 요약)가 FR-015~FR-018에 반영됨
- [x] UI 디자인 요구(ui-ux-pro-max 미니멀/스위스)가 FR-019에 반영됨
- [x] 모든 외부 호출이 Protocol/fake 주입 가능함을 명시(FR-013)
- [x] 비밀값·본문 비노출 요구가 FR-014에 반영됨

## Readiness

- [x] 스펙만으로 plan.md 작성이 가능한 수준의 상세도
- [ ] 사용자 리뷰/승인 (spec-kit gate) — plan 진행 전 확인 필요
