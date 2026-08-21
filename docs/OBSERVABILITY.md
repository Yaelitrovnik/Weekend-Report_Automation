# Observability

**Documentation synchronized:** 2026-08-21

## 1. Current state

`app/logging_utils.py` provides structured (single-line JSON) logging via
the Python standard library's `logging` module — no new heavyweight
dependency. It is wired into:

- `app/orchestrator/runner.py`: per-module start, per-collector duration,
  per-validator duration, per-module finish/error (with duration and
  outcome), heartbeat writes (DEBUG level), and overall run finish.
- `app/worker/main.py`: worker start, run claim, preflight failure, and
  overall run finish/failure with duration.

Log lines look like:

```json
{"duration_ms": 42, "event": "collector_finish", "level": "INFO", "logger": "app.orchestrator.runner", "message": "collector finished", "module_name": "portainer", "outcome": "success", "run_id": "WR-20260811-000000", "timestamp": "2026-08-21T10:15:03"}
```

The field is named `module_name` rather than `module` because `module` is a
reserved attribute on Python's built-in `logging.LogRecord` (it holds the
source Python module name derived from the caller's filename); passing
`module` as an `extra` key raises `KeyError` at the logging call site, not
at format time.

Log level is controlled by `WEEKEND_REPORT_LOG_LEVEL` (default `INFO`). Set
it to `DEBUG` to also see heartbeat-write log lines, which are deliberately
quieter than module-level events since they fire once per module per run
and add little diagnostic value at INFO.

## 2. What this does not cover yet

This is deliberately the first, cheapest step — structured/greppable logs,
nothing more. It does not include:

- **Metrics.** No counters/histograms (e.g. Prometheus) for run duration,
  module failure rate, or per-module collector latency over time. A
  metrics pass would instrument the same call sites already carrying
  `duration_ms` in the log lines above — that overlap is intentional.
- **Tracing.** No distributed tracing (e.g. OpenTelemetry) correlating a
  run's collector → validator → evidence-write → aggregation chain across
  process boundaries. `run_id` on every log line gives basic manual
  correlation via log search today; tracing would replace that with a
  proper trace ID instead of grepping.
- **Log shipping/aggregation.** Logs go to stdout only, sufficient for
  `docker compose logs`. Centralized aggregation is out of scope here.
- **web/api process instrumentation.** Only the worker and orchestrator are
  instrumented. FastAPI request logging, auth failures, and CSRF
  rejections in `app/api/` are not yet covered.

## 3. Why this order

Structured logging first because it is the lowest-risk, lowest-dependency
change that immediately makes "why did this run take 40 minutes" or "which
module keeps timing out" answerable by grepping existing process output,
with no new infrastructure to run or maintain. Metrics and tracing both
need somewhere to send data and are worth doing deliberately once there's
an actual deployment target for them.