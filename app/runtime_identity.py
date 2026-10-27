from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

UNSET_RUNTIME_VALUES = {"", "<TBD>", "<TO_VERIFY>", "UNKNOWN"}
LOCAL_APP_VERSION = "0.1.0-local"
LOCAL_BUILD_ID = "LOCAL-FOLDER"
GIT_NOT_APPLICABLE = "<NOT_APPLICABLE>"


@dataclass(slots=True)
class RuntimeIdentity:
    application_version: str
    build_id: str
    configuration_hash: str
    git_commit: str


def current_runtime_identity(config: dict[str, Any]) -> RuntimeIdentity:
    application_version = os.getenv("WEEKEND_REPORT_APP_VERSION", "").strip() or LOCAL_APP_VERSION
    build_id = os.getenv("WEEKEND_REPORT_BUILD_ID", "").strip() or LOCAL_BUILD_ID
    git_commit = os.getenv("WEEKEND_REPORT_GIT_COMMIT", "").strip()
    return RuntimeIdentity(
        application_version=application_version,
        build_id=build_id,
        configuration_hash=str(config.get("_config_hash", "")).strip(),
        git_commit=git_commit if git_commit not in UNSET_RUNTIME_VALUES else GIT_NOT_APPLICABLE,
    )


def runtime_identity_errors(
    config: dict[str, Any],
    *,
    production_preflight: bool = True,
) -> list[str]:
    if not production_preflight:
        return []

    errors: list[str] = []
    if _is_unset(os.getenv("WEEKEND_REPORT_APP_VERSION")):
        errors.append("WEEKEND_REPORT_APP_VERSION must be set for production traceability")
    if _is_unset(os.getenv("WEEKEND_REPORT_BUILD_ID")):
        errors.append("WEEKEND_REPORT_BUILD_ID must be set for production traceability")
    if _is_unset(str(config.get("_config_hash", ""))):
        errors.append("configuration hash could not be calculated for the effective config")
    return errors


def _is_unset(value: str | None) -> bool:
    return value is None or value.strip() in UNSET_RUNTIME_VALUES
