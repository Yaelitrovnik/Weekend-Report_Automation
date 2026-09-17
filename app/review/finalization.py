from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.domain import (
    CheckResult,
    CheckStatus,
    ManualDBReviewResult,
    NoteScope,
    ReviewDecision,
    ReviewNote,
)
from app.orchestrator.execution_plan import build_execution_plan


class FinalizationReadinessError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


def validate_finalization_readiness(
    repository,
    config: dict[str, Any],
    run_id: str,
    decision: ReviewDecision,
) -> list[str]:
    errors: list[str] = []

    errors.extend(
        _manual_db_review_errors(
            repository,
            config,
            run_id,
            decision,
        )
    )

    if decision == ReviewDecision.REJECT:
        errors.extend(_reject_errors(config))
        return errors

    results = repository.list_results(run_id)
    notes = repository.list_notes(run_id)

    errors.extend(
        _required_module_completion_errors(
            config,
            results,
        )
    )
    errors.extend(
        _splunk_review_errors(
            config,
            notes,
        )
    )
    errors.extend(
        _required_note_errors(
            config,
            results,
            notes,
        )
    )
    errors.extend(
        _approval_status_policy_errors(
            config,
            results,
            notes,
        )
    )

    return errors


def enforce_finalization_readiness(
    repository,
    config: dict[str, Any],
    run_id: str,
    decision: ReviewDecision,
) -> None:
    errors = validate_finalization_readiness(repository, config, run_id, decision)
    if errors:
        raise FinalizationReadinessError(errors)

def _manual_db_review_errors(
    repository,
    config: dict[str, Any],
    run_id: str,
    decision: ReviewDecision,
) -> list[str]:
    manual_config = config.get(
        "manual_db_check",
        {},
    )

    if not isinstance(manual_config, dict):
        return []

    if manual_config.get("enabled") is not True:
        return []

    if manual_config.get("required") is not True:
        return []

    review = repository.get_manual_db_review(
        run_id
    )

    if review is None:
        return [
            "Manual Database Synchronization Check "
            "must be completed before finalization."
        ]

    if not review.comment.strip():
        return [
            "Manual Database Synchronization Check "
            "requires a non-empty comment before finalization."
        ]

    if decision == ReviewDecision.APPROVE:
        if review.result != ManualDBReviewResult.PASS:
            return [
                "APPROVE requires Manual Database "
                "Synchronization Check result PASS; "
                f"current result is {review.result.value}."
            ]

        return []

    if decision == ReviewDecision.REJECT:
        if review.result == ManualDBReviewResult.PASS:
            return [
                "REJECT is not permitted when Manual Database "
                "Synchronization Check result is PASS."
            ]

        if review.result in {
            ManualDBReviewResult.FAIL,
            ManualDBReviewResult.NOT_RUN,
        }:
            return []

        return [
            "REJECT requires Manual Database Synchronization "
            "Check result FAIL or NOT RUN."
        ]

    return [
        "Unsupported final review decision."
    ]

def _review_config(config: dict[str, Any]) -> dict[str, Any]:
    review = config.get("rules", {}).get("review", {})
    return review if isinstance(review, dict) else {}


def _reject_errors(config: dict[str, Any]) -> list[str]:
    review = _review_config(config)
    if review.get("reject_allowed", True):
        return []
    return ["REJECT is disabled by rules.review.reject_allowed."]


def _required_module_completion_errors(
    config: dict[str, Any],
    results: list[CheckResult],
) -> list[str]:
    completed_modules = {result.module for result in results}
    errors: list[str] = []
    for step in build_execution_plan(config):
        if step.required and step.module not in completed_modules:
            errors.append(
                f"Required module {step.module} has no automated results; "
                "run the module before APPROVE."
            )
    return errors


def _splunk_review_errors(config: dict[str, Any], notes: list[ReviewNote]) -> list[str]:
    notes_by_dashboard = {
        note.dashboard_id: note
        for note in notes
        if note.scope == NoteScope.SPLUNK_DASHBOARD and note.dashboard_id
    }
    errors: list[str] = []
    for dashboard in config.get("splunk_dashboards", {}).get("dashboards", []):
        if not isinstance(dashboard, dict):
            continue
        dashboard_id = dashboard.get("id")
        if not isinstance(dashboard_id, str):
            continue
        note = notes_by_dashboard.get(dashboard_id)
        if dashboard.get("required_review") and not _dashboard_reviewed(note):
            errors.append(
                f"Splunk dashboard {dashboard_id} must be reviewed and saved before APPROVE."
            )
        if dashboard.get("note_required") and not _has_text(note):
            errors.append(f"Splunk dashboard {dashboard_id} requires a non-empty note.")
    return errors


def _required_note_errors(
    config: dict[str, Any],
    results: list[CheckResult],
    notes: list[ReviewNote],
) -> list[str]:
    review = _review_config(config)
    notes_by_module, notes_by_result, general_note = _note_indexes(notes)
    errors: list[str] = []
    for module in review.get("required_module_notes", []) or []:
        if not _has_text(notes_by_module.get(module)):
            errors.append(f"Module {module} requires a non-empty reviewer note before APPROVE.")
    if review.get("general_notes_enabled") and review.get("general_note_required"):
        if not _has_text(general_note):
            errors.append("A non-empty general reviewer note is required before APPROVE.")
    required_statuses: set[CheckStatus] = set()
    for status in review.get("result_note_required_statuses", []) or []:
        try:
            required_statuses.add(CheckStatus(status))
        except ValueError:
            continue
    for result in results:
        note = notes_by_result.get(result.id)
        if result.status in required_statuses and not _has_text(note):
            errors.append(
                f"Result {result.id} ({result.check_id}) requires a reviewer note "
                f"because its status is {result.status.value}."
            )
        if (
            result.status == CheckStatus.MANUAL_REVIEW
            and review.get("manual_review_acknowledgment_required", False)
            and not _has_text(note)
        ):
            errors.append(
                f"MANUAL_REVIEW result {result.id} ({result.check_id}) must be acknowledged."
            )
        if (
            result.module == "recording"
            and result.metadata.get("cleanup_required") is True
            and review.get("recording_cleanup_acknowledgment_required", False)
            and not _has_text(note)
        ):
            errors.append(
                f"Recording cleanup for result {result.id} ({result.check_id}) "
                "must be verified in a reviewer note before APPROVE."
            )
    return errors


def _approval_status_policy_errors(
    config: dict[str, Any],
    results: list[CheckResult],
    notes: list[ReviewNote],
) -> list[str]:
    review = _review_config(config)
    policy = review.get("approval_status_policy", {}) or {}
    notes_by_result = _note_indexes(notes)[1]
    errors: list[str] = []
    for status, grouped in _results_by_status(results).items():
        action = policy.get(status.value, "ALLOW")
        if action == "BLOCK":
            blocked = [
                result
                for result in grouped
                if not _reviewable_doctor_health_error_is_covered(
                    result,
                    results,
                    notes_by_result,
                )
            ]
            if blocked:
                errors.append(
                    f"APPROVE is blocked by configured policy for {status.value} "
                    f"({len(blocked)} result(s))."
                )
        elif action == "REQUIRE_NOTE":
            for result in grouped:
                if not _has_text(notes_by_result.get(result.id)):
                    errors.append(
                        f"Result {result.id} ({result.check_id}) needs a note before "
                        f"APPROVE because {status.value} is configured as REQUIRE_NOTE."
                    )
    return errors


def _note_indexes(
    notes: list[ReviewNote],
) -> tuple[dict[str, ReviewNote], dict[int | None, ReviewNote], ReviewNote | None]:
    by_module: dict[str, ReviewNote] = {}
    by_result: dict[int | None, ReviewNote] = {}
    general: ReviewNote | None = None
    for note in notes:
        if note.scope == NoteScope.MODULE and note.module:
            by_module[note.module] = note
        elif note.scope == NoteScope.RESULT:
            by_result[note.result_id] = note
        elif note.scope == NoteScope.GENERAL:
            general = note
    return by_module, by_result, general


def _results_by_status(results: list[CheckResult]) -> dict[CheckStatus, list[CheckResult]]:
    grouped: dict[CheckStatus, list[CheckResult]] = defaultdict(list)
    for result in results:
        if result.status != CheckStatus.PASS:
            grouped[result.status].append(result)
    return dict(grouped)


def _reviewable_doctor_health_error_is_covered(
    result: CheckResult,
    results: list[CheckResult],
    notes_by_result: dict[int | None, ReviewNote],
) -> bool:
    module_result = next(
        (
            item
            for item in results
            if item.module == "doctor"
            and item.check_id == "doctor.module_status"
            and item.status == CheckStatus.MANUAL_REVIEW
            and item.metadata.get("doctor_manual_review_path") is True
        ),
        None,
    )

    return (
        result.module == "doctor"
        and result.check_id == "doctor.service.health"
        and result.status == CheckStatus.ERROR
        and result.metadata.get("doctor_error_type") == "health_issue"
        and result.metadata.get("reviewable_health_issue") is True
        and module_result is not None
        and _has_text(notes_by_result.get(module_result.id))
    )


def _has_text(note: ReviewNote | None) -> bool:
    return bool(note and note.note.strip())


def _dashboard_reviewed(note: ReviewNote | None) -> bool:
    return bool(note and note.reviewed)
