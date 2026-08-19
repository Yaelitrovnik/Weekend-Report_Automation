from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = ROOT / "deploy" / "docker" / "compose.yml"
CI_COMPOSE_FILE = ROOT / "deploy" / "docker" / "compose.ci.yml"


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
        "WEEKEND_REPORT_APP_VERSION": "ci",
        "WEEKEND_REPORT_BUILD_ID": "ci-local",
        "WEEKEND_REPORT_AUTH_MODE": "development",
        "WEEKEND_REPORT_AUTH_PROVIDER": "trusted_header",
        "WEEKEND_REPORT_AUTH_TRUSTED_HEADER": "X-Authenticated-User",
        "WEEKEND_REPORT_AUTHORIZED_REVIEWERS": "ci-reviewer",
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


def gate_config() -> None:
    _run([sys.executable, "scripts/validate_config.py", "--config", "tests/fixtures/config_valid"])
    _run(
        [
            sys.executable,
            "scripts/validate_config.py",
            "--config",
            "config",
            "--expect-invalid",
        ]
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
    _run(
        _python_module(
            "unittest", "discover", "-s", "tests/contract", "-p", "test_*.py", "-v"
        )
    )


def gate_integration() -> None:
    loader = unittest.TestLoader()
    discovered = loader.discover(
        str(ROOT / "tests" / "integration"),
        pattern="test_*.py",
        top_level_dir=str(ROOT),
    )
    selected = unittest.TestSuite(
        test
        for test in _iter_tests(discovered)
        if "test_postgres_concurrency" not in test.id()
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
    _run(["docker", "compose", "-f", str(COMPOSE_FILE), "config"], env=env)
    _run(["docker", "compose", "-f", str(CI_COMPOSE_FILE), "config"], env=env)


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_health(port: int, *, timeout_seconds: int = 90) -> None:
    deadline = time.monotonic() + timeout_seconds
    url = f"http://127.0.0.1:{port}/healthz"
    last_error = "health endpoint was not contacted"
    while time.monotonic() < deadline:
        try:
            # The target is always a loopback CI endpoint created by this script.
            with urllib.request.urlopen(url, timeout=3) as response:
                body = response.read().decode("utf-8", errors="replace")
                if response.status == 200 and '"status":"ok"' in body.replace(" ", ""):
                    print(f"health check passed: {body}")
                    return
                last_error = f"HTTP {response.status}: {body}"
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last_error = str(exc)
        time.sleep(2)
    raise RuntimeError(f"built-image health check failed: {last_error}")


def gate_image_smoke(image: str) -> None:
    if not image.strip():
        raise SystemExit("--image is required for image-smoke")
    port = _free_local_port()
    project = f"weekend-report-ci-{os.getpid()}"
    env = _ci_env(
        {
            "WEEKEND_REPORT_CI_IMAGE": image,
            "WEEKEND_REPORT_CI_PORT": str(port),
            "WEEKEND_REPORT_APP_VERSION": os.getenv("WEEKEND_REPORT_APP_VERSION", "ci-image"),
            "WEEKEND_REPORT_BUILD_ID": os.getenv("WEEKEND_REPORT_BUILD_ID", project),
        }
    )
    compose = ["docker", "compose", "-p", project, "-f", str(CI_COMPOSE_FILE)]
    try:
        _run([*compose, "up", "-d"], env=env)
        _wait_for_health(port)
        _run([*compose, "exec", "-T", "web", "python", "scripts/migrate.py"], env=env)
        completed = subprocess.run(
            [*compose, "ps", "--services", "--status", "running"],
            cwd=ROOT,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        running = {line.strip() for line in completed.stdout.splitlines() if line.strip()}
        expected = {"postgres", "web", "worker"}
        missing = expected - running
        if missing:
            raise RuntimeError(f"built-image smoke missing running services: {sorted(missing)}")
        print(f"built-image smoke passed for {image}; services={sorted(running)}")
    except Exception:
        subprocess.run([*compose, "logs", "--no-color"], cwd=ROOT, env=env, check=False)
        raise
    finally:
        subprocess.run(
            [*compose, "down", "-v", "--remove-orphans"],
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
