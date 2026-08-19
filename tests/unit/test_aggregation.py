from __future__ import annotations

import unittest

from app.domain import CheckResult, CheckStatus
from app.orchestrator.aggregation import aggregate_status


class AggregationTests(unittest.TestCase):
    def r(self, status: CheckStatus) -> CheckResult:
        return CheckResult("WR-1", "m", "c", status, status.value)

    def test_error_and_fail_are_blocking(self):
        self.assertEqual(
            aggregate_status([self.r(CheckStatus.PASS), self.r(CheckStatus.ERROR)]),
            CheckStatus.ERROR,
        )
        self.assertEqual(
            aggregate_status([self.r(CheckStatus.PASS), self.r(CheckStatus.FAIL)]), CheckStatus.FAIL
        )

    def test_skipped_and_manual_review_never_become_pass(self):
        self.assertEqual(aggregate_status([self.r(CheckStatus.SKIPPED)]), CheckStatus.SKIPPED)
        self.assertEqual(
            aggregate_status([self.r(CheckStatus.MANUAL_REVIEW)]), CheckStatus.MANUAL_REVIEW
        )

    def test_configured_blocking_rules_drive_aggregation(self):
        config = {
            "rules": {
                "aggregation": {
                    "fail_blocks": False,
                    "error_blocks": True,
                    "manual_review_blocks": False,
                    "skipped_blocks": False,
                    "warning_overall_status": "WARNING",
                }
            }
        }
        self.assertEqual(
            aggregate_status([self.r(CheckStatus.PASS), self.r(CheckStatus.FAIL)], config),
            CheckStatus.WARNING,
        )
        self.assertEqual(
            aggregate_status([self.r(CheckStatus.ERROR), self.r(CheckStatus.PASS)], config),
            CheckStatus.ERROR,
        )

    def test_nonblocking_nonpass_statuses_do_not_silently_pass(self):
        config = {
            "rules": {
                "aggregation": {
                    "fail_blocks": False,
                    "error_blocks": False,
                    "manual_review_blocks": False,
                    "skipped_blocks": False,
                    "warning_overall_status": "PASS",
                }
            }
        }
        self.assertEqual(
            aggregate_status([self.r(CheckStatus.SKIPPED)], config),
            CheckStatus.WARNING,
        )
        self.assertEqual(
            aggregate_status([self.r(CheckStatus.MANUAL_REVIEW)], config),
            CheckStatus.WARNING,
        )

    def test_warning_overall_status_is_configurable(self):
        config = {
            "rules": {
                "aggregation": {
                    "fail_blocks": True,
                    "error_blocks": True,
                    "manual_review_blocks": True,
                    "skipped_blocks": True,
                    "warning_overall_status": "PASS",
                }
            }
        }
        self.assertEqual(
            aggregate_status([self.r(CheckStatus.WARNING)], config),
            CheckStatus.PASS,
        )


if __name__ == "__main__":
    unittest.main()
