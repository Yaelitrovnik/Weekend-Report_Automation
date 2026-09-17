from __future__ import annotations

from typing import Any

REQUIRED_FILES = ["rules.yml"]

PLACEHOLDER_TBD = "<TBD>"
PLACEHOLDER_TO_VERIFY = "<TO_VERIFY>"
PLACEHOLDER_NOT_APPLICABLE = "<NOT_APPLICABLE>"
UNRESOLVED_PLACEHOLDERS = {PLACEHOLDER_TBD, PLACEHOLDER_TO_VERIFY}
ALL_PLACEHOLDERS = UNRESOLVED_PLACEHOLDERS | {PLACEHOLDER_NOT_APPLICABLE}

VALID_CHECK_STATUSES = {"PASS", "WARNING", "FAIL", "ERROR", "SKIPPED", "MANUAL_REVIEW"}
VALID_MODULES = {
    "portainer",
    "doctor",
    "rabbitmq",
    "recording",
    "infrastructure",
    "splunk",
}
VALID_NOTE_SCOPES = {"MODULE", "RESULT", "SPLUNK_DASHBOARD", "GENERAL"}

# Configuration-contract enums used by app/config/validation.py.
DOCTOR_SUPPORTED_MODES = {"api", "manual"}
DOCTOR_MANUAL_REVIEW_TRIGGERS = {"any_unhealthy"}
RABBITMQ_SUPPORTED_COLLECTION_MODES = {"fixture", "live"}
RABBITMQ_SUPPORTED_SCOPES = {"all"}
RABBITMQ_HEALTH_STATES = {"green"}
RECORDING_SUPPORTED_COLLECTION_MODES = {"fixture", "live"}
RECORDING_SUPPORTED_WORKFLOWS = {"existing_device_start_stop"}
RECORDING_INITIAL_STATES = {"not_recording"}
SSH_SUPPORTED_AUTH_TYPES = {"private_key", "fixture"}
SSH_SUPPORTED_HOST_KEY_POLICIES = {"strict", "fixture"}

NOT_APPLICABLE_ALLOWED_PATH_PARTS = {
    "optional_reason",
    "allowed_difference",
    "notes",
    "archive.destination",
    "email.recipients",
    "manual_review.optional_reference",
}


def is_unresolved_placeholder(value: Any) -> bool:
    """Return True for any unresolved angle-bracket placeholder.

    The project historically used only <TBD> and <TO_VERIFY>, but the edited
    configuration also uses descriptive placeholders such as <SERVICE_01>,
    <DASHBOARD_1_URL>, and <TO_IMPLEMENT>. Production preflight must not allow
    any of those values to pass silently.
    """

    if not isinstance(value, str):
        return False
    stripped = value.strip()
    if stripped == PLACEHOLDER_NOT_APPLICABLE:
        return False
    return len(stripped) >= 3 and stripped.startswith("<") and stripped.endswith(">")
