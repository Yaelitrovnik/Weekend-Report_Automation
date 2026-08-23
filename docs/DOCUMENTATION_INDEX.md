# Documentation Index

**Synchronized:** 2026-08-23

Read in this order:

1. `README.md` — project overview and current operating model.
2. `docs/ARCHITECTURE.md` — runtime, module, security, review, and CI boundaries.
3. `docs/CI_CD.md` — Python 3.14 quality gates and TAG-driven verified-image delivery.
4. `docs/CONFIGURATION_GUIDE.md` — how YAML, secrets, traceability, and module configuration work.
5. `docs/ENVIRONMENT_INPUTS_REQUIRED.md` — private/manager/environment values still required.
6. `docs/VALIDATION_CATALOG.md` — exact validation semantics.
7. `docs/RECOVERY_POLICY.md` — stale worker / Recording recovery.
8. `docs/PORTABLE_DEPLOYMENT.md` — hard-disk transfer, verified-image transfer, target-PC/GitLab deployment.
9. `docs/PROJECT_BUILD_REPORT.md` — current implementation and verification status.

Current key conventions:

```text
Python runtime: Python 3.14
Release trigger/version: root TAG file
Normal commit: quality gates only
TAG change: quality gates -> build -> exact-image smoke -> verified artifact
Verified artifact loads as: weekend-report:<TAG>
Production run: manual FastAPI action only
```
