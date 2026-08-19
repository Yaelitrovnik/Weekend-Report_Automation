from __future__ import annotations

import copy
import re
from typing import Any

from app.collectors.base import Collector
from app.orchestrator.run_context import RunContext
from app.time_utils import iso_now

SYSTEM_TIME_RE = re.compile(
    r"System time\s*:\s*(?P<value>[+-]?\d+(?:\.\d+)?)\s+seconds\s+(?P<direction>slow|fast)",
    re.IGNORECASE,
)
LEAP_STATUS_RE = re.compile(r"Leap status\s*:\s*(?P<status>.+)", re.IGNORECASE)
SOURCE_RE = re.compile(r"^\s*\^\*\s+(?P<source>\S+)", re.MULTILINE)


class InfrastructureCollector(Collector):
    def collect(self, context: RunContext) -> dict[str, Any]:
        config = context.config.get("servers", {})
        fixture = config.get("fixture_actual")
        if fixture is not None:
            return copy.deepcopy(fixture)
        return {
            "mode": "blocked",
            "collection_timestamp": iso_now(),
            "sites": {},
            "error": (
                "Infrastructure live SSH collection is blocked until approved SSH targets, "
                "credentials, commands, and host-key policy are supplied"
            ),
        }


def parse_chronyc_tracking(text: str) -> dict[str, Any]:
    system_time = SYSTEM_TIME_RE.search(text)
    leap_status = LEAP_STATUS_RE.search(text)
    if system_time is None or leap_status is None:
        raise ValueError("chronyc tracking output missing System time or Leap status")
    offset = float(system_time.group("value"))
    if system_time.group("direction").lower() == "fast":
        offset = -offset
    leap = leap_status.group("status").strip()
    return {
        "offset": offset,
        "leap_status": leap,
        "synchronized": leap.lower() == "normal",
    }


def parse_chronyc_sources(text: str) -> dict[str, Any]:
    selected = SOURCE_RE.search(text)
    if selected is None:
        raise ValueError("chronyc sources output did not contain a selected source")
    return {"source": selected.group("source"), "selected": True}


def normalize_chrony(tracking_text: str, sources_text: str) -> dict[str, Any]:
    tracking = parse_chronyc_tracking(tracking_text)
    sources = parse_chronyc_sources(sources_text)
    return {
        "synchronized": bool(tracking["synchronized"] and sources["selected"]),
        "source": sources["source"],
        "offset": tracking["offset"],
        "raw": {
            "tracking": tracking,
            "sources": sources,
        },
    }
