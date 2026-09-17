from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class SSHResult:
    host: str
    command: str
    exit_code: int | None
    stdout: str
    stderr: str
    timeout: int


class SSHExecutor:
    def run(
        self,
        *,
        host: str,
        port: int,
        username: str,
        command: str,
        connect_timeout: int,
        command_timeout: int,
        key_path: str,
        known_hosts_path: str,
    ) -> SSHResult:
        if not key_path:
            raise RuntimeError("SSH_PRIVATE_KEY_PATH is required for private-key SSH")
        if not known_hosts_path:
            raise RuntimeError("SSH_KNOWN_HOSTS_PATH is required for strict SSH host verification")

        target = f"{username}@{host}"
        args = [
            "ssh",
            "-i",
            key_path,
            "-p",
            str(port),
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={known_hosts_path}",
            "-o",
            f"ConnectTimeout={connect_timeout}",
        ]
        args.extend([target, command])

        try:
            completed = subprocess.run(
                args,
                check=False,
                capture_output=True,
                text=True,
                timeout=command_timeout,
            )
        except subprocess.TimeoutExpired as exc:
            stdout_value = exc.stdout
            stderr_value = exc.stderr

            stdout_text = (
                stdout_value.decode(errors="replace")
                if isinstance(stdout_value, bytes)
                else (stdout_value or "")
            )
            stderr_text = (
                stderr_value.decode(errors="replace")
                if isinstance(stderr_value, bytes)
                else (stderr_value or "SSH command timed out")
            )

            return SSHResult(
                host=host,
                command=command,
                exit_code=None,
                stdout=stdout_text,
                stderr=stderr_text,
                timeout=command_timeout,
            )

        return SSHResult(
            host=host,
            command=command,
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            timeout=command_timeout,
        )

def result_payload(result: SSHResult) -> dict[str, Any]:
    return {
        "host": result.host,
        "command": result.command,
        "exit_code": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "timeout": result.timeout,
    }
