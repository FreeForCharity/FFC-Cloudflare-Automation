[CmdletBinding(DefaultParameterSetName = 'Audit')]
param(
    [Parameter(Mandatory = $true, HelpMessage = 'Zone / domain name, e.g. example.org')]
    [string]$Zone,

    # Read the zone's bot-management settings and print them. Writes nothing.
    [Parameter(ParameterSetName = 'Audit', Mandatory = $true)]
    [switch]$Audit,

    # FFC's default for every zone it manages: AI crawlers MAY read a charity's
    # public site. Turns Cloudflare's "block AI bots" OFF and the AI Labyrinth OFF.
    [Parameter(ParameterSetName = 'Allow', Mandatory = $true)]
    [switch]$Allow,

    # The explicit opt-out for a zone whose charity asks for it.
    [Parameter(ParameterSetName = 'Block', Mandatory = $true)]
    [switch]$Block,

    # With -Allow / -Block: decide and PRINT the change, write nothing. This is
    # what 106 passes under dry_run. Exit 0 on noop or a printed plan, 1 on a
    # refusal - a rehearsal that cannot read the shape must still say so.
    [Parameter(ParameterSetName = 'Allow')]
    [Parameter(ParameterSetName = 'Block')]
    [switch]$DryRun,

    # Optional override of the env-discovered tokens (tests, local runs).
    [Parameter()]
    [array]$Tokens
)

<#
.SYNOPSIS
    Audit or set a Cloudflare zone's AI-crawler posture. FFC's default is ALLOW.

.DESCRIPTION
    Cloudflare now blocks AI crawlers on new zones unless told otherwise, and
    it is a zone-level setting that nothing in this repo touched until #37
    (NHEG). FFC's position is the opposite of Cloudflare's default: the sites
    FFC hosts are 501(c)(3) charities' public information, and being findable by
    assistants and search engines is the point of publishing them. So every
    zone 110 creates, and every zone 106 enforces, is set to ALLOW unless the
    dispatcher opts it out.

    Six settings on `/zones/{id}/bot_management` carry the posture:

        ai_bots_protection       "block" | "disabled"   -- Block AI Bots toggle
        crawler_protection       "enabled" | "disabled" -- AI Labyrinth
        ai_training              "block" | "disabled"   -- per-category: training crawlers
        ai_search                "block" | "disabled"   -- per-category: search / indexing
        ai_user                  "block" | "disabled"   -- per-category: user-initiated fetches
        content_bots_protection  "block" | "disabled"   -- content-scraping bots

    The first two were in the field map from day one. The other four were
    MEASURED on 2026-10-02, on the first live run (110 run 36952204684,
    zone newheightseducation.org): Cloudflare returned all six, every one
    `disabled`, so `allow` on those four is confirmed and `block` on them is
    inferred from the vocabulary `ai_bots_protection` uses. A write that
    Cloudflare refuses fails loudly here, never silently.

    The exact field set Cloudflare returns is the one thing this script does
    NOT assume. The API reference was not reachable from the sandbox that wrote
    this, so the decision is made from the GET response rather than from a
    remembered schema: a response that does not carry `ai_bots_protection` is
    REFUSED, naming the keys it did carry, instead of being patched on faith.
    The same choice as `missingTooling` in optimize-captured-assets.mjs - fail
    closed on a shape you cannot confirm. -Audit prints the full response for
    exactly this reason: the first live run is the measurement.

    Every write is confirmed by re-reading the setting (CLAUDE.md: confirm a
    GitHub/Cloudflare write by re-reading the state it should have changed,
    never from the POST body).

.PARAMETER Zone
    The zone name. Resolved across both FFC Cloudflare accounts via the shared
    Resolve-CfZone, so no -Account is needed.

.EXAMPLE
    .\scripts\cloudflare-ai-crawlers.ps1 -Zone example.org -Audit
    .\scripts\cloudflare-ai-crawlers.ps1 -Zone example.org -Allow
    .\scripts\cloudflare-ai-crawlers.ps1 -Zone example.org -Allow -DryRun

.NOTES
    Exit codes: 0 applied / already in the desired state / audit or dry-run plan printed;
                1 refused, write failed, or re-read disagrees;
                2 usage: no tokens, or the zone is not visible to any token.

    A 403 on the PUT means the wr-all token lacks the Bot Management (or Zone
    Settings) edit permission. That is a Cloudflare-dashboard change to the
    token, not something this script can grant itself; the message says so.
#>

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'cloudflare-api-common.ps1')

# ---------------------------------------------------------------------------
# The decision, pure and testable
# ---------------------------------------------------------------------------

function Resolve-AiCrawlerPatch {
    <#
        Given the zone's CURRENT bot_management object and a desired posture,
        decide what to do. Returns a hashtable:

            Action  'noop' | 'patch' | 'refuse'
            Body    the PUT body for 'patch' (only keys the response CARRIES)
            Reason  one line a human can act on

        Pure: no network, no env. Everything the live run decides about WHAT to
        write comes out of here, which is what tests/cloudflare-ai-crawlers.Tests.ps1
        pins - the HTTP around it cannot be unit-tested without a zone.
    #>
    param(
        # AllowNull: a `result: null` from the GET must reach the refusal below
        # and be named as such, not die at the binder with a message about
        # argument binding that says nothing about the zone.
        [Parameter(Mandatory = $true)][AllowNull()]$Current,
        [Parameter(Mandatory = $true)][ValidateSet('allow', 'block')][string]$Desired
    )

    $names = @()
    if ($null -ne $Current) {
        $names = @($Current.PSObject.Properties | ForEach-Object { $_.Name })
    }

    # Fail closed on an unrecognised shape. `ai_bots_protection` is the one
    # field the posture cannot do without; patching a response that lacks it
    # would be writing to a schema this script only remembers.
    if ($names -notcontains 'ai_bots_protection') {
        $have = if ($names.Count) { $names -join ', ' } else { '(no properties)' }
        return @{
            Action = 'refuse'
            Body   = @{}
            Reason = "bot_management response carries no 'ai_bots_protection' field; it has: $have. Refusing to write to a shape this script cannot confirm - read the -Audit output and update the field map."
        }
    }

    # Desired values per field. Only fields PRESENT in the response are sent,
    # so a plan that lacks the AI Labyrinth toggle (or the per-category
    # fields) is not asked to set it. The order is the order they are
    # reported in.
    $want = if ($Desired -eq 'allow') {
        [ordered]@{
            ai_bots_protection      = 'disabled'
            crawler_protection      = 'disabled'
            ai_training             = 'disabled'
            ai_search               = 'disabled'
            ai_user                 = 'disabled'
            content_bots_protection = 'disabled'
        }
    }
    else {
        [ordered]@{
            ai_bots_protection      = 'block'
            crawler_protection      = 'enabled'
            ai_training             = 'block'
            ai_search               = 'block'
            ai_user                 = 'block'
            content_bots_protection = 'block'
        }
    }

    $body = @{}
    $changes = @()
    foreach ($field in @($want.Keys)) {
        if ($names -notcontains $field) { continue }
        $have = [string]$Current.$field
        if ($have -ne $want[$field]) {
            $body[$field] = $want[$field]
            $changes += "$field $have -> $($want[$field])"
        }
    }

    if ($body.Count -eq 0) {
        return @{ Action = 'noop'; Body = @{}; Reason = "already '$Desired' (ai_bots_protection=$([string]$Current.ai_bots_protection))" }
    }
    return @{ Action = 'patch'; Body = $body; Reason = ($changes -join '; ') }
}

# ---------------------------------------------------------------------------
# Live path
# ---------------------------------------------------------------------------

function Get-BotManagement {
    param([Parameter(Mandatory = $true)]$ZoneRef)
    $resp = Invoke-CfApi -Method GET -Path "/zones/$($ZoneRef.ZoneId)/bot_management" -Token $ZoneRef.Token
    return $resp.result
}

try {
    $zoneRef = Resolve-CfZone -Domain $Zone -Tokens $Tokens
    if (-not $zoneRef) {
        Write-Output "::error::Zone '$Zone' is not visible to any Cloudflare token in the environment (FFC, CM). Create it first (workflow 110) or check the token scopes."
        exit 2
    }
    Write-Output "Zone $($zoneRef.ZoneName) ($($zoneRef.ZoneId)) via the $($zoneRef.Account) token"

    $current = Get-BotManagement -ZoneRef $zoneRef
    Write-Output '--- bot_management (as returned by Cloudflare) ---'
    Write-Output ($current | ConvertTo-Json -Depth 6)

    if ($Audit) { exit 0 }

    $desired = if ($Allow) { 'allow' } elseif ($Block) { 'block' } else {
        throw 'neither -Allow nor -Block was bound; the parameter sets should make this unreachable'
    }
    $plan = Resolve-AiCrawlerPatch -Current $current -Desired $desired

    switch ($plan.Action) {
        'noop' {
            Write-Output "No change: $($plan.Reason)"
            exit 0
        }
        'refuse' {
            Write-Output "::error::$($plan.Reason)"
            exit 1
        }
        'patch' {
            if ($DryRun) {
                Write-Output "DRY RUN: would set AI crawlers to '$desired' ($($plan.Reason)) with PUT /zones/$($zoneRef.ZoneId)/bot_management body $($plan.Body | ConvertTo-Json -Compress). Nothing written."
                exit 0
            }
            Write-Output "Setting AI crawlers to '$desired': $($plan.Reason)"
            try {
                $null = Invoke-CfApi -Method PUT -Path "/zones/$($zoneRef.ZoneId)/bot_management" -Token $zoneRef.Token -Body $plan.Body
            }
            catch {
                $msg = $_.Exception.Message
                if ($msg -match 'HTTP 403|code.?:\s*10000|not authorized|Authentication error') {
                    Write-Output "::error::Cloudflare refused the bot_management write (permissions). The wr-all Cloudflare token needs 'Bot Management: Edit' (and/or 'Zone Settings: Edit') added in the Cloudflare dashboard; this script cannot grant it. Detail: $msg"
                    exit 1
                }
                throw
            }

            # Confirm by re-reading, never from the PUT's own body.
            $after = Get-BotManagement -ZoneRef $zoneRef
            $bad = @()
            foreach ($field in $plan.Body.Keys) {
                if ([string]$after.$field -ne [string]$plan.Body[$field]) {
                    $bad += "$field is '$([string]$after.$field)', expected '$($plan.Body[$field])'"
                }
            }
            if ($bad.Count) {
                Write-Output "::error::Write did not take - re-read disagrees: $($bad -join '; ')"
                exit 1
            }
            Write-Output "Confirmed by re-read: $(($plan.Body.Keys | ForEach-Object { "$_=$([string]$after.$_)" }) -join ', ')"
            exit 0
        }
        default {
            Write-Output "::error::Resolve-AiCrawlerPatch returned an unknown action '$($plan.Action)'"
            exit 1
        }
    }
}
catch {
    $msg = $_.Exception.Message
    Write-Output "::error::cloudflare-ai-crawlers.ps1 failed: $msg"
    # Usage, not failure: no token in the environment is the caller's wiring
    # (the cloudflare-tokens-from-kv step did not run or did not export).
    if ($msg -like 'No Cloudflare tokens available*') { exit 2 }
    exit 1
}
