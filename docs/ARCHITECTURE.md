# Architecture

**Documentation synchronized:** 2026-09-06

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

- Weekend Report does not implement application user login, local accounts, trusted identity headers, or an authorized-reviewer list.
- Read-only application pages, evidence endpoints, and final-report downloads are reachable without an application login. Network exposure must therefore be controlled by the deployment environment as required.
- Browser mutations use signed CSRF tokens generated with `WEEKEND_REPORT_CSRF_SIGNING_KEY` when that key is configured. Production preflight requires the key.
- The reviewer name is not an authenticated identity. It is entered manually only during final confirmation and is written to the reviewer confirmation section at the bottom of the final report.
- Reviewer notes remain operational review data; they are not identity-bearing authentication records.

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

The Docker image contains code only. Deployment values, endpoints, site inventory, and secrets are
provided through external ENV/secrets. `rules.yml` is the read-only policy file.

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

Services and tasks are discovered dynamically. Independent site-health validation occurs before
cross-site parity.

```text
Site 1 actual -> Site 1 health validation
Site 2 actual -> Site 2 health validation
                               |
                               v
                       configured parity
```

Both sites being identically wrong must still fail independent site-health validation.

### 7.2 RabbitMQ

RabbitMQ runtime ENV contains Management API connection values and required sites. `rules.yml`
contains all-queue zero-count policy, queue recheck policy, and all-node resource-state policy.

Actual state comes from the RabbitMQ Management API or fixture actuals.

The validator checks observed queue ready/unacked/total counts and node resource states. It does not validate the removed vhost/exchange/binding topology contract.

### 7.3 Recording

Recording uses an **existing-device** start/stop workflow.

The application must not create/delete devices.

High-level flow:

1. select and verify a suitable existing non-recording device in the Manager WebApp;
2. collect four runtime baselines from Site 1 WebApp, Site 2 WebApp, Site 1 server, and Site 2 server;
3. start recording on that same device through the Manager WebApp;
4. verify all four observations increased by the configured delta;
5. stop the same device through the Manager WebApp;
6. verify all four observations returned to baseline;
7. verify cleanup.

Crash/unknown state after a state-changing action requires `RECOVERY_REQUIRED`.

### 7.4 Infrastructure

Infrastructure collection is read-only.

Live SSH remains blocked until server inventory, authentication, host-key policy, and approved commands are supplied.

Validation covers:

- filesystem existence/utilization;
- Chrony synchronization/source/offset.

### 7.5 DOCTOR

DOCTOR API mode uses dynamic service discovery per site. Discovered services are validated independently for health and the two site service sets are compared for parity.

API mode requires a verified endpoint/schema/auth/validation contract. Reviewable service-health issues remain service-level `ERROR` findings and roll the module to `MANUAL_REVIEW`; transport/API/authentication/timeout/schema/collection errors remain blocking `ERROR`s.

### 7.6 Splunk

Splunk is a manual dashboard-review area.

Each configured dashboard can have:

- stable ID;
- display name;
- URL;
- required-review flag;
- note-required flag;
- display order.

All saved Splunk notes are frozen into the snapshot/final report.

Opening a dashboard URL is not review evidence by itself. Required review is satisfied only by a persisted dashboard review acknowledgment, with a separate note requirement when configured.

## 8. Aggregation and Finalization

`deploy/docker/config/rules.yml` is the single authoritative runtime policy source for:

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
TAG = v1.0.2
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
