"""One JSON event per line for Docker stdout; never include exception reprs."""

from __future__ import annotations

import json
import sys
from typing import Any

from app.evidence.redaction import sanitize
from app.time_utils import iso_now


def event(
    name: str,
    *,
    run_id: str | None = None,
    site: str | None = None,
    module: str | None = None,
    severity: str = "INFO",
    **details: Any,
) -> None:
    payload = {
        "timestamp": iso_now(),
        "run_id": run_id,
        "site": site,
        "module": module,
        "severity": severity,
        "event": name,
        "details": sanitize(details),
    }
    print(json.dumps(payload, default=str), file=sys.stdout, flush=True)
