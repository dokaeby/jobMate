import tempfile

from jobmate.models import LinkStatus, NotificationStatus, RunStatus
from jobmate.store.db import Store


def _store():
    return Store(str(__import__("pathlib").Path(tempfile.mkdtemp()) / "t.db"))


def test_run_lifecycle():
    s = _store()
    r = s.create_run("dev_review", "0.1.0", {"task": "t"}, trace_id="x")
    assert r.status == RunStatus.QUEUED
    s.update_run(r.id, status=RunStatus.RUNNING)
    assert s.get_run(r.id).status == RunStatus.RUNNING
    s.update_run(r.id, status=RunStatus.COMPLETED)
    assert s.get_run(r.id).status == RunStatus.COMPLETED
    assert s.list_runs()[0].id == r.id


def test_stage_runs():
    s = _store()
    r = s.create_run("w", "1", {}, trace_id="t")
    sr = s.start_stage(r.id, "analysis", 1, {"a": 1})
    s.finish_stage(sr.id, status="succeeded", output={"ok": True}, tokens_in=5, tokens_out=7)
    stages = s.get_stage_runs(r.id)
    assert len(stages) == 1
    assert stages[0].output == {"ok": True}
    assert stages[0].tokens_in == 5
    assert s.latest_stage(r.id, "analysis").attempt == 1


def test_reserve_link_idempotent():
    s = _store()
    l1 = s.reserve_link("k1", "m1", "c1", "https://mail.google.com/x")
    l2 = s.reserve_link("k1", "m1", "c1", "https://mail.google.com/x")
    assert l1 is not None and l2 is None
    s.update_link(l1.id, status=LinkStatus.CALENDAR_CREATED, calendar_event_id="evt1")
    assert s.get_link_by_key("k1").calendar_event_id == "evt1"


def test_notifications():
    s = _store()
    n = s.add_notification(title="t", body="b", source_url="https://mail.google.com/x")
    assert n.status == NotificationStatus.PENDING
    s.set_notification_status(n.id, NotificationStatus.SENT)
    assert s.list_notifications()[0].status == NotificationStatus.SENT


def test_audit_redaction_safe():
    s = _store()
    r = s.create_run("w", "1", {}, trace_id="t")
    s.audit(
        r.id,
        "agent:reviewer",
        "verdict",
        "code_review",
        {"decision": "approve", "finding_count": 2},
    )
    events = s.get_audit(r.id)
    assert any(e.action == "verdict" for e in events)


def test_pg_translate():
    from jobmate.store.db import _is_pg_dsn, _pg_translate

    assert _is_pg_dsn("postgres://u:p@h/db")
    assert _is_pg_dsn("postgresql://u:p@h/db")
    assert not _is_pg_dsn("data/jobmate.db")
    out = _pg_translate("INSERT OR IGNORE INTO t(a,b) VALUES(?,?)")
    assert out == "INSERT INTO t(a,b) VALUES(%s,%s) ON CONFLICT DO NOTHING"
    out = _pg_translate("INSERT OR IGNORE INTO t(a) VALUES(?) RETURNING id")
    assert out == ("INSERT INTO t(a) VALUES(%s) ON CONFLICT DO NOTHING RETURNING id")
    out = _pg_translate("SELECT * FROM t WHERE a=? AND b<=?")
    assert out == "SELECT * FROM t WHERE a=%s AND b<=%s"
