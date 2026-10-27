from __future__ import annotations

import copy
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database.repository import Repository
from app.domain import (
    ManualDBReview,
    ManualDBReviewResult,
    NoteScope,
    ReviewDecision,
    ReviewNote,
    RunState,
)
from app.evidence.manager import EvidenceManager
from app.orchestrator.run_context import RunContext
from app.orchestrator.runner import OrchestratorRunner
from app.reporting.snapshot import finalize_run
from app.review.finalization import FinalizationReadinessError
from app.review.manual_db import record_manual_db_review
from tests.fixtures.config_factory import load_fixture_config


def prepare_review_ready_run(
    repo: Repository,
    evidence: EvidenceManager,
    config: dict,
    run_id: str,
) -> None:
    run = repo.create_run(
        started_by="ci-e2e",
        run_id=run_id,
        application_version="ci-e2e",
        build_id="ci-e2e",
        git_commit="<NOT_APPLICABLE>",
        config_version=config["_config_hash"],
    )

    claimed = repo.claim_next_run("ci-e2e-worker")
    assert claimed
    assert claimed.run_id == run.run_id

    OrchestratorRunner().run(
        RunContext(
            run.run_id,
            config,
            repo,
            evidence,
        )
    )

    assert repo.get_run(run.run_id).state is RunState.REVIEW_READY


def save_required_approval_notes(
    repo: Repository,
    config: dict,
    run_id: str,
) -> list[str]:
    note_texts: list[str] = []

    for module in config["rules"]["review"]["required_module_notes"]:
        text = f"CI E2E reviewed module {module}"
        repo.save_note(
            ReviewNote(
                run_id,
                NoteScope.MODULE,
                "ci-reviewer",
                text,
                module=module,
            )
        )
        note_texts.append(text)

    for result in repo.list_results(run_id):
        if result.status.value != "PASS":
            text = f"CI E2E acknowledged {result.check_id}"
            repo.save_note(
                ReviewNote(
                    run_id,
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
                run_id,
                NoteScope.SPLUNK_DASHBOARD,
                "ci-reviewer",
                text,
                dashboard_id=dashboard["id"],
                reviewed=True,
            )
        )
        note_texts.append(text)

    general_note = "CI E2E general review note"
    repo.save_note(
        ReviewNote(
            run_id,
            NoteScope.GENERAL,
            "ci-reviewer",
            general_note,
        )
    )
    note_texts.append(general_note)

    return note_texts


def save_manual_db_result(
    repo: Repository,
    run_id: str,
    result: ManualDBReviewResult,
) -> None:
    record_manual_db_review(
        repo,
        ManualDBReview(
            run_id=run_id,
            display_name="Database Synchronization Check",
            script_path=(
                "C:\\Scripts\\DatabaseSync\\"
                "database_sync_check.ps1"
            ),
            result=result,
            comment=(
                "CI E2E manual database synchronization "
                f"result: {result.value}."
            ),
            reviewer="ci-reviewer",
        ),
    )


def assert_finalization_blocked(
    repo: Repository,
    evidence: EvidenceManager,
    config: dict,
    run_id: str,
    decision: ReviewDecision,
    expected_fragment: str,
) -> None:
    try:
        finalize_run(
            repo,
            evidence,
            config,
            run_id,
            "ci-reviewer",
            decision,
        )
    except FinalizationReadinessError as exc:
        assert expected_fragment in str(exc), (
            f"expected finalization error containing "
            f"{expected_fragment!r}, got {str(exc)!r}"
        )
    else:
        raise AssertionError(
            f"{decision.value} unexpectedly succeeded for {run_id}"
        )

    run = repo.get_run(run_id)
    assert run.state is RunState.REVIEW_READY
    assert not run.final_snapshot_path
    assert not run.final_pdf_path


def assert_manual_db_final_artifacts(
    repo: Repository,
    evidence: EvidenceManager,
    run_id: str,
    snapshot: dict,
    expected_result: str,
    expected_decision: str,
) -> None:
    manual_db = snapshot["manual_db_review"]

    assert manual_db["display_name"] == "Database Synchronization Check"
    assert manual_db["script_path"].endswith("database_sync_check.ps1")
    assert manual_db["execution_method"] == "Performed locally by reviewer"
    assert manual_db["result"] == expected_result
    assert "reviewer" not in manual_db
    assert snapshot["review"]["reviewer"] == "ci-reviewer"
    assert manual_db["reviewed_at"]
    assert manual_db["comment"]
    assert manual_db["final_decision"] == expected_decision

    finalized = repo.get_run(run_id)
    assert finalized.final_snapshot_path
    assert finalized.final_pdf_path

    snapshot_path = evidence.root / finalized.final_snapshot_path
    frozen_snapshot = snapshot_path.read_text(encoding="utf-8")

    assert '"manual_db_review"' in frozen_snapshot
    assert expected_result in frozen_snapshot
    assert expected_decision in frozen_snapshot
    assert "database_sync_check.ps1" in frozen_snapshot

    pdf_path = evidence.root / finalized.final_pdf_path
    pdf_bytes = pdf_path.read_bytes()

    assert b"Manual Database Synchronization Check" in pdf_bytes
    assert f"Result: {expected_result}".encode("latin-1") in pdf_bytes
    assert b"Execution method: Performed locally by reviewer" in pdf_bytes
    assert b"Reviewer: ci-reviewer" in pdf_bytes
    assert pdf_bytes.index(b"Evidence References") < pdf_bytes.index(
        b"Reviewer Confirmation"
    )


def run_no_review_scenario(
    root: Path,
    config: dict,
) -> None:
    repo = Repository("sqlite:///:memory:")
    evidence = EvidenceManager(root / "no-review")

    try:
        run_id = "WR-CI-E2E-NO-DB"

        prepare_review_ready_run(
            repo,
            evidence,
            config,
            run_id,
        )
        save_required_approval_notes(
            repo,
            config,
            run_id,
        )

        assert repo.get_manual_db_review(run_id) is None

        assert_finalization_blocked(
            repo,
            evidence,
            config,
            run_id,
            ReviewDecision.APPROVE,
            (
                "Manual Database Synchronization Check "
                "must be completed before finalization."
            ),
        )

        assert_finalization_blocked(
            repo,
            evidence,
            config,
            run_id,
            ReviewDecision.REJECT,
            (
                "Manual Database Synchronization Check "
                "must be completed before finalization."
            ),
        )
    finally:
        repo.close()


def run_pass_scenario(
    root: Path,
    config: dict,
) -> None:
    repo = Repository("sqlite:///:memory:")
    evidence = EvidenceManager(root / "pass")

    try:
        run_id = "WR-CI-E2E-PASS"

        prepare_review_ready_run(
            repo,
            evidence,
            config,
            run_id,
        )

        before_statuses = {
            item.id: item.status.value
            for item in repo.list_results(run_id)
        }

        note_texts = save_required_approval_notes(
            repo,
            config,
            run_id,
        )

        save_manual_db_result(
            repo,
            run_id,
            ManualDBReviewResult.PASS,
        )

        assert_finalization_blocked(
            repo,
            evidence,
            config,
            run_id,
            ReviewDecision.REJECT,
            (
                "REJECT is not permitted when Manual Database "
                "Synchronization Check result is PASS."
            ),
        )

        snapshot = finalize_run(
            repo,
            evidence,
            config,
            run_id,
            "ci-reviewer",
            ReviewDecision.APPROVE,
        )

        finalized = repo.get_run(run_id)
        assert finalized.state is RunState.APPROVED
        assert finalized.final_pdf_path is not None

        after_statuses = {
            item.id: item.status.value
            for item in repo.list_results(run_id)
        }

        assert before_statuses == after_statuses, (
            "automated statuses changed during review"
        )

        persisted_notes = repo.list_notes(run_id)
        snapshot_ids = {
            item["id"]
            for item in snapshot["notes"]
        }
        persisted_ids = {
            item.id
            for item in persisted_notes
        }

        assert persisted_ids == snapshot_ids, (
            "snapshot did not contain every persisted note"
        )

        snapshot_texts = {
            item["note"]
            for item in snapshot["notes"]
        }

        for text in note_texts:
            assert text in snapshot_texts, (
                f"missing note from snapshot: {text}"
            )

        pdf_path = evidence.root / finalized.final_pdf_path
        pdf_bytes = pdf_path.read_bytes()

        for text in note_texts:
            assert text.encode("latin-1") in pdf_bytes, (
                f"missing note from final PDF: {text}"
            )

        assert_manual_db_final_artifacts(
            repo,
            evidence,
            run_id,
            snapshot,
            "PASS",
            "APPROVE",
        )

        assert repo.list_evidence(run_id), (
            "safe E2E produced no evidence"
        )
    finally:
        repo.close()


def run_fail_scenario(
    root: Path,
    config: dict,
) -> None:
    repo = Repository("sqlite:///:memory:")
    evidence = EvidenceManager(root / "fail")

    try:
        run_id = "WR-CI-E2E-FAIL"

        prepare_review_ready_run(
            repo,
            evidence,
            config,
            run_id,
        )

        save_required_approval_notes(
            repo,
            config,
            run_id,
        )

        save_manual_db_result(
            repo,
            run_id,
            ManualDBReviewResult.FAIL,
        )

        assert_finalization_blocked(
            repo,
            evidence,
            config,
            run_id,
            ReviewDecision.APPROVE,
            (
                "APPROVE requires Manual Database Synchronization "
                "Check result PASS; current result is FAIL."
            ),
        )

        snapshot = finalize_run(
            repo,
            evidence,
            config,
            run_id,
            "ci-reviewer",
            ReviewDecision.REJECT,
        )

        assert repo.get_run(run_id).state is RunState.REJECTED

        assert_manual_db_final_artifacts(
            repo,
            evidence,
            run_id,
            snapshot,
            "FAIL",
            "REJECT",
        )
    finally:
        repo.close()


def run_not_run_scenario(
    root: Path,
    config: dict,
) -> None:
    repo = Repository("sqlite:///:memory:")
    evidence = EvidenceManager(root / "not-run")

    try:
        run_id = "WR-CI-E2E-NOT-RUN"

        prepare_review_ready_run(
            repo,
            evidence,
            config,
            run_id,
        )

        save_required_approval_notes(
            repo,
            config,
            run_id,
        )

        save_manual_db_result(
            repo,
            run_id,
            ManualDBReviewResult.NOT_RUN,
        )

        assert_finalization_blocked(
            repo,
            evidence,
            config,
            run_id,
            ReviewDecision.APPROVE,
            (
                "APPROVE requires Manual Database Synchronization "
                "Check result PASS; current result is NOT RUN."
            ),
        )

        snapshot = finalize_run(
            repo,
            evidence,
            config,
            run_id,
            "ci-reviewer",
            ReviewDecision.REJECT,
        )

        assert repo.get_run(run_id).state is RunState.REJECTED

        assert_manual_db_final_artifacts(
            repo,
            evidence,
            run_id,
            snapshot,
            "NOT RUN",
            "REJECT",
        )
    finally:
        repo.close()


def main() -> int:
    config = load_fixture_config()

    approval_config = copy.deepcopy(config)
    approval_config[
        "rules"
    ][
        "review"
    ][
        "approval_status_policy"
    ][
        "FAIL"
    ] = "REQUIRE_NOTE"

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        run_no_review_scenario(
            root,
            approval_config,
        )

        run_pass_scenario(
            root,
            approval_config,
        )

        run_fail_scenario(
            root,
            approval_config,
        )

        run_not_run_scenario(
            root,
            approval_config,
        )

    print(
        "safe CI end-to-end passed: automated checks -> REVIEW_READY -> "
        "manual DB decision matrix -> immutable snapshot -> final PDF"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())