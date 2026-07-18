<#
.SYNOPSIS
    Runs a smoke-protected Dependabot "wave" across FFC repos.

.DESCRIPTION
    This orchestrates the standard operating loop:
      1) Inventory open Dependabot PRs org-wide (gh search)
      2) Compute merge readiness (scripts/Test-PrMergeReadiness.ps1)
      3) Merge/queue only Ready=true PRs (scripts/Invoke-DependabotAutoMerge.ps1)
      4) Post-smoke triage (scripts/post-smoke-triage.ps1)
      5) Post a wave update to a tracking issue (default: FFC-IN-ClarkeMoyerAdmin#42)

        Post-smoke triage is intentionally post-merge. It does not block the current
        wave; it determines whether the next wave should proceed.

    It writes timestamped artifacts into ./reviews/ (gitignored).

.PARAMETER WaveNumber
    Wave number for logging and artifact naming.

.PARAMETER OutputDir
    Directory for artifacts (default: ./reviews).

.PARAMETER ReposFile
    Repo list JSON (array of "owner/name") for post-smoke triage.

.PARAMETER NonLiveFile
    Non-live registry JSON for triage comments.

.PARAMETER CommentOnIncidents
    If set, post idempotent triage comments on open incident issues.

.PARAMETER IssueRepo
    Repo that contains the tracking issue.

.PARAMETER IssueNumber
    Tracking issue number.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\Invoke-DependabotWave.ps1 -WaveNumber 17 -CommentOnIncidents

.EXAMPLE
    # Dry run (no merges, no comments, no issue updates)
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\Invoke-DependabotWave.ps1 -WaveNumber 17 -WhatIf
#>

[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 9999)]
    [int]$WaveNumber,

    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$OutputDir = './reviews',

    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$ReposFile = './data/dependabot-affected-repos.json',

    [Parameter()]
    [string]$NonLiveFile = './data/non-live-sites.json',

    [Parameter()]
    [switch]$CommentOnIncidents,

    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$IssueRepo = 'FreeForCharity/FFC-IN-ClarkeMoyerAdmin',

    [Parameter()]
    [ValidateRange(1, 999999)]
    [int]$IssueNumber = 42
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

function Get-PreferredPowerShellHost {
    $pwshCmd = Get-Command pwsh -ErrorAction SilentlyContinue
    if ($pwshCmd) {
        return 'pwsh'
    }

    $powershellCmd = Get-Command powershell -ErrorAction SilentlyContinue
    if ($powershellCmd) {
        return 'powershell'
    }

    throw 'Unable to locate a PowerShell executable (pwsh or powershell).'
}

function Ensure-Dir {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        [System.IO.Directory]::CreateDirectory($Path) | Out-Null
    }
}

function Write-Utf8NoBom {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Content
    )

    $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllText($Path, $Content, $utf8NoBom)
}

Assert-GhCli
Assert-GhAuth
Ensure-Dir -Path $OutputDir

$scriptHost = Get-PreferredPowerShellHost

$ts = (Get-Date).ToString('yyyyMMdd-HHmmss')

$inventoryJson = Join-Path $OutputDir "dependabot-open-prs-search-$ts.json"
$allUrls = Join-Path $OutputDir "dependabot-wave$WaveNumber-all-open-pr-urls-$ts.txt"
$readinessJson = Join-Path $OutputDir "dependabot-merge-readiness-$ts.json"
$readyJson = Join-Path $OutputDir "dependabot-wave$WaveNumber-ready-all-$ts.json"
$readyUrls = Join-Path $OutputDir "dependabot-wave$WaveNumber-ready-all-urls-$ts.txt"
$mergeResults = Join-Path $OutputDir "dependabot-wave$WaveNumber-automerge-$ts.json"
$triageJson = Join-Path $OutputDir "post-smoke-triage-$ts.json"
$triageLatest = Join-Path $OutputDir 'post-smoke-triage-latest.json'
$issueUpdateMd = Join-Path $OutputDir "dependabot-issue$IssueNumber-wave$WaveNumber-update-$ts.md"

# 1) Inventory
$searchJson = gh search prs --owner FreeForCharity --state open --author app/dependabot --limit 1000 --json 'repository,number,title,url,updatedAt' | Out-String
Write-Utf8NoBom -Path $inventoryJson -Content $searchJson.TrimEnd()
$inventory = Get-Content -Raw $inventoryJson | ConvertFrom-Json
$openCount = @($inventory).Count
$inventoryUrls = @($inventory | ForEach-Object { $_.url })
if ($inventoryUrls.Count -gt 0) {
    Write-Utf8NoBom -Path $allUrls -Content (($inventoryUrls -join [Environment]::NewLine) + [Environment]::NewLine)
}
else {
    Write-Utf8NoBom -Path $allUrls -Content ''
}

# 2) Readiness scan
$rows = @()
$ready = @()

if ($inventoryUrls.Count -gt 0) {
    $readinessOutLines = & $scriptHost -NoProfile -ExecutionPolicy Bypass -File scripts/Test-PrMergeReadiness.ps1 -PrUrlsPath $allUrls -OutputPath $readinessJson -AllowNoReviewDecision 2>&1
    if ($LASTEXITCODE -notin @(0, 2)) {
        $readinessOut = ($readinessOutLines | Out-String).TrimEnd()
        throw "Readiness scan failed (exit $LASTEXITCODE). Output:\n$readinessOut"
    }

    if (-not (Test-Path -LiteralPath $readinessJson)) {
        $readinessOut = ($readinessOutLines | Out-String).TrimEnd()
        throw "Readiness scan did not produce output file: $readinessJson\nOutput:\n$readinessOut"
    }

    $rows = Get-Content -Raw $readinessJson | ConvertFrom-Json
    $ready = @($rows | Where-Object { $_.Ready -eq $true })
}
else {
    Write-Utf8NoBom -Path $readinessJson -Content '[]'
}

if ($ready.Count -gt 0) {
    Write-Utf8NoBom -Path $readyJson -Content ($ready | ConvertTo-Json -Depth 10)
    Write-Utf8NoBom -Path $readyUrls -Content (((@($ready | ForEach-Object { $_.Url })) -join [Environment]::NewLine) + [Environment]::NewLine)
}
else {
    Write-Utf8NoBom -Path $readyJson -Content '[]'
    Write-Utf8NoBom -Path $readyUrls -Content ''
}

# 3) Merge/queue Ready=true PRs
$mergeSummary = $null
if ($PSCmdlet.ShouldProcess($IssueRepo, 'Merge/queue Ready=true Dependabot PRs')) {
    $mergeSummaryLines = & $scriptHost -NoProfile -ExecutionPolicy Bypass -File scripts/Invoke-DependabotAutoMerge.ps1 -ReadyJson $readyJson -ResultsJson $mergeResults 2>&1
    $mergeSummary = ($mergeSummaryLines | Out-String).TrimEnd()
    if ($LASTEXITCODE -ne 0) {
        throw "Auto-merge runner failed (exit $LASTEXITCODE). Output:\n$mergeSummary"
    }

    if (-not (Test-Path -LiteralPath $mergeResults)) {
        throw "Auto-merge runner did not produce results file: $mergeResults"
    }
}
else {
    # Ensure an explicit empty results file exists for WhatIf.
    Write-Utf8NoBom -Path $mergeResults -Content '[]'
    $mergeSummary = '{"readyCount":0,"okCount":0,"failCount":0,"results":"(WhatIf)"}'
}

# 4) Post-smoke triage
# Always write a triage artifact; only enable incident commenting when not -WhatIf.
$triageArgs = @(
    '-NoProfile',
    '-ExecutionPolicy', 'Bypass',
    '-File', 'scripts/post-smoke-triage.ps1',
    '-ReposFile', $ReposFile,
    '-NonLiveFile', $NonLiveFile,
    '-OutputFile', $triageJson,
    '-UpdateLatest'
)

if ($CommentOnIncidents -and (-not $WhatIfPreference) -and $PSCmdlet.ShouldProcess($IssueRepo, 'Incident commenting on open incidents')) {
    $triageArgs += '-CommentOnIncidents'
}

$triageOutLines = & $scriptHost @triageArgs 2>&1
if ($LASTEXITCODE -ne 0) {
    $triageOut = ($triageOutLines | Out-String).TrimEnd()
    throw "Post-smoke triage failed (exit $LASTEXITCODE). Output:\n$triageOut"
}

if (-not (Test-Path -LiteralPath $triageJson)) {
    throw "Post-smoke triage did not produce output file: $triageJson"
}

# 5) Build + post issue update
$readyCount = @($ready).Count
$mergeObj = $null
try { $mergeObj = ($mergeSummary | ConvertFrom-Json) } catch { }

$okCount = if ($mergeObj -and $mergeObj.okCount -ne $null) { [int]$mergeObj.okCount } else { 0 }
$failCount = if ($mergeObj -and $mergeObj.failCount -ne $null) { [int]$mergeObj.failCount } else { 0 }

$lines = @()
$lines += "Wave $WaveNumber Dependabot merge pass ($((Get-Date).ToString('yyyy-MM-dd')))"
$lines += ''
$lines += "- Refreshed inventory: $openCount open Dependabot PRs"
$lines += "  - Source: $inventoryJson"
$lines += "- Full-scan readiness: $readyCount/$openCount Ready=true"
$lines += "  - Readiness report: $readinessJson"
$lines += ''
$lines += 'Merges / queueing'
$lines += "- Attempted Ready=true PRs: $readyCount"
$lines += "- Successful queue/merge requests: $okCount/$readyCount"
$lines += "- Results: $mergeResults"
$lines += ''
$lines += 'Post-smoke monitoring'
$lines += "- Latest global triage: $triageLatest"
$lines += "- Timestamped: $triageJson"
$lines += ''
Write-Utf8NoBom -Path $issueUpdateMd -Content (($lines -join [Environment]::NewLine) + [Environment]::NewLine)

if ($PSCmdlet.ShouldProcess("$IssueRepo#$IssueNumber", 'gh issue comment (wave update)')) {
    $issueOutLines = & gh issue comment $IssueNumber --repo $IssueRepo --body-file $issueUpdateMd 2>&1
    if ($LASTEXITCODE -ne 0) {
        $issueOut = ($issueOutLines | Out-String).TrimEnd()
        throw "Failed to post wave update comment (exit $LASTEXITCODE). Output:\n$issueOut"
    }
}

Write-Output ([pscustomobject]@{
        wave         = $WaveNumber
        ts           = $ts
        openCount    = $openCount
        readyCount   = $readyCount
        okCount      = $okCount
        failCount    = $failCount
        inventory    = $inventoryJson
        urls         = $allUrls
        readiness    = $readinessJson
        readyJson    = $readyJson
        mergeResults = $mergeResults
        triage       = $triageJson
        triageLatest = $triageLatest
        issueUpdate  = $issueUpdateMd
    } | ConvertTo-Json)
