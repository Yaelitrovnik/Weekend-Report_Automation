# Weekend Report Automation
## Technical Handover & Continuation Guide

**Project version:** v1.5.3
**Handover date:** November 12, 2026
**Project status:** Deployed and operational, but production validation is incomplete
**Intended audience:** DevOps engineer / system administrator taking ownership of the project

---

# 1. Executive Summary

Weekend Report Automation is a web-based operational health-check system designed to automate the collection and validation of infrastructure and application health information.

Its primary purpose is to replace a largely manual operational review with a standardized, repeatable and evidence-backed process.

The system checks Docker Swarm services through Portainer, service health through DOCTOR, RabbitMQ queues and resources, Linux server infrastructure, and selected manual review activities.

An operator starts a report manually, examines the collected results, adds review notes, completes a manual database synchronization check, and explicitly approves or rejects the report.

The final result is a reviewable PDF containing the findings and the operator's decision.

**The report is intentionally not scheduled.** It runs only when a user explicitly starts it.

### Current position

Version **v1.5.3** has been deployed and is producing live results.

The Portainer integration is now working successfully. Several remaining findings require attention, particularly DOCTOR certificate verification, RabbitMQ socket metrics and infrastructure validation.

The system must not yet be considered fully accepted for production use.

---

# 2. How the Automation Works

## 2.1 Overall workflow

The application follows this lifecycle:

```text
Operator opens Weekend Report
              |
              v
       RUN WEEKEND REPORT
              |
              v
      Configuration validation
              |
              v
       Run created in DB
              |
              v
      Background worker claims run
              |
              v
     Collect information from:
       - Portainer
       - DOCTOR
       - RabbitMQ
       - Infrastructure
              |
              v
     Validate collected information
              |
              v
       Store evidence and results
              |
              v
         REVIEW_READY
              |
              v
      Operator reviews findings
       - Adds notes
       - Reviews Splunk dashboards
       - Records manual DB result
              |
              v
       APPROVE or REJECT
              |
              v
      Freeze review snapshot
              |
              v
        Generate final PDF
```

### Important operational principles

- The system collects actual monitoring information rather than simply asking an operator whether a component is healthy.
- Automated results and evidence are preserved. An operator's note does not change a technical finding from FAIL to PASS.
- Some issues require human review and acknowledgment.
- A final report requires an explicit reviewer decision.
- Historical runs and their results remain stored for later inspection.

## 2.2 Result status definitions

| Status | Meaning |
|---|---|
| PASS | The measured condition satisfies its expected policy |
| WARNING | A condition requires attention but is not necessarily a hard failure |
| FAIL | A measured condition violates an expected requirement |
| ERROR | A technical problem prevents reliable collection or validation, or the module defines a technical resource issue |
| MANUAL_REVIEW | A finding requires explicit human acknowledgment |
| SKIPPED | A check or module was not executed |

These statuses are significant.

For example, a monitoring API returning an invalid value must not automatically become PASS just because the monitored service appears to be working.

---

# 3. Technical Architecture

## 3.1 Main application components

The solution runs as a Docker Compose stack.

| Component | Technology | Responsibility |
|---|---|---|
| Web | Python 3.14 / FastAPI | User interface, REST API, notes, decisions, report downloads |
| Worker | Python 3.14 | Runs health-check collectors, validators and evidence processing |
| Database | PostgreSQL 16 | Stores runs, findings, review information, decisions and execution state |
| Evidence storage | Persistent Docker volume | Stores collected evidence, final snapshots and PDF reports |
| Configuration | ENV files and YAML | Defines connection settings, runtime inventory and validation rules |

The **web** and **worker** containers use the same Weekend Report Docker image but execute different commands.

The worker performs the actual collection. The web application manages the operator-facing workflow.

## 3.2 Important runtime data

The main persistent storage is:

- PostgreSQL database volume: `db-data`
- Evidence volume: `evidence`

The database and evidence should be treated as one logical application dataset for backup and recovery.

Never run `docker compose down -v` against this deployment unless intentionally deleting its persistent data is explicitly authorized.

## 3.3 Application access and security

The application has **no built-in login or user-account system**.

Access must be controlled through the existing network, firewall, reverse-proxy or other external access-control mechanism.

Browser mutations use CSRF protection when configured. The reviewer name is manually entered and provides attribution; it is not an authenticated identity.

This behavior is intentional in the current version. Do not introduce authentication changes as part of ordinary troubleshooting without an approved requirement.

---

# 4. What Each Module Does

## 4.1 Portainer — Docker Swarm Health

Portainer is used to discover and inspect Docker Swarm services.

The collector obtains service and task information through the Portainer API.

It checks service inventory, expected and running replicas, service/task states, available health evidence and image information.

Version v1.5.3 includes support for both:

- **Replicated services:** Desired number of replicas versus currently running replicas.
- **Global services:** Expected service instances based on eligible Swarm nodes.

For multi-site deployments, the application can also compare service state and inventory across sites.

**Important:** Two sites being equally unhealthy must not result in a healthy parity result.

Portainer performs read-only queries; it does not redeploy or restart services.

## 4.2 DOCTOR — Microservice Health

DOCTOR is an existing health-check system accessed through its backend API.

Weekend Report queries DOCTOR, discovers services and evaluates their reported health.

It is designed to distinguish actual unhealthy services from technical collection failures.

Examples:

- Healthy services can produce PASS.
- Unhealthy services retain their error evidence and can require manual review.
- TLS, authentication, connection or response-format problems produce technical ERROR findings.

The DOCTOR URL must point to a working backend API endpoint, not merely the browser's health-check page.

The real API response schema and expected production service inventory must also be verified.

## 4.3 RabbitMQ — Queues and Node Resources

RabbitMQ collection uses its Management HTTP API.

The collector performs read-only requests to:

```text
GET /api/queues
GET /api/nodes
```

### Queue checks

Every discovered queue is expected to have:

```text
Ready   = 0
Unacked = 0
Total   = 0
```

When messages remain, the collector performs up to three additional checks according to the configured policy.

Persistent nonzero queue counts result in ERROR.

### Node resource checks

The module evaluates:

| Resource | Expected condition |
|---|---|
| File Descriptors | Green |
| Socket Descriptors | Green |
| Erlang Processes | Green |
| Disk Space | Green |

The application uses RabbitMQ API measurements to determine these states.

Missing or invalid data must not be interpreted as healthy.

## 4.4 Infrastructure — Linux Server Health

The Infrastructure module accesses configured Linux servers over SSH.

It uses strict SSH host-key verification.

### Filesystem checks

The required command is:

```bash
df -h | grep mapper
```

All matching mapper-backed filesystems are checked independently.

Thresholds:

| Usage | Result |
|---|---|
| Below 70% | PASS |
| 70% to below 80% | WARNING |
| 80% or above | FAIL |

Missing required mapper filesystems and technical collection problems are reported according to their specific validation rules.

**Do not replace this check with `df -h /`.** The requirement is to inspect all matching mapper filesystems, not just the root mount.

### Chrony/NTP checks

The system also examines:

- Configured and observed time sources
- Synchronization status
- Local-clock fallback
- Clock offset
- Timezone
- Time-source availability and uncertainty

These results must be interpreted carefully, especially in an isolated network where internal servers may act as time sources.

## 4.5 Splunk — Manual Dashboard Review

Splunk is not automatically queried for dashboard health.

Instead, Weekend Report can provide configured links to dashboards that the reviewer must inspect.

Each dashboard may require explicit acknowledgment and optionally a note.

**Opening a Splunk dashboard does not automatically count as reviewing it.**

## 4.6 Manual Database Synchronization Review

The database replication check relies on an existing Windows PowerShell script.

That script is executed separately on the reviewer's authorized Windows workstation, not by the Weekend Report server.

Weekend Report displays the script's configured location as read-only information.

After executing the script, the reviewer records:

- PASS
- FAIL
- NOT RUN

A comment is required.

The application does not execute the PowerShell script, and its previous nonworking script-launch/copy controls were removed.

## 4.7 Recording Synthetic Test

Recording synthetic-test automation was part of the broader project concept, but **it is not implemented as an operational module in the current v1.5.3 repository**.

Do not assume that recording start/stop tests are automatically performed.

Implementing this feature would be a separate, explicitly approved development task.

---

# 5. Report Review, Approval and PDF

After collection, the run enters `REVIEW_READY`.

The operator can inspect individual findings, review evidence and add notes.

Approval and rejection follow different validation rules.

### APPROVE

Approval is restricted by the configured validation and review policy.

Technical FAIL and ERROR findings are blocking under the current rules.

Warnings require appropriate review notes.

The required manual database result, dashboard acknowledgments and reviewer information must also satisfy their respective requirements.

### REJECT

A report can be intentionally rejected even when its manual database check reports PASS.

Rejection requires the necessary review inputs, a reviewer name, and a reason.

The existing `reject_allowed` policy remains in force.

### Final PDF

Finalization freezes the results and review information into an immutable snapshot and generates a PDF from that snapshot.

The PDF includes operational findings, relevant evidence, notes, review decision, reviewer and traceability information.

A PDF rendering failure should preserve the frozen snapshot and permit a controlled retry without changing the original decision.

These workflows have automated test coverage, but **complete live browser acceptance testing remains necessary**.

---

# 6. Current Issues and Troubleshooting History

## 6.1 Portainer — Working

**Current result: PASS**

The Portainer collector originally had difficulty interpreting certain Docker Swarm service states and replica counts, particularly Global services.

Version v1.5.3 introduced changes to distinguish Global and Replicated services and avoid fabricating unavailable metrics.

The latest production run reports:

```text
Portainer
126 checks
PASS
```

This is an important completed milestone.

No current Portainer failure is shown in the supplied report.

Further changes should not be made to this collector without a reproduced problem.

## 6.2 DOCTOR — TLS Certificate Validation

**Current displayed result: ERROR**

The observed error is:

```text
DOCTOR_TLS_ERROR:
DOCTOR TLS/certificate verification failed
```

The endpoint in the report is:

```text
https://92.103.1.9/doctor-api
```

### What has already been done

Initial OpenSSL testing reported:

```text
unable to get local issuer certificate
```

A later test using the RHEL system CA bundle returned:

```text
Verify return code: 0 (ok)
```

The working CA bundle was:

```text
/etc/pki/tls/certs/ca-bundle.crt
```

The previous engineer subsequently reported:

- Obtaining/copying the appropriate certificate bundle.
- Updating the DOCTOR environment configuration.

The application Compose configuration already mounts the `secrets` directory into the worker and web containers as read-only.

### What is still unverified

The latest displayed run still shows `DOCTOR_TLS_ERROR`.

It has not yet been established whether that run occurred after the updated environment variables were loaded by a recreated worker.

It is also not yet confirmed that the actual API endpoint and response schema work successfully after TLS verification.

### Required continuation

1. Confirm the configured CA file exists inside the running worker container.
2. Confirm the worker's actual DOCTOR TLS and CA path settings match the intended values.
3. Ensure the worker was recreated after changing the environment file, using the existing Compose project and deployment mode.
4. Test HTTPS certificate validation from inside the worker.
5. Confirm the endpoint returns an expected HTTP response and the application's expected JSON structure.
6. Start a fresh Weekend Report run and verify that DOCTOR discovers its services.

**Do not disable TLS certificate verification.**

A successful `openssl` test on the RHEL host is not, by itself, proof that Python inside the worker has the required trust configuration.

## 6.3 RabbitMQ — Socket Descriptor Metric

**Current displayed result: ERROR**

The relevant finding is:

```text
RABBITMQ_NODE_METRIC_UNAVAILABLE:
Socket Descriptors capacity is zero;
utilization cannot be calculated
```

The result shows the normalized socket state as unavailable.

### What has already been verified

The RabbitMQ Management API was previously observed reporting:

```text
sockets_used  = 0
sockets_total = 0
```

However, `rabbitmq-diagnostics status` on a RabbitMQ container returned:

```text
File Descriptors:
Total:   4
Limit:   1048479

Socket Descriptors:
Used:    2
Limit:   943629
```

This proves that **the RabbitMQ node examined through the CLI has a valid, nonzero socket capacity**.

It does not prove that this is the same node referenced by Weekend Report.

The previous engineer reported that the identifiers or results being compared were not the same. That discrepancy remains unresolved.

### Why Weekend Report reports ERROR

The current collector calculates socket health from `sockets_used` and `sockets_total` returned by `/api/nodes`.

A zero or unavailable total cannot produce a meaningful utilization percentage.

The validator therefore reports ERROR rather than assuming 0% usage and PASS.

That is the intended safety behavior.

### Required continuation

First establish which RabbitMQ node Weekend Report is actually checking.

Run inside the relevant RabbitMQ container:

```bash
rabbitmqctl eval 'node().'
```

Compare the returned node name against the `name` field of each entry in:

```text
GET /api/nodes
```

Use the same RabbitMQ environment and endpoint configured for the `sadab` site.

Compare `sockets_used`, `sockets_total`, `fd_used` and `fd_total` for the **same node**.

If the node names differ, repeat the CLI diagnostic on the node corresponding to the API record.

If the node names match but the API still reports `0/0`, investigate RabbitMQ Management API statistics and resource reporting.

Only after the actual cause is established should a change to the RabbitMQ collector or validation policy be considered.

**Do not:**

- Treat `0/0` as 0% utilization.
- Mark the resource PASS without valid evidence.
- Restart RabbitMQ solely because the monitoring API returns a zero capacity.
- Change ERROR to WARNING without an explicit monitoring-policy decision.

The preferred outcome is to obtain trustworthy resource information and retain a meaningful health check.

## 6.4 Infrastructure — Timezone Comparison

A previously observed failure showed:

```text
Expected: UTC
Actual:   Etc/UTC
```

These are equivalent UTC timezone identifiers.

The previous engineer reported adding the necessary accepted alias to the deployed `rules.yml`.

Expected policy:

```yaml
infrastructure:
  chrony:
    timezone_aliases:
      - Etc/UTC
```

This is a configuration change, not a Docker image code change.

**Remaining verification:** Run the collector with the updated configuration and confirm the timezone finding is no longer incorrectly marked FAIL.

The latest summary still shows one Infrastructure FAIL. Its precise cause must be identified from the corresponding result before assuming the timezone issue remains.

## 6.5 Infrastructure — Chrony Local Reference

Previous investigation found that several servers show local-clock reference behavior.

Observed information included:

```text
DC-1 selected as an NTP source
DC-2 unreachable in the observed sample
Local reference behavior present
```

DC-1 was previously observed advertising a local reference and relatively high root dispersion.

A small measured clock offset does not establish a trustworthy upstream time reference.

The current policy reports local-reference behavior as WARNING.

This should remain visible until the NTP design is understood and approved.

The next engineer should establish whether the isolated environment intentionally treats DC-1 as its authoritative local time source or expects it to synchronize with another reference.

Do not repeatedly troubleshoot individual servers before understanding the shared NTP architecture.

## 6.6 Splunk — No Checks Displayed

The latest screenshot shows:

```text
Splunk
0 checks
SKIPPED
```

This is a separate acceptance gap.

The next engineer must inspect the deployed Splunk configuration to determine whether dashboard review is intentionally unavailable, disabled, or missing required configuration.

No conclusion about Splunk health can be drawn from zero checks.

## 6.7 Outstanding Warnings and Infrastructure Failure

The latest summary reports:

```text
10 WARNING
1 FAIL
```

The screenshots provided do not show the complete identities and details of these findings.

The next engineer must open the current run, identify every non-PASS result, and record its cause and disposition.

No warning should be silently ignored or altered merely to obtain a green dashboard.

---

# 7. Important Project Files and Directories

## 7.1 Development repository

The project contains the following important components:

```text
Weekend-Report_automation/
|
|-- app/
|   |-- api/             REST API routes
|   |-- collectors/      Data collection
|   |-- validators/      Expected vs actual evaluation
|   |-- config/          Runtime configuration handling
|   |-- database/        DB access and migrations
|   |-- orchestrator/    Run coordination and execution
|   |-- reporting/       Snapshot, HTML and PDF generation
|   |-- review/          Notes and finalization logic
|   |-- web/             FastAPI pages and frontend
|   |-- worker/          Background execution
|
|-- deploy/docker/
|   |-- compose.yml
|   |-- compose.proxy.yml
|   |-- compose.direct.yml
|   |-- config/rules.yml
|   |-- env/*.env.example
|
|-- scripts/
|   |-- ci.py
|   |-- migrate.py
|   |-- validate_config.py
|   |-- deployment/
|
|-- tests/
|-- docs/
|-- Dockerfile
|-- Dockerfile.ci
|-- Dockerfile.runtime-base
|-- requirements.txt
|-- requirements.runtime.txt
|-- TAG
```

## 7.2 Runtime configuration

The known RHEL deployment location is:

```text
/mnt/Weekend-Report
```

The source project expects Docker deployment files under:

```text
deploy/docker/
```

Confirm the actual locations and Compose working directory in the running installation before executing commands.

Key files include:

| File | Purpose |
|---|---|
| `.env` | Compose project and application image selection |
| `env/app.env` | Database, application identity, CSRF and manual DB review |
| `env/portainer.env` | Portainer endpoints and credentials |
| `env/doctor.env` | DOCTOR endpoints, credentials and certificate settings |
| `env/rabbitmq.env` | RabbitMQ API access |
| `env/infrastructure.env` | Sites, server inventory, SSH and NTP expectations |
| `env/splunk.env` | Dashboard inventory and review settings |
| `config/rules.yml` | Business and monitoring validation policy |
| `secrets/` | Mounted certificate and other approved secret files |

The deployed environment files are not supposed to be committed to Git.

## 7.3 Existing documentation

Read these files in the repository before making substantial changes:

- `README.md` — operating model and overview.
- `docs/ARCHITECTURE.md` — component responsibilities and lifecycle.
- `docs/CONFIGURATION.md` — deployment settings and validation policies.
- `docs/DEPLOYMENT.md` — installation, backup and upgrade process.
- `docs/RECOVERY_POLICY.md` — failed runs and worker recovery.
- `docs/RELEASE_v1.5.3.md` — v1.5.3 changes and release considerations.
- `docs/VALIDATION_v1.5.3.md` — automated tests and known verification gaps.

Some documentation describes the pre-deployment state at the time it was written. The running configuration and current report results are the authoritative evidence for the deployed environment.

---

# 8. How to Inspect the Running System

First identify the exact existing Compose project and deployment mode. The repository supports reverse-proxy mode and direct HTTPS mode.

Do not accidentally create a new Compose project, since doing so can attach different database and evidence volumes.

From the actual production Compose directory, use the same Compose arguments as the current deployment.

For example, in reverse-proxy mode:

```bash
docker compose \
  --env-file .env \
  -f compose.yml \
  -f compose.direct.yml \
  ps
```

Inspect logs:

```bash
docker compose \
  --env-file .env \
  -f compose.yml \
  -f compose.direct.yml \
  logs --tail=100 web worker
```

View the effective configuration without sharing secrets:

```bash
docker compose \
  --env-file .env \
  -f compose.yml \
  config
```

Be aware that the rendered configuration can contain sensitive environment values. Do not paste the full output into tickets or chats without redaction.

For any troubleshooting task, collect:

1. The run ID and timestamp.
2. The actual failing check and module.
3. Expected and actual values.
4. Technical details or sanitized raw evidence.
5. The deployed image version/build ID.
6. The relevant configuration policy.
6. Results of a fresh run after any correction.

This distinguishes a genuine application defect from an endpoint problem, configuration problem or unavailable monitoring measurement.

---

# 9. Prioritized Continuation Plan

## Priority 1 — Establish a reproducible baseline

Before modifying the application:

- Record the most recent run ID and results.
- Confirm the exact deployed image and build ID.
- Confirm whether only `sadab` is currently configured or whether additional sites are expected.
- Identify the active Compose project, deployment mode and configuration directory.
- Preserve the current database, evidence, ENV files and `rules.yml`.

Expected result: a known and recoverable starting state.

## Priority 2 — Finish DOCTOR validation

The certificate bundle and environment file have reportedly been updated.

Complete the remaining checks:

- Verify the CA file exists inside the worker.
- Verify the worker loaded the updated environment.
- Confirm HTTPS validation succeeds inside the worker.
- Confirm the backend returns the expected JSON.
- Confirm discovered services and their health results appear correctly.
- Execute a new report and verify that `DOCTOR_TLS_ERROR` disappears.

Do not assume the issue is resolved based only on an OpenSSL test from RHEL.

## Priority 3 — Resolve RabbitMQ socket reporting

This is the primary unresolved technical investigation.

- Identify the exact RabbitMQ node referenced by Weekend Report.
- Compare it against the node checked with `rabbitmq-diagnostics`.
- Compare Management API and CLI values for that same node.
- Determine why the Management API reports unavailable/zero socket capacity.
- Fix the actual metric source or apply a separately approved validation-policy change.
- Rerun the RabbitMQ check.

Retain the requirement that actual resource health must be measured reliably.

## Priority 4 — Verify Infrastructure findings

- Confirm the `Etc/UTC` alias is loaded from the deployed `rules.yml`.
- Run a new report to check whether the timezone false failure is gone.
- Identify the exact remaining Infrastructure FAIL.
- Document all infrastructure warnings and their real causes.
- Confirm the intended internal NTP architecture before changing the Chrony local-reference policy.

## Priority 5 — Restore or validate Splunk review

- Inspect `env/splunk.env`.
- Confirm the expected dashboard count, URLs and review requirements.
- Determine why the current run shows zero Splunk checks.
- Validate the manual acknowledgment and note workflow.

## Priority 6 — Validate the complete review lifecycle

After collectors are understood, perform a controlled end-to-end test.

Check that an operator can:

- Start a run manually.
- Follow progress.
- Review module findings and evidence.
- Add and save notes.
- Execute the separate Windows database synchronization script.
- Record the manual database result with a comment.
- REJECT a report with a reason, even if the DB result is PASS.
- Verify that APPROVE remains blocked by unresolved technical failures.
- Generate and download the final PDF.
- Verify the report contains the correct results, notes, reviewer and decision.
- Confirm that results cannot be altered after finalization.

Use a nonproduction or controlled acceptance environment where appropriate, especially when testing database state, finalization races and recovery.

## Priority 7 — Complete release and operational acceptance

Once fixes are implemented and tested:

- Run Python, JavaScript, configuration, type-checking and integration gates.
- Complete tests requiring a disposable PostgreSQL 16 database.
- Verify the GitLab Runner uses the correct dependency image.
- Build and smoke-test the exact application image.
- Back up PostgreSQL, evidence and configuration before any further upgrade.
- Preserve the existing Compose project and volumes.
- Deploy only the approved version.
- Execute another full report and record the result.

Changes to the application should be limited to demonstrated defects or explicitly approved requirements.

---

# 10. Operational Precautions

The engineer continuing this project should preserve the following decisions:

**Manual execution only.** Do not introduce automatic scheduling of Weekend Reports.

**No fabricated health.** Unavailable metrics must remain distinguishable from genuinely healthy metrics.

**Read-only collectors.** Portainer, RabbitMQ and infrastructure validation must not unexpectedly mutate production services.

**No authentication redesign.** The current application uses an external access-control boundary, not an internal login system.

**No direct database-script execution.** The Windows PowerShell replication script remains manually operated.

**No silent changes to validation policy.** Any change that makes FAIL or ERROR nonblocking requires explicit operational approval.

**No destructive deployment.** Protect the existing PostgreSQL and evidence volumes, along with the current image and environment files.

**No unverified TLS bypass.** Maintain certificate validation and strict SSH host verification.

**No assumptions about Recording.** Synthetic recording functionality is not implemented in v1.5.3.

---

# 11. Definition of Completion

The next engineer can consider v1.5.3 ready for operational acceptance when:

- Portainer continues to work against the real Swarm environment.
- DOCTOR communicates successfully and validates the actual service inventory.
- RabbitMQ returns trustworthy queue and node resource measurements.
- Infrastructure checks correctly identify healthy and unhealthy conditions.
- Timezone comparisons do not generate false failures.
- The NTP/local-reference policy has been reviewed and approved.
- Splunk manual review behaves as intended.
- All remaining WARNING, FAIL and ERROR findings have documented dispositions.
- The manual database synchronization review works correctly.
- APPROVE and REJECT follow their distinct policies.
- The final PDF accurately represents the frozen reviewed results.
- Critical automated and live acceptance tests are complete.
- Deployment, backup and rollback procedures have been verified.

A report containing a genuine infrastructure failure should still report that failure, even when the application is functioning correctly.

**Successful automation means accurate, trustworthy results—not necessarily an all-green report.**

---

# 12. Final Handover Statement

Weekend Report Automation v1.5.3 is an operational monitoring and review framework with an implemented web interface, persistent execution worker, multiple live integrations, stored evidence, manual review controls and final PDF generation.

The current deployment demonstrates that large portions of the solution are functioning, especially Portainer.

The primary remaining work is live integration troubleshooting, configuration validation, and end-to-end acceptance—not a complete rewrite of the application.

The most immediate technical investigations are DOCTOR TLS verification and RabbitMQ socket metrics, followed by the remaining Infrastructure finding, warnings and Splunk configuration.

The new owner should continue from the existing deployment and source repository, preserve established behavior, and make changes only when supported by collected evidence.

**Handover classification: v1.5.3 deployed; operational acceptance pending.**