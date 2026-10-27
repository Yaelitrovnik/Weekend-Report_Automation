# Validation Catalog

**Documentation synchronized:** 2026-09-06

Controlled placeholders (`<TBD>`, `<TO_VERIFY>`) are not production-ready.

Required unresolved values fail production preflight.

## 1. Global Rules

- Business policy lives in `rules.yml`.
- Deployment state, endpoints, inventory, and secrets live outside the image in ENV/secrets.
- Collectors gather actual state or return a clear blocked/error payload.
- Validators produce `PASS`, `WARNING`, `FAIL`, `ERROR`, `SKIPPED`, or `MANUAL_REVIEW`.
- Raw/normalized evidence is persisted with checksum metadata.
- Cross-site parity is additive and never masks expected-state failures.
- Reviewer notes are additive and never rewrite automated statuses.
- `deploy/docker/config/rules.yml` is authoritative for aggregation and approval readiness.
- CI fixture success does not mean production integration is configured.

## 2. Configuration / Runtime Identity

### `config.preflight`

- loaded `rules.yml`;
- runtime ENV values;
- schema/reference integrity.

Expected:
- all required enabled values resolved;
- controlled placeholders absent where production requires real values;
- valid site/module references.

PASS:
- production-ready config resolves cleanly.

ERROR:
- unresolved required placeholders;
- invalid enum/reference;
- missing required runtime secret/reference.

Evidence:
- preflight diagnostics.

### `runtime.traceability`

Actual:
- `WEEKEND_REPORT_APP_VERSION`;
- `WEEKEND_REPORT_BUILD_ID`;
- configuration hash;
- optional Git commit.

Expected:
- real app version/build ID in production;
- deterministic config hash.

PASS:
- all mandatory values present.

ERROR:
- missing/placeholder production identity.

## 3. Portainer

### `portainer.collection`

Actual source:
- read-only HTTPS GET to configured Portainer Server/API;
- dynamic Docker Swarm service and task discovery.

Expected:
- connection URL;
- auth;
- endpoint ID;
- API contract;
- TLS;
- timeout/retry.

PASS:
- no separate collection PASS required.

ERROR:
- auth/TLS/timeout/unsupported API/invalid JSON/malformed response/unresolved required live config.

Evidence:
- sanitized raw/error payload;
- normalized state.

### `portainer.discovery`

ERROR:
- site collection succeeded but no services were discovered.

### `portainer.service.exists`

PASS:
- discovered service is present and can participate in reporting/parity.

ERROR:
- site state was not collected reliably.

### `portainer.service.desired_replicas`

PASS:
- actual desired replica count is present and valid.

ERROR:
- missing/unreliable data.

### `portainer.service.running_replicas`

PASS:
- running replicas equal the discovered desired replicas.

FAIL:
- reliable running replica shortfall or surplus.

ERROR:
- unreliable count.

### `portainer.service.healthy_replicas`

PASS:
- healthy replicas equal the discovered desired replicas.

FAIL:
- reliable healthy-count shortfall.

ERROR:
- health signal unavailable/unverified when required.

### `portainer.service.image`

PASS:
- discovered service image value is present for evidence/parity.

ERROR:
- image value is missing/unreliable.

### `portainer.service.state`

PASS:
- actual service state is allowed by `rules.yml`.

FAIL:
- actual service state maps to FAIL by `rules.yml`.

ERROR:
- malformed/unreliable state.

### `portainer.service.task_state`

Evaluate independently:

- failed;
- rejected;
- restarting;
- starting.

Configured `rules.yml` policy decides WARNING/FAIL/IGNORE as approved.

Unresolved policy is ERROR/preflight-blocking.

### Portainer parity

Parity runs after site validation.

Both sites being identically unhealthy must not become PASS because they match each other.

## 4. RabbitMQ

### `rabbitmq.collection`

Actual:
- read-only RabbitMQ Management API or fixture actuals.

ERROR:
- configuration/runtime value missing;
- authentication/TLS/timeout/API unavailable;
- malformed response.

Evidence:
- sanitized Management API error payloads;
- queue snapshots;
- node resource payloads.

### `rabbitmq.queue.counts`

Actual:
- observed live queues;
- `ready`;
- `unacked`;
- `total`;
- recheck snapshots/check count.

Expected:
- all observed queues have zero ready/unacked/total messages after configured rechecks.

PASS:
- all counts are zero.

ERROR:
- queue count is missing/malformed;
- any count remains non-zero after configured rechecks unless policy explicitly changes the status.

### `rabbitmq.node.file_descriptors`
### `rabbitmq.node.socket_descriptors`
### `rabbitmq.node.erlang_processes`
### `rabbitmq.node.disk_space`

Expected:
- configured resource state is `green`.

PASS:
- collected resource state is `green`.

ERROR:
- collected state is non-green under current policy;
- required green-state mapping is unavailable.

## 5. Infrastructure

Infrastructure collection is read-only and uses SSH with username/password authentication.

SSH requirements:

- username/password authentication only;
- strict SSH host-key verification;
- a pre-verified `known_hosts` file is required;
- password is passed directly to Paramiko in memory, never through process arguments;
- host-key verification must not be disabled or changed to `accept-new`.

### Collection

ERROR:
- SSH runtime configuration is missing or unresolved;
- username, password, or `known_hosts` configuration is unavailable;
- server is unreachable;
- SSH authentication or host verification fails;
- command execution times out or fails;
- required output cannot be parsed reliably.

### Filesystem

Weekend Report executes `df -h | grep mapper` on every configured infrastructure
server in every site. It discovers mapper filesystems dynamically and evaluates every
returned filesystem independently. No device inventory or specific mountpoint is configured;
`/` is not required. Each result identifies the site, server, device, mountpoint, and usage.

Versioned policy in `rules.yml`:

```yaml
command: df -h | grep mapper
match: mapper
warning_percent: 70
critical_percent: 80
required: true
```

NFS validation is not part of the Infrastructure module.

PASS:
- an individual mapper filesystem's utilization is below 70%.

WARNING:
- utilization is >= 70% and < 80%.

FAIL:
- utilization is >= 80%;
- empty successful output or grep exit 1 without diagnostics:
  "No mapper filesystems were found".

ERROR:
- SSH connectivity, authentication, strict known_hosts verification, or timeout fails;
- command exit is greater than 1 (including SSH exit 255);
- stderr diagnostics indicate collection may be incomplete, even with exit 0 or 1;
- exit 1 unexpectedly includes output;
- output is missing, malformed, truncated, or cannot be parsed reliably.

The exact pipeline reports grep's exit status. A clean exit 1 is a reachable server
with no matching filesystems, not an SSH failure. Diagnostics on stderr are treated
conservatively as ERROR because a failing `df` can otherwise be masked by grep.
The pipeline cannot expose a silent upstream failure that emits no diagnostics.
Mixed valid and malformed output is ERROR rather than a partially successful check.
Raw output and command metadata are retained in evidence alongside normalized results.

### Chrony

Infrastructure validates:

- configured timezone;
- synchronization state;
- selected Chrony source;
- absolute clock offset.

The selected source is collected from:

```text
chronyc sources -n
```

Synchronization and offset state are collected from:

```text
chronyc tracking
```

PASS:
- timezone matches the configured value;
- Chrony is synchronized;
- selected source matches the configured source;
- absolute offset is below the warning threshold.

WARNING:
- absolute offset is in the configured warning range.

FAIL:
- timezone mismatch;
- Chrony is unsynchronized;
- selected source does not match;
- absolute offset reaches the configured critical threshold.

ERROR:
- timezone or Chrony command fails;
- synchronization state is unavailable;
- selected source is unavailable;
- offset is unavailable or malformed.

## 6. DOCTOR

API mode discovers the complete service inventory returned by each site dynamically. Every discovered service is validated independently, and the discovered service sets must match across all runtime sites when there is more than one site.

### `doctor.collection`

ERROR:
- transport/API/authentication/timeout/schema/collection failure;
- malformed site payload.

### `doctor.service.health`

PASS:
- expected service is present and healthy.

ERROR:
- expected service is unhealthy;
- expected service is missing;
- health state is unknown/unparseable.

Only explicitly marked unhealthy/missing service-health findings are reviewable health issues.

### `doctor.module_status`

PASS:
- all discovered services are healthy on every runtime site;
- all sites expose matching discovered service inventories when parity applies.

MANUAL_REVIEW:
- one or more service-level health findings are reviewable health issues.

ERROR:
- any technical/transport/API/authentication/timeout/schema/collection error exists.

The underlying `doctor.service.health` `ERROR` remains recorded as `ERROR` in evidence and reporting.

## 7. Splunk

Actual:
- persisted human dashboard review state.

Expected:
- configured dashboard definitions/review-note policy.

MANUAL_REVIEW:
- dashboard awaits human review.

Finalization ERROR/block:
- required dashboard review/note missing according to policy.

Evidence:
- saved dashboard review acknowledgment and note in database/snapshot.

Opening a dashboard URL is not review completion.

## 8. Review / Finalization

### `review.note_ownership`

Validate:

- result belongs to run;
- dashboard exists;
- module valid;
- general notes enabled if used;
- run is editable.

ERROR:
- ownership/state/scope violation.

### `review.finalization_readiness`

Before APPROVE enforce configured:

- required module completion;
- dashboard review;
- required notes;
- manual-review acknowledgments;
- status-specific approval policy.

Narrow DOCTOR finalization exception:

- a `doctor.service.health` `ERROR` may be excluded from the `ERROR: BLOCK` finalization count only when it is explicitly marked as a reviewable health issue;
- the same result set contains `doctor.module_status=MANUAL_REVIEW`;
- the reviewer has saved the required acknowledgment/note on that module-level manual-review result.

This exception does not apply to DOCTOR transport/API/authentication/timeout/schema/collection errors, configuration errors, infrastructure errors, RabbitMQ errors, Portainer errors, or any unrelated `ERROR`.

FAIL:
- explicit status policy blocks approval.

ERROR:
- required note/ack/state missing;
- snapshot/PDF failure.

### Automated-status immutability

Reviewer acceptance never changes machine:

```text
WARNING -> PASS
FAIL -> PASS
ERROR -> PASS
```

Automated status and reviewer decision remain separate facts.

## 9. Evidence

### `evidence.persistence`

PASS:
- safe run-owned path;
- file exists;
- checksum metadata recorded.

ERROR:
- unsafe path;
- write failure;
- missing file;
- checksum/path validation failure.

Raw evidence must be sanitized of known credentials/tokens.

## 10. Application Access / CSRF

### `security.application_access`

Weekend Report does not implement an application login, local user database, trusted identity header, or authorized-reviewer list. Deployment access is controlled outside the application.

### `security.browser_csrf`

PASS:
- signed token is valid when CSRF protection is configured.

FAIL:
- invalid, expired, or missing token for a protected browser mutation.

ERROR:
- production preflight is missing the CSRF signing key or has an invalid TTL.

The reviewer name is entered manually only during final confirmation and is not an access-control identity.

## 11. Recovery

### `recovery.stale_worker`

Stale worker:
- fail without replay.

### `recovery.manual_resolution`

PASS:
- explicit safe human resolution completed;
- new run unblocked without replaying uncertain state-changing call.

ERROR:
- unresolved/invalid recovery.

## 12. Reporting

### `reporting.final_pdf_access`

PASS:
- route serves recorded run-owned final PDF;
- safe path;
- safe run-owned report path and existing file.

ERROR:
- unsafe/missing/arbitrary path.

### `reporting.snapshot_completeness`

PASS:
- all persisted notes represented;
- results/evidence/summaries/traceability represented.

ERROR:
- completeness mismatch.

## 13. Docker Runtime

### `docker.runtime_secret_boundary`

PASS:
- Compose receives real required runtime values.

ERROR:
- required secret missing;
- literal controlled placeholder used as runtime secret.

### `docker.exact_image_smoke`

Actual:
- supplied `WEEKEND_REPORT_CI_IMAGE`.

Expected:
- PostgreSQL healthy;
- web running;
- worker running;
- `/healthz` OK;
- migration/access succeeds.

PASS:
- exact image passes all checks.

ERROR/FAIL:
- any required container/health/migration check fails.

Important:
`compose.ci.yml` must not rebuild a different image.

## 14. CI / Release Validation

### `ci.pre_image_quality`

Required gates:

- config;
- Ruff;
- Mypy;
- unit;
- contract;
- integration;
- PostgreSQL concurrency;
- safe E2E;
- dependency audit;
- Compose validation.

PASS:
- every required gate succeeds.

FAIL:
- any gate fails.

Effect:
- image build must not start.

### `ci.postgres_concurrency`

PASS:
- simultaneous create protected;
- single worker claim protected.

FAIL:
- race/concurrency invariant broken.

Must use disposable PostgreSQL.

### `ci.release_tag_file`

Actual:
- root `TAG` content.

Expected:
- one semantic-style version with leading `v`.

PASS example:

```text
v1.0.2
```

ERROR:
- file missing;
- malformed;
- empty;
- release workflow derives version from Git tag instead.

GitHub must not require `GITHUB_REF_NAME` for release version.

GitLab must not require `CI_COMMIT_TAG` for release version.

### `ci.release_trigger`

Normal source change:

```text
quality only
```

TAG change on configured release/default branch:

```text
quality -> image
```

FAIL:
- normal source-only commit builds release image;
- TAG change bypasses required quality gates.

### `ci.image_delivery`

Required sequence:

```text
build exact image
-> record identity
-> smoke exact image
-> tag same image as weekend-report:<TAG>
-> verify image IDs match
-> export weekend-report:<TAG>
-> optional push same image
```

FAIL:
- export/publish happens before smoke;
- exported archive contains only the CI tag instead of weekend-report:<TAG>;
- release tag does not point to the same image ID as the smoked CI tag;
- a different image is rebuilt after smoke;
- smoke failure still permits release.

### `ci.secret_boundary`

PASS:
- only disposable/test credentials;
- no production integration secrets in CI definitions/artifacts.

FAIL:
- production secret names/values are embedded where forbidden.

## 15. Release Artifact

Verified artifact contains:

```text
weekend-report_<TAG-version>_<short-sha>.tar.gz
weekend-report_<TAG-version>_<short-sha>.tar.gz.sha256
image-id.txt
```

The SHA-256 must be verified after offline transfer before loading/deployment.

## Dynamic sites and parity results

All independent Portainer, DOCTOR, RabbitMQ, and Infrastructure checks apply to each runtime
site from `SITE_COUNT`. Single-site deployments omit parity entirely: no parity PASS, FAIL,
ERROR, SKIPPED, or MANUAL_REVIEW result is emitted merely for non-applicability. They can
be approved when actual checks and review requirements pass. Splunk and manual DB review
remain independent of site count.

With two or more sites, parity runs after independent checks. Portainer fields remain
`service_presence`, `desired_replicas`, `running_replicas`, `healthy_replicas`, `image`, and
`service_state`; DOCTOR uses `service_presence`. No RabbitMQ parity is added. Each field
compares service/value maps across all sites. Equal or fully allowed differences PASS;
unallowed mismatches FAIL under default policy; unavailable reliable data produces ERROR.
Evidence includes all participating IDs, observed maps, and per-service differences.
Independent health failures are never changed by parity, including identically unhealthy
sites. DOCTOR technical failures block approval while unaffected sites are still checked.
Explicit allowed-difference syntax and scope are documented in CONFIGURATION.md.
