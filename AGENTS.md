# Weekend Report Automation Agent Rules

This repository implements the Weekend Report Automation from the final authoritative specification, implementation guide, and validation worksheet in the parent workspace.

## Non-Negotiables

- Do not invent production facts. Use only `"<TBD>"`, `"<TO_VERIFY>"`, and schema-approved `"<NOT_APPLICABLE>"`.
- Do not connect to or mutate production systems unless the owner supplies and approves real configuration.
- The Recording synthetic test is state-changing. Keep production execution blocked until create/delete/cleanup definitions are supplied and approved.
- Collectors collect raw state and evidence. Validators decide statuses.
- Reviewer notes are additive. Never rewrite automated statuses because of reviewer approval.
- Every module, result, and Splunk dashboard note must be persisted, frozen in `review_snapshot.json`, and included in the final PDF.
- Review pages are HTML. Generate only one final PDF after final confirmation.

## Local Quality Commands

```powershell
python scripts/validate_config.py --config tests/fixtures/config_valid
python scripts/validate_config.py --config config --expect-invalid
python -m unittest discover -s tests
docker compose -f deploy/docker/compose.yml config
docker build -t weekend-report:local .
```

The default `config/` intentionally contains unresolved placeholders and must fail real-run preflight until the owner supplies production values.
