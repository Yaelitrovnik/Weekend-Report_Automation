from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class RunState(StrEnum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    REVIEW_READY = "REVIEW_READY"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


class CheckStatus(StrEnum):
    PASS = "PASS"
    WARNING = "WARNING"
    FAIL = "FAIL"
    ERROR = "ERROR"
    SKIPPED = "SKIPPED"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class NoteScope(StrEnum):
    MODULE = "MODULE"
    RESULT = "RESULT"
    SPLUNK_DASHBOARD = "SPLUNK_DASHBOARD"
    GENERAL = "GENERAL"


class ReviewDecision(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class DistributionStatus(StrEnum):
    NOT_CONFIGURED = "NOT_CONFIGURED"
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


STATUS_STRENGTH = {
    CheckStatus.ERROR: 60,
    CheckStatus.FAIL: 50,
    CheckStatus.WARNING: 40,
    CheckStatus.MANUAL_REVIEW: 30,
    CheckStatus.SKIPPED: 20,
    CheckStatus.PASS: 10,
}


MODULES = [
    "portainer",
    "doctor",
    "rabbitmq",
    "recording",
    "infrastructure",
    "database",
    "splunk",
]


@dataclass(slots=True)
class EvidenceRecord:
    run_id: str
    module: str
    site: str | None
    evidence_type: str
    path: str
    checksum: str
    mime_type: str
    result_id: int | None = None
    id: int | None = None
    created_at: str | None = None


@dataclass(slots=True)
class CheckResult:
    run_id: str
    module: str
    check_id: str
    status: CheckStatus
    message: str
    site: str | None = None
    target: str | None = None
    expected: Any = None
    actual: Any = None
    started_at: str | None = None
    finished_at: str | None = None
    evidence: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    id: int | None = None


@dataclass(slots=True)
class RunRecord:
    run_id: str
    state: RunState
    automation_status: CheckStatus | None
    started_by: str
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    worker_id: str | None = None
    last_heartbeat: str | None = None
    current_module: str | None = None
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    review_decision: str | None = None
    application_version: str | None = None
    build_id: str | None = None
    git_commit: str | None = None
    config_version: str | None = None
    final_snapshot_path: str | None = None
    final_pdf_path: str | None = None
    final_pdf_checksum: str | None = None


@dataclass(slots=True)
class ReviewNote:
    run_id: str
    scope: NoteScope
    author: str
    note: str
    module: str | None = None
    result_id: int | None = None
    dashboard_id: str | None = None
    id: int | None = None
    created_at: str | None = None
    updated_at: str | None = None


@dataclass(slots=True)
class SiteSummary:
    site: str
    status: CheckStatus
    result_count: int


@dataclass(slots=True)
class ModuleSummary:
    module: str
    status: CheckStatus
    result_count: int


@dataclass(slots=True)
class SplunkDashboard:
    dashboard_id: str
    display_name: str
    url: str
    required_review: bool
    note_required: bool
    order: int


@dataclass(slots=True)
class WorkerHeartbeat:
    worker_id: str
    run_id: str
    current_module: str | None
    last_heartbeat: str


def to_jsonable(value: Any) -> Any:
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "__dataclass_fields__"):
        return {k: to_jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, list):
        return [to_jsonable(v) for v in value]
    if isinstance(value, tuple):
        return [to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    return value
