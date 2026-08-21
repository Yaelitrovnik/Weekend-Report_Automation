from __future__ import annotations

import json
import logging
import time
import unittest

from app.logging_utils import JsonLogFormatter, duration_ms


class LoggingUtilsTests(unittest.TestCase):
    def test_formatter_produces_valid_json_with_extra_fields(self):
        logger = logging.getLogger("test.logging_utils.formatter")
        record = logger.makeRecord(
            logger.name,
            logging.INFO,
            __file__,
            1,
            "module finished",
            (),
            None,
            extra={
                "event": "module_finish",
                "run_id": "WR-TEST",
                "module_name": "portainer",
                "duration_ms": 12,
                "outcome": "success",
            },
        )
        payload = json.loads(JsonLogFormatter().format(record))
        self.assertEqual(payload["message"], "module finished")
        self.assertEqual(payload["level"], "INFO")
        self.assertEqual(payload["event"], "module_finish")
        self.assertEqual(payload["run_id"], "WR-TEST")
        self.assertEqual(payload["module_name"], "portainer")
        self.assertEqual(payload["duration_ms"], 12)

    def test_extra_key_module_collides_with_logrecord_reserved_attribute(self):
        # Documents why module_name is used instead of module throughout
        # app/orchestrator/runner.py and app/worker/main.py: "module" is a
        # standard LogRecord attribute (the Python module name of the
        # caller), so passing it via extra raises KeyError. Do not
        # "simplify" the field name back to module.
        logger = logging.getLogger("test.logging_utils.collision")
        with self.assertRaises(KeyError):
            logger.makeRecord(
                logger.name,
                logging.INFO,
                __file__,
                1,
                "boom",
                (),
                None,
                extra={"module": "portainer"},
            )

    def test_duration_ms_measures_elapsed_time(self):
        started = time.monotonic()
        time.sleep(0.01)
        elapsed = duration_ms(started)
        self.assertGreaterEqual(elapsed, 10)
        self.assertLess(elapsed, 5000)


if __name__ == "__main__":
    unittest.main()