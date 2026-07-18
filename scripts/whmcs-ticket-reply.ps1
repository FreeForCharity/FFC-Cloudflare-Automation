<#
.SYNOPSIS
    Reply to a WHMCS ticket (AddTicketReply) or add an internal staff note
    (AddTicketNote). Used to log break/fix actions on a charity's site.

.DESCRIPTION
    Default: adds a client-visible reply (AddTicketReply) attributed to an admin
    (-AdminUsername) or client (-ClientId). With -InternalNote, adds a staff-only
    note (AddTicketNote) - ideal for recording break/fix remediation steps.

    Emits JSON on stdout: { action, dryRun, ticketid }. -DryRun previews without
    writing (secrets redacted).
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [int]$TicketId,

    [Parameter(Mandatory = $true)]
    [string]$Message,

    # Add a staff-only internal note instead of a client-visible reply.
    [Parameter()]
    [switch]$InternalNote,

    # Attribution for a client-visible reply (one of these for AddTicketReply).
    [Parameter()]
    [string]$AdminUsername,

    [Parameter()]
    [int]$ClientId,

    [Parameter()]
    [switch]$Markdown,

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

    $action = if ($InternalNote) { 'AddTicketNote' } else { 'AddTicketReply' }

    $body = @{
        identifier   = $creds.Identifier
        secret       = $creds.Secret
        action       = $action
        responsetype = 'json'
        ticketid     = $TicketId
        message      = $Message
    }
    if (-not [string]::IsNullOrWhiteSpace($accessKey)) { $body.accesskey = $accessKey }
    if ($Markdown) { $body.markdown = $true }

    if (-not $InternalNote) {
        if ($AdminUsername) { $body.adminusername = $AdminUsername }
        elseif ($PSBoundParameters.ContainsKey('ClientId') -and $ClientId -gt 0) { $body.clientid = $ClientId }
        else { throw 'AddTicketReply needs attribution: provide -AdminUsername or -ClientId (or use -InternalNote).' }
    }

    if ($DryRun) {
        $preview = $body.Clone()
        foreach ($k in @('secret', 'accesskey')) { if ($preview.ContainsKey($k)) { $preview[$k] = '***' } }
        [pscustomobject]@{ action = $action; dryRun = $true; ticketid = $TicketId; request = $preview } | ConvertTo-Json -Depth 8
        exit 0
    }

    [void](Invoke-WhmcsApi -ApiUrl $api -Body $body)
    [pscustomobject]@{ action = $action; dryRun = $false; ticketid = $TicketId } | ConvertTo-Json -Depth 6
    exit 0
}
catch {
    Write-Error $_
    exit 1
}
