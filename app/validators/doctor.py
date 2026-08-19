from __future__ import annotations

from typing import Any

from app.domain import CheckResult, CheckStatus
from app.orchestrator.run_context import RunContext
from app.time_utils import iso_now
from app.validators.base import Validator


class DoctorValidator(Validator):
    def validate(
        self, actual: dict[str, Any], config: dict[str, Any], context: RunContext
    ) -> list[CheckResult]:
        started = iso_now()
        if actual.get("mode") == "manual":
            return [
                CheckResult(
                    run_id=context.run_id,
                    module="doctor",
                    check_id="doctor.manual_review",
                    status=CheckStatus.MANUAL_REVIEW,
                    message=(
                        "DOCTOR is configured for manual review; reviewer notes are "
                        "required by policy if configured."
                    ),
                    expected=config.get("doctor", {}).get("doctor", {}).get("manual_review", {}),
                    actual=actual.get("manual_review", {}),
                    started_at=started,
                    finished_at=iso_now(),
                )
            ]
        return [
            CheckResult(
                run_id=context.run_id,
                module="doctor",
                check_id="doctor.api_contract",
                status=CheckStatus.ERROR,
                message="DOCTOR API contract is not implemented until supplied and approved.",
                expected=config.get("doctor", {}),
                actual=actual,
                started_at=started,
                finished_at=iso_now(),
            )
        ]
