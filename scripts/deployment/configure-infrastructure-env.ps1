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
Write-Host "This config supports any positive number of servers per site."
Write-Host "The root filesystem check and SSH/Chrony thresholds remain in rules.yml."
Write-Host ""

$site1Id = Get-ExistingEnvValue $EnvPath "SITE1_ID" "site1"
$site2Id = Get-ExistingEnvValue $EnvPath "SITE2_ID" "site2"

$site1DisplayName = Read-WithDefault "Site 1 display name" "Site 1"
$site2DisplayName = Read-WithDefault "Site 2 display name" "Site 2"
$sshUsername = Read-RequiredValue "SSH username used on the infrastructure servers"

Write-Host ""
Write-Host "Select the SSH private key used by Weekend Report."
$privateKeyContainerPath = Copy-SecretFile `
    "Windows path to the SSH private key" `
    "ssh_private_key"

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

$site1Count = Read-PositiveInt "Number of infrastructure servers in Site 1" 1
$site2Count = Read-PositiveInt "Number of infrastructure servers in Site 2" 1

$lines = New-Object 'System.Collections.Generic.List[string]'
$lines.Add("# Site definitions, SSH identity paths, and dynamic server inventories.")
$lines.Add("")
$lines.Add("SITE_COUNT=2")
$lines.Add("")
$lines.Add("SITE1_ID=$site1Id")
$lines.Add("SITE1_DISPLAY_NAME=$site1DisplayName")
$lines.Add("SITE1_PURPOSE=weekend_report_site")
$lines.Add("")
$lines.Add("SITE2_ID=$site2Id")
$lines.Add("SITE2_DISPLAY_NAME=$site2DisplayName")
$lines.Add("SITE2_PURPOSE=weekend_report_site")
$lines.Add("")
$lines.Add("SSH_USERNAME=$sshUsername")
$lines.Add("SSH_PRIVATE_KEY_PATH=$privateKeyContainerPath")
$lines.Add("SSH_KNOWN_HOSTS_PATH=$knownHostsContainerPath")
$lines.Add("")
$lines.Add("CHRONY_TIMEZONE=$chronyTimezone")
$lines.Add("CHRONY_SOURCE=$chronySource")

Add-ServerInventory $lines 1 $site1Count $chronyTimezone $chronySource
Add-ServerInventory $lines 2 $site2Count $chronyTimezone $chronySource

[System.IO.File]::WriteAllLines(
    (Resolve-Path $EnvPath),
    $lines,
    (New-Object System.Text.UTF8Encoding($false))
)

Write-Host ""
Write-Host "Infrastructure ENV configuration completed."
Write-Host "Runtime SSH files were copied to deploy/docker/secrets/."
Write-Host "The ENV file uses container paths under /app/secrets."
Write-Host "No private-key contents were displayed."
Write-Host ""
Write-Host "IMPORTANT for the final Linux deployment: verify that the container user can read"
Write-Host "the private key while the key still has restrictive OpenSSH permissions."
