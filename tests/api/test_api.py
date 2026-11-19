import tempfile

from fastapi.testclient import TestClient

from jobmate.api.app import create_app
from jobmate.config import Settings
from jobmate.store.db import Store
from tests.integration.helpers import default_outputs, scripted_llm


def _client():
    db = str(__import__("pathlib").Path(tempfile.mkdtemp()) / "t.db")
    settings = Settings(db_path=db, budget_max_tokens=100_000)
    store = Store(db)
    llm = scripted_llm(default_outputs())
    app = create_app(settings=settings, store=store, llm=llm)
    return TestClient(app), store


def test_health():
    client, _ = _client()
    assert client.get("/api/v1/health").json() == {"status": "ok"}


def test_create_run_accepted():
    client, store = _client()
    r = client.post("/api/v1/runs", json={"workflow": "dev_review",
                                          "task": "do thing"})
    assert r.status_code == 202
    run_id = r.json()["id"]
    # 백그라운드 스레드 실행 대기
    import time
    for _ in range(50):
        d = client.get(f"/api/v1/runs/{run_id}").json()
        if d["status"] in ("waiting_approval", "completed", "failed",
                           "needs_human", "rejected"):
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
    r = client.post("/api/v1/runs", json={"workflow": "dev_review",
                                          "task": "t"})
    run_id = r.json()["id"]
    import time
    for _ in range(50):
        d = client.get(f"/api/v1/runs/{run_id}").json()
        if d["status"] == "waiting_approval":
            break
        time.sleep(0.1)
    r = client.post(f"/api/v1/runs/{run_id}/approve",
                    json={"decision": "approve", "comment": "ok"})
    assert r.status_code == 200
    assert r.json()["status"] == "completed"


def test_approve_invalid_state():
    client, store = _client()
    run = store.create_run("dev_review", "0.1.0", {"task": "t"}, trace_id="x")
    r = client.post(f"/api/v1/runs/{run.id}/approve",
                    json={"decision": "approve"})
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
