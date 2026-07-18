<#
.SYNOPSIS
    Queue/merge Dependabot PRs that are marked Ready=true.

.DESCRIPTION
    Reads a JSON file produced by scripts/Test-PrMergeReadiness.ps1 (or a ready-only subset)
    and attempts to merge each PR via GitHub CLI.

    This script is defensive:
    - Accepts common JSON shapes: array, single object, wrapper { value: [...] }
    - Skips entries with missing/blank Url
    - Skips entries where Ready is explicitly false
    - Always writes a results JSON file (even when no PRs are attempted)

.PARAMETER ReadyJson
    Path to readiness JSON (array of objects containing Url, Repo, Pr, Ready).

.PARAMETER ResultsJson
    Path to write results JSON.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\Invoke-DependabotAutoMerge.ps1 \
      -ReadyJson reviews\dependabot-wave17-ready-all-20260326-123000.json \
      -ResultsJson reviews\dependabot-wave17-automerge-20260326-123000.json

.EXAMPLE
    # Dry run
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\Invoke-DependabotAutoMerge.ps1 \
      -ReadyJson reviews\dependabot-wave17-ready-all-20260326-123000.json \
      -ResultsJson reviews\dependabot-wave17-automerge-20260326-123000.json \
      -WhatIf
#>

[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$ReadyJson,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$ResultsJson
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

function Write-Utf8NoBom {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Content
    )

    $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllText($Path, $Content, $utf8NoBom)
}

function Get-ReadyItems {
    param(
        [Parameter(Mandatory = $true)]
        $InputObject
    )

    # Handle common shapes:
    # - Array of PR rows
    # - Single object
    # - Wrapper object: { value: [ ... ], Count: N }
    $items = @()

    if ($null -eq $InputObject) {
        return @()
    }

    if ($InputObject -is [System.Array]) {
        $items = @($InputObject)
    }
    elseif ($InputObject.PSObject -and ($InputObject.PSObject.Properties.Name -contains 'value')) {
        $items = @($InputObject.value)
    }
    else {
        $items = @($InputObject)
    }

    # Be defensive: only attempt merges on rows that look mergeable.
    $items = @($items | Where-Object {
            ($null -ne $_) -and
            ($null -ne $_.Url) -and
            (-not [string]::IsNullOrWhiteSpace([string]$_.Url)) -and
            ($null -eq $_.Ready -or $_.Ready -eq $true)
        })

    return $items
}

function Invoke-PrMerge {
    param(
        [Parameter(Mandatory = $true)][string]$Url
    )

    $attempts = @(
        @{ label = '--auto'; args = @('--auto') },
        @{ label = '--auto --squash'; args = @('--auto', '--squash') },
        @{ label = '--auto --merge'; args = @('--auto', '--merge') },
        @{ label = '--auto --rebase'; args = @('--auto', '--rebase') },
        @{ label = '(default)'; args = @() },
        @{ label = '--squash'; args = @('--squash') },
        @{ label = '--merge'; args = @('--merge') },
        @{ label = '--rebase'; args = @('--rebase') }
    )

    foreach ($a in $attempts) {
        $outLines = & gh pr merge $Url @($a.args) 2>&1
        $out = ($outLines | Out-String).TrimEnd()

        if ($LASTEXITCODE -eq 0) {
            return [pscustomobject]@{ ok = $true; action = "gh pr merge $($a.label)"; output = $out }
        }

        if ($out -match '(?i)already merged|is merged|is closed|pull request is not open') {
            return [pscustomobject]@{ ok = $true; action = "gh pr merge $($a.label)"; output = $out }
        }

        # Continue trying fallbacks.
    }

    return [pscustomobject]@{ ok = $false; action = 'gh pr merge (all attempts failed)'; output = 'All merge attempts failed.' }
}

Assert-GhCli
Assert-GhAuth

$readyObj = Get-Content -LiteralPath $ReadyJson -Raw | ConvertFrom-Json
$ready = Get-ReadyItems -InputObject $readyObj

$results = @()
foreach ($p in $ready) {
    $url = [string]$p.Url
    $repo = $p.Repo
    $prNumber = $p.Pr

    if (-not $PSCmdlet.ShouldProcess($url, 'gh pr merge')) {
        $results += [pscustomobject]@{
            repo   = $repo
            number = $prNumber
            url    = $url
            ok     = $false
            action = 'skipped (WhatIf)'
            output = ''
        }
        continue
    }

    try {
        $merge = Invoke-PrMerge -Url $url
        $results += [pscustomobject]@{
            repo   = $repo
            number = $prNumber
            url    = $url
            ok     = [bool]$merge.ok
            action = $merge.action
            output = $merge.output
        }
    }
    catch {
        $results += [pscustomobject]@{
            repo   = $repo
            number = $prNumber
            url    = $url
            ok     = $false
            action = 'exception'
            output = $_.Exception.Message
        }
    }
}

# Always write a results file (even when $results is empty).
$resultsJsonContent = ConvertTo-Json -InputObject $results -Depth 6
Write-Utf8NoBom -Path $ResultsJson -Content $resultsJsonContent

$okCount = @($results | Where-Object { $_.ok }).Count
$failCount = @($results | Where-Object { -not $_.ok }).Count
Write-Output ([pscustomobject]@{ readyCount = $ready.Count; okCount = $okCount; failCount = $failCount; results = $ResultsJson } | ConvertTo-Json)
