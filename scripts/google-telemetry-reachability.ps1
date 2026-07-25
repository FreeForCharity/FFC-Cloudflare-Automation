<#
.SYNOPSIS
    Fleet telemetry reachability: which FFC sites have reachable GTM, GA4 and Search Console,
    and how much traffic each actually gets.

.DESCRIPTION
    Read-only. Answers four questions the fleet could not previously answer:

      1. Which domains have a GTM container, and is any container SHARED between sites?
      2. Which domains have a GA4 property + web stream?
      3. Which domains are verified in Search Console?
      4. How much traffic does each get — ranked, so rollout order is measured, not alphabetical.

    Two design decisions carry most of the value:

    * **Served HTML is checked against the API.** A domain can have a GTM container provisioned and
      still not load it (component present but never imported), or load a container the API does not
      know about (hardcoded template id). Provisioning records and reality disagree often enough
      that the disagreement is a first-class output, not an afterthought.

    * **"no data" is never conflated with "no traffic".** A site with no GA4 property is UNMEASURED;
      a site with a property and zero sessions has NO TRAFFIC. Collapsing those sorts unmeasured
      sites to the bottom of a traffic-ordered rollout, which is precisely how a site that matters
      gets treated as if it does not.

    GA4 properties are matched to domains by web-stream `defaultUri`, never by property display
    name — docs/google-api.md records display names as historically unreliable.

    TWO AUTH PATHS, and conflating them is a 401 on every call:
      * Tag Manager, Search Console, GA4 ADMIN API — Workspace-adjacent. Need a DWD token minted
        from the ffc-workspace-admin SA key impersonating a Workspace admin (-WorkspaceKeyPath).
      * GA4 DATA API (traffic) — plain service-account token from ADC
        (GOOGLE_APPLICATION_CREDENTIALS), as 501/502 use.

.PARAMETER Domains
    Domains to report on. Defaults to every live domain in the convergence set.

    The templates ARE live and ARE testable: FFC_Single_Page_Template serves
    ffcworkingsite1.org (public/CNAME), and Footer_Only_Template serves its GitHub Pages project
    URL. Treating a template as "not a real site" is how template-only defects — the shared
    GTM-TQ5H8HPR container, the sitemap/trailingSlash mismatch — survive to reach charity sites.
    They get probed like anything else.

    Designated low-risk test sites (safe to modify freely) are marked in the default list.

.PARAMETER GtmAccountId
    FFC GTM account. Default 4702611686.

.PARAMETER GaAccountName
    GA4 account holding charity properties. Default 'FFC Supported Sites'.

.PARAMETER Subject
    Workspace user the service account impersonates for DWD.

.PARAMETER Days
    Traffic lookback window. Default 28.

.PARAMETER OutputPath
    Where to write the JSON report.

.PARAMETER SkipLiveProbe
    Skip fetching served HTML (offline/dry runs).
#>
[CmdletBinding()]
param(
    [string[]]$Domains = @(
        # FFC production — the proof points. Changes here are measured before the fleet follows.
        'freeforcharity.org',
        'ffcadmin.org',
        # Templates. LIVE and testable, not abstractions: SPT serves ffcworkingsite1.org via
        # public/CNAME; FOT serves its Pages project URL. Probed like any other site.
        'ffcworkingsite1.org',
        'freeforcharity.github.io/FFC-IN-Footer_Only_Template',
        # Designated low-risk test sites — safe to modify, test and iterate on.
        'technologymonastery.org',
        'amargraves.org',
        'makeacalendarinvite.org'
    ),
    [string]$GtmAccountId = '4702611686',
    [string]$GaAccountName = 'FFC Supported Sites',
    [string]$Subject = 'clarkemoyer@freeforcharity.org',
    [int]$Days = 28,
    [string]$OutputPath = 'telemetry-reachability.json',
    # Workspace SA key (DWD). REQUIRED for Tag Manager, Search Console and the GA4 ADMIN API:
    # domain-wide delegation is configured on the ffc-workspace-admin service account, NOT on the
    # analytics SA that GOOGLE_APPLICATION_CREDENTIALS points at. 501's gsc-smoke, 503 and 505 all
    # download this key separately for exactly this reason.
    [string]$WorkspaceKeyPath,
    [switch]$SkipLiveProbe
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot/google-api-common.ps1"

# Every API call records its own outcome. A probe that FAILED must never render as a probe that
# found nothing — that is the failure mode this whole report exists to expose, so the collector
# is not allowed to commit it either.
$script:Errors = [System.Collections.Generic.List[string]]::new()

# Resolved once so every DWD call uses the same key. Left $null when not supplied, which makes
# Get-GoogleDwdAccessToken fall back to ADC — wrong for these APIs, so the probes will fail loudly
# rather than silently returning nothing.
$script:WorkspaceKey = $WorkspaceKeyPath

function Invoke-Probe {
    param(
        [Parameter(Mandatory)][string]$Label,
        [Parameter(Mandatory)][scriptblock]$Action
    )
    try {
        return @{ ok = $true; value = (& $Action) }
    }
    catch {
        $msg = $_.Exception.Message
        $script:Errors.Add("${Label}: $msg")
        # Flatten CR/LF before emitting a workflow command: a multi-line exception message would
        # otherwise terminate the ::warning:: line and let the remainder be parsed as further
        # workflow commands, corrupting the log and the step summary.
        $safe = ($msg -replace '
?
', ' ')
        Write-Host "::warning::$Label failed — $safe"
        return @{ ok = $false; value = $null; error = $msg }
    }
}

function Invoke-GooglePagedApi {
    <#
    Follows nextPageToken and returns the concatenated items from $CollectionName.

    Not optional plumbing: the GA Admin and Tag Manager list endpoints paginate, so a single
    unpaged call silently returns a PREFIX of reality. In a report whose entire purpose is
    catching under-reporting, under-reporting itself is the worst possible bug — a site with a
    GA4 property on page 2 would be reported as having none.
  #>
    param(
        [Parameter(Mandatory)][string]$Uri,
        [Parameter(Mandatory)][string]$AccessToken,
        [Parameter(Mandatory)][string]$CollectionName,
        [int]$MaxPages = 50
    )
    $items = @()
    $token = $null
    $page = 0
    do {
        $page++
        $u = if ($token) {
            $sep = if ($Uri -match '\?') { '&' } else { '?' }
            "$Uri${sep}pageToken=$([uri]::EscapeDataString($token))"
        }
        else { $Uri }
        $resp = Invoke-GoogleApi -Uri $u -AccessToken $AccessToken
        if ($resp.PSObject.Properties.Name -contains $CollectionName -and $resp.$CollectionName) {
            $items += @($resp.$CollectionName)
        }
        $token = if ($resp.PSObject.Properties.Name -contains 'nextPageToken') { $resp.nextPageToken } else { $null }
    } while ($token -and $page -lt $MaxPages)

    if ($token) {
        # Hitting the cap means the result IS truncated. Say so loudly rather than returning a
        # quietly partial list.
        throw "Pagination cap ($MaxPages pages) reached for $Uri — result would be truncated."
    }
    return $items
}

function Normalize-Domain {
    <#
    Host, lowercased, without scheme or leading www.

    A trailing PATH is preserved when present, because a GitHub Pages project URL
    (freeforcharity.github.io/FFC-IN-Footer_Only_Template) is a real, live, testable site and
    stripping its path would collapse every project-hosted site onto the same host. Templates are
    not abstractions — Footer_Only_Template is served on the internet — and treating them as
    "not real sites" is how template-only defects reach charity sites.
  #>
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return '' }
    $d = $Value.Trim()
    $d = $d -replace '^https?://', ''
    $d = $d -replace '/+$', ''
    # Lowercase ONLY the host. GitHub Pages project paths are CASE-SENSITIVE —
    # /FFC-IN-Footer_Only_Template and /ffc-in-footer_only_template are different URLs, and
    # lowercasing the path would make the live probe 404 against a site that is up.
    $slash = $d.IndexOf('/')
    if ($slash -lt 0) {
        return ($d.ToLowerInvariant() -replace '^www\.', '')
    }
    # NB: not $host — that is a PowerShell automatic variable and assigning it is an error
    # in some hosts.
    $hostPart = $d.Substring(0, $slash).ToLowerInvariant() -replace '^www\.', ''
    return $hostPart + $d.Substring($slash)
}

# ---------------------------------------------------------------- GTM

function Get-GtmInventory {
    param([string]$AccountId, [string]$Subject)
    $tok = Get-GoogleDwdAccessToken -Subject $Subject -Scope 'https://www.googleapis.com/auth/tagmanager.readonly' -CredentialsPath $script:WorkspaceKey
    $uri = "https://tagmanager.googleapis.com/tagmanager/v2/accounts/$AccountId/containers"
    $raw = Invoke-GooglePagedApi -Uri $uri -AccessToken $tok -CollectionName 'container'
    $containers = @()
    if ($raw.Count -gt 0) {
        foreach ($c in $raw) {
            # domainName is a GTM field listing the domains a container is declared for. It may be
            # absent, a single string, or an array. Built explicitly rather than inline: @() does
            # flatten correctly here (verified — an array yields String elements, not a nested
            # Object[]), but the inline form reads as if it might nest, and container->domain
            # matching plus shared-container detection both depend on this being flat.
            $domainList = @()
            if (($c.PSObject.Properties.Name -contains 'domainName') -and $c.domainName) {
                $domainList = @($c.domainName)
            }
            $containers += [pscustomobject]@{
                name        = $c.name
                publicId    = $c.publicId
                containerId = $c.containerId
                domains     = $domainList
            }
        }
    }
    return $containers
}

# ---------------------------------------------------------------- GA4

function Get-Ga4Inventory {
    param([string]$AccountName, [string]$Subject)
    $tok = Get-GoogleDwdAccessToken -Subject $Subject -Scope 'https://www.googleapis.com/auth/analytics.readonly' -CredentialsPath $script:WorkspaceKey

    $allAccounts = Invoke-GooglePagedApi -Uri 'https://analyticsadmin.googleapis.com/v1beta/accounts' -AccessToken $tok -CollectionName 'accounts'
    $acct = $allAccounts | Where-Object { $_.displayName -eq $AccountName } | Select-Object -First 1

    $out = @()
    # Report ALL properties the token can see, not just those under the named account: a charity
    # property created outside the expected account is exactly the drift worth surfacing.
    $filters = @()
    if ($acct) { $filters += $acct.name }
    foreach ($a in $allAccounts) { if ($filters -notcontains $a.name) { $filters += $a.name } }

    foreach ($parent in $filters) {
        $props = Invoke-GooglePagedApi -Uri "https://analyticsadmin.googleapis.com/v1beta/properties?filter=parent:$parent" -AccessToken $tok -CollectionName 'properties'
        foreach ($p in $props) {
            $streams = Invoke-GooglePagedApi -Uri "https://analyticsadmin.googleapis.com/v1beta/$($p.name)/dataStreams" -AccessToken $tok -CollectionName 'dataStreams'
            $webStreams = @()
            if ($streams.Count -gt 0) {
                foreach ($s in $streams) {
                    if ($s.PSObject.Properties.Name -contains 'webStreamData' -and $s.webStreamData) {
                        $webStreams += [pscustomobject]@{
                            defaultUri    = $s.webStreamData.defaultUri
                            measurementId = $s.webStreamData.measurementId
                            displayName   = $s.displayName
                        }
                    }
                }
            }
            $out += [pscustomobject]@{
                propertyName = $p.name          # e.g. properties/123456
                displayName  = $p.displayName
                account      = $parent
                webStreams   = $webStreams
            }
        }
    }
    return $out
}

function Get-Ga4Sessions {
    param([string]$PropertyName, [int]$Days, [string]$Subject)
    # DATA API, not Admin: plain SA token from ADC — the same call 501's smoke and 502's report
    # make. It does NOT use DWD.
    $tok = Get-GoogleAccessToken -Scope 'https://www.googleapis.com/auth/analytics.readonly'
    # Pass the hashtable, NOT pre-serialized JSON: Invoke-GoogleApi runs ConvertTo-Json itself,
    # so handing it a string double-encodes the body and the API rejects it.
    $body = @{
        dateRanges = @(@{ startDate = "${Days}daysAgo"; endDate = 'today' })
        metrics    = @(@{ name = 'sessions' }, @{ name = 'activeUsers' })
    }
    $resp = Invoke-GoogleApi -Uri "https://analyticsdata.googleapis.com/v1beta/${PropertyName}:runReport" -AccessToken $tok -Method Post -Body $body
    # Get-GoogleRows exists because the Data API OMITS 'rows' entirely for an empty range —
    # reaching for $resp.rows directly throws under Set-StrictMode.
    $rows = Get-GoogleRows -Response $resp
    $sessions = 0; $users = 0
    if ($rows.Count -gt 0) {
        $sessions = [int]$rows[0].metricValues[0].value
        $users = [int]$rows[0].metricValues[1].value
    }
    return [pscustomobject]@{ sessions = $sessions; activeUsers = $users }
}

# ---------------------------------------------------------------- Search Console

function Get-GscInventory {
    param([string]$Subject)
    $tok = Get-GoogleDwdAccessToken -Subject $Subject -Scope 'https://www.googleapis.com/auth/webmasters' -CredentialsPath $script:WorkspaceKey
    $resp = Invoke-GoogleApi -Uri 'https://searchconsole.googleapis.com/webmasters/v3/sites' -AccessToken $tok
    # siteEntry is absent when the list is empty, and @($null).Count is 1 — guard both.
    if (($resp.PSObject.Properties.Name -contains 'siteEntry') -and $resp.siteEntry) {
        return @($resp.siteEntry)
    }
    return @()
}

# ---------------------------------------------------------------- Live probe

function Get-ServedTelemetry {
    param([string]$Domain)
    $url = "https://$Domain/"
    try {
        # -UseBasicParsing is a deprecated no-op under pwsh 7 (still accepted, does nothing) — omitted.
        $html = (Invoke-WebRequest -Uri $url -MaximumRedirection 5 -TimeoutSec 30).Content
    }
    catch {
        return [pscustomobject]@{ reachable = $false; error = $_.Exception.Message; gtmIds = @(); gaIds = @() }
    }
    $gtm = [regex]::Matches($html, 'GTM-[A-Z0-9]{4,}') | ForEach-Object { $_.Value } | Sort-Object -Unique
    $ga = [regex]::Matches($html, 'G-[A-Z0-9]{8,}') | ForEach-Object { $_.Value } | Sort-Object -Unique
    return [pscustomobject]@{
        reachable      = $true
        gtmIds         = @($gtm)
        gaIds          = @($ga)
        # A placeholder id shipped to production is a defect, not a configuration.
        hasPlaceholder = [bool](@($ga) | Where-Object { $_ -match '^G-X+$' })
    }
}

# ---------------------------------------------------------------- Collect

Write-Host "Collecting telemetry for $($Domains.Count) domain(s)…"

$gtmProbe = Invoke-Probe -Label 'GTM containers' -Action { Get-GtmInventory -AccountId $GtmAccountId -Subject $Subject }
$gaProbe = Invoke-Probe -Label 'GA4 properties' -Action { Get-Ga4Inventory -AccountName $GaAccountName -Subject $Subject }
$gscProbe = Invoke-Probe -Label 'Search Console sites' -Action { Get-GscInventory -Subject $Subject }

$gtmAll = if ($gtmProbe.ok) { @($gtmProbe.value) } else { @() }
$gaAll = if ($gaProbe.ok) { @($gaProbe.value) } else { @() }
$gscAll = if ($gscProbe.ok) { @($gscProbe.value) } else { @() }

# Which GTM containers serve more than one domain — violates one-container-per-charity.
$sharedContainers = @()
foreach ($c in $gtmAll) {
    $ds = @($c.domains | ForEach-Object { Normalize-Domain $_ } | Where-Object { $_ })
    if ($ds.Count -gt 1) {
        $sharedContainers += [pscustomobject]@{ publicId = $c.publicId; domains = $ds }
    }
}

$report = @()
foreach ($domain in $Domains) {
    $d = Normalize-Domain $domain

    $gtmMatch = @($gtmAll | Where-Object {
            @($_.domains | ForEach-Object { Normalize-Domain $_ }) -contains $d
        })

    $gaMatch = $null; $gaStream = $null
    foreach ($p in $gaAll) {
        $s = @($p.webStreams | Where-Object { (Normalize-Domain $_.defaultUri) -eq $d }) | Select-Object -First 1
        if ($s) { $gaMatch = $p; $gaStream = $s; break }
    }

    $gscMatch = @($gscAll | Where-Object {
            (Normalize-Domain $_.siteUrl) -eq $d -or $_.siteUrl -eq "sc-domain:$d"
        })

    # Traffic: three distinct states, never collapsed.
    #   measured   — property exists and the Data API answered
    #   unmeasured — no GA4 property, so traffic is UNKNOWN (not zero)
    #   error      — property exists but the query failed; also UNKNOWN
    $traffic = [pscustomobject]@{ state = 'unmeasured'; sessions = $null; activeUsers = $null }
    if ($gaMatch) {
        $t = Invoke-Probe -Label "GA4 sessions for $d" -Action { Get-Ga4Sessions -PropertyName $gaMatch.propertyName -Days $Days -Subject $Subject }
        if ($t.ok) {
            $traffic = [pscustomobject]@{ state = 'measured'; sessions = $t.value.sessions; activeUsers = $t.value.activeUsers }
        }
        else {
            $traffic = [pscustomobject]@{ state = 'error'; sessions = $null; activeUsers = $null }
        }
    }

    $served = if ($SkipLiveProbe) { $null } else { Get-ServedTelemetry -Domain $d }

    # Provisioned-vs-served disagreement — the finding this report exists for.
    #
    # Only comparable when BOTH sides were actually read. If the provisioning probe 401'd or
    # errored, "provisioned" is UNKNOWN, not empty — and emitting "served X is not provisioned"
    # off an unknown would be a confidently wrong answer, which is the exact failure class this
    # report was built to catch. Observed live on the first real run: all three APIs returned 401
    # and the report still printed three "not provisioned" lines.
    $mismatches = @()
    $gtmKnown = $gtmProbe.ok
    $gaKnown = $gaProbe.ok
    if ($served -and $served.reachable) {
        $provisionedGtm = @($gtmMatch | ForEach-Object { $_.publicId })
        if ($gtmKnown) {
            foreach ($id in $served.gtmIds) {
                if ($provisionedGtm -notcontains $id) { $mismatches += "served GTM $id is not provisioned for this domain" }
            }
            foreach ($id in $provisionedGtm) {
                if ($served.gtmIds -notcontains $id) { $mismatches += "provisioned GTM $id is NOT present in served HTML" }
            }
        }
        # Symmetric, matching the GTM checks above. An asymmetric check would miss the more
        # alarming direction: a site serving a GA4 id that no provisioned stream accounts for —
        # traffic flowing to a property nobody is watching.
        if ($gaKnown -and $gaStream -and ($served.gaIds -notcontains $gaStream.measurementId)) {
            $mismatches += "provisioned GA4 $($gaStream.measurementId) is NOT present in served HTML"
        }
        foreach ($id in $served.gaIds) {
            if ($id -match '^G-X+$') { continue }  # placeholder: already reported separately
            if (-not $gaKnown) { continue }        # provisioning unknown — cannot call it drift
            if (-not $gaStream) {
                $mismatches += "served GA4 $id but NO provisioned stream matches this domain"
            }
            elseif ($id -ne $gaStream.measurementId) {
                $mismatches += "served GA4 $id does not match provisioned $($gaStream.measurementId)"
            }
        }
        if ($served.hasPlaceholder) { $mismatches += 'served HTML contains a PLACEHOLDER GA4 id' }
    }

    $report += [pscustomobject]@{
        domain     = $d
        gtm        = [pscustomobject]@{
            provisioned = ($gtmMatch.Count -gt 0)
            containers  = @($gtmMatch | ForEach-Object { $_.publicId })
        }
        ga4        = [pscustomobject]@{
            provisioned   = [bool]$gaMatch
            propertyName  = if ($gaMatch) { $gaMatch.propertyName } else { $null }
            measurementId = if ($gaStream) { $gaStream.measurementId } else { $null }
        }
        gsc        = [pscustomobject]@{
            verified        = ($gscMatch.Count -gt 0)
            permissionLevel = if ($gscMatch.Count -gt 0) { $gscMatch[0].permissionLevel } else { $null }
        }
        traffic    = $traffic
        served     = $served
        mismatches = $mismatches
    }
}

# Ranking: measured sites descending by sessions; unmeasured listed separately so they can never
# be read as "least popular".
$measured = @($report | Where-Object { $_.traffic.state -eq 'measured' } | Sort-Object -Property @{ Expression = { $_.traffic.sessions }; Descending = $true })
$unknown = @($report | Where-Object { $_.traffic.state -ne 'measured' })

$out = [pscustomobject]@{
    schemaVersion    = 1
    generatedAt      = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    lookbackDays     = $Days
    gtmAccountId     = $GtmAccountId
    gaAccountName    = $GaAccountName
    probeErrors      = @($script:Errors)
    sharedContainers = $sharedContainers
    trafficRanking   = @($measured | ForEach-Object { [pscustomobject]@{ domain = $_.domain; sessions = $_.traffic.sessions } })
    unmeasured       = @($unknown | ForEach-Object { [pscustomobject]@{ domain = $_.domain; reason = $_.traffic.state } })
    sites            = $report
}

$json = $out | ConvertTo-Json -Depth 10
$json | Out-File -FilePath $OutputPath -Encoding utf8
Write-Host "Wrote $OutputPath"

# ---------------------------------------------------------------- Summary

$lines = @()
$lines += '## Fleet telemetry reachability'
$lines += ''
$lines += "Generated $($out.generatedAt) · lookback ${Days}d"
$lines += ''
$lines += '| Domain | GTM | GA4 | GSC | Sessions | Mismatches |'
$lines += '| --- | --- | --- | --- | --- | --- |'
foreach ($s in $report) {
    $gtmCell = if ($s.gtm.provisioned) { '✅ ' + ($s.gtm.containers -join ', ') } else { '❌' }
    $gaCell = if ($s.ga4.provisioned) { '✅ ' + $s.ga4.measurementId } else { '❌' }
    $gscCell = if ($s.gsc.verified) { '✅' } else { '❌' }
    $tCell = switch ($s.traffic.state) {
        'measured' { [string]$s.traffic.sessions }
        'unmeasured' { '— no property (UNKNOWN, not zero)' }
        default { '— query failed (UNKNOWN)' }
    }
    $mCell = if ($s.mismatches.Count -gt 0) { '⚠️ ' + $s.mismatches.Count } else { '—' }
    $lines += "| $($s.domain) | $gtmCell | $gaCell | $gscCell | $tCell | $mCell |"
}

if ($sharedContainers.Count -gt 0) {
    $lines += ''
    $lines += '### Shared GTM containers (violates one-container-per-charity)'
    foreach ($c in $sharedContainers) { $lines += "- ``$($c.publicId)`` → $($c.domains -join ', ')" }
}

$allMismatch = @($report | Where-Object { $_.mismatches.Count -gt 0 })
if ($allMismatch.Count -gt 0) {
    $lines += ''
    $lines += '### Provisioned vs served disagreements'
    foreach ($s in $allMismatch) {
        foreach ($m in $s.mismatches) { $lines += "- **$($s.domain)** — $m" }
    }
}

if ($script:Errors.Count -gt 0) {
    $lines += ''
    $lines += '### Probe errors (results above are INCOMPLETE)'
    foreach ($e in $script:Errors) { $lines += "- $e" }
}

$summary = $lines -join "`n"
Write-Host $summary
if ($env:GITHUB_STEP_SUMMARY) { $summary | Out-File -FilePath $env:GITHUB_STEP_SUMMARY -Append -Encoding utf8 }

# Exit non-zero if a probe failed: a partial report must not read as a clean one.
if ($script:Errors.Count -gt 0) {
    Write-Host "::error::$($script:Errors.Count) probe(s) failed — the report is incomplete."
    exit 1
}
