from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.domain import EvidenceRecord
from app.evidence.checksum import sha256_file
from app.evidence.paths import ensure_under, safe_relative_path
from app.time_utils import iso_now


class EvidenceManager:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def run_dir(self, run_id: str) -> Path:
        path = self.root / safe_relative_path("runs", run_id)
        return ensure_under(self.root, path)

    def write_json(
        self,
        run_id: str,
        module: str,
        site: str | None,
        filename: str,
        data: Any,
        *,
        evidence_type: str = "raw",
    ) -> EvidenceRecord:
        raw = json.dumps(data, indent=2, sort_keys=True).encode("utf-8")
        return self._write(
            run_id, module, site, filename, raw, "application/json", evidence_type=evidence_type
        )

    def write_text(
        self,
        run_id: str,
        module: str,
        site: str | None,
        filename: str,
        data: str,
        mime_type: str = "text/plain",
        *,
        evidence_type: str = "raw",
    ) -> EvidenceRecord:
        return self._write(
            run_id,
            module,
            site,
            filename,
            data.encode("utf-8"),
            mime_type,
            evidence_type=evidence_type,
        )

    def _write(
        self,
        run_id: str,
        module: str,
        site: str | None,
        filename: str,
        data: bytes,
        mime_type: str,
        *,
        evidence_type: str,
    ) -> EvidenceRecord:
        parts = [run_id]
        if site:
            parts.append(site)
        parts.extend([module, filename])
        relative = safe_relative_path(*parts)
        path = ensure_under(self.root, self.root / "runs" / relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
        checksum = sha256_file(path)
        return EvidenceRecord(
            run_id,
            module,
            site,
            evidence_type,
            path.relative_to(self.root).as_posix(),
            checksum,
            mime_type,
            created_at=iso_now(),
        )

    def write_final_json(self, run_id: str, filename: str, data: Any) -> EvidenceRecord:
        raw = json.dumps(data, indent=2, sort_keys=True).encode("utf-8")
        path = ensure_under(
            self.root, self.root / "runs" / safe_relative_path(run_id, "final", filename)
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return EvidenceRecord(
            run_id,
            "final",
            None,
            "final",
            path.relative_to(self.root).as_posix(),
            sha256_file(path),
            "application/json",
            created_at=iso_now(),
        )

    def absolute(self, relative_path: str) -> Path:
        return ensure_under(self.root, self.root / relative_path)
