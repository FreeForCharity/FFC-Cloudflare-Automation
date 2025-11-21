<#
.SYNOPSIS
    One-time Cloudflare DNS updater for staging.clarkemoyer.com
.DESCRIPTION
    Updates or creates the A record for staging.clarkemoyer.com using a Cloudflare API token.
.PARAMETER NewIp
    New IPv4 address for the staging subdomain.
.PARAMETER Token
    Cloudflare API token. If omitted you will be securely prompted.
.PARAMETER DryRun
    Show intended action without performing API write.
.EXAMPLE
    ./Update-StagingDns.ps1 -NewIp 203.0.113.42
.EXAMPLE
    $env:CLOUDFLARE_API_TOKEN = "token"; ./Update-StagingDns.ps1 -NewIp 203.0.113.42
.EXAMPLE
    ./Update-StagingDns.ps1 -NewIp 203.0.113.42 -DryRun
.NOTES
    Requires PowerShell 5.1+ and network access.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)]
    [string]$NewIp,
    [string]$Token,
    [switch]$DryRun,
    [switch]$Proxied
)

$ErrorActionPreference = 'Stop'
$RootDomain = 'clarkemoyer.com'
$SubDomain = 'staging'
$Fqdn = "$SubDomain.$RootDomain"
$ApiBase = 'https://api.cloudflare.com/client/v4'

function Get-PlainToken {
    param([string]$Provided)
    if ($Provided) { return $Provided.Trim() }
    if ($env:CLOUDFLARE_API_TOKEN) { return $env:CLOUDFLARE_API_TOKEN.Trim() }
    $secure = Read-Host 'Enter Cloudflare API Token' -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

function Validate-IPv4 {
    param([string]$Ip)
    $out = $null
    if (-not [System.Net.IPAddress]::TryParse($Ip, [ref]$out)) {
        throw "Invalid IPv4 address: $Ip"
    }
    if ($Ip -notmatch '^(?:\d{1,3}\.){3}\d{1,3}$') { throw "IPv4 format error: $Ip" }
}

function Invoke-CfGet {
    param([string]$Path, [string]$Token, [hashtable]$Query)
    $headers = @{ Authorization = "Bearer $Token" }
    $uri = "$ApiBase$Path"
    if ($Query -and $Query.Count -gt 0) {
        $queryString = ($Query.GetEnumerator() | ForEach-Object { [System.Uri]::EscapeDataString($_.Key) + '=' + [System.Uri]::EscapeDataString($_.Value) }) -join '&'
        $uri = "$uri?$queryString"
    }
    $resp = Invoke-RestMethod -Method Get -Uri $uri -Headers $headers -Body $null -ErrorAction Stop -Verbose:$false -TimeoutSec 30 -ContentType 'application/json'
    if (-not $resp.success) { throw "GET $Path failed: $(ConvertTo-Json $resp)" }
    return $resp
}

function Invoke-CfPatch {
    param([string]$Path, [string]$Token, [hashtable]$Payload)
    $headers = @{ Authorization = "Bearer $Token"; 'Content-Type' = 'application/json' }
    $uri = "$ApiBase$Path"
    $resp = Invoke-RestMethod -Method Patch -Uri $uri -Headers $headers -Body ($Payload | ConvertTo-Json -Depth 5) -ErrorAction Stop -TimeoutSec 30 -Verbose:$false
    if (-not $resp.success) { throw "PATCH $Path failed: $(ConvertTo-Json $resp)" }
    return $resp
}

function Invoke-CfPost {
    param([string]$Path, [string]$Token, [hashtable]$Payload)
    $headers = @{ Authorization = "Bearer $Token"; 'Content-Type' = 'application/json' }
    $uri = "$ApiBase$Path"
    $resp = Invoke-RestMethod -Method Post -Uri $uri -Headers $headers -Body ($Payload | ConvertTo-Json -Depth 5) -ErrorAction Stop -TimeoutSec 30 -Verbose:$false
    if (-not $resp.success) { throw "POST $Path failed: $(ConvertTo-Json $resp)" }
    return $resp
}

try {
    Validate-IPv4 -Ip $NewIp
    $plainToken = Get-PlainToken -Provided $Token
    if (-not $plainToken) { throw 'Cloudflare API token is required.' }

    Write-Host "Retrieving zone id for $RootDomain..." -ForegroundColor Cyan
    $zoneResp = Invoke-CfGet -Path '/zones' -Token $plainToken -Query @{ name = $RootDomain }
    $zoneId = $zoneResp.result[0].id
    if (-not $zoneId) { throw "Zone not found for $RootDomain" }

    Write-Host "Looking up existing DNS record for $Fqdn..." -ForegroundColor Cyan
    $recordResp = Invoke-CfGet -Path "/zones/$zoneId/dns_records" -Token $plainToken -Query @{ type = 'A'; name = $Fqdn }
    $existing = $recordResp.result

    $payload = @{ type = 'A'; name = $Fqdn; content = $NewIp; ttl = 120; proxied = [bool]$Proxied }

    if ($existing.Count -gt 0) {
        Write-Host "Found $($existing.Count) existing A record(s) for $Fqdn" -ForegroundColor Cyan
        foreach ($rec in $existing) {
            $needsIp = $rec.content -ne $NewIp
            $needsProxy = $rec.proxied -ne [bool]$Proxied
            if (-not $needsIp -and -not $needsProxy) {
                Write-Host "  id=$($rec.id) unchanged ip=$($rec.content) proxied=$($rec.proxied)" -ForegroundColor DarkGray
                continue
            }
            if ($DryRun) {
                Write-Host "  DRY-RUN id=$($rec.id) old_ip=$($rec.content) -> new_ip=$NewIp old_proxied=$($rec.proxied) -> new_proxied=$([bool]$Proxied)" -ForegroundColor Yellow
                continue
            }
            Write-Host "  Updating id=$($rec.id)" -ForegroundColor Yellow
            $upd = Invoke-CfPatch -Path "/zones/$zoneId/dns_records/$($rec.id)" -Token $plainToken -Payload $payload
            Write-Host "    Updated ip: $($rec.content) -> $($upd.result.content) proxied: $($rec.proxied) -> $($upd.result.proxied)" -ForegroundColor Green
        }
    } else {
        if ($DryRun) {
            Write-Host "DRY-RUN: Would create new A record for $Fqdn with $NewIp proxied=$([bool]$Proxied)" -ForegroundColor Yellow
        } else {
            Write-Host "Creating new A record for $Fqdn with $NewIp proxied=$([bool]$Proxied)" -ForegroundColor Yellow
            $create = Invoke-CfPost -Path "/zones/$zoneId/dns_records" -Token $plainToken -Payload $payload
            Write-Host "Created: $($create.result.name) -> $($create.result.content) proxied=$($create.result.proxied)" -ForegroundColor Green
        }
    }
}
catch {
    Write-Error $_
    exit 1
}
