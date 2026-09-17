from __future__ import annotations

import copy
from typing import Any


def resolve_rabbitmq_expected(
    config: dict[str, Any],
) -> dict[str, Any]:
    """Return the current RabbitMQ queue/node policy and site mappings."""
    rabbitmq = config.get(
        "rabbitmq_expected",
        config,
    )
    return copy.deepcopy(rabbitmq)
