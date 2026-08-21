# CI Verification Status

**Documentation synchronized:** 2026-08-21

This tracks which CI/CD guarantees have actually been exercised on a hosted
runner versus verified only locally or via unit test. Update this file's
"Last confirmed" line whenever an item below is actually re-run, not when it
is assumed to still be true.

## 1. Purpose

`docs/PROJECT_BUILD_REPORT.md` (section 11) and `docs/CI_CD.md` (section 18)
both flag open verification debt: a hosted GitHub Actions run needs to be
repeated after the `.gitlab/ci/image.yml` `CI_COMMIT_TAG` fix, and GitLab CI
has never executed on a real Runner at all. This file makes that debt
trackable instead of implicit in prose buried in two other documents.

## 2. Local / static verification (current)

These are verified as of the latest local run and are NOT in question:

| Check | Status | Evidence |
|---|---|---|
| `.gitlab/ci/image.yml` does not contain `CI_COMMIT_TAG` | PASS | `tests/unit/test_ci_config.py::test_image_release_is_driven_by_tag_file` |
| `.github/workflows/build-image.yml` does not contain `GITHUB_REF_NAME` or `refs/tags/` | PASS | same test |
| Image version is read from root `TAG` in both platforms | PASS | same test |
| GitLab image job requires all quality jobs via `needs:` | PASS | `test_gitlab_image_needs_all_quality_jobs` |
| No production integration secrets referenced in CI definitions | PASS | `test_ci_files_do_not_contain_production_integration_secrets` |

These checks run on every `python scripts/ci.py unit` and locally in
`tests/unit/test_ci_config.py`. They protect against static regressions but
say nothing about hosted runner behavior.

## 3. Outstanding hosted verification (NOT yet re-confirmed)

### 3.1 GitHub Actions — quality-gates.yml, full hosted rerun

**Status: PENDING.** The build report notes a hosted GitHub Actions quality
run reached green *before* the final `.gitlab/ci/image.yml` TAG-only
correction, and that the first hosted pre-image run *after* adding the
TAG-only regression test correctly caught the stale `CI_COMMIT_TAG` logic —
which is good (the gate worked), but it means the corrected state has never
had a clean hosted pass recorded.

To close this:
1. Push the current `dev` branch (or open a PR) so `quality-gates.yml` runs
   on GitHub-hosted infrastructure.
2. Confirm every job listed in section 2 of `docs/CI_CD.md` §10.1 goes green,
   in particular `test_image_release_is_driven_by_tag_file` running hosted,
   not just locally.
3. Record the run URL and date below.

**Last confirmed:** not yet — no hosted run recorded since the GitLab
`CI_COMMIT_TAG` fix landed.

### 3.2 GitHub Actions — TAG-driven image pipeline, real exercise

**Status: PENDING.** `build-image.yml` triggers on a push to the release
branch that changes the `TAG` file. This design has never actually been
exercised end-to-end on GitHub's infrastructure.

To close this:
1. With 3.1 green, bump `TAG` (e.g. `v1.0.1` → `v1.0.2`) on the configured
   release branch.
2. Confirm quality gates re-run, then build → smoke → export archive +
   `.sha256` → (if enabled) registry publish, in that order.
3. Confirm a *normal* commit that does **not** touch `TAG` does not trigger
   `build-image.yml` at all.
4. Record the run URL, image digest, and date below.

**Last confirmed:** never exercised.

### 3.3 GitLab CI — first real Runner execution

**Status: PENDING — never run.** Per `docs/PROJECT_BUILD_REPORT.md` §12 and
§15, GitLab pipeline definitions are "DEFINED / NOT YET RUN ON A REAL GITLAB
RUNNER." This is the largest open item: the GitLab quality and image jobs
have only ever been validated by reading YAML, never by execution.

To close this, after the project is imported to a real GitLab instance:
1. Confirm a Docker-capable Runner (Docker-in-Docker per `docs/CI_CD.md`
   §11.3) is available.
2. Run the quality stage and confirm every job in `.gitlab/ci/quality.yml`
   passes, including `postgres-concurrency` against GitLab's disposable
   PostgreSQL service.
3. Confirm the image job's `rules:` correctly fires only on a `TAG` change
   to the default branch, and does **not** fire on unrelated commits.
4. Confirm the image job derives its version from the `TAG` file at runtime,
   not from any Git ref.
5. Record the run URL and date below.

**Last confirmed:** never — no GitLab Runner has executed this project.

## 4. New since the last hosted verification

- `requirements.txt` now includes `pydantic==2.11.7` (added by the typed
  config validation work). `pip-audit` has been run locally
  (`scripts/ci.py audit`) but this dependency has not yet been through a
  hosted `dependency-audit` job. Include it explicitly when re-running 3.1.

## 5. Update discipline

When any item in section 3 is actually re-run and confirmed:
- Change its status from PENDING to CONFIRMED.
- Add the run URL/date under "Last confirmed."
- Do not mark an item CONFIRMED based on a prior run before the fix it's
  meant to verify — that's the exact mistake this file exists to prevent.