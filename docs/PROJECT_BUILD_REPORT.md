# Project Build Report

**Updated:** 2026-08-23

## 1. Baseline Replacement

The project source was completely replaced with the supplied home ZIP baseline:

```text
C:\Users\Administrator\Downloads\Weekend-Report_Automation-master.zip
```

The active project root is:

```text
C:\Users\Administrator\Desktop\Projects\Yael\weekend_report\weekend-report
```

The root contains the expected ZIP project shape:

```text
.github/
.gitlab/
app/
config/
deploy/
docs/
scripts/
tests/
.dockerignore
.env.example
.gitignore
.gitlab-ci.yml
AGENTS.md
Dockerfile
pyproject.toml
README.md
requirements.txt
TAG
```

Intentionally preserved outside the ZIP:

- `.git/` repository metadata.

No real local `.env`, certificates, private keys, or production secrets were copied into source.
No branch, commit, push, Git tag, release publication, or `TAG` bump was performed.

## 2. Files Changed After Replacement

Files modified after the home ZIP baseline was copied:

```text
.env.example
.github/workflows/build-image.yml
.gitlab-ci.yml
.gitlab/ci/image.yml
Dockerfile
README.md
deploy/docker/README.md
deploy/docker/compose.yml
deploy/docker/env.example
docs/ARCHITECTURE.md
docs/CI_CD.md
docs/CONFIGURATION_GUIDE.md
docs/DOCUMENTATION_INDEX.md
docs/ENVIRONMENT_INPUTS_REQUIRED.md
docs/PORTABLE_DEPLOYMENT.md
docs/PROJECT_BUILD_REPORT.md
docs/RECOVERY_POLICY.md
docs/VALIDATION_CATALOG.md
scripts/migrate.py
tests/unit/test_ci_config.py
tests/unit/test_docker_config.py
tests/unit/test_recovery.py
```

## 3. Required Fixes Implemented

- GitHub image workflow now builds `weekend-report:ci-<short-sha>`, records its image ID,
  smoke-tests that exact image, tags the same image as `weekend-report:<TAG>`, verifies matching
  image IDs, and saves the versioned release image.
- GitLab image workflow now follows the same build-once, smoke-once, version-tag-same-image,
  export-same-image sequence.
- GitLab root workflow no longer starts pipelines solely because a Git tag was pushed.
- GitHub release branch was verified locally as `origin/main`; the image workflow now uses `main`.
- Production Compose no longer has a production `build:` path and uses
  `${WEEKEND_REPORT_IMAGE:-weekend-report:local}` for both web and worker.
- `.env.example` and `deploy/docker/env.example` are template-only and use `<TBD>` for unresolved
  runtime identity values; both include `WEEKEND_REPORT_IMAGE=weekend-report:local`.
- `docs/RECOVERY_POLICY.md` was restored to the full stale-worker/Recording recovery policy.
- Docker base image was pinned to the locally verified Python 3.14 Slim Bookworm digest:
  `sha256:23c59390fc717bf09f9336908199a0ae75d9c4264bf296123f94ad772fea3b52`.
- `scripts/migrate.py` now closes its repository connection deterministically.
- Regression tests were strengthened for CI release invariants, Compose image selection,
  environment templates, Dockerfile digest pinning, and recovery-policy coverage.

## 4. Final Release Flow

Normal source commit:

```text
source change
-> config validation
-> Ruff
-> Mypy
-> unit / contract / integration / PostgreSQL concurrency / safe E2E
-> pip-audit
-> Compose validation
-> no release image
```

TAG release:

```text
TAG changed on main/default branch
-> pre-image quality gates
-> build weekend-report:ci-<short-sha>
-> record image ID
-> smoke weekend-report:ci-<short-sha>
-> docker tag same image weekend-report:<TAG>
-> verify CI tag ID == release tag ID
-> docker save weekend-report:<TAG>
-> optional registry tags point to the same image
```

The root `TAG` file remains `v1.0.1` and preserves the leading `v`.

## 5. Offline Image Behavior

The release artifact name remains:

```text
weekend-report_v1.0.1_<short-sha>.tar.gz
```

After `docker load` of that artifact, the expected deployable image tag is:

```text
weekend-report:v1.0.1
```

The archive is produced from `weekend-report:<TAG>`, not only from the temporary CI tag.

## 6. Production Compose Behavior

`deploy/docker/compose.yml` selects the application image for both web and worker with:

```text
WEEKEND_REPORT_IMAGE
```

Default local value:

```text
weekend-report:local
```

Verified release deployment example:

```text
WEEKEND_REPORT_IMAGE=weekend-report:v1.0.1
WEEKEND_REPORT_APP_VERSION=v1.0.1
WEEKEND_REPORT_BUILD_ID=<actual-build-id>
```

Real runtime secrets must come from a non-committed `.env`, Docker secret, or approved secret
mechanism. Literal `<TBD>` values are not accepted as production runtime secrets/identity.

## 7. Test Results

Unittest suites:

```text
PASS: 88
FAIL: 0
SKIP: 0
```

Breakdown:

- unit: 81 passed;
- contract: 1 passed;
- integration: 4 passed;
- PostgreSQL concurrency: 2 passed.

Additional gates:

- config fixture validation: PASS;
- expected-invalid production template validation: PASS, with `Configuration invalid as expected`;
- safe fixture E2E: PASS;
- pip-audit: PASS, no known vulnerabilities found;
- Docker Compose config validation: PASS for `compose.yml` and `compose.ci.yml`;
- Docker exact-image smoke: PASS;
- local image-ID invariant check: PASS.

Warnings observed:

- existing FastAPI/Starlette `httpx2` deprecation warning in unit tests;
- Docker/Compose warning reading `C:\Users\Administrator\.docker\config.json`, but Compose config
  validation succeeded.

## 8. PostgreSQL Concurrency

PASS.

Executed with a disposable local `postgres:16-alpine` container and:

```text
WEEKEND_REPORT_TEST_POSTGRES_DISPOSABLE=1
```

Validated:

- duplicate active run prevention is atomic;
- only one worker can claim a created run.

The disposable PostgreSQL container was removed after the test.

## 9. Docker Results

Image build:

```text
PASS: docker build --no-cache -t weekend-report:python314 .
```

Final image ID:

```text
sha256:f122b1d4688b902e41a73581fb7b0bd6b9ec725e82df0a564442ddaebb61fe58
```

Python inside image:

```text
PASS: Python 3.14.7
```

Exact-image smoke:

```text
PASS: python scripts\ci.py image-smoke --image weekend-report:python314
```

Verified:

- PostgreSQL healthy;
- web running;
- worker running;
- `/healthz` returned OK;
- migration/database access succeeded;
- disposable containers, volumes, and networks were removed.

Image-ID invariant:

```text
PASS
weekend-report:python314
weekend-report:ci-6b731139296c
weekend-report:v1.0.1
```

all pointed to:

```text
sha256:f122b1d4688b902e41a73581fb7b0bd6b9ec725e82df0a564442ddaebb61fe58
```

Image cleanup:

- removed temporary `weekend-report:python314`;
- removed temporary `weekend-report:ci-6b731139296c`;
- removed temporary `weekend-report:v1.0.1`;
- no validation containers, volumes, or networks remained.

Pre-existing local images `weekend-report:ui-validation`, `weekend-report:local`, and
`weekend-report:validation` were not removed.

## 10. Security / Static Checks

Ruff:

```text
PASS: All checks passed
```

Mypy:

```text
PASS: Success: no issues found in 95 source files
```

pip-audit:

```text
PASS: No known vulnerabilities found
```

Bandit:

```text
NOT ADDED AS A RELEASE GATE
```

`bandit==1.8.6` is installed in `requirements.txt`, but it crashes internally under Python 3.14
with `AttributeError: module 'ast' has no attribute 'Num'`. Adding it to the shared release gates
now would create a broken blocker unrelated to project findings.

## 11. Documentation

Synchronized documents:

```text
README.md
deploy/docker/README.md
docs/ARCHITECTURE.md
docs/CI_CD.md
docs/CONFIGURATION_GUIDE.md
docs/DOCUMENTATION_INDEX.md
docs/ENVIRONMENT_INPUTS_REQUIRED.md
docs/PORTABLE_DEPLOYMENT.md
docs/PROJECT_BUILD_REPORT.md
docs/RECOVERY_POLICY.md
docs/VALIDATION_CATALOG.md
```

The documentation now consistently states:

- Python 3.14;
- root `TAG` is the release trigger/version source;
- normal source changes run quality gates only;
- `TAG` changes build and smoke the exact CI image;
- the same smoked image is version-tagged before export/publish;
- `docker load` produces `weekend-report:<TAG>`;
- production Compose uses `WEEKEND_REPORT_IMAGE`.

## 12. Remaining Recommendations

- Revisit Ruff target policy. `pyproject.toml` still has `target-version = "py311"` while Mypy
  and runtime are Python 3.14. No local document clearly states whether syntax compatibility
  should remain Python 3.11 or become Python 3.14-only.
- Separate runtime and development/CI dependencies in a future Docker optimization. The current
  image intentionally keeps test/quality tools because exact-image smoke depends on the current
  project layout.
- Recheck Bandit when a Python 3.14-compatible Bandit release/configuration is approved.
- Track the Starlette/FastAPI `httpx2` warning as a future dependency-compatibility upgrade.

## 13. Owner Decisions Still Required

Remaining controlled placeholders in deploy/config templates:

```text
<TBD>: 236
<TO_VERIFY>: 11
```

Files still containing unresolved production inputs:

```text
config/database.yml
config/doctor.yml
config/portainer_expected.yml
config/rabbitmq_expected.yml
config/recording.yml
config/rules.yml
config/servers.yml
config/sites.yml
config/splunk_dashboards.yml
.env.example
deploy/docker/env.example
```

Information still needed:

- production site IDs/display names/purposes;
- manager-approved rules, aggregation, approval, note, parity, and recovery policy;
- production auth provider and authorized reviewers;
- non-committed runtime secret mechanism and real values;
- Portainer URLs/auth/API contract/endpoint IDs/service expectations;
- RabbitMQ URLs/auth/topology/thresholds;
- Recording WebApp/backend/device selection/start/stop/cleanup/recovery contracts;
- approved database sync-function adapter contract;
- infrastructure server inventory/SSH/host-key/filesystem/NFS/Chrony expectations;
- DOCTOR manual/API mode and semantics;
- Splunk dashboard IDs/names/URLs/note requirements;
- production evidence retention, backup, archive, and distribution policy;
- GitLab Runner/DinD or equivalent execution details before relying on GitLab.

No real production integrations were enabled or contacted.

## 14. Final Verdict

Core project ready?

```text
YES, ready for owner-supplied production configuration.
```

Normal CI ready?

```text
YES, local shared gates are passing.
```

TAG release workflow ready?

```text
YES, local CI definitions and regression tests enforce TAG-file-driven exact-image delivery.
Hosted GitHub/GitLab execution still needs to be run by the owner after review.
```

Offline image ready?

```text
YES, final Docker build/smoke passed and the versioned-image invariant was verified locally.
No release artifact was exported because this was not a real release.
```

GitLab ready for later import?

```text
YES, definitions are present and cleaned of Git-tag release behavior.
Actual GitLab Runner execution is still pending.
```

Production integrations ready?

```text
NO, by design. They remain blocked until real approved configuration and secrets are supplied.
```
