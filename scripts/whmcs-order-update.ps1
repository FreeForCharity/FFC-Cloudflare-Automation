<#
.SYNOPSIS
    Change the state of a WHMCS order: accept (AcceptOrder), cancel
    (CancelOrder), or mark fraud (FraudOrder).

.DESCRIPTION
    Wraps the WHMCS order state-change actions with the same credential / error
    conventions as the other write scripts in this repo. Emits a single JSON
    object on stdout: { action, dryRun, orderid, requested, skipped? }.

    SAFETY: there is intentionally NO bulk / automatic accept-or-cancel loop.
    Each live state change is a single explicit invocation. -DryRun previews the
    request without writing (secrets redacted) and skips the status pre-check.

    On a live run the script first reads the order's current status (GetOrders)
    and refuses a no-op or illegal transition, emitting
    { ..., skipped = 'already-<status>' } instead of calling the API:
      - accept : only a Pending order can be accepted
      - cancel : a Cancelled order is left alone
      - fraud  : a Fraud order is left alone
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [int]$OrderId,

    [Parameter(Mandatory = $true)]
    [ValidateSet('accept', 'cancel', 'fraud')]
    [string]$Action,

    # Free-text reason recorded on cancel/fraud (optional).
    [Parameter()]
    [string]$Reason,

    # accept-only: run product auto-setup / module create on acceptance.
    [Parameter()]
    [switch]$AutoSetup,

    # accept/cancel: send the related WHMCS email (default: suppressed).
    [Parameter()]
    [switch]$SendEmail,

    [Parameter()]
    [string]$ApiUrl,

    [Parameter()]
    [string]$Identifier,

    [Parameter()]
    [string]$Secret,

    [Parameter()]
    [string]$CredentialsJson,

    [Parameter()]
    [string]$AccessKey,

    [Parameter()]
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'whmcs-api-common.ps1')

function Get-WhmcsOrderStatus {
    # Returns the current status string for an order id, or $null if not found.
    [OutputType([string])]
    param(
        [Parameter(Mandatory = $true)][string]$ApiUrl,
        [Parameter(Mandatory = $true)][hashtable]$Auth,
        [Parameter(Mandatory = $true)][int]$OrderId
    )
    $body = $Auth.Clone()
    $body.action = 'GetOrders'
    $body.id = $OrderId
    $resp = Invoke-WhmcsApi -ApiUrl $ApiUrl -Body $body

    $orders = @()
    if ($resp.orders -and $resp.orders.order) { $orders = @($resp.orders.order) }
    elseif ($resp.orders -is [System.Array]) { $orders = @($resp.orders) }

    foreach ($o in $orders) {
        $oid = $null
        try { $oid = [string]$o.id } catch {}
        if ($oid -eq [string]$OrderId) { return [string]$o.status }
    }
    return $null
}

try {
    $api = Resolve-WhmcsApiUrl -ApiUrlParam $ApiUrl
    $creds = Resolve-WhmcsCredentials -IdentifierParam $Identifier -SecretParam $Secret -CredentialsJsonParam $CredentialsJson
    $accessKey = Resolve-WhmcsAccessKey -AccessKeyParam $AccessKey

    $apiAction = switch ($Action) {
        'accept' { 'AcceptOrder' }
        'cancel' { 'CancelOrder' }
        'fraud' { 'FraudOrder' }
    }

    $body = @{
        identifier   = $creds.Identifier
        secret       = $creds.Secret
        action       = $apiAction
        responsetype = 'json'
        orderid      = $OrderId
    }
    if (-not [string]::IsNullOrWhiteSpace($accessKey)) { $body.accesskey = $accessKey }

    switch ($Action) {
        'accept' {
            if ($AutoSetup) { $body.autosetup = $true }
            $body.sendemail = [bool]$SendEmail
        }
        'cancel' {
            if ($Reason) { $body.cancelreason = $Reason }
            $body.sendemail = [bool]$SendEmail
        }
        'fraud' {
            if ($Reason) { $body.cancelreason = $Reason }
        }
    }

    if ($DryRun) {
        $preview = $body.Clone()
        foreach ($k in @('secret', 'accesskey')) { if ($preview.ContainsKey($k)) { $preview[$k] = '***' } }
        [pscustomobject]@{ action = $apiAction; dryRun = $true; orderid = $OrderId; requested = $Action; request = $preview } | ConvertTo-Json -Depth 8
        exit 0
    }

    # Safety: read current status and refuse no-op / illegal transitions.
    $auth = New-WhmcsAuthBody -Creds $creds -AccessKey $accessKey
    $current = Get-WhmcsOrderStatus -ApiUrl $api -Auth $auth -OrderId $OrderId
    if ($null -eq $current) {
        throw "Order $OrderId not found via GetOrders; refusing to $Action."
    }
    $blocked = $false
    switch ($Action) {
        'accept' { if ($current -ne 'Pending') { $blocked = $true } }
        'cancel' { if ($current -eq 'Cancelled') { $blocked = $true } }
        'fraud' { if ($current -eq 'Fraud') { $blocked = $true } }
    }
    if ($blocked) {
        [pscustomobject]@{ action = $apiAction; dryRun = $false; orderid = $OrderId; requested = $Action; skipped = "already-$($current.ToLowerInvariant())" } | ConvertTo-Json -Depth 6
        exit 0
    }

    [void](Invoke-WhmcsApi -ApiUrl $api -Body $body)
    [pscustomobject]@{ action = $apiAction; dryRun = $false; orderid = $OrderId; requested = $Action; previousStatus = $current } | ConvertTo-Json -Depth 6
    exit 0
}
catch {
    Write-Error $_
    exit 1
}
