[CmdletBinding()]
param(
    [Parameter()]
    [string]$SitesDomainsFile = 'ffcadmin_sites_list_domains.csv',

    [Parameter()]
    [string]$CloudflareZonesFile = 'domain_summary.csv',

    [Parameter()]
    [string]$WhmcsDomainsFile = 'whmcs_domains.csv',

    [Parameter()]
    [string]$HttpProbeFile = 'domain_http_probe.csv',

    [Parameter()]
    [string]$OutputFile = 'enom_cloudflare_transition_inventory.csv'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$utilsPath = Join-Path -Path $PSScriptRoot -ChildPath 'ffc-utils.psm1'
Import-Module $utilsPath -Force

function Test-IsSubdomain {
    param([string]$Domain)
    if ([string]::IsNullOrWhiteSpace($Domain)) { return $false }
    # Heuristic: 3+ labels => subdomain. Not perfect for public suffixes, but good enough for triage.
    return ($Domain.Split('.').Count -ge 3)
}

if (-not (Test-Path $SitesDomainsFile)) { throw "SitesDomainsFile not found: $SitesDomainsFile" }

$sites = Import-Csv -Path $SitesDomainsFile
$allDomains = @($sites | ForEach-Object { Normalize-Domain $_.domain } | Where-Object { $_ } | Sort-Object -Unique)

# Sites-list booleans as a fallback (if the inventory CSVs are not present yet).
$sitesByDomain = @{}
foreach ($r in $sites) {
    $d = Normalize-Domain $r.domain
    if (-not $d) { continue }
    if (-not $sitesByDomain.ContainsKey($d)) {
        $sitesByDomain[$d] = $r
    }
}

# Cloudflare zones
$cfZones = @{}
if (Test-Path $CloudflareZonesFile) {
    $cf = Import-Csv -Path $CloudflareZonesFile
    $zoneCol = if ($cf -and $cf.Count -gt 0 -and ($cf[0].PSObject.Properties.Name -contains 'zone')) { 'zone' } elseif ($cf -and $cf.Count -gt 0 -and ($cf[0].PSObject.Properties.Name -contains 'domain')) { 'domain' } else { $null }

    if ($zoneCol) {
        foreach ($r in $cf) {
            $z = Normalize-Domain $r.$zoneCol
            if ($z -and -not $cfZones.ContainsKey($z)) { $cfZones[$z] = $true }
        }
    }
}

# WHMCS domains
$whmcsByDomain = @{}
if (Test-Path $WhmcsDomainsFile) {
    $w = Import-Csv -Path $WhmcsDomainsFile
    foreach ($r in $w) {
        $d = Normalize-Domain $r.domain
        if (-not $d) { continue }
        if (-not $whmcsByDomain.ContainsKey($d)) {
            $whmcsByDomain[$d] = $r
        }
    }
}

# HTTP probe
$httpByDomain = @{}
if (Test-Path $HttpProbeFile) {
    $p = Import-Csv -Path $HttpProbeFile
    foreach ($r in $p) {
        $d = Normalize-Domain $r.domain
        if (-not $d) { continue }
        if (-not $httpByDomain.ContainsKey($d)) {
            $httpByDomain[$d] = $r
        }
    }
}

$rowsOut = foreach ($d in $allDomains) {
    $siteRow = if ($sitesByDomain.ContainsKey($d)) { $sitesByDomain[$d] } else { $null }

    $inCf = $cfZones.ContainsKey($d)
    if (-not $inCf -and $siteRow -and ($siteRow.PSObject.Properties.Name -contains 'cloudflare')) {
        $inCf = ConvertTo-Bool -Value $siteRow.cloudflare
    }

    $inWhmcs = $whmcsByDomain.ContainsKey($d)
    if (-not $inWhmcs -and $siteRow -and ($siteRow.PSObject.Properties.Name -contains 'whmcs')) {
        $inWhmcs = ConvertTo-Bool -Value $siteRow.whmcs
    }

    $wh = if ($inWhmcs) { $whmcsByDomain[$d] } else { $null }
    $registrar = if ($wh -and $wh.registrar) { [string]$wh.registrar } else { '' }
    $registrarNorm = $registrar.Trim().ToLowerInvariant()
    $registrarKnown = -not [string]::IsNullOrWhiteSpace($registrarNorm)

    $status = if ($wh -and $wh.status) { [string]$wh.status } else { '' }

    $isEnom = ($registrarNorm -match 'enom')
    $isRegistrarCloudflare = ($registrarNorm -eq 'cloudflare')

    $probe = if ($httpByDomain.ContainsKey($d)) { $httpByDomain[$d] } else { $null }
    $healthRaw = if ($probe -and $probe.health) { [string]$probe.health } elseif ($siteRow -and $siteRow.health) { [string]$siteRow.health } else { '' }
    $health = if ([string]::IsNullOrWhiteSpace($healthRaw)) { '' } else { $healthRaw.Trim().ToLowerInvariant() }

    $isCritical = $null
    if ($probe -and ($probe.PSObject.Properties.Name -contains 'isCritical')) {
        $isCritical = ConvertTo-Bool -Value $probe.isCritical
    }
    elseif ($health -match '^(live|redirect)$') {
        $isCritical = $true
    }

    $category = ''
    $action = ''

    if ($inCf -and $inWhmcs -and $registrarKnown -and -not $isRegistrarCloudflare) {
        $category = 'cat1'
        $action = 'Transfer registration to Cloudflare Registrar (FFC account)'
    }
    elseif ($inWhmcs -and -not $inCf) {
        $category = 'cat2'
        $action = 'Onboard zone to Cloudflare, then transfer registration'
    }
    elseif (-not $inWhmcs -and -not $inCf) {
        $category = 'cat3'
        $action = 'Manual review (not found in WHMCS or Cloudflare)'
    }
    elseif ($inCf -and $inWhmcs -and -not $registrarKnown) {
        $category = 'other'
        $action = 'Needs WHMCS export to determine registrar (Cat1 vs already migrated)'
    }
    else {
        $category = 'other'
        $action = 'No action by this project (or already aligned)'
    }

    [PSCustomObject]@{
        domain                  = $d
        isSubdomain             = (Test-IsSubdomain -Domain $d)
        in_cloudflare           = $inCf
        in_whmcs                = $inWhmcs
        whmcs_status            = $status
        whmcs_registrar         = $registrar
        whmcs_registrar_known   = $registrarKnown
        is_enom                 = $isEnom
        registrar_is_cloudflare = $isRegistrarCloudflare
        http_health             = $health
        http_is_critical        = $isCritical
        category                = $category
        action                  = $action
    }
}

$outputDirectory = Split-Path -Path $OutputFile -Parent
if ($outputDirectory -and -not (Test-Path -LiteralPath $outputDirectory)) {
    New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
}

$rowsOut | Export-Csv -Path $OutputFile -NoTypeInformation -Encoding utf8
Write-Host "Wrote $($rowsOut.Count) rows to $OutputFile" -ForegroundColor Green
