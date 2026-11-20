import pytest
from pydantic import ValidationError

from jobmate.models import ExtractedEvent, Finding, Verdict


def test_verdict_schema():
    v = Verdict(
        decision="approve", findings=[{"severity": "major", "summary": "x", "confidence": "high"}]
    )
    assert v.findings[0].severity == "major"


def test_verdict_invalid_decision_rejected():
    with pytest.raises(ValidationError):
        Verdict(decision="lgtm")


def test_finding_requires_summary():
    with pytest.raises(ValidationError):
        Finding(severity="minor")


def test_extracted_event_defaults():
    ev = ExtractedEvent(
        title="t", start_at="2026-01-01T10:00:00+09:00", end_at="2026-01-01T11:00:00+09:00"
    )
    assert ev.timezone == "Asia/Seoul"
    assert ev.confidence == 0.0
