from __future__ import annotations

REQUIRED_FILES = [
    "sites.yml",
    "servers.yml",
    "rules.yml",
    "portainer_expected.yml",
    "rabbitmq_expected.yml",
    "doctor.yml",
    "recording.yml",
    "database.yml",
    "splunk_dashboards.yml",
]

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
    "database",
    "splunk",
}
VALID_NOTE_SCOPES = {"MODULE", "RESULT", "SPLUNK_DASHBOARD", "GENERAL"}

NOT_APPLICABLE_ALLOWED_PATH_PARTS = {
    "optional_reason",
    "allowed_difference",
    "notes",
    "archive.destination",
    "email.recipients",
    "manual_review.optional_reference",
}
