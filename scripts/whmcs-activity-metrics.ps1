# Full-history WHMCS activity metrics (Phase 1 of the data-quality plan; no PII).
#
# "Active nonprofit members in year Y" cannot be answered from status flags
# (WHMCS keeps no status history), but invoices, orders, and service
# registrations all carry their own dates and span the full 10+ years. This
# script tallies, per client-year, whether ANY of that evidence exists, splits
# clients nonprofit-vs-other by the product groups they hold, and emits the
# per-year active/new/cumulative nonprofit-member series for 2014-present.
#
# PRIVACY: only {clientid -> year tallies} live in memory; output is aggregate
# integer counts only. No client id, name, email, or domain is persisted or
# printed.

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
    [string]$OutputFile = 'artifacts/whmcs/whmcs_activity_metrics.json',

    [Parameter()]
    [ValidateRange(1, 250)]
    [int]$PageSize = 250,

    # Product groups whose services mark a client as a nonprofit member
    # (default per run-215 catalog: 2 .org domains, 5 501c3 consulting,
    # 6 charity onboarding/hosting, 7 nonprofit social media, 8 onlineimpacts).
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
        # Runs a paginated WHMCS list action, invoking $OnItem for every item.
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

    # --- 1. Nonprofit classification + service-registration activity ---------
    $catalog = @{}
    $rp = Invoke-WhmcsGet -ApiUrl $api -Body (New-Body 'GetProducts')
    foreach ($p in (Get-WhmcsListFromResponse -Response $rp -Container 'products' -Item 'product')) {
        if ($null -ne $p.pid) { $catalog["$($p.pid)"] = "$($p.gid)" }
    }

    $nonprofit = New-Object 'System.Collections.Generic.HashSet[string]'
    $activity = @{}          # clientid -> HashSet[int year]
    $evidence = @{ services = 0; invoices = 0; orders = 0 }

    function Add-Activity {
        param([string]$Cid, $Year)
        if ($null -eq $Year -or [string]::IsNullOrWhiteSpace($Cid)) { return }
        if (-not $activity.ContainsKey($Cid)) { $activity[$Cid] = New-Object 'System.Collections.Generic.HashSet[int]' }
        [void]$activity[$Cid].Add([int]$Year)
    }

    Invoke-Paged -Action 'GetClientsProducts' -Container 'products' -Item 'product' -OnItem {
        param($s)
        $cid = "$($s.clientid)"
        $gid = if ($catalog.ContainsKey("$($s.pid)")) { $catalog["$($s.pid)"] } else { '' }
        if ($charityGidSet -contains $gid) { [void]$nonprofit.Add($cid) }
        Add-Activity -Cid $cid -Year (Get-YearFromDate ([string]$s.regdate))
        $evidence.services++
    }

    # --- 2. Invoice + order activity (the historical evidence layer) ---------
    Invoke-Paged -Action 'GetInvoices' -Container 'invoices' -Item 'invoice' -OnItem {
        param($i)
        Add-Activity -Cid "$($i.userid)" -Year (Get-YearFromDate ([string]$i.date))
        $evidence.invoices++
    }
    Invoke-Paged -Action 'GetOrders' -Container 'orders' -Item 'order' -OnItem {
        param($o)
        Add-Activity -Cid "$($o.userid)" -Year (Get-YearFromDate ([string]$o.date))
        $evidence.orders++
    }

    if ($activity.Count -le 0) { throw 'No dated activity evidence found - refusing to write an empty metrics file.' }

    # --- 3. Per-year series ---------------------------------------------------
    $allYears = @()
    foreach ($set in $activity.Values) { $allYears += @($set) }
    $firstYear = ($allYears | Measure-Object -Minimum).Minimum
    $currentYear = (Get-Date).ToUniversalTime().Year

    $firstActivity = @{}     # clientid -> min year
    foreach ($cid in $activity.Keys) {
        $firstActivity[$cid] = (@($activity[$cid]) | Measure-Object -Minimum).Minimum
    }

    $years = [ordered]@{}
    $cumNp = 0
    for ($y = [int]$firstYear; $y -le $currentYear; $y++) {
        $activeNp = 0
        $activeOther = 0
        $newNp = 0
        foreach ($cid in $activity.Keys) {
            if ($activity[$cid].Contains($y)) {
                if ($nonprofit.Contains($cid)) { $activeNp++ } else { $activeOther++ }
            }
            if ($firstActivity[$cid] -eq $y -and $nonprofit.Contains($cid)) { $newNp++ }
        }
        $cumNp += $newNp
        $years["$y"] = [ordered]@{
            activeNonprofit     = $activeNp
            activeOther         = $activeOther
            newNonprofit        = $newNp
            cumulativeNonprofit = $cumNp
        }
    }

    $result = [ordered]@{
        generatedAt = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        definition  = [ordered]@{
            active      = 'Distinct clients with ANY dated evidence in the year: an invoice, an order, or a service registration'
            nonprofit   = "Clients holding at least one service in product groups [$CharityGids]"
            newAndCumul = 'New = first-ever activity year; cumulative = running total of new nonprofit members'
        }
        charityGids = $charityGidSet
        evidence    = $evidence
        clients     = [ordered]@{
            withActivity = $activity.Count
            nonprofit    = $nonprofit.Count
        }
        years       = $years
    }

    $dir = Split-Path -Parent $OutputFile
    if (-not [string]::IsNullOrWhiteSpace($dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $result | ConvertTo-Json -Depth 6 | Out-File -FilePath $OutputFile -Encoding utf8

    $lines = @(
        '## WHMCS activity metrics - full history (aggregate, no PII)'
        ''
        "- Evidence: $($evidence.invoices) invoices, $($evidence.orders) orders, $($evidence.services) service registrations"
        "- Clients with dated activity: $($activity.Count); classified nonprofit (groups $CharityGids): $($nonprofit.Count)"
        ''
        '| Year | Active nonprofit | Active other | New nonprofit | Cumulative nonprofit |'
        '| ---- | ---------------- | ------------ | ------------- | -------------------- |'
    )
    foreach ($y in $years.Keys) {
        $m = $years[$y]
        $lines += "| $y | $($m.activeNonprofit) | $($m.activeOther) | $($m.newNonprofit) | $($m.cumulativeNonprofit) |"
    }
    $lines += ''
    $lines += "_'Active in year Y' = at least one invoice, order, or service registration dated Y - real evidence, not status flags._"

    if ($env:GITHUB_STEP_SUMMARY) {
        $lines -join "`n" | Out-File -FilePath $env:GITHUB_STEP_SUMMARY -Append -Encoding utf8
    }
    $lines | ForEach-Object { Write-Host $_ }

    Write-Host ("Activity matrix built: {0} clients, {1}-{2} -> {3}" -f $activity.Count, $firstYear, $currentYear, $OutputFile)
    exit 0
}
catch {
    Write-Error $_
    exit 1
}
