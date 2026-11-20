"""security.py + API 키 미들웨어 + 저장 데이터 암호화 테스트."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from jobmate.api.app import create_app
from jobmate.config import Settings
from jobmate.security import decrypt_str, encrypt_str
from jobmate.store.db import Store


@pytest.fixture()
def enc_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBMATE_ENCRYPTION_KEY", "test-secret-material")
    monkeypatch.delenv("JOBMATE_API_KEY", raising=False)
    monkeypatch.delenv("CRON_SECRET", raising=False)
    monkeypatch.delenv("JOBMATE_CRON_SECRET", raising=False)


def test_encrypt_roundtrip(enc_env: None) -> None:
    ct = encrypt_str("secret-value")
    assert ct.startswith("enc:v1:")
    assert ct != "secret-value"
    assert decrypt_str(ct) == "secret-value"


def test_passthrough_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    for v in (
        "JOBMATE_ENCRYPTION_KEY",
        "JOBMATE_API_KEY",
        "CRON_SECRET",
        "JOBMATE_CRON_SECRET",
    ):
        monkeypatch.delenv(v, raising=False)
    assert encrypt_str("plain") == "plain"
    assert decrypt_str("plain") == "plain"


def test_decrypt_legacy_plaintext(enc_env: None) -> None:
    assert decrypt_str("not-encrypted") == "not-encrypted"
    assert decrypt_str("") == ""


def test_push_subscription_encrypted_at_rest(enc_env: None, tmp_path) -> None:
    store = Store(str(tmp_path / "t.db"))
    store.save_push_subscription("https://push.example/abc", {"p256dh": "k1", "auth": "k2"})
    row = store._conn.execute("SELECT keys_json FROM push_subscriptions").fetchone()
    assert row["keys_json"].startswith("enc:v1:")
    subs = store.list_push_subscriptions()
    assert subs[0]["keys"] == {"p256dh": "k1", "auth": "k2"}
    store.close()


def test_email_summary_encrypted_at_rest(enc_env: None, tmp_path) -> None:
    store = Store(str(tmp_path / "t.db"))
    store.save_email_summary("gmail", "m1", subject="비밀 제목", sender="a@b.c", summary="3줄 요약")
    row = store._conn.execute("SELECT subject, summary FROM email_summaries").fetchone()
    assert row["subject"].startswith("enc:v1:")
    assert row["summary"].startswith("enc:v1:")
    cards = store.list_email_summaries("gmail")
    assert cards[0]["subject"] == "비밀 제목"
    assert cards[0]["summary"] == "3줄 요약"
    store.close()


def _client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("JOBMATE_API_KEY", "secret-key-123")
    settings = Settings(db_path=tmp_path / "api.db")
    store = Store(str(settings.db_path))
    return TestClient(create_app(settings=settings, store=store, llm=None, adapters={}))


def test_api_requires_key(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch)
    assert client.get("/api/v1/settings").status_code == 401
    assert client.get("/api/v1/settings", headers={"X-API-Key": "wrong"}).status_code == 401
    ok = client.get("/api/v1/settings", headers={"X-API-Key": "secret-key-123"})
    assert ok.status_code == 200
    ok2 = client.get("/api/v1/settings", headers={"Authorization": "Bearer secret-key-123"})
    assert ok2.status_code == 200


def test_api_exempt_paths(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch)
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/").status_code == 200
    # webhook 경로는 자체 서명 검증 — API 키 없이 도달해야 함(400/401은 검증 실패)
    r = client.post("/api/v1/webhooks/slack", content="{}")
    assert r.status_code != 401 or "unauthorized" not in r.text


def test_security_headers(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch)
    r = client.get("/")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
