<#
.SYNOPSIS
    Read or change a domain's registrar lock (clientTransferProhibited) in WHMCS,
    for the eNOM -> Cloudflare Registrar transfer project (#157).

.DESCRIPTION
    A registrar transfer bounces while the losing registrar has the domain
    transfer-locked. This script reports the current lock state (read-only) and,
    unless -DryRun, sets it to the desired state (default: UNLOCKED) via the WHMCS
    API so the domain can be transferred out to Cloudflare.

    Calls used:
      * DomainGetLockingStatus    - read current lock state (read-only).
      * DomainUpdateLockingStatus - set the lock state (only when not -DryRun).

    -DryRun makes only the read call, so it is safe to run anywhere to confirm
    connectivity and the current state without changing anything.

    Connection details follow the same convention as the other whmcs-*.ps1
    scripts (env WHMCS_API_URL / WHMCS_API_IDENTIFIER / WHMCS_API_SECRET /
    WHMCS_API_CREDENTIALS_JSON / WHMCS_API_ACCESS_KEY) and reuse the shared
    helpers in whmcs-api-common.ps1.

.PARAMETER Domain
    The domain whose lock to read/change (must exist in WHMCS).

.PARAMETER Lock
    Set the registrar lock ON. By default the script UNLOCKS (the transfer-prep
    direction); pass -Lock to re-lock instead.

.PARAMETER DryRun
    Only read and report the current lock state; make no change.

.PARAMETER ApiUrl / Identifier / Secret / CredentialsJson / AccessKey
    WHMCS API connection details (env fallbacks as above).

.PARAMETER PageSize
    GetClientsDomains page size while locating the domain. Default 250.

.OUTPUTS
    A single JSON object on stdout: domain, domainId, before, desired, after,
    changed.

.EXAMPLE
    # Confirm current lock state (no change)
    pwsh -File scripts/whmcs-domain-lock.ps1 -Domain browncanyonranch.org -DryRun

.EXAMPLE
    # Unlock ahead of an eNOM -> Cloudflare transfer
    pwsh -File scripts/whmcs-domain-lock.ps1 -Domain browncanyonranch.org
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Domain,

    [Parameter()]
    [switch]$Lock,

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
    [ValidateRange(1, 250)]
    [int]$PageSize = 250,

    [Parameter()]
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'whmcs-api-common.ps1')

function Write-Diag {
    param([Parameter(Mandatory = $true)][string]$Message)
    [Console]::Error.WriteLine($Message)
}

function Normalize-DomainName {
    param([Parameter(Mandatory = $true)][string]$Value)
    $d = $Value.Trim().ToLowerInvariant()
    if ($d.StartsWith('http://') -or $d.StartsWith('https://')) {
        try { $d = ([uri]$d).Host } catch {}
    }
    return $d.Trim('.')
}

function Find-WhmcsDomain {
    param(
        [Parameter(Mandatory = $true)][string]$ApiUrl,
        [Parameter(Mandatory = $true)][hashtable]$AuthBody,
        [Parameter(Mandatory = $true)][string]$DomainName,
        [Parameter(Mandatory = $true)][int]$PageSize
    )

    $start = 0
    while ($true) {
        $body = @{}
        foreach ($k in $AuthBody.Keys) { $body[$k] = $AuthBody[$k] }
        $body.action = 'GetClientsDomains'
        $body.responsetype = 'json'
        $body.limitstart = $start
        $body.limitnum = $PageSize

        $r = Invoke-WhmcsApi -ApiUrl $ApiUrl -Body $body

        $domains = @()
        if ($r.domains -and $r.domains.domain) { $domains = @($r.domains.domain) }
        elseif ($r.domains -is [System.Array]) { $domains = @($r.domains) }

        foreach ($d in $domains) {
            $name = $null
            try { $name = [string]$d.domainname } catch {}
            if ([string]::IsNullOrWhiteSpace($name)) { try { $name = [string]$d.domain } catch {} }
            if ([string]::IsNullOrWhiteSpace($name)) { continue }
            if ((Normalize-DomainName -Value $name) -eq $DomainName) { return $d }
        }

        $returned = $domains.Count
        if ($returned -le 0) { break }
        $start += $returned
        $total = 0
        if ($r.totalresults) { [void][int]::TryParse($r.totalresults.ToString(), [ref]$total) }
        if ($total -gt 0 -and $start -ge $total) { break }
    }
    return $null
}

# WHMCS reports the lock as a boolean-ish value; normalise to 'locked'/'unlocked'/'unknown'.
function ConvertTo-LockState {
    param($Value)
    if ($null -eq $Value) { return 'unknown' }
    $v = ("$Value").Trim().ToLowerInvariant()
    if ($v -in @('true', '1', 'locked', 'yes', 'on')) { return 'locked' }
    if ($v -in @('false', '0', 'unlocked', 'no', 'off')) { return 'unlocked' }
    return 'unknown'
}

function Get-LockState {
    param([string]$ApiUrl, [hashtable]$AuthBody, [string]$DomainId)
    $body = @{}
    foreach ($k in $AuthBody.Keys) { $body[$k] = $AuthBody[$k] }
    $body.action = 'DomainGetLockingStatus'
    $body.responsetype = 'json'
    $body.domainid = $DomainId
    $r = Invoke-WhmcsApi -ApiUrl $ApiUrl -Body $body
    return ConvertTo-LockState $r.lockstatus
}

try {
    $domainName = Normalize-DomainName -Value $Domain
    if ([string]::IsNullOrWhiteSpace($domainName)) { throw 'Domain is required.' }

    $api = Resolve-WhmcsApiUrl -ApiUrlParam $ApiUrl
    $creds = Resolve-WhmcsCredentials -IdentifierParam $Identifier -SecretParam $Secret -CredentialsJsonParam $CredentialsJson
    $accessKey = Resolve-WhmcsAccessKey -AccessKeyParam $AccessKey

    $auth = @{ identifier = $creds.Identifier; secret = $creds.Secret }
    if (-not [string]::IsNullOrWhiteSpace($accessKey)) { $auth.accesskey = $accessKey }

    $existing = Find-WhmcsDomain -ApiUrl $api -AuthBody $auth -DomainName $domainName -PageSize $PageSize
    if (-not $existing) { throw "Domain '$domainName' not found in WHMCS (GetClientsDomains)." }

    $domainId = $null
    try { $domainId = [string]$existing.id } catch {}
    if ([string]::IsNullOrWhiteSpace($domainId)) { try { $domainId = [string]$existing.domainid } catch {} }
    if ([string]::IsNullOrWhiteSpace($domainId)) { throw 'WHMCS domain record did not include an id/domainid field.' }

    $desired = if ($Lock) { 'locked' } else { 'unlocked' }
    $before = Get-LockState -ApiUrl $api -AuthBody $auth -DomainId $domainId

    $result = [ordered]@{
        domain   = $domainName
        domainId = $domainId
        before   = $before
        desired  = $desired
        after    = $before
        changed  = $false
    }

    if ($DryRun) {
        Write-Diag "[DRY-RUN] '$domainName' (domainId=$domainId) lock is '$before'; desired '$desired'. No change made."
        $result | ConvertTo-Json -Depth 4
        exit 0
    }

    if ($before -eq $desired) {
        Write-Diag "'$domainName' is already '$desired'; no change needed."
        $result | ConvertTo-Json -Depth 4
        exit 0
    }

    $body = @{}
    foreach ($k in $auth.Keys) { $body[$k] = $auth[$k] }
    $body.action = 'DomainUpdateLockingStatus'
    $body.responsetype = 'json'
    $body.domainid = $domainId
    $body.lockstatus = if ($Lock) { 'true' } else { 'false' }
    [void](Invoke-WhmcsApi -ApiUrl $api -Body $body)

    $after = Get-LockState -ApiUrl $api -AuthBody $auth -DomainId $domainId
    $result.after = $after
    $result.changed = ($after -ne $before)

    if ($after -ne $desired) {
        Write-Diag "WARNING: '$domainName' lock is '$after' after update (desired '$desired'). The registrar may apply the change asynchronously; re-check shortly."
    }
    else {
        Write-Diag "'$domainName' lock set to '$after' (was '$before')."
    }
    $result | ConvertTo-Json -Depth 4
    exit 0
}
catch {
    Write-Error $_
    exit 1
}
