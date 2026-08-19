from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from app.auth import UNSET_RUNTIME_VALUES

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
    production = _is_production_auth_mode()
    application_version = os.getenv("WEEKEND_REPORT_APP_VERSION", "").strip()
    build_id = os.getenv("WEEKEND_REPORT_BUILD_ID", "").strip()
    git_commit = os.getenv("WEEKEND_REPORT_GIT_COMMIT", "").strip()
    if not production:
        application_version = application_version or LOCAL_APP_VERSION
        build_id = build_id or LOCAL_BUILD_ID
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
    if not production_preflight or not _is_production_auth_mode():
        return []
    identity = current_runtime_identity(config)
    errors: list[str] = []
    if _is_unset(identity.application_version):
        errors.append("WEEKEND_REPORT_APP_VERSION must be set for production traceability")
    if _is_unset(identity.build_id):
        errors.append("WEEKEND_REPORT_BUILD_ID must be set for production traceability")
    if _is_unset(identity.configuration_hash):
        errors.append("configuration hash could not be calculated for the effective config")
    return errors


def _is_production_auth_mode() -> bool:
    mode = os.getenv("WEEKEND_REPORT_AUTH_MODE", "development").strip().lower()
    return mode == "production"


def _is_unset(value: str | None) -> bool:
    return value is None or value.strip() in UNSET_RUNTIME_VALUES
