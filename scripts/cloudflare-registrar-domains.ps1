<#
.SYNOPSIS
    READ-ONLY: list the domains registered in the FFC Cloudflare Registrar
    account (the authoritative "we own this domain at Cloudflare" source).

.DESCRIPTION
    Calls the Cloudflare Registrar API (GET /accounts/{id}/registrar/domains)
    and writes a JSON array of domain names. This is the correct source for the
    pid-39 "Domain Registered in Cloudflare" product alignment -- distinct from
    "in our Cloudflare DNS", which does not imply registrar ownership (most
    DNS-managed domains are still registered at eNom).

    The token comes from the env var CLOUDFLARE_API_TOKEN_FFC (loaded by the
    cloudflare-tokens-from-kv composite action) or -ApiToken. The account id is a
    non-secret GUID.

.PARAMETER AccountId
    Cloudflare account id. Default is the Free For Charity account.

.PARAMETER OutputFile
    JSON output path. Default 'artifacts/cloudflare/registrar_domains.json'.

.OUTPUTS
    Writes -OutputFile (sorted JSON array of names) and a count on stdout.
#>
[CmdletBinding()]
param(
    [Parameter()]
    [string]$AccountId = '0fa33828a8a294ba7c3e945cec827f12',

    [Parameter()]
    [string]$ApiToken,

    [Parameter()]
    [string]$OutputFile = 'artifacts/cloudflare/registrar_domains.json'
)

$ErrorActionPreference = 'Stop'

$token = if ($ApiToken) { $ApiToken } else { $env:CLOUDFLARE_API_TOKEN_FFC }
if ([string]::IsNullOrWhiteSpace($token)) {
    throw 'Missing Cloudflare token: pass -ApiToken or set CLOUDFLARE_API_TOKEN_FFC.'
}

$headers = @{ Authorization = "Bearer $token" }
$names = New-Object System.Collections.Generic.List[string]
# NB: this endpoint is 0-indexed (result_info.page starts at 0); a page=1 first
# call returns an empty set. Start at 0.
$page = 0
while ($true) {
    $uri = "https://api.cloudflare.com/client/v4/accounts/$AccountId/registrar/domains?page=$page&per_page=50"
    $resp = Invoke-RestMethod -Method Get -Uri $uri -Headers $headers -TimeoutSec 30
    if (-not $resp.success) {
        throw "Cloudflare registrar API error: $($resp.errors | ConvertTo-Json -Compress)"
    }
    $batch = @($resp.result)
    foreach ($d in $batch) {
        if ($d.name) { $names.Add([string]$d.name) }
    }
    $info = $resp.result_info
    $totalPages = if ($info -and $info.total_pages) { [int]$info.total_pages } else { 1 }
    # total_pages is 0/1-based inconsistently on this endpoint; stop when a page
    # comes back short or we've reached the reported page count.
    if ($batch.Count -eq 0 -or ($page + 1) -ge $totalPages) { break }
    $page++
}

$sorted = @($names | Sort-Object -Unique)
$dir = Split-Path -Parent $OutputFile
if (-not [string]::IsNullOrWhiteSpace($dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
$sorted | ConvertTo-Json -AsArray | Out-File -FilePath $OutputFile -Encoding utf8
Write-Host "Cloudflare registrar domains: $($sorted.Count) -> $OutputFile"
