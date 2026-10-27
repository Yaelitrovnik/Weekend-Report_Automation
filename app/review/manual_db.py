from __future__ import annotations

from pathlib import PureWindowsPath
from urllib.parse import quote

from app.domain import ManualDBReview, ManualDBReviewResult


class ManualDBReviewValidationError(ValueError):
    pass

def build_manual_db_powershell_command(
    script_path: str,
) -> str:
    path = script_path.strip()

    if any(char in path for char in ('"', "\r", "\n")):
        raise ManualDBReviewValidationError(
            "script_path contains unsupported characters"
        )

    windows_path = PureWindowsPath(path)

    if not windows_path.is_absolute():
        raise ManualDBReviewValidationError(
            "script_path must be an absolute Windows path"
        )

    if windows_path.suffix.lower() != ".ps1":
        raise ManualDBReviewValidationError(
            "script_path must reference a .ps1 file"
        )

    return (
        'powershell.exe -ExecutionPolicy Bypass '
        f'-File "{path}"'
    )


def build_manual_db_file_uri(
    script_path: str,
) -> str:
    path = script_path.strip()

    if any(
        char in path
        for char in ('"', "\r", "\n")
    ):
        raise ManualDBReviewValidationError(
            "script_path contains unsupported characters"
        )

    windows_path = PureWindowsPath(path)

    if not windows_path.is_absolute():
        raise ManualDBReviewValidationError(
            "script_path must be an absolute Windows path"
        )

    if windows_path.suffix.lower() != ".ps1":
        raise ManualDBReviewValidationError(
            "script_path must reference a .ps1 file"
        )

    posix_path = windows_path.as_posix()

    return (
        "file:///"
        + quote(
            posix_path,
            safe="/:",
        )
    )

def validate_manual_db_review(review: ManualDBReview) -> None:
    errors: list[str] = []

    if not isinstance(review.run_id, str) or not review.run_id.strip():
        errors.append("run_id is required")

    if not isinstance(review.display_name, str) or not review.display_name.strip():
        errors.append("display_name is required")

    if not isinstance(review.script_path, str) or not review.script_path.strip():
        errors.append("script_path is required")
    else:
        script_path = PureWindowsPath(review.script_path.strip())

        if any(char in review.script_path for char in ('"', "\r", "\n")):
            errors.append("script_path contains unsupported characters")

        if not script_path.is_absolute():
            errors.append("script_path must be an absolute Windows path")

        if script_path.suffix.lower() != ".ps1":
            errors.append("script_path must reference a .ps1 file")

    if not isinstance(review.result, ManualDBReviewResult):
        errors.append("result must be one of: PASS, FAIL, NOT RUN")

    if not isinstance(review.comment, str) or not review.comment.strip():
        errors.append("comment is required")

    if not isinstance(review.reviewer, str) or not review.reviewer.strip():
        errors.append("reviewer is required")

    if errors:
        raise ManualDBReviewValidationError("; ".join(errors))

def record_manual_db_review(
    repository,
    review: ManualDBReview,
) -> int:
    validate_manual_db_review(review)
    return repository.save_manual_db_review(review)