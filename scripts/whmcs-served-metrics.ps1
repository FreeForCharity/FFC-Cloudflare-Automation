# Served-per-year metrics from service/domain SPANS (no PII).
#
# FFC's services are free, so billing events systematically undercount service
# delivery: a charity hosted since 2019 with no later invoice disappears from
# billing-based activity while still being served. This script counts a member
# as SERVED in year Y when they had a service or domain IN FORCE during Y
# (registration date through next-due/expiry, or through today when the
# service is still Active). It emits, side by side:
#   - served nonprofit members per year (span evidence, client level),
#   - organizations under management per year (domain-keyed, with the
#     sites-list-only orgs merged into the current year),
#   - billing-activity per year (the workflow-216 notion, for comparison),
#   - invoice composition per year ($0 paperwork vs paid vs other), so the
#     stability of invoice evidence is visible.
#
# PRIVACY: aggregate integer counts only; no client id, name, email, or
# domain is persisted or printed.

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
    [string]$OutputFile = 'artifacts/whmcs/whmcs_served_metrics.json',

    [Parameter()]
    [ValidateRange(1, 250)]
    [int]$PageSize = 250,

    [Parameter()]
    [string]$CharityGids = '2,5,6,7,8'
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'whmcs-api-common.ps1')
. (Join-Path $PSScriptRoot 'whmcs-metrics-common.ps1')

try {
    $api = Resolve-WhmcsApiUrl -ApiUrlParam $ApiUrl
    $creds = Resolve-WhmcsCredentials -IdentifierParam $Identifier -SecretParam $Secret -CredentialsJsonParam $CredentialsJson
    $accessKey = Resolve-WhmcsAccessKey -AccessKeyParam $AccessKey
    $charityGidSet = @($CharityGids -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    $currentYear = (Get-Date).ToUniversalTime().Year

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

    function Invoke-Paged {
        param(
            [string]$Action,
            [string]$Container,
            [string]$Item,
            [scriptblock]$OnItem
        )
        $start = 0
        while ($true) {
            $b = New-Body $Action
            $b.limitstart = $start
            $b.limitnum = $PageSize
            $r = Invoke-WhmcsGet -ApiUrl $api -Body $b
            $items = Get-WhmcsListFromResponse -Response $r -Container $Container -Item $Item
            if ($items.Count -le 0) { break }
            foreach ($i in $items) { & $OnItem $i }
            $start += $items.Count
            $total = 0
            if ($r.totalresults) { [void][int]::TryParse($r.totalresults.ToString(), [ref]$total) }
            if ($total -gt 0 -and $start -ge $total) { break }
        }
    }

    # --- 1. Classification (catalog + charity clients + for-profit clients) ---
    $catalog = @{}
    $rp = Invoke-WhmcsGet -ApiUrl $api -Body (New-Body 'GetProducts')
    foreach ($p in (Get-WhmcsListFromResponse -Response $rp -Container 'products' -Item 'product')) {
        if ($null -ne $p.pid) { $catalog["$($p.pid)"] = "$($p.gid)" }
    }

    $npClients = New-Object 'System.Collections.Generic.HashSet[string]'
    $anySvcClients = New-Object 'System.Collections.Generic.HashSet[string]'
    $clientServed = @{}     # clientid -> HashSet[int] years a charity service/domain was in force
    $clientFirstServed = @{}
    $billActivity = @{}     # clientid -> HashSet[int] years with a billing event (216 notion)
    $domainServed = @{}     # domain -> HashSet[int] years in force
    $svcCount = 0

    function Add-Span {
        # Marks years [startYear..endYear] (clamped to currentYear) in a
        # per-key HashSet map; used for both client-served and domain-served.
        param([hashtable]$Map, [string]$Key, $StartYear, $EndYear)
        if ([string]::IsNullOrWhiteSpace($Key) -or $null -eq $StartYear) { return }
        $s = [int]$StartYear
        $e = if ($null -eq $EndYear) { $s } else { [int]$EndYear }
        if ($e -lt $s) { $e = $s }
        if ($e -gt $currentYear) { $e = $currentYear }
        if (-not $Map.ContainsKey($Key)) { $Map[$Key] = New-Object 'System.Collections.Generic.HashSet[int]' }
        for ($y = $s; $y -le $e; $y++) { [void]$Map[$Key].Add($y) }
    }

    function Add-Bill {
        param([string]$Cid, $Year)
        if ($null -eq $Year -or [string]::IsNullOrWhiteSpace($Cid)) { return }
        if (-not $billActivity.ContainsKey($Cid)) { $billActivity[$Cid] = New-Object 'System.Collections.Generic.HashSet[int]' }
        [void]$billActivity[$Cid].Add([int]$Year)
    }

    # --- 2. Services: spans + classification -----------------------------------
    Invoke-Paged -Action 'GetClientsProducts' -Container 'products' -Item 'product' -OnItem {
        param($s)
        $svcCount++
        $cid = "$($s.clientid)"
        [void]$anySvcClients.Add($cid)
        $gid = if ($catalog.ContainsKey("$($s.pid)")) { $catalog["$($s.pid)"] } else { '' }
        $isCharity = $charityGidSet -contains $gid
        if ($isCharity) { [void]$npClients.Add($cid) }

        $startY = Get-YearFromDate ([string]$s.regdate)
        Add-Bill -Cid $cid -Year $startY

        if (-not $isCharity) { return }
        # In-force window: Active services run through today; ended services run
        # through their last billed period (nextduedate), floored at regdate.
        $endY = $null
        if ([string]$s.status -eq 'Active') { $endY = $currentYear }
        else {
            $due = Get-YearFromDate ([string]$s.nextduedate)
            $endY = if ($null -ne $due) { $due } else { $startY }
        }
        Add-Span -Map $clientServed -Key $cid -StartYear $startY -EndYear $endY

        $sd = ([string]$s.domain).ToLowerInvariant()
        if (-not [string]::IsNullOrWhiteSpace($sd)) {
            Add-Span -Map $domainServed -Key $sd -StartYear $startY -EndYear $endY
        }
    }

    # --- 3. Registered domains: spans (domain management is service, too) -----
    Invoke-Paged -Action 'GetClientsDomains' -Container 'domains' -Item 'domain' -OnItem {
        param($d)
        $cid = "$($d.userid)"
        $name = if ($d.domainname) { [string]$d.domainname } else { [string]$d.domain }
        $startY = Get-YearFromDate ([string]$d.regdate)
        $endY = $null
        if ([string]$d.status -eq 'Active') { $endY = $currentYear }
        else {
            $exp = Get-YearFromDate ([string]$d.expirydate)
            if ($null -eq $exp) { $exp = Get-YearFromDate ([string]$d.nextduedate) }
            $endY = if ($null -ne $exp) { $exp } else { $startY }
        }
        Add-Span -Map $clientServed -Key $cid -StartYear $startY -EndYear $endY
        if (-not [string]::IsNullOrWhiteSpace($name)) {
            Add-Span -Map $domainServed -Key $name.ToLowerInvariant() -StartYear $startY -EndYear $endY
        }
        Add-Bill -Cid $cid -Year $startY
    }

    # --- 4. Billing events + invoice composition ------------------------------
    $invoiceComp = @{}      # year -> @{ zero; paidNonzero; otherNonzero }
    Invoke-Paged -Action 'GetInvoices' -Container 'invoices' -Item 'invoice' -OnItem {
        param($i)
        $y = Get-YearFromDate ([string]$i.date)
        Add-Bill -Cid "$($i.userid)" -Year $y
        if ($null -eq $y) { return }
        if (-not $invoiceComp.ContainsKey($y)) { $invoiceComp[$y] = @{ zero = 0; paidNonzero = 0; otherNonzero = 0 } }
        $total = 0.0
        [void][double]::TryParse(([string]$i.total), [ref]$total)
        if ($total -eq 0) { $invoiceComp[$y].zero++ }
        elseif ([string]$i.status -eq 'Paid') { $invoiceComp[$y].paidNonzero++ }
        else { $invoiceComp[$y].otherNonzero++ }
    }
    Invoke-Paged -Action 'GetOrders' -Container 'orders' -Item 'order' -OnItem {
        param($o)
        Add-Bill -Cid "$($o.userid)" -Year (Get-YearFromDate ([string]$o.date))
    }

    # --- 5. Sites-list orgs with no WHMCS presence ----------------------------
    $sitesListOnlyActive = 0
    $sitesListLeftFfc = 0
    if (Test-Path $SitesListPath) {
        $sites = Get-Content -Raw -Path $SitesListPath | ConvertFrom-Json
        foreach ($row in @($sites)) {
            $domain = ([string]$row.Domain).ToLowerInvariant()
            if ([string]::IsNullOrWhiteSpace($domain)) { continue }
            if (-not [string]::IsNullOrWhiteSpace([string]$row.'Left FFC')) { $sitesListLeftFfc++; continue }
            if (-not $domainServed.ContainsKey($domain)) {
                # Under management now (in the reconciled inventory) but with no
                # WHMCS span history: counts as served in the current year only.
                Add-Span -Map $domainServed -Key $domain -StartYear $currentYear -EndYear $currentYear
                $sitesListOnlyActive++
            }
        }
    }

    if ($clientServed.Count -le 0) { throw 'No service spans found - refusing to write an empty metrics file.' }

    # --- 6. Per-year series -----------------------------------------------------
    foreach ($cid in $clientServed.Keys) {
        $clientFirstServed[$cid] = (@($clientServed[$cid]) | Measure-Object -Minimum).Minimum
    }
    $firstYear = ($clientFirstServed.Values | Measure-Object -Minimum).Minimum

    $years = [ordered]@{}
    $cumNp = 0
    for ($y = [int]$firstYear; $y -le $currentYear; $y++) {
        $servedNp = 0
        $newNp = 0
        foreach ($cid in $clientServed.Keys) {
            if (-not $npClients.Contains($cid)) { continue }
            if ($clientServed[$cid].Contains($y)) { $servedNp++ }
            if ($clientFirstServed[$cid] -eq $y) { $newNp++ }
        }
        $cumNp += $newNp
        $billNp = 0
        foreach ($cid in $billActivity.Keys) {
            if ($npClients.Contains($cid) -and $billActivity[$cid].Contains($y)) { $billNp++ }
        }
        $orgs = 0
        foreach ($dom in $domainServed.Keys) {
            if ($domainServed[$dom].Contains($y)) { $orgs++ }
        }
        $comp = if ($invoiceComp.ContainsKey($y)) { $invoiceComp[$y] } else { @{ zero = 0; paidNonzero = 0; otherNonzero = 0 } }
        $years["$y"] = [ordered]@{
            servedNonprofit     = $servedNp
            newNonprofit        = $newNp
            cumulativeNonprofit = $cumNp
            billingActive       = $billNp
            orgsUnderMgmt       = $orgs
            invZero             = [int]$comp.zero
            invPaid             = [int]$comp.paidNonzero
            invOther            = [int]$comp.otherNonzero
        }
    }

    $result = [ordered]@{
        generatedAt = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        definition  = [ordered]@{
            served        = 'Client had a charity service or registered domain IN FORCE during the year (regdate through next-due/expiry; Active services run through today)'
            orgs          = 'Distinct domains with a span covering the year (service + registered-domain spans), plus current sites-list-only orgs in the current year'
            billingActive = 'The workflow-216 notion (invoice/order/service registration DATED in the year), kept for comparison'
            caveat        = 'Ended services expose no termination date via the API; next-due/expiry is the best available end bound, so served-spans are an approximation that can trail reality in both directions'
        }
        charityGids = $charityGidSet
        totals      = [ordered]@{
            services            = $svcCount
            nonprofitClients    = $npClients.Count
            clientsWithSpans    = $clientServed.Count
            domainsWithSpans    = $domainServed.Count
            sitesListOnlyActive = $sitesListOnlyActive
            sitesListLeftFfc    = $sitesListLeftFfc
        }
        years       = $years
    }

    $dir = Split-Path -Parent $OutputFile
    if (-not [string]::IsNullOrWhiteSpace($dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $result | ConvertTo-Json -Depth 6 | Out-File -FilePath $OutputFile -Encoding utf8

    $lines = @(
        '## WHMCS served-per-year metrics (span evidence, no PII)'
        ''
        "- Nonprofit clients (charity products): $($npClients.Count); clients with spans: $($clientServed.Count); distinct domains with spans: $($domainServed.Count)"
        "- Sites-list-only orgs added to the current year: $sitesListOnlyActive (excluded as Left FFC: $sitesListLeftFfc)"
        ''
        '| Year | Served nonprofit (span) | Billing-active (216) | Orgs under mgmt | New | Cumulative | Inv $0 | Inv paid | Inv other |'
        '| ---- | ----------------------- | -------------------- | --------------- | --- | ---------- | ------ | -------- | --------- |'
    )
    foreach ($y in $years.Keys) {
        $m = $years[$y]
        $lines += "| $y | $($m.servedNonprofit) | $($m.billingActive) | $($m.orgsUnderMgmt) | $($m.newNonprofit) | $($m.cumulativeNonprofit) | $($m.invZero) | $($m.invPaid) | $($m.invOther) |"
    }
    $lines += ''
    $lines += "_Served = a charity service or domain in force during the year (span from regdate to next-due/expiry). Billing-active is the old event-dated notion, shown for comparison._"

    if ($env:GITHUB_STEP_SUMMARY) {
        $lines -join "`n" | Out-File -FilePath $env:GITHUB_STEP_SUMMARY -Append -Encoding utf8
    }
    $lines | ForEach-Object { Write-Host $_ }

    Write-Host ("Served matrix built: {0}-{1} -> {2}" -f $firstYear, $currentYear, $OutputFile)
    exit 0
}
catch {
    Write-Error $_
    exit 1
}
