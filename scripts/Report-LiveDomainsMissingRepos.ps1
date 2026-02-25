[CmdletBinding()]
param(
    [Parameter()]
    [string]$SitesListCsv = '_run_artifacts/ffcadmin_sites_list_domains.csv',

    [Parameter()]
    [string]$Organization = 'FreeForCharity',

    [Parameter()]
    [string]$OutputFile = '_run_artifacts/live_active_domains_missing_repos.csv'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Require-Command {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command not found on PATH: $Name"
    }
}

function Normalize-Domain {
    param([string]$Domain)
    if ([string]::IsNullOrWhiteSpace($Domain)) { return $null }
    return $Domain.Trim().ToLowerInvariant().TrimEnd('.')
}

function Normalize-Text {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return $null }
    return $Value.Trim()
}

function Normalize-Health {
    param([string]$Health)
    if ([string]::IsNullOrWhiteSpace($Health)) { return $null }
    return $Health.Trim().ToLowerInvariant()
}

function Normalize-Status {
    param([string]$Status)
    if ([string]::IsNullOrWhiteSpace($Status)) { return $null }
    return $Status.Trim().ToLowerInvariant()
}

function Get-RepoTargetDomain {
    param(
        [string]$Domain,
        [hashtable]$AllDomains
    )

    $d = Normalize-Domain -Domain $Domain
    if (-not $d) { return $null }

    if ($d.EndsWith('.com')) {
        $org = $d.Substring(0, $d.Length - 4) + '.org'
        if ($AllDomains.ContainsKey($org)) {
            return $org
        }
    }

    return $d
}

function Test-RepoExists {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FullRepo
    )

    $oldEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'

    $hadNativePref = $false
    $oldNativePref = $null
    try {
        $nativePrefVar = Get-Variable -Name 'PSNativeCommandUseErrorActionPreference' -Scope Global -ErrorAction SilentlyContinue
        if ($null -ne $nativePrefVar) {
            $hadNativePref = $true
            $oldNativePref = $global:PSNativeCommandUseErrorActionPreference
            $global:PSNativeCommandUseErrorActionPreference = $false
        }
    }
    catch {
        # ignore
    }

    try {
        gh repo view $FullRepo --json nameWithOwner 1>$null 2>$null
        return ($LASTEXITCODE -eq 0)
    }
    catch {
        return $false
    }
    finally {
        $global:LASTEXITCODE = 0
        $ErrorActionPreference = $oldEap
        if ($hadNativePref) {
            $global:PSNativeCommandUseErrorActionPreference = $oldNativePref
        }
    }
}

Require-Command -Name 'gh'

if (-not (Test-Path -Path $SitesListCsv)) {
    throw "Sites list CSV not found: $SitesListCsv"
}

$rows = Import-Csv -Path $SitesListCsv

$allDomains = @{}
foreach ($r in $rows) {
    $d = Normalize-Domain -Domain ([string]$r.domain)
    if ($d -and -not $allDomains.ContainsKey($d)) {
        $allDomains[$d] = $true
    }
}

$candidates = @()
foreach ($r in $rows) {
    $domain = Normalize-Domain -Domain ([string]$r.domain)
    if (-not $domain) { continue }

    $health = Normalize-Health -Health ([string]$r.health)
    $status = Normalize-Status -Status ([string]$r.status)
    $category = Normalize-Text -Value ([string]$r.category)

    if ($health -ne 'live') { continue }
    if ($status -ne 'active') { continue }
    if ($category -eq 'Subdomain') { continue }

    $repoDomain = Get-RepoTargetDomain -Domain $domain -AllDomains $allDomains
    if (-not $repoDomain) { continue }

    $candidates += [PSCustomObject]@{
        domain     = $domain
        repoDomain = $repoDomain
        health     = [string]$r.health
        status     = [string]$r.status
        category   = [string]$r.category
        notes      = [string]$r.notes
    }
}

# Deduplicate by repoDomain
$targets = @(
    $candidates |
        Group-Object repoDomain |
        ForEach-Object { $_.Group | Select-Object -First 1 }
)

$outDir = Split-Path -Parent $OutputFile
if (-not [string]::IsNullOrWhiteSpace($outDir)) {
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null
}

$results = foreach ($t in $targets | Sort-Object repoDomain) {
    $repoName = "FFC-EX-$($t.repoDomain)"
    $fullRepo = "$Organization/$repoName"

    $exists = Test-RepoExists -FullRepo $fullRepo

    [PSCustomObject]@{
        repoDomain   = $t.repoDomain
        sourceDomain = $t.domain
        repo         = $fullRepo
        repoExists   = $exists
        health       = $t.health
        status       = $t.status
        category     = $t.category
        notes        = $t.notes
    }
}

$results | Export-Csv -Path $OutputFile -NoTypeInformation -Encoding utf8

$missing = @($results | Where-Object { -not $_.repoExists })
Write-Host "Live+Active (non-subdomain) repo targets: $(@($results).Count)" -ForegroundColor Cyan
Write-Host "Missing repos: $(@($missing).Count)" -ForegroundColor Yellow
Write-Host "Wrote: $OutputFile" -ForegroundColor Green

# Also print the missing list for quick viewing
$missing | Select-Object repoDomain, repo, sourceDomain, category | Format-Table -AutoSize
