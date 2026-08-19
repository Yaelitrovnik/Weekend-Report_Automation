from __future__ import annotations

import copy
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.loader import load_config_dir
from app.database.repository import Repository
from app.domain import NoteScope, ReviewDecision, ReviewNote, RunState
from app.evidence.manager import EvidenceManager
from app.orchestrator.run_context import RunContext
from app.orchestrator.runner import OrchestratorRunner
from app.reporting.snapshot import finalize_run


def main() -> int:
    config = load_config_dir("tests/fixtures/config_valid")
    approval_config = copy.deepcopy(config)
    approval_config["rules"]["review"]["approval_status_policy"]["FAIL"] = "REQUIRE_NOTE"

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repo = Repository("sqlite:///:memory:")
        evidence = EvidenceManager(root / "evidence")
        try:
            run = repo.create_run(
                started_by="ci-e2e",
                run_id="WR-CI-E2E-000001",
                application_version="ci-e2e",
                build_id="ci-e2e",
                git_commit="<NOT_APPLICABLE>",
                config_version=config["_config_hash"],
            )
            claimed = repo.claim_next_run("ci-e2e-worker")
            assert claimed and claimed.run_id == run.run_id
            OrchestratorRunner().run(RunContext(run.run_id, config, repo, evidence))
            assert repo.get_run(run.run_id).state is RunState.REVIEW_READY

            before_statuses = {item.id: item.status.value for item in repo.list_results(run.run_id)}

            note_texts: list[str] = []
            module_note = "CI E2E module review note"
            repo.save_note(
                ReviewNote(
                    run.run_id,
                    NoteScope.MODULE,
                    "ci-reviewer",
                    module_note,
                    module="portainer",
                )
            )
            note_texts.append(module_note)

            for result in repo.list_results(run.run_id):
                if result.status.value != "PASS":
                    text = f"CI E2E acknowledged {result.check_id}"
                    repo.save_note(
                        ReviewNote(
                            run.run_id,
                            NoteScope.RESULT,
                            "ci-reviewer",
                            text,
                            result_id=result.id,
                        )
                    )
                    note_texts.append(text)

            for dashboard in config["splunk_dashboards"]["dashboards"]:
                text = f"CI E2E reviewed dashboard {dashboard['id']}"
                repo.save_note(
                    ReviewNote(
                        run.run_id,
                        NoteScope.SPLUNK_DASHBOARD,
                        "ci-reviewer",
                        text,
                        dashboard_id=dashboard["id"],
                    )
                )
                note_texts.append(text)

            general_note = "CI E2E general review note"
            repo.save_note(
                ReviewNote(run.run_id, NoteScope.GENERAL, "ci-reviewer", general_note)
            )
            note_texts.append(general_note)

            snapshot = finalize_run(
                repo,
                evidence,
                approval_config,
                run.run_id,
                "ci-reviewer",
                ReviewDecision.APPROVE,
            )
            finalized = repo.get_run(run.run_id)
            assert finalized.state is RunState.APPROVED
            assert finalized.final_snapshot_path
            assert finalized.final_pdf_path

            after_statuses = {item.id: item.status.value for item in repo.list_results(run.run_id)}
            assert before_statuses == after_statuses, "automated statuses changed during review"

            persisted_notes = repo.list_notes(run.run_id)
            snapshot_ids = {item["id"] for item in snapshot["notes"]}
            persisted_ids = {item.id for item in persisted_notes}
            assert persisted_ids == snapshot_ids, "snapshot did not contain every persisted note"

            snapshot_texts = {item["note"] for item in snapshot["notes"]}
            for text in note_texts:
                assert text in snapshot_texts, f"missing note from snapshot: {text}"

            pdf_path = evidence.root / finalized.final_pdf_path
            pdf_bytes = pdf_path.read_bytes()
            for text in note_texts:
                assert text.encode("latin-1") in pdf_bytes, f"missing note from final PDF: {text}"

            assert repo.list_evidence(run.run_id), "safe E2E produced no evidence"
            print(
                "safe CI end-to-end passed: create -> claim -> execute -> evidence -> "
                "review -> notes -> approve -> snapshot -> final PDF"
            )
            return 0
        finally:
            repo.close()


if __name__ == "__main__":
    raise SystemExit(main())
