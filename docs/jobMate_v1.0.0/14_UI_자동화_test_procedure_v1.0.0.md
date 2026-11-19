# jobMate UI 자동화 테스트 절차

| 항목 | 값 |
|---|---|
| 문서 버전 | 1.0.0 |
| 기준일 | 2026-11-19 |
| 관련 문서 | 13_UI_자동화_test_cases_v1.0.0.md |

## 절차

1. 테스트용 서버, PostgreSQL, worker와 브라우저를 준비한다.
2. 합성 Gmail 메일이 있는 Google sandbox 계정 또는 deterministic fake integration을 준비한다.
3. 지원 브라우저별 notification permission 상태를 허용·거부로 설정한다.
4. 각 케이스를 실행하고 화면 상태, API 응답, 생성 event ID, 클릭 destination을 기록한다.
5. 접근성 키보드 탐색 및 화면 확대 상태를 확인한다.

UI 자동화에서 실제 사용자 메일이나 개인 Google 계정을 사용하지 않는다. push 표시 자체를 브라우저가 제한할 때는 서비스 워커 수신 이벤트와 앱 내 알림을 확인하고 제한 사실을 결과에 남긴다.
