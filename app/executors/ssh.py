from __future__ import annotations

import logging
import time
from contextlib import suppress
from dataclasses import dataclass
from threading import Event, Timer
from typing import Any

import paramiko

from app.config.schema import is_unresolved_placeholder

# Transport diagnostics can contain untrusted server banners and exception text.
# Application callers receive only the fixed error categories below.
TRANSPORT_LOG = "weekend_report.ssh.transport"
_transport_logger = logging.getLogger(TRANSPORT_LOG)
_transport_logger.addHandler(logging.NullHandler())
_transport_logger.propagate = False


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
        password: str,
        command: str,
        connect_timeout: int,
        command_timeout: int,
        known_hosts_path: str,
    ) -> SSHResult:
        for name, value in (
            ("SSH_USERNAME", username),
            ("SSH_PASSWORD", password),
            ("SSH_KNOWN_HOSTS_PATH", known_hosts_path),
        ):
            if not value.strip() or value.strip() == "UNKNOWN" or is_unresolved_placeholder(value):
                raise RuntimeError(f"{name} is required and must be resolved for SSH")

        stdout_data = bytearray()
        stderr_data = bytearray()
        exit_code: int | None = None
        error = ""
        phase = "known_hosts"
        client = paramiko.SSHClient()
        expired = Event()
        timer: Timer | None = None

        def expire() -> None:
            expired.set()
            # Also interrupt an exec request whose acknowledgement never arrives.
            with suppress(Exception):
                client.close()

        try:
            client.set_log_channel(TRANSPORT_LOG)
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
            # Explicit filename only: never inherit trust from the user's home.
            # System host keys are read-only; we never save or enroll host keys.
            client.load_system_host_keys(known_hosts_path)
            phase = "connection"
            client.connect(
                hostname=host,
                port=port,
                username=username,
                password=password,
                allow_agent=False,
                look_for_keys=False,
                timeout=connect_timeout,
                banner_timeout=connect_timeout,
                auth_timeout=connect_timeout,
                channel_timeout=command_timeout,
            )
            phase = "command"
            deadline = time.monotonic() + command_timeout
            timer = Timer(command_timeout, expire)
            timer.daemon = True
            timer.start()
            stdin, stdout, _stderr = client.exec_command(command, timeout=command_timeout)
            stdin.close()
            channel = stdout.channel
            while True:
                if expired.is_set() or time.monotonic() >= deadline:
                    raise TimeoutError
                # Drain both streams together to avoid SSH window deadlocks.
                if channel.recv_ready():
                    stdout_data.extend(channel.recv(65536))
                if channel.recv_stderr_ready():
                    stderr_data.extend(channel.recv_stderr(65536))
                if (
                    channel.exit_status_ready()
                    and not channel.recv_ready()
                    and not channel.recv_stderr_ready()
                ):
                    status = channel.recv_exit_status()
                    if status < 0:
                        error = "SSH command ended without an exit status"
                    else:
                        exit_code = status
                    break
                expired.wait(min(0.01, max(0, deadline - time.monotonic())))
        except paramiko.BadHostKeyException:
            error = "SSH host key verification failed"
        except paramiko.AuthenticationException:
            error = "SSH authentication failed"
        except TimeoutError:
            error = "SSH command timed out" if phase == "command" else "SSH connection timed out"
        except Exception:
            # Never propagate library exception text, which may contain secrets.
            error = {
                "known_hosts": "SSH known_hosts file could not be read",
                "connection": "SSH connection or host key verification failed",
                "command": "SSH command failed",
            }[phase]
        finally:
            if timer is not None:
                timer.cancel()
            with suppress(Exception):
                client.close()
        if expired.is_set():
            exit_code = None
            error = "SSH command timed out"

        def redact(value: str) -> str:
            return value.replace(password, "<REDACTED>")

        # Even a remote command that echoes the credential cannot put it in evidence.
        return SSHResult(
            host=redact(host),
            command=redact(command),
            exit_code=exit_code,
            stdout=redact(stdout_data.decode("utf-8", errors="replace")),
            stderr=redact(error or stderr_data.decode("utf-8", errors="replace")),
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
