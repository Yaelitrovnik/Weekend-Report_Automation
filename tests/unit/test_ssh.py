from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from app.executors.ssh import SSHExecutor


class SSHExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.executor = SSHExecutor()

    def test_private_key_is_required(self):
        with self.assertRaisesRegex(
            RuntimeError,
            "SSH_PRIVATE_KEY_PATH",
        ):
            self.executor.run(
                host="server.example.invalid",
                port=22,
                username="monitor",
                command="df -h /",
                connect_timeout=5,
                command_timeout=5,
                key_path="",
                known_hosts_path="/app/secrets/known_hosts",
            )

    def test_known_hosts_file_is_required(self):
        with self.assertRaisesRegex(
            RuntimeError,
            "SSH_KNOWN_HOSTS_PATH",
        ):
            self.executor.run(
                host="server.example.invalid",
                port=22,
                username="monitor",
                command="df -h /",
                connect_timeout=5,
                command_timeout=5,
                key_path="/app/secrets/ssh_private_key",
                known_hosts_path="",
            )

    @patch("app.executors.ssh.subprocess.run")
    def test_ssh_command_enforces_strict_host_verification(
        self,
        run_mock,
    ):
        run_mock.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="ok\n",
            stderr="",
        )

        result = self.executor.run(
            host="server.example.invalid",
            port=2222,
            username="monitor",
            command="df -h /",
            connect_timeout=5,
            command_timeout=10,
            key_path="/app/secrets/ssh_private_key",
            known_hosts_path="/app/secrets/known_hosts",
        )

        run_mock.assert_called_once()

        args = run_mock.call_args.args[0]
        kwargs = run_mock.call_args.kwargs

        self.assertEqual(
            args[:5],
            [
                "ssh",
                "-i",
                "/app/secrets/ssh_private_key",
                "-p",
                "2222",
            ],
        )

        self.assertIn("BatchMode=yes", args)
        self.assertIn("IdentitiesOnly=yes", args)
        self.assertIn("StrictHostKeyChecking=yes", args)
        self.assertIn(
            "UserKnownHostsFile=/app/secrets/known_hosts",
            args,
        )
        self.assertIn("ConnectTimeout=5", args)

        self.assertNotIn("StrictHostKeyChecking=no", args)
        self.assertNotIn("StrictHostKeyChecking=accept-new", args)
        self.assertNotIn("UserKnownHostsFile=/dev/null", args)

        self.assertEqual(
            args[-2:],
            [
                "monitor@server.example.invalid",
                "df -h /",
            ],
        )

        self.assertFalse(kwargs["check"])
        self.assertTrue(kwargs["capture_output"])
        self.assertTrue(kwargs["text"])
        self.assertEqual(kwargs["timeout"], 10)

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout, "ok\n")


if __name__ == "__main__":
    unittest.main()