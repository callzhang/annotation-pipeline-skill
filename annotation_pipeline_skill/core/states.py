from enum import Enum


class TaskStatus(str, Enum):
    DRAFT = "draft"
    READY = "ready"
    ANNOTATING = "annotating"
    VALIDATING = "validating"
    QC = "qc"
    HUMAN_REVIEW = "human_review"
    REPAIR = "repair"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    MERGED = "merged"
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
