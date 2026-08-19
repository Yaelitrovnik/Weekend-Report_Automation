# Portable Deployment

**Documentation synchronized:** 2026-08-19

The project is portable as a complete `weekend-report` folder and/or as a verified Docker image artifact produced by CI.

Current development/delivery model:

```text
Development PC / GitHub
        |
        v
validated source folder
        |
        +--> normal changes: quality gates only
        |
        `--> TAG change: verified image artifact
        |
        v
external hard disk
        |
        v
target PC
        |
        +--> optional GitLab import
        |
        v
production secrets/config
        |
        v
controlled acceptance
        |
        v
production operation
```

GitHub/GitLab are not runtime dependencies.

## 1. Target-PC Prerequisites

Required for container deployment:

- Docker Engine;
- Docker Compose plugin;
- sufficient disk;
- production network access;
- approved reverse proxy/auth boundary if `trusted_header` is used;
- persistent PostgreSQL storage;
- persistent evidence storage;
- approved certificates/keys supplied separately.

Python on the host is not required to run the containerized application.

If local validation scripts are also run on the target PC, use Python 3.14.

## 2. Current Runtime Baseline

```text
Application Python: 3.14
Validated container: Python 3.14.7
Base image: python:3.14-slim-bookworm
PostgreSQL: 16-alpine
```

## 3. Transfer the Source Folder

Transfer:

```text
TAG
app/
config/
deploy/
docs/
scripts/
tests/
.github/
.gitlab/
.gitlab-ci.yml
.env.example
.gitignore
.dockerignore
Dockerfile
pyproject.toml
requirements.txt
README.md
```

If repository metadata exists and the organizational process wants to preserve it, `.git/` may be transferred separately. Runtime does not require it.

## 4. Do Not Transfer Generated Development State

Do not transfer as application content:

```text
.venv/
.mypy_cache/
.ruff_cache/
.pytest_cache/
__pycache__/
*.pyc
local SQLite data
temporary runs/
temporary CI artifacts
```

Do not place real secrets in the source folder.

`.venv` is machine-specific and should be recreated only if local Python execution is needed.

## 5. TAG File

Root `TAG` is part of the project and must be transferred.

Example:

```text
v1.0.1
```

It is:

- software release/version metadata;
- CI image-build trigger when changed on the release/default branch;
- not a secret;
- not an environment configuration file.

Do not replace it with a Git tag as the only release-version source.

## 6. Preferred Verified-Image Transfer

When the CI image path is approved:

```text
TAG change
  -> pre-image gates PASS
  -> build
  -> exact-image smoke PASS
  -> docker save archive
  -> SHA-256
  -> external hard disk
  -> target PC
  -> checksum verification
  -> docker load
```

Expected artifacts:

```text
weekend-report_<version>_<short-sha>.tar.gz
weekend-report_<version>_<short-sha>.tar.gz.sha256
image-id.txt
```

Example:

```text
weekend-report_v1.0.1_abc123def456.tar.gz
```

Verify:

```powershell
Get-FileHash .\weekend-report_v1.0.1_<short-sha>.tar.gz -Algorithm SHA256
```

Then load:

```powershell
docker load -i .\weekend-report_v1.0.1_<short-sha>.tar.gz
```

If the target Docker version requires an uncompressed TAR, decompress first.

## 7. Alternative: Build on Target PC

If policy requires rebuilding:

```powershell
docker build --no-cache -t weekend-report:<TAG-version> .
```

Before production use, reproduce the appropriate local quality gates and record a new build ID.

Do not assume a target-PC rebuild is identical to the already-verified CI image unless its digest is proven identical.

## 8. Secrets and Runtime Values

Create real secrets separately:

- `.env` not committed;
- Docker secrets;
- approved secret manager.

Production values include:

```text
POSTGRES_PASSWORD
WEEKEND_REPORT_APP_VERSION
WEEKEND_REPORT_BUILD_ID
WEEKEND_REPORT_AUTH_MODE=production
WEEKEND_REPORT_AUTH_PROVIDER
WEEKEND_REPORT_AUTHORIZED_REVIEWERS
WEEKEND_REPORT_CSRF_SIGNING_KEY
integration-specific credentials
```

If a CI-built image uses `TAG=v1.0.1`, production `WEEKEND_REPORT_APP_VERSION` should correspond to that software version unless there is a documented packaging policy that says otherwise.

## 9. Production Configuration

Complete `config/*.yml` with verified values.

Unknown required values remain placeholders and block production.

Pre-production check:

```powershell
python scripts/validate_config.py --config config
```

Only run this as a production-ready check after the YAML has actually been completed.

## 10. Docker Compose

Validate:

```powershell
docker compose -f deploy/docker/compose.yml config
```

Start:

```powershell
docker compose -f deploy/docker/compose.yml up -d
```

Confirm:

- PostgreSQL healthy;
- web running;
- worker running;
- `/healthz` OK;
- protected pages reject unauthenticated production access;
- approved reviewer identity works;
- evidence volume persists;
- database volume persists.

## 11. Controlled Acceptance

Before production:

- verify traceability fields;
- verify configuration hash;
- verify production auth;
- verify CSRF;
- verify run lock;
- verify review notes;
- verify snapshot/PDF;
- verify enabled integrations one by one;
- keep Recording disabled until explicitly approved.

## 12. Evidence and Backups

Back up PostgreSQL and evidence as one logical dataset.

Recommended backup set:

- PostgreSQL dump/volume snapshot;
- evidence storage;
- effective `config/`;
- final PDFs/snapshots where required;
- deployed `.env` through the approved secret backup process, not in source control.

## 13. Upgrade

Preferred:

1. complete/test code change;
2. normal quality pipeline green;
3. bump `TAG`;
4. release-image pipeline green;
5. transfer verified archive/checksum;
6. back up production;
7. load new image;
8. update runtime app version/build ID;
9. restart;
10. acceptance test.

## 14. Rollback

Preserve:

- previous verified image/archive;
- previous effective config;
- previous database/evidence backup;
- previous secret set;
- previous build ID/version.

Rollback must not mix a previous image with incompatible database/config state without verification.

## 15. Future GitLab Onboarding

After transfer to the second PC, the project may be imported to GitLab.

Keep:

```text
.gitlab-ci.yml
.gitlab/ci/quality.yml
.gitlab/ci/image.yml
```

GitLab must preserve the same release rule:

```text
normal changes -> quality only
TAG changed on default branch -> gated image job
```

Do not reintroduce `CI_COMMIT_TAG` as the release-version source.

## 16. Troubleshooting

### Config preflight fails

Expected while production values are unresolved.

### `.venv` missing

Normal for deployment. Containers do not need the host virtual environment.

### Production page returns 401/403

Verify auth provider, reverse proxy/trusted header, and reviewer authorization.

### Mutation returns 403

Reload the page to obtain a new reviewer-bound CSRF token and verify signing configuration.

### New run blocked by `RECOVERY_REQUIRED`

Resolve the previous Recording recovery safely before starting another run.

### Image release did not start

Confirm:

- the root `TAG` file actually changed in the pushed commit;
- the change was on the configured default/release branch;
- the GitHub/GitLab image workflow uses a path/rules change filter for `TAG`.

A normal source-only commit should not start the release-image workflow.
