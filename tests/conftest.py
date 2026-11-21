"""테스트 격리 — 개발용 .env의 실제 비밀값/키가 테스트에 새지 않도록 무력화.

`jobmate.api.app` 모듈은 import 시 `create_app()`을 실행하고, `get_settings()`가
`load_dotenv()`로 .env를 os.environ에 로드한다. pop으로 지우면 dotenv가 다시
로드하므로, 빈 문자열로 설정해 덮어쓰기를 막는다(override=False 기본 동작).
빈 값은 `bool(env)` 검사에서 비활성으로 처리된다.
"""

import os

_SECRET_EXACT = {
    "JOBMATE_API_KEY",
    "CRON_SECRET",
    "JOBMATE_CRON_SECRET",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GITHUB_TOKEN",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "LANGSMITH_API_KEY",
    "CRONJOB_ORG_API_KEY",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "GOOGLE_REDIRECT_URI",
    "POSTGRES_URL",
    "POSTGRES_PRISMA_URL",
    "VAPID_PUBLIC_KEY",
    "VAPID_PRIVATE_KEY",
}
_SECRET_PREFIXES = ("VAPID_", "CRONJOB_")
_SECRET_SUFFIXES = ("_TOKEN_JSON", "_API_KEY")

# .env에 실제로 존재하는 키는 이름을 알고 있으므로 무조건 빈 값으로 고정한다.
# (패턴 매칭은 쉘에서 export된 경우만 커버 — .env 로드 전이라 environ에 없을 수 있음)
for _name in _SECRET_EXACT:
    os.environ[_name] = ""

for _name in list(os.environ):
    if _name == "LANGCHAIN_API_KEY":
        continue  # configure_langsmith가 setdefault로 설정하는 파생 변수
    if _name.startswith(_SECRET_PREFIXES) or _name.endswith(_SECRET_SUFFIXES):
        os.environ[_name] = ""
