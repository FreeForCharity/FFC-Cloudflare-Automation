<#
.SYNOPSIS
    Open a WHMCS support ticket (OpenTicket). Used to track new requests and
    break/fix incidents for charity sites.

.DESCRIPTION
    Wraps the WHMCS 'OpenTicket' API action. Identify the requester by -ClientId
    (preferred, links the ticket to the charity) OR by -Name + -Email. Emits a
    single JSON object on stdout: { action, dryRun, ticketid, tid }. Use -DryRun
    to preview (no ticket created); secrets/customfields are redacted.

    PRIVACY: ticket bodies live in WHMCS (private). The dry-run preview redacts
    secrets and any serialized customfields payload.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [int]$DeptId,

    [Parameter(Mandatory = $true)]
    [string]$Subject,

    [Parameter(Mandatory = $true)]
    [string]$Message,

    [Parameter()]
    [int]$ClientId,

    [Parameter()]
    [string]$Name,

    [Parameter()]
    [string]$Email,

    [Parameter()]
    [ValidateSet('Low', 'Medium', 'High')]
    [string]$Priority = 'Medium',

    # Treat the message as Markdown.
    [Parameter()]
    [switch]$Markdown,

    # JSON object of { "<ticketCustomFieldId>": "value" }.
    [Parameter()]
    [string]$CustomFieldsJson,

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

try {
    $api = Resolve-WhmcsApiUrl -ApiUrlParam $ApiUrl
    $creds = Resolve-WhmcsCredentials -IdentifierParam $Identifier -SecretParam $Secret -CredentialsJsonParam $CredentialsJson
    $accessKey = Resolve-WhmcsAccessKey -AccessKeyParam $AccessKey

    $hasClient = $PSBoundParameters.ContainsKey('ClientId') -and $ClientId -gt 0
    if (-not $hasClient -and ([string]::IsNullOrWhiteSpace($Name) -or [string]::IsNullOrWhiteSpace($Email))) {
        throw 'Identify the requester: provide -ClientId, or both -Name and -Email.'
    }

    $body = @{
        identifier   = $creds.Identifier
        secret       = $creds.Secret
        action       = 'OpenTicket'
        responsetype = 'json'
        deptid       = $DeptId
        subject      = $Subject
        message      = $Message
        priority     = $Priority
    }
    if (-not [string]::IsNullOrWhiteSpace($accessKey)) { $body.accesskey = $accessKey }
    if ($hasClient) { $body.clientid = $ClientId }
    if ($Name) { $body.name = $Name }
    if ($Email) { $body.email = $Email }
    if ($Markdown) { $body.markdown = $true }
    if (-not [string]::IsNullOrWhiteSpace($CustomFieldsJson)) {
        $body.customfields = ConvertTo-WhmcsCustomFields -Json $CustomFieldsJson
    }

    if ($DryRun) {
        $preview = $body.Clone()
        foreach ($k in @('secret', 'accesskey', 'customfields')) { if ($preview.ContainsKey($k)) { $preview[$k] = '***' } }
        [pscustomobject]@{ action = 'OpenTicket'; dryRun = $true; ticketid = $null; tid = $null; request = $preview } | ConvertTo-Json -Depth 8
        exit 0
    }

    $resp = Invoke-WhmcsApi -ApiUrl $api -Body $body
    $ticketId = $null; $tid = $null
    try { $ticketId = [string]$resp.id } catch {}
    try { $tid = [string]$resp.tid } catch {}

    [pscustomobject]@{ action = 'OpenTicket'; dryRun = $false; ticketid = $ticketId; tid = $tid } | ConvertTo-Json -Depth 6
    exit 0
}
catch {
    Write-Error $_
    exit 1
}
