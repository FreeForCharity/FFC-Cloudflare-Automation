<#
.SYNOPSIS
    READ-ONLY domain-name search via the Cloudflare Registrar API. Suggests
    available domains for a keyword/phrase. Never registers or charges.

.DESCRIPTION
    Wraps GET /accounts/{id}/registrar/domain-search?q=...&limit=... (Cloudflare
    Registrar API, public beta). Returns suggestions across the API-supported
    extensions with availability/pricing when provided.

    This is the third Registrar read/search operation alongside
    cloudflare-domain-register.ps1 (availability check + register). It is purely
    a research helper: it spends no money and makes no changes.

.PARAMETER Query
    The search term: a keyword, phrase, or partial domain (e.g. 'acme corp',
    'evergreen coffee', 'example').

.PARAMETER Account
    Which Cloudflare account/token to use: 'FFC' or 'CM'. Mirrors the convention
    used by cloudflare-domain-register.ps1 (env vars CLOUDFLARE_API_TOKEN_FFC /
    CLOUDFLARE_API_TOKEN_CM).

.PARAMETER Limit
    Maximum number of suggestions to return (default 5).

.OUTPUTS
    A single JSON object on stdout: { query, account, accountId, count,
    suggestions: [ { name, available, currency, registrationCost } ... ] }.

.EXAMPLE
    pwsh -File scripts/cloudflare-domain-search.ps1 -Query 'evergreen coffee' -Account FFC

.EXAMPLE
    pwsh -File scripts/cloudflare-domain-search.ps1 -Query freeforcharity -Limit 10
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Query,

    [Parameter()]
    [ValidateSet('FFC', 'CM')]
    [string]$Account = 'FFC',

    [Parameter()]
    [ValidateRange(1, 50)]
    [int]$Limit = 5
)

$ErrorActionPreference = 'Stop'

# Human-readable diagnostics go to stderr so stdout stays strictly the final
# JSON object (callers and the workflow capture stdout and may parse it).
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

function Invoke-CfApi {
    param(
        [Parameter(Mandatory = $true)][string]$Method,
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][string]$Token,
        [Parameter()][hashtable]$Query
    )

    $base = 'https://api.cloudflare.com/client/v4'
    $url = "$base$Uri"

    if ($Query -and $Query.Count -gt 0) {
        $qs = ($Query.Keys | ForEach-Object { "$($_)=$([uri]::EscapeDataString([string]$Query[$_]))" }) -join '&'
        if ($qs) { $url = "$url`?$qs" }
    }

    # -SkipHttpErrorCheck so non-2xx responses do not throw before we read the
    # Cloudflare error body; we normalize the message including the HTTP status.
    $resp = Invoke-RestMethod -Method $Method -Uri $url -Headers @{ Authorization = "Bearer $Token" } `
        -SkipHttpErrorCheck -StatusCodeVariable 'statusCode' -ErrorAction Stop
    if ($statusCode -lt 200 -or $statusCode -ge 300 -or -not $resp.success) {
        $msg = ($resp.errors | Select-Object -First 1 -ExpandProperty message -ErrorAction SilentlyContinue)
        if (-not $msg) { $msg = ($resp | ConvertTo-Json -Depth 6) }
        throw "Cloudflare API error (HTTP $statusCode): $msg"
    }
    return $resp
}

function Resolve-AccountId {
    param(
        [Parameter(Mandatory = $true)][string]$Token,
        [Parameter(Mandatory = $true)][string]$Account
    )
    $accounts = @( (Invoke-CfApi -Method 'GET' -Uri '/accounts' -Token $Token).result )
    if ($accounts.Count -lt 1) {
        throw 'Could not determine Cloudflare account from token (GET /accounts returned no results).'
    }
    if ($accounts.Count -gt 1) {
        $names = ($accounts | Select-Object -ExpandProperty name -ErrorAction SilentlyContinue)
        throw ("Token '{0}' has access to multiple Cloudflare accounts; refusing to proceed. Accounts: {1}" -f $Account, ($names -join ', '))
    }
    return $accounts[0]
}

try {
    $q = $Query.Trim()
    if ([string]::IsNullOrWhiteSpace($q)) { throw 'Query is required.' }

    $token = Get-TokenForAccount -Account $Account
    $acct = Resolve-AccountId -Token $token -Account $Account
    $accountId = $acct.id
    Write-Diag ("Account: {0} ({1}) [{2}]" -f $acct.name, $accountId, $Account)

    $resp = Invoke-CfApi -Method 'GET' -Uri "/accounts/$accountId/registrar/domain-search" -Token $token `
        -Query @{ q = $q; limit = $Limit }

    # The API returns suggestions under result.domains (each with name +
    # availability/pricing). Normalize to a stable shape, tolerating field drift.
    $rows = @()
    if ($resp.result.domains) { $rows = @($resp.result.domains) }
    elseif ($resp.result -is [System.Array]) { $rows = @($resp.result) }

    $suggestions = foreach ($r in $rows) {
        $name = $null
        foreach ($n in @('name', 'domain', 'domain_name')) {
            if ($r.PSObject.Properties[$n] -and -not [string]::IsNullOrWhiteSpace([string]$r.$n)) { $name = [string]$r.$n; break }
        }
        if ([string]::IsNullOrWhiteSpace($name)) { continue }

        [ordered]@{
            name             = $name
            available        = $(if ($null -ne $r.registrable) { [bool]$r.registrable } elseif ($null -ne $r.available) { [bool]$r.available } else { $null })
            currency         = [string]$r.pricing.currency
            registrationCost = [string]$r.pricing.registration_cost
        }
    }
    $suggestions = @($suggestions | ForEach-Object { [pscustomobject]$_ })

    Write-Diag ("Found {0} suggestion(s) for '{1}'." -f $suggestions.Count, $q)

    [ordered]@{
        query       = $q
        account     = $Account
        accountId   = $accountId
        count       = $suggestions.Count
        suggestions = $suggestions
    } | ConvertTo-Json -Depth 6
    exit 0
}
catch {
    Write-Error $_
    exit 1
}
