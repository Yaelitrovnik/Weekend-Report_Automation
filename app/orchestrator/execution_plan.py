from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain import MODULES


@dataclass(slots=True)
class ExecutionStep:
    module: str
    enabled: bool
    required: bool


def parity_applicable(config: dict[str, Any]) -> bool:
    """Cross-site comparisons follow independent checks only in multi-site runs."""
    return len(config.get("sites", {}).get("sites", [])) > 1 and any(
        rule.get("enabled", False) for rule in config.get("rules", {}).get("parity", [])
    )


def build_execution_plan(config: dict[str, Any]) -> list[ExecutionStep]:
    modules = config.get("rules", {}).get("modules", {})
    steps: list[ExecutionStep] = []
    for module in MODULES:
        rule = modules.get(module, {})
        steps.append(
            ExecutionStep(
                module=module,
                enabled=bool(rule.get("enabled", False)),
                required=bool(rule.get("required", False)),
            )
        )
    return [step for step in steps if step.enabled and step.module != "splunk"]
