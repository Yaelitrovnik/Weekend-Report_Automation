# Stale Worker And Recovery Policy

**Documentation synchronized:** 2026-08-23

Weekend Report runs are manually triggered, stored in the database, and claimed by one
persistent worker. The worker records its identity, current module, and heartbeat while a run is
`RUNNING`. Recovery is intentionally conservative because some modules, especially Recording,
can have external state-changing effects.

## 1. Stale-Worker Definition

A stale run is a `RUNNING` run whose `last_heartbeat` is older than the configured timeout in:

```text
rules.recovery.heartbeat_timeout_seconds
```

The current module recorded on the run determines whether normal failure handling is safe or
manual recovery is required.

## 2. Heartbeat Timeout

The heartbeat timeout is a production policy value. Unknown values remain `<TBD>` or
`<TO_VERIFY>` in configuration and block a real production run until approved.

CI and fixture tests may use safe short fixture values. Those values are not production policy.

## 3. Non-Recording Stale-Run Behavior

If a stale run was not executing the `recording` module, the repository moves it to:

```text
state=FAILED
automation_status=ERROR
```

The active run lock is released after the run is marked failed. The worker does not reuse partial
results from the stale run, and a future manually triggered run starts fresh.

## 4. Recording Stale-Run Behavior

If a stale run was executing the `recording` module, the repository moves it to:

```text
state=RECOVERY_REQUIRED
automation_status=ERROR
```

The Recording operation must be treated as uncertain until a human verifies the external
Recording Manager state, all four observation points, and cleanup status.

## 5. `RECOVERY_REQUIRED`

`RECOVERY_REQUIRED` is a blocking state. While any run remains in this state, new Weekend Report
run creation is blocked by the database-backed run-lock policy.

This prevents a new run from compounding an unresolved Recording state.

## 6. No Automatic Replay Of Uncertain Recording Calls

Recording start/stop calls can change real external system state. After a worker crash or stale
heartbeat, the application may not know whether a start or stop call reached the target system.

For that reason, Weekend Report must never automatically replay an uncertain Recording start or
stop call. Recovery must be human-led and evidence-backed.

## 7. Human Cleanup / Recovery Procedure

The operator must inspect the affected run page and identify:

- run ID;
- worker ID;
- current module/recovery reason;
- last heartbeat timestamp;
- available raw and normalized evidence;
- selected existing Recording device if one was recorded;
- Site 1 WebApp observation count/status;
- Site 2 WebApp observation count/status;
- Site 1 server observation count/status;
- Site 2 server observation count/status;
- configured operator instructions.

The operator must verify whether the selected existing device is still recording, whether counts
returned to baseline, and whether cleanup is complete.

## 8. Recovery Evidence Requirements

Recovery evidence should include the human-observed or approved-system facts used to decide that
cleanup is complete. Examples include:

- dashboard or API observation references;
- device state;
- all four configured observation counts;
- cleanup timestamp;
- reviewer/operator note.

Evidence and notes are additive. They must not overwrite automated findings.

## 9. Explicit Recovery Resolution

Recovery is resolved only through an explicit reviewer/operator action with a resolution note.
The repository records that the run was moved to `FAILED` and that no Recording replay was
performed.

The recovery note must be specific enough for later audit/review.

## 10. Blocking Of New Runs Until Resolution

New run creation remains blocked until `RECOVERY_REQUIRED` is explicitly resolved. This is
intentional even if the run lock itself has been released, because unresolved Recording state is
a production safety risk.

## 11. CI/CD Boundary

CI/CD does not perform real Weekend Report production runs and does not execute real Recording
state-changing actions. CI uses fixture configuration, disposable services, and mocks/fixtures
only.

The CI recovery tests verify the state machine and blocking behavior without contacting
production systems.

## 12. Production-Owner Approval Requirements

Do not enable live Recording recovery procedures until the production owner approves:

- heartbeat timeout;
- device selection criteria;
- start/stop contracts;
- cleanup verification;
- operator instructions;
- evidence requirements;
- manager approval path for resolving `RECOVERY_REQUIRED`.

Unknown production values remain controlled placeholders and continue to block production
preflight.
