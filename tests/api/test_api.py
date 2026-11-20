import tempfile

from fastapi.testclient import TestClient

from jobmate.api.app import create_app
from jobmate.config import Settings
from jobmate.store.db import Store
from tests.integration.helpers import default_outputs, scripted_llm


def _client():
    db = str(__import__("pathlib").Path(tempfile.mkdtemp()) / "t.db")
    settings = Settings(db_path=db, budget_max_tokens=100_000, coding_tool="none")
    store = Store(db)
    llm = scripted_llm(default_outputs())
    app = create_app(settings=settings, store=store, llm=llm)
    return TestClient(app), store


def test_health():
    client, _ = _client()
    assert client.get("/api/v1/health").json() == {"status": "ok"}


def test_create_run_accepted():
    client, store = _client()
    r = client.post("/api/v1/runs", json={"workflow": "dev_review", "task": "do thing"})
    assert r.status_code == 202
    run_id = r.json()["id"]
    # 백그라운드 스레드 실행 대기
    import time

    for _ in range(50):
        d = client.get(f"/api/v1/runs/{run_id}").json()
        if d["status"] in ("waiting_approval", "completed", "failed", "needs_human", "rejected"):
            break
        time.sleep(0.1)
    assert d["status"] == "waiting_approval"
    assert len(d["stages"]) == 7


def test_create_run_unknown_workflow():
    client, _ = _client()
    r = client.post("/api/v1/runs", json={"workflow": "nope", "task": "x"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "unknown_workflow"


def test_create_run_validation_error():
    client, _ = _client()
    r = client.post("/api/v1/runs", json={"workflow": "dev_review", "task": ""})
    assert r.status_code == 422


def test_run_not_found():
    client, _ = _client()
    r = client.get("/api/v1/runs/nonexistent")
    assert r.status_code == 404
    assert "request_id" in r.json()["error"]


def test_approve_flow():
    client, store = _client()
    r = client.post("/api/v1/runs", json={"workflow": "dev_review", "task": "t"})
    run_id = r.json()["id"]
    import time

    for _ in range(50):
        d = client.get(f"/api/v1/runs/{run_id}").json()
        if d["status"] == "waiting_approval":
            break
        time.sleep(0.1)
    r = client.post(f"/api/v1/runs/{run_id}/approve", json={"decision": "approve", "comment": "ok"})
    assert r.status_code == 200
    assert r.json()["status"] == "completed"


def test_approve_invalid_state():
    client, store = _client()
    run = store.create_run("dev_review", "0.1.0", {"task": "t"}, trace_id="x")
    r = client.post(f"/api/v1/runs/{run.id}/approve", json={"decision": "approve"})
    assert r.status_code == 409


def test_notifications_and_links_empty():
    client, _ = _client()
    assert client.get("/api/v1/notifications").json() == []
    assert client.get("/api/v1/event-links").json() == []


def test_index_page_served():
    client, _ = _client()
    r = client.get("/")
    assert r.status_code == 200
    assert "jobMate" in r.text


def test_email_check_and_summaries():
    import pathlib
    import tempfile

    from jobmate.adapters.fakes import FakeGmail
    from jobmate.llm import FakeLLM

    db = str(pathlib.Path(tempfile.mkdtemp()) / "t.db")
    settings = Settings(db_path=db, coding_tool="none")
    store = Store(db)
    msgs = [
        {
            "id": "m1",
            "subject": "회의 안내",
            "from_addr": "a@b.com",
            "internal_date": "1763000000000",
            "body_text": "내일 오후 2시 회의실B에서 미팅",
        }
    ]
    llm = FakeLLM(lambda **kw: "일정 안내 메일입니다\n내일 14시 미팅\n장소는 회의실B")
    app = create_app(settings=settings, store=store, llm=llm, adapters={"gmail": FakeGmail(msgs)})
    client = TestClient(app)

    r = client.post("/api/v1/email/check", json={"limit": 5})
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] and d["checked"] == 1
    card = d["cards"][0]
    assert card["subject"] == "회의 안내"
    assert card["summary"].count("\n") == 2  # 정확히 3줄
    assert "mail.google.com" in card["url"]

    r2 = client.get("/api/v1/email/summaries")
    assert len(r2.json()["cards"]) == 1

    # 재호출 시 같은 source_id는 upsert (카드 수 유지)
    client.post("/api/v1/email/check", json={"limit": 5})
    assert len(client.get("/api/v1/email/summaries").json()["cards"]) == 1


def test_email_check_no_gmail_adapter():
    import pathlib
    import tempfile

    db = str(pathlib.Path(tempfile.mkdtemp()) / "t.db")
    settings = Settings(db_path=db, coding_tool="none")
    app = create_app(settings=settings, store=Store(db), llm=None, adapters={})
    client = TestClient(app)
    r = client.post("/api/v1/email/check", json={})
    assert r.status_code == 503


def test_coding_tools_and_run_validation():
    client, _ = _client()
    r = client.get("/api/v1/coding/tools")
    assert r.status_code == 200
    names = [t["name"] for t in r.json()["tools"]]
    assert set(names) == {"claude", "codex", "antigravity", "opencode"}

    r = client.post("/api/v1/coding/run", json={"tool": "nope", "task": "x"})
    assert r.status_code == 400
    r = client.post("/api/v1/coding/run", json={"tool": "codex", "task": ""})
    assert r.status_code == 422
    r = client.get("/api/v1/coding/jobs/nonexistent")
    assert r.status_code == 404


def test_settings_prefs_roundtrip():
    client, store = _client()
    r = client.get("/api/v1/settings")
    assert r.status_code == 200
    assert r.json()["gmail_check_minutes"] == 30

    r = client.post(
        "/api/v1/settings",
        json={
            "gmail_check_minutes": 60,
            "radar_interval": "weekly",
            "coding_tool": "codex",
            "coding_model": "gpt-5",
        },
    )
    assert r.status_code == 200
    d = client.get("/api/v1/settings").json()
    assert d["gmail_check_minutes"] == 60
    assert d["radar_interval"] == "weekly"
    assert d["coding_tool"] == "codex"
    assert d["coding_model"] == "gpt-5"
    # 영속화 확인
    assert store.get_state("prefs")["radar_interval"] == "weekly"

    r = client.post("/api/v1/settings", json={"gmail_check_minutes": 15})
    assert r.status_code == 422
    r = client.post("/api/v1/settings", json={"coding_tool": "bogus"})
    assert r.status_code == 422
