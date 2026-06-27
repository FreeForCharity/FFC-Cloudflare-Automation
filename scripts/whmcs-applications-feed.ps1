<#
.SYNOPSIS
    Build the PII-safe applications.json feed consumed by the FFCadmin roadmap.

.DESCRIPTION
    A charity "application" in FFC is a WHMCS client that holds an onboarding
    product: pre-501c3 = pid 16, 501c3 = pid 33 (see
    config/whmcs-onboarding-products.json). Donors are payments/transactions, not
    clients holding these products, so gating on the onboarding product ids
    excludes them by construction.

    This script enumerates clients holding those products via the read-only WHMCS
    API and writes ONLY non-PII fields (an allowlist):

      - id            opaque, stable per-charity surrogate ("ffc-<clientid>")
      - charityName   the public organization name (companyname)
      - serviceTier   derived from the onboarding product (stage encoded)
      - missionExcerpt (optional) truncated mission text, when the product
                       collected it (pre-501c3 custom field)
      - submittedAt   ISO-8601 onboarding/registration date

    Applicant emails, phone numbers, addresses, board contacts, EIN, GuideStar
    links, and every other custom field are NEVER written. Output is built from
    an explicit allowlist, not by removing fields from a fuller object.

    Dot-sources scripts/whmcs-api-common.ps1 for credential resolution and the
    retry/error handling already used by every WHMCS workflow in this repo.

.NOTES
    Read-only: issues only GetClientsProducts and GetClientsDetails. Run with
    -DryRun first to preview the feed in the logs without writing the file.
#>
[CmdletBinding()]
param(
    [string]$ApiUrl,
    [string]$Identifier,
    [string]$Secret,
    [string]$AccessKey,
    [string]$OutputFile = 'applications/applications.json',
    [int[]]$ProductIds = @(16, 33),
    [int]$MissionMaxLength = 180,
    [string]$GeneratedAt,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'whmcs-api-common.ps1')

$creds = Resolve-WhmcsCredentials -IdentifierParam $Identifier -SecretParam $Secret
$api = Resolve-WhmcsApiUrl -ApiUrlParam $ApiUrl
$accessKey = Resolve-WhmcsAccessKey -AccessKeyParam $AccessKey

# pid -> public service-tier label. The stage is encoded in the label; the raw
# pid is never published.
$TierByPid = @{
    '16' = 'Tier 1 — Application & verification (pre-501(c)(3))'
    '33' = 'Tier 1 — Application & verification (501(c)(3))'
}

function New-BaseBody {
    $b = @{
        identifier   = $creds.Identifier
        secret       = $creds.Secret
        responsetype = 'json'
    }
    if ($accessKey) { $b['accesskey'] = $accessKey }
    return $b
}

function Get-MissionFromProduct {
    param($Product)
    # WHMCS returns customfields.customfield[] with { id, name, value }. The
    # mission lives in a field whose name contains "mission" (pre-501c3 pid 16).
    $nodes = $null
    try { $nodes = $Product.customfields.customfield } catch { return $null }
    if (-not $nodes) { return $null }
    foreach ($f in @($nodes)) {
        if ("$($f.name)" -match '(?i)mission') {
            $val = "$($f.value)".Trim()
            if ($val) { return $val }
        }
    }
    return $null
}

function ConvertTo-IsoDate {
    param([string]$Raw)
    if ([string]::IsNullOrWhiteSpace($Raw) -or $Raw -match '^0000') { return $null }
    [datetime]$dt = [datetime]::MinValue
    if ([datetime]::TryParse($Raw, [ref]$dt)) {
        return $dt.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    }
    return $null
}

# clientid -> working record. A client holding both products is listed once;
# the 501c3 product (pid 33) wins for the tier label.
$byClient = [ordered]@{}

foreach ($pid in $ProductIds) {
    $body = New-BaseBody
    $body['action'] = 'GetClientsProducts'
    $body['pid'] = "$pid"
    $body['limitnum'] = '5000'
    $resp = Invoke-WhmcsApi -ApiUrl $api -Body $body

    $products = @()
    try { $products = @($resp.products.product) } catch { $products = @() }
    Write-Host "pid $pid -> $($products.Count) client product(s)"

    foreach ($p in $products) {
        $clientId = "$($p.clientid)".Trim()
        if ([string]::IsNullOrWhiteSpace($clientId)) { continue }

        $regIso = ConvertTo-IsoDate "$($p.regdate)"
        $mission = Get-MissionFromProduct -Product $p

        if ($byClient.Contains($clientId)) {
            $existing = $byClient[$clientId]
            if ($pid -eq 33) { $existing.pid = $pid }                 # 501c3 wins
            if (-not $existing.mission -and $mission) { $existing.mission = $mission }
            if (-not $existing.regIso -and $regIso) { $existing.regIso = $regIso }
            continue
        }

        $byClient[$clientId] = [pscustomobject]@{
            clientId = $clientId
            pid      = $pid
            mission  = $mission
            regIso   = $regIso
            company  = $null
        }
    }
}

# Resolve the public organization name per client (read-only, no stats).
foreach ($clientId in @($byClient.Keys)) {
    $body = New-BaseBody
    $body['action'] = 'GetClientsDetails'
    $body['clientid'] = "$clientId"
    $body['stats'] = 'false'
    try {
        $d = Invoke-WhmcsApi -ApiUrl $api -Body $body
        $company = "$($d.client.companyname)".Trim()
        if ([string]::IsNullOrWhiteSpace($company)) { $company = "$($d.companyname)".Trim() }
        $byClient[$clientId].company = $company
        if (-not $byClient[$clientId].regIso) {
            $byClient[$clientId].regIso = ConvertTo-IsoDate "$($d.client.datecreated)"
        }
    }
    catch {
        Write-Warning "GetClientsDetails failed for client ${clientId}: $($_.Exception.Message)"
    }
}

# Build the published records from an explicit allowlist ONLY.
$applications = [System.Collections.Generic.List[object]]::new()
foreach ($clientId in $byClient.Keys) {
    $r = $byClient[$clientId]
    $company = "$($r.company)".Trim()
    if ([string]::IsNullOrWhiteSpace($company)) {
        Write-Warning "Skipping client ${clientId}: no public organization name."
        continue
    }

    $mission = $null
    if ($r.mission) {
        $m = ($r.mission -replace '\s+', ' ').Trim()
        if ($m.Length -gt $MissionMaxLength) {
            $m = $m.Substring(0, $MissionMaxLength - 1).TrimEnd() + '…'
        }
        $mission = $m
    }

    $tier = $TierByPid["$($r.pid)"]
    if (-not $tier) { $tier = 'Tier 1 — Application & verification' }

    $rec = [ordered]@{
        id          = "ffc-$clientId"
        charityName = $company
        serviceTier = $tier
    }
    if ($mission) { $rec['missionExcerpt'] = $mission }
    if ($r.regIso) { $rec['submittedAt'] = $r.regIso }
    $applications.Add([pscustomobject]$rec)
}

$sorted = @($applications | Sort-Object @{ Expression = { $_.submittedAt } }, @{ Expression = { $_.charityName } })

$gen = if ($GeneratedAt) { $GeneratedAt } else { (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ') }
$feed = [ordered]@{
    version      = 1
    generatedAt  = $gen
    applications = $sorted
}

$json = $feed | ConvertTo-Json -Depth 6
Write-Host "Applications in feed: $($sorted.Count)"

if ($DryRun) {
    Write-Host '--- DRY RUN (not writing) ---'
    Write-Host $json
    return
}

$dir = Split-Path -Parent $OutputFile
if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
$full = Join-Path (Get-Location) $OutputFile
[System.IO.File]::WriteAllText($full, $json + "`n", (New-Object System.Text.UTF8Encoding($false)))
Write-Host "Wrote $OutputFile"
