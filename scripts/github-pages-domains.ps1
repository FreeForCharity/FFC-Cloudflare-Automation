<#
.SYNOPSIS
    READ-ONLY: list the domains hosted on GitHub Pages by FFC — one per
    FreeForCharity/FFC-EX-<domain> repository.

.DESCRIPTION
    Every FFC-EX-<domain> repo is a GitHub Pages site for <domain>. This lists
    those repos via the GitHub API and emits the domain for each (repo name minus
    the FFC-EX- prefix, lower-cased). The result is the authoritative work-list
    for the pid-40 "Hosted by GitHub Pages" product alignment (feed it to
    scripts/whmcs-product-alignment.ps1 -ProductId 40).

    Uses `gh` with the ambient token (GH_TOKEN); read-only.

.PARAMETER Org
    GitHub org. Default FreeForCharity.

.PARAMETER OutputFile
    JSON output path. Default 'artifacts/github/github_pages_domains.json'.
#>
[CmdletBinding()]
param(
    [Parameter()]
    [string]$Org = 'FreeForCharity',

    [Parameter()]
    [string]$OutputFile = 'artifacts/github/github_pages_domains.json'
)

$ErrorActionPreference = 'Stop'

$names = gh api "orgs/$Org/repos?per_page=100&type=public" --paginate --jq '.[].name'
if ($LASTEXITCODE -ne 0) { throw "gh api list repos for '$Org' failed (exit $LASTEXITCODE)." }

$domains = @($names -split "`n") |
    ForEach-Object { $_.Trim() } |
    Where-Object { $_ -match '^(?i)FFC-EX-.+' -and $_ -notmatch '(?i)Static-site-conversion' } |
    ForEach-Object { ($_ -replace '^(?i)FFC-EX-', '').ToLowerInvariant() } |
    Where-Object { $_ } |
    Sort-Object -Unique

$dir = Split-Path -Parent $OutputFile
if (-not [string]::IsNullOrWhiteSpace($dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
$domains | ConvertTo-Json -AsArray | Out-File -FilePath $OutputFile -Encoding utf8
Write-Host "GitHub Pages (FFC-EX) domains: $($domains.Count) -> $OutputFile"
