<#
.SYNOPSIS
  Pull an aggregate GA4 report for one site and write it to JSON (Wave 1 of the Google API epic,
  FreeForCharity/FFC-Cloudflare-Automation#508 / #514).

.DESCRIPTION
  Read-only. Authenticates via Application Default Credentials (GOOGLE_APPLICATION_CREDENTIALS set by
  the google-secrets-from-kv action) using the shared google-api-common.ps1 helper, calls the GA4
  Data API runReport, and emits PII-safe aggregate metrics only (no user-level dimensions).

  -DryRun emits a stub JSON without contacting Google, so the script and its consumers can be
  validated before the GCP project / credentials exist.

.EXAMPLE
  pwsh -File scripts/google-analytics-report.ps1 -PropertyId 123456789 -SiteLabel freeforcharity.org -OutPath data/google-analytics/freeforcharity.org.json

.EXAMPLE
  pwsh -File scripts/google-analytics-report.ps1 -SiteLabel ffcadmin.org -OutPath out.json -DryRun
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$SiteLabel,
    [Parameter(Mandatory)][string]$OutPath,
    [string]$PropertyId,
    [int]$Days = 28,
    # Production hostname; when set, a hostname-filtered copy of the headline metrics is
    # emitted (metricsProduction) so CI/staging traffic can be excluded from public numbers.
    [string]$Hostname,
    # Cap for the appended time-series history (~13 months of daily points).
    [int]$TimeseriesCap = 400,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot/google-api-common.ps1"

$startDate = "$Days" + 'daysAgo'
$now = [DateTimeOffset]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')

function Write-Report {
    param([object]$Report, [string]$Path)
    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    ($Report | ConvertTo-Json -Depth 20) | Set-Content -Path $Path -Encoding utf8
    Write-Host "Wrote GA report for $SiteLabel -> $Path"
}

if ($DryRun) {
    $stub = [ordered]@{
        schemaVersion = 2
        updatedAt     = $now
        site          = $SiteLabel
        propertyId    = if ($PropertyId) { $PropertyId } else { 'DRYRUN' }
        dryRun        = $true
        range         = [ordered]@{ days = $Days; startDate = $startDate; endDate = 'today' }
        metrics       = [ordered]@{ activeUsers = 0; newUsers = 0; sessions = 0; screenPageViews = 0; engagementRate = 0; eventCount = 0; keyEvents = 0 }
        topPages      = @()
        channels      = @()
    }
    Write-Report -Report $stub -Path $OutPath
    return
}

if ([string]::IsNullOrWhiteSpace($PropertyId)) {
    throw 'A GA4 -PropertyId is required for a live run (omit only with -DryRun).'
}

$token = Get-GoogleAccessToken -Scope 'https://www.googleapis.com/auth/analytics.readonly'
# NOTE: ${} braces are required — "$PropertyId:runReport" would parse ':' as a scope separator
# and silently produce an empty variable (404 from the API).
$uri = "https://analyticsdata.googleapis.com/v1beta/properties/${PropertyId}:runReport"

# Headline metrics (no dimensions -> single aggregate row; PII-safe). eventCount/keyEvents
# extend the schema (schemaVersion 2) for outcome metrics alongside traffic.
$metricNames = @('activeUsers', 'newUsers', 'sessions', 'screenPageViews', 'engagementRate', 'eventCount', 'keyEvents')

function Get-HeadlineMetrics {
    param([string]$FilterHostname)
    $reqBody = @{
        dateRanges = @(@{ startDate = $startDate; endDate = 'today' })
        metrics    = @($metricNames | ForEach-Object { @{ name = $_ } })
    }
    if ($FilterHostname) {
        # Match apex + www so both serving forms count as production traffic.
        $reqBody.dimensionFilter = @{ filter = @{ fieldName = 'hostName'; inListFilter = @{ values = @($FilterHostname, "www.$FilterHostname") } } }
    }
    $resp = Invoke-GoogleApi -Method POST -Uri $uri -AccessToken $token -Body $reqBody
    $respRows = Get-GoogleRows $resp
    $vals = [ordered]@{}
    for ($i = 0; $i -lt $metricNames.Count; $i++) {
        $vals[$metricNames[$i]] = if ($respRows.Count) { $respRows[0].metricValues[$i].value } else { 0 }
    }
    return $vals
}

$metrics = Get-HeadlineMetrics
$metricsProduction = if ($Hostname) { Get-HeadlineMetrics -FilterHostname $Hostname } else { $null }

# Top pages by views (pagePath is page-level, not user-level -> safe to publish).
$pagesResp = Invoke-GoogleApi -Method POST -Uri $uri -AccessToken $token -Body @{
    dateRanges = @(@{ startDate = $startDate; endDate = 'today' })
    dimensions = @(@{ name = 'pagePath' })
    metrics    = @(@{ name = 'screenPageViews' })
    orderBys   = @(@{ desc = $true; metric = @{ metricName = 'screenPageViews' } })
    limit      = 10
}
$topPages = @()
foreach ($r in (Get-GoogleRows $pagesResp)) {
    $topPages += [ordered]@{ path = $r.dimensionValues[0].value; views = $r.metricValues[0].value }
}

# Channel mix.
$chResp = Invoke-GoogleApi -Method POST -Uri $uri -AccessToken $token -Body @{
    dateRanges = @(@{ startDate = $startDate; endDate = 'today' })
    dimensions = @(@{ name = 'sessionDefaultChannelGroup' })
    metrics    = @(@{ name = 'sessions' })
    orderBys   = @(@{ desc = $true; metric = @{ metricName = 'sessions' } })
}
$channels = @()
foreach ($r in (Get-GoogleRows $chResp)) {
    $channels += [ordered]@{ channel = $r.dimensionValues[0].value; sessions = $r.metricValues[0].value }
}

$report = [ordered]@{
    schemaVersion = 2
    updatedAt     = $now
    site          = $SiteLabel
    propertyId    = $PropertyId
    dryRun        = $false
    range         = [ordered]@{ days = $Days; startDate = $startDate; endDate = 'today' }
    metrics       = $metrics
    topPages      = $topPages
    channels      = $channels
}
if ($null -ne $metricsProduction) {
    $report.productionHostname = $Hostname
    $report.metricsProduction = $metricsProduction
}
Write-Report -Report $report -Path $OutPath

# Time-series history: append today's headline snapshot to <OutPath>.timeseries.json
# (one point per UTC day; same-day reruns replace; capped at -TimeseriesCap points).
$tsPath = [System.IO.Path]::ChangeExtension($OutPath, $null).TrimEnd('.') + '.timeseries.json'
$today = [DateTimeOffset]::UtcNow.ToString('yyyy-MM-dd')
$history = @()
if (Test-Path $tsPath) {
    try { $history = @((Get-Content $tsPath -Raw | ConvertFrom-Json)) } catch { $history = @() }
}
$history = @($history | Where-Object { $_.date -ne $today })
$snapshot = if ($null -ne $metricsProduction) { $metricsProduction } else { $metrics }
$history += [ordered]@{
    date            = $today
    activeUsers     = $snapshot['activeUsers']
    sessions        = $snapshot['sessions']
    screenPageViews = $snapshot['screenPageViews']
}
if ($history.Count -gt $TimeseriesCap) { $history = @($history | Select-Object -Last $TimeseriesCap) }
($history | ConvertTo-Json -Depth 5 -AsArray) | Set-Content -Path $tsPath -Encoding utf8
Write-Host "Appended time-series point for $today -> $tsPath ($($history.Count) points)"
