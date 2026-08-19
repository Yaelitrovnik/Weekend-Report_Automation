from __future__ import annotations

ACTIVE_STATES = {"CREATED", "RUNNING", "RECOVERY_REQUIRED"}


class DuplicateActiveRun(RuntimeError):
    pass


class InvalidRunTransition(RuntimeError):
    pass
