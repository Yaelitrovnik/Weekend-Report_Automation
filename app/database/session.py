from __future__ import annotations

import os
from pathlib import Path


def default_database_url() -> str:
    return os.getenv("WEEKEND_REPORT_DATABASE_URL", "sqlite:///data/weekend-report.sqlite")


def sqlite_path_from_url(url: str) -> Path:
    if not url.startswith("sqlite:///"):
        raise ValueError("Only sqlite URLs are supported by the local stdlib repository adapter")
    return Path(url.removeprefix("sqlite:///"))
