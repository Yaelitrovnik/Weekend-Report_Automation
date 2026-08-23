from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database.repository import Repository


def main() -> int:
    repo = Repository(os.getenv("WEEKEND_REPORT_DATABASE_URL", "sqlite:///data/weekend-report.sqlite"))
    try:
        print("database schema initialized")
        return 0
    finally:
        repo.close()


if __name__ == "__main__":
    raise SystemExit(main())
