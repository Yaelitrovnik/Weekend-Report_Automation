from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain import MODULES


@dataclass(slots=True)
class ExecutionStep:
    module: str
    enabled: bool
    required: bool


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
