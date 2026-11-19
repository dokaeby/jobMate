from jobmate.models import Finding, Verdict
from jobmate.sensors import (
    downgrade_approve,
    filter_findings,
    has_blocking_findings,
)


def _v(decision="approve", findings=None):
    return Verdict(decision=decision, findings=findings or [], summary="s")


def _f(sev, conf="high"):
    return Finding(severity=sev, confidence=conf, summary="x")


def test_approve_no_findings_unchanged():
    v = _v()
    assert downgrade_approve(v).decision == "approve"


def test_approve_with_blocker_downgraded():
    v = _v(findings=[_f("blocker")])
    out = downgrade_approve(v)
    assert out.decision == "request_changes"
    assert "강등" in out.summary


def test_approve_with_major_downgraded():
    v = _v(findings=[_f("major")])
    assert downgrade_approve(v).decision == "request_changes"


def test_approve_with_minor_only_stays():
    v = _v(findings=[_f("minor"), _f("nit")])
    assert downgrade_approve(v).decision == "approve"


def test_very_low_confidence_blocker_not_blocking():
    v = _v(findings=[_f("blocker", conf="very_low")])
    assert has_blocking_findings(v) is False
    assert downgrade_approve(v).decision == "approve"


def test_request_changes_unchanged():
    v = _v("request_changes", [_f("minor")])
    assert downgrade_approve(v).decision == "request_changes"


def test_filter_findings_removes_very_low():
    v = _v(findings=[_f("major", "high"), _f("minor", "very_low")])
    out = filter_findings(v)
    assert len(out.findings) == 1
    assert out.findings[0].severity == "major"
