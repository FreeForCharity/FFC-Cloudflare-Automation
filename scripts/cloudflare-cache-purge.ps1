<#
.SYNOPSIS
    Purge Cloudflare's edge cache for a zone, by URL or wholesale.

.DESCRIPTION
    WRITE operation. Wraps POST /zones/{zone_id}/purge_cache.

    Two purge modes are supported, and deliberately only two:

      -Url  <list>   ->  { "files": [...] }        targeted, the default choice
      -All           ->  { "purge_everything": true }

    Cloudflare's other selectors (prefixes / hosts / tags) are Enterprise-only
    and return HTTP 400 on every other plan, so shipping them here would be
    shipping a button that cannot work on FFC zones. They are intentionally
    absent rather than present-and-broken; add them the day a zone is upgraded.

    WHY THIS EXISTS (the 2026-08-07 freeforcharity.org incident)
    LiteSpeed at the cPanel origin answered a burst of asset requests with 429.
    The origin's static-asset rule stamps `Cache-Control: public,
    max-age=31536000, immutable` on responses by file extension without
    conditioning on status, so those 429s carried a one-year TTL -- and
    Cloudflare caches a 429 when it arrives with an explicit Cache-Control.
    Four homepage SVGs and a Next.js JS chunk were then served as empty 429s
    from the edge for ~16 hours, with the origin healthy the whole time. The
    only remedy is a purge, which until now had no automation and no audit
    trail. See docs/cloudflare-cache.md.

    Purging is safe but not free: every purged object is refetched from the
    origin on next request. -All on a busy zone produces exactly the origin
    burst that caused the incident, so -Url is the default posture and -All
    requires its own switch.

    Requests are batched at 30 URLs per call (Cloudflare's documented maximum
    for non-Enterprise `files` purges). A batch that fails does not stop the
    remaining batches; every batch's outcome lands in the verdict and any
    failure makes the exit code non-zero.

    Human-readable lines go to stderr so stdout stays strictly the final JSON
    verdict object -- same contract as cloudflare-registrar-access-check.ps1.

.PARAMETER Domain
    The zone to purge, e.g. freeforcharity.org. Resolved through
    Resolve-CfZone, which probes the FFC token then the CM token and uses
    whichever can see the zone.

.PARAMETER Url
    One or more absolute URLs to purge. Must be fully qualified
    (https://host/path) -- Cloudflare rejects bare paths. Mutually exclusive
    with -All.

    NOTE: `pwsh -File` binds only a SINGLE value to a [string[]] parameter, so
    repeated -Url flags on a console invocation silently purge just the first
    one. Use -UrlList from a console; -Url is for in-process callers, where
    array binding works normally.

.PARAMETER UrlList
    The same list as a single delimited string (newline, comma or space
    separated). This exists so a `workflow_dispatch` input -- which is always a
    string -- can be handed straight through without the calling YAML doing any
    parsing of its own. Splitting here keeps that logic in a file Pester can
    test instead of embedded in a workflow. Combines with -Url; both are
    merged, trimmed and de-duplicated.

.PARAMETER All
    Purge the entire zone. Mutually exclusive with -Url.

.PARAMETER Verify
    After purging, re-request each purged URL and report its HTTP status and
    cf-cache-status. This is what actually proves the purge landed: a URL that
    still answers 429 with `cf-cache-status: HIT` was not purged. Ignored for
    -All (there is no URL list to re-probe).

.PARAMETER DryRun
    Print the exact request body that would be sent and exit without calling
    Cloudflare. No purge is performed.

.OUTPUTS
    A single JSON verdict object on stdout.

.EXAMPLE
    pwsh -File scripts/cloudflare-cache-purge.ps1 -Domain freeforcharity.org `
        -UrlList 'https://freeforcharity.org/Svgs/FFC-Consulting.svg' -Verify

.EXAMPLE
    # Several URLs from a console: comma-separate them into -UrlList. Repeated
    # -Url flags would bind only the first (see the -Url note above).
    pwsh -File scripts/cloudflare-cache-purge.ps1 -Domain freeforcharity.org `
        -UrlList 'https://freeforcharity.org/a.svg,https://freeforcharity.org/b.js' -Verify

.EXAMPLE
    pwsh -File scripts/cloudflare-cache-purge.ps1 -Domain freeforcharity.org -All -DryRun
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Domain,

    [Parameter()]
    [string[]]$Url,

    [Parameter()]
    [string]$UrlList,

    [Parameter()]
    [switch]$All,

    [Parameter()]
    [switch]$Verify,

    [Parameter()]
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

# Shared Cloudflare plumbing: Invoke-CfApi, Resolve-CfZone, Get-CfEnvTokens (#778).
. (Join-Path $PSScriptRoot 'cloudflare-api-common.ps1')

# Cloudflare's documented ceiling for a single `files` purge on non-Enterprise plans.
$script:PurgeBatchSize = 30

# Diagnostics go to stderr so stdout is strictly the final JSON object.
function Write-Diag {
    param([Parameter(Mandatory = $true)][string]$Message)
    [Console]::Error.WriteLine($Message)
}

function Split-IntoBatch {
    <#
        Splits a flat list into fixed-size chunks. Returns an array of arrays.

        TWO THINGS ARE LOAD-BEARING HERE, and they only work as a pair:

          1. `return , $batches` -- PowerShell unrolls an array returned to the
             pipeline. A plain `return $batches` on the single-batch case (the
             common one) emits the INNER array, so the caller gets a flat list
             of URLs instead of one batch of them and sends a separate purge
             request per URL.
          2. The caller must assign it BARE -- `$x = Split-IntoBatch ...`, never
             `$x = @(Split-IntoBatch ...)`. `@()` around a single array-valued
             pipeline result wraps it in another layer rather than flattening
             it, which re-breaks exactly what the comma fixed.

        Verified both directions in tests/cloudflare-cache.Tests.ps1 rather than
        reasoned about: the nesting assertions there fail if either half is
        changed on its own.
    #>
    [OutputType([array])]
    param(
        [Parameter(Mandatory = $true)][string[]]$Items,
        [Parameter(Mandatory = $true)][int]$Size
    )

    $batches = @()
    for ($i = 0; $i -lt $Items.Count; $i += $Size) {
        $end = [Math]::Min($i + $Size - 1, $Items.Count - 1)
        $batches += , @($Items[$i..$end])
    }
    return , $batches
}

function Test-AbsoluteHttpUrl {
    # Cloudflare rejects bare paths; catch that here with a message that names
    # the offending value rather than letting it come back as a generic 400.
    [OutputType([bool])]
    param([Parameter(Mandatory = $true)][string]$Value)

    $parsed = $null
    if (-not [uri]::TryCreate($Value, [UriKind]::Absolute, [ref]$parsed)) { return $false }
    return ($parsed.Scheme -eq 'http' -or $parsed.Scheme -eq 'https')
}

function Resolve-UrlTarget {
    <#
        Merges -Url and -UrlList into one trimmed, de-duplicated list.
        -UrlList splits on newline, comma or whitespace so a workflow input can
        be pasted in any of the shapes a human actually types.
    #>
    [OutputType([string[]])]
    param(
        [Parameter()][string[]]$FromArray,
        [Parameter()][string]$FromString
    )

    $collected = @()
    if ($FromArray) { $collected += $FromArray }
    if (-not [string]::IsNullOrWhiteSpace($FromString)) {
        $collected += ($FromString -split '[\r\n,\s]+')
    }

    return @(
        $collected |
            ForEach-Object { $_.Trim() } |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
            Select-Object -Unique
    )
}

try {
    # --- Mode selection (exactly one of -Url/-UrlList / -All) ------------------
    $targets = @(Resolve-UrlTarget -FromArray $Url -FromString $UrlList)
    $hasUrls = ($targets.Count -gt 0)

    if ($hasUrls -and $All) {
        throw 'Specify either -Url/-UrlList or -All, not both.'
    }
    if (-not $hasUrls -and -not $All) {
        throw 'Nothing to purge. Pass -Url/-UrlList for a targeted purge, or -All to purge the whole zone.'
    }

    $mode = if ($All) { 'everything' } else { 'files' }

    if ($hasUrls) {
        $bad = @($targets | Where-Object { -not (Test-AbsoluteHttpUrl -Value $_) })
        if ($bad.Count -gt 0) {
            throw ("These values are not absolute http(s) URLs: {0}" -f ($bad -join ', '))
        }
    }

    # --- Resolve the zone ------------------------------------------------------
    $zone = Resolve-CfZone -Domain $Domain
    if (-not $zone) {
        throw "No available Cloudflare token can see zone '$Domain'. Checked FFC then CM."
    }
    Write-Diag ("Zone '{0}' resolved to {1} via the {2} token." -f $Domain, $zone.ZoneId, $zone.Account)

    $batches = @()
    if ($mode -eq 'files') {
        # Bare assignment, not @(...) -- see the note in Split-IntoBatch.
        $batches = Split-IntoBatch -Items $targets -Size $script:PurgeBatchSize
        Write-Diag ("Purging {0} URL(s) in {1} batch(es) of up to {2}." -f $targets.Count, $batches.Count, $script:PurgeBatchSize)
    }
    else {
        Write-Diag 'Purging EVERYTHING in this zone. Every cached object refetches from origin on next request.'
    }

    # --- Dry run: show the payload, change nothing -----------------------------
    if ($DryRun) {
        $preview = @()
        if ($mode -eq 'everything') {
            $preview += , @{ purge_everything = $true }
        }
        else {
            foreach ($batch in $batches) { $preview += , @{ files = @($batch) } }
        }

        $verdict = [ordered]@{
            domain    = $Domain
            zoneId    = $zone.ZoneId
            account   = $zone.Account
            mode      = $mode
            dryRun    = $true
            requested = $targets.Count
            batches   = $preview.Count
            payloads  = $preview
            unhealthy = 0
            purged    = @()
            failures  = @()
            success   = $true
        }
        Write-Diag 'DRY RUN: no purge issued.'
        $verdict | ConvertTo-Json -Depth 8
        exit 0
    }

    # --- Apply -----------------------------------------------------------------
    $purged = @()
    $failures = @()

    if ($mode -eq 'everything') {
        try {
            $resp = Invoke-CfApi -Method POST -Token $zone.Token -Path "/zones/$($zone.ZoneId)/purge_cache" -Body @{ purge_everything = $true }
            Write-Diag ("  purge_everything: OK (id {0})" -f $resp.result.id)
            $purged += 'purge_everything'
        }
        catch {
            Write-Diag ("  purge_everything: FAILED -- {0}" -f $_.Exception.Message)
            $failures += [ordered]@{ batch = 1; items = @('purge_everything'); error = $_.Exception.Message }
        }
    }
    else {
        $batchNumber = 0
        foreach ($batch in $batches) {
            $batchNumber++
            try {
                $null = Invoke-CfApi -Method POST -Token $zone.Token -Path "/zones/$($zone.ZoneId)/purge_cache" -Body @{ files = @($batch) }
                Write-Diag ("  batch {0}/{1}: OK ({2} URL(s))" -f $batchNumber, $batches.Count, $batch.Count)
                $purged += $batch
            }
            catch {
                # Keep going: one rejected batch should not strand the rest.
                Write-Diag ("  batch {0}/{1}: FAILED -- {2}" -f $batchNumber, $batches.Count, $_.Exception.Message)
                $failures += [ordered]@{ batch = $batchNumber; items = @($batch); error = $_.Exception.Message }
            }
        }
    }

    # --- Verify (targeted purges only) -----------------------------------------
    # A purge that "succeeded" per the API but left the object cached is the
    # failure mode worth catching, so re-probe rather than trust the 200.
    $verification = @()
    if ($Verify -and $mode -eq 'files' -and $purged.Count -gt 0) {
        Write-Diag 'Verifying purged URLs...'
        foreach ($target in $purged) {
            try {
                # No -MaximumRedirection 0 here: PS7's Invoke-WebRequest throws
                # on a 3xx under that flag even with -SkipHttpErrorCheck (the
                # same trap documented in 111-dns-create-redirect-rule.yml), and
                # the final status is what we want anyway.
                $probe = Invoke-WebRequest -Uri $target -Method GET -SkipHttpErrorCheck -TimeoutSec 30 -ErrorAction Stop
                $cacheStatus = $null
                if ($probe.Headers.ContainsKey('cf-cache-status')) {
                    $cacheStatus = ($probe.Headers['cf-cache-status'] | Select-Object -First 1)
                }
                $entry = [ordered]@{
                    url           = $target
                    status        = [int]$probe.StatusCode
                    cfCacheStatus = $cacheStatus
                    healthy       = ([int]$probe.StatusCode -ge 200 -and [int]$probe.StatusCode -lt 400)
                }
                Write-Diag ("  {0} -> HTTP {1} (cf-cache-status: {2})" -f $target, $entry.status, $cacheStatus)
                $verification += $entry
            }
            catch {
                Write-Diag ("  {0} -> probe failed: {1}" -f $target, $_.Exception.Message)
                $verification += [ordered]@{ url = $target; status = 0; cfCacheStatus = $null; healthy = $false; error = $_.Exception.Message }
            }
        }
    }

    $unhealthy = @($verification | Where-Object { -not $_.healthy })

    $verdict = [ordered]@{
        domain       = $Domain
        zoneId       = $zone.ZoneId
        account      = $zone.Account
        mode         = $mode
        dryRun       = $false
        requested    = $targets.Count
        batches      = if ($mode -eq 'everything') { 1 } else { $batches.Count }
        purged       = @($purged)
        failures     = @($failures)
        verification = @($verification)
        unhealthy    = $unhealthy.Count
        # `success` must agree with the exit code. It covers BOTH halves --
        # the purge calls and the verification probes -- because a caller
        # consuming stdout as the authoritative verdict would otherwise read
        # success=true on a run that exited 1 because the edge was still
        # serving the bad object. `failures` and `unhealthy` say which half.
        success      = ($failures.Count -eq 0 -and $unhealthy.Count -eq 0)
    }

    $verdict | ConvertTo-Json -Depth 8

    if ($failures.Count -gt 0) {
        Write-Error ("{0} purge batch(es) failed." -f $failures.Count)
        exit 1
    }
    if ($unhealthy.Count -gt 0) {
        # The purge API accepted it but the edge still serves a bad response.
        Write-Error ("{0} URL(s) still unhealthy after purge." -f $unhealthy.Count)
        exit 1
    }

    exit 0
}
catch {
    Write-Error $_
    exit 1
}
