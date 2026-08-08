<#
.SYNOPSIS
    Read a zone's Cloudflare Cache Rules and report whether error responses can
    be edge-cached.

.DESCRIPTION
    READ-ONLY. Changes nothing. Reads two things and joins them into one
    verdict:

      1. GET /zones/{id}/rulesets/phases/http_request_cache_settings/entrypoint
         -- the zone's Cache Rules (same phase-entrypoint shape that
         Set-CloudflareRedirectRule.ps1 uses for http_request_dynamic_redirect).
         A 404 here is not an error: it means the zone has no Cache Rules at
         all and is running on Cloudflare's defaults.
      2. GET /zones/{id}/settings/{cache_level,browser_cache_ttl,always_online}
         -- the zone-level cache posture the rules sit on top of.

    THE QUESTION THIS ANSWERS
    Cloudflare does not normally cache a 429, but it will when the response
    arrives carrying an explicit Cache-Control. On 2026-08-07 the
    freeforcharity.org origin did exactly that: an .htaccess rule stamps
    `Cache-Control: public, max-age=31536000, immutable` on responses by file
    extension with no status condition, so a transient LiteSpeed 429 was
    frozen at the edge for a year and served as an empty response for ~16
    hours while the origin was healthy. See docs/cloudflare-cache.md.

    Cache Rules are the Cloudflare-side control that makes that impossible.
    An `edge_ttl` block with `status_code_ttl` entries covering 4xx/5xx caps or
    forbids caching for those statuses regardless of what the origin says;
    `edge_ttl.mode = 'respect_origin'` (the default posture) is precisely the
    setting that honours the origin header on an error. This script reports
    which of those is in force.

    Findings are advisory. Each carries a severity so a caller can gate on
    them, but nothing here is a policy decision: it reports what the zone does,
    names the rules involved, and leaves the choice to a human.

    Human-readable lines go to stderr so stdout stays strictly the final JSON
    verdict object.

.PARAMETER Domain
    The zone to inspect, e.g. freeforcharity.org. Resolved through
    Resolve-CfZone (FFC token first, then CM).

.PARAMETER FailOnFinding
    Exit non-zero when any finding of severity 'warn' or higher is present.
    Off by default so the script stays a reporting tool; the audit workflow
    turns it on.

.OUTPUTS
    A single JSON verdict object on stdout.

.EXAMPLE
    pwsh -File scripts/cloudflare-cache-rules-get.ps1 -Domain freeforcharity.org

.EXAMPLE
    pwsh -File scripts/cloudflare-cache-rules-get.ps1 -Domain freeforcharity.org -FailOnFinding
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Domain,

    [Parameter()]
    [switch]$FailOnFinding
)

$ErrorActionPreference = 'Stop'

# Shared Cloudflare plumbing: Invoke-CfApi, Resolve-CfZone, Get-CfEnvTokens (#778).
. (Join-Path $PSScriptRoot 'cloudflare-api-common.ps1')

$script:CachePhase = 'http_request_cache_settings'

# Zone settings worth reading alongside the rules. Each is its own endpoint.
$script:ZoneSettingKeys = @('cache_level', 'browser_cache_ttl', 'always_online')

# Diagnostics go to stderr so stdout is strictly the final JSON object.
function Write-Diag {
    param([Parameter(Mandatory = $true)][string]$Message)
    [Console]::Error.WriteLine($Message)
}

function Test-CoversErrorStatus {
    <#
        True when a status_code_ttl entry applies to any 4xx or 5xx status.

        Cloudflare expresses these two ways and both appear in real rulesets:
        a single `status_code`, or a `status_code_range` with optional `from` /
        `to`. An open-ended range (`from` only, or `to` only) is treated as
        reaching the missing bound, which is how Cloudflare evaluates it.
    #>
    [OutputType([bool])]
    param([Parameter(Mandatory = $true)][object]$Entry)

    if ($Entry.PSObject.Properties.Name -contains 'status_code' -and $null -ne $Entry.status_code) {
        return ([int]$Entry.status_code -ge 400)
    }

    if ($Entry.PSObject.Properties.Name -contains 'status_code_range' -and $null -ne $Entry.status_code_range) {
        $range = $Entry.status_code_range
        $from = 100
        $to = 599
        if ($range.PSObject.Properties.Name -contains 'from' -and $null -ne $range.from) { $from = [int]$range.from }
        if ($range.PSObject.Properties.Name -contains 'to' -and $null -ne $range.to) { $to = [int]$range.to }
        # Overlaps [400, 599] at all.
        return ($to -ge 400 -and $from -le 599)
    }

    return $false
}

function Get-ZoneSetting {
    <#
        Reads one zone setting. Returns $null rather than throwing when the
        setting is unavailable on the plan or the token lacks the read -- an
        absent setting must not sink the whole audit.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$ZoneId,
        [Parameter(Mandatory = $true)][string]$Token,
        [Parameter(Mandatory = $true)][string]$Key
    )

    try {
        $resp = Invoke-CfApi -Method GET -Token $Token -Path "/zones/$ZoneId/settings/$Key"
        return $resp.result.value
    }
    catch {
        Write-Diag ("  setting '{0}' unavailable: {1}" -f $Key, $_.Exception.Message)
        return $null
    }
}

try {
    $zone = Resolve-CfZone -Domain $Domain
    if (-not $zone) {
        throw "No available Cloudflare token can see zone '$Domain'. Checked FFC then CM."
    }
    Write-Diag ("Zone '{0}' resolved to {1} via the {2} token." -f $Domain, $zone.ZoneId, $zone.Account)

    # --- Cache Rules (phase entrypoint) ----------------------------------------
    # A 404 means "no Cache Rules configured", which is a legitimate state and
    # the most likely one on a zone nobody has tuned. Distinguish it from a
    # genuine failure so the verdict can say which.
    $ruleset = $null
    $hasEntrypoint = $false
    try {
        $resp = Invoke-CfApi -Method GET -Token $zone.Token -Path "/zones/$($zone.ZoneId)/rulesets/phases/$script:CachePhase/entrypoint"
        $ruleset = $resp.result
        $hasEntrypoint = $true
    }
    catch {
        if ($_.Exception.Message -match 'HTTP 404') {
            Write-Diag 'No Cache Rules entrypoint on this zone (running on Cloudflare defaults).'
        }
        else {
            throw
        }
    }

    $rules = @()
    if ($hasEntrypoint -and $ruleset -and $ruleset.rules) {
        $rules = @($ruleset.rules)
    }
    Write-Diag ("Cache Rules found: {0}" -f $rules.Count)

    # --- Summarize each rule ---------------------------------------------------
    $ruleSummaries = @()
    $errorStatusCovered = $false

    foreach ($rule in $rules) {
        $ap = $rule.action_parameters
        $edgeTtlMode = $null
        $edgeTtlDefault = $null
        $statusTtlEntries = @()
        $cacheEnabled = $null

        if ($ap) {
            if ($ap.PSObject.Properties.Name -contains 'cache' -and $null -ne $ap.cache) { $cacheEnabled = [bool]$ap.cache }
            if ($ap.PSObject.Properties.Name -contains 'edge_ttl' -and $null -ne $ap.edge_ttl) {
                $edgeTtlMode = $ap.edge_ttl.mode
                if ($ap.edge_ttl.PSObject.Properties.Name -contains 'default') { $edgeTtlDefault = $ap.edge_ttl.default }
                if ($ap.edge_ttl.PSObject.Properties.Name -contains 'status_code_ttl' -and $ap.edge_ttl.status_code_ttl) {
                    foreach ($entry in @($ap.edge_ttl.status_code_ttl)) {
                        $covers = Test-CoversErrorStatus -Entry $entry
                        if ($covers) { $errorStatusCovered = $true }
                        $statusTtlEntries += [ordered]@{
                            statusCode       = if ($entry.PSObject.Properties.Name -contains 'status_code') { $entry.status_code } else { $null }
                            statusCodeRange  = if ($entry.PSObject.Properties.Name -contains 'status_code_range') { $entry.status_code_range } else { $null }
                            value            = if ($entry.PSObject.Properties.Name -contains 'value') { $entry.value } else { $null }
                            coversErrorCodes = $covers
                        }
                    }
                }
            }
        }

        $ruleSummaries += [ordered]@{
            id             = $rule.id
            description    = $rule.description
            expression     = $rule.expression
            action         = $rule.action
            enabled        = if ($rule.PSObject.Properties.Name -contains 'enabled') { [bool]$rule.enabled } else { $true }
            cache          = $cacheEnabled
            edgeTtlMode    = $edgeTtlMode
            edgeTtlDefault = $edgeTtlDefault
            statusCodeTtl  = @($statusTtlEntries)
        }
    }

    # --- Zone settings ---------------------------------------------------------
    $settings = [ordered]@{}
    foreach ($key in $script:ZoneSettingKeys) {
        $settings[$key] = Get-ZoneSetting -ZoneId $zone.ZoneId -Token $zone.Token -Key $key
    }

    # --- Findings --------------------------------------------------------------
    $findings = @()

    if (-not $errorStatusCovered) {
        # The condition behind the 2026-08-07 incident: with nothing capping TTL
        # by status, an origin Cache-Control on a 4xx/5xx is honoured as-is.
        $findings += [ordered]@{
            severity = 'warn'
            code     = 'no-error-status-ttl'
            message  = 'No Cache Rule caps edge TTL for 4xx/5xx responses. An origin that sends Cache-Control on an error response can have that error cached at the edge for its full max-age.'
            remedy   = 'Add a Cache Rule with edge_ttl.status_code_ttl covering 400-599 set to no-store, or stop the origin sending a long Cache-Control on non-200 responses.'
        }
    }

    $respectOriginRules = @($ruleSummaries | Where-Object { $_.edgeTtlMode -eq 'respect_origin' })
    if ($respectOriginRules.Count -gt 0 -and -not $errorStatusCovered) {
        $findings += [ordered]@{
            severity = 'info'
            code     = 'respect-origin-without-status-cap'
            message  = ("{0} rule(s) use edge_ttl.mode 'respect_origin' with no status-code cap, so the origin fully controls edge TTL including on errors." -f $respectOriginRules.Count)
            remedy   = 'Pair respect_origin with a status_code_ttl entry for 400-599.'
        }
    }

    if ($rules.Count -eq 0) {
        $findings += [ordered]@{
            severity = 'info'
            code     = 'no-cache-rules'
            message  = 'Zone has no Cache Rules; caching follows Cloudflare defaults and origin headers.'
            remedy   = 'Not a defect on its own. It does mean there is no zone-side guard against a bad origin Cache-Control.'
        }
    }

    $blocking = @($findings | Where-Object { $_.severity -eq 'warn' -or $_.severity -eq 'error' })

    $verdict = [ordered]@{
        domain        = $Domain
        zoneId        = $zone.ZoneId
        account       = $zone.Account
        phase         = $script:CachePhase
        hasEntrypoint = $hasEntrypoint
        rulesetId     = if ($ruleset) { $ruleset.id } else { $null }
        ruleCount     = $rules.Count
        rules         = @($ruleSummaries)
        zoneSettings  = $settings
        findings      = @($findings)
        clean         = ($blocking.Count -eq 0)
    }

    foreach ($finding in $findings) {
        Write-Diag ("[{0}] {1}: {2}" -f $finding.severity.ToUpper(), $finding.code, $finding.message)
    }

    $verdict | ConvertTo-Json -Depth 10

    if ($FailOnFinding -and $blocking.Count -gt 0) {
        Write-Error ("{0} finding(s) at severity warn or higher." -f $blocking.Count)
        exit 1
    }

    exit 0
}
catch {
    Write-Error $_
    exit 1
}
