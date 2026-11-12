# v1.5.3 validation record

Date: 2026-11-09. Repository:
`C:\Users\Administrator\Desktop\Projects\Yael\Weekend-Report_automation`.
Baseline HEAD: `062e3ee05cc88bf2b2b462d8836ac1991d464aa6`.
Existing implementation was retained; no reset, checkout, clean, commit, push, image
publication, deployment, or production-data migration was performed.

**Release status: locally validated candidate, pending environment acceptance.**
Use [release/build/deployment/rollback instructions](RELEASE_v1.5.3.md).

## Completed gates

All Python commands used the repository `.venv/Scripts/python.exe` (Python 3.14.2).
Logs are retained under `output/qa153/` and are intentionally Git-ignored.

| Gate | Actual result | Evidence |
| --- | --- | --- |
| Full `pytest -q -ra` suite | **264 passed, 3 skipped, 169 subtests passed**, 33.12 seconds | `qa153-tests-final.log` |
| Shared `scripts/ci.py unit` | **252 tests, OK** | `qa153-unit.log` |
| Shared `scripts/ci.py contract` | **1 test, OK** | `qa153-contract.log` |
| Shared `scripts/ci.py integration` | **11 tests, OK**; PostgreSQL intentionally excluded by this gate | `qa153-integration.log` |
| Shared `scripts/ci.py e2e` | **PASS**, fixture orchestration -> manual DB matrix -> frozen snapshot -> final PDF | `qa153-e2e.log` |
| Shared `scripts/ci.py typecheck` | **PASS**, no issues in **111 source files** | `qa153-typecheck.log` |
| Shared `scripts/ci.py lint` | **PASS**, Ruff | `qa153-lint.log` |
| Shared `scripts/ci.py config` | **PASS**, valid fixture plus intentionally rejected unresolved production configuration | `qa153-config.log` |
| Shared `scripts/ci.py compose-config` | **PASS**, production and CI Compose models; no services started | `qa153-compose.log` |
| Shared `scripts/ci.py audit` | **PASS**, no known vulnerabilities found on network-enabled retry | `qa153-audit-retry.log` |
| `pip check` | **PASS**, no broken requirements | `qa153-final-integrity.log` |
| `node --check app/web/static/app.js` | **PASS** | `qa153-final-integrity.log` |
| `node --test scripts/test_frontend_153.cjs` | **6 passed, 0 failed** | `qa153-frontend.log` |
| `git diff --check` | **PASS** | `qa153-final-integrity.log` |
| Release instruction PowerShell parsing | **PASS**, syntax-only; deployment commands were not executed | `qa153-doc-syntax.log` |

The full-suite count overlaps the shared unit/contract/integration counts; do not add
them as independent coverage. One existing FastAPI/Starlette TestClient warning reports
httpx deprecation. Mypy also emits informational notes about untyped function bodies.

The three skipped tests are `test_duplicate_run_prevention_is_atomic`,
`test_manual_db_review_upsert_works_in_postgres`, and
`test_only_one_worker_claims_run` in `test_postgres_concurrency.py`.
No PostgreSQL URL was configured. No PostgreSQL executable was found on PATH, and the
Docker engine was unavailable; these checks did not pass and were not run against a
shared database.

## Verification coverage and boundaries

| Area | Completed verification | Remaining live check |
| --- | --- | --- |
| REJECT with DB PASS | Finalized PASS + other-module PASS/FAIL/ERROR cases, including Portainer, infrastructure, DOCTOR, RabbitMQ; reason retained in database/HTML/PDF; statuses unchanged | Ordinary-browser staged workflow |
| Approval and safety gates | Required DB comment, reviewer, rejection reason, reject policy, APPROVE policy, RECOVERY_REQUIRED block, repeated finalization block | Deployment access control |
| Frozen snapshot/PDF retry | Original snapshot bytes retained; note mutation blocked; normalized reviewer/redacted reason retry; same-page API freeze header and JS handling | Real-browser PDF failure/retry |
| Frontend | Six isolated Node DOM/event tests: duplicate editors, deduplicated saves, CSRF header, two-step confirmation, edits resetting confirmation, locked controls, failed policy, frozen retry | Browser rendering, keyboard/responsive layout, console/network, actual download |
| Portainer | Mock HTTP GET-only discovery of 21 services including Global; eligible nodes, constraints/platforms, pause/drain/down, historical/current failures, unavailable/partial health | Actual Swarm inventory and Portainer permissions |
| DOCTOR | Mock success/unhealthy/incomplete inventory, malformed JSON, 401/403/404/500, timeout/DNS/refused/TLS, bearer redaction, invalid fragment URL | Verified backend endpoint, response contract, auth/CA and actual inventory |
| RabbitMQ | Normal capacity, zero/missing/null capacity, critical metrics, existing queue tests and CA/TLS policy | Real `/api/nodes` measurements |
| Chrony/filesystems | Tracked source versus selected source, local reference, unsynchronized/source mismatch, zero reach, root uncertainty, timezone, NaN/infinity policy rejection; mapper filesystem at 95% remains FAIL; mapper regressions | Actual SSH/Chrony output and approved source/uncertainty policy |
| Secret handling | Nested fields, Env connection strings, URL credentials, inline secrets, opaque configured token, private keys, cookie headers; evidence and log assertions | Staged synthetic-sentinel end-to-end review |
| CSRF and connector auth | Existing signed/missing/invalid/expired-token tests; SSH password/strict known_hosts/TLS tests; no changes to `app/security.py` or SSH executor | External authentication/proxy boundary |
| Recording/manual DB script | Existing tests reject unsupported recording route/config; collector requests read-only; manual DB script stays workstation-operated | No recording execution was attempted |
| Database preservation | Populated SQLite old-schema upgrade twice, every preexisting column/row compared, PDF/snapshot bytes unchanged; concurrent finalize produces one report | PostgreSQL migration/locking and actual deployment row counts |

There was no application login boundary in the baseline and none was added or removed.
Reviewer attribution is manually supplied. CSRF does not establish identity. Existing
external authentication/access controls could not be inspected in this environment.

## PDF visual verification

Regenerated the four synthetic samples using `scripts/qa_release_153.py`:

| File | Pages | Scenario |
| --- | --- | --- |
| `output/pdf/v1.5.3-all-pass.pdf` | 5 | One site, all checks PASS, APPROVE |
| `output/pdf/v1.5.3-many-errors.pdf` | 22 | Two sites, 246 checks, 12 errors, REJECT |
| `output/pdf/v1.5.3-long-text.pdf` | 19 | Two sites, 246 checks, long target/diagnostic, REJECT |
| `output/pdf/v1.5.3-single-site-reject.pdf` | 5 | One site, DB PASS and automation PASS, deliberate REJECT |

All **51 pages** were rendered with Poppler and visually inspected in eight contact
sheets. Long-text pages 2/3 and the many-errors final page were also inspected at full
render size. No clipping, overlap, broken glyphs, missing page footers, or cut-off long
message was observed in these samples. Long diagnostic text reaches `END OF LONG MESSAGE`.
All page numbers were checked by extraction. Reports retain the DB result, review reason,
reviewer, timestamp, evidence reference/checksum, and one-site NOT_APPLICABLE parity.
This is synthetic English-report coverage, not a live production-report or browser check.
Poppler emitted system-font mapping warnings for unused font aliases; rendering exited 0.
Contact sheets/full renders are in `output/qa153/pdf-renders/`.

## Interrupted-session issues and fixes completed

The first system-Python attempt stopped at collection because ReportLab was absent.
The existing project virtual environment was then used; no implementation was restarted.
Its baseline reproduced **257 passed, 1 failed, 3 skipped** and 165 passing subtests.
The failed test expected the old `<TBD>` DOCTOR placeholder; it now checks the existing
explicit verified-endpoint placeholder. Two QA-script lint errors were fixed.

Further validation fixes in this resumed session:

1. Synchronize duplicate result note editors and save each endpoint only once, avoiding
   stale hidden-field overwrites; remove the duplicate save-notes event branch.
2. Disable final and review controls immediately after success. Reset confirmation when
   reviewer/reason changes. Keep failed policy checks retryable.
3. Signal a frozen snapshot after render failure; lock notes/manual DB controls so a
   same-page retry reaches finalization without attempting prohibited note writes.
4. Normalize padded reviewer/reason input consistently and compare redacted reasons on
   retry, preserving the original frozen artifact.
5. Redact textual Cookie/Set-Cookie headers, including multiple cookie values.
6. Reject non-finite Chrony uncertainty limits that would otherwise bypass comparisons.
7. Exclude local virtual environments, QA files and logs from Docker context; correct
   trailing whitespace; add release instructions and this validation record.

The first new Chrony test used the wrong fixture shape; it was corrected to iterate the
configured check list. Its three boundary subtests pass. The audit's first restricted
attempt failed during temporary package setup; a network-enabled rerun completed and
reported no known vulnerabilities. These failed attempts are not counted as passes.

## Remaining release blockers

- Docker client 29.7.2 and Compose 5.4.0 were present, but the Docker engine pipe was
  absent. Image builds, exact-image smoke, configured Python 3.14.7 container runtime,
  container health and volume behavior were **not tested**.
- Browser tool discovery was attempted twice. Both failed with
  `Unable to load browser request-header policy`. No browser navigation, screenshots,
  browser click workflow or real PDF download was performed. Node tests are a fallback,
  not browser acceptance.
- PostgreSQL tests skipped as described above. New finalization interleavings require
  additional disposable PostgreSQL acceptance, beyond the existing three-test gate.
- No live Portainer, DOCTOR, RabbitMQ, Chrony/SSH, reverse proxy, or production database
  was contacted. The actual DOCTOR API URL and schema remain unverified.
- The tree is uncommitted, including new migration/helpers/tests/documents. An image
  build must include those files; tagging only the old commit would omit this release.

## Existing local database

The only preexisting repository SQLite file found was `tmp/ui-preview.sqlite`.
Its before/after SHA-256 is
`4d533d404ee37908cdd14027412e77723722055e2f2a71f0b4b664b79914a775`.
No production records were read or changed. Byte equality is recorded in
`output/qa153/qa153-final-integrity.log`.

## All modified and new files

The following inventory is generated from the final Git working tree. `M` means a
modified tracked file; `??` means a new untracked file. Paths are repository-relative.
It includes the preexisting implementation and the resumed-session fixes, with no
deleted files. Generated PDFs, logs and contact sheets are ignored QA artifacts.

59 changed/new files.

```text
 M .dockerignore
 M .gitignore
 M Dockerfile
 M TAG
 M app/api/routes_reports.py
 M app/api/routes_review.py
 M app/api/routes_runs.py
 M app/collectors/doctor.py
 M app/collectors/infrastructure.py
 M app/collectors/portainer.py
 M app/collectors/rabbitmq.py
 M app/config/loader.py
 M app/config/validation.py
 M app/database/repository.py
 M app/domain.py
 M app/evidence/manager.py
 M app/orchestrator/runner.py
 M app/reporting/final_pdf.py
 M app/reporting/html.py
 M app/reporting/snapshot.py
 M app/review/finalization.py
 M app/validators/doctor.py
 M app/validators/infrastructure.py
 M app/validators/portainer.py
 M app/validators/rabbitmq.py
 M app/web/static/app.css
 M app/web/static/app.js
 M app/web/templates/base.html
 M app/web/templates/module.html
 M app/web/templates/review.html
 M app/web/templates/run.html
 M app/worker/main.py
 M deploy/docker/config/rules.yml
 M deploy/docker/env/doctor.env.example
 M deploy/docker/env/rabbitmq.env.example
 M docs/CONFIGURATION.md
 M requirements.runtime.txt
 M requirements.txt
 M scripts/ci_e2e.py
 M tests/integration/test_run_workflow.py
 M tests/unit/test_config_validation.py
 M tests/unit/test_docker_config.py
 M tests/unit/test_dynamic_sites.py
 M tests/unit/test_finalization_readiness.py
 M tests/unit/test_infrastructure_mapper.py
 M tests/unit/test_rabbitmq.py
 M tests/unit/test_review_notes.py
 M tests/unit/test_validators.py
?? app/collectors/swarm.py
?? app/database/migrations/005_add_rejection_reason.sql
?? app/evidence/redaction.py
?? app/observability.py
?? app/reporting/pdf_layout.py
?? docs/RELEASE_v1.5.3.md
?? docs/VALIDATION_v1.5.3.md
?? scripts/qa_release_153.py
?? scripts/test_frontend_153.cjs
?? tests/unit/test_release_153.py
?? tests/unit/test_resume_153.py
```
