[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Domain,

    [Parameter()]
    [string]$AccessToken
)

$ErrorActionPreference = 'Stop'

function Invoke-Graph {
    param(
        [Parameter(Mandatory = $true)][string]$Method,
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][string]$Token,
        [Parameter()][object]$Body
    )

    $headers = @{ Authorization = "Bearer $Token" }

    if ($null -eq $Body) {
        return Invoke-RestMethod -Method $Method -Uri $Uri -Headers $headers -ErrorAction Stop
    }

    return Invoke-RestMethod -Method $Method -Uri $Uri -Headers $headers -Body ($Body | ConvertTo-Json -Depth 10) -ContentType 'application/json' -ErrorAction Stop
}

function Get-GraphAccessToken {
    param([AllowNull()][string]$Token)

    $tokenToUse = if ($Token) { $Token } else { $env:GRAPH_ACCESS_TOKEN }
    if (-not [string]::IsNullOrWhiteSpace($tokenToUse)) {
        return $tokenToUse
    }

    $az = Get-Command az -ErrorAction SilentlyContinue
    if (-not $az) {
        throw 'No AccessToken provided and GRAPH_ACCESS_TOKEN is not set. Install Azure CLI (az) or pass -AccessToken.'
    }

    $tokenToUse = (az account get-access-token --resource-type ms-graph --query accessToken -o tsv)
    if ([string]::IsNullOrWhiteSpace($tokenToUse)) {
        throw 'Failed to acquire a Microsoft Graph access token via Azure CLI.'
    }

    return $tokenToUse
}

function Get-DomainIfExists {
    param(
        [Parameter(Mandatory = $true)][string]$Domain,
        [Parameter(Mandatory = $true)][string]$Token
    )

    $encoded = [Uri]::EscapeDataString($Domain)
    $uri = "https://graph.microsoft.com/v1.0/domains/$encoded`?$select=id,isDefault,isVerified,supportedServices"

    try {
        return Invoke-Graph -Method 'GET' -Uri $uri -Token $Token -Body $null
    }
    catch {
        $resp = $_.Exception.Response
        if ($resp -and $resp.StatusCode -and ([int]$resp.StatusCode -eq 404)) {
            return $null
        }
        throw
    }
}

try {
    $Domain = $Domain.Trim().ToLowerInvariant()
    if ([string]::IsNullOrWhiteSpace($Domain)) { throw 'Domain is required.' }

    $token = Get-GraphAccessToken -Token $AccessToken

    Write-Host 'M365 - Add tenant domain (Graph)' -ForegroundColor Cyan
    Write-Host ("Domain: {0}" -f $Domain)

    $existing = Get-DomainIfExists -Domain $Domain -Token $token

    if (-not $existing) {
        Write-Host 'Domain not found in tenant; creating...' -ForegroundColor Yellow
        $created = Invoke-Graph -Method 'POST' -Uri 'https://graph.microsoft.com/v1.0/domains' -Token $token -Body @{ id = $Domain }
        $existing = $created
        Write-Host 'Domain created.' -ForegroundColor Green
    }
    else {
        Write-Host 'Domain already exists in tenant.' -ForegroundColor Green
    }

    Write-Host ("IsVerified: {0}" -f $existing.isVerified)
    Write-Host ("IsDefault: {0}" -f $existing.isDefault)

    $encoded = [Uri]::EscapeDataString($Domain)

    Write-Host ''
    Write-Host 'verificationDnsRecords' -ForegroundColor Cyan
    $verification = (Invoke-Graph -Method 'GET' -Uri "https://graph.microsoft.com/v1.0/domains/$encoded/verificationDnsRecords" -Token $token -Body $null).value
    ($verification | ConvertTo-Json -Depth 8)

    Write-Host ''
    Write-Host 'serviceConfigurationRecords' -ForegroundColor Cyan
    $service = (Invoke-Graph -Method 'GET' -Uri "https://graph.microsoft.com/v1.0/domains/$encoded/serviceConfigurationRecords" -Token $token -Body $null).value
    ($service | ConvertTo-Json -Depth 8)

    exit 0
}
catch {
    Write-Error $_
    exit 1
}
