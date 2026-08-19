from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class SSHResult:
    host: str
    command: str
    exit_code: int | None
    stdout: str
    stderr: str
    timeout: int


class SSHExecutor:
    def run(self, host: str, command: str, timeout: int) -> SSHResult:
        raise RuntimeError(
            "SSH execution is blocked until server inventory, credentials, "
            "and host-key policy are approved"
        )
