from __future__ import annotations

import json
import logging
import tempfile
import unittest
from collections import deque
from pathlib import Path
from threading import Event
from unittest.mock import Mock, patch

import paramiko

from app.executors.ssh import TRANSPORT_LOG, SSHExecutor, result_payload

PASSWORD = "fake-ssh-password-for-tests-only"
COMMAND = "df -h | grep mapper"


class FakeChannel:
    def __init__(self, stdout=b"ok\n", stderr=b"", status=0, ready=True):
        self.out = deque([stdout] if stdout else [])
        self.err = deque([stderr] if stderr else [])
        self.status = status
        self.ready = ready

    def recv_ready(self):
        return bool(self.out)

    def recv_stderr_ready(self):
        return bool(self.err)

    def recv(self, _size):
        return self.out.popleft()

    def recv_stderr(self, _size):
        return self.err.popleft()

    def exit_status_ready(self):
        return self.ready

    def recv_exit_status(self):
        return self.status


class SSHExecutorTests(unittest.TestCase):
    def setUp(self):
        self.kwargs = {
            "host": "server.example.invalid",
            "port": 22,
            "username": "fixture-user",
            "password": PASSWORD,
            "command": COMMAND,
            "connect_timeout": 5,
            "command_timeout": 5,
            "known_hosts_path": "/fixture/known_hosts",
        }
        self.client = Mock()
        self.channel = FakeChannel()
        self.client.exec_command.return_value = (
            Mock(), Mock(channel=self.channel), Mock(channel=self.channel),
        )

    def run_mock(self, **kwargs):
        with patch("app.executors.ssh.paramiko.SSHClient", return_value=self.client):
            return SSHExecutor().run(**(self.kwargs | kwargs))

    def test_required_values_fail_without_echoing_values(self):
        for field, label in (
            ("username", "SSH_USERNAME"),
            ("password", "SSH_PASSWORD"),
            ("known_hosts_path", "SSH_KNOWN_HOSTS_PATH"),
        ):
            for value in ("", " ", "<TBD>", "<fake-secret-placeholder>", "UNKNOWN"):
                with self.subTest(field=field, value=value):
                    with self.assertRaisesRegex(RuntimeError, label) as raised:
                        self.run_mock(**{field: value})
                    self.assertNotIn("fake-secret-placeholder", str(raised.exception))
        self.client.connect.assert_not_called()

    def test_password_and_only_explicit_trust_file_are_used(self):
        with patch("subprocess.run") as process:
            result = self.run_mock(port=2222)
        process.assert_not_called()
        self.client.load_system_host_keys.assert_called_once_with("/fixture/known_hosts")
        self.client.load_host_keys.assert_not_called()
        self.assertIsInstance(
            self.client.set_missing_host_key_policy.call_args.args[0], paramiko.RejectPolicy,
        )
        self.client.connect.assert_called_once_with(
            hostname="server.example.invalid", port=2222, username="fixture-user",
            password=PASSWORD, allow_agent=False, look_for_keys=False,
            timeout=5, banner_timeout=5, auth_timeout=5, channel_timeout=5,
        )
        self.client.exec_command.assert_called_once_with(COMMAND, timeout=5)
        self.assertNotIn(PASSWORD, repr(self.client.exec_command.call_args))
        self.client.save_host_keys.assert_not_called()
        self.client.close.assert_called_once()
        self.assertEqual(result.stdout, "ok\n")
        self.assertEqual(result.exit_code, 0)

    def test_library_errors_never_escape_or_echo_credentials(self):
        cases = (
            (paramiko.AuthenticationException(PASSWORD), "SSH authentication failed"),
            (TimeoutError(PASSWORD), "SSH connection timed out"),
            (paramiko.SSHException(PASSWORD), "SSH connection or host key verification failed"),
            (OSError(PASSWORD), "SSH connection or host key verification failed"),
        )
        for exception, message in cases:
            with self.subTest(category=type(exception).__name__):
                self.client.connect.side_effect = exception
                result = self.run_mock()
                self.assertIsNone(result.exit_code)
                self.assertEqual(result.stderr, message)
                self.assertNotIn(PASSWORD, repr(result))
        self.client.exec_command.assert_not_called()

    def test_missing_or_unreadable_known_hosts_stops_before_connect(self):
        for exception in (
            FileNotFoundError(PASSWORD), PermissionError(PASSWORD), ValueError(PASSWORD),
        ):
            self.client.load_system_host_keys.side_effect = exception
            result = self.run_mock()
            self.assertIsNone(result.exit_code)
            self.assertEqual(result.stderr, "SSH known_hosts file could not be read")
        self.client.connect.assert_not_called()

    def test_redacts_both_output_streams_even_across_chunks(self):
        self.channel.out = deque([b"fake-ssh-", b"password-for-tests-only"])
        self.channel.err = deque([PASSWORD.encode()])
        result = self.run_mock()
        self.assertEqual(result.stdout, "<REDACTED>")
        self.assertEqual(result.stderr, "<REDACTED>")
        self.assertNotIn(PASSWORD, json.dumps(result_payload(result)))
        self.assertNotIn(PASSWORD, repr(result))

    def test_server_diagnostics_do_not_propagate_to_application_logs(self):
        def connect(**_kwargs):
            logging.getLogger(TRANSPORT_LOG).error(PASSWORD)
            logging.getLogger(TRANSPORT_LOG + ".channel").error(PASSWORD)
            raise paramiko.SSHException(PASSWORD)
        self.client.connect.side_effect = connect
        with self.assertNoLogs(level="DEBUG"):
            result = self.run_mock()
        self.assertNotIn(PASSWORD, repr(result))

    def test_remote_status_and_stderr_are_preserved(self):
        for status in (0, 1, 2, 127, 255):
            self.channel.status = status
            result = self.run_mock()
            self.assertEqual(result.exit_code, status)

    def test_no_remote_exit_status_is_technical_failure(self):
        self.channel.status = -1
        result = self.run_mock()
        self.assertIsNone(result.exit_code)
        self.assertIn("without an exit status", result.stderr)

    def test_command_timeout_while_waiting_for_output(self):
        self.channel.ready = False
        result = self.run_mock(command_timeout=0.02)
        self.assertIsNone(result.exit_code)
        self.assertEqual(result.stderr, "SSH command timed out")
        self.assertTrue(self.client.close.called)

    def test_command_timeout_covers_exec_acknowledgement(self):
        closed = Event()
        self.client.close.side_effect = closed.set
        def blocked_exec(*_args, **_kwargs):
            self.assertTrue(closed.wait(1), "command deadline did not close the connection")
            raise paramiko.SSHException(PASSWORD)
        self.client.exec_command.side_effect = blocked_exec
        result = self.run_mock(command_timeout=0.02)
        self.assertIsNone(result.exit_code)
        self.assertEqual(result.stderr, "SSH command timed out")

    def test_busy_stream_cannot_extend_command_deadline(self):
        self.channel.recv_ready = lambda: True
        self.channel.recv = lambda _size: b"x"
        result = self.run_mock(command_timeout=0.02)
        self.assertIsNone(result.exit_code)
        self.assertEqual(result.stderr, "SSH command timed out")


class StrictHostTrustTests(unittest.TestCase):
    """Exercise real Paramiko parsing, lookup, rejection and password selection.

    Only the socket/transport and command response are fixtures. No network traffic.
    """

    @classmethod
    def setUpClass(cls):
        cls.key = paramiko.RSAKey.generate(2048)
        cls.other_key = paramiko.RSAKey.generate(2048)

    def check_connection(self, entry_host, *, host="server.example.invalid", port=22,
                         mismatch=False, bad_password=False, hashed=False):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "known_hosts"
            name = paramiko.HostKeys.hash_host(entry_host) if hashed else entry_host
            contents = f"{name} {self.key.get_name()} {self.key.get_base64()}\n"
            path.write_text(contents, encoding="utf-8")
            client = paramiko.SSHClient()
            real_connect = client.connect
            transport = Mock()
            transport.gss_kex_used = False
            transport.get_security_options.return_value.key_types = ["rsa-sha2-512", "rsa-sha2-256"]
            transport.get_remote_server_key.return_value = self.other_key if mismatch else self.key
            if bad_password:
                transport.auth_password.side_effect = paramiko.AuthenticationException(PASSWORD)
            def fixture_connect(**kwargs):
                return real_connect(
                    **kwargs, sock=Mock(), transport_factory=Mock(return_value=transport),
                )
            client.connect = Mock(side_effect=fixture_connect)
            channel = FakeChannel()
            client.exec_command = Mock(return_value=(Mock(), Mock(channel=channel), Mock()))
            with (
                patch("app.executors.ssh.paramiko.SSHClient", return_value=client),
                patch("socket.socket", side_effect=AssertionError("network forbidden")),
            ):
                result = SSHExecutor().run(
                    host=host, port=port, username="fixture-user", password=PASSWORD,
                    command=COMMAND, connect_timeout=5, command_timeout=5,
                    known_hosts_path=str(path),
                )
            self.assertEqual(path.read_text(encoding="utf-8"), contents)
            transport.auth_publickey.assert_not_called()
            self.assertNotIn(PASSWORD, repr(result))
            return result, transport, client

    def test_matching_key_and_default_or_nondefault_ports(self):
        for host, port in (("server.example.invalid", 22), ("server.example.invalid", 2222),
                           ("192.0.2.20", 2222)):
            name = host if port == 22 else f"[{host}]:{port}"
            for hashed in (False, True):
                result, transport, client = self.check_connection(
                    name, host=host, port=port, hashed=hashed,
                )
                self.assertEqual(result.exit_code, 0, result)
                transport.auth_password.assert_called_once_with("fixture-user", PASSWORD)
                client.exec_command.assert_called_once_with(COMMAND, timeout=5)

    def test_unknown_host_or_wrong_port_fails_before_authentication(self):
        for name, port in (("other.example.invalid", 22), ("server.example.invalid", 2222)):
            result, transport, client = self.check_connection(name, port=port)
            self.assertIsNone(result.exit_code)
            transport.auth_password.assert_not_called()
            client.exec_command.assert_not_called()

    def test_changed_key_fails_before_authentication(self):
        result, transport, client = self.check_connection("server.example.invalid", mismatch=True)
        self.assertEqual(result.stderr, "SSH host key verification failed")
        self.assertIsNone(result.exit_code)
        transport.auth_password.assert_not_called()
        client.exec_command.assert_not_called()

    def test_bad_password_fails_after_host_verification(self):
        result, transport, client = self.check_connection(
            "server.example.invalid", bad_password=True,
        )
        self.assertEqual(result.stderr, "SSH authentication failed")
        self.assertIsNone(result.exit_code)
        transport.auth_password.assert_called_once()
        client.exec_command.assert_not_called()
