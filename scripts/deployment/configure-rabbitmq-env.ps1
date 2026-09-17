$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $ProjectRoot

$EnvPath = "deploy\docker\env\rabbitmq.env"
$SecretsDir = "deploy\docker\secrets"

if (-not (Test-Path $EnvPath)) {
    throw "$EnvPath does not exist."
}

if (-not (Test-Path $SecretsDir)) {
    New-Item -ItemType Directory -Path $SecretsDir | Out-Null
}

function Set-EnvValue {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Name,

        [AllowEmptyString()]
        [string]$Value
    )

    $Lines = [System.Collections.Generic.List[string]]::new()

    Get-Content $Path | ForEach-Object {
        [void]$Lines.Add($_)
    }

    $Found = $false

    for ($i = 0; $i -lt $Lines.Count; $i++) {
        if ($Lines[$i] -match ("^" + [regex]::Escape($Name) + "=")) {
            $Lines[$i] = "$Name=$Value"
            $Found = $true
            break
        }
    }

    if (-not $Found) {
        throw "$Name was not found in $Path"
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

function Read-TlsMode {
    param(
        [Parameter(Mandatory = $true)]
        [int]$SiteNumber
    )

    do {
        Write-Host ""
        Write-Host "TLS verification for Site $SiteNumber"
        Write-Host "  1 = normal trusted CA store"
        Write-Host "  2 = custom/internal CA"

        $Choice = (Read-Host "Select 1 or 2").Trim()

        switch ($Choice) {
            "1" {
                return @{
                    Mode = "true"
                    CaFile = ""
                    LocalCaFile = ""
                }
            }

            "2" {
                $LocalCaFile = (Read-Host "Local path to RabbitMQ CA certificate").Trim()

                if (-not (Test-Path -LiteralPath $LocalCaFile -PathType Leaf)) {
                    Write-Host "CA file does not exist: $LocalCaFile"
                    continue
                }

                $DestinationName = "rabbitmq-site${SiteNumber}-ca.pem"
                $DestinationPath = Join-Path $SecretsDir $DestinationName

                Copy-Item `
                    -LiteralPath $LocalCaFile `
                    -Destination $DestinationPath `
                    -Force

                return @{
                    Mode = "custom_ca"
                    CaFile = "/app/secrets/$DestinationName"
                    LocalCaFile = (Resolve-Path $DestinationPath).Path
                }
            }

            default {
                Write-Host "Enter 1 or 2."
            }
        }
    }
    while ($true)
}

function Test-RabbitMQApi {
    param(
        [Parameter(Mandatory = $true)]
        [string]$BaseUrl,

        [Parameter(Mandatory = $true)]
        [string]$Username,

        [Parameter(Mandatory = $true)]
        [string]$Password,

        [Parameter(Mandatory = $true)]
        [string]$TlsMode,

        [AllowEmptyString()]
        [string]$LocalCaFile
    )

    $Pair = "${Username}:${Password}"
    $Bytes = [System.Text.Encoding]::UTF8.GetBytes($Pair)
    $BasicAuth = [Convert]::ToBase64String($Bytes)

    $Headers = @{
        Authorization = "Basic $BasicAuth"
        Accept = "application/json"
    }

    try {
        if ($TlsMode -eq "true") {
            Write-Host "Testing $BaseUrl/api/queues ..."

            Invoke-RestMethod `
                -Uri "$BaseUrl/api/queues" `
                -Headers $Headers `
                -Method Get `
                -TimeoutSec 20 |
                Out-Null

            Write-Host "PASS: queues API"

            Write-Host "Testing $BaseUrl/api/nodes ..."

            Invoke-RestMethod `
                -Uri "$BaseUrl/api/nodes" `
                -Headers $Headers `
                -Method Get `
                -TimeoutSec 20 |
                Out-Null

            Write-Host "PASS: nodes API"

            return
        }

        if ($TlsMode -eq "custom_ca") {
            $Curl = Get-Command curl.exe -ErrorAction SilentlyContinue

            if ($null -eq $Curl) {
                throw "curl.exe is required to test a custom RabbitMQ CA."
            }

            foreach ($ApiPath in @("/api/queues", "/api/nodes")) {
                Write-Host "Testing $BaseUrl$ApiPath with custom CA ..."

                & curl.exe `
                    --silent `
                    --show-error `
                    --fail `
                    --cacert $LocalCaFile `
                    --user "${Username}:${Password}" `
                    --header "Accept: application/json" `
                    "$BaseUrl$ApiPath" `
                    --output NUL

                if ($LASTEXITCODE -ne 0) {
                    throw "RabbitMQ API test failed for $ApiPath."
                }

                Write-Host "PASS: $ApiPath"
            }

            return
        }

        throw "Unsupported TLS mode: $TlsMode"
    }
    finally {
        $Pair = $null
        $BasicAuth = $null
    }
}

function Configure-RabbitMQSite {
    param(
        [Parameter(Mandatory = $true)]
        [int]$SiteNumber
    )

    Write-Host ""
    Write-Host "RabbitMQ Site $SiteNumber"
    Write-Host "--------------------"

    $Url = (Read-Host "Management HTTPS base URL (without /api)").Trim().TrimEnd("/")

    if ([string]::IsNullOrWhiteSpace($Url)) {
        throw "URL cannot be empty."
    }

    if ($Url -match "/api$") {
        throw "Use the RabbitMQ Management base URL WITHOUT /api."
    }

    if ($Url -notmatch "^https://") {
        throw "Production RabbitMQ URL must start with https://."
    }

    $Username = (Read-Host "Username").Trim()

    if ([string]::IsNullOrWhiteSpace($Username)) {
        throw "Username cannot be empty."
    }

    $SecurePassword = Read-Host "Password" -AsSecureString
    $Password = ConvertFrom-SecureStringPlain $SecurePassword

    try {
        if ([string]::IsNullOrWhiteSpace($Password)) {
            throw "Password cannot be empty."
        }

        $Tls = Read-TlsMode -SiteNumber $SiteNumber

        Test-RabbitMQApi `
            -BaseUrl $Url `
            -Username $Username `
            -Password $Password `
            -TlsMode $Tls.Mode `
            -LocalCaFile $Tls.LocalCaFile

        $Prefix = "RABBITMQ_SITE$SiteNumber"

        Set-EnvValue `
            -Path $EnvPath `
            -Name "${Prefix}_URL" `
            -Value $Url

        Set-EnvValue `
            -Path $EnvPath `
            -Name "${Prefix}_USER" `
            -Value $Username

        Set-EnvValue `
            -Path $EnvPath `
            -Name "${Prefix}_PASSWORD" `
            -Value $Password

        Set-EnvValue `
            -Path $EnvPath `
            -Name "${Prefix}_TLS_VERIFY" `
            -Value $Tls.Mode

        Set-EnvValue `
            -Path $EnvPath `
            -Name "${Prefix}_CA_FILE" `
            -Value $Tls.CaFile

        Write-Host ""
        Write-Host "RabbitMQ Site $SiteNumber configured successfully."
    }
    finally {
        $Password = $null
        $SecurePassword = $null
    }
}

Write-Host ""
Write-Host "Configure RabbitMQ"
Write-Host "------------------"
Write-Host ""
Write-Host "RabbitMQ Management API access is read-only."
Write-Host "HTTPS is required."
Write-Host "Credentials are not displayed."

Configure-RabbitMQSite -SiteNumber 1
Configure-RabbitMQSite -SiteNumber 2

Write-Host ""
Write-Host "RabbitMQ configuration completed successfully."
Write-Host "Updated:"
Write-Host "  $EnvPath"