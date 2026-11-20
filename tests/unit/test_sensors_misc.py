from jobmate.models import ExtractedEvent
from jobmate.sensors import (
    build_gmail_url,
    idempotency_key,
    is_safe_source_url,
    looks_like_diff,
    scan_secrets,
    validate_event,
)


def test_scan_secrets_detects_patterns():
    assert "aws_key" in scan_secrets("key AKIAIOSFODNN7EXAMPLE")
    assert "private_key" in scan_secrets("-----BEGIN RSA PRIVATE KEY-----")
    assert scan_secrets("nothing here") == []


def test_looks_like_diff():
    assert looks_like_diff("diff --git a/x b/x\n@@ -1 +1 @@")
    assert not looks_like_diff("just text")


def test_gmail_url_safe():
    url = build_gmail_url("18f2a1b2c3d4e5f6")
    assert url.startswith("https://mail.google.com/")
    assert is_safe_source_url(url)
    assert not is_safe_source_url("https://evil.com/x")
    assert not is_safe_source_url("http://mail.google.com/x")


def test_idempotency_key_stable():
    assert idempotency_key("c1", "m1") == idempotency_key("c1", "m1")
    assert idempotency_key("c1", "m1") != idempotency_key("c2", "m1")


def _ev(**kw):
    base = dict(
        title="회의",
        start_at="2026-01-01T10:00:00+09:00",
        end_at="2026-01-01T11:00:00+09:00",
        timezone="Asia/Seoul",
        confidence=0.9,
    )
    base.update(kw)
    return ExtractedEvent(**base)


def test_validate_event_ok():
    ok, reason = validate_event(_ev())
    assert ok and reason == "ok"


def test_validate_event_edges():
    assert validate_event(_ev(is_ambiguous=True))[1] == "ambiguous"
    assert validate_event(_ev(is_recurring=True))[1] == "recurring"
    assert validate_event(_ev(timezone="Mars/Olympus"))[1] == "bad_timezone"
    assert validate_event(_ev(end_at="2026-01-01T09:00:00+09:00"))[1] == "end_before_start"
    assert validate_event(_ev(title="  "))[1] == "empty_title"
    assert validate_event(_ev(confidence=0.2))[1] == "low_confidence"
    assert validate_event(_ev(start_at="not-a-date"))[1] == "bad_datetime"
