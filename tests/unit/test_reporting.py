from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.reporting.final_pdf import render_final_pdf


class ReportingTests(unittest.TestCase):
    def test_multi_page_pdf_contains_snapshot_sections(self):
        snapshot = {
            "snapshot_version": 1,
            "created_at": "2026-08-11T00:00:00Z",
            "overall_status": "WARNING",
            "run": {
                "run_id": "WR-20260811-000000",
                "state": "REVIEW_READY",
                "automation_status": "WARNING",
                "started_by": "tester",
                "created_at": "2026-08-11T00:00:00Z",
                "application_version": "0.1.0",
                "build_id": "test-build",
                "git_commit": "abc123",
                "config_version": "hash",
            },
            "configuration": {
                "hash": "hash",
                "revision": "hash",
                "source_dir": "tests/fixtures/config_valid",
            },
            "review": {
                "reviewer": "alice",
                "decision": "APPROVE",
                "confirmed_at": "2026-08-11T00:01:00Z",
            },
            "site_summaries": [{"site": "site1", "status": "PASS", "result_count": 60}],
            "module_summaries": [{"module": "portainer", "status": "WARNING", "result_count": 60}],
            "parity_summaries": [
                {
                    "check_id": "parity.portainer.expected_replicas",
                    "target": "portainer.expected_replicas",
                    "status": "WARNING",
                    "expected": {"site1": [1], "site2": [2]},
                    "actual": {"match": False},
                    "message": "parity compared separately from site health",
                    "evidence": ["runs/WR-1/site_parity/result-1.json"],
                }
            ],
            "results": [
                {
                    "id": idx,
                    "module": "portainer",
                    "site": "site1",
                    "check_id": f"check-{idx}",
                    "target": "target",
                    "status": "PASS",
                    "message": f"finding {idx}",
                    "expected": {"value": idx},
                    "actual": {"value": idx},
                    "evidence": [f"runs/WR-1/site1/portainer/result-{idx}.json"],
                }
                for idx in range(75)
            ],
            "notes": [
                {
                    "id": 1,
                    "scope": "MODULE",
                    "module": "portainer",
                    "author": "alice",
                    "note": "module note appears",
                    "updated_at": "2026-08-11T00:02:00Z",
                },
                {
                    "id": 2,
                    "scope": "SPLUNK_DASHBOARD",
                    "dashboard_id": "system_health",
                    "author": "alice",
                    "note": "splunk note appears",
                    "updated_at": "2026-08-11T00:03:00Z",
                },
            ],
            "splunk_dashboards": [
                {
                    "id": "system_health",
                    "display_name": "System Health",
                    "url": "https://example.invalid/splunk/system-health",
                    "required_review": True,
                    "note_required": False,
                    "order": 1,
                }
            ],
            "evidence": [
                {
                    "id": 1,
                    "run_id": "WR-1",
                    "result_id": 1,
                    "module": "portainer",
                    "site": "site1",
                    "evidence_type": "normalized_result",
                    "path": "runs/WR-1/site1/portainer/result-1.json",
                    "checksum": "abc",
                    "mime_type": "application/json",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path, checksum = render_final_pdf(snapshot, Path(tmp) / "final.pdf")
            data = Path(path).read_bytes()
        self.assertTrue(checksum)
        self.assertGreater(data.count(b"/Type /Page"), 1)
        self.assertIn(b"Reviewer Notes", data)
        self.assertIn(b"build_id: test-build", data)
        self.assertIn(b"configuration_hash: hash", data)
        self.assertIn(b"module note appears", data)
        self.assertIn(b"splunk note appears", data)
        self.assertIn(b"Evidence References", data)


if __name__ == "__main__":
    unittest.main()
