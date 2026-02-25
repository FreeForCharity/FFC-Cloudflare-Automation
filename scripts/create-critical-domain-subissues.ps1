[CmdletBinding()]
param(
    [Parameter()]
    [string]$InventoryFile = '_run_artifacts/enom_cloudflare_transition_inventory.csv',

    [Parameter()]
    [ValidateSet('live', 'redirect', 'error', 'unreachable')]
    [string[]]$Health = @('live', 'redirect'),

    [Parameter(Mandatory = $true)]
    [int]$Cat1EpicIssue,

    [Parameter(Mandatory = $true)]
    [int]$Cat2EpicIssue,

    [Parameter(Mandatory = $true)]
    [int]$Cat3EpicIssue,

    [Parameter()]
    [string]$Repo = 'FreeForCharity/FFC-Cloudflare-Automation',

    [Parameter()]
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$utilsPath = Join-Path -Path $PSScriptRoot -ChildPath 'ffc-utils.psm1'
Import-Module $utilsPath -Force

function Require-Command {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command not found on PATH: $Name"
    }
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

function Get-EpicIssueNumber {
    param([string]$Category)

    switch ($Category) {
        'cat1' { return $Cat1EpicIssue }
        'cat2' { return $Cat2EpicIssue }
        'cat3' { return $Cat3EpicIssue }
        default { return $null }
    }
}

Require-Command -Name 'gh'

if (-not (Test-Path -Path $InventoryFile)) {
    throw "Inventory file not found: $InventoryFile"
}

$inventory = Import-Csv -Path $InventoryFile

$allDomains = @{}
foreach ($r in $inventory) {
    $d = Normalize-Domain -Domain $r.domain
    if ($d -and -not $allDomains.ContainsKey($d)) { $allDomains[$d] = $true }
}

$alreadyCreated = @{}
Get-ChildItem -Path (Split-Path -Parent $InventoryFile) -Filter 'created_domain_issues_*.csv' -ErrorAction SilentlyContinue |
    ForEach-Object {
        try {
            Import-Csv -Path $_.FullName | ForEach-Object {
                $d = Normalize-Domain -Domain $_.domain
                if ($d -and -not $alreadyCreated.ContainsKey($d)) { $alreadyCreated[$d] = $true }
            }
        }
        catch { }
    }

$targets = @(
    $inventory |
        Where-Object { ([string]$_.http_health).Trim().ToLowerInvariant() -in $Health } |
        Where-Object { $_.category -in @('cat1', 'cat2', 'cat3') } |
        Sort-Object category, http_health, domain
)

$healthLabel = ($Health -join ',')
Write-Host "Creating issues for $($targets.Count) domains (health in: $healthLabel)..." -ForegroundColor Cyan

$created = @()

foreach ($r in $targets) {
    $domain = Normalize-Domain -Domain ([string]$r.domain)
    if ([string]::IsNullOrWhiteSpace($domain)) { continue }

    if ($alreadyCreated.ContainsKey($domain)) {
        continue
    }

    $cat = ([string]$r.category).Trim().ToLowerInvariant()
    $health = ([string]$r.http_health).Trim()
    $registrar = ([string]$r.whmcs_registrar).Trim()
    $action = ([string]$r.action).Trim()
    $epic = Get-EpicIssueNumber -Category $cat

    $repoTargetDomain = Get-RepoTargetDomain -Domain $domain -AllDomains $allDomains
    $repoName = if ($repoTargetDomain) { "FFC-EX-$repoTargetDomain" } else { '' }

    if (-not $epic) {
        Write-Warning "Skipping domain '$domain' due to unknown category: $cat"
        continue
    }

    $title = "[Domain][$($cat.ToUpperInvariant())][$health] $domain"

    $bodyLines = @(
        "Part of #$epic.",
        '',
        "Domain: $domain",
        "Health: $health",
        "WHMCS: $($r.in_whmcs)",
        "Cloudflare: $($r.in_cloudflare)",
        "Registrar (WHMCS): $registrar",
        "Repo target domain: $repoTargetDomain",
        "Expected repo name (org only): $repoName",
        '',
        "Action: $action",
        '',
        "Checklist:",
        "- [ ] Confirm zone presence + account (FFC)",
        "- [ ] Ensure website repo exists (org-only rule; expected: $repoName)",
        "- [ ] Transfer registrar to Cloudflare Registrar (FFC account)",
        "- [ ] Verify site still Live/Redirect"
    )

    $body = ($bodyLines -join "`n")

    if ($DryRun) {
        Write-Host "DRY RUN: would create issue: $title" -ForegroundColor Yellow
        continue
    }

    $url = & gh issue create --repo $Repo --title $title --label cloudflare --body $body
    $url = ([string]$url).Trim()
    if ($url) {
        Write-Host "Created: $url" -ForegroundColor Green
        $created += [PSCustomObject]@{ domain = $domain; category = $cat; health = $health; issueUrl = $url }
    }
}

if (-not $DryRun) {
    $healthTag = ($Health -join '_')
    $outFile = Join-Path -Path (Split-Path -Parent $InventoryFile) -ChildPath "created_domain_issues_$healthTag.csv"

    $dir = Split-Path -Parent $outFile
    if (-not [string]::IsNullOrWhiteSpace($dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }

    $created | Export-Csv -Path $outFile -NoTypeInformation -Encoding utf8
    Write-Host "Wrote created issue list to $outFile" -ForegroundColor Green
}
