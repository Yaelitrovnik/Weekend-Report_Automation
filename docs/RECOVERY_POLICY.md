# Stale Worker And Recovery Policy

**Documentation synchronized:** 2026-10-11

Weekend Report runs are manually triggered, stored in the database, and claimed by one
persistent worker. The worker records its identity, current module, and heartbeat while a run is
`RUNNING`. Recovery is intentionally conservative: stale runs are failed without reusing partial
results or replaying uncertain external operations.

## 1. Stale-Worker Definition

A stale run is a `RUNNING` run whose `last_heartbeat` is older than the configured timeout in:

```text
rules.recovery.heartbeat_timeout_seconds
```

## 2. Heartbeat Timeout

The heartbeat timeout is a production policy value. Unknown values remain `<TBD>` or
`<TO_VERIFY>` in configuration and block a real production run until approved.

CI and fixture tests may use safe short fixture values. Those values are not production policy.

## 3. Stale-Run Behavior

When a stale worker is detected, the repository moves the run to:

```text
state=FAILED
automation_status=ERROR
```

The active run lock is released after the run is marked failed. The worker does not reuse partial
results from the stale run, and a future manually triggered run starts fresh.

## 4. `RECOVERY_REQUIRED`

`RECOVERY_REQUIRED` remains a generic blocking state for future recovery policies or administrator
intervention. While any run remains in this state, new Weekend Report run creation is blocked by
the database-backed run-lock policy.

## 5. No Automatic Replay

After a worker crash or stale heartbeat, the application may not know which external operations
completed. Weekend Report must never automatically replay uncertain operations. Recovery must be
human-led and evidence-backed.

## 6. Human Cleanup / Recovery Procedure

The operator must inspect the affected run page and identify:

- run ID;
- worker ID;
- current module/recovery reason;
- last heartbeat timestamp;
- available raw and normalized evidence;
- configured operator instructions.

The operator must verify the target environment before resolving recovery.

## 7. Recovery Evidence Requirements

Recovery evidence should include the human-observed or approved-system facts used to decide that
the environment is safe for another run. Evidence and notes are additive. They must not overwrite
automated findings.

## 8. Explicit Recovery Resolution

Recovery is resolved only through an explicit reviewer/operator action with a resolution note.
The repository records that the run was moved to `FAILED` and that no automatic replay was
performed.

The recovery note must be specific enough for later audit/review.

## 9. Blocking Of New Runs Until Resolution

New run creation remains blocked until `RECOVERY_REQUIRED` is explicitly resolved. This is
intentional even if the run lock itself has been released, because unresolved recovery state is
a production safety risk.

## 10. CI/CD Boundary

CI/CD does not perform real Weekend Report production runs. CI uses fixture configuration,
disposable services, and mocks/fixtures only.

The CI recovery tests verify the state machine and blocking behavior without contacting
production systems.

## 11. Production-Owner Approval Requirements

Production owner approval is required for:

- heartbeat timeout;
- operator instructions;
- evidence requirements;
- manager approval path for resolving `RECOVERY_REQUIRED`.

Unknown production values remain controlled placeholders and continue to block production
preflight.

## Runtime site count

Recovery policy applies equally to one-site and multi-site deployments. `SITE_COUNT`
controls the independent site inventories; only multi-site runs execute parity afterward.
Omitting parity for one site is normal execution and does not create a recovery condition
or blocking SKIPPED finding. Technical collection errors remain attached to the affected
site and retain their existing blocking behavior. Parity never clears independent health
failures. Splunk and manual DB review requirements are unchanged. Increasing sites or
servers increases sequential collection time; review the configured heartbeat timeout
against expected module duration without bypassing stale-worker protection.


### Infrastructure SSH credentials and host trust

Infrastructure authenticates with `SSH_USERNAME` and secret `SSH_PASSWORD`;
`SSH_KNOWN_HOSTS_PATH=/app/secrets/known_hosts` independently verifies server identity.
No SSH private key is needed. Missing credentials, unreadable trust files,
unknown/changed keys, authentication failures, and timeouts remain ERROR.

Correct credentials in runtime `env/infrastructure.env` without logging or committing
the password. Verify a changed server key independently before replacing the
read-only mounted trust file. Never bypass strict verification or auto-enroll keys.
Rerun collection after correcting the cause; mapper and Chrony policies are unchanged.
See [configuration](CONFIGURATION.md#infrastructure-ssh-credentials-and-host-trust).
