# Stale Worker And Recovery Policy

Weekend Report runs are manually triggered and then claimed by one worker. The worker records a heartbeat and current module during execution. A stale run is a `RUNNING` run whose `last_heartbeat` is older than `rules.recovery.heartbeat_timeout_seconds`.

Recovery is intentionally conservative:

- Non-Recording stale runs are moved to `FAILED` with `automation_status=ERROR`. The run lock is released so a new run can be created after review of the failure.
- Recording stale runs are moved to `RECOVERY_REQUIRED` with `automation_status=ERROR`. The run lock may be released, but `RECOVERY_REQUIRED` itself blocks creation of any new run until a human resolves recovery. The Recording operation is not replayed automatically because a start/stop call may have changed recording state in the target system.
- Recording recovery requires a human to inspect the affected run page, affected module/reason, worker ID, last heartbeat, available evidence, and configured operator instructions. The operator must verify whether the selected existing device is still recording, whether WebApp/backend counts returned to baseline, and whether cleanup is complete before supplying a recovery resolution note.
- Explicit recovery resolution moves the run to `FAILED` with a resolution message that states no Recording replay was performed. Only after that resolution can a new run be created.
- The worker does not reuse partial module results from stale runs. A future run must start from a fresh manually triggered run after recovery is complete.

The production timeout value is deliberately a configuration input. Unknown values must remain `<TBD>` or `<TO_VERIFY>` and will block a real production run until verified.

Do not resolve recovery until the production owner has approved the cleanup/recovery instructions for the specific environment.
