from __future__ import annotations

import traceback
from typing import Any

from app.collectors.database import DatabaseCollector
from app.collectors.doctor import DoctorCollector
from app.collectors.infrastructure import InfrastructureCollector
from app.collectors.portainer import PortainerCollector
from app.collectors.rabbitmq import RabbitMQCollector
from app.collectors.recording import RecordingCollector
from app.domain import CheckResult, CheckStatus, EvidenceRecord, to_jsonable
from app.orchestrator.aggregation import aggregate_status
from app.orchestrator.execution_plan import build_execution_plan
from app.orchestrator.run_context import RunContext
from app.time_utils import iso_now
from app.validators.database import DatabaseValidator
from app.validators.doctor import DoctorValidator
from app.validators.infrastructure import InfrastructureValidator
from app.validators.portainer import PortainerValidator
from app.validators.rabbitmq import RabbitMQValidator
from app.validators.recording import RecordingValidator
from app.validators.site_parity import SiteParityValidator

COLLECTORS: dict[str, Any] = {
    "portainer": PortainerCollector,
    "doctor": DoctorCollector,
    "rabbitmq": RabbitMQCollector,
    "recording": RecordingCollector,
    "infrastructure": InfrastructureCollector,
    "database": DatabaseCollector,
}

VALIDATORS: dict[str, Any] = {
    "portainer": PortainerValidator,
    "doctor": DoctorValidator,
    "rabbitmq": RabbitMQValidator,
    "recording": RecordingValidator,
    "infrastructure": InfrastructureValidator,
    "database": DatabaseValidator,
}


class OrchestratorRunner:
    def run(self, context: RunContext) -> list[CheckResult]:
        all_results: list[CheckResult] = []
        for step in build_execution_plan(context.config):
            context.repository.heartbeat(context.run_id, current_module=step.module)
            started = iso_now()
            raw_evidence: EvidenceRecord | None = None
            try:
                collector = COLLECTORS[step.module]()
                validator = VALIDATORS[step.module]()
                actual = collector.collect(context)
                raw_evidence = context.evidence.write_json(
                    context.run_id,
                    step.module,
                    None,
                    "raw-collector.json",
                    actual,
                    evidence_type="raw_collector",
                )
                context.repository.add_evidence(raw_evidence)
                results = validator.validate(actual, context.config, context)
            except Exception as exc:
                unavailable_status = _if_unavailable_status(context.config, step.module)
                error_payload = {
                    "module": step.module,
                    "exception": type(exc).__name__,
                    "message": str(exc),
                    "traceback": traceback.format_exc(limit=8),
                    "configured_if_unavailable_status": unavailable_status.value,
                    "required": step.required,
                }
                raw_evidence = context.evidence.write_json(
                    context.run_id,
                    step.module,
                    None,
                    "collector-or-validator-error.json",
                    error_payload,
                    evidence_type="error",
                )
                context.repository.add_evidence(raw_evidence)
                results = [
                    CheckResult(
                        run_id=context.run_id,
                        module=step.module,
                        check_id=f"{step.module}.module_error",
                        site=None,
                        target=step.module,
                        expected={"module": step.module},
                        actual={"exception": type(exc).__name__},
                        status=unavailable_status,
                        message=f"{step.module} failed unexpectedly: {exc}",
                        started_at=started,
                        finished_at=iso_now(),
                        metadata={
                            "traceback": traceback.format_exc(limit=8),
                            "required": step.required,
                            "configured_if_unavailable_status": unavailable_status.value,
                        },
                    )
                ]
            for result in results:
                self._store_result_with_evidence(context, result, raw_evidence)
            all_results.extend(results)
        parity_basis = context.evidence.write_json(
            context.run_id,
            "site_parity",
            None,
            "raw-parity-input.json",
            {"results": [to_jsonable(result) for result in all_results]},
            evidence_type="raw_collector",
        )
        context.repository.add_evidence(parity_basis)
        parity = SiteParityValidator().validate({"results": all_results}, context.config, context)
        for result in parity:
            self._store_result_with_evidence(context, result, parity_basis)
        all_results.extend(parity)
        status = aggregate_status(all_results, context.config)
        context.repository.mark_review_ready(context.run_id, status)
        return all_results

    def _store_result_with_evidence(
        self,
        context: RunContext,
        result: CheckResult,
        raw_evidence: EvidenceRecord | None,
    ) -> None:
        stored = context.repository.add_result(result)
        result.id = stored
        normalized = context.evidence.write_json(
            context.run_id,
            result.module,
            result.site,
            f"result-{stored}.json",
            {"result": to_jsonable(result)},
            evidence_type="normalized_result",
        )
        normalized.result_id = stored
        context.repository.add_evidence(normalized)
        evidence_paths = list(result.evidence)
        if raw_evidence is not None:
            evidence_paths.append(raw_evidence.path)
        evidence_paths.append(normalized.path)
        result.evidence = evidence_paths
        context.repository.update_result_evidence(stored, evidence_paths)


def _if_unavailable_status(config: dict[str, Any], module: str) -> CheckStatus:
    value = (
        config.get("rules", {})
        .get("modules", {})
        .get(module, {})
        .get("if_unavailable_status", CheckStatus.ERROR.value)
    )
    try:
        status = CheckStatus(value)
    except ValueError:
        return CheckStatus.ERROR
    if status == CheckStatus.PASS:
        return CheckStatus.WARNING
    return status
