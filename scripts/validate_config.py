from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.loader import ConfigError, load_config_dir, load_env_files
from app.config.validation import validate_config


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="deploy/docker/config")
    parser.add_argument(
        "--env-file",
        action="append",
        default=[],
        help="Load a dotenv-style runtime ENV file before validating configuration.",
    )
    parser.add_argument(
        "--no-production-preflight",
        action="store_true",
        help="Validate config shape without requiring production-only runtime readiness checks.",
    )
    parser.add_argument("--expect-invalid", action="store_true")
    args = parser.parse_args()
    try:
        load_env_files(args.env_file)
        config = load_config_dir(args.config)
        report = validate_config(
            config,
            production_preflight=not args.no_production_preflight,
        )
    except ConfigError as exc:
        if args.expect_invalid:
            print(f"Configuration invalid as expected: {exc}")
            return 0
        print(f"ERROR: {exc}")
        return 1
    for line in report.lines():
        print(line)
    if args.expect_invalid:
        if report.ok:
            print("Expected invalid configuration, but validation passed")
            return 1
        print("Configuration invalid as expected")
        return 0
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
