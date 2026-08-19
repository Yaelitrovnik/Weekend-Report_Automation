from __future__ import annotations

import unittest

from app.domain import CheckResult, CheckStatus, to_jsonable


class ResultContractTests(unittest.TestCase):
    def test_common_result_contract_serializes_required_fields(self):
        result = CheckResult(
            run_id="WR-CI-CONTRACT-000001",
            module="portainer",
            check_id="portainer.service.required",
            status=CheckStatus.PASS,
            message="required service exists",
            site="site1",
            target="service-a",
            expected={"present": True},
            actual={"present": True},
            started_at="2026-08-18T00:00:00Z",
            finished_at="2026-08-18T00:00:01Z",
            evidence=["runs/WR-CI-CONTRACT-000001/site1/portainer/service-a.json"],
            metadata={"source": "fixture"},
        )
        payload = to_jsonable(result)
        for field in (
            "run_id",
            "module",
            "check_id",
            "site",
            "target",
            "expected",
            "actual",
            "status",
            "message",
            "started_at",
            "finished_at",
            "evidence",
            "metadata",
        ):
            self.assertIn(field, payload)
        self.assertEqual(payload["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
