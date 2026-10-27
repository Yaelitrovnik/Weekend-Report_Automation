$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $ProjectRoot

$EnvPath = "deploy\docker\env\app.env"

if (-not (Test-Path $EnvPath)) {
    throw "$EnvPath does not exist."
}

function Set-EnvValue {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Name,

        [Parameter(Mandatory = $true)]
        [string]$Value
    )

    $Lines = [System.Collections.Generic.List[string]]::new()
    Get-Content $Path | ForEach-Object { [void]$Lines.Add($_) }

    $Found = $false

    for ($i = 0; $i -lt $Lines.Count; $i++) {
        if ($Lines[$i] -match "^\Q$Name\E=") {
            $Lines[$i] = "$Name=$Value"
            $Found = $true
            break
        }
    }

    if (-not $Found) {
        [void]$Lines.Add("$Name=$Value")
    }

    $Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllLines(
        (Resolve-Path $Path),
        $Lines,
        $Utf8NoBom
    )
}

function New-RandomSecret {
    param(
        [int]$Bytes = 48
    )

    $Buffer = New-Object byte[] $Bytes
    $Rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()

    try {
        $Rng.GetBytes($Buffer)
    }
    finally {
        $Rng.Dispose()
    }

    return [Convert]::ToBase64String($Buffer)
}

Write-Host ""
Write-Host "Configure Weekend Report application ENV"
Write-Host "----------------------------------------"
Write-Host ""

$PostgresPassword = New-RandomSecret 36
$CsrfSigningKey = New-RandomSecret 48

Set-EnvValue -Path $EnvPath -Name "POSTGRES_PASSWORD" -Value $PostgresPassword
Set-EnvValue `
    -Path $EnvPath `
    -Name "WEEKEND_REPORT_DATABASE_URL" `
    -Value "postgresql://weekend_report:$PostgresPassword@postgres:5432/weekend_report"

Set-EnvValue `
    -Path $EnvPath `
    -Name "WEEKEND_REPORT_CSRF_SIGNING_KEY" `
    -Value $CsrfSigningKey

Write-Host ""
Write-Host "Secrets were not printed."
Write-Host ""
Write-Host "Leave these for the final release/build step:"
Write-Host "  WEEKEND_REPORT_APP_VERSION"
Write-Host "  WEEKEND_REPORT_BUILD_ID"
