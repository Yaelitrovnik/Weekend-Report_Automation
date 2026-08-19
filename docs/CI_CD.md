# CI/CD and Verified Image Delivery

**Documentation synchronized:** 2026-08-19

## 1. Purpose

The project has one shared quality-gate contract and two CI adapters:

```text
scripts/ci.py
    |
    +--> GitHub Actions
    |
    `--> GitLab CI/CD
```

The CI platforms orchestrate the same local commands. They do not contain a second implementation of Weekend Report business logic.

The operational Weekend Report remains manually triggered from FastAPI. CI never starts a real production Weekend Report and never performs a real state-changing Recording action.

## 2. Current Python / Container Baseline

```text
Local Python:        3.14
GitHub Actions:      3.14
GitLab quality jobs: 3.14
Application image:   python:3.14-slim-bookworm
Validated runtime:   Python 3.14.7
PostgreSQL CI:       postgres:16-alpine
```

Important dependency compatibility pins include the Python-3.14-compatible PyYAML/Psycopg packages and the PyYAML typing stubs used by Mypy.

## 3. Two Different Pipelines

### 3.1 Pre-image quality pipeline

Normal code changes run validation only.

```text
SOURCE CHANGE
    |
    v
CONFIG VALIDATION
    |
    v
RUFF
MYPY
UNIT TESTS
CONTRACT TESTS
INTEGRATION TESTS
POSTGRESQL CONCURRENCY
SAFE FIXTURE E2E
DEPENDENCY AUDIT
COMPOSE VALIDATION
    |
    v
QUALITY PASS
```

A normal commit **does not build a release image**.

### 3.2 TAG-driven image pipeline

The authoritative release trigger/version source is the root file:

```text
TAG
```

Example content:

```text
v1.0.1
```

The file contains one semantic-style version value and preserves the leading `v`.

Changing `TAG` on the configured release/default branch starts the image-delivery path.

```text
TAG changed
    |
    v
pre-image quality gates run again
    |
    +--> failure: STOP, no image
    |
    `--> success
            |
            v
        BUILD EXACT IMAGE
            |
            v
        SMOKE EXACT IMAGE
            |
            +--> failure: STOP, no verified release
            |
            `--> success
                    |
                    v
        EXPORT VERIFIED ARCHIVE + SHA-256
                    |
                    `--> optional registry push
```

A Git tag is not required.

Do not reintroduce version derivation from:

```text
GITHUB_REF_NAME
CI_COMMIT_TAG
```

The unit regression test `test_image_release_is_driven_by_tag_file` exists specifically to protect this rule.

## 4. TAG File Contract

Path:

```text
/TAG
```

Valid example:

```text
v1.0.1
```

Recommended validation expression:

```text
^v[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.-]+)?$
```

Release version usage:

```text
TAG value       v1.0.1
OCI version     v1.0.1
registry tag    :v1.0.1
archive prefix  weekend-report_v1.0.1_
```

Normal CI/SHA identities remain separate:

```text
weekend-report:ci-<short-sha>
sha-<short-sha>
```

## 5. Shared Local Gates

Install:

```powershell
python -m pip install -r requirements.txt
```

Run:

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

PostgreSQL concurrency:

```powershell
$env:WEEKEND_REPORT_TEST_POSTGRES_URL = "postgresql://USER:PASSWORD@HOST:PORT/weekend_report_test"
$env:WEEKEND_REPORT_TEST_POSTGRES_DISPOSABLE = "1"
python scripts/ci.py postgres
```

Only use a disposable/test PostgreSQL database.

## 6. Configuration Gate Semantics

`python scripts/ci.py config` performs two checks:

1. `tests/fixtures/config_valid` must be valid.
2. production template `config/` must currently be invalid while unresolved controlled placeholders remain.

Therefore a long list of expected production-template errors followed by:

```text
Configuration invalid as expected
```

is a successful gate during the configuration-template phase.

Once production configuration is intentionally completed, the release/deployment procedure must also run direct production validation without `--expect-invalid`.

## 7. Safe Fixture E2E

`scripts/ci_e2e.py` does not contact production systems.

It verifies:

```text
create run
  -> worker claim
  -> fixture module execution
  -> evidence
  -> REVIEW_READY
  -> reviewer notes
  -> approval
  -> frozen snapshot
  -> final PDF
```

It verifies:

- automated statuses do not change during review;
- persisted notes are all present in the frozen snapshot;
- notes are represented in the final PDF;
- evidence is produced.

## 8. PostgreSQL Concurrency Gate

The concurrency gate is release-blocking.

It requires:

```text
WEEKEND_REPORT_TEST_POSTGRES_URL
WEEKEND_REPORT_TEST_POSTGRES_DISPOSABLE=1
```

It verifies:

- two simultaneous run-creation attempts cannot create two active Weekend Reports;
- multiple workers cannot claim the same `CREATED` run.

Both GitHub and GitLab definitions provide a disposable PostgreSQL 16 service for this gate.

Local verification can use a temporary Docker PostgreSQL container.

## 9. Built-Image Smoke

Build locally:

```powershell
docker build --no-cache -t weekend-report:python314 .
```

Confirm runtime:

```powershell
docker run --rm weekend-report:python314 python --version
```

Expected current runtime:

```text
Python 3.14.x
```

Smoke:

```powershell
python scripts/ci.py image-smoke --image weekend-report:python314
```

The smoke flow uses `deploy/docker/compose.ci.yml`.

It verifies:

- PostgreSQL starts/healthy;
- web starts;
- worker starts;
- `/healthz` returns OK;
- migration/database access succeeds;
- exact supplied image is used;
- temporary containers/volumes/networks are removed.

`compose.ci.yml` must not contain a `build:` directive.

## 10. GitHub Actions

Files:

```text
.github/workflows/quality-gates.yml
.github/workflows/build-image.yml
```

### 10.1 Quality workflow

Quality runs on normal repository activity such as configured pushes/pull requests and can be called by the image workflow.

It exposes separate release-blocking jobs so failures are visible by category:

```text
config-validation
ruff
mypy
unit-tests
integration-tests
contract-tests
postgres-concurrency
safe-e2e
dependency-audit
docker-compose-validate
```

A pre-image failure prevents the image job from starting.

### 10.2 Image workflow trigger

The release image workflow is intentionally **not** triggered by every normal commit.

It is triggered when the root `TAG` file changes on the configured release/default branch.

Concept:

```yaml
on:
  push:
    branches:
      - main
    paths:
      - TAG
```

If the project uses a different release branch, keep the actual workflow branch name authoritative.

The workflow reads the version from:

```bash
version="$(tr -d '\r\n' < TAG)"
```

It must not use Git tag references as the application release version.

### 10.3 GitHub release flow

```text
TAG change
   |
   v
reusable pre-image quality workflow
   |
   v
needs: quality
   |
   v
derive TAG version + build identity
   |
   v
Buildx build/load exact local image
   |
   v
record image ID
   |
   v
exact-image smoke
   |
   v
docker save archive
   |
   v
SHA-256
   |
   v
upload workflow artifact
   |
   `--> optional GHCR push
```

No production integration secrets are required.

### 10.4 GitHub registry controls

Registry publication remains optional.

When enabled, publish the already-tested image as:

```text
ghcr.io/<owner>/<repo>:sha-<short-sha>
ghcr.io/<owner>/<repo>:v1.0.1
```

Optional:

```text
ghcr.io/<owner>/<repo>:latest
```

Do not strip the `v` from the `TAG` value.

Do not publish a different rebuilt image.

## 11. GitLab CI/CD

Files:

```text
.gitlab-ci.yml
.gitlab/ci/quality.yml
.gitlab/ci/image.yml
```

GitLab is currently maintained as a future-ready delivery path for later import/use.

### 11.1 Quality jobs

GitLab exposes the same quality categories and uses the same `scripts/ci.py` commands.

### 11.2 Image-job trigger

The image job must run only for a root `TAG` change on the default branch.

Concept:

```yaml
rules:
  - if: '$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH'
    changes:
      - TAG
    when: on_success
  - when: never
```

The image version must be read from:

```bash
IMAGE_VERSION="$(tr -d '\r\n' < TAG)"
```

The GitLab image file must not derive the release version from `CI_COMMIT_TAG`.

### 11.3 GitLab runner requirement

The current image implementation uses Docker-in-Docker because the same job must build and run the exact image.

Typical requirements:

```text
docker:27-cli
docker:27-dind
Docker-capable GitLab Runner
```

Self-managed GitLab may require a privileged Docker service depending on Runner configuration.

If privileged DinD is forbidden, replace the image execution adapter with an approved equivalent that can still satisfy:

```text
build once -> test exact image -> export/push exact image
```

Do not weaken pre-image gates.

## 12. Verified Offline Image Artifact

After smoke success:

```text
weekend-report_<TAG-version>_<short-sha>.tar.gz
weekend-report_<TAG-version>_<short-sha>.tar.gz.sha256
image-id.txt
```

Example:

```text
weekend-report_v1.0.1_abc123def456.tar.gz
```

Verify after transfer:

```powershell
Get-FileHash .\weekend-report_v1.0.1_<short-sha>.tar.gz -Algorithm SHA256
```

Compare with the `.sha256` file.

Load:

```powershell
docker load -i .\weekend-report_v1.0.1_<short-sha>.tar.gz
```

If the local Docker version requires decompression first, decompress to `.tar` then load.

## 13. Release Procedure

### Normal development

Make application/CI/docs changes and push them.

Expected result:

```text
quality pipeline runs
image release does not run
```

### Create a release image

When the normal quality pipeline is green and the code is ready:

1. edit only/primarily `TAG`;
2. bump for example:

```diff
-v1.0.0
+v1.0.1
```

3. commit:

```text
chore(release): bump version to v1.0.1
```

4. push the default/release branch.

Expected result:

```text
TAG change detected
  -> pre-image gates run again
  -> image build only if green
  -> image smoke
  -> verified artifact
  -> optional publish
```

No `git tag -a ...` command is required for this release mechanism.

## 14. Dependency Audit and Closed Networks

`pip-audit` is a release-blocking quality gate when its approved vulnerability/package sources are available.

GitHub-hosted runners can normally use network sources.

A closed/self-managed GitLab installation must provide an approved internal/offline source or treat the gate as blocked rather than silently reporting PASS.

## 15. Secrets / Production Isolation

Standard CI must not receive:

- Portainer production tokens;
- RabbitMQ production passwords;
- production SSH private keys;
- production DB validation credentials;
- DOCTOR credentials;
- Recording credentials;
- production evidence.

CI uses fixture configuration and disposable infrastructure only.

## 16. CI Regression Tests

`tests/unit/test_ci_config.py` verifies delivery invariants, including:

- CI YAML parses;
- quality gates remain separately visible;
- image build depends on pre-image gates;
- exact image is smoked before export/publish;
- CI files do not contain known production integration secrets;
- `compose.ci.yml` uses an exact image and contains no `build:`;
- release image is driven by the `TAG` file;
- GitHub release logic does not fall back to `GITHUB_REF_NAME`/Git tags;
- GitLab release logic does not fall back to `CI_COMMIT_TAG`.

Do not weaken these tests simply to make a pipeline green. Fix the delivery file that violated the invariant.

## 17. Failure Diagnosis

```text
Ruff FAIL
  -> image NOT STARTED

Mypy FAIL
  -> image NOT STARTED

Unit regression detects stale CI_COMMIT_TAG
  -> image NOT STARTED
  -> fix .gitlab/ci/image.yml

PostgreSQL concurrency FAIL
  -> image NOT STARTED

All pre-image gates PASS
Image build PASS
Image smoke FAIL
  -> no verified archive/registry release
```

This fail-before-build behavior is intentional.

## 18. Current Verification Notes

Verified locally during the Python 3.14 migration:

- configuration gate behavior;
- Ruff;
- Mypy;
- unit/contract/non-PostgreSQL integration tests;
- PostgreSQL concurrency using a disposable PostgreSQL container;
- safe fixture E2E;
- dependency audit with no known vulnerabilities at the time tested;
- Docker Compose validation;
- Docker build using `python:3.14-slim-bookworm`;
- container runtime `Python 3.14.7`;
- exact-image smoke with PostgreSQL, web, worker, `/healthz`, and migration.

A hosted GitHub quality run also passed before the final TAG-file release-trigger refactor. The first hosted pre-image run after adding the TAG-only regression test correctly detected remaining legacy `CI_COMMIT_TAG` logic in the GitLab image definition. After correcting that definition, run GitHub Actions again before claiming the final TAG-only refactor is hosted-verified.

Actual GitLab Runner execution must still be verified after the project is imported to GitLab.
