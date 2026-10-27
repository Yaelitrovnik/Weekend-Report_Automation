from __future__ import annotations

import copy
import re

from app.config.loader import load_config_dir
from tests.fixtures.config_factory import load_fixture_config, valid_env


def dynamic_env(count: int) -> dict[str, str]:
    base = valid_env()
    numbered = re.compile(r"^(?:PORTAINER_|DOCTOR_|RABBITMQ_)?SITE[0-9]+_")
    env = {key: value for key, value in base.items() if not numbered.match(key)}
    env["SITE_COUNT"] = str(count)
    for index in range(1, count + 1):
        for key, value in base.items():
            if re.match(r"^(?:PORTAINER_|DOCTOR_|RABBITMQ_)?SITE1_", key):
                env[key.replace("SITE1_", f"SITE{index}_")] = (
                    value.replace("site1", f"site{index}")
                    .replace("fixture1", f"fixture{index}")
                    .replace("srv1", f"srv{index}")
                )
        env[f"SITE{index}_ID"] = f"region-{index}"
        env[f"SITE{index}_DISPLAY_NAME"] = f"Region {index}"
    return env


def dynamic_config(count: int) -> dict:
    config = load_config_dir("tests/fixtures/config_valid", env=dynamic_env(count))
    source = load_fixture_config()
    for area in ("portainer_expected", "rabbitmq_expected", "servers"):
        config[area]["fixture_actual"] = {"sites": {}}
        for index in range(1, count + 1):
            site_id = f"region-{index}"
            actual = copy.deepcopy(source[area]["fixture_actual"]["sites"]["site1"])
            if area == "portainer_expected":
                actual["services"][0]["site"] = site_id
                second = copy.deepcopy(actual["services"][0])
                second.update(id="svc2", name="second-service", site=site_id)
                actual["services"].append(second)
            if area == "servers":
                actual["servers"][f"srv{index}"] = actual["servers"].pop("srv1")
            config[area]["fixture_actual"]["sites"][site_id] = actual
        if area != "servers":
            config[area]["collection_mode"] = "fixture"
    config["doctor"]["doctor"]["fixture_actual"] = {
        "sites": {
            f"region-{index}": copy.deepcopy(
                source["doctor"]["doctor"]["fixture_actual"]["sites"]["site1"]
            )
            for index in range(1, count + 1)
        }
    }
    return config
