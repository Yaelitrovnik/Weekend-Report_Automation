from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.config.schema import REQUIRED_FILES


class ConfigError(ValueError):
    pass


def _load_text(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml

        loaded = yaml.safe_load(text)
    except ModuleNotFoundError:
        loaded = json.loads(text)
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ConfigError(f"{path.name}: expected mapping at top level")
    return loaded


def load_config_dir(config_dir: str | Path) -> dict[str, Any]:
    root = Path(config_dir)
    data: dict[str, Any] = {"_config_dir": str(root)}
    for filename in REQUIRED_FILES:
        path = root / filename
        if not path.exists():
            raise ConfigError(f"Missing required configuration file: {filename}")
        data[filename.removesuffix(".yml")] = _load_text(path)
    data["_config_hash"] = config_hash(root)
    return data


def config_hash(config_dir: str | Path) -> str:
    root = Path(config_dir)
    h = hashlib.sha256()
    for filename in REQUIRED_FILES:
        path = root / filename
        if path.exists():
            h.update(filename.encode("utf-8"))
            h.update(path.read_bytes())
    return h.hexdigest()
