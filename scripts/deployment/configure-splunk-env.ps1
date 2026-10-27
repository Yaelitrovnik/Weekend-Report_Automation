$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $ProjectRoot

$EnvPath = "deploy\docker\env\splunk.env"

if (-not (Test-Path $EnvPath)) {
    throw "$EnvPath does not exist."
}

function Read-BooleanChoice {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Prompt,

        [Parameter(Mandatory = $true)]
        [bool]$Default
    )

    $Suffix = if ($Default) { "[Y/n]" } else { "[y/N]" }

    while ($true) {
        $Raw = (Read-Host "$Prompt $Suffix").Trim().ToLowerInvariant()

        if ([string]::IsNullOrWhiteSpace($Raw)) {
            return $Default
        }

        switch ($Raw) {
            "y"     { return $true }
            "yes"   { return $true }
            "true"  { return $true }
            "n"     { return $false }
            "no"    { return $false }
            "false" { return $false }
            default {
                Write-Host "Enter y/yes/true or n/no/false."
            }
        }
    }
}

Write-Host ""
Write-Host "Configure Splunk dashboards"
Write-Host "---------------------------"

do {
    $Raw = Read-Host "How many Splunk dashboards should be reviewed?"
    $DashboardCount = 0
    $Valid = [int]::TryParse($Raw, [ref]$DashboardCount)

    if (-not $Valid -or $DashboardCount -lt 1) {
        Write-Host "Enter a positive integer."
    }
} until ($Valid -and $DashboardCount -ge 1)

$Dashboards = @()
$UsedIds = @{}
$UsedOrders = @{}

for ($i = 1; $i -le $DashboardCount; $i++) {
    Write-Host ""
    Write-Host "Dashboard $i"
    Write-Host "-----------"

    do {
        $Id = (Read-Host "Dashboard ID (short unique name)").Trim()

        if ([string]::IsNullOrWhiteSpace($Id)) {
            Write-Host "Dashboard ID cannot be empty."
            continue
        }

        if ($UsedIds.ContainsKey($Id)) {
            Write-Host "Dashboard ID '$Id' is already used."
            $Id = ""
            continue
        }
    } until (-not [string]::IsNullOrWhiteSpace($Id))

    $UsedIds[$Id] = $true

    do {
        $DisplayName = (Read-Host "Display name").Trim()
    } until (-not [string]::IsNullOrWhiteSpace($DisplayName))

    do {
        $Url = (Read-Host "Full Splunk dashboard URL").Trim()

        if ($Url -notmatch '^https?://') {
            Write-Host "URL must start with http:// or https://"
            $Url = ""
        }
    } until (-not [string]::IsNullOrWhiteSpace($Url))

    $RequiredReview = Read-BooleanChoice `
        -Prompt "Require this dashboard to be explicitly reviewed before APPROVE?" `
        -Default $true

    $NoteRequired = Read-BooleanChoice `
        -Prompt "Require a non-empty reviewer note for this dashboard?" `
        -Default $false

    do {
        $RawOrder = (
            Read-Host "Display order [default: $i]"
        ).Trim()

        $Order = 0
        $ValidOrder = $false

        if ([string]::IsNullOrWhiteSpace($RawOrder)) {
            $Order = $i
            $ValidOrder = $true
        }
        else {
            $ValidOrder = [int]::TryParse(
                $RawOrder,
                [ref]$Order
            )
        }

        if (-not $ValidOrder -or $Order -lt 1) {
            Write-Host "Order must be a positive integer."
            $ValidOrder = $false
            continue
        }

        if ($UsedOrders.ContainsKey($Order)) {
            Write-Host "Order '$Order' is already used."
            $ValidOrder = $false
            continue
        }
    } until ($ValidOrder)

    $UsedOrders[$Order] = $true

    $Dashboards += [PSCustomObject]@{
        Id             = $Id
        DisplayName    = $DisplayName
        Url            = $Url
        RequiredReview = $RequiredReview
        NoteRequired   = $NoteRequired
        Order          = $Order
    }
}

$Lines = @(
    "# Splunk dashboard review targets.",
    "",
    "SPLUNK_DASHBOARD_COUNT=$DashboardCount",
    ""
)

for ($i = 0; $i -lt $Dashboards.Count; $i++) {
    $n = $i + 1
    $Dashboard = $Dashboards[$i]

    $RequiredReviewText = if ($Dashboard.RequiredReview) {
        "true"
    }
    else {
        "false"
    }

    $NoteRequiredText = if ($Dashboard.NoteRequired) {
        "true"
    }
    else {
        "false"
    }

    $Lines += "SPLUNK_DASHBOARD_${n}_ID=$($Dashboard.Id)"
    $Lines += "SPLUNK_DASHBOARD_${n}_DISPLAY_NAME=$($Dashboard.DisplayName)"
    $Lines += "SPLUNK_DASHBOARD_${n}_URL=$($Dashboard.Url)"
    $Lines += "SPLUNK_DASHBOARD_${n}_REQUIRED_REVIEW=$RequiredReviewText"
    $Lines += "SPLUNK_DASHBOARD_${n}_NOTE_REQUIRED=$NoteRequiredText"
    $Lines += "SPLUNK_DASHBOARD_${n}_ORDER=$($Dashboard.Order)"
    $Lines += ""
}

[System.IO.File]::WriteAllLines(
    (Resolve-Path $EnvPath),
    $Lines,
    (New-Object System.Text.UTF8Encoding($false))
)

Write-Host ""
Write-Host "Splunk configuration completed. $DashboardCount dashboard(s) written."