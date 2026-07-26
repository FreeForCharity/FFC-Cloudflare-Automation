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
    # client: GetClientsDetails returns the record inline, so ReadList is $null.
    client  = @{
        WriteAction = 'UpdateClient'
        IdParam     = 'clientid'
        ReadAction  = 'GetClientsDetails'
        ReadIdParam = 'clientid'
        ReadList    = $null
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
    # service: the only target that also accepts customfield:<id>.
    service = @{
        WriteAction  = 'UpdateClientProduct'
        IdParam      = 'serviceid'
        ReadAction   = 'GetClientsProducts'
        ReadIdParam  = 'serviceid'
        ReadList     = 'products'
        ReadChild    = 'product'
        Fields       = @('domain', 'dedicatedip', 'notes')
        CustomFields = $true
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

function Get-WhmcsListItems {
    # WHMCS returns list payloads nested ({domains:{domain:[...]}}) but sometimes
    # FLATTENS them into keys like `domains[domain][0][id]` instead - observed on
    # GetClientsDomains and handled the same way in domain-transfer-epp-probe.ps1.
    # Reading only the nested shape produces a false "not found" for those.
    param($Response, [Parameter(Mandatory = $true)][string]$ListProperty,
        [Parameter(Mandatory = $true)][string]$ChildName)

    $node = if ($Response.PSObject.Properties[$ListProperty]) { $Response.$ListProperty } else { $null }
    $items = @(Get-WhmcsNodeList -Node $node -ChildName $ChildName)
    if ($items.Count -gt 0) { return $items }

    $rx = '^' + [regex]::Escape($ListProperty) + '\[' + [regex]::Escape($ChildName) + '\]\[(\d+)\]\[([^\]]+)\]$'
    $byIndex = @{}
    foreach ($prop in $Response.PSObject.Properties) {
        $m = [regex]::Match($prop.Name, $rx)
        if (-not $m.Success) { continue }
        $idx = [int]$m.Groups[1].Value
        if (-not $byIndex.ContainsKey($idx)) { $byIndex[$idx] = @{} }
        $byIndex[$idx][$m.Groups[2].Value] = $prop.Value
    }
    $out = @()
    foreach ($idx in ($byIndex.Keys | Sort-Object)) { $out += [pscustomobject]$byIndex[$idx] }
    return $out
}

function Test-WhmcsRecordId {
    # True when the record carries the id we asked for. WHMCS names the id field
    # differently per record type, so check the plausible ones.
    param($Record, [Parameter(Mandatory = $true)][string]$IdParam,
        [Parameter(Mandatory = $true)][int]$RecordId)
    foreach ($f in @('id', $IdParam, 'userid')) {
        if ($Record.PSObject.Properties[$f] -and [string]$Record.$f -eq [string]$RecordId) { return $true }
    }
    return $false
}

function Get-WhmcsRecord {
    # Returns the record for the target id, or $null when absent.
    #
    # The id is VERIFIED rather than assumed: a filtered read that WHMCS ignores
    # (or that returns several rows) would otherwise hand back row 0, and this
    # record decides both previousValue and the overwrite guard - so trusting it
    # blindly means the guard protects the wrong record and the write lands on
    # the right one.
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
        # GetClientsDetails returns the record inline and echoes the id.
        $record = if ($resp.PSObject.Properties[$Spec.ReadChild]) { $resp.$($Spec.ReadChild) } else { $resp }
        if ($null -eq $record) { return $null }
        if (Test-WhmcsRecordId -Record $record -IdParam $Spec.IdParam -RecordId $RecordId) { return $record }
        return $null
    }

    foreach ($item in @(Get-WhmcsListItems -Response $resp -ListProperty $Spec.ReadList -ChildName $Spec.ReadChild)) {
        if (Test-WhmcsRecordId -Record $item -IdParam $Spec.IdParam -RecordId $RecordId) { return $item }
    }
    return $null
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
        # nothing masks stdout the way the Actions log does. `customfields`
        # carries the base64-serialized application answers (mission, EIN,
        # contact details), so it is redacted as payload, not as a credential.
        foreach ($k in @('identifier', 'secret', 'accesskey', 'customfields')) {
            if ($preview.ContainsKey($k)) { $preview[$k] = '***' }
        }
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
