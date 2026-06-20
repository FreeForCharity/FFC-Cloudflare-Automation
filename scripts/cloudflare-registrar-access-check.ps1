<#
.SYNOPSIS
    Validate whether a Cloudflare API token has Registrar API rights.

.DESCRIPTION
    READ-ONLY / non-mutating probe. Determines what level of Cloudflare
    Registrar access the selected token has, without registering or changing
    anything. It checks:

      1. Token usable / account      -> GET  /accounts (also sets tokenActive)
      2. Registrar READ access       -> GET  /accounts/{id}/registrar/domains
      3. Registrar WRITE access       -> POST /accounts/{id}/registrar/domain-check
                                        (availability check only; never charges)

    Note: tokenActive reflects whether the token can list its account. The
    /user/tokens/verify endpoint is intentionally not used: it does not apply to
    account-scoped tokens and can report a valid token as inactive.

    Each capability is classified as:
      granted       - the call succeeded (right is present)
      denied        - auth/permission error (right is missing)
      inconclusive  - some other error (e.g. Registrar not enabled on the
                      account, or billing/onboarding incomplete); the token
                      right may be present but the feature/account is not ready

    'canRegister' is true only when Registrar WRITE is granted.

    Human-readable lines are written to stderr so stdout stays strictly the
    final JSON verdict object.

.PARAMETER Account
    Which token to test: 'FFC' or 'CM'. Reads env CLOUDFLARE_API_TOKEN_FFC /
    CLOUDFLARE_API_TOKEN_CM (same convention as the other cloudflare-*.ps1).

.PARAMETER RequireWrite
    Exit non-zero if Registrar WRITE is not 'granted'. Useful as a gate in CI.

.OUTPUTS
    A single JSON verdict object on stdout.

.EXAMPLE
    pwsh -File scripts/cloudflare-registrar-access-check.ps1 -Account FFC

.EXAMPLE
    pwsh -File scripts/cloudflare-registrar-access-check.ps1 -Account FFC -RequireWrite
#>
[CmdletBinding()]
param(
    [Parameter()]
    [ValidateSet('FFC', 'CM')]
    [string]$Account = 'FFC',

    [Parameter()]
    [switch]$RequireWrite
)

$ErrorActionPreference = 'Stop'

# Diagnostics go to stderr so stdout is strictly the final JSON object.
function Write-Diag {
    param([Parameter(Mandatory = $true)][string]$Message)
    [Console]::Error.WriteLine($Message)
}

function Get-TokenForAccount {
    param(
        [Parameter(Mandatory = $true)][string]$Account
    )

    switch ($Account) {
        'FFC' {
            if (-not $env:CLOUDFLARE_API_TOKEN_FFC) { throw 'CLOUDFLARE_API_TOKEN_FFC is not set.' }
            return [string]$env:CLOUDFLARE_API_TOKEN_FFC
        }
        'CM' {
            if (-not $env:CLOUDFLARE_API_TOKEN_CM) { throw 'CLOUDFLARE_API_TOKEN_CM is not set.' }
            return [string]$env:CLOUDFLARE_API_TOKEN_CM
        }
        default {
            throw "Unsupported Account value: $Account"
        }
    }
}

# Non-throwing probe: returns status code + parsed body regardless of HTTP result.
function Invoke-CfProbe {
    param(
        [Parameter(Mandatory = $true)][string]$Method,
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][string]$Token,
        [Parameter()][object]$Body
    )

    $params = @{
        Method             = $Method
        Uri                = "https://api.cloudflare.com/client/v4$Uri"
        Headers            = @{ Authorization = "Bearer $Token" }
        SkipHttpErrorCheck = $true
        StatusCodeVariable = 'statusCode'
        ErrorAction        = 'Stop'
    }
    if ($null -ne $Body) {
        $params.Body = ($Body | ConvertTo-Json -Depth 10)
        $params.ContentType = 'application/json'
    }

    $resp = Invoke-RestMethod @params
    return [pscustomobject]@{
        status = [int]$statusCode
        body   = $resp
    }
}

# Classify a probe result into granted / denied / inconclusive.
function Get-Capability {
    param(
        [Parameter(Mandatory = $true)][int]$Status,
        [Parameter()][object]$Body
    )

    $success = ($Status -ge 200 -and $Status -lt 300 -and [bool]$Body.success)
    if ($success) {
        return [pscustomobject]@{ state = 'granted'; status = $Status; detail = 'OK' }
    }

    $codes = @()
    $message = $null
    if ($Body -and $Body.errors) {
        $codes = @($Body.errors | ForEach-Object { [int]$_.code })
        $message = ($Body.errors | Select-Object -First 1 -ExpandProperty message -ErrorAction SilentlyContinue)
    }
    if (-not $message) { $message = "HTTP $Status" }

    $authCodes = @(1001, 9106, 9109, 10000)
    $hasAuthCode = (@($codes | Where-Object { $authCodes -contains $_ }).Count -gt 0)
    $matchesAuthText = ($message -match 'authenticat|authoriz|permission|not allowed|unauthorized')
    $isDenied = ($Status -eq 401 -or $Status -eq 403 -or $hasAuthCode -or $matchesAuthText)

    $state = 'inconclusive'
    if ($isDenied) {
        $state = 'denied'
    }

    $detail = $message
    if ($codes.Count -gt 0) {
        $detail = "$message (codes: $($codes -join ','))"
    }

    return [pscustomobject]@{ state = $state; status = $Status; detail = $detail }
}

try {
    $token = Get-TokenForAccount -Account $Account

    # Token validity is inferred from a successful account listing below. The
    # /user/tokens/verify endpoint does not apply to account-scoped API tokens
    # and can return 403 even for a fully valid token, so we do not rely on it.

    # Resolve the account id (single-account guard, matching cloudflare-zone-create.ps1).
    $acctProbe = Invoke-CfProbe -Method 'GET' -Uri '/accounts' -Token $token
    $acctOk = ($acctProbe.status -ge 200 -and $acctProbe.status -lt 300 -and [bool]$acctProbe.body.success)
    if (-not $acctOk) {
        throw "Could not list accounts for token '$Account' (HTTP $($acctProbe.status)). Token may be invalid or lack Account read."
    }
    $tokenActive = $acctOk
    $accounts = @($acctProbe.body.result)
    if ($accounts.Count -lt 1) { throw "Token '$Account' resolved no accounts." }
    if ($accounts.Count -gt 1) {
        $names = ($accounts | Select-Object -ExpandProperty name -ErrorAction SilentlyContinue)
        throw ("Token '{0}' can access multiple accounts; refusing to guess. Accounts: {1}" -f $Account, ($names -join ', '))
    }
    $accountId = $accounts[0].id
    $accountName = $accounts[0].name

    # 2) Registrar READ probe (list domains).
    $readProbe = Invoke-CfProbe -Method 'GET' -Uri "/accounts/$accountId/registrar/domains?per_page=1" -Token $token
    $read = Get-Capability -Status $readProbe.status -Body $readProbe.body

    # 3) Registrar WRITE probe (availability check; never charges, example.com is harmless).
    $writeProbe = Invoke-CfProbe -Method 'POST' -Uri "/accounts/$accountId/registrar/domain-check" -Token $token -Body @{ domains = @('example.com') }
    $write = Get-Capability -Status $writeProbe.status -Body $writeProbe.body

    $canRegister = ($write.state -eq 'granted')

    $verdict = [ordered]@{
        account        = $Account
        accountName    = $accountName
        accountId      = $accountId
        tokenActive    = $tokenActive
        registrarRead  = $read.state
        registrarWrite = $write.state
        canRegister    = $canRegister
        details        = [ordered]@{
            registrarRead  = "$($read.status): $($read.detail)"
            registrarWrite = "$($write.status): $($write.detail)"
        }
    }

    Write-Diag ("Token '$Account' ($accountName): active=$tokenActive, registrarRead=$($read.state), registrarWrite=$($write.state), canRegister=$canRegister")
    Write-Diag ("  read : " + $verdict.details.registrarRead)
    Write-Diag ("  write: " + $verdict.details.registrarWrite)

    $verdict | ConvertTo-Json -Depth 6

    if ($RequireWrite -and -not $canRegister) {
        Write-Error "Registrar WRITE access is '$($write.state)', not 'granted'. Token cannot register domains."
        exit 1
    }

    exit 0
}
catch {
    Write-Error $_
    exit 1
}
