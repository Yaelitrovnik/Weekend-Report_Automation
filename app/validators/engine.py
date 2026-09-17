from __future__ import annotations

from app.domain import CheckStatus


def threshold_status(value: float, warning: float, critical: float) -> CheckStatus:
    if value >= critical:
        return CheckStatus.FAIL

    if value >= warning:
        return CheckStatus.WARNING

    return CheckStatus.PASS
