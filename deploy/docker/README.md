# Docker Deployment

`compose.yml` defines web, worker, and PostgreSQL. The same image is used by web and worker through `WEEKEND_REPORT_IMAGE`, defaulting to `weekend-report:local` for local development. PostgreSQL is only attached to the backend network and is not exposed publicly.

The checked-in `env.example` is a template only. Do not pass it to Compose as a runtime
`env_file`. Create a non-committed `deploy/docker/.env`, use Docker secrets, or use an
approved secret mechanism before production use.

Required runtime values include at least `POSTGRES_PASSWORD`, `WEEKEND_REPORT_APP_VERSION`,
`WEEKEND_REPORT_BUILD_ID`, configured production auth provider values, authorized reviewers,
and `WEEKEND_REPORT_CSRF_SIGNING_KEY`. The application fails clearly when production
traceability, auth, or mutation protection is incomplete.

For a verified offline release, load the CI artifact and set:

```text
WEEKEND_REPORT_IMAGE=weekend-report:v1.0.2
WEEKEND_REPORT_APP_VERSION=v1.0.2
WEEKEND_REPORT_BUILD_ID=<actual-build-id>
```

Do not invent the production build ID.

Portainer runtime variables such as `PORTAINER_SITE1_URL`, `PORTAINER_SITE1_TOKEN`,
`PORTAINER_SITE1_CA_FILE`, `PORTAINER_SITE2_URL`, `PORTAINER_SITE2_TOKEN`, and
`PORTAINER_SITE2_CA_FILE` are passed through when supplied. They are not real values in the
template files. Live Portainer collection remains blocked by application preflight until the
approved authentication method, TLS policy, API contract, endpoint IDs, and service expectations
are configured.

```powershell
docker compose -f deploy/docker/compose.yml config
docker build -t weekend-report:local .
docker compose -f deploy/docker/compose.yml up
```

For syntax-only Compose validation before real secrets exist, set safe dummy values in the shell.
Do not copy `env.example` to `.env` without replacing every controlled placeholder with an
approved real value.
