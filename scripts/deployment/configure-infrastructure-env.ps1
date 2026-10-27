Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $ProjectRoot

$EnvPath = "deploy\docker\env\infrastructure.env"
$SecretsDir = "deploy\docker\secrets"

if (-not (Test-Path $EnvPath)) {
    throw "$EnvPath does not exist."
}

function Read-RequiredValue {
    param([string]$Prompt)

    while ($true) {
        $value = Read-Host $Prompt
        if (-not [string]::IsNullOrWhiteSpace($value)) {
            return $value.Trim()
        }
        Write-Host "Value is required."
    }
}

function Read-WithDefault {
    param(
        [string]$Prompt,
        [string]$Default
    )

    $value = Read-Host "$Prompt [$Default]"
    if ([string]::IsNullOrWhiteSpace($value)) {
        return $Default
    }
    return $value.Trim()
}

function Read-PositiveInt {
    param(
        [string]$Prompt,
        [int]$Default
    )

    while ($true) {
        $raw = Read-Host "$Prompt [$Default]"
        if ([string]::IsNullOrWhiteSpace($raw)) {
            return $Default
        }

        $number = 0
        if ([int]::TryParse($raw, [ref]$number) -and $number -ge 1) {
            return $number
        }

        Write-Host "Enter an integer greater than or equal to 1."
    }
}

function Read-Port {
    param([string]$Prompt)

    while ($true) {
        $raw = Read-Host "$Prompt [22]"
        if ([string]::IsNullOrWhiteSpace($raw)) {
            return 22
        }

        $port = 0
        if ([int]::TryParse($raw, [ref]$port) -and $port -ge 1 -and $port -le 65535) {
            return $port
        }

        Write-Host "Enter a TCP port from 1 to 65535."
    }
}

function Get-ExistingEnvValue {
    param(
        [string]$Path,
        [string]$Name,
        [string]$Default
    )

    foreach ($line in Get-Content $Path) {
        if ($line -match ('^' + [regex]::Escape($Name) + '=(.*)$')) {
            $value = $matches[1].Trim()
            if (-not [string]::IsNullOrWhiteSpace($value) -and $value -notmatch '^<.*>$') {
                return $value
            }
        }
    }

    return $Default
}

function Copy-SecretFile {
    param(
        [string]$Prompt,
        [string]$DestinationName
    )

    $source = Read-RequiredValue $Prompt
    $source = $source.Trim('"')

    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "File does not exist: $source"
    }

    New-Item -ItemType Directory -Force -Path $SecretsDir | Out-Null
    $destination = Join-Path $SecretsDir $DestinationName
    Copy-Item -LiteralPath $source -Destination $destination -Force

    return "/app/secrets/$DestinationName"
}

function Add-ServerInventory {
    param(
        [System.Collections.Generic.List[string]]$Lines,
        [int]$SiteNumber,
        [int]$Count,
        [string]$DefaultTimezone,
        [string]$DefaultChronySource
    )

    $Lines.Add("")
    $Lines.Add("SITE${SiteNumber}_SERVER_COUNT=$Count")

    for ($i = 1; $i -le $Count; $i++) {
        Write-Host ""
        Write-Host "Site $SiteNumber - server $i of $Count"
        Write-Host "-----------------------------"

        $serverId = Read-RequiredValue "Server ID (stable logical name)"
        $hostName = Read-RequiredValue "Hostname or IP"
        $port = Read-Port "SSH port"

        $Lines.Add("SITE${SiteNumber}_SERVER_${i}_ID=$serverId")
        $Lines.Add("SITE${SiteNumber}_SERVER_${i}_HOST=$hostName")
        $Lines.Add("SITE${SiteNumber}_SERVER_${i}_PORT=$port")
        $Lines.Add("SITE${SiteNumber}_SERVER_${i}_REQUIRED=true")

        $override = Read-Host "Different timezone/Chrony source for this server? [y/N]"
        if ($override -match '^(?i)y(es)?$') {
            $tz = Read-WithDefault "Expected timezone" $DefaultTimezone
            $source = Read-WithDefault "Expected selected Chrony source" $DefaultChronySource
            $Lines.Add("SITE${SiteNumber}_SERVER_${i}_CHRONY_TIMEZONE=$tz")
            $Lines.Add("SITE${SiteNumber}_SERVER_${i}_CHRONY_SOURCE=$source")
        }
    }
}

Write-Host ""
Write-Host "Weekend Report - Infrastructure configuration"
Write-Host "============================================="
Write-Host ""
Write-Host "This config supports any positive number of sites and servers per site."
Write-Host "Dynamic mapper filesystem checks and SSH/Chrony thresholds remain in rules.yml."
Write-Host ""

$existingCount = Get-ExistingEnvValue $EnvPath "SITE_COUNT" "2"
$defaultSiteCount = 2
if (-not [int]::TryParse($existingCount, [ref]$defaultSiteCount) -or $defaultSiteCount -lt 1) {
    $defaultSiteCount = 2
}
$siteCount = Read-PositiveInt "Number of runtime sites" $defaultSiteCount
$siteDefinitions = New-Object 'System.Collections.Generic.List[string]'
$siteIds = New-Object 'System.Collections.Generic.HashSet[string]'
for ($siteNumber = 1; $siteNumber -le $siteCount; $siteNumber++) {
    $defaultId = Get-ExistingEnvValue $EnvPath "SITE${siteNumber}_ID" "site$siteNumber"
    do {
        $siteId = Read-WithDefault "Site $siteNumber ID (unique stable name)" $defaultId
        $unique = $siteIds.Add($siteId)
        if (-not $unique) { Write-Host "Site IDs must be unique." }
    } while (-not $unique)
    $defaultName = Get-ExistingEnvValue $EnvPath "SITE${siteNumber}_DISPLAY_NAME" "Site $siteNumber"
    $displayName = Read-WithDefault "Site $siteNumber display name" $defaultName
    $defaultPurpose = Get-ExistingEnvValue $EnvPath "SITE${siteNumber}_PURPOSE" "weekend_report_site"
    $purpose = Read-WithDefault "Site $siteNumber purpose" $defaultPurpose
    $siteDefinitions.Add("")
    $siteDefinitions.Add("SITE${siteNumber}_ID=$siteId")
    $siteDefinitions.Add("SITE${siteNumber}_DISPLAY_NAME=$displayName")
    $siteDefinitions.Add("SITE${siteNumber}_PURPOSE=$purpose")
}
$sshUsername = Read-RequiredValue "SSH username used on the infrastructure servers"

Write-Host ""
do {
    $sshPassword = Read-Host "SSH password (secret; input is hidden)" -AsSecureString
    if ($sshPassword.Length -eq 0) { Write-Host "Password is required." }
} while ($sshPassword.Length -eq 0)

Write-Host ""
Write-Host "Select the pre-verified OpenSSH known_hosts file."
$knownHostsContainerPath = Copy-SecretFile `
    "Windows path to known_hosts" `
    "known_hosts"

Write-Host ""
Write-Host "Chrony values must match the server output exactly."
Write-Host "Timezone command: timedatectl show -p Timezone --value"
Write-Host "Source command:   chronyc sources -n"
Write-Host "Use the source marked with ^*."
Write-Host ""

$chronyTimezone = Read-RequiredValue "Default expected timezone"
$chronySource = Read-RequiredValue "Default expected selected Chrony source"

$lines = New-Object 'System.Collections.Generic.List[string]'
$lines.Add("# Runtime SSH password is secret. Do not commit this file.")
$lines.Add("")
$lines.Add("SITE_COUNT=$siteCount")
$lines.AddRange($siteDefinitions)
$lines.Add("")
$lines.Add("SSH_USERNAME=$sshUsername")

$lines.Add("SSH_KNOWN_HOSTS_PATH=$knownHostsContainerPath")
$lines.Add("")
$lines.Add("CHRONY_TIMEZONE=$chronyTimezone")
$lines.Add("CHRONY_SOURCE=$chronySource")

for ($siteNumber = 1; $siteNumber -le $siteCount; $siteNumber++) {
    $serverCount = Read-PositiveInt "Number of infrastructure servers in Site $siteNumber" 1
    Add-ServerInventory $lines $siteNumber $serverCount $chronyTimezone $chronySource
}

# Convert only when ready to write. Single quotes prevent Compose interpolation.
# The shared ENV parser preserves the contents of these quotes exactly.
$passwordPointer = [IntPtr]::Zero
try {
    $passwordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sshPassword)
    $plainPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPointer)
    if (
        [string]::IsNullOrWhiteSpace($plainPassword) -or
        $plainPassword.Trim() -match '^<.*>$' -or
        $plainPassword.Trim() -eq 'UNKNOWN' -or
        $plainPassword.IndexOfAny([char[]]"'`r`n") -ge 0
    ) {
        throw "Password must be resolved and cannot contain single quotes or line breaks."
    }
    $lines.Add("SSH_PASSWORD='$plainPassword'")
    [System.IO.File]::WriteAllLines(
        (Resolve-Path $EnvPath),
        $lines,
        (New-Object System.Text.UTF8Encoding($false))
    )
} catch {
    # Never render an exception containing the secret-bearing write arguments.
    throw "Could not write infrastructure ENV. Check password format and file permissions."
} finally {
    if ($passwordPointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPointer)
    }
    $plainPassword = $null
    $lines.Clear()
    $sshPassword.Dispose()
}

Write-Host ""
Write-Host "Infrastructure ENV configuration completed."
Write-Host "Ensure Portainer, DOCTOR, and RabbitMQ ENV blocks also cover sites 1 through $siteCount."
Write-Host "Remove module site blocks above SITE_COUNT, then run configuration preflight."
Write-Host "Pre-verified known_hosts was copied to deploy/docker/secrets/known_hosts."
Write-Host "The ENV file uses container paths under /app/secrets."
Write-Host "SSH password was saved without displaying it. Never commit infrastructure.env."
Write-Host ""
Write-Host "IMPORTANT for the final Linux deployment: verify that the container user can read"
Write-Host "known_hosts. Restrict access to env/infrastructure.env; no SSH private key is needed."
