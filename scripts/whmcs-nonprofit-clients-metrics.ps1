# Aggregate WHMCS NONPROFIT-client metrics from service-level evidence (no PII).
#
# Revalidation of the client-count metrics: not every WHMCS client is a
# nonprofit, and client-level status has no history, so this script classifies
# clients by the PRODUCTS/SERVICES they hold instead. Each service carries its
# own registration date and status, so "nonprofit client since year Y" is
# grounded in evidence (they took a charity service that year), not in a
# present-day status flag.
#
# Emits, all aggregate-only:
#   - the product catalog with distinct-client and service-status counts per
#     product and per product group (so the correct "nonprofit product" set is
#     visible in the data rather than assumed),
#   - per-group and overall per-year series: new clients by first-service year
#     and cumulative distinct clients,
#   - overall client tallies (with/without services, with an Active service).
#
# PRIVACY: GetClients/GetClientsProducts responses include names/emails/domains,
# but only {clientid -> aggregate tallies} are held in memory and ONLY counts,
# product names, and group names (org catalog data, not personal data) are ever
# written. No client id, name, email, or domain is persisted or printed.

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
    [string]$OutputFile = 'artifacts/whmcs/whmcs_nonprofit_clients_metrics.json',

    [Parameter()]
    [ValidateRange(1, 250)]
    [int]$PageSize = 250
)

$ErrorActionPreference = 'Stop'

# Shared credential resolution + allowlisted, retrying API invocation.
. (Join-Path $PSScriptRoot 'whmcs-api-common.ps1')

function Get-WhmcsListFromResponse {
    # Handles both the nested ({container:{item:[...]}}) and flat
    # ("container[item][0][field]") WHMCS response formats.
    param(
        [Parameter(Mandatory = $true)] $Response,
        [Parameter(Mandatory = $true)] [string]$Container,
        [Parameter(Mandatory = $true)] [string]$Item
    )

    $node = $Response.$Container
    if ($node) {
        if ($node.$Item) { return @($node.$Item) }
        if ($node -is [System.Array]) { return @($node) }
    }

    $rx = '^' + [regex]::Escape($Container) + '\[' + [regex]::Escape($Item) + '\]\[(\d+)\]\[([^\]]+)\]$'
    $byIndex = @{}
    foreach ($prop in $Response.PSObject.Properties) {
        $m = [regex]::Match($prop.Name, $rx)
        if (-not $m.Success) { continue }
        $idx = [int]$m.Groups[1].Value
        if (-not $byIndex.ContainsKey($idx)) { $byIndex[$idx] = @{} }
        $byIndex[$idx][$m.Groups[2].Value] = $prop.Value
    }
    if ($byIndex.Count -le 0) { return @() }

    $items = @()
    foreach ($idx in ($byIndex.Keys | Sort-Object)) { $items += [PSCustomObject]$byIndex[$idx] }
    return $items
}

function Get-YearFromDate {
    param([string]$Date)
    if ($Date -match '^(\d{4})-\d{2}-\d{2}') { return [int]$Matches[1] }
    return $null
}

try {
    $api = Resolve-WhmcsApiUrl -ApiUrlParam $ApiUrl
    $creds = Resolve-WhmcsCredentials -IdentifierParam $Identifier -SecretParam $Secret -CredentialsJsonParam $CredentialsJson
    $accessKey = Resolve-WhmcsAccessKey -AccessKeyParam $AccessKey

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

    # --- 1. Product catalog (pid -> gid/group/name) --------------------------
    $catalog = @{}
    $rp = Invoke-WhmcsApi -ApiUrl $api -Body (New-Body 'GetProducts')
    foreach ($p in (Get-WhmcsListFromResponse -Response $rp -Container 'products' -Item 'product')) {
        if ($null -eq $p.pid) { continue }
        $catalog["$($p.pid)"] = @{
            gid   = "$($p.gid)"
            group = if ($p.groupname) { [string]$p.groupname } else { "gid-$($p.gid)" }
            name  = [string]$p.name
        }
    }
    if ($catalog.Count -le 0) { throw 'GetProducts returned no products.' }

    # --- 2. Clients (signup year + current status, tallies only) -------------
    $clientCreatedYear = @{}
    $clientStatus = @{}
    $start = 0
    while ($true) {
        $b = New-Body 'GetClients'
        $b.limitstart = $start; $b.limitnum = $PageSize
        $r = Invoke-WhmcsApi -ApiUrl $api -Body $b
        $clients = Get-WhmcsListFromResponse -Response $r -Container 'clients' -Item 'client'
        if ($clients.Count -le 0) { break }
        foreach ($c in $clients) {
            $cid = "$($c.id)"
            $clientCreatedYear[$cid] = Get-YearFromDate ([string]$c.datecreated)
            $clientStatus[$cid] = if ([string]::IsNullOrWhiteSpace([string]$c.status)) { 'Unknown' } else { [string]$c.status }
        }
        $start += $clients.Count
        $total = 0
        if ($r.totalresults) { [void][int]::TryParse($r.totalresults.ToString(), [ref]$total) }
        if ($total -gt 0 -and $start -ge $total) { break }
    }
    $totalClients = $clientCreatedYear.Count
    if ($totalClients -le 0) { throw 'GetClients returned no clients.' }

    # --- 3. All services (the evidence layer) --------------------------------
    # Per-service: which client, which product, its own status and reg year.
    $svcTotal = 0
    $pidClients = @{}       # pid -> HashSet[clientid]
    $pidSvcStatus = @{}     # pid -> status -> count
    $gidClients = @{}       # gid -> HashSet[clientid]
    $gidActiveClients = @{} # gid -> HashSet[clientid] with an Active service in group
    $gidClientFirstYear = @{} # gid -> clientid -> min service reg year
    $clientAnySvc = New-Object 'System.Collections.Generic.HashSet[string]'
    $clientActiveSvc = New-Object 'System.Collections.Generic.HashSet[string]'
    $clientFirstSvcYear = @{}

    $start = 0
    while ($true) {
        $b = New-Body 'GetClientsProducts'
        $b.limitstart = $start; $b.limitnum = $PageSize
        $r = Invoke-WhmcsApi -ApiUrl $api -Body $b
        $services = Get-WhmcsListFromResponse -Response $r -Container 'products' -Item 'product'
        if ($services.Count -le 0) { break }
        foreach ($s in $services) {
            $svcTotal++
            $cid = "$($s.clientid)"
            $spid = "$($s.pid)"
            $status = if ([string]::IsNullOrWhiteSpace([string]$s.status)) { 'Unknown' } else { [string]$s.status }
            $year = Get-YearFromDate ([string]$s.regdate)
            $gid = if ($catalog.ContainsKey($spid)) { $catalog[$spid].gid } else { 'unknown' }

            if (-not $pidClients.ContainsKey($spid)) { $pidClients[$spid] = New-Object 'System.Collections.Generic.HashSet[string]' }
            [void]$pidClients[$spid].Add($cid)
            if (-not $pidSvcStatus.ContainsKey($spid)) { $pidSvcStatus[$spid] = @{} }
            if (-not $pidSvcStatus[$spid].ContainsKey($status)) { $pidSvcStatus[$spid][$status] = 0 }
            $pidSvcStatus[$spid][$status]++

            if (-not $gidClients.ContainsKey($gid)) {
                $gidClients[$gid] = New-Object 'System.Collections.Generic.HashSet[string]'
                $gidActiveClients[$gid] = New-Object 'System.Collections.Generic.HashSet[string]'
                $gidClientFirstYear[$gid] = @{}
            }
            [void]$gidClients[$gid].Add($cid)
            if ($status -eq 'Active') { [void]$gidActiveClients[$gid].Add($cid) }
            if ($null -ne $year) {
                if (-not $gidClientFirstYear[$gid].ContainsKey($cid) -or $year -lt $gidClientFirstYear[$gid][$cid]) {
                    $gidClientFirstYear[$gid][$cid] = $year
                }
                if (-not $clientFirstSvcYear.ContainsKey($cid) -or $year -lt $clientFirstSvcYear[$cid]) {
                    $clientFirstSvcYear[$cid] = $year
                }
            }
            [void]$clientAnySvc.Add($cid)
            if ($status -eq 'Active') { [void]$clientActiveSvc.Add($cid) }
        }
        $start += $services.Count
        $total = 0
        if ($r.totalresults) { [void][int]::TryParse($r.totalresults.ToString(), [ref]$total) }
        if ($total -gt 0 -and $start -ge $total) { break }
    }

    # --- 4. Aggregate --------------------------------------------------------
    $currentYear = (Get-Date).ToUniversalTime().Year

    function New-YearSeries {
        # first-service years -> {year: {new, cumulative}} from a clientid->year map
        param([hashtable]$FirstYearByClient)
        $newByYear = @{}
        foreach ($y in $FirstYearByClient.Values) {
            if (-not $newByYear.ContainsKey($y)) { $newByYear[$y] = 0 }
            $newByYear[$y]++
        }
        $years = @($newByYear.Keys | Sort-Object)
        if ($years.Count -le 0) { return [ordered]@{} }
        $out = [ordered]@{}
        $cum = 0
        for ($y = [int]$years[0]; $y -le $currentYear; $y++) {
            $n = 0
            if ($newByYear.ContainsKey($y)) { $n = [int]$newByYear[$y] }
            $cum += $n
            $out["$y"] = [ordered]@{ newClients = $n; cumulativeClients = $cum }
        }
        return $out
    }

    $productRows = @()
    foreach ($spid in ($pidClients.Keys | Sort-Object { [int]$_ })) {
        $meta = if ($catalog.ContainsKey($spid)) { $catalog[$spid] } else { @{ gid = 'unknown'; group = 'unknown'; name = "pid-$spid" } }
        $statuses = $pidSvcStatus[$spid]
        $active = 0
        if ($statuses.ContainsKey('Active')) { $active = [int]$statuses['Active'] }
        $totalSvc = 0
        foreach ($v in $statuses.Values) { $totalSvc += [int]$v }
        $productRows += [ordered]@{
            pid             = $spid
            gid             = $meta.gid
            group           = $meta.group
            product         = $meta.name
            distinctClients = $pidClients[$spid].Count
            activeServices  = $active
            totalServices   = $totalSvc
        }
    }

    $groupRows = @()
    $groupSeries = [ordered]@{}
    foreach ($gid in ($gidClients.Keys | Sort-Object)) {
        $groupName = 'unknown'
        foreach ($meta in $catalog.Values) { if ($meta.gid -eq $gid) { $groupName = $meta.group; break } }
        $groupRows += [ordered]@{
            gid                      = $gid
            group                    = $groupName
            distinctClients          = $gidClients[$gid].Count
            clientsWithActiveService = $gidActiveClients[$gid].Count
        }
        $groupSeries[$gid] = [ordered]@{
            group   = $groupName
            byYear  = New-YearSeries -FirstYearByClient $gidClientFirstYear[$gid]
        }
    }

    $overallSeries = New-YearSeries -FirstYearByClient $clientFirstSvcYear

    $result = [ordered]@{
        generatedAt = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        definition  = [ordered]@{
            classification = 'Clients are classified by the products/services they hold; per-year series use each client FIRST service registration year in the group (service-level evidence with its own date, not the client status flag)'
            note           = 'Which product groups count as "nonprofit services" is visible in the catalog tables; combine groups as appropriate rather than assuming all clients are nonprofits'
        }
        totals      = [ordered]@{
            clients                  = $totalClients
            clientsWithAnyService    = $clientAnySvc.Count
            clientsWithNoService     = $totalClients - $clientAnySvc.Count
            clientsWithActiveService = $clientActiveSvc.Count
            services                 = $svcTotal
        }
        products    = $productRows
        groups      = $groupRows
        seriesByGroup = $groupSeries
        seriesAllServices = $overallSeries
    }

    $dir = Split-Path -Parent $OutputFile
    if (-not [string]::IsNullOrWhiteSpace($dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $result | ConvertTo-Json -Depth 8 | Out-File -FilePath $OutputFile -Encoding utf8

    # --- 5. Report (summary + full log echo; aggregate-only by construction) --
    $lines = @(
        '## WHMCS nonprofit clients metrics (service evidence, no PII)'
        ''
        "- Clients: **$totalClients** total; $($clientAnySvc.Count) with >=1 service; $($totalClients - $clientAnySvc.Count) with none; $($clientActiveSvc.Count) with an Active service"
        "- Services: $svcTotal"
        ''
        '### Product catalog reach'
        ''
        '| pid | Group | Product | Distinct clients | Active services | Total services |'
        '| --- | ----- | ------- | ---------------- | --------------- | -------------- |'
    )
    foreach ($row in $productRows) {
        $lines += "| $($row.pid) | $($row.group) | $($row.product) | $($row.distinctClients) | $($row.activeServices) | $($row.totalServices) |"
    }
    $lines += ''
    $lines += '### Product groups'
    $lines += ''
    $lines += '| gid | Group | Distinct clients | Clients w/ Active service |'
    $lines += '| --- | ----- | ---------------- | ------------------------- |'
    foreach ($row in $groupRows) {
        $lines += "| $($row.gid) | $($row.group) | $($row.distinctClients) | $($row.clientsWithActiveService) |"
    }
    $lines += ''
    $lines += '### Per-year clients by first service (all products)'
    $lines += ''
    $lines += '| Year | New clients (first service) | Cumulative |'
    $lines += '| ---- | --------------------------- | ---------- |'
    foreach ($y in $overallSeries.Keys) {
        $m = $overallSeries[$y]
        $lines += "| $y | $($m.newClients) | $($m.cumulativeClients) |"
    }
    $lines += ''
    $lines += '### Per-year clients by first service, per group'
    foreach ($gid in $groupSeries.Keys) {
        $gs = $groupSeries[$gid]
        $lines += ''
        $lines += "#### gid $gid — $($gs.group)"
        $lines += ''
        $lines += '| Year | New clients | Cumulative |'
        $lines += '| ---- | ----------- | ---------- |'
        foreach ($y in $gs.byYear.Keys) {
            $m = $gs.byYear[$y]
            $lines += "| $y | $($m.newClients) | $($m.cumulativeClients) |"
        }
    }

    if ($env:GITHUB_STEP_SUMMARY) {
        $lines -join "`n" | Out-File -FilePath $env:GITHUB_STEP_SUMMARY -Append -Encoding utf8
    }
    # Step summaries are not reachable via the REST API; the log is the
    # machine-readable channel and this output is aggregate-only.
    $lines | ForEach-Object { Write-Host $_ }

    Write-Host ("Aggregated {0} services across {1} clients -> {2}" -f $svcTotal, $totalClients, $OutputFile)
    exit 0
}
catch {
    Write-Error $_
    exit 1
}
