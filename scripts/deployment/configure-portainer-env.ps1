$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $ProjectRoot

$EnvPath = "deploy\docker\env\portainer.env"

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

function ConvertFrom-SecureStringPlain {
    param(
        [Parameter(Mandatory = $true)]
        [Security.SecureString]$SecureValue
    )

    $Ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureValue)

    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($Ptr)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Ptr)
    }
}

function Normalize-BaseUrl {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Url
    )

    return $Url.Trim().TrimEnd("/")
}

function Get-PortainerEndpoints {
    param(
        [Parameter(Mandatory = $true)]
        [string]$BaseUrl,

        [Parameter(Mandatory = $true)]
        [string]$Token
    )

    $Headers = @{
        "X-API-Key" = $Token
    }

    try {
        return Invoke-RestMethod `
            -Method Get `
            -Uri "$BaseUrl/api/endpoints" `
            -Headers $Headers `
            -TimeoutSec 20
    }
    catch {
        throw "Unable to query Portainer endpoints at $BaseUrl : $($_.Exception.Message)"
    }
}

function Configure-PortainerSite {
    param(
        [Parameter(Mandatory = $true)]
        [int]$SiteNumber
    )

    Write-Host ""
    Write-Host "Site $SiteNumber"
    Write-Host "------"

    do {
        $Url = Normalize-BaseUrl (Read-Host "Portainer URL")

        if ($Url -notmatch '^https?://') {
            Write-Host "URL must start with http:// or https://"
            $Url = ""
        }
    }
    until (-not [string]::IsNullOrWhiteSpace($Url))

    $SecureToken = Read-Host "Portainer API token" -AsSecureString
    $Token = ConvertFrom-SecureStringPlain $SecureToken

    if ([string]::IsNullOrWhiteSpace($Token)) {
        throw "Portainer API token cannot be empty."
    }

    Write-Host ""
    Write-Host "Querying Portainer endpoints..."

    $Endpoints = @(Get-PortainerEndpoints -BaseUrl $Url -Token $Token)

    if ($Endpoints.Count -eq 0) {
        throw "Portainer returned no endpoints for Site $SiteNumber."
    }

    Write-Host ""
    Write-Host "Available endpoints:"
    $Endpoints |
        Select-Object Id, Name, Type, Status |
        Format-Table -AutoSize

    do {
        $EndpointIdRaw = (Read-Host "Endpoint ID to use for Site $SiteNumber").Trim()

        $EndpointId = 0
        $ValidEndpointId = [int]::TryParse($EndpointIdRaw, [ref]$EndpointId)

        if (-not $ValidEndpointId) {
            Write-Host "Endpoint ID must be an integer."
            continue
        }

        $EndpointExists = @(
            $Endpoints | Where-Object { [int]$_.Id -eq $EndpointId }
        ).Count -gt 0

        if (-not $EndpointExists) {
            Write-Host "Endpoint ID $EndpointId was not returned by Portainer."
        }

    } until ($ValidEndpointId -and $EndpointExists)

    Set-EnvValue `
        -Path $EnvPath `
        -Name "PORTAINER_SITE${SiteNumber}_URL" `
        -Value $Url

    Set-EnvValue `
        -Path $EnvPath `
        -Name "PORTAINER_SITE${SiteNumber}_ENDPOINT_ID" `
        -Value "$EndpointId"

    Set-EnvValue `
        -Path $EnvPath `
        -Name "PORTAINER_SITE${SiteNumber}_TOKEN" `
        -Value $Token

    Write-Host "Site $SiteNumber Portainer configuration saved."
}

Write-Host ""
Write-Host "Configure Portainer"
Write-Host "-------------------"
Write-Host ""
Write-Host "The token is requested securely and is not printed."
Write-Host ""

Configure-PortainerSite -SiteNumber 1
Configure-PortainerSite -SiteNumber 2

Write-Host ""
Write-Host "Portainer configuration completed successfully."
Write-Host "Updated:"
Write-Host "  $EnvPath"
Write-Host ""
Write-Host "Keep these existing defaults unless your deployment requires otherwise:"
Write-Host "  PORTAINER_COLLECTION_MODE=live"
Write-Host "  PORTAINER_API_CONTRACT=docker_proxy_v1"
Write-Host "  PORTAINER_AUTH_TYPE=x_api_key"
