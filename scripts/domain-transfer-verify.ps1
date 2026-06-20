<#
.SYNOPSIS
    READ-ONLY post-transfer verification for a domain moved to Cloudflare
    Registrar (eNOM -> Cloudflare; parent project #157).

.DESCRIPTION
    Confirms a transfer landed cleanly. Makes only read calls and an HTTP GET; it
    changes nothing. It checks:

      1. Registrar = Cloudflare  -> GET /accounts/{id}/registrar/domains/{domain}
      2. Nameservers on Cloudflare -> GET /zones?name={domain} (name_servers)
      3. Site still reachable      -> HTTP GET (Live / Redirect / Error / Unreachable)

    'verified' is true only when the domain is managed by Cloudflare Registrar,
    its nameservers are Cloudflare/FFC, and the site is Live or Redirect.

.PARAMETER Domain
    Domain to verify.

.PARAMETER Account
    Which Cloudflare token to use: 'FFC' or 'CM'. Reads env
    CLOUDFLARE_API_TOKEN_FFC / CLOUDFLARE_API_TOKEN_CM (same convention as the
    other cloudflare-*.ps1).

.PARAMETER RequireVerified
    Exit non-zero if the domain is not fully verified. Useful as a CI gate.

.OUTPUTS
    A single JSON verdict object on stdout.

.EXAMPLE
    pwsh -File scripts/domain-transfer-verify.ps1 -Domain example.org -Account FFC
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Domain,

    [Parameter()]
    [ValidateSet('FFC', 'CM')]
    [string]$Account = 'FFC',

    [Parameter()]
    [switch]$RequireVerified
)

$ErrorActionPreference = 'Stop'

function Write-Diag {
    param([Parameter(Mandatory = $true)][string]$Message)
    [Console]::Error.WriteLine($Message)
}

function Get-TokenForAccount {
    param([Parameter(Mandatory = $true)][string]$Account)
    switch ($Account) {
        'FFC' {
            if (-not $env:CLOUDFLARE_API_TOKEN_FFC) { throw 'CLOUDFLARE_API_TOKEN_FFC is not set.' }
            return [string]$env:CLOUDFLARE_API_TOKEN_FFC
        }
        'CM' {
            if (-not $env:CLOUDFLARE_API_TOKEN_CM) { throw 'CLOUDFLARE_API_TOKEN_CM is not set.' }
            return [string]$env:CLOUDFLARE_API_TOKEN_CM
        }
        default { throw "Unsupported Account value: $Account" }
    }
}

# Non-throwing probe: returns status code + parsed body regardless of HTTP result.
function Invoke-CfProbe {
    param(
        [Parameter(Mandatory = $true)][string]$Method,
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][string]$Token
    )
    $resp = Invoke-RestMethod -Method $Method -Uri "https://api.cloudflare.com/client/v4$Uri" `
        -Headers @{ Authorization = "Bearer $Token" } -SkipHttpErrorCheck -StatusCodeVariable 'statusCode' -ErrorAction Stop
    return [pscustomobject]@{ status = [int]$statusCode; body = $resp }
}

function Get-HttpHealth {
    param([Parameter(Mandatory = $true)][string]$Domain)
    foreach ($scheme in @('https', 'http')) {
        try {
            $r = Invoke-WebRequest -Uri ("{0}://{1}" -f $scheme, $Domain) -Method Get -MaximumRedirection 0 `
                -SkipHttpErrorCheck -TimeoutSec 20 -ErrorAction Stop
            $code = [int]$r.StatusCode
            $health = if ($code -ge 200 -and $code -lt 300) { 'Live' }
            elseif ($code -ge 300 -and $code -lt 400) { 'Redirect' }
            else { 'Error' }
            return [pscustomobject]@{ status = $code; health = $health }
        }
        catch {
            # Try the next scheme; if both fail it is unreachable.
        }
    }
    return [pscustomobject]@{ status = $null; health = 'Unreachable' }
}

try {
    $d = $Domain.Trim().ToLowerInvariant().Trim('.')
    if ([string]::IsNullOrWhiteSpace($d)) { throw 'Domain is required.' }

    $token = Get-TokenForAccount -Account $Account

    # Resolve the single account for this token (single-account guard).
    $acctProbe = Invoke-CfProbe -Method 'GET' -Uri '/accounts' -Token $token
    if ($acctProbe.status -lt 200 -or $acctProbe.status -ge 300 -or -not $acctProbe.body.success) {
        throw "Could not list accounts for token '$Account' (HTTP $($acctProbe.status))."
    }
    $accounts = @($acctProbe.body.result)
    if ($accounts.Count -lt 1) { throw "Token '$Account' resolved no accounts." }
    if ($accounts.Count -gt 1) {
        $names = ($accounts | Select-Object -ExpandProperty name -ErrorAction SilentlyContinue)
        throw ("Token '{0}' can access multiple accounts; refusing to guess. Accounts: {1}" -f $Account, ($names -join ', '))
    }
    $accountId = $accounts[0].id

    # 1) Registrar = Cloudflare? (404 here just means "not managed by CF registrar".)
    $regProbe = Invoke-CfProbe -Method 'GET' -Uri "/accounts/$accountId/registrar/domains/$d" -Token $token
    $registrarIsCloudflare = ($regProbe.status -ge 200 -and $regProbe.status -lt 300 -and [bool]$regProbe.body.success)
    $registrarState = $null
    if ($registrarIsCloudflare -and $regProbe.body.result) {
        try { $registrarState = [string]$regProbe.body.result.status } catch {}
    }

    # 2) Nameservers on Cloudflare?
    $zoneProbe = Invoke-CfProbe -Method 'GET' -Uri "/zones?name=$([uri]::EscapeDataString($d))" -Token $token
    $nameServers = @()
    if ($zoneProbe.status -ge 200 -and $zoneProbe.status -lt 300 -and [bool]$zoneProbe.body.success) {
        $zones = @($zoneProbe.body.result)
        if ($zones.Count -ge 1) { $nameServers = @($zones[0].name_servers) }
    }
    $nsAtCloudflare = (@($nameServers | Where-Object { $_ -match '(?i)cloudflare|freeforcharity' }).Count -gt 0)

    # 3) Site reachability.
    $http = Get-HttpHealth -Domain $d

    $verified = ($registrarIsCloudflare -and $nsAtCloudflare -and ($http.health -in @('Live', 'Redirect')))

    $verdict = [ordered]@{
        domain                  = $d
        account                 = $Account
        accountId               = $accountId
        registrarIsCloudflare   = $registrarIsCloudflare
        registrarState          = $registrarState
        nameServers             = $nameServers
        nameserversAtCloudflare = $nsAtCloudflare
        httpStatus              = $http.status
        httpHealth              = $http.health
        verified                = $verified
    }

    Write-Diag ("Verify '$d' ($Account): registrar=$registrarIsCloudflare ns=$nsAtCloudflare http=$($http.health) verified=$verified")
    ([pscustomobject]$verdict) | ConvertTo-Json -Depth 6

    if ($RequireVerified -and -not $verified) {
        Write-Error "Domain '$d' is not fully verified (registrar=$registrarIsCloudflare, ns=$nsAtCloudflare, http=$($http.health))."
        exit 1
    }
    exit 0
}
catch {
    Write-Error $_
    exit 1
}
