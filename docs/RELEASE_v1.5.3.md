# Weekend Report Automation v1.5.3

Prepared 2026-11-09 from the existing modified working tree based on
`062e3ee05cc88bf2b2b462d8836ac1991d464aa6`. The changes remain uncommitted.
Nothing was deployed, pushed, or published. `TAG` is `v1.5.3`.

**Readiness: local validation complete; production release is conditional.**
The Docker engine and connected browser were unavailable. A disposable PostgreSQL
instance and verified production DOCTOR endpoint/schema were not supplied.
Complete those checks below before authorizing deployment. See
[validation evidence and complete changed-file inventory](VALIDATION_v1.5.3.md).

## Changes retained and completed

- REJECT accepts a completed, commented manual DB review with PASS, FAIL, or NOT RUN.
  Reviewer identity, a rejection reason (or legacy general-note fallback),
  `reject_allowed`, and REVIEW_READY remain enforced. APPROVE keeps its stricter gates.
- Review decisions, timestamps, reasons, results, notes, and evidence are frozen in
  snapshot version 3. The nullable `runs.rejection_reason` addition preserves old rows.
  Finalization and note/manual-review writes coordinate through locking. A PDF failure
  preserves the snapshot and supports retry with the same reviewer/decision/reason.
- Portainer distinguishes Global and Replicated services; Global desired counts use
  read-only node inventory, placement constraints, platforms, and node availability.
  Missing scheduling evidence stays unavailable. Historical failures are retained
  separately; missing health measurements never become invented healthy counts.
- DOCTOR supports existing unauthenticated access plus Basic, bearer, and API-key auth;
  TLS verification remains required. Transport/HTTP/schema errors are classified and
  missing configured services are technical errors. Backend URLs must be verified;
  a UI fragment URL is not a backend API endpoint.
- RabbitMQ missing/null/nonpositive capacity stays ERROR. Chrony distinguishes the
  tracked reference, local fallback, synchronization, reachability, uncertainty, and
  timezone. Non-finite uncertainty limits are rejected. Mapper defaults remain intact;
  explicitly configured root checks still fail at 95% usage.
- Reports use paginated ReportLab layout. UI shows review blockers, progress, service
  mode, unavailable values, and single-site parity as NOT_APPLICABLE.
- Shared redaction covers structured secrets, connection strings, URL credentials,
  bearer/basic credentials, private keys, and cookie headers. Structured event logging
  avoids raw exception messages from collectors/workers.
- Resumed-session fixes: DOCTOR template test alignment; QA script lint; duplicate note
  editors synchronized and saved once; final controls locked on success; same-page PDF
  retries skip frozen note writes; reviewer/reason normalization on retry; cookie-header
  redaction; finite Chrony limits; Docker context excludes local QA/venv artifacts.

## Security and data boundaries

There is no built-in application login/account authentication in this repository,
including the v1.5.2 baseline. Preserve the existing reverse proxy/access-control and
network boundary. Manually entered reviewer names are attribution, not authentication.
Do not interpret passing CSRF tests as proof of an authenticated deployment.

Production configuration requires a CSRF signing key; browser mutations send signed
tokens. Connector credentials, verified SSH host keys, TLS, and read-only collector
operations remain in place. Recording execution remains unsupported/disabled; the
manual database PowerShell script is never executed by the server.

Tests used temporary SQLite/fixture data only. No production database was contacted.
The existing local preview database remained byte-identical. Migration preservation
was verified with populated SQLite tables and retained PDFs/snapshots; PostgreSQL
migration and locking still need the disposable-database gate.

## Build and validate on a Docker-capable Windows host

Use PowerShell and Python 3.14. Commands below stop on failed external commands.
Building does not authorize publication or deployment. Review/commit the source using
your normal process before producing the release image; do not push merely to run QA:
changing `TAG` on the configured default branch can trigger image delivery in CI.

```powershell
Set-Location 'C:\Users\Administrator\Desktop\Projects\Yael\Weekend-Report_automation'
function Invoke-Checked {
    param([string]$File, [string[]]$Arguments)
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$File failed with exit code $LASTEXITCODE" }
}
$Version = (Get-Content -LiteralPath TAG -Raw).Trim()
if ($Version -ne 'v1.5.3') { throw 'Unexpected TAG' }
Invoke-Checked git @('status', '--short')
Invoke-Checked python @('-m', 'venv', '.venv') # omit if the project venv already exists
$Python = (Resolve-Path '.venv\Scripts\python.exe').Path
Invoke-Checked $Python @('-m', 'pip', 'install', '-r', 'requirements.txt')
Invoke-Checked $Python @('-m', 'pip', 'check')
foreach ($Gate in @('config','lint','typecheck','unit','contract','integration','e2e','audit','compose-config')) {
    Invoke-Checked $Python @('scripts/ci.py', $Gate)
}
Invoke-Checked $Python @('-m', 'pytest', '-q', '-ra')
Invoke-Checked node @('--check', 'app/web/static/app.js')
Invoke-Checked node @('--test', 'scripts/test_frontend_153.cjs')
Invoke-Checked $Python @('scripts/qa_release_153.py')
Invoke-Checked docker @('version')

# Rebuild the base: v1.5.2's base may not contain reportlab==4.4.10.
Invoke-Checked docker @('build', '--pull', '-f', 'Dockerfile.runtime-base', '-t', 'weekend-report-runtime-base:python314', '.')
$BuildId = 'v1.5.3-' + (Get-Date -Format 'yyyyMMddTHHmmss')
Invoke-Checked docker @('build', '--no-cache', '--label', "org.opencontainers.image.version=$Version", '--label', "weekend-report.build-id=$BuildId", '-t', "weekend-report:$Version", '.')
Invoke-Checked docker @('run', '--rm', '--network', 'none', "weekend-report:$Version", 'python', '--version')
Invoke-Checked docker @('run', '--rm', '--network', 'none', "weekend-report:$Version", 'python', '-c', "import reportlab,paramiko; assert reportlab.Version == '4.4.10'; assert paramiko.__version__ == '5.0.1'")
$env:WEEKEND_REPORT_APP_VERSION = $Version
$env:WEEKEND_REPORT_BUILD_ID = $BuildId
Invoke-Checked $Python @('scripts/ci.py', 'image-smoke', '--image', "weekend-report:$Version")

# Export only the exact smoke-tested image. No registry push is included.
New-Item -ItemType Directory -Force -Path 'output\release' | Out-Null
Invoke-Checked docker @('save', '--output', 'output/release/weekend-report_v1.5.3.tar', 'weekend-report:v1.5.3')
$Digest = (Get-FileHash 'output/release/weekend-report_v1.5.3.tar' -Algorithm SHA256).Hash
Set-Content 'output/release/weekend-report_v1.5.3.tar.sha256' $Digest -Encoding ascii
Set-Content 'output/release/build-id.txt' $BuildId -Encoding ascii
Invoke-Checked docker @('image', 'inspect', 'weekend-report:v1.5.3', '--format', '{{.Id}}')
```

`Dockerfile.runtime-base` currently specifies `python:3.14.7-slim-trixie`; the local
tests used Python 3.14.2. The configured container runtime has not been verified here.
Rebuild the dedicated `Dockerfile.ci` image as well if using an offline CI runner:
requirements and its baked audit/hash must match this release. The runtime server only
needs the application image and its existing PostgreSQL image, not the development venv.

Before release, point the PostgreSQL gate only at a disposable test database:

```powershell
$env:WEEKEND_REPORT_TEST_POSTGRES_URL = 'postgresql://TEST_USER:TEST_PASSWORD@TEST_HOST:5432/weekend_report_test'
$env:WEEKEND_REPORT_TEST_POSTGRES_DISPOSABLE = '1'
Invoke-Checked $Python @('scripts/ci.py', 'postgres')
Remove-Item Env:WEEKEND_REPORT_TEST_POSTGRES_URL
Remove-Item Env:WEEKEND_REPORT_TEST_POSTGRES_DISPOSABLE
```

The PostgreSQL gate truncates its test tables. Never use a shared or production URL.
Also exercise concurrent finalization/note writes and migration on a disposable
PostgreSQL copy before production; the existing PostgreSQL gate covers run creation,
worker claiming, and manual DB upsert, not all new finalization interleavings.

## Browser and live-integration acceptance still required

On a disposable staging deployment with verified infrastructure access:

1. Confirm the deployment's authentication/access boundary and reject mutations with
   missing, invalid, or expired CSRF tokens. Use ordinary browser sessions and real TLS.
2. Create a run; check progress, every module page, one-site and multiple-site views,
   desktop/narrow layout, keyboard controls, and absence of console/network failures.
3. Edit an attention-finding note, save/reload, and verify the duplicate editor agrees.
   Save DB PASS; REJECT separately with Portainer FAIL, infrastructure FAIL, DOCTOR ERROR,
   and RabbitMQ failure. Require reviewer/reason and the second confirmation.
4. Verify immutable results, correct rejected state/reason/timestamp, PDF download,
   disabled editing after finalization, blocked duplicate finalization, and PDF failure
   retry using the original frozen snapshot. Verify APPROVE still obeys all gates.
5. Confirm 21-service/Global behavior against actual Portainer nodes/tasks and permissions;
   test unavailable health honestly. Verify the actual DOCTOR backend request, response
   schema, auth, and expected inventory. Confirm RabbitMQ capacity and Chrony source,
   local fallback, reachability, uncertainty, and timezone against retained evidence.
6. Check redacted evidence/logs with synthetic secret sentinels, without real credentials
   in screenshots or reports. Confirm recording remains disabled and no server-side
   database script is executed.

## Deployment after explicit authorization

Use the existing runtime directory/project name/access mode. Do not create a fresh
Compose project: that would create different database/evidence volumes. The following
parameters are intentionally operator-supplied because the running stack was unavailable.
Use the `Invoke-Checked` helper above in the deployment PowerShell session.

```powershell
$Deploy = 'C:\REPLACE_WITH_EXISTING_RUNTIME_DIRECTORY'
$Project = 'REPLACE_WITH_EXISTING_COMPOSE_PROJECT'
$Mode = 'proxy' # retain the existing mode; use 'direct' only if already configured
$Package = 'C:\REPLACE_WITH_TRANSFERRED_RELEASE_DIRECTORY'
if ($Deploy -match 'REPLACE_' -or $Project -match 'REPLACE_' -or $Package -match 'REPLACE_') {
    throw 'Set the existing deployment and transferred package parameters first'
}
$Compose = @('-p', $Project, '--project-directory', $Deploy, '--env-file', "$Deploy\.env",
    '-f', "$Deploy\compose.yml", '-f', "$Deploy\compose.$Mode.yml")
function Invoke-Compose {
    & docker compose @Compose @args
    if ($LASTEXITCODE -ne 0) { throw "Compose failed with exit code $LASTEXITCODE" }
}
Invoke-Compose ps
$ExpectedHash = (Get-Content "$Package\weekend-report_v1.5.3.tar.sha256" -Raw).Trim()
if ((Get-FileHash "$Package\weekend-report_v1.5.3.tar" -Algorithm SHA256).Hash -ne $ExpectedHash) {
    throw 'Image archive checksum mismatch'
}
Invoke-Checked docker @('load', '--input', "$Package\weekend-report_v1.5.3.tar")

$Stamp = Get-Date -Format 'yyyyMMddTHHmmss'
$Backup = Join-Path (Split-Path $Deploy -Parent) "weekend-report-backup-$Stamp"
New-Item -ItemType Directory -Path $Backup | Out-Null
$WebContainer = (Invoke-Compose ps -q web).Trim()
if (-not $WebContainer) { throw 'Existing web container not found' }
$OldImageId = (& docker inspect --format '{{.Image}}' $WebContainer).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Cannot identify existing image' }
$RollbackImage = "weekend-report:rollback-pre153-$Stamp"
Invoke-Checked docker @('tag', $OldImageId, $RollbackImage)
Set-Content "$Backup\rollback-image.txt" $RollbackImage -Encoding ascii
Copy-Item -LiteralPath "$Deploy\.env" -Destination "$Backup\compose.env"
Copy-Item -LiteralPath "$Deploy\env" -Destination "$Backup\env" -Recurse
Copy-Item -LiteralPath "$Deploy\config" -Destination "$Backup\config" -Recurse
# Treat this directory as sensitive because it contains existing runtime ENV files.

# Arrange a maintenance window and finish active runs/reviews first.
Invoke-Compose stop web worker
Invoke-Compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f /tmp/weekend-report-pre153.dump'
Invoke-Compose cp 'postgres:/tmp/weekend-report-pre153.dump' "$Backup\database.dump"
if ((Get-Item "$Backup\database.dump").Length -eq 0) { throw 'Empty database backup' }
Invoke-Checked docker @('run', '--rm', '--network', 'none', '--user', '0',
    '--volumes-from', "${WebContainer}:ro", '--mount', "type=bind,source=$Backup,target=/backup",
    '--entrypoint', 'python', $RollbackImage, '-c',
    "import shutil; shutil.make_archive('/backup/evidence', 'gztar', '/app/runs')")
if ((Get-Item "$Backup\evidence.tar.gz").Length -eq 0) { throw 'Empty evidence backup' }
```

Before continuing, verify the database backup can be listed/restored in a disposable
database and the evidence archive can be opened. Preserve the existing deployment
secrets/TLS and proxy settings. Merge the new optional policy defaults and DOCTOR ENV
fields into the existing files; do not overwrite inventory or secrets with examples.

Set only these release identity/image values in the existing runtime files, retaining
all other values. This helper updates or appends one dotenv key without printing secrets:

```powershell
function Set-DeploymentEnvValue {
    param([string]$File, [string]$Key, [string]$Value)
    $Lines = [System.Collections.Generic.List[string]]::new()
    $Found = $false
    foreach ($Line in [System.IO.File]::ReadAllLines($File)) {
        if ($Line -match ('^' + [regex]::Escape($Key) + '=')) {
            if (-not $Found) { $Lines.Add("$Key=$Value") }
            $Found = $true
        } else { $Lines.Add($Line) }
    }
    if (-not $Found) { $Lines.Add("$Key=$Value") }
    [System.IO.File]::WriteAllLines($File, $Lines, [System.Text.UTF8Encoding]::new($false))
}
$BuildId = (Get-Content "$Package\build-id.txt" -Raw).Trim()
Set-DeploymentEnvValue "$Deploy\.env" 'WEEKEND_REPORT_IMAGE' 'weekend-report:v1.5.3'
Set-DeploymentEnvValue "$Deploy\env\app.env" 'WEEKEND_REPORT_APP_VERSION' 'v1.5.3'
Set-DeploymentEnvValue "$Deploy\env\app.env" 'WEEKEND_REPORT_BUILD_ID' $BuildId
# Replace with the reviewed commit when known; never identify a dirty tree as clean HEAD.
Set-DeploymentEnvValue "$Deploy\env\app.env" 'WEEKEND_REPORT_GIT_COMMIT' '<NOT_APPLICABLE>'
```

For unavailable Git metadata use the literal supported value `<NOT_APPLICABLE>`.
Keep the existing database URL, PostgreSQL credentials, CSRF key, secrets paths, and
volumes. A verified DOCTOR backend URL/schema/auth is a prerequisite; the checked-in
placeholder deliberately blocks production preflight. Update the process environment
if it already overrides the Compose image, then validate and migrate once while both
application services are stopped:

```powershell
$env:WEEKEND_REPORT_IMAGE = 'weekend-report:v1.5.3'
Invoke-Compose config --quiet
Invoke-Compose run --rm --no-deps web python scripts/validate_config.py --config /app/config
Invoke-Compose run --rm --no-deps web python scripts/migrate.py
Invoke-Compose up -d --no-deps web worker
Invoke-Compose ps
if ($Mode -eq 'proxy') {
    Invoke-Compose exec -T web python -c "import urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8080/healthz'); assert r.status == 200; print(r.read().decode())"
} else {
    $HealthUrl = 'https://REPLACE_WITH_EXISTING_TRUSTED_HOST:8080/healthz'
    if ($HealthUrl -match 'REPLACE_') { throw 'Set the existing trusted HTTPS health URL' }
    Invoke-RestMethod -Uri $HealthUrl # use the real trusted hostname; no TLS bypass
}
Invoke-Compose logs --tail 100 web worker
```

Do not use the HTTP-only health command in direct TLS mode. Check the trusted public
health URL instead. Confirm both services use the new image, existing historical run
counts/records and PDF downloads are intact, and the browser acceptance checklist passes.
Starting services does not authorize triggering live operational checks automatically.

## Rollback after authorization

Prefer application/config rollback while retaining the database/evidence volumes.
The migration only adds nullable text; v1.5.2 can leave that column in place. Rejection
reasons remain in new snapshots/PDFs even if the old UI does not display the new column.

```powershell
# Same $Deploy, $Project, $Compose and $Backup from the deployment session.
Invoke-Compose stop web worker
$RollbackImage = (Get-Content "$Backup\rollback-image.txt" -Raw).Trim()
Copy-Item -LiteralPath "$Backup\compose.env" -Destination "$Deploy\.env" -Force
Copy-Item -Path "$Backup\env\*" -Destination "$Deploy\env" -Force
Copy-Item -Path "$Backup\config\*" -Destination "$Deploy\config" -Force
$env:WEEKEND_REPORT_IMAGE = $RollbackImage
Set-DeploymentEnvValue "$Deploy\.env" 'WEEKEND_REPORT_IMAGE' $RollbackImage
Invoke-Compose config --quiet
Invoke-Compose up -d --no-deps web worker
Invoke-Compose ps
Invoke-Compose logs --tail 100 web worker
```

Repeat the correct HTTP/HTTPS health and historical-record checks. Do not drop the new
column, run `down -v`, prune volumes, or restore the pre-upgrade database during normal
rollback: a database restore would discard newer records. If restoration is necessary,
take a fresh backup and authorize a coordinated database/evidence restore separately.
