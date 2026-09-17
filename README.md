# Weekend Report Automation

**Documentation synchronized:** 2026-09-06

Weekend Report Automation is a manually triggered operational-validation application built with Python 3.14, FastAPI, a persistent worker, PostgreSQL, filesystem evidence storage, HTML review pages, immutable review snapshots, and one final PDF generated only after explicit human confirmation.

The application is intentionally configuration-driven. The Docker image contains code only; deployment
values come from external ENV files/secrets, and business validation policy comes from read-only
`rules.yml`.

## Current Operating Model

```text
Operator opens Weekend Report
        |
        v
RUN WEEKEND REPORT
        |
        v
Configuration preflight
        |
        v
Database-backed execution lock
        |
        v
CREATED -> RUNNING
        |
        v
Collectors + validators + evidence
        |
        v
REVIEW_READY
        |
        v
HTML review + notes
        |
        v
APPROVE / REJECT
        |
        v
Immutable review snapshot
        |
        v
One final PDF
```

The Weekend Report itself is never scheduled by GitHub Actions or GitLab CI/CD.

## Runtime Architecture

- **web**: FastAPI UI/API for run creation, protected HTML review, notes, evidence, recovery resolution, and final confirmation.
- **worker**: persistent Python process that atomically claims `CREATED` runs and executes the orchestrator.
- **database**: PostgreSQL in deployment; SQLite is used by safe local fixture tests.
- **evidence**: persistent run evidence under the configured evidence root.
- **configuration**: read-only `rules.yml` policy plus runtime ENV/secrets supplied outside the image.
- **reporting**: frozen snapshot first, then exactly one final PDF rendered from that snapshot.
- **traceability**: `application_version`, `build_id`, deterministic `configuration_hash`, plus optional Git commit metadata when available.

## Supported Python Version

The current project and delivery pipeline are standardized on:

```text
Python 3.14
```

Current validated Docker runtime:

```text
python:3.14-slim-bookworm pinned by digest
Python 3.14.7
```

Local development should use a Python 3.14 virtual environment. The virtual environment itself is not part of the deployable project and must not be committed or transferred as runtime content.

## Local Quality Gates

Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

Run the shared gates:

```powershell
python scripts/ci.py config
python scripts/ci.py lint
python scripts/ci.py typecheck
python scripts/ci.py unit
python scripts/ci.py contract
python scripts/ci.py integration
python scripts/ci.py e2e
python scripts/ci.py audit
python scripts/ci.py compose-config
```

The PostgreSQL concurrency gate requires a disposable PostgreSQL database:

```powershell
$env:WEEKEND_REPORT_TEST_POSTGRES_URL = "postgresql://USER:PASSWORD@HOST:PORT/weekend_report_test"
$env:WEEKEND_REPORT_TEST_POSTGRES_DISPOSABLE = "1"
python scripts/ci.py postgres
```

Never point that gate at a production/shared database.

The expected-invalid part of `python scripts/ci.py config` intentionally proves unresolved
deployment ENV does not pass production preflight and finishes with:

```text
Configuration invalid as expected
```

That is a PASS until production configuration is intentionally completed.

## Docker

Build:

```powershell
docker build --no-cache -t weekend-report:python314 .
```

Verify the runtime:

```powershell
docker run --rm weekend-report:python314 python --version
```

Smoke-test the exact built image:

```powershell
python scripts/ci.py image-smoke --image weekend-report:python314
```

The CI smoke definition is `deploy/docker/compose.ci.yml`. It contains no `build:` directive, so it tests the exact image supplied through `WEEKEND_REPORT_CI_IMAGE`.

The FastAPI container never mounts the Docker socket.

## CI/CD and Release Model

The project has one shared quality-gate contract (`scripts/ci.py`) and thin adapters for:

```text
GitHub Actions
GitLab CI/CD
```

### Normal source change

```text
source commit/push
    |
    v
pre-image quality gates
    |
    +--> failure: STOP
    |
    `--> success: quality PASS only

NO release image is created merely because normal source code changed.
```

### Release image trigger

The authoritative release trigger and release version are stored in the root file:

```text
TAG
```

Example:

```text
v1.0.2
```

Changing `TAG` on the configured default branch triggers the image-delivery path.

```text
TAG changed
    |
    v
pre-image quality gates run again
    |
    v
all gates PASS
    |
    v
build exact image
    |
    v
smoke exact image
    |
    v
tag same image as weekend-report:<TAG>
    |
    v
export verified image + SHA-256
    |
    `--> optional registry publication
```

A Git tag is **not** required to trigger image creation.

The `v` prefix is preserved everywhere:

```text
TAG:            v1.0.2
image version:  v1.0.2
registry tag:   :v1.0.
archive:        weekend-report_v1.0.2_<short-sha>.tar.gz
```

The generated archive must load as:

```text
weekend-report:v1.0.2
```

Key delivery files:

```text
TAG
.github/workflows/quality-gates.yml
.github/workflows/build-image.yml
.gitlab-ci.yml
.gitlab-ci-cd/quality.yml
.gitlab-ci-cd/image.yml
deploy/docker/compose.ci.yml
scripts/ci.py
scripts/ci_e2e.py
docs/CI_CD.md
```

GitHub Actions is usable now. GitLab CI/CD remains ready for later import/use on the target PC. Neither platform is a runtime dependency.

## Configuration

Secrets and deployment values belong in non-committed ENV files under `deploy/docker/env/`, Docker
secrets, or another approved secret mechanism. Production Compose selects the already-built or loaded
image with `WEEKEND_REPORT_IMAGE`; for a verified release it should point at a loaded versioned image
such as `weekend-report:v1.0.2`.

Policy remains in YAML:

```text
deploy/docker/config/rules.yml
```

Deployment inventory, endpoints, and environment-specific targets are supplied through runtime ENV files.
The application does not use per-module deployment YAML for those values.

Do not put credentials in YAML, fixtures, documentation, image layers, or CI artifacts.

Controlled placeholders:

- `<TBD>`: required value not yet supplied.
- `<TO_VERIFY>`: candidate value requiring verification.
- `<NOT_APPLICABLE>`: intentionally not applicable only where schema permits it.

Real runs must stay blocked while required production values are unresolved.

## Live Integration Status

The framework supports the following module boundaries, but real environment enablement remains controlled:

- Portainer: read-only Docker Swarm service discovery, independent site health, and cross-site parity.
- RabbitMQ: read-only Management API queue and node-health validation.
- Infrastructure: read-only SSH/command collection for root-filesystem usage and Chrony/NTP state.
- DOCTOR: dynamic cross-site service validation and live Health Checks UI `doctor-api` collection are implemented.
- Splunk: human dashboard review.
- Recording: existing-device workflow and recovery policy are implemented, but live collection/control remains intentionally blocked.

Do not enable any live integration until the required environment values, expected state, integration credentials, evidence policy, and status semantics are approved.

## Review and Finalization

Weekend Report does not implement an application login or user-account boundary. Control network exposure at the deployment layer.

Browser mutations use signed CSRF tokens when the CSRF signing key is configured; production preflight requires the key.

Reviewer notes are additive and never rewrite machine findings.

Final confirmation:

1. validates readiness policy;
2. freezes results, evidence references, notes, summaries, the manually entered reviewer name, decision, and traceability;
3. writes `review_snapshot.json`;
4. renders exactly one final PDF from that immutable snapshot;
5. records the PDF path and checksum.

See:

- `docs/ARCHITECTURE.md`
- `docs/CI_CD.md`
- `docs/CONFIGURATION.md`
- `docs/DEPLOYMENT.md`
- `docs/RECOVERY_POLICY.md`
- `docs/VALIDATION_CATALOG.md`


