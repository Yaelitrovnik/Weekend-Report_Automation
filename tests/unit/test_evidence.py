from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.evidence.manager import EvidenceManager
from app.evidence.paths import safe_relative_path


class EvidenceTests(unittest.TestCase):
    def test_path_traversal_is_rejected(self):
        with self.assertRaises(ValueError):
            safe_relative_path("run", "..", "secret")

    def test_evidence_write_has_checksum(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = EvidenceManager(tmp)
            record = manager.write_json("WR-1", "module", "site1", "raw.json", {"ok": True})
            self.assertTrue(record.checksum)
            self.assertTrue((Path(tmp) / record.path).exists())


if __name__ == "__main__":
    unittest.main()
