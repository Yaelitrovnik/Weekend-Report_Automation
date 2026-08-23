# Architecture

**Documentation synchronized:** 2026-08-23

## 1. System Purpose

Weekend Report Automation is a manually triggered operational-validation system. FastAPI creates a run only after configuration preflight, a persistent worker atomically claims that run, collectors gather actual state and evidence, validators generate immutable automated findings, and a human reviewer completes the report in HTML before one final PDF is generated.

GitHub Actions and GitLab CI/CD are software-delivery systems only. They never replace the manual Weekend Report trigger.

## 2. Runtime Topology

```text
Reviewer
   |
   v
FastAPI web
   |
   +------ PostgreSQL
   |
   +------ Evidence store
   |
   v
Run record: CREATED
   |
   v
Persistent worker
   |
   v
Collectors
   |
   v
Validators
   |
   v
Evidence + Results
   |
   v
REVIEW_READY
   |
   v
HTML review / notes
   |
   v
APPROVE or REJECT
   |
   v
Frozen snapshot
   |
   v
Final PDF
```

The same application image is used for web and worker with different commands.

Current Python baseline:

```text
Python 3.14
Docker base: python:3.14-slim-bookworm pinned by digest
```

## 3. Runtime Security Boundary

- Health endpoints may remain public.
- In production, main pages, run pages, module/review pages, evidence endpoints, evidence downloads, recovery actions, and final-report access require authenticated/authorized access.
- Development mode may use local convenience identity.
- Production rejects arbitrary `X-Reviewer`.
- The implemented production identity boundary is configurable and currently supports `trusted_header` only behind an approved trusted reverse-proxy/authentication boundary.
- Browser mutations use reviewer-bound signed CSRF tokens generated with `WEEKEND_REPORT_CSRF_SIGNING_KEY`.
- One permanent public/shared CSRF token is not supported.

## 4. Run and Worker Model

Run states:

```text
CREATED
RUNNING
REVIEW_READY
APPROVED
REJECTED
FAILED
RECOVERY_REQUIRED
```

Run creation is protected by a database-backed singleton lock.

PostgreSQL uses explicit locking/atomic transitions. SQLite uses local transaction locking for safe fixture tests.

Any unresolved active/recovery run prevents unsafe concurrent execution.

The worker records:

- worker identity;
- heartbeat;
- current module;
- timestamps.

A stale Recording operation is never automatically replayed.

## 5. Collector / Validator Separation

```text
Collector
  -> obtains actual state
  -> stores/sanitizes raw evidence

Validator
  -> receives actual + expected
  -> produces PASS/WARNING/FAIL/ERROR/SKIPPED/MANUAL_REVIEW

Parity validator
  -> compares configured normalized fields only after site validation
```

Collectors do not decide business PASS/FAIL policy.

Validators do not invent actual state.

## 6. Evidence and Review

Evidence paths are stored relative to the configured evidence root.

Evidence must:

- remain under the run-owned evidence root;
- reject traversal/arbitrary paths;
- include SHA-256 metadata;
- exclude credentials/tokens;
- remain immutable for review.

Review notes can be scoped to:

- MODULE
- RESULT
- SPLUNK_DASHBOARD
- GENERAL

Reviewer notes never rewrite automated status.

The frozen review snapshot contains:

- run metadata;
- `application_version`;
- `build_id`;
- `configuration_hash`;
- optional Git commit;
- results;
- evidence references;
- site summaries;
- module summaries;
- parity summaries;
- all persisted reviewer notes;
- reviewer identity;
- decision;
- confirmation timestamp.

## 7. Module Boundaries

### 7.1 Portainer

Portainer integration is strictly read-only and limited to Docker Swarm Services.

```text
Weekend Report Worker
  |
  +-- HTTPS GET --> Portainer Site 1 Server/API --> Docker Swarm Services
  |
  `-- HTTPS GET --> Portainer Site 2 Server/API --> Docker Swarm Services
```

The application does not expose Portainer mutation operations.

Expected-state validation occurs independently per site before cross-site parity.

```text
Site 1 actual -> Site 1 expected-state validation
Site 2 actual -> Site 2 expected-state validation
                               |
                               v
                       configured parity
```

Both sites being identically wrong must still fail expected-state validation.

### 7.2 RabbitMQ

RabbitMQ expected state contains common vhosts/queues/exchanges/bindings, defaults, and per-site overrides.

Actual state comes from the RabbitMQ Management API or fixture actuals.

Required topology defaults to required unless explicitly marked optional.

### 7.3 Recording

Recording uses an **existing-device** start/stop workflow.

The application must not create/delete devices.

High-level flow:

1. collect WebApp/backend baselines;
2. select a suitable existing non-recording device;
3. start recording on that same device;
4. verify device/WebApp/backend expected transition;
5. stop the same device;
6. verify restoration;
7. verify cleanup.

Crash/unknown state after a state-changing action requires `RECOVERY_REQUIRED`.

### 7.4 Database

The database module is an adapter around the owner-supplied existing sync function.

Expected structured outcomes include:

- create success;
- replication after create;
- delete success;
- replication after delete;
- cleanup complete;
- errors.

The Weekend Report project does not silently replace the existing temp-table algorithm.

### 7.5 Infrastructure

Infrastructure collection is read-only.

Live SSH remains blocked until server inventory, authentication, host-key policy, and approved commands are supplied.

Validation covers:

- filesystem existence/utilization;
- NFS mapping/source/usability/utilization;
- Chrony synchronization/source/offset.

### 7.6 DOCTOR

DOCTOR supports:

```text
manual
api
```

API mode requires a verified endpoint/schema/auth/validation contract.

Manual mode remains a human-review finding.

### 7.7 Splunk

Splunk is a manual dashboard-review area.

Each configured dashboard can have:

- stable ID;
- display name;
- URL;
- required-review flag;
- note-required flag;
- display order.

All saved Splunk notes are frozen into the snapshot/final report.

## 8. Aggregation and Finalization

`config/rules.yml` is authoritative for:

- module enablement;
- module requiredness;
- unavailable status;
- aggregation;
- parity;
- note requirements;
- status-specific approval policy;
- rejection policy;
- recovery timeout.

Automated findings remain immutable.

Final confirmation writes:

```text
runs/<RUN_ID>/final/review_snapshot.json
runs/<RUN_ID>/final/weekend-report-<RUN_ID>.pdf
```

The PDF is rendered only from the frozen snapshot.

## 9. Portable Traceability

Mandatory runtime traceability:

- `application_version`
- `build_id`
- deterministic `configuration_hash`

Optional:

- Git commit SHA, when Git metadata is actually available.

Local-folder operation does not depend on Git metadata.

Release-image versioning uses the root `TAG` file.

Example:

```text
TAG = v1.0.1
```

The `v` prefix is preserved.

## 10. Software Delivery / CI Boundary

### Normal change

```text
source push / PR / MR
  -> config validation
  -> Ruff
  -> Mypy
  -> unit
  -> contract
  -> integration
  -> PostgreSQL concurrency
  -> safe fixture E2E
  -> dependency audit
  -> Compose validation
  -> quality PASS

NO release image merely because source changed.
```

### Release request

The root `TAG` file is the release trigger/version source.

```text
TAG changed on release/default branch
  -> pre-image quality gates run again
  -> all pass
  -> build exact image
  -> smoke exact image
  -> tag same image as weekend-report:<TAG>
  -> export archive + SHA-256
  -> optional registry publication
```

Neither GitHub nor GitLab should derive the application release version from a Git tag.

The CI regression suite checks this contract.

## 11. Build-Once / Test-Same-Image Principle

The release path follows:

```text
BUILD
  |
  v
TEST EXACT IMAGE
  |
  v
EXPORT / TAG / PUSH THAT SAME IMAGE
```

`deploy/docker/compose.ci.yml` intentionally contains no `build:` directive.

See `docs/CI_CD.md`.
