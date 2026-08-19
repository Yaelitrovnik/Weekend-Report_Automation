from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.loader import load_config_dir
from app.config.validation import validate_config


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config")
    parser.add_argument("--expect-invalid", action="store_true")
    args = parser.parse_args()
    config = load_config_dir(args.config)
    report = validate_config(config)
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
