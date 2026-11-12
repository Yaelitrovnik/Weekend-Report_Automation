"""Conservative Global-service scheduling eligibility from read-only Docker data."""

from __future__ import annotations

import re
from typing import Any


def global_eligibility(service: dict, nodes: Any, tasks: list[dict]) -> tuple[int | None, dict]:
    try:
        return _eligible(service, nodes, tasks)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        return None, {"available": False, "reason": str(exc) or "Malformed scheduling data"}


def _eligible(service: dict, nodes: Any, tasks: list[dict]) -> tuple[int, dict]:
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("Read-only Docker nodes inventory is unavailable or empty")
    template = service["Spec"].get("TaskTemplate") or {}
    placement = template.get("Placement") or {}
    reservations = (template.get("Resources") or {}).get("Reservations") or {}
    if any(reservations.values()):
        raise ValueError("Resource reservations require scheduler allocation evidence")
    eligible = []
    for node in nodes:
        spec, status, description = node["Spec"], node["Status"], node["Description"]
        availability = spec["Availability"].lower()
        state = status["State"].lower()
        if availability not in {"active", "pause", "drain"}:
            raise ValueError("Unknown node availability")
        if state != "ready" or availability == "drain":
            continue
        if availability == "pause" and not any(
            task.get("node_id") == node["ID"] and task.get("current_state") == "running"
            for task in tasks
        ):
            continue
        platforms = placement.get("Platforms") or []
        platform = description.get("Platform") or {}
        if platforms and (not platform.get("OS") or not platform.get("Architecture")):
            raise ValueError("Node platform data is unavailable")
        if platforms and not any(
            item.get("OS") == platform.get("OS")
            and _architecture(item.get("Architecture"))
            == _architecture(platform.get("Architecture"))
            for item in platforms
        ):
            continue
        if all(_constraint(item, node) for item in placement.get("Constraints") or []):
            eligible.append(node["ID"])
    return len(eligible), {"available": True, "source": "docker_nodes", "eligible_nodes": eligible}


def _architecture(value: Any) -> Any:
    return {"x86_64": "amd64", "aarch64": "arm64"}.get(value, value)


def _constraint(expression: str, node: dict) -> bool:
    match = re.fullmatch(r"\s*([\w.-]+)\s*(==|!=)\s*(\S+)\s*", expression)
    if not match:
        raise ValueError("Unsupported placement constraint")
    key, operator, expected = match.groups()
    spec, desc = node["Spec"], node["Description"]
    attributes = {
        "node.id": node["ID"],
        "node.hostname": desc.get("Hostname"),
        "node.role": spec.get("Role"),
        "node.platform.os": (desc.get("Platform") or {}).get("OS"),
        "node.platform.arch": (desc.get("Platform") or {}).get("Architecture"),
    }
    if key.startswith("node.labels."):
        actual = (spec.get("Labels") or {}).get(key.removeprefix("node.labels."), "")
    elif key.startswith("engine.labels."):
        actual = (desc.get("Engine", {}).get("Labels") or {}).get(
            key.removeprefix("engine.labels."), ""
        )
    elif key in attributes and attributes[key] is not None:
        actual = attributes[key]
    else:
        raise ValueError("Constraint attribute is unsupported or unavailable")
    if key == "node.platform.arch":
        actual, expected = _architecture(actual), _architecture(expected)
    return (actual == expected) if operator == "==" else (actual != expected)
