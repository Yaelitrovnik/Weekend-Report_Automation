from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.database.repository import Repository
from app.evidence.manager import EvidenceManager


@dataclass(slots=True)
class RunContext:
    run_id: str
    config: dict[str, Any]
    repository: Repository
    evidence: EvidenceManager
    fixture_root: Path | None = None
