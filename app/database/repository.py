from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path
from typing import Any

from app.database.models import POSTGRES_SCHEMA, SQLITE_SCHEMA
from app.domain import (
    CheckResult,
    CheckStatus,
    EvidenceRecord,
    ReviewNote,
    RunRecord,
    RunState,
    to_jsonable,
)
from app.orchestrator.lock import ACTIVE_STATES, DuplicateActiveRun, InvalidRunTransition
from app.runtime_identity import GIT_NOT_APPLICABLE, LOCAL_APP_VERSION, LOCAL_BUILD_ID
from app.time_utils import iso_now, parse_dt, utcnow

NOTE_EDITABLE_STATES = {RunState.REVIEW_READY}


class Repository:
    def __init__(self, database_url: str = "sqlite:///:memory:") -> None:
        self.database_url = database_url
        self.backend = "sqlite" if database_url.startswith("sqlite:///") else "postgres"
        self.conn: Any
        if self.backend == "sqlite":
            path = database_url.removeprefix("sqlite:///")
            if path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
            self.conn.row_factory = sqlite3.Row
        else:
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ModuleNotFoundError as exc:
                raise RuntimeError(
                    "PostgreSQL database URLs require psycopg; install requirements.txt"
                ) from exc
            self.conn = psycopg.connect(database_url, autocommit=True, row_factory=dict_row)
        self.init_schema()

    def close(self) -> None:
        self.conn.close()

    def init_schema(self) -> None:
        schema = SQLITE_SCHEMA if self.backend == "sqlite" else POSTGRES_SCHEMA
        for statement in schema:
            self._execute(statement)
        self._ensure_run_column("build_id", "TEXT")
        if self.backend == "sqlite":
            self._execute(
                """
                INSERT OR IGNORE INTO run_lock(name, active_run_id, updated_at)
                VALUES('weekend_report', NULL, ?)
                """,
                (iso_now(),),
            )
        else:
            self._execute(
                """
                INSERT INTO run_lock(name, active_run_id, updated_at)
                VALUES('weekend_report', NULL, ?)
                ON CONFLICT (name) DO NOTHING
                """,
                (iso_now(),),
            )

    def _ensure_run_column(self, column: str, column_type: str) -> None:
        if self.backend == "sqlite":
            columns = {
                row["name"]
                for row in self._execute("PRAGMA table_info(runs)").fetchall()
            }
        else:
            rows = self._execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name='runs'
                """
            ).fetchall()
            columns = {row["column_name"] for row in rows}
        if column not in columns:
            self._execute(f"ALTER TABLE runs ADD COLUMN {column} {column_type}")

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.backend == "postgres" else sql

    def _execute(self, sql: str, params: tuple[Any, ...] = ()):
        return self.conn.execute(self._sql(sql), params)

    def _insert_id(self, sql: str, params: tuple[Any, ...]) -> int:
        if self.backend == "postgres":
            row = self._execute(sql + " RETURNING id", params).fetchone()
            return int(row["id"])
        cur = self._execute(sql, params)
        return int(cur.lastrowid)

    def _json_value(self, value: Any) -> Any:
        value = to_jsonable(value)
        if self.backend == "postgres":
            from psycopg.types.json import Jsonb

            return Jsonb(value)
        return json.dumps(value, sort_keys=True)

    def transaction(self) -> _Transaction:
        return _Transaction(self.conn, self.backend)

    def _run_lock_sql(self) -> str:
        suffix = " FOR UPDATE" if self.backend == "postgres" else ""
        return "SELECT active_run_id FROM run_lock WHERE name='weekend_report'" + suffix

    def _claim_candidate_sql(self) -> str:
        suffix = " FOR UPDATE SKIP LOCKED" if self.backend == "postgres" else ""
        return "SELECT run_id FROM runs WHERE state=? ORDER BY created_at LIMIT 1" + suffix

    def create_run(
        self,
        *,
        started_by: str,
        run_id: str | None = None,
        application_version: str = LOCAL_APP_VERSION,
        build_id: str = LOCAL_BUILD_ID,
        git_commit: str = GIT_NOT_APPLICABLE,
        config_version: str = "UNKNOWN",
    ) -> RunRecord:
        run_id = run_id or utcnow().strftime("WR-%Y%m%d-%H%M%S")
        with self.transaction():
            lock_row = self._execute(self._run_lock_sql()).fetchone()
            active = lock_row["active_run_id"] if lock_row else None
            if active:
                active_state = self._execute(
                    "SELECT state FROM runs WHERE run_id=?", (active,)
                ).fetchone()
                if active_state and active_state["state"] in ACTIVE_STATES:
                    raise DuplicateActiveRun(f"active run already exists: {active}")
            blocking = self._execute(
                """
                SELECT run_id
                FROM runs
                WHERE state IN (?,?,?)
                ORDER BY created_at
                LIMIT 1
                """,
                (
                    RunState.CREATED.value,
                    RunState.RUNNING.value,
                    RunState.RECOVERY_REQUIRED.value,
                ),
            ).fetchone()
            if blocking:
                raise DuplicateActiveRun(f"active run already exists: {blocking['run_id']}")
            now = iso_now()
            self._execute(
                """
                INSERT INTO runs(
                    run_id,state,automation_status,started_by,created_at,application_version,
                    build_id,git_commit,config_version,updated_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    run_id,
                    RunState.CREATED.value,
                    None,
                    started_by,
                    now,
                    application_version,
                    build_id,
                    git_commit,
                    config_version,
                    now,
                ),
            )
            self._execute(
                "UPDATE run_lock SET active_run_id=?, updated_at=? WHERE name='weekend_report'",
                (run_id, now),
            )
        return self.get_run(run_id)

    def claim_next_run(self, worker_id: str) -> RunRecord | None:
        with self.transaction():
            row = self._execute(self._claim_candidate_sql(), (RunState.CREATED.value,)).fetchone()
            if not row:
                return None
            run_id = row["run_id"]
            now = iso_now()
            updated = self._execute(
                """
                UPDATE runs
                SET state=?, worker_id=?, started_at=?, last_heartbeat=?, updated_at=?
                WHERE run_id=? AND state=?
                """,
                (RunState.RUNNING.value, worker_id, now, now, now, run_id, RunState.CREATED.value),
            ).rowcount
            if updated != 1:
                return None
        return self.get_run(run_id)

    def heartbeat(
        self, run_id: str, current_module: str | None = None, worker_id: str | None = None
    ) -> None:
        now = iso_now()
        self._execute(
            """
            UPDATE runs
            SET last_heartbeat=?,
                current_module=COALESCE(?, current_module),
                worker_id=COALESCE(?, worker_id),
                updated_at=?
            WHERE run_id=?
            """,
            (now, current_module, worker_id, now, run_id),
        )

    def mark_review_ready(self, run_id: str, status: CheckStatus) -> None:
        now = iso_now()
        with self.transaction():
            self._execute(
                """
                UPDATE runs
                SET state=?, automation_status=?, finished_at=?, current_module=NULL, updated_at=?
                WHERE run_id=?
                """,
                (RunState.REVIEW_READY.value, status.value, now, now, run_id),
            )
            self._execute(
                "UPDATE run_lock SET active_run_id=NULL, updated_at=? WHERE active_run_id=?",
                (now, run_id),
            )

    def mark_failed(self, run_id: str, message: str) -> None:
        now = iso_now()
        with self.transaction():
            self._execute(
                """
                UPDATE runs
                SET state=?, automation_status=?, finished_at=?, current_module=?, updated_at=?
                WHERE run_id=?
                """,
                (RunState.FAILED.value, CheckStatus.ERROR.value, now, message, now, run_id),
            )
            self._execute(
                "UPDATE run_lock SET active_run_id=NULL, updated_at=? WHERE active_run_id=?",
                (now, run_id),
            )

    def recover_stale_runs(
        self, *, heartbeat_timeout_seconds: int, worker_id: str
    ) -> list[RunRecord]:
        cutoff = utcnow() - timedelta(seconds=heartbeat_timeout_seconds)
        stale = []
        for run in self.list_runs():
            last = parse_dt(run.last_heartbeat)
            if run.state == RunState.RUNNING and last and last < cutoff:
                stale.append(run)
        recovered: list[RunRecord] = []
        now = iso_now()
        with self.transaction():
            for run in stale:
                if run.current_module == "recording":
                    state = RunState.RECOVERY_REQUIRED
                    message = "stale worker during Recording; manual cleanup/recovery required"
                else:
                    state = RunState.FAILED
                    message = f"stale worker detected by {worker_id}; run failed without replay"
                self._execute(
                    """
                    UPDATE runs
                    SET state=?, automation_status=?, current_module=?, finished_at=?, updated_at=?
                    WHERE run_id=? AND state=?
                    """,
                    (
                        state.value,
                        CheckStatus.ERROR.value,
                        message,
                        now,
                        now,
                        run.run_id,
                        RunState.RUNNING.value,
                    ),
                )
                self._execute(
                    "UPDATE run_lock SET active_run_id=NULL, updated_at=? WHERE active_run_id=?",
                    (now, run.run_id),
                )
        for run in stale:
            recovered.append(self.get_run(run.run_id))
        return recovered

    def resolve_recovery(self, run_id: str, *, reviewer: str, note: str) -> RunRecord:
        run = self.get_run(run_id)
        if run.state != RunState.RECOVERY_REQUIRED:
            raise InvalidRunTransition(f"run must be RECOVERY_REQUIRED, got {run.state}")
        message = (
            "manual recovery resolved by "
            f"{reviewer}; no Recording replay performed; resolution note: {note.strip()}"
        )
        now = iso_now()
        with self.transaction():
            self._execute(
                """
                UPDATE runs
                SET state=?, current_module=?, finished_at=COALESCE(finished_at, ?), updated_at=?
                WHERE run_id=? AND state=?
                """,
                (
                    RunState.FAILED.value,
                    message,
                    now,
                    now,
                    run_id,
                    RunState.RECOVERY_REQUIRED.value,
                ),
            )
            self._execute(
                "UPDATE run_lock SET active_run_id=NULL, updated_at=? WHERE active_run_id=?",
                (now, run_id),
            )
        return self.get_run(run_id)

    def add_result(self, result: CheckResult) -> int:
        return self._insert_id(
            """
            INSERT INTO results(
                run_id,module,site,check_id,target,status,expected_json,actual_json,
                message,started_at,finished_at,metadata_json
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                result.run_id,
                result.module,
                result.site,
                result.check_id,
                result.target,
                result.status.value,
                self._json_value(result.expected),
                self._json_value(result.actual),
                result.message,
                result.started_at,
                result.finished_at,
                self._json_value(result.metadata),
            ),
        )

    def update_result_evidence(self, result_id: int, evidence_paths: list[str]) -> None:
        result = self.get_result(result_id)
        metadata = result.metadata
        metadata["evidence"] = evidence_paths
        self._execute(
            "UPDATE results SET metadata_json=? WHERE id=?",
            (self._json_value(metadata), result_id),
        )

    def add_evidence(self, evidence: EvidenceRecord) -> int:
        return self._insert_id(
            """
            INSERT INTO evidence(
                run_id,result_id,module,site,evidence_type,path,checksum,mime_type,created_at
            )
            VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                evidence.run_id,
                evidence.result_id,
                evidence.module,
                evidence.site,
                evidence.evidence_type,
                evidence.path,
                evidence.checksum,
                evidence.mime_type,
                evidence.created_at or iso_now(),
            ),
        )

    def save_note(self, note: ReviewNote) -> int:
        self.require_note_editable(note.run_id)
        now = iso_now()
        existing = self._execute(
            """
            SELECT id, created_at
            FROM review_notes
            WHERE run_id=?
              AND scope=?
              AND COALESCE(module,'')=COALESCE(?, '')
              AND COALESCE(result_id,-1)=COALESCE(?, -1)
              AND COALESCE(dashboard_id,'')=COALESCE(?, '')
            """,
            (note.run_id, note.scope.value, note.module, note.result_id, note.dashboard_id),
        ).fetchone()
        if existing:
            self._execute(
                "UPDATE review_notes SET author=?, note=?, updated_at=? WHERE id=?",
                (note.author, note.note, now, existing["id"]),
            )
            return int(existing["id"])
        return self._insert_id(
            """
            INSERT INTO review_notes(
                run_id,scope,module,result_id,dashboard_id,author,note,created_at,updated_at
            )
            VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                note.run_id,
                note.scope.value,
                note.module,
                note.result_id,
                note.dashboard_id,
                note.author,
                note.note,
                now,
                now,
            ),
        )

    def get_run(self, run_id: str) -> RunRecord:
        row = self._execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            raise KeyError(run_id)
        return _run_from_row(row)

    def get_result(self, result_id: int) -> CheckResult:
        row = self._execute("SELECT * FROM results WHERE id=?", (result_id,)).fetchone()
        if not row:
            raise KeyError(result_id)
        return _result_from_row(row)

    def list_runs(self) -> list[RunRecord]:
        return [
            _run_from_row(row)
            for row in self._execute("SELECT * FROM runs ORDER BY created_at DESC").fetchall()
        ]

    def list_results(self, run_id: str, module: str | None = None) -> list[CheckResult]:
        if module:
            rows = self._execute(
                "SELECT * FROM results WHERE run_id=? AND module=? ORDER BY id",
                (run_id, module),
            ).fetchall()
        else:
            rows = self._execute(
                "SELECT * FROM results WHERE run_id=? ORDER BY id", (run_id,)
            ).fetchall()
        return [_result_from_row(row) for row in rows]

    def list_evidence(
        self, run_id: str, module: str | None = None, result_id: int | None = None
    ) -> list[EvidenceRecord]:
        clauses = ["run_id=?"]
        params: list[Any] = [run_id]
        if module is not None:
            clauses.append("module=?")
            params.append(module)
        if result_id is not None:
            clauses.append("result_id=?")
            params.append(result_id)
        rows = self._execute(
            f"SELECT * FROM evidence WHERE {' AND '.join(clauses)} ORDER BY id",
            tuple(params),
        ).fetchall()
        return [_evidence_from_row(row) for row in rows]

    def get_evidence_by_path(self, path: str) -> EvidenceRecord:
        row = self._execute("SELECT * FROM evidence WHERE path=?", (path,)).fetchone()
        if not row:
            raise KeyError(path)
        return _evidence_from_row(row)

    def list_notes(self, run_id: str) -> list[ReviewNote]:
        return [
            _note_from_row(row)
            for row in self._execute(
                "SELECT * FROM review_notes WHERE run_id=? ORDER BY id", (run_id,)
            ).fetchall()
        ]

    def set_snapshot_path(self, run_id: str, path: str) -> None:
        self._execute(
            "UPDATE runs SET final_snapshot_path=?, updated_at=? WHERE run_id=?",
            (path, iso_now(), run_id),
        )

    def set_final_pdf(
        self,
        run_id: str,
        *,
        state: RunState,
        reviewer: str,
        decision: str,
        pdf_path: str,
        checksum: str,
    ) -> None:
        now = iso_now()
        self._execute(
            """
            UPDATE runs
            SET state=?, reviewed_by=?, reviewed_at=?, review_decision=?, final_pdf_path=?,
                final_pdf_checksum=?, updated_at=?
            WHERE run_id=?
            """,
            (state.value, reviewer, now, decision, pdf_path, checksum, now, run_id),
        )

    def require_review_ready(self, run_id: str) -> RunRecord:
        run = self.get_run(run_id)
        if run.state != RunState.REVIEW_READY:
            raise InvalidRunTransition(f"run must be REVIEW_READY, got {run.state}")
        return run

    def require_note_editable(self, run_id: str) -> RunRecord:
        run = self.get_run(run_id)
        if run.state not in NOTE_EDITABLE_STATES:
            raise InvalidRunTransition(f"notes may be edited only in REVIEW_READY; got {run.state}")
        return run


class _Transaction:
    def __init__(self, conn, backend: str) -> None:
        self.conn = conn
        self.backend = backend

    def __enter__(self):
        if self.backend == "postgres":
            self.conn.execute("BEGIN")
            return self
        for _ in range(20):
            try:
                self.conn.execute("BEGIN IMMEDIATE")
                return self
            except sqlite3.OperationalError:
                time.sleep(0.01)
        self.conn.execute("BEGIN IMMEDIATE")
        return self

    def __exit__(self, exc_type, exc, tb):
        self.conn.execute("ROLLBACK" if exc else "COMMIT")


def _json_loads(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        return json.loads(value)
    return value


def _run_from_row(row: Mapping[str, Any]) -> RunRecord:
    status = CheckStatus(row["automation_status"]) if row["automation_status"] else None
    return RunRecord(
        row["run_id"],
        RunState(row["state"]),
        status,
        row["started_by"],
        str(row["created_at"]),
        str(row["started_at"]) if row["started_at"] else None,
        str(row["finished_at"]) if row["finished_at"] else None,
        row["worker_id"],
        str(row["last_heartbeat"]) if row["last_heartbeat"] else None,
        row["current_module"],
        row["reviewed_by"],
        str(row["reviewed_at"]) if row["reviewed_at"] else None,
        row["review_decision"],
        row["application_version"],
        row["build_id"],
        row["git_commit"],
        row["config_version"],
        row["final_snapshot_path"],
        row["final_pdf_path"],
        row["final_pdf_checksum"],
    )


def _result_from_row(row: Mapping[str, Any]) -> CheckResult:
    metadata = _json_loads(row["metadata_json"]) or {}
    evidence = metadata.get("evidence", []) if isinstance(metadata, dict) else []
    return CheckResult(
        row["run_id"],
        row["module"],
        row["check_id"],
        CheckStatus(row["status"]),
        row["message"],
        site=row["site"],
        target=row["target"],
        expected=_json_loads(row["expected_json"]),
        actual=_json_loads(row["actual_json"]),
        started_at=str(row["started_at"]) if row["started_at"] else None,
        finished_at=str(row["finished_at"]) if row["finished_at"] else None,
        evidence=evidence,
        metadata=metadata,
        id=row["id"],
    )


def _evidence_from_row(row: Mapping[str, Any]) -> EvidenceRecord:
    return EvidenceRecord(
        row["run_id"],
        row["module"],
        row["site"],
        row["evidence_type"],
        row["path"],
        row["checksum"],
        row["mime_type"],
        row["result_id"],
        row["id"],
        str(row["created_at"]) if row["created_at"] else None,
    )


def _note_from_row(row: Mapping[str, Any]) -> ReviewNote:
    from app.domain import NoteScope

    return ReviewNote(
        row["run_id"],
        NoteScope(row["scope"]),
        row["author"],
        row["note"],
        row["module"],
        row["result_id"],
        row["dashboard_id"],
        row["id"],
        str(row["created_at"]) if row["created_at"] else None,
        str(row["updated_at"]) if row["updated_at"] else None,
    )
