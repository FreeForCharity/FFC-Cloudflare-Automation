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
    updatedAt  = $now
    site       = $SiteLabel
    propertyId = if ($PropertyId) { $PropertyId } else { 'DRYRUN' }
    dryRun     = $true
    range      = [ordered]@{ days = $Days; startDate = $startDate; endDate = 'today' }
    metrics    = [ordered]@{ activeUsers = 0; newUsers = 0; sessions = 0; screenPageViews = 0; engagementRate = 0 }
    topPages   = @()
    channels   = @()
  }
  Write-Report -Report $stub -Path $OutPath
  return
}

if ([string]::IsNullOrWhiteSpace($PropertyId)) {
  throw 'A GA4 -PropertyId is required for a live run (omit only with -DryRun).'
}

$token = Get-GoogleAccessToken -Scope 'https://www.googleapis.com/auth/analytics.readonly'
$uri = "https://analyticsdata.googleapis.com/v1beta/properties/$PropertyId:runReport"

# Headline metrics (no dimensions -> single aggregate row; PII-safe).
$headline = Invoke-GoogleApi -Method POST -Uri $uri -AccessToken $token -Body @{
  dateRanges = @(@{ startDate = $startDate; endDate = 'today' })
  metrics    = @(
    @{ name = 'activeUsers' }, @{ name = 'newUsers' }, @{ name = 'sessions' },
    @{ name = 'screenPageViews' }, @{ name = 'engagementRate' }
  )
}

$metricNames = @('activeUsers', 'newUsers', 'sessions', 'screenPageViews', 'engagementRate')
$metrics = [ordered]@{}
for ($i = 0; $i -lt $metricNames.Count; $i++) {
  $v = if ($headline.rows) { $headline.rows[0].metricValues[$i].value } else { 0 }
  $metrics[$metricNames[$i]] = $v
}

# Top pages by views (pagePath is page-level, not user-level -> safe to publish).
$pagesResp = Invoke-GoogleApi -Method POST -Uri $uri -AccessToken $token -Body @{
  dateRanges = @(@{ startDate = $startDate; endDate = 'today' })
  dimensions = @(@{ name = 'pagePath' })
  metrics    = @(@{ name = 'screenPageViews' })
  orderBys   = @(@{ desc = $true; metric = @{ metricName = 'screenPageViews' } })
  limit      = 10
}
$topPages = @()
foreach ($r in @($pagesResp.rows)) {
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
foreach ($r in @($chResp.rows)) {
  $channels += [ordered]@{ channel = $r.dimensionValues[0].value; sessions = $r.metricValues[0].value }
}

$report = [ordered]@{
  updatedAt  = $now
  site       = $SiteLabel
  propertyId = $PropertyId
  dryRun     = $false
  range      = [ordered]@{ days = $Days; startDate = $startDate; endDate = 'today' }
  metrics    = $metrics
  topPages   = $topPages
  channels   = $channels
}
Write-Report -Report $report -Path $OutPath
