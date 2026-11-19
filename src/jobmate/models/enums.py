"""공통 열거형."""
from enum import StrEnum


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_REVIEW = "waiting_review"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    BUDGET_EXHAUSTED = "budget_exhausted"
    NEEDS_HUMAN = "needs_human"


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class LinkStatus(StrEnum):
    DISCOVERED = "discovered"
    EXTRACTED = "extracted"
    VALIDATED = "validated"
    CALENDAR_CREATED = "calendar_created"
    NOTIFICATION_QUEUED = "notification_queued"
    NOTIFIED = "notified"
    NEEDS_REVIEW = "needs_review"
    DUPLICATE = "duplicate"
    CONNECTION_ERROR = "connection_error"
    RETRY_WAIT = "retry_wait"


class NotificationStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class DoneState(StrEnum):
    """gstack식 단계 완료 프로토콜."""

    DONE = "DONE"
    DONE_WITH_CONCERNS = "DONE_WITH_CONCERNS"
    BLOCKED = "BLOCKED"
    NEEDS_CONTEXT = "NEEDS_CONTEXT"


class Severity(StrEnum):
    BLOCKER = "blocker"
    MAJOR = "major"
    MINOR = "minor"
    NIT = "nit"


class Confidence(StrEnum):
    HIGH = "high"        # 7+
    MEDIUM = "medium"    # 5-6
    LOW = "low"          # 3-4
    VERY_LOW = "very_low"  # 1-2


class VerdictDecision(StrEnum):
    APPROVE = "approve"
    REQUEST_CHANGES = "request_changes"
    PASS = "pass"  # noqa: S105
    FAIL = "fail"
    NEEDS_HUMAN = "needs_human"
