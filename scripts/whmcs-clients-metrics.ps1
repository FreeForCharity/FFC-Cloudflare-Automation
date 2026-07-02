# Aggregate WHMCS client-membership metrics per calendar year (no PII).
#
# Feeds the Candid Platinum "active members" metric series and the
# freeforcharity.org impact metrics (FFC-IN-freeforcharity.org
# docs/METRICS-PLAYBOOK.md): for each year, how many clients signed up, the
# cumulative member count by year-end, and a conservative floor for "active
# members" (clients signed up by year-end whose status is Active today).
#
# PRIVACY: GetClients responses include names/emails, but this script tallies
# ONLY {datecreated, status} per client — no client row, name, email, or id is
# ever persisted or printed. Output is aggregate integer counts only, so the
# JSON artifact and step summary are PII-free by construction.

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
    [string]$OutputFile = 'artifacts/whmcs/whmcs_clients_metrics.json',

    [Parameter()]
    [ValidateRange(1, 250)]
    [int]$PageSize = 250
)

$ErrorActionPreference = 'Stop'

# Shared credential resolution + allowlisted, retrying API invocation.
. (Join-Path $PSScriptRoot 'whmcs-api-common.ps1')

function Get-WhmcsClientsFromResponse {
    param([Parameter(Mandatory = $true)] $Response)

    # Nested format: { clients: { client: [...] } }
    if ($Response.clients) {
        if ($Response.clients.client) { return @($Response.clients.client) }
        if ($Response.clients -is [System.Array]) { return @($Response.clients) }
    }

    # Flat format: "clients[client][0][datecreated]": "2019-04-02"
    $rx = '^clients\[client\]\[(\d+)\]\[([^\]]+)\]$'
    $byIndex = @{}
    foreach ($prop in $Response.PSObject.Properties) {
        $m = [regex]::Match($prop.Name, $rx)
        if (-not $m.Success) { continue }
        $idx = [int]$m.Groups[1].Value
        if (-not $byIndex.ContainsKey($idx)) { $byIndex[$idx] = @{} }
        $byIndex[$idx][$m.Groups[2].Value] = $prop.Value
    }
    if ($byIndex.Count -le 0) { return @() }

    $clients = @()
    foreach ($idx in ($byIndex.Keys | Sort-Object)) { $clients += [PSCustomObject]$byIndex[$idx] }
    return $clients
}

try {
    $api = Resolve-WhmcsApiUrl -ApiUrlParam $ApiUrl
    $creds = Resolve-WhmcsCredentials -IdentifierParam $Identifier -SecretParam $Secret -CredentialsJsonParam $CredentialsJson
    $accessKey = Resolve-WhmcsAccessKey -AccessKeyParam $AccessKey

    # Tallies only — a client's PII never leaves the loop iteration.
    $signupYearStatus = @{}   # year -> status -> count
    $statusCounts = @{}
    $total = 0
    $unparseableDates = 0
    $start = 0

    while ($true) {
        $body = @{
            identifier   = $creds.Identifier
            secret       = $creds.Secret
            action       = 'GetClients'
            responsetype = 'json'
            limitstart   = $start
            limitnum     = $PageSize
        }
        if (-not [string]::IsNullOrWhiteSpace($accessKey)) { $body.accesskey = $accessKey }

        $r = Invoke-WhmcsApi -ApiUrl $api -Body $body

        $totalResults = 0
        if ($r.totalresults) { [void][int]::TryParse($r.totalresults.ToString(), [ref]$totalResults) }

        $clients = Get-WhmcsClientsFromResponse -Response $r
        if ($clients.Count -le 0) { break }

        foreach ($c in $clients) {
            $total++

            $status = if ([string]::IsNullOrWhiteSpace([string]$c.status)) { 'Unknown' } else { [string]$c.status }
            if (-not $statusCounts.ContainsKey($status)) { $statusCounts[$status] = 0 }
            $statusCounts[$status]++

            $year = $null
            $created = [string]$c.datecreated
            if ($created -match '^(\d{4})-\d{2}-\d{2}') { $year = [int]$Matches[1] }
            if ($null -eq $year) { $unparseableDates++; continue }

            if (-not $signupYearStatus.ContainsKey($year)) { $signupYearStatus[$year] = @{} }
            if (-not $signupYearStatus[$year].ContainsKey($status)) { $signupYearStatus[$year][$status] = 0 }
            $signupYearStatus[$year][$status]++
        }

        $start += $clients.Count
        if ($totalResults -gt 0 -and $start -ge $totalResults) { break }
    }

    if ($total -le 0) { throw 'GetClients returned no clients — refusing to write an empty metrics file.' }

    # Per-year series, oldest signup year through the current year.
    $years = @($signupYearStatus.Keys | Sort-Object)
    $currentYear = (Get-Date).ToUniversalTime().Year
    $firstYear = if ($years.Count -gt 0) { [int]$years[0] } else { $currentYear }

    $yearMetrics = [ordered]@{}
    $cumulative = 0
    $activeCumulative = 0
    for ($y = $firstYear; $y -le $currentYear; $y++) {
        $newClients = 0
        $newActive = 0
        if ($signupYearStatus.ContainsKey($y)) {
            foreach ($kv in $signupYearStatus[$y].GetEnumerator()) {
                $newClients += [int]$kv.Value
                if ($kv.Key -eq 'Active') { $newActive += [int]$kv.Value }
            }
        }
        $cumulative += $newClients
        $activeCumulative += $newActive
        $yearMetrics["$y"] = [ordered]@{
            newClients                = $newClients
            cumulativeByYearEnd       = $cumulative
            activeCumulativeByYearEnd = $activeCumulative
        }
    }

    $result = [ordered]@{
        generatedAt      = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        definition       = [ordered]@{
            newClients                = 'clients whose WHMCS signup date falls in the year'
            cumulativeByYearEnd       = 'clients signed up on or before Dec 31 of the year (any current status)'
            activeCumulativeByYearEnd = "clients signed up on or before Dec 31 of the year whose status is Active TODAY - a floor for historical 'active members', since members active in that year but closed later are not counted"
        }
        totalClients     = $total
        statusCounts     = $statusCounts
        unparseableDates = $unparseableDates
        years            = $yearMetrics
    }

    $dir = Split-Path -Parent $OutputFile
    if (-not [string]::IsNullOrWhiteSpace($dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $result | ConvertTo-Json -Depth 6 | Out-File -FilePath $OutputFile -Encoding utf8

    $statusLine = ($statusCounts.GetEnumerator() | Sort-Object Name | ForEach-Object { "$($_.Key) $($_.Value)" }) -join ' / '
    $sum = @(
        '## WHMCS clients metrics (aggregate, no PII)'
        ''
        "- Total clients: **$total** ($statusLine)"
        "- Unparseable signup dates: $unparseableDates"
        ''
        '| Year | New clients | Cumulative by year-end | Currently-active cumulative |'
        '| ---- | ----------- | ---------------------- | --------------------------- |'
    )
    foreach ($y in $yearMetrics.Keys) {
        $m = $yearMetrics[$y]
        $sum += "| $y | $($m.newClients) | $($m.cumulativeByYearEnd) | $($m.activeCumulativeByYearEnd) |"
    }
    $sum += ''
    $sum += "_'Currently-active cumulative' uses today's status, so it is a floor for historical active-member counts._"

    if ($env:GITHUB_STEP_SUMMARY) {
        $sum -join "`n" | Out-File -FilePath $env:GITHUB_STEP_SUMMARY -Append -Encoding utf8
    }

    # Echo the same aggregate-only table to the job log: step summaries are not
    # reachable via the REST API, and this output contains no PII by construction,
    # so the log is a safe machine-readable channel for the values.
    $sum | ForEach-Object { Write-Host $_ }

    Write-Host ("Aggregated {0} clients into per-year metrics ({1}-{2}) -> {3}" -f $total, $firstYear, $currentYear, $OutputFile)
    exit 0
}
catch {
    Write-Error $_
    exit 1
}
