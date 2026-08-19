from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass(slots=True)
class CommandResult:
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    timeout: int


class CommandExecutor:
    def run(self, command: list[str], timeout: int, cwd: str | None = None) -> CommandResult:
        import time

        start = time.monotonic()
        proc = subprocess.run(
            command, cwd=cwd, timeout=timeout, capture_output=True, text=True, check=False
        )
        return CommandResult(
            command, proc.returncode, proc.stdout, proc.stderr, time.monotonic() - start, timeout
        )
