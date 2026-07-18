<#
.SYNOPSIS
    Regenerates the post-smoke triage repo list from the live GitHub org.

.DESCRIPTION
    Queries GitHub via the GitHub CLI (gh) to list repositories in an org and
    writes the results as a JSON array of "owner/name" strings.

    This is intended to keep data/dependabot-affected-repos.json in sync with
    the current FreeForCharity organization repo set.

.PARAMETER Org
    GitHub organization name.

.PARAMETER OutputFile
    Path to write the JSON array.

.PARAMETER IncludeArchived
    If set, include archived repositories.

.PARAMETER IncludeForks
    If set, include forked repositories.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\Update-DependabotAffectedRepos.ps1

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\Update-DependabotAffectedRepos.ps1 -Org FreeForCharity -WhatIf
#>

[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$Org = 'FreeForCharity',

    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$OutputFile = './data/dependabot-affected-repos.json',

    [Parameter()]
    [switch]$IncludeArchived,

    [Parameter()]
    [switch]$IncludeForks

)

$ErrorActionPreference = 'Stop'

function Assert-GhCli {
    $cmd = Get-Command gh -ErrorAction SilentlyContinue
    if (-not $cmd) {
        throw 'GitHub CLI (gh) is required but was not found on PATH.'
    }
}

function Assert-GhAuth {
    $outLines = & gh auth status 2>&1
    if ($LASTEXITCODE -ne 0) {
        $out = ($outLines | Out-String).TrimEnd()
        throw "GitHub CLI (gh) is not authenticated. Run: gh auth login`n$out"
    }
}

function New-ParentDirectory {
    param([Parameter(Mandatory = $true)][string]$Path)

    $parent = Split-Path -Parent $Path
    if ([string]::IsNullOrWhiteSpace($parent)) {
        return
    }

    if (-not (Test-Path -LiteralPath $parent)) {
        [System.IO.Directory]::CreateDirectory($parent) | Out-Null
    }
}

function Write-Utf8NoBom {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Content
    )

    $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllText($Path, $Content, $utf8NoBom)
}

Assert-GhCli
Assert-GhAuth
New-ParentDirectory -Path $OutputFile

$repos = gh repo list $Org --limit 500 --json 'nameWithOwner,isArchived,isFork' | ConvertFrom-Json

$filtered = @($repos | Where-Object {
        ($IncludeArchived -or -not $_.isArchived) -and
        ($IncludeForks -or -not $_.isFork)
    } | ForEach-Object { [string]$_.nameWithOwner })

$filtered = @($filtered | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Sort-Object -Unique)

if ($PSCmdlet.ShouldProcess($OutputFile, "Write $($filtered.Count) repos")) {
    Write-Utf8NoBom -Path $OutputFile -Content ($filtered | ConvertTo-Json -Depth 3)
}

[pscustomobject]@{
    org             = $Org
    outputFile      = $OutputFile
    count           = $filtered.Count
    includeArchived = [bool]$IncludeArchived
    includeForks    = [bool]$IncludeForks
}
