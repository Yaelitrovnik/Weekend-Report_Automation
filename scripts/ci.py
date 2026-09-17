from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import unittest
from collections.abc import Iterable
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.fixtures.config_factory import valid_env  # noqa: E402

COMPOSE_FILE = ROOT / "deploy" / "docker" / "compose.yml"
CI_COMPOSE_FILE = ROOT / "deploy" / "docker" / "compose.ci.yml"
DEPLOY_ENV_DIR = ROOT / "deploy" / "docker" / "env"

COMPOSE_ENV_FILE_KEYS = {
    "app.env": [
        "WEEKEND_REPORT_APP_VERSION",
        "WEEKEND_REPORT_BUILD_ID",
        "WEEKEND_REPORT_GIT_COMMIT",
        "WEEKEND_REPORT_CONFIG_DIR",
        "WEEKEND_REPORT_EVIDENCE_ROOT",
        "WEEKEND_REPORT_WORKER_POLL_SECONDS",
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "WEEKEND_REPORT_DATABASE_URL",
        "WEEKEND_REPORT_CSRF_SIGNING_KEY",
        "WEEKEND_REPORT_CSRF_TTL_SECONDS",
        "WEEKEND_REPORT_DB_MANUAL_CHECK_ENABLED",
        "WEEKEND_REPORT_DB_MANUAL_CHECK_DISPLAY_NAME",
        "WEEKEND_REPORT_DB_MANUAL_CHECK_SCRIPT_PATH",
        "WEEKEND_REPORT_DB_MANUAL_CHECK_REQUIRED",
    ],
    "portainer.env": [
        "PORTAINER_COLLECTION_MODE",
        "PORTAINER_API_CONTRACT",
        "PORTAINER_AUTH_TYPE",
        "PORTAINER_SITE1_URL",
        "PORTAINER_SITE1_ENDPOINT_ID",
        "PORTAINER_SITE1_TOKEN",
        "PORTAINER_SITE1_TLS_VERIFY",
        "PORTAINER_SITE2_URL",
        "PORTAINER_SITE2_ENDPOINT_ID",
        "PORTAINER_SITE2_TOKEN",
        "PORTAINER_SITE2_TLS_VERIFY",
    ],
    "doctor.env": [
        "DOCTOR_MODE",
        "DOCTOR_SITE1_API_URL",
        "DOCTOR_SITE2_API_URL",
    ],
    "rabbitmq.env": [
        "RABBITMQ_COLLECTION_MODE",
        "RABBITMQ_SITE1_URL",
        "RABBITMQ_SITE1_USER",
        "RABBITMQ_SITE1_PASSWORD",
        "RABBITMQ_SITE1_TLS_VERIFY",
        "RABBITMQ_SITE1_CA_FILE",
        "RABBITMQ_SITE2_URL",
        "RABBITMQ_SITE2_USER",
        "RABBITMQ_SITE2_PASSWORD",
        "RABBITMQ_SITE2_TLS_VERIFY",
        "RABBITMQ_SITE2_CA_FILE",
    ],
    "recording.env": [
        "RECORDING_COLLECTION_MODE",
        "RECORDING_MANAGER_WEBAPP_URL",
        "RECORDING_SITE1_WEBAPP_URL",
        "RECORDING_SITE1_SERVER_REFERENCE",
        "RECORDING_SITE2_WEBAPP_URL",
        "RECORDING_SITE2_SERVER_REFERENCE",
    ],
    "infrastructure.env": [
        "SITE_COUNT",
        "SITE1_ID",
        "SITE1_DISPLAY_NAME",
        "SITE1_PURPOSE",
        "SITE2_ID",
        "SITE2_DISPLAY_NAME",
        "SITE2_PURPOSE",
        "SSH_USERNAME",
        "SSH_PRIVATE_KEY_PATH",
        "SSH_KNOWN_HOSTS_PATH",
        "CHRONY_TIMEZONE",
        "CHRONY_SOURCE",
        "SITE1_SERVER_COUNT",
        "SITE1_SERVER_1_ID",
        "SITE1_SERVER_1_HOST",
        "SITE1_SERVER_1_PORT",
        "SITE1_SERVER_1_REQUIRED",
        "SITE2_SERVER_COUNT",
        "SITE2_SERVER_1_ID",
        "SITE2_SERVER_1_HOST",
        "SITE2_SERVER_1_PORT",
        "SITE2_SERVER_1_REQUIRED",
    ],
    "splunk.env": [
        "SPLUNK_DASHBOARD_COUNT",
        "SPLUNK_DASHBOARD_1_ID",
        "SPLUNK_DASHBOARD_1_DISPLAY_NAME",
        "SPLUNK_DASHBOARD_1_URL",
        "SPLUNK_DASHBOARD_1_REQUIRED_REVIEW",
        "SPLUNK_DASHBOARD_1_NOTE_REQUIRED",
        "SPLUNK_DASHBOARD_1_ORDER",
        "SPLUNK_DASHBOARD_2_ID",
        "SPLUNK_DASHBOARD_2_DISPLAY_NAME",
        "SPLUNK_DASHBOARD_2_URL",
        "SPLUNK_DASHBOARD_2_REQUIRED_REVIEW",
        "SPLUNK_DASHBOARD_2_NOTE_REQUIRED",
        "SPLUNK_DASHBOARD_2_ORDER",
        "SPLUNK_DASHBOARD_3_ID",
        "SPLUNK_DASHBOARD_3_DISPLAY_NAME",
        "SPLUNK_DASHBOARD_3_URL",
        "SPLUNK_DASHBOARD_3_REQUIRED_REVIEW",
        "SPLUNK_DASHBOARD_3_NOTE_REQUIRED",
        "SPLUNK_DASHBOARD_3_ORDER",
        "SPLUNK_DASHBOARD_4_ID",
        "SPLUNK_DASHBOARD_4_DISPLAY_NAME",
        "SPLUNK_DASHBOARD_4_URL",
        "SPLUNK_DASHBOARD_4_REQUIRED_REVIEW",
        "SPLUNK_DASHBOARD_4_NOTE_REQUIRED",
        "SPLUNK_DASHBOARD_4_ORDER",
    ],
}


def _run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    display = " ".join(command)
    print(f"+ {display}", flush=True)
    subprocess.run(command, cwd=ROOT, env=env, check=True)


def _python_module(module: str, *args: str) -> list[str]:
    return [sys.executable, "-m", module, *args]


def _ci_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    defaults = {
        "POSTGRES_PASSWORD": "ci-only-postgres-password",
        "WEEKEND_REPORT_IMAGE": "weekend-report:ci-validation",
        "WEEKEND_REPORT_APP_VERSION": "ci",
        "WEEKEND_REPORT_BUILD_ID": "ci-local",
        "WEEKEND_REPORT_CSRF_SIGNING_KEY": "ci-only-csrf-signing-key-not-for-production",
        "WEEKEND_REPORT_CSRF_TTL_SECONDS": "3600",
        "WEEKEND_REPORT_CI_IMAGE": "weekend-report:ci-validation",
        "WEEKEND_REPORT_CI_PORT": "18080",
    }
    for key, value in defaults.items():
        env.setdefault(key, value)
    if extra:
        env.update(extra)
    return env


def _compose_runtime_values() -> dict[str, str]:
    values = valid_env()
    values.update(
        {
            "POSTGRES_DB": "weekend_report_ci",
            "POSTGRES_USER": "weekend_report",
            "POSTGRES_PASSWORD": "ci-only-postgres-password",
            "WEEKEND_REPORT_DATABASE_URL": (
                "postgresql://weekend_report:"
                "ci-only-postgres-password@postgres:5432/weekend_report_ci"
            ),
            "WEEKEND_REPORT_CONFIG_DIR": "/app/config",
            "WEEKEND_REPORT_EVIDENCE_ROOT": "/app/runs",
            "WEEKEND_REPORT_GIT_COMMIT": "<NOT_APPLICABLE>",
            "WEEKEND_REPORT_WORKER_POLL_SECONDS": "0.5",
        }
    )
    return values


@contextmanager
def _temporary_compose_env_files():
    DEPLOY_ENV_DIR.mkdir(parents=True, exist_ok=True)
    values = _compose_runtime_values()
    created: list[Path] = []
    for filename, keys in COMPOSE_ENV_FILE_KEYS.items():
        path = DEPLOY_ENV_DIR / filename
        if path.exists():
            continue
        lines = [f"{key}={values.get(key, '')}" for key in keys]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        created.append(path)
    try:
        yield
    finally:
        for path in created:
            path.unlink(missing_ok=True)


def gate_config() -> None:
    complete_env = _ci_env(valid_env())
    _run(
        [
            sys.executable,
            "scripts/validate_config.py",
            "--config",
            "deploy/docker/config",
            "--no-production-preflight",
        ],
        env=complete_env,
    )
    _run(
        [
            sys.executable,
            "scripts/validate_config.py",
            "--config",
            "deploy/docker/config",
            "--expect-invalid",
        ],
        env=_ci_env(),
    )


def gate_lint() -> None:
    _run(_python_module("ruff", "check", ".", "--no-cache"))


def gate_typecheck() -> None:
    _run(_python_module("mypy", "app", "scripts", "tests"))


def gate_unit() -> None:
    _run(_python_module("unittest", "discover", "-s", "tests/unit", "-p", "test_*.py", "-v"))


def _iter_tests(suite: unittest.TestSuite) -> Iterable[unittest.TestCase]:
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from _iter_tests(test)
        else:
            yield test


def gate_contract() -> None:
    _run(_python_module("unittest", "discover", "-s", "tests/contract", "-p", "test_*.py", "-v"))


def gate_integration() -> None:
    loader = unittest.TestLoader()
    discovered = loader.discover(
        str(ROOT / "tests" / "integration"),
        pattern="test_*.py",
        top_level_dir=str(ROOT),
    )
    selected = unittest.TestSuite(
        test for test in _iter_tests(discovered) if "test_postgres_concurrency" not in test.id()
    )
    result = unittest.TextTestRunner(verbosity=2).run(selected)
    if not result.wasSuccessful():
        raise SystemExit(1)


def gate_postgres() -> None:
    if not os.getenv("WEEKEND_REPORT_TEST_POSTGRES_URL"):
        raise SystemExit(
            "WEEKEND_REPORT_TEST_POSTGRES_URL is required for the PostgreSQL concurrency gate"
        )
    if os.getenv("WEEKEND_REPORT_TEST_POSTGRES_DISPOSABLE") != "1":
        raise SystemExit(
            "WEEKEND_REPORT_TEST_POSTGRES_DISPOSABLE=1 is required for the PostgreSQL CI gate"
        )
    _run(_python_module("unittest", "tests.integration.test_postgres_concurrency", "-v"))


def gate_e2e() -> None:
    _run([sys.executable, "scripts/ci_e2e.py"])


def gate_audit() -> None:
    _run(_python_module("pip_audit", "-r", "requirements.txt"))


def gate_compose_config() -> None:
    env = _ci_env()
    with _temporary_compose_env_files():
        _run(["docker", "compose", "-f", str(COMPOSE_FILE), "config"], env=env)
        _run(["docker", "compose", "-f", str(CI_COMPOSE_FILE), "config"], env=env)


def _wait_for_health(
    compose: list[str],
    env: dict[str, str],
    *,
    timeout_seconds: int = 90,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = "health endpoint was not contacted"

    health_command = [
        *compose,
        "exec",
        "-T",
        "web",
        "python",
        "-c",
        (
            "import urllib.request; "
            "r=urllib.request.urlopen("
            "'http://127.0.0.1:8080/healthz', timeout=3"
            "); "
            "body=r.read().decode('utf-8', errors='replace'); "
            "assert r.status == 200, body; "
            "assert '\"status\":\"ok\"' in body.replace(' ', ''), body; "
            "print(body)"
        ),
    ]

    while time.monotonic() < deadline:
        completed = subprocess.run(
            health_command,
            cwd=ROOT,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )

        if completed.returncode == 0:
            print(
                f"health check passed: {completed.stdout.strip()}",
                flush=True,
            )
            return

        last_error = (
            completed.stderr.strip()
            or completed.stdout.strip()
            or f"exit code {completed.returncode}"
        )

        time.sleep(2)

    raise RuntimeError(f"built-image health check failed: {last_error}")


def gate_image_smoke(image: str) -> None:
    if not image.strip():
        raise SystemExit("--image is required for image-smoke")

    project = f"weekend-report-ci-{os.getpid()}"

    env = _ci_env(
        {
            "WEEKEND_REPORT_CI_IMAGE": image,
            "WEEKEND_REPORT_APP_VERSION": os.getenv(
                "WEEKEND_REPORT_APP_VERSION",
                "ci-image",
            ),
            "WEEKEND_REPORT_BUILD_ID": os.getenv(
                "WEEKEND_REPORT_BUILD_ID",
                project,
            ),
        }
    )

    compose = [
        "docker",
        "compose",
        "-p",
        project,
        "-f",
        str(CI_COMPOSE_FILE),
    ]

    try:
        _run([*compose, "create"], env=env)

        _run(
            [
                *compose,
                "cp",
                "deploy/docker/config/rules.yml",
                "web:/app/config/rules.yml",
            ],
            env=env,
        )

        _run(
            [
                *compose,
                "cp",
                "deploy/docker/config/rules.yml",
                "worker:/app/config/rules.yml",
            ],
            env=env,
        )

        _run([*compose, "up", "-d"], env=env)

        _wait_for_health(
            compose,
            env,
        )

        _run(
            [
                *compose,
                "exec",
                "-T",
                "web",
                "python",
                "scripts/migrate.py",
            ],
            env=env,
        )

        completed = subprocess.run(
            [
                *compose,
                "ps",
                "--services",
                "--status",
                "running",
            ],
            cwd=ROOT,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )

        running = {
            line.strip()
            for line in completed.stdout.splitlines()
            if line.strip()
        }

        expected = {"postgres", "web", "worker"}
        missing = expected - running

        if missing:
            raise RuntimeError(
                f"built-image smoke missing running services: "
                f"{sorted(missing)}"
            )

        print(
            f"built-image smoke passed for {image}; "
            f"services={sorted(running)}"
        )

    except Exception:
        subprocess.run(
            [*compose, "logs", "--no-color"],
            cwd=ROOT,
            env=env,
            check=False,
        )
        raise

    finally:
        subprocess.run(
            [
                *compose,
                "down",
                "-v",
                "--remove-orphans",
            ],
            cwd=ROOT,
            env=env,
            check=False,
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Portable Weekend Report CI quality-gate commands used locally, "
            "by GitHub, and by GitLab."
        )
    )
    sub = parser.add_subparsers(dest="gate", required=True)
    for name in (
        "config",
        "lint",
        "typecheck",
        "unit",
        "integration",
        "contract",
        "postgres",
        "e2e",
        "audit",
        "compose-config",
    ):
        sub.add_parser(name)
    image_smoke = sub.add_parser("image-smoke")
    image_smoke.add_argument("--image", required=True)
    args = parser.parse_args()

    gates = {
        "config": gate_config,
        "lint": gate_lint,
        "typecheck": gate_typecheck,
        "unit": gate_unit,
        "integration": gate_integration,
        "contract": gate_contract,
        "postgres": gate_postgres,
        "e2e": gate_e2e,
        "audit": gate_audit,
        "compose-config": gate_compose_config,
    }
    if args.gate == "image-smoke":
        gate_image_smoke(args.image)
    else:
        gates[args.gate]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
