<#
.SYNOPSIS
    DRAFT: Check availability/pricing and (optionally) register a domain via the
    Cloudflare Registrar API.

.DESCRIPTION
    Wraps the Cloudflare Registrar API (public BETA as of 2026):
      - POST /accounts/{id}/registrar/domain-check   (availability + pricing)
      - POST /accounts/{id}/registrar/registrations   (register a new domain)

    SAFETY MODEL (registration spends real money):
      * Default behavior is a READ-ONLY availability + pricing check. Nothing is
        purchased unless you pass BOTH -Register and -Execute.
      * -Register without -Execute performs a DRY RUN (prints what it would do).
      * -MaxRegistrationCost caps spend: registration is refused if the quoted
        first-year cost exceeds the cap.

    LIMITATIONS (Cloudflare Registrar API beta):
      * Only NEW registrations are supported here. Renewals, transfers, and
        contact updates are NOT available via the API yet (dashboard-only).
      * The Cloudflare account must have a billing profile with a default payment
        method, a default registrant contact, and the registration agreement
        accepted. The API token must have Registrar write permissions.

.PARAMETER Domain
    Domain to check/register (e.g. example.org).

.PARAMETER Account
    Which Cloudflare account/token to use: 'FFC' or 'CM'. Mirrors the convention
    used by cloudflare-zone-create.ps1 / cloudflare-zone-get.ps1
    (env vars CLOUDFLARE_API_TOKEN_FFC / CLOUDFLARE_API_TOKEN_CM).

.PARAMETER Register
    Attempt registration. Without -Execute this is a DRY RUN.

.PARAMETER Execute
    Required alongside -Register to actually purchase the domain (charges money).

.PARAMETER AutoRenew
    Enable auto-renew on the newly registered domain (default: off).

.PARAMETER PrivacyMode
    Optional privacy_mode value passed through to the API when set. Leave empty
    to use Cloudflare's default (WHOIS redaction where supported).

.PARAMETER MaxRegistrationCost
    Optional spend cap (in the quoted currency). 0 = no cap. If the quoted
    first-year registration cost exceeds this value, registration is refused.

.PARAMETER ContactJsonPath
    Optional path to a JSON file with a "contacts" object to override the
    account default registrant contact. See the Cloudflare Registrar API docs
    for the contact shape.

.OUTPUTS
    A single JSON object on stdout describing the result (composable, like
    cloudflare-zone-get.ps1).

.EXAMPLE
    # Availability + pricing only (safe, no purchase):
    pwsh -File scripts/cloudflare-domain-register.ps1 -Domain example.org -Account FFC

.EXAMPLE
    # Dry run of a registration (no purchase):
    pwsh -File scripts/cloudflare-domain-register.ps1 -Domain example.org -Account FFC -Register

.EXAMPLE
    # Actually register, capped at $25, with auto-renew:
    pwsh -File scripts/cloudflare-domain-register.ps1 -Domain example.org -Account FFC `
        -Register -Execute -AutoRenew -MaxRegistrationCost 25
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Domain,

    [Parameter(Mandatory = $true)]
    [ValidateSet('FFC', 'CM')]
    [string]$Account,

    [Parameter()]
    [switch]$Register,

    [Parameter()]
    [switch]$Execute,

    [Parameter()]
    [switch]$AutoRenew,

    [Parameter()]
    [string]$PrivacyMode = '',

    [Parameter()]
    [decimal]$MaxRegistrationCost = 0,

    [Parameter()]
    [string]$ContactJsonPath = ''
)

$ErrorActionPreference = 'Stop'

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

function Invoke-CfApi {
    param(
        [Parameter(Mandatory = $true)][string]$Method,
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][string]$Token,
        [Parameter()][hashtable]$Query,
        [Parameter()][object]$Body
    )

    $base = 'https://api.cloudflare.com/client/v4'
    $url = "$base$Uri"

    if ($Query -and $Query.Count -gt 0) {
        $qs = ($Query.Keys | ForEach-Object { "$($_)=$([uri]::EscapeDataString([string]$Query[$_]))" }) -join '&'
        if ($qs) { $url = "$url`?$qs" }
    }

    $headers = @{ Authorization = "Bearer $Token" }

    $irmParams = @{
        Method      = $Method
        Uri         = $url
        Headers     = $headers
        ErrorAction = 'Stop'
    }

    if ($null -ne $Body) {
        $irmParams.Body = ($Body | ConvertTo-Json -Depth 12)
        $irmParams.ContentType = 'application/json'
    }

    $resp = Invoke-RestMethod @irmParams
    if (-not $resp.success) {
        $msg = ($resp.errors | Select-Object -First 1 -ExpandProperty message -ErrorAction SilentlyContinue)
        if (-not $msg) { $msg = ($resp | ConvertTo-Json -Depth 6) }
        throw "Cloudflare API error: $msg"
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
    $d = $Domain.Trim().ToLowerInvariant().Trim('.')
    if ([string]::IsNullOrWhiteSpace($d)) { throw 'Domain is required.' }

    $token = Get-TokenForAccount -Account $Account
    $acct = Resolve-AccountId -Token $token -Account $Account
    $accountId = $acct.id

    Write-Host ("Account: {0} ({1}) [{2}]" -f $acct.name, $accountId, $Account) -ForegroundColor Cyan

    # --- 1) Availability + pricing check (always; never charges) ---
    $checkResp = Invoke-CfApi -Method 'POST' -Uri "/accounts/$accountId/registrar/domain-check" -Token $token -Body @{ domains = @($d) }
    $checkRows = @($checkResp.result.domains)
    $check = $checkRows | Where-Object { $_.name -eq $d } | Select-Object -First 1
    if (-not $check) { $check = $checkRows | Select-Object -First 1 }
    if (-not $check) { throw "Cloudflare returned no availability result for '$d'." }

    $registrable = [bool]$check.registrable
    $currency = [string]$check.pricing.currency
    $regCostRaw = [string]$check.pricing.registration_cost
    $renewCostRaw = [string]$check.pricing.renewal_cost

    $regCost = $null
    if (-not [string]::IsNullOrWhiteSpace($regCostRaw)) {
        [decimal]$parsed = 0
        if ([decimal]::TryParse($regCostRaw, [ref]$parsed)) { $regCost = $parsed }
    }

    Write-Host ("Domain '{0}': registrable={1}, registration={2} {3}, renewal={4} {5}" -f `
            $d, $registrable, $regCostRaw, $currency, $renewCostRaw, $currency) -ForegroundColor Green

    # Result object accumulated and emitted as JSON at the end.
    $result = [ordered]@{
        domain           = $d
        account          = $Account
        accountId        = $accountId
        registrable      = $registrable
        reason           = [string]$check.reason
        currency         = $currency
        registrationCost = $regCostRaw
        renewalCost      = $renewCostRaw
        action           = 'check'
        registered       = $false
        dryRun           = $true
        state            = $null
        expiresAt        = $null
        autoRenew        = [bool]$AutoRenew
        message          = $null
    }

    # --- Stop here unless registration was requested ---
    if (-not $Register) {
        $result.message = 'Availability/pricing check only (no -Register specified).'
        $result | ConvertTo-Json -Depth 6
        exit 0
    }

    # --- Guard rails before any spend ---
    if (-not $registrable) {
        throw ("Domain '{0}' is not registrable via Cloudflare (reason: {1})." -f $d, $result.reason)
    }

    if ($MaxRegistrationCost -gt 0) {
        if ($null -eq $regCost) {
            throw ("Refusing to register: could not parse registration cost ('{0}') to enforce -MaxRegistrationCost." -f $regCostRaw)
        }
        if ($regCost -gt $MaxRegistrationCost) {
            throw ("Refusing to register '{0}': cost {1} {2} exceeds -MaxRegistrationCost {3}." -f $d, $regCost, $currency, $MaxRegistrationCost)
        }
    }

    # --- Build registration body ---
    $regBody = [ordered]@{
        domain_name = $d
        auto_renew  = [bool]$AutoRenew
    }
    if (-not [string]::IsNullOrWhiteSpace($PrivacyMode)) {
        $regBody.privacy_mode = $PrivacyMode
    }
    if (-not [string]::IsNullOrWhiteSpace($ContactJsonPath)) {
        if (-not (Test-Path -LiteralPath $ContactJsonPath)) {
            throw "ContactJsonPath not found: $ContactJsonPath"
        }
        $contactObj = Get-Content -LiteralPath $ContactJsonPath -Raw | ConvertFrom-Json
        if ($contactObj.contacts) { $regBody.contacts = $contactObj.contacts }
        else { $regBody.contacts = $contactObj }
    }

    # --- DRY RUN unless -Execute ---
    if (-not $Execute) {
        $result.action = 'register'
        $result.dryRun = $true
        $result.message = "DRY RUN: would POST /accounts/$accountId/registrar/registrations. Re-run with -Execute to purchase."
        Write-Host $result.message -ForegroundColor Yellow
        Write-Host ('Request body: ' + ($regBody | ConvertTo-Json -Depth 6)) -ForegroundColor DarkGray
        $result | ConvertTo-Json -Depth 6
        exit 0
    }

    # --- LIVE registration (charges money) ---
    Write-Host ("[LIVE] Registering '{0}' for {1} {2}..." -f $d, $regCostRaw, $currency) -ForegroundColor Magenta
    $regResp = Invoke-CfApi -Method 'POST' -Uri "/accounts/$accountId/registrar/registrations" -Token $token -Body $regBody
    $reg = $regResp.result

    $result.action = 'register'
    $result.dryRun = $false
    $result.state = [string]$reg.state
    $result.registered = ($reg.state -eq 'succeeded' -or [bool]$reg.completed)
    if ($reg.context -and $reg.context.registration) {
        $result.expiresAt = [string]$reg.context.registration.expires_at
        $result.autoRenew = [bool]$reg.context.registration.auto_renew
    }
    $result.message = if ($result.registered) {
        "Registration succeeded."
    }
    else {
        "Registration state '$($reg.state)' (may still be in progress; check the Cloudflare dashboard)."
    }

    Write-Host $result.message -ForegroundColor Green
    $result | ConvertTo-Json -Depth 6
    exit 0
}
catch {
    Write-Error $_
    exit 1
}
