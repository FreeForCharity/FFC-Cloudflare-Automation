<#
.SYNOPSIS
    Set one field on one existing WHMCS record: client, contact, service, or
    domain.

.DESCRIPTION
    The FFC onboarding flow repeatedly needs a single recorded value corrected
    after the fact - a client's email, a contact's phone, the domain on an
    onboarding service, a domain's nameservers. Doing that by hand in the admin
    UI leaves no audit trail and no dry-run.

    This is ONE dispatch surface that can write to four record types, so the
    allowlist is the safety mechanism, not a convenience: a field that is not
    explicitly listed for its target cannot be written, and the failure names
    what is allowed. Adding a field to the list is a reviewed code change.

    Emits a single JSON object on stdout:
    { action, dryRun, target, recordId, field, value, previousValue?, skipped? }.

    Guards (same contract as the order-state writer):
      - one record, one field, one invocation - no bulk loop;
      - -DryRun previews the request with credentials redacted and writes
        nothing;
      - a live run reads the CURRENT value first and:
          * refuses if the record does not exist,
          * skips when the value already matches (skipped = 'already-set'),
          * refuses to replace a DIFFERENT non-empty value unless -Force.
        Silently overwriting a recorded value is how the wrong charity's data
        ends up on the wrong record, so replacement has to be deliberate.

.EXAMPLE
    whmcs-record-field-set.ps1 -Target service -RecordId 618 -Field domain -Value minoritywealthgap.org

.EXAMPLE
    # Product custom fields (the onboarding application's own answers) use the
    # customfield:<id> syntax; ids come from 219 / the products export.
    whmcs-record-field-set.ps1 -Target service -RecordId 618 -Field customfield:204 -Value 'Updated mission text'
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('client', 'contact', 'service', 'domain')]
    [string]$Target,

    [Parameter(Mandatory = $true)]
    [int]$RecordId,

    # Field name, or customfield:<id> for a service's product custom field.
    [Parameter(Mandatory = $true)]
    [string]$Field,

    [Parameter(Mandatory = $true)]
    [AllowEmptyString()]
    [string]$Value,

    # Required to replace an existing, different, non-empty value.
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

# Per-target contract: which WHMCS action writes it, which parameter carries the
# record id, how to read the record back, and EXACTLY which fields may be
# written. Anything absent here is refused - that is the whole point of having a
# single generic writer.
$TargetMap = @{
    client  = @{
        WriteAction = 'UpdateClient'
        IdParam     = 'clientid'
        ReadAction  = 'GetClientsDetails'
        ReadIdParam = 'clientid'
        ReadList    = $null      # GetClientsDetails returns the client inline
        ReadChild   = 'client'
        Fields      = @('firstname', 'lastname', 'companyname', 'email', 'address1', 'address2',
            'city', 'state', 'postcode', 'country', 'phonenumber', 'notes')
    }
    contact = @{
        WriteAction = 'UpdateContact'
        IdParam     = 'contactid'
        ReadAction  = 'GetContacts'
        ReadIdParam = 'contactid'
        ReadList    = 'contacts'
        ReadChild   = 'contact'
        Fields      = @('firstname', 'lastname', 'companyname', 'email', 'address1', 'address2',
            'city', 'state', 'postcode', 'country', 'phonenumber')
    }
    service = @{
        WriteAction = 'UpdateClientProduct'
        IdParam     = 'serviceid'
        ReadAction  = 'GetClientsProducts'
        ReadIdParam = 'serviceid'
        ReadList    = 'products'
        ReadChild   = 'product'
        Fields      = @('domain', 'dedicatedip', 'notes')
        CustomFields = $true     # also accepts customfield:<id>
    }
    domain  = @{
        WriteAction = 'UpdateClientDomain'
        IdParam     = 'domainid'
        ReadAction  = 'GetClientsDomains'
        ReadIdParam = 'domainid'
        ReadList    = 'domains'
        ReadChild   = 'domain'
        Fields      = @('ns1', 'ns2', 'ns3', 'ns4', 'ns5', 'notes')
    }
}

function Get-WhmcsRecord {
    # Returns the record object for the target id, or $null when absent.
    param(
        [Parameter(Mandatory = $true)][string]$ApiUrl,
        [Parameter(Mandatory = $true)][hashtable]$Auth,
        [Parameter(Mandatory = $true)][hashtable]$Spec,
        [Parameter(Mandatory = $true)][int]$RecordId
    )
    $body = $Auth.Clone()
    $body.action = $Spec.ReadAction
    $body[$Spec.ReadIdParam] = $RecordId
    $resp = Invoke-WhmcsApi -ApiUrl $ApiUrl -Body $body

    if (-not $Spec.ReadList) {
        # GetClientsDetails returns the record inline (and echoes the id).
        if ($resp.PSObject.Properties[$Spec.ReadChild]) { return $resp.$($Spec.ReadChild) }
        return $resp
    }
    $node = if ($resp.PSObject.Properties[$Spec.ReadList]) { $resp.$($Spec.ReadList) } else { $null }
    $items = @(Get-WhmcsNodeList -Node $node -ChildName $Spec.ReadChild)
    if ($items.Count -lt 1) { return $null }
    return $items[0]
}

function Get-WhmcsCustomFieldValue {
    # Current value of a product custom field by id, or $null when not present.
    param($Record, [Parameter(Mandatory = $true)][string]$FieldId)
    foreach ($f in @(Get-WhmcsNodeList -Node $Record.customfields -ChildName 'customfield')) {
        if ([string]$f.id -eq $FieldId) { return [string]$f.value }
    }
    return $null
}

try {
    $api = Resolve-WhmcsApiUrl -ApiUrlParam $ApiUrl
    $creds = Resolve-WhmcsCredentials -IdentifierParam $Identifier -SecretParam $Secret -CredentialsJsonParam $CredentialsJson
    $accessKey = Resolve-WhmcsAccessKey -AccessKeyParam $AccessKey

    $spec = $TargetMap[$Target]
    $field = $Field.Trim()
    $customFieldId = $null

    if ($field -match '^customfield:(\d+)$') {
        if (-not $spec.CustomFields) {
            throw "Target '$Target' does not support custom fields; only 'service' does."
        }
        $customFieldId = $Matches[1]
    }
    elseif ($spec.Fields -notcontains $field.ToLowerInvariant()) {
        throw ("Field '$field' is not writable on target '$Target'. Allowed: " +
            ($spec.Fields -join ', ') +
            $(if ($spec.CustomFields) { ', customfield:<id>' } else { '' }) +
            '. Adding a field to the allowlist is a reviewed code change, not a dispatch input.')
    }
    else {
        $field = $field.ToLowerInvariant()
    }

    $body = @{
        identifier   = $creds.Identifier
        secret       = $creds.Secret
        action       = $spec.WriteAction
        responsetype = 'json'
    }
    $body[$spec.IdParam] = $RecordId
    if ($customFieldId) {
        # WHMCS takes product custom fields as base64(serialize(array(id=>value))).
        $body.customfields = ConvertTo-WhmcsCustomFields -Json (@{ $customFieldId = $Value } | ConvertTo-Json -Compress)
    }
    else {
        $body[$field] = $Value
    }
    if (-not [string]::IsNullOrWhiteSpace($accessKey)) { $body.accesskey = $accessKey }

    if ($DryRun) {
        $preview = $body.Clone()
        # Redact the identifier too - this script also runs locally, where
        # nothing masks stdout the way the Actions log does.
        foreach ($k in @('identifier', 'secret', 'accesskey')) { if ($preview.ContainsKey($k)) { $preview[$k] = '***' } }
        [pscustomobject]@{
            action   = $spec.WriteAction
            dryRun   = $true
            target   = $Target
            recordId = $RecordId
            field    = $Field
            value    = $Value
            request  = $preview
        } | ConvertTo-Json -Depth 8
        exit 0
    }

    $auth = New-WhmcsAuthBody -Creds $creds -AccessKey $accessKey
    $record = Get-WhmcsRecord -ApiUrl $api -Auth $auth -Spec $spec -RecordId $RecordId
    if ($null -eq $record) {
        throw "$Target $RecordId not found via $($spec.ReadAction); refusing to write."
    }

    $previous = if ($customFieldId) {
        Get-WhmcsCustomFieldValue -Record $record -FieldId $customFieldId
    }
    elseif ($record.PSObject.Properties[$field]) {
        [string]$record.$field
    }
    else { $null }
    $previous = if ($null -eq $previous) { '' } else { ([string]$previous).Trim() }

    if ($previous -eq $Value.Trim()) {
        [pscustomobject]@{
            action   = $spec.WriteAction
            dryRun   = $false
            target   = $Target
            recordId = $RecordId
            field    = $Field
            value    = $Value
            skipped  = 'already-set'
        } | ConvertTo-Json -Depth 6
        exit 0
    }
    if (-not [string]::IsNullOrWhiteSpace($previous) -and -not $Force) {
        throw ("$Target $RecordId already records '$previous' for '$Field'; refusing to replace it with " +
            "'$Value'. Re-run with -Force if the replacement is intended.")
    }

    [void](Invoke-WhmcsApi -ApiUrl $api -Body $body)
    [pscustomobject]@{
        action        = $spec.WriteAction
        dryRun        = $false
        target        = $Target
        recordId      = $RecordId
        field         = $Field
        value         = $Value
        previousValue = $previous
    } | ConvertTo-Json -Depth 6
    exit 0
}
catch {
    Write-Error $_
    exit 1
}
