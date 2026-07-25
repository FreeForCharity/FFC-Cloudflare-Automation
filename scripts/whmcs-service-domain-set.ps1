<#
.SYNOPSIS
    Set the `domain` field on a WHMCS client service (UpdateClientProduct).

.DESCRIPTION
    The charity-onboarding products are service products, so WHMCS never prompts
    for a domain at order time and the service's `domain` column stays empty. The
    domain then exists only in conversation - which means every downstream
    workflow that wants "the domain for this charity" has to infer it from custom
    fields or an email address. This backfills the recorded value.

    Emits a single JSON object on stdout:
    { action, dryRun, serviceid, domain, previousDomain?, skipped? }.

    SAFETY: one service per invocation, no bulk loop - same convention as
    whmcs-order-update.ps1. -DryRun previews the request (secrets redacted)
    without writing.

    On a live run the script first reads the service (GetClientsProducts) and:
      - refuses if the service id does not exist;
      - skips when the domain already matches (emits skipped = 'already-set');
      - refuses to OVERWRITE a different non-empty domain unless -Force is given.
        Silently replacing a recorded domain is how the wrong site gets
        provisioned, so it has to be deliberate.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [int]$ServiceId,

    # Apex domain, no scheme and no leading "www." - matches what the website
    # provisioning workflows expect (FFC-EX-<domain>).
    [Parameter(Mandatory = $true)]
    [string]$Domain,

    # Required to replace an existing, different domain.
    [Parameter()]
    [switch]$Force,

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

function Get-WhmcsServiceDomain {
    # Returns @{ Found = [bool]; Domain = [string] } for a service id.
    param(
        [Parameter(Mandatory = $true)][string]$ApiUrl,
        [Parameter(Mandatory = $true)][hashtable]$Auth,
        [Parameter(Mandatory = $true)][int]$ServiceId
    )
    $body = $Auth.Clone()
    $body.action = 'GetClientsProducts'
    $body.serviceid = $ServiceId
    $resp = Invoke-WhmcsApi -ApiUrl $ApiUrl -Body $body
    foreach ($p in (Get-WhmcsNodeList -Node $resp.products -ChildName 'product')) {
        if ([string]$p.id -eq [string]$ServiceId) {
            return @{ Found = $true; Domain = [string]$p.domain }
        }
    }
    return @{ Found = $false; Domain = '' }
}

try {
    $api = Resolve-WhmcsApiUrl -ApiUrlParam $ApiUrl
    $creds = Resolve-WhmcsCredentials -IdentifierParam $Identifier -SecretParam $Secret -CredentialsJsonParam $CredentialsJson
    $accessKey = Resolve-WhmcsAccessKey -AccessKeyParam $AccessKey

    $wanted = $Domain.Trim().ToLowerInvariant()
    $wanted = $wanted -replace '^https?://', ''
    $wanted = $wanted -replace '/.*$', ''
    if ($wanted -match '^www\.') { $wanted = $wanted.Substring(4) }
    if ($wanted -notmatch '^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$') {
        throw "Domain '$Domain' is not a valid apex domain (expected e.g. example.org, no scheme, no www)."
    }

    $body = @{
        identifier   = $creds.Identifier
        secret       = $creds.Secret
        action       = 'UpdateClientProduct'
        responsetype = 'json'
        serviceid    = $ServiceId
        domain       = $wanted
    }
    if (-not [string]::IsNullOrWhiteSpace($accessKey)) { $body.accesskey = $accessKey }

    if ($DryRun) {
        $preview = $body.Clone()
        foreach ($k in @('secret', 'accesskey')) { if ($preview.ContainsKey($k)) { $preview[$k] = '***' } }
        [pscustomobject]@{
            action    = 'UpdateClientProduct'
            dryRun    = $true
            serviceid = $ServiceId
            domain    = $wanted
            request   = $preview
        } | ConvertTo-Json -Depth 8
        exit 0
    }

    $auth = New-WhmcsAuthBody -Creds $creds -AccessKey $accessKey
    $current = Get-WhmcsServiceDomain -ApiUrl $api -Auth $auth -ServiceId $ServiceId
    if (-not $current.Found) {
        throw "Service $ServiceId not found via GetClientsProducts; refusing to set its domain."
    }

    $existing = ([string]$current.Domain).Trim()
    if ($existing.ToLowerInvariant() -eq $wanted) {
        [pscustomobject]@{
            action    = 'UpdateClientProduct'
            dryRun    = $false
            serviceid = $ServiceId
            domain    = $wanted
            skipped   = 'already-set'
        } | ConvertTo-Json -Depth 6
        exit 0
    }
    if (-not [string]::IsNullOrWhiteSpace($existing) -and -not $Force) {
        throw ("Service $ServiceId already records domain '$existing'; refusing to replace it with '$wanted'. " +
            'Re-run with -Force if the replacement is intended.')
    }

    [void](Invoke-WhmcsApi -ApiUrl $api -Body $body)
    [pscustomobject]@{
        action         = 'UpdateClientProduct'
        dryRun         = $false
        serviceid      = $ServiceId
        domain         = $wanted
        previousDomain = $existing
    } | ConvertTo-Json -Depth 6
    exit 0
}
catch {
    Write-Error $_
    exit 1
}
