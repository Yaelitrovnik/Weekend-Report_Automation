$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $ProjectRoot

$RuntimeFiles = @(
    "deploy\docker\.env",
    "deploy\docker\env\app.env",
    "deploy\docker\env\portainer.env",
    "deploy\docker\env\doctor.env",
    "deploy\docker\env\rabbitmq.env",
    "deploy\docker\env\infrastructure.env",
    "deploy\docker\env\splunk.env"
)

$ReleaseLater = @(
    "WEEKEND_REPORT_APP_VERSION",
    "WEEKEND_REPORT_BUILD_ID"
)

$MissingFiles = @()
$MustConfigure = @()
$DeferredRelease = @()

function Get-EnvValue {
    param(
        [string]$Path,
        [string]$Name
    )

    if (-not (Test-Path $Path)) {
        return $null
    }

    foreach ($Line in Get-Content $Path) {
        if ($Line -match ('^\s*' + [regex]::Escape($Name) + '=(.*)$')) {
            return $matches[1].Trim()
        }
    }

    return $null
}

foreach ($File in $RuntimeFiles) {
    if (-not (Test-Path $File)) {
        $MissingFiles += $File
        continue
    }

    foreach ($Line in Get-Content $File) {
        if ($Line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
            $Name = $matches[1]
            $Value = $matches[2]
            if ($Value -match '<TBD>|<TO_IMPLEMENT>|<TO_VERIFY>|<VERIFY_[^>]*>') {
                $Item = [PSCustomObject]@{ File=$File; Variable=$Name }
                if ($ReleaseLater -contains $Name) { $DeferredRelease += $Item }
                else { $MustConfigure += $Item }
            }
        }
    }
}

$InfrastructureEnv = "deploy\docker\env\infrastructure.env"

if (Test-Path $InfrastructureEnv) {
    foreach ($Name in @(
        "SSH_USERNAME",
        "SSH_PASSWORD",
        "SSH_KNOWN_HOSTS_PATH"
    )) {
        $Value = Get-EnvValue $InfrastructureEnv $Name

        if (
            [string]::IsNullOrWhiteSpace($Value) -or
            $Value -match '^<.*>$'
        ) {
            $AlreadyReported = @(
                $MustConfigure |
                    Where-Object {
                        $_.File -eq $InfrastructureEnv -and
                        $_.Variable -eq $Name
                    }
            ).Count -gt 0

            if (-not $AlreadyReported) {
                $MustConfigure += [PSCustomObject]@{
                    File = $InfrastructureEnv
                    Variable = $Name
                }
            }
        }
    }
}

Write-Host ""
Write-Host "=== Runtime configuration scan ==="

if ($MissingFiles.Count -gt 0) {
    Write-Host ""
    Write-Host "MISSING RUNTIME FILES:"
    $MissingFiles | ForEach-Object { Write-Host "  $_" }
} else {
    Write-Host "All expected runtime ENV files exist."
}

Write-Host ""
Write-Host "MUST CONFIGURE NOW:"
if ($MustConfigure.Count -eq 0) { Write-Host "  none" }
else { $MustConfigure | ForEach-Object { Write-Host "  $($_.File) :: $($_.Variable)" } }

Write-Host ""
Write-Host "DEFERRED - FINAL RELEASE/BUILD VALUES:"
if ($DeferredRelease.Count -eq 0) { Write-Host "  none" }
else { $DeferredRelease | ForEach-Object { Write-Host "  $($_.File) :: $($_.Variable)" } }

Write-Host ""
Write-Host "=== Policy source check ==="
$RootRules = "config\rules.yml"
$DeployRules = "deploy\docker\config\rules.yml"
if ((-not (Test-Path $RootRules)) -and (Test-Path $DeployRules)) {
    Write-Host "PASS: deploy\docker\config\rules.yml is the single runtime policy source."
} elseif (Test-Path $RootRules) {
    Write-Host "FAIL: obsolete duplicate policy file still exists: config\rules.yml"
} else {
    Write-Host "FAIL: runtime policy file is missing: deploy\docker\config\rules.yml"
}

Write-Host ""
Write-Host "=== Removed hardcoded inventory checks ==="
$Needles = @("DOCTOR_EXPECTED_SERVICES", "image.reference", "image.comparison")
foreach ($Needle in $Needles) {
    $hits = @(Get-ChildItem app,deploy,tests -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -notmatch '\\.git\\' } |
        Select-String -SimpleMatch $Needle -ErrorAction SilentlyContinue)
    if ($hits.Count -eq 0) { Write-Host "PASS: no active reference to '$Needle'." }
    else {
        $nonGuard = @($hits | Where-Object { $_.Path -notmatch 'test_' })
        if ($nonGuard.Count -eq 0) { Write-Host "PASS: '$Needle' appears only in regression tests guarding against reintroduction." }
        else {
            Write-Host "CHECK: '$Needle' still appears outside regression tests:"
            $nonGuard | ForEach-Object { Write-Host "  $($_.Path):$($_.LineNumber)" }
        }
    }
}

Write-Host ""
Write-Host "=== Shape validation using all runtime ENV files ==="
$ShapeExit = $null
if ($MissingFiles.Count -gt 0) {
    Write-Host "SKIP: runtime ENV files are not all present yet."
    Write-Host "      Run shape validation after deployment configuration files are generated."
} else {
    $EnvArgs = @()
    foreach ($File in $RuntimeFiles | Where-Object { $_ -like 'deploy\docker\env\*.env' }) {
        $EnvArgs += "--env-file"
        $EnvArgs += $File
    }
    & python scripts\validate_config.py --config deploy/docker/config --no-production-preflight @EnvArgs
    $ShapeExit = $LASTEXITCODE
    if ($ShapeExit -eq 0) { Write-Host "PASS: configuration shape validation passed." }
    else { Write-Host "FAIL: configuration shape validation failed with exit code $ShapeExit." }
}

Write-Host ""
Write-Host "=== Git protection check ==="
foreach ($File in $RuntimeFiles) {
    if (Test-Path $File) {
        & git check-ignore -q -- $File
        if ($LASTEXITCODE -eq 0) { Write-Host "PASS: ignored: $File" }
        else { Write-Host "CHECK: not ignored: $File" }
    }
}

Write-Host ""
if ($MissingFiles.Count -gt 0) {
    Write-Host "PRE-DEPLOYMENT INCOMPLETE:"
    Write-Host "Runtime ENV files have not been generated yet."
    Write-Host "This is expected on the development machine."
    Write-Host "Deployment-time validation must be rerun after ENV generation."
    if ($MustConfigure.Count -gt 0) {
        Write-Host "Existing runtime ENV files also contain MUST CONFIGURE NOW items."
    }
} elseif ($MustConfigure.Count -eq 0 -and $ShapeExit -eq 0) {
    Write-Host "RESULT: runtime ENV configuration has no unresolved values that must be filled now."
    Write-Host "Remaining deferred values are the release/build values shown above."
} else {
    Write-Host "RESULT: unresolved deployment configuration remains. Review MUST CONFIGURE NOW and validation failures above."
}
