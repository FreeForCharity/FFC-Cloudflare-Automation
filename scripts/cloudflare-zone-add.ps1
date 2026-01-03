[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Domain,

    [Parameter(Mandatory = $true)]
    [string]$AccountId,

    [Parameter(Mandatory = $true)]
    [string]$ApiToken,

    [Parameter()]
    [ValidateSet('full', 'partial')]
    [string]$ZoneType = 'full'
)

$ErrorActionPreference = 'Stop'

function Write-GitHubOutput {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Value
    )

    if ($env:GITHUB_OUTPUT) {
        "{0}={1}" -f $Name, $Value | Out-File -FilePath $env:GITHUB_OUTPUT -Append -Encoding utf8
    }
}

$domainName = $Domain.Trim().ToLowerInvariant()
if ([string]::IsNullOrWhiteSpace($domainName)) { throw 'Domain cannot be empty.' }
if ([string]::IsNullOrWhiteSpace($AccountId)) { throw 'AccountId cannot be empty.' }
if ([string]::IsNullOrWhiteSpace($ApiToken)) { throw 'ApiToken cannot be empty.' }

$headers = @{
    Authorization  = "Bearer $ApiToken"
    'Content-Type' = 'application/json'
}

$baseUri = 'https://api.cloudflare.com/client/v4'

function Invoke-Cloudflare {
    param(
        [Parameter(Mandatory = $true)][ValidateSet('GET', 'POST')][string]$Method,
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter()][object]$Body
    )

    $uri = "$baseUri$Path"
    if ($null -ne $Body) {
        $json = $Body | ConvertTo-Json -Depth 8
        return Invoke-RestMethod -Method $Method -Uri $uri -Headers $headers -Body $json
    }

    return Invoke-RestMethod -Method $Method -Uri $uri -Headers $headers
}

Write-Host "Creating Cloudflare zone for '$domainName' (type=$ZoneType)..."

$created = $false
$zoneId = $null
$nameServers = @()

try {
    $createBody = @{
        name    = $domainName
        account = @{ id = $AccountId }
        type    = $ZoneType
    }

    $resp = Invoke-Cloudflare -Method POST -Path '/zones' -Body $createBody
    if (-not $resp.success) {
        $errs = ($resp.errors | ForEach-Object { "[$($_.code)] $($_.message)" }) -join '; '
        throw "Cloudflare API returned success=false. $errs"
    }

    $created = $true
    $zoneId = $resp.result.id
    $nameServers = @($resp.result.name_servers)
}
catch {
    Write-Host "Create zone failed; checking if zone already exists..." -ForegroundColor Yellow

    $q = [System.Web.HttpUtility]::UrlEncode($domainName)
    $lookupPath = "/zones?name=$q&account.id=$AccountId&per_page=1"
    $existing = Invoke-Cloudflare -Method GET -Path $lookupPath

    if ($existing.success -and $existing.result_info.count -ge 1) {
        $created = $false
        $zoneId = $existing.result[0].id
        $nameServers = @($existing.result[0].name_servers)
    }
    else {
        throw
    }
}

Write-Host "Zone ID: $zoneId"
if ($nameServers -and $nameServers.Count -gt 0) {
    Write-Host 'Assigned name servers:'
    $nameServers | ForEach-Object { Write-Host "- $_" }
}
else {
    Write-Host 'No name servers returned; check the Cloudflare dashboard for details.' -ForegroundColor Yellow
}

Write-GitHubOutput -Name 'created' -Value ([string]$created)
Write-GitHubOutput -Name 'zone_id' -Value ([string]$zoneId)
Write-GitHubOutput -Name 'name_servers' -Value (($nameServers -join ', '))

if ($created) {
    Write-Host ''
    Write-Host 'Next steps:' -ForegroundColor Cyan
    Write-Host '- Update registrar nameservers to the values above.'
    Write-Host '- Wait for DNS propagation, then run 01/02 domain workflows for configuration.'
}
