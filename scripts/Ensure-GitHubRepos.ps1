[CmdletBinding()]
param(
    [Parameter()]
    [string]$SitesListUrl = 'https://ffcadmin.org/sites-list/',

    [Parameter()]
    [string]$SitesListCsv,

    [Parameter()]
    [string]$HealthCsv = 'Live,Redirect,Error',

    [Parameter()]
    [int]$Limit = 0,

    [Parameter()]
    [string]$Organization = 'FreeForCharity',

    [Parameter()]
    [string]$TemplateRepo = 'FreeForCharity/FFC_Single_Page_Template',

    [Parameter()]
    [ValidateSet('public', 'private', 'internal')]
    [string]$Visibility = 'public',

    [Parameter()]
    [bool]$EnableIssues = $true,

    [Parameter()]
    [bool]$EnablePages = $true,

    [Parameter()]
    [ValidateSet('apex', 'staging', 'github-default')]
    [string]$PagesDomainType = 'apex',

    [Parameter()]
    [switch]$DryRun,

    [Parameter()]
    [switch]$EnsureSettingsOnExisting,

    [Parameter()]
    [string]$OutputFile = '_run_artifacts/ensure_github_repos_results.csv'
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

function Normalize-Health {
    param([string]$Health)
    if ([string]::IsNullOrWhiteSpace($Health)) { return $null }
    return $Health.Trim().ToLowerInvariant()
}

Require-Command -Name 'gh'

if ([string]::IsNullOrWhiteSpace($SitesListCsv)) {
    $SitesListCsv = '_run_artifacts/ffcadmin_sites_list_domains.csv'
}

$outDir = Split-Path -Parent $OutputFile
if (-not [string]::IsNullOrWhiteSpace($outDir)) {
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null
}

if (-not (Test-Path -Path $SitesListCsv)) {
    Write-Host "Sites list CSV not found; generating from $SitesListUrl" -ForegroundColor Cyan
    ./scripts/ffcadmin-sites-list-domains.ps1 -Url $SitesListUrl -OutputFile $SitesListCsv
}

$rows = Import-Csv -Path $SitesListCsv

$healthAllow = @{}
foreach ($h in ($HealthCsv -split ',')) {
    $nh = Normalize-Health -Health $h
    if ($nh) { $healthAllow[$nh] = $true }
}

$healthPriority = @{
    'live'       = 1
    'redirect'   = 2
    'error'      = 3
    'unreachable' = 4
}

$allDomains = @{}
foreach ($r in $rows) {
    $d = Normalize-Domain -Domain ([string]$r.domain)
    if ($d -and -not $allDomains.ContainsKey($d)) { $allDomains[$d] = $true }
}

$targetsByRepoDomain = @{}
foreach ($r in $rows) {
    $domain = Normalize-Domain -Domain ([string]$r.domain)
    if (-not $domain) { continue }

    $health = Normalize-Health -Health ([string]$r.health)
    if (-not $healthAllow.ContainsKey($health)) { continue }

    $repoDomain = Get-RepoTargetDomain -Domain $domain -AllDomains $allDomains
    if (-not $repoDomain) { continue }

    $priority = if ($healthPriority.ContainsKey($health)) { [int]$healthPriority[$health] } else { 99 }

    if (-not $targetsByRepoDomain.ContainsKey($repoDomain)) {
        $targetsByRepoDomain[$repoDomain] = [PSCustomObject]@{
            repoDomain    = $repoDomain
            sourceDomain  = $domain
            health        = ([string]$r.health)
            category      = ([string]$r.category)
            priority      = $priority
        }
    }
    else {
        # Prefer the best health (Live > Redirect > Error)
        if ($priority -lt [int]$targetsByRepoDomain[$repoDomain].priority) {
            $targetsByRepoDomain[$repoDomain] = [PSCustomObject]@{
                repoDomain    = $repoDomain
                sourceDomain  = $domain
                health        = ([string]$r.health)
                category      = ([string]$r.category)
                priority      = $priority
            }
        }
    }
}

$targets = @(
    $targetsByRepoDomain.Values |
        Sort-Object priority, repoDomain
)

if ($Limit -gt 0 -and @($targets).Count -gt $Limit) {
    $targets = @($targets | Select-Object -First $Limit)
}

Write-Host "Repo targets: $(@($targets).Count) (health: $HealthCsv, limit: $Limit)" -ForegroundColor Cyan

$results = @()

foreach ($t in @($targets)) {
    $repoDomain = [string]$t.repoDomain
    $repoName = "FFC-EX-$repoDomain"
    $fullRepo = "$Organization/$repoName"

    $desc = "Website for $repoDomain (ensure-repos)"

    $createParams = @{
        RepoName        = $repoName
        Organization    = $Organization
        Description     = $desc
        TemplateRepo    = $TemplateRepo
        Visibility      = $Visibility
        EnableIssues    = $EnableIssues
        PagesDomainType = $PagesDomainType
        CNAME           = $repoDomain
    }
    if ($EnablePages) { $createParams.EnablePages = $true }

    $exists = $false
    if (-not $DryRun) {
        gh repo view "$fullRepo" --json nameWithOwner 1>$null 2>$null
        if ($LASTEXITCODE -eq 0) { $exists = $true }
        $global:LASTEXITCODE = 0
    }

    if ($exists) {
        Write-Host "Exists: $fullRepo" -ForegroundColor Gray
        if ($EnsureSettingsOnExisting) {
            if ($DryRun) {
                Write-Host "DRY RUN: would ensure settings/rulesets for $fullRepo" -ForegroundColor Yellow
                $results += [PSCustomObject]@{ repoDomain = $repoDomain; repo = $fullRepo; action = 'would_ensure_settings'; health = $t.health; sourceDomain = $t.sourceDomain }
            }
            else {
                Write-Host "Ensuring settings/rulesets: $fullRepo" -ForegroundColor Cyan
                ./scripts/Create-GitHubRepo.ps1 @createParams
                $results += [PSCustomObject]@{ repoDomain = $repoDomain; repo = $fullRepo; action = 'ensured_settings'; health = $t.health; sourceDomain = $t.sourceDomain }
            }
        }
        else {
            $results += [PSCustomObject]@{ repoDomain = $repoDomain; repo = $fullRepo; action = 'exists'; health = $t.health; sourceDomain = $t.sourceDomain }
        }

        continue
    }

    if ($DryRun) {
        Write-Host "DRY RUN: would create $fullRepo" -ForegroundColor Yellow
        $results += [PSCustomObject]@{ repoDomain = $repoDomain; repo = $fullRepo; action = 'would_create'; health = $t.health; sourceDomain = $t.sourceDomain }
        continue
    }

    Write-Host "Creating: $fullRepo" -ForegroundColor Green
    ./scripts/Create-GitHubRepo.ps1 @createParams
    $results += [PSCustomObject]@{ repoDomain = $repoDomain; repo = $fullRepo; action = 'created'; health = $t.health; sourceDomain = $t.sourceDomain }
}

$results | Export-Csv -Path $OutputFile -NoTypeInformation -Encoding utf8
Write-Host "Wrote results: $OutputFile" -ForegroundColor Green
