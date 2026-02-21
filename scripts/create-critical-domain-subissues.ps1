[CmdletBinding()]
param(
    [Parameter()]
    [string]$InventoryFile = '_run_artifacts/enom_cloudflare_transition_inventory.csv',

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

function Require-Command {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command not found on PATH: $Name"
    }
}

function ConvertTo-Bool {
    param([object]$Value)
    if ($null -eq $Value) { return $false }
    if ($Value -is [bool]) { return [bool]$Value }

    $s = ([string]$Value).Trim().ToLowerInvariant()
    if ([string]::IsNullOrWhiteSpace($s)) { return $false }

    switch ($s) {
        'true' { return $true }
        'false' { return $false }
        'yes' { return $true }
        'no' { return $false }
        '1' { return $true }
        '0' { return $false }
        default { return $false }
    }
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

$targets = @(
    $inventory |
        Where-Object { ConvertTo-Bool -Value $_.http_is_critical } |
        Where-Object { $_.category -in @('cat1', 'cat2', 'cat3') } |
        Sort-Object category, http_health, domain
)

Write-Host "Creating issues for $($targets.Count) critical domains..." -ForegroundColor Cyan

$created = @()

foreach ($r in $targets) {
    $domain = ([string]$r.domain).Trim().ToLowerInvariant()
    if ([string]::IsNullOrWhiteSpace($domain)) { continue }

    $cat = ([string]$r.category).Trim().ToLowerInvariant()
    $health = ([string]$r.http_health).Trim()
    $registrar = ([string]$r.whmcs_registrar).Trim()
    $action = ([string]$r.action).Trim()
    $epic = Get-EpicIssueNumber -Category $cat

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
        '',
        "Action: $action",
        '',
        "Checklist:",
        "- [ ] Confirm zone presence + account (FFC)",
        "- [ ] Ensure website repo exists (expected naming: FFC-EX-$domain)",
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
    $outFile = Join-Path -Path (Split-Path -Parent $InventoryFile) -ChildPath 'created_critical_domain_issues.csv'
    $created | Export-Csv -Path $outFile -NoTypeInformation -Encoding utf8
    Write-Host "Wrote created issue list to $outFile" -ForegroundColor Green
}
