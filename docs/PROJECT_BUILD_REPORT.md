# Project Build Report

**Updated:** 2026-08-19

## 1. Current State

The Weekend Report project has completed:

- core framework implementation;
- production security/review hardening;
- portability hardening;
- Python 3.14 migration;
- shared local CI command contract;
- GitHub Actions quality pipeline;
- GitHub verified-image pipeline;
- GitLab quality/image pipeline definitions for later use;
- disposable PostgreSQL concurrency testing;
- exact-image Docker smoke testing;
- TAG-file-driven release versioning/trigger design.

No real production Portainer/RabbitMQ/SSH/Database/DOCTOR/Recording integration has been enabled through this documentation pass.

## 2. Current Runtime Baseline

```text
Python:          3.14
Docker base:     python:3.14-slim-bookworm
Validated image: Python 3.14.7
PostgreSQL:      16-alpine
```

Python 3.14 dependency compatibility changes included:

- Python-3.14-compatible PyYAML;
- Python-3.14-compatible Psycopg binary;
- PyYAML typing stubs for Mypy;
- Ruff import fixes;
- removal of obsolete Mypy ignore for `yaml`.

## 3. Core Application Capabilities

Implemented:

- FastAPI web UI/API;
- persistent worker;
- PostgreSQL repository/migrations;
- SQLite fixture-test path;
- run states;
- singleton execution lock;
- worker heartbeat/current module;
- stale-worker/recovery behavior;
- protected production read/mutation paths;
- reviewer-bound CSRF;
- result/evidence persistence;
- checksum/path controls;
- HTML review;
- module/result/Splunk/general notes;
- finalization-readiness policy;
- configured aggregation;
- immutable snapshot;
- one multi-page final PDF;
- protected final-PDF serving;
- portable traceability.

## 4. Module Boundaries

### Portainer

Implemented generic read-only Docker Swarm Service collector/validator architecture, fixture mode, sanitization, task-state/image/replica/parity tests.

Live mode requires real approved environment details.

### RabbitMQ

Expected-state/topology validation architecture exists; real Management API configuration is still environment-dependent.

### Recording

Existing-device start/stop safety model exists.

Real state-changing live calls remain disabled until approved.

### Database

Owner-supplied sync-function adapter boundary exists.

Live execution remains blocked until the approved function/contract is provided.

### Infrastructure

Filesystem/NFS/Chrony validators exist.

Live SSH is environment-dependent.

### DOCTOR / Splunk

Manual/API and manual-dashboard-review boundaries exist; production definitions are still required.

## 5. CI/CD Architecture

Shared command contract:

```text
scripts/ci.py
```

Safe E2E:

```text
scripts/ci_e2e.py
```

CI-only exact-image Compose:

```text
deploy/docker/compose.ci.yml
```

GitHub:

```text
.github/workflows/quality-gates.yml
.github/workflows/build-image.yml
```

GitLab:

```text
.gitlab-ci.yml
.gitlab/ci/quality.yml
.gitlab/ci/image.yml
```

## 6. Pre-Image Gates

Release-blocking gates:

```text
Config validation
Ruff
Mypy
Unit tests
Contract tests
Integration tests
PostgreSQL concurrency
Safe fixture E2E
pip-audit
Docker Compose validation
```

A failure stops image creation.

## 7. Release Trigger Model

The root:

```text
TAG
```

is the authoritative release trigger/version source.

Example:

```text
v1.0.1
```

Normal commits:

```text
quality only
NO release image
```

TAG change on the configured release/default branch:

```text
quality gates again
-> image build
-> exact-image smoke
-> verified archive/checksum
-> optional registry publication
```

The `v` prefix is preserved.

Git tags (`GITHUB_REF_NAME`, `CI_COMMIT_TAG`) are not the application release-version source.

## 8. Local Python 3.14 Validation Results

Verified during the current migration cycle:

### Configuration

PASS:

```text
python scripts/ci.py config
```

The production-template half intentionally reported unresolved placeholders and ended with:

```text
Configuration invalid as expected
```

### Ruff

PASS:

```text
python scripts/ci.py lint
```

### Mypy

PASS:

```text
python scripts/ci.py typecheck
```

Result observed:

```text
Success: no issues found in 95 source files
```

Informational `annotation-unchecked` notes remain non-failing.

### Unit

PASS before the final TAG-only regression correction:

```text
75 unit tests passed
```

A new unit regression test was then added:

```text
test_image_release_is_driven_by_tag_file
```

The first hosted run of that test caught remaining legacy `CI_COMMIT_TAG` references in `.gitlab/ci/image.yml`, proving the gate worked as intended.

The GitLab image definition was then required to be made fully TAG-driven. Run the hosted quality pipeline once more after the final YAML correction to record the final hosted result.

### Contract

PASS:

```text
1 contract test
```

### Non-PostgreSQL integration

PASS:

```text
4 integration workflow tests
```

### PostgreSQL concurrency

VERIFIED LOCALLY with a disposable PostgreSQL 16 Docker container.

Required test-only env:

```text
WEEKEND_REPORT_TEST_POSTGRES_URL
WEEKEND_REPORT_TEST_POSTGRES_DISPOSABLE=1
```

This is no longer considered an unverified local capability.

### Safe E2E

PASS:

```text
create -> claim -> execute -> evidence -> review -> notes -> approve -> snapshot -> final PDF
```

### Dependency audit

PASS at time tested:

```text
No known vulnerabilities found
```

### Compose validation

PASS for:

```text
deploy/docker/compose.yml
deploy/docker/compose.ci.yml
```

## 9. Docker / Image Verification

PASS:

```powershell
docker build --no-cache -t weekend-report:python314 .
```

Observed image:

```text
sha256:263f4c07558118fb4c5b5098fd4428e643682c8a66dabcb7f1b21397b1212809
```

PASS:

```powershell
docker run --rm weekend-report:python314 python --version
```

Observed:

```text
Python 3.14.7
```

PASS:

```powershell
python scripts/ci.py image-smoke --image weekend-report:python314
```

Observed:

- disposable PostgreSQL healthy;
- web started;
- worker started;
- `/healthz` returned OK;
- database schema initialized;
- exact-image smoke passed;
- containers/volumes/networks removed successfully.

## 10. Docker Build-Context Improvement

An earlier Docker build transferred roughly 153 MB of context.

After ignore/cleanup changes, the later Python 3.14 build transferred roughly:

```text
630.89 kB
```

This confirms `.dockerignore`/project cleanup significantly reduced build context.

## 11. GitHub Actions Status

A hosted GitHub Actions quality run reached green after the Python 3.14/Ruff/Mypy/dependency corrections.

The image workflow did not run on a normal commit, which exposed that the desired release policy was not Git-tag-driven.

The design was then corrected to:

```text
normal commit -> quality only
TAG change -> gated image delivery
```

The first hosted pre-image run after adding the TAG-only regression test failed intentionally because `.gitlab/ci/image.yml` still contained `CI_COMMIT_TAG`.

That defect is a CI-definition consistency issue, not an application failure.

Final required hosted verification after correcting `.gitlab/ci/image.yml`:

```text
test_image_release_is_driven_by_tag_file ... ok
```

and then the TAG-driven image workflow should be tested with an intentional `TAG` change.

## 12. GitLab Status

GitLab CI definitions are present and designed to use:

```text
default branch + rules:changes: TAG
```

The release version must come from the file content.

Actual GitLab Runner execution is still pending future GitLab import/use.

## 13. Remaining Controlled Production Inputs

Still required locally/organizationally:

- site definitions;
- manager-approved rules/approval policy;
- Portainer real values;
- RabbitMQ real values;
- Recording contracts/approval;
- database adapter binding;
- infrastructure inventory/SSH policy;
- DOCTOR mode/contract;
- Splunk dashboard definitions;
- production authentication source;
- reviewer authorization;
- evidence retention/backups;
- distribution/archive policy.

See `docs/ENVIRONMENT_INPUTS_REQUIRED.md`.

## 14. Known Non-Blocking Maintenance

Unit tests emit a Starlette/FastAPI TestClient deprecation warning concerning the future `httpx2` transition.

It is currently a warning, not a failing gate.

Do not change working FastAPI/Starlette/httpx dependencies solely to silence it without a deliberate compatibility upgrade/test pass.

## 15. Current Readiness

### Core application framework

READY for controlled real-environment configuration.

### Python 3.14

VERIFIED locally and in Docker.

### Docker image runtime

VERIFIED locally.

### PostgreSQL concurrency

VERIFIED locally with disposable PostgreSQL.

### GitHub quality pipeline

VERIFIED before the final TAG-trigger consistency edit; rerun required after the final GitLab YAML TAG-only correction.

### GitHub TAG-driven image pipeline

IMPLEMENTED DESIGN; must be exercised with an intentional TAG change after the final pre-image pipeline is green.

### GitLab pipeline

DEFINED / NOT YET RUN ON A REAL GITLAB RUNNER.

### Production integrations

NOT YET ENABLED.

## 16. Next Engineering Step

After the CI release-trigger correction is green:

1. preserve the current core baseline;
2. obtain manager-approved global policy;
3. fill private site definitions locally;
4. begin Portainer as the first live read-only integration;
5. keep all secrets outside YAML/source control.
