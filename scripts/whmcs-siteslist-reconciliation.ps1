# WHMCS x sites-list reconciliation + product alignment (Phase 1; no client PII).
#
# The sites-list (WHMCS + Cloudflare + WPMUDEV + health probes) and WHMCS
# describe overlapping but different populations: legacy-WordPress charities
# may have no WHMCS record, and WHMCS clients on Cloudflare/GitHub Pages may
# lack the catalog products that SHOULD represent that reality (pid 39
# 'Domain Registered in Cloudflare', pid 40 'Hosted by GitHub Pages'). This
# script joins the two sources and measures both the coverage gap and the
# product-alignment gap, producing the work-list sizes for Phase 2 cleanup.
#
# PRIVACY: org domains are public project data (the sites-list publishes
# them), but this output stays aggregate-only in the summary/log; the JSON
# artifact includes gap domain lists (org domains only) for the Phase 2
# backfill work-list.

[CmdletBinding()]
param(
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
    [string]$SitesListPath = 'sites-list/sites_list.json',

    [Parameter()]
    [string]$OutputFile = 'artifacts/whmcs/whmcs_siteslist_reconciliation.json',

    [Parameter()]
    [ValidateRange(1, 250)]
    [int]$PageSize = 250,

    [Parameter()]
    [string]$CloudflareProductPid = '39',

    [Parameter()]
    [string]$GithubPagesProductPid = '40'
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'whmcs-api-common.ps1')
. (Join-Path $PSScriptRoot 'whmcs-metrics-common.ps1')

try {
    $api = Resolve-WhmcsApiUrl -ApiUrlParam $ApiUrl
    $creds = Resolve-WhmcsCredentials -IdentifierParam $Identifier -SecretParam $Secret -CredentialsJsonParam $CredentialsJson
    $accessKey = Resolve-WhmcsAccessKey -AccessKeyParam $AccessKey

    if (-not (Test-Path $SitesListPath)) { throw "Sites list not found at $SitesListPath." }
    $sites = Get-Content -Raw -Path $SitesListPath | ConvertFrom-Json

    function New-Body {
        param([string]$Action)
        $b = @{
            identifier   = $creds.Identifier
            secret       = $creds.Secret
            action       = $Action
            responsetype = 'json'
        }
        if (-not [string]::IsNullOrWhiteSpace($accessKey)) { $b.accesskey = $accessKey }
        return $b
    }

    # --- 1. WHMCS domain -> client and client -> product maps ------------------
    $whmcsDomainClient = @{}
    $start = 0
    while ($true) {
        $b = New-Body 'GetClientsDomains'
        $b.limitstart = $start
        $b.limitnum = $PageSize
        $r = Invoke-WhmcsGet -ApiUrl $api -Body $b
        $domains = Get-WhmcsListFromResponse -Response $r -Container 'domains' -Item 'domain'
        if ($domains.Count -le 0) { break }
        foreach ($d in $domains) {
            $name = if ($d.domainname) { [string]$d.domainname } else { [string]$d.domain }
            if (-not [string]::IsNullOrWhiteSpace($name)) {
                $whmcsDomainClient[$name.ToLowerInvariant()] = "$($d.userid)"
            }
        }
        $start += $domains.Count
        $total = 0
        if ($r.totalresults) { [void][int]::TryParse($r.totalresults.ToString(), [ref]$total) }
        if ($total -gt 0 -and $start -ge $total) { break }
    }

    $clientPids = @{}
    $serviceDomainClient = @{}
    $start = 0
    while ($true) {
        $b = New-Body 'GetClientsProducts'
        $b.limitstart = $start
        $b.limitnum = $PageSize
        $r = Invoke-WhmcsGet -ApiUrl $api -Body $b
        $services = Get-WhmcsListFromResponse -Response $r -Container 'products' -Item 'product'
        if ($services.Count -le 0) { break }
        foreach ($s in $services) {
            $cid = "$($s.clientid)"
            if (-not $clientPids.ContainsKey($cid)) { $clientPids[$cid] = New-Object 'System.Collections.Generic.HashSet[string]' }
            [void]$clientPids[$cid].Add("$($s.pid)")
            $sd = [string]$s.domain
            if (-not [string]::IsNullOrWhiteSpace($sd)) {
                $serviceDomainClient[$sd.ToLowerInvariant()] = $cid
            }
        }
        $start += $services.Count
        $total = 0
        if ($r.totalresults) { [void][int]::TryParse($r.totalresults.ToString(), [ref]$total) }
        if ($total -gt 0 -and $start -ge $total) { break }
    }

    # --- 2. Join every sites-list row against WHMCS ---------------------------
    $counts = [ordered]@{
        sitesListTotal        = 0
        leftFfc               = 0
        inWhmcs               = 0
        sitesListOnly         = 0
        cloudflareTotal       = 0
        cloudflareWithClient  = 0
        cloudflareHasPid      = 0
        cloudflarePidGap      = 0
        githubPagesTotal      = 0
        githubPagesWithClient = 0
        githubPagesHasPid     = 0
        githubPagesPidGap     = 0
    }
    $tierCounts = @{}
    $cfGapDomains = @()
    $ghGapDomains = @()
    $sitesListOnlyDomains = @()
    $sitesListDomainSet = New-Object 'System.Collections.Generic.HashSet[string]'

    foreach ($row in @($sites)) {
        $domain = ([string]$row.Domain).ToLowerInvariant()
        if ([string]::IsNullOrWhiteSpace($domain)) { continue }
        [void]$sitesListDomainSet.Add($domain)
        $counts.sitesListTotal++

        if (-not [string]::IsNullOrWhiteSpace([string]$row.'Left FFC')) { $counts.leftFfc++ }

        $tier = [string]$row.'Work Tier'
        if ([string]::IsNullOrWhiteSpace($tier)) { $tier = '(none)' }
        if (-not $tierCounts.ContainsKey($tier)) { $tierCounts[$tier] = @{ total = 0; inWhmcs = 0 } }
        $tierCounts[$tier].total++

        $cid = $null
        if ($whmcsDomainClient.ContainsKey($domain)) { $cid = $whmcsDomainClient[$domain] }
        elseif ($serviceDomainClient.ContainsKey($domain)) { $cid = $serviceDomainClient[$domain] }

        if ($null -ne $cid) {
            $counts.inWhmcs++
            $tierCounts[$tier].inWhmcs++
        }
        else {
            $counts.sitesListOnly++
            $sitesListOnlyDomains += $domain
        }

        $isCloudflare = ([string]$row.'Is In Cloudflare' -eq 'Yes') -or ([string]$row.'In Cloudflare' -eq 'Yes')
        if ($isCloudflare) {
            $counts.cloudflareTotal++
            if ($null -ne $cid) {
                $counts.cloudflareWithClient++
                if ($clientPids.ContainsKey($cid) -and $clientPids[$cid].Contains($CloudflareProductPid)) {
                    $counts.cloudflareHasPid++
                }
                else {
                    $counts.cloudflarePidGap++
                    $cfGapDomains += $domain
                }
            }
            else {
                $counts.cloudflarePidGap++
                $cfGapDomains += $domain
            }
        }

        if ([string]$row.'Host Category' -eq 'GitHub Pages') {
            $counts.githubPagesTotal++
            if ($null -ne $cid) {
                $counts.githubPagesWithClient++
                if ($clientPids.ContainsKey($cid) -and $clientPids[$cid].Contains($GithubPagesProductPid)) {
                    $counts.githubPagesHasPid++
                }
                else {
                    $counts.githubPagesPidGap++
                    $ghGapDomains += $domain
                }
            }
            else {
                $counts.githubPagesPidGap++
                $ghGapDomains += $domain
            }
        }
    }

    $whmcsNotInSitesList = @()
    foreach ($d in $whmcsDomainClient.Keys) {
        if (-not $sitesListDomainSet.Contains($d)) { $whmcsNotInSitesList += $d }
    }

    $result = [ordered]@{
        generatedAt         = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        counts              = $counts
        whmcsDomains        = $whmcsDomainClient.Count
        whmcsNotInSitesList = @($whmcsNotInSitesList | Sort-Object)
        tierCounts          = $tierCounts
        gapLists            = [ordered]@{
            cloudflareProductGap  = @($cfGapDomains | Sort-Object)
            githubPagesProductGap = @($ghGapDomains | Sort-Object)
            sitesListOnly         = @($sitesListOnlyDomains | Sort-Object)
        }
    }

    $dir = Split-Path -Parent $OutputFile
    if (-not [string]::IsNullOrWhiteSpace($dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $result | ConvertTo-Json -Depth 6 | Out-File -FilePath $OutputFile -Encoding utf8

    $lines = @(
        '## WHMCS x sites-list reconciliation (aggregate summary)'
        ''
        "- Sites-list domains: **$($counts.sitesListTotal)** ($($counts.leftFfc) marked Left FFC)"
        "- Matched to a WHMCS client: $($counts.inWhmcs); sites-list only (no WHMCS record): **$($counts.sitesListOnly)**"
        "- WHMCS domains not in the sites-list: $($whmcsNotInSitesList.Count) (of $($whmcsDomainClient.Count) WHMCS domains)"
        ''
        '### Product alignment (the Phase 2 backfill work-list sizes)'
        ''
        "- Cloudflare domains: $($counts.cloudflareTotal) total; with WHMCS client $($counts.cloudflareWithClient); already holding pid $CloudflareProductPid : $($counts.cloudflareHasPid); **gap: $($counts.cloudflarePidGap)**"
        "- GitHub Pages sites: $($counts.githubPagesTotal) total; with WHMCS client $($counts.githubPagesWithClient); already holding pid $GithubPagesProductPid : $($counts.githubPagesHasPid); **gap: $($counts.githubPagesPidGap)**"
        ''
        '### By work tier'
        ''
        '| Work tier | Domains | In WHMCS |'
        '| --------- | ------- | -------- |'
    )
    foreach ($tier in ($tierCounts.Keys | Sort-Object)) {
        $t = $tierCounts[$tier]
        $lines += "| $tier | $($t.total) | $($t.inWhmcs) |"
    }
    $lines += ''
    $lines += '_Gap domain lists (org domains, public project data) are in the JSON artifact for the Phase 2 backfill._'

    if ($env:GITHUB_STEP_SUMMARY) {
        $lines -join "`n" | Out-File -FilePath $env:GITHUB_STEP_SUMMARY -Append -Encoding utf8
    }
    $lines | ForEach-Object { Write-Host $_ }

    Write-Host ("Reconciled {0} sites-list domains against {1} WHMCS domains -> {2}" -f $counts.sitesListTotal, $whmcsDomainClient.Count, $OutputFile)
    exit 0
}
catch {
    Write-Error $_
    exit 1
}
