# Run-Lock Scope Decision

**Documentation synchronized:** 2026-08-22

**Status: design-decision document, no code changes.** This lays out the
current single-global-lock model, what moving to a per-site lock would
actually require, and the tradeoffs, for a human to review and approve
before any implementation begins. Nothing here should be treated as
approved scope — see `AGENTS.md`'s non-negotiables on not inventing
production facts or silently expanding scope.

## 1. The current model

Run concurrency is enforced twice, redundantly, inside one transaction in
`Repository.create_run()` (`app/database/repository.py`):

1. A single-row lock table, `run_lock`, keyed by the literal name
   `'weekend_report'` (see `app/database/models.py` — both the SQLite and
   PostgreSQL schemas), holding at most one `active_run_id`.
2. A direct query against `runs` for any row whose `state` is in
   `ACTIVE_STATES` (`app/orchestrator/lock.py`:
   `{"CREATED", "RUNNING", "RECOVERY_REQUIRED"}`).

Either check failing raises `DuplicateActiveRun`, which blocks a second run
from being created at all. This is deliberately belt-and-suspenders, not
fragile single-point enforcement — worth knowing going in, since it means
"the lock" isn't one brittle mechanism but two independent guards on the
same invariant.

Once a run *is* created, its execution is **not** site-scoped in any way:
`build_execution_plan()` (`app/orchestrator/execution_plan.py`) returns one
ordered list of *modules* (portainer, rabbitmq, recording, etc.), and each
collector is responsible for gathering both sites' actual state within that
single module step. `OrchestratorRunner.run()` calls each collector exactly
once per module, not once per site per module.

## 2. Before assuming the lock needs to change: what already works today

The task that prompted this document uses a specific scenario: "site1 could
be ready to check while site2 is still being configured." It's worth
checking whether that's actually blocked by the *lock*, or by something
else entirely — because they have very different fixes.

**The lock itself has nothing to do with per-site readiness.** It only
prevents a *second Weekend Report run* from starting while one is already
`CREATED`/`RUNNING`/`RECOVERY_REQUIRED`. A single run already processes
whatever data each collector returns for each site, and — critically — most
collectors already tolerate one site failing without blocking the other:

- `PortainerCollector._collect_live()` (`app/collectors/portainer.py`)
  iterates sites individually, catching `PortainerError` **per site** into
  an `errors` list, and continues on to the next site rather than aborting
  the whole module. A misconfigured or not-yet-ready site1 produces a
  `portainer.collection` `ERROR` result for site1 specifically —
  site2 still collects and validates normally, in the same run.
- Per-check results already carry a `site` field independently
  (`app/domain.py`'s `CheckResult.site`), and aggregation/review already
  groups and displays by site (`_site_summary_items` in
  `app/api/routes_review.py`, the Site Summary panel in `run.html`).

So: **a run today already produces independent PASS/FAIL/ERROR outcomes per
site within a single execution**, without any lock-scope change. If the
actual pain point is "I want to review and approve site1's results without
waiting for site2," that's a **finalization/review-granularity** question
(can you `APPROVE` a run whose site2 checks are all `ERROR`, given
`rules.review.approval_status_policy`?), not fundamentally a locking or
execution-scoping question.

**The one real gap:** `RabbitMQCollector` (`app/collectors/rabbitmq.py`) is
*not* per-site tolerant the way Portainer is — its live path is monolithic
(`RABBITMQ_LIVE_BLOCKED` is a single collector-wide error, not iterated per
site), so a RabbitMQ live-collection problem for one site currently blocks
both. That's worth fixing on its own, as a collector-level change scoped
like the other per-module fixes in this task list — it does not require
touching `run_lock`, `ACTIVE_STATES`, or the orchestrator's execution plan
at all.

**Implication:** before treating "per-site locking" as the fix, it's worth
confirming with whoever raised the need which of these they actually mean:

- (a) *"I want one run to tolerate a site not being ready yet"* — already
  mostly true today, RabbitMQ being the one gap; and
- (b) *"I want to run, review, and approve site1 completely independently
  of site2, on separate schedules, with separate sign-off"* — this is a
  genuinely different, much larger architectural change, covered below.

The rest of this document assumes (b), since that's the interpretation that
actually requires touching the lock.

## 3. What full per-site independence would require

### 3.1 Schema changes

- `run_lock` would need to move from one global row to one row per site
  (or a composite key), e.g. `(name, site)` instead of `name` alone, so
  `site1` and `site2` can each have an independent `active_run_id`.
- `runs` would need a `site` column (nullable, to keep supporting a
  combined "both sites" run mode if that's still wanted) — every place that
  currently assumes one run = one report covering all sites would need to
  either filter by this column or be duplicated per site.
- `results`, `evidence`, and `review_notes` already carry `site` as a
  column/field, so those tables need **no** structural change — this is a
  real mitigating factor, not a full rewrite of the persistence layer.

### 3.2 Orchestrator / collector changes

- `build_execution_plan()` and `OrchestratorRunner.run()` would need a
  concept of "which site(s) is this run for," and every collector would
  need to accept and honor a site filter — right now collectors like
  `PortainerCollector` iterate `config["portainer_expected"]["sites"]`
  unconditionally; they'd need to skip sites outside the run's scope.
- **`SiteParityValidator` is the central architectural obstacle here, not
  a peripheral detail.** Per `docs/ARCHITECTURE.md` §7.1, the entire point
  of parity validation is that "both sites being identically wrong must
  still fail expected-state validation" — and mechanically, that only
  works because `SiteParityValidator.validate()` (`app/validators/site_parity.py`)
  receives **both sites' `CheckResult`s together** in one `all_results`
  list, groups them by `(module, site)`, and compares. If site1 and site2
  become genuinely separate runs, there is no single point in time where
  both sites' results exist together to compare — parity validation as
  currently designed becomes structurally impossible, not just harder.
  Preserving it would require either (a) a separate process that reads
  results across two independent runs after the fact, reintroducing
  cross-run coupling the "independent" model was meant to remove, or
  (b) accepting the loss of same-run parity checking, which is a real
  safety-feature regression, not a minor UX tradeoff.

### 3.3 Review / finalization / reporting changes

- One run currently produces one `review_snapshot.json` and one final PDF
  (`app/reporting/snapshot.py`'s `finalize_run()`). Per-site independence
  means either two separate snapshots/PDFs per weekend (doubling the
  artifact count and diluting "one final PDF" as the single source of
  truth — see `README.md`'s "Review and Finalization" section and
  `AGENTS.md`'s "Generate only one final PDF after final confirmation"),
  or a more complex model where a single PDF is assembled from two
  independently-approved site runs, which is a different kind of
  complexity (need to track and merge two `APPROVE`d states before a PDF
  can even be generated).
- `run.html` and `review.html` currently render one `overall_status`, one
  set of module summaries, one finalize/APPROVE-or-REJECT action per page.
  Per-site independence needs either two parallel review flows or a
  restructured page that can represent "site1 approved, site2 still
  pending" as a first-class state — not a small template tweak.

### 3.4 Evidence layer — the one area already well-positioned

Worth calling out explicitly since it's the opposite of the rest of this
section: `EvidenceManager._write()` already partitions storage by
`runs/<run_id>/<site>/<module>/<filename>` when a site is given. Evidence
storage would need effectively no structural change for per-site
independence — it's already organized as if sites were separable. This
doesn't offset §3.2's parity-validation problem, but it means evidence
storage specifically is not a blocker.

### 3.5 Migration path for in-flight runs

- Existing `runs` rows have no site-scoping information at all — a new
  nullable `site` column defaulting to `NULL` would need to mean "this run
  covers all configured sites" (the current, only-ever-existing behavior),
  so historical runs remain valid without backfilling.
- Any run in `CREATED`/`RUNNING`/`RECOVERY_REQUIRED` at the moment of a
  schema migration needs a defined behavior — most likely: let it finish
  under the old combined-run semantics, and only apply per-site locking to
  runs created after the migration. Do not attempt to retroactively split
  an in-progress combined run into two site-scoped ones.

## 4. Tradeoffs

| | Current (single global lock) | Per-site independent locking |
|---|---|---|
| Blocks concurrent runs | Yes, always, across all sites | Yes, but scoped per site |
| Site1 can proceed while site2 unconfigured | **Already true today** for most modules (§2), independent of locking | Also true, but for a different reason (separate runs) |
| Cross-site parity validation | Fully supported — the documented flagship anti-masking feature | Structurally broken as currently designed (§3.2) unless reintroduced with cross-run coupling |
| Final PDF / snapshot model | One PDF, one snapshot, one source of truth per weekend | Either two artifacts, or a more complex assembled-from-two-approvals model |
| Schema/orchestrator/review footprint | N/A — this is the current state | Touches `run_lock`, `runs`, collectors, `SiteParityValidator`, review templates, finalization |
| Migration risk for historical runs | N/A | Real but manageable via a nullable `site` column (§3.5) |

## 5. Recommendation

**Do not implement per-site independent locking now.** The concrete
scenario motivating this — a site not being ready yet — does not actually
require it; §2 shows most of that already works, with RabbitMQ's collector
being the one real, much smaller gap worth fixing on its own (no lock or
schema change needed for that fix). Full per-site independence would trade
away the parity validator's core safety property (§3.2) for a workflow
benefit that's mostly achievable another way.

If, after reading this, the actual need still turns out to be (b) from §2 —
genuinely separate review/approval cycles per site, not just per-site fault
tolerance within one run — that is a legitimate reason to revisit this, but
it should be scoped as its own deliberate project (schema, orchestrator,
validators, and review UI all in coordination), not as an incremental
change to `app/orchestrator/lock.py` alone. Recommend closing the RabbitMQ
collector gap first, then reassessing whether the harder problem is still
open.

## 6. Explicitly out of scope for this document

- No code changes accompany this document.
- No schema migration has been written.
- The RabbitMQ per-site collector fix mentioned in §2 is not implemented
  here — it's flagged as a smaller, separate, lower-risk follow-up if
  fault-tolerance (not review independence) turns out to be the actual need.