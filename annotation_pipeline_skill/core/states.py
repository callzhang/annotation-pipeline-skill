from enum import Enum


class TaskStatus(str, Enum):
    DRAFT = "draft"
    PENDING = "pending"
    ANNOTATING = "annotating"
    QC = "qc"
    ARBITRATING = "arbitrating"
    HUMAN_REVIEW = "human_review"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class AttemptStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class FeedbackSource(str, Enum):
    VALIDATION = "validation"
    QC = "qc"
    HUMAN_REVIEW = "human_review"


class FeedbackSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    BLOCKING = "blocking"


class OutboxKind(str, Enum):
    STATUS = "status"
    SUBMIT = "submit"


class OutboxStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    DEAD_LETTER = "dead_letter"
