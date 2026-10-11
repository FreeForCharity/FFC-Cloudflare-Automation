<#
.SYNOPSIS
    Resolves a NEW-ticket subject and message from config/whmcs-ticket-templates.json
    (workflow 205's optional `template` input).

.DESCRIPTION
    Workflow 207 replies to an existing ticket from any template. Opening a NEW
    ticket from one needs a subject as well, so 205 accepts only templates that
    carry a `subject` field and are client-visible (`internal: false`); every
    other template is reply-only and is refused here with a message that says so.

    Subject = "<subject_prefix> <subject>" (prefix omitted when blank).
    Message = body, then a blank line and the dispatcher's extra_message when one
    is given (the same composition 207 uses). A template with
    `extra_message_required: true` (preview_ready: the preview URL) refuses an
    empty extra_message.

    Dot-source to load the function; it returns before doing anything else when
    dot-sourced.

    Pester: scripts/tests/whmcs-ticket-template.Tests.ps1
#>
[CmdletBinding()]
param()

function Resolve-WhmcsOpenTicketTemplate {
    <#
    .SYNOPSIS
        Returns [pscustomobject]@{ Subject; Message; Template } for opening a ticket, or throws with the reason.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$ConfigPath,
        [Parameter(Mandatory = $true)][string]$Name,
        [AllowEmptyString()][AllowNull()][string]$ExtraMessage
    )

    if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
        throw "Ticket template config not found: $ConfigPath"
    }
    $key = $Name.Trim()
    if ($key -notmatch '^[a-z0-9_]+$') {
        throw "template '$Name' is not a template key (lowercase letters, digits and underscores)."
    }
    $cfg = Get-Content -LiteralPath $ConfigPath -Raw -Encoding utf8 | ConvertFrom-Json
    $names = @($cfg.templates.PSObject.Properties.Name)
    if ($names -cnotcontains $key) {
        throw "No template '$key' in $(Split-Path -Leaf $ConfigPath). Known: $($names -join ', ')."
    }
    $tpl = $cfg.templates.$key

    if ([bool]$tpl.internal) {
        throw "template '$key' is an internal (staff-only) note; a new ticket is client-visible, so it cannot be opened from it. Use workflow 207 to add it to an existing ticket."
    }
    $subject = ([string]$tpl.subject).Trim()
    if ($subject -eq '') {
        throw "template '$key' has no 'subject', so it is a reply-only template (workflow 207). Templates that can open a ticket: $(@($names | Where-Object { -not [bool]$cfg.templates.$_.internal -and -not [string]::IsNullOrWhiteSpace([string]$cfg.templates.$_.subject) }) -join ', ')."
    }
    $body = ([string]$tpl.body).Trim()
    if ($body -eq '') {
        throw "template '$key' has an empty body."
    }

    $extra = ([string]$ExtraMessage).Trim()
    if ([bool]$tpl.extra_message_required -and $extra -eq '') {
        throw "template '$key' requires extra_message (for preview_ready: the preview URL the charity should open)."
    }

    $prefix = ([string]$tpl.subject_prefix).Trim()
    $fullSubject = if ($prefix) { "$prefix $subject" } else { $subject }
    $message = $body
    if ($extra) { $message += "`n`n" + $extra }

    return [pscustomobject]@{
        Template = $key
        Subject  = $fullSubject
        Message  = $message
    }
}

if ($MyInvocation.InvocationName -eq '.') { return }

Write-Error 'Dot-source this script to load Resolve-WhmcsOpenTicketTemplate; it does nothing when run directly.'
exit 2
