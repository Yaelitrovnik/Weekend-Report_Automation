from __future__ import annotations

import os

from fastapi import Request

from app.auth import require_csrf_for_mutation, resolve_reviewer
from app.config.loader import load_config_dir
from app.database.repository import Repository
from app.evidence.manager import EvidenceManager

_repo: Repository | None = None


def get_config():
    return load_config_dir(os.getenv("WEEKEND_REPORT_CONFIG_DIR", "config"))


def get_repository() -> Repository:
    global _repo
    if _repo is None:
        url = os.getenv("WEEKEND_REPORT_DATABASE_URL", "sqlite:///data/weekend-report.sqlite")
        _repo = Repository(url)
    return _repo


def get_evidence_manager() -> EvidenceManager:
    return EvidenceManager(os.getenv("WEEKEND_REPORT_EVIDENCE_ROOT", "runs"))


def get_reviewer(request: Request) -> str:
    return resolve_reviewer(request)


def get_mutating_reviewer(request: Request) -> str:
    reviewer = resolve_reviewer(request, mutating=True)
    require_csrf_for_mutation(request, reviewer)
    return reviewer
