from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from app.config.loader import parse_env_file


@unittest.skipUnless(shutil.which("pwsh"), "PowerShell is required for the deployment helper test")
class DeploymentSitesTests(unittest.TestCase):
    def test_interactive_setup_writes_chosen_site_and_server_counts(self):
        source = Path("scripts/deployment/configure-infrastructure-env.ps1")
        for count in (1, 2, 3):
            with self.subTest(count=count), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                script = root / source
                script.parent.mkdir(parents=True)
                shutil.copyfile(source, script)
                env_file = root / "deploy/docker/env/infrastructure.env"
                env_file.parent.mkdir(parents=True)
                env_file.write_text("SITE_COUNT=2\nSITE1_ID=primary\n", encoding="utf-8")
                secret = r' fake-ssh-test-$VALUE # "quoted" \\literal '
                known_hosts = root / "test-hosts"
                known_hosts.write_text("test fixture only", encoding="utf-8")
                answers = ["0", str(count)] if count == 1 else ["" if count == 2 else str(count)]
                for index in range(1, count + 1):
                    if index == 2:
                        answers.append("primary")  # Duplicate ID must prompt again.
                    answers.extend(["", "", ""])
                answers.extend(
                    ["fixture-user", secret, str(known_hosts), "UTC", "fixture-ntp"]
                )
                for index in range(1, count + 1):
                    answers.append(str(index))
                    for server in range(1, index + 1):
                        answers.extend([f"srv-{index}-{server}", f"host-{index}-{server}", "", ""])
                (root / "answers.json").write_text(json.dumps(answers), encoding="utf-8")
                harness = root / "test.ps1"
                harness.write_text(
                    "$ErrorActionPreference = 'Stop'\n"
                    "$global:siteTestAnswers = [System.Collections.Generic.Queue[string]]::new()\n"
                    "Get-Content (Join-Path $PSScriptRoot 'answers.json') -Raw | "
                    "ConvertFrom-Json | "
                    "ForEach-Object { $global:siteTestAnswers.Enqueue($_) }\n"
                    "function Read-Host { param([string]$Prompt, [switch]$AsSecureString)\n"
                    "  if ($global:siteTestAnswers.Count -eq 0) { throw 'Unexpected prompt' }\n"
                    "  $answer = $global:siteTestAnswers.Dequeue()\n"
                    "  if ($AsSecureString) {\n"
                    "    return ConvertTo-SecureString $answer -AsPlainText -Force\n"
                    "  }\n"
                    "  if ($Prompt -like 'SSH password*') { throw 'Password must be hidden' }\n"
                    "  return $answer\n"
                    "}\n"
                    "& (Join-Path $PSScriptRoot "
                    "'scripts/deployment/configure-infrastructure-env.ps1')\n"
                    "if ($global:siteTestAnswers.Count -ne 0) { throw 'Unused answers' }\n",
                    encoding="utf-8",
                )
                completed = subprocess.run(
                    [str(shutil.which("pwsh")), "-NoProfile", "-File", str(harness)],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                self.assertNotIn(secret, completed.stdout + completed.stderr)
                env = parse_env_file(env_file)
                self.assertEqual(env["SSH_USERNAME"], "fixture-user")
                self.assertEqual(env["SSH_PASSWORD"], secret)
                self.assertEqual(env["SSH_KNOWN_HOSTS_PATH"], "/app/secrets/known_hosts")
                self.assertNotIn("SSH_PRIVATE_KEY_PATH", env)
                secrets = root / "deploy/docker/secrets"
                self.assertEqual([path.name for path in secrets.iterdir()], ["known_hosts"])
                self.assertEqual(
                    (secrets / "known_hosts").read_bytes(), known_hosts.read_bytes()
                )
                self.assertEqual(env["SITE_COUNT"], str(count))
                self.assertEqual(env["SITE1_ID"], "primary")
                self.assertNotIn(f"SITE{count + 1}_ID", env)
                self.assertIn(f"sites 1 through {count}", completed.stdout)
                for index in range(1, count + 1):
                    self.assertEqual(env[f"SITE{index}_SERVER_COUNT"], str(index))
                    for server in range(1, index + 1):
                        self.assertEqual(
                            env[f"SITE{index}_SERVER_{server}_ID"], f"srv-{index}-{server}"
                        )
