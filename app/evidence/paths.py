from __future__ import annotations

import re
from pathlib import Path

SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9_.=-]+")


def sanitize_segment(value: str) -> str:
    cleaned = SAFE_SEGMENT.sub("_", value).strip("._")
    if not cleaned:
        raise ValueError("empty evidence path segment")
    return cleaned


def safe_relative_path(*segments: str) -> Path:
    safe = [sanitize_segment(segment) for segment in segments]
    path = Path(*safe)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("unsafe evidence path")
    return path


def ensure_under(root: Path, candidate: Path) -> Path:
    resolved_root = root.resolve()
    resolved_candidate = candidate.resolve()
    if resolved_root != resolved_candidate and resolved_root not in resolved_candidate.parents:
        raise ValueError("path traversal rejected")
    return resolved_candidate
