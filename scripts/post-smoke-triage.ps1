<#
.SYNOPSIS
    Post-deploy smoke triage across multiple repos.

.DESCRIPTION
    Reads a list of repos (e.g., from reviews/dependabot-affected-repos.json), fetches:
    - Latest Actions runs on main (optionally filtered to deploy/smoke-like workflows)
    - Open incident issues (simple search for "incident")
    - GitHub Pages publishing info (custom domain vs default Pages URL)

    Produces a JSON report and (optionally) posts a standardized triage comment
    on any open incident issues.

.PARAMETER ReposFile
    Path to a JSON file containing an array of "owner/name" repo strings.

.PARAMETER NonLiveFile
    Optional path to a JSON file containing objects like:
      [{ "repo": "Owner/Repo", "note": "...", "until": "YYYY-MM-DD" }]
    Used to annotate incidents as expected noise for pre-launch/non-live sites.

.PARAMETER OutputFile
    Path to write the resulting JSON report.

.PARAMETER CommentOnIncidents
    If set, posts a triage comment on each open incident issue found.

.PARAMETER LimitRuns
    Max number of workflow runs to consider per repo (default 20).

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\post-smoke-triage.ps1 \
      -ReposFile reviews\dependabot-affected-repos.json \
      -NonLiveFile reviews\non-live-sites.json \
      -OutputFile reviews\post-smoke-triage.json

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\post-smoke-triage.ps1 \
      -ReposFile reviews\dependabot-affected-repos.json \
      -NonLiveFile reviews\non-live-sites.json \
      -OutputFile reviews\post-smoke-triage.json \
      -CommentOnIncidents
#>

[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$ReposFile = './data/dependabot-affected-repos.json',

    [Parameter()]
    [string]$NonLiveFile = './data/non-live-sites.json',

    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$OutputFile = "./reviews/post-smoke-triage-$((Get-Date).ToString('yyyyMMdd-HHmmss')).json",

    [Parameter()]
    [switch]$UpdateLatest,

    [Parameter()]
    [switch]$CommentOnIncidents,

    [Parameter()]
    [ValidateRange(1, 100)]
    [int]$LimitRuns = 20
)

$ErrorActionPreference = 'Stop'

$AutoTriageMarker = '<!-- ffc-auto-triage:v1 -->'

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

function Read-JsonFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        throw "File not found: $Path"
    }

    $raw = Get-Content -LiteralPath $Path -Raw
    if ([string]::IsNullOrWhiteSpace($raw)) {
        return $null
    }

    return $raw | ConvertFrom-Json
}

function Get-NonLiveMap {
    param(
        [Parameter()][string]$Path
    )

    $map = @{}
    if ([string]::IsNullOrWhiteSpace($Path)) {
        return $map
    }

    $items = Read-JsonFile -Path $Path
    if ($null -eq $items) {
        return $map
    }

    foreach ($i in @($items)) {
        if ($null -eq $i.repo) { continue }
        $key = [string]$i.repo
        if ([string]::IsNullOrWhiteSpace($key)) { continue }
        $map[$key] = $i
    }

    return $map
}

function Get-PagesInfo {
    param(
        [Parameter(Mandatory = $true)][string]$Repo
    )

    try {
        $obj = gh api "repos/$Repo/pages" 2>$null | ConvertFrom-Json
        $cname = $obj.cname
        $htmlUrl = $obj.html_url

        $publishesTo = 'unknown'
        if (-not [string]::IsNullOrWhiteSpace($cname)) {
            $publishesTo = 'custom-domain'
        }
        elseif (-not [string]::IsNullOrWhiteSpace($htmlUrl) -and $htmlUrl -match 'github\.io') {
            $publishesTo = 'default-pages'
        }

        return [pscustomobject]@{
            hasPages      = $true
            publishesTo   = $publishesTo
            cname         = $cname
            htmlUrl       = $htmlUrl
            httpsEnforced = $obj.https_enforced
            public        = $obj.public
        }
    }
    catch {
        # Common case: repo has no Pages site.
        return [pscustomobject]@{
            hasPages      = $false
            publishesTo   = 'none'
            cname         = $null
            htmlUrl       = $null
            httpsEnforced = $null
            public        = $null
            error         = $_.Exception.Message
        }
    }
}

function Get-OpenIncidents {
    param(
        [Parameter(Mandatory = $true)][string]$Repo
    )

    # Simple heuristic: open issues with "incident" in title/body.
    $issues = gh issue list --repo $Repo --state open --search incident --limit 20 --json 'number,title,createdAt,updatedAt,url' 2>$null | ConvertFrom-Json

    if ($null -eq $issues) {
        return @()
    }

    # Normalize to an array even when gh returns a single object.
    if ($issues -is [System.Array]) {
        return @($issues)
    }

    return @($issues)
}

function Get-LatestRun {
    param(
        [Parameter(Mandatory = $true)][string]$Repo,
        [Parameter(Mandatory = $true)][int]$Limit
    )

    $runs = gh run list --repo $Repo --branch main --limit $Limit --json 'databaseId,name,workflowName,status,conclusion,createdAt,updatedAt,url' 2>$null | ConvertFrom-Json
    $runs = @($runs)

    if ($runs.Count -lt 1) {
        return $null
    }

    # Prefer deploy/smoke/pages/lighthouse-like workflows, else take newest.
    $preferred = @($runs | Where-Object {
            ($_.name -match '(?i)deploy|pages|smoke|lighthouse') -or
            ($_.workflowName -match '(?i)deploy|pages|smoke|lighthouse')
        })

    $picked = $null
    if ($preferred.Count -gt 0) {
        $picked = $preferred | Select-Object -First 1
    }
    else {
        $picked = $runs | Select-Object -First 1
    }

    return [pscustomobject]@{
        name       = $picked.name
        workflow   = $picked.workflowName
        status     = $picked.status
        conclusion = $picked.conclusion
        createdAt  = $picked.createdAt
        updatedAt  = $picked.updatedAt
        url        = $picked.url
        id         = $picked.databaseId
    }
}

function New-IncidentTriageComment {
    param(
        [Parameter(Mandatory = $true)][string]$Repo,
        [Parameter(Mandatory = $true)][psobject]$Pages,
        [Parameter()][psobject]$NonLive
    )

    $lines = @()

    $lines += 'Triage note (automated)'
    $lines += $AutoTriageMarker
    $lines += ''

    if ($Pages.hasPages -eq $true) {
        if ($Pages.publishesTo -eq 'custom-domain') {
            $lines += "Publishing: custom domain ($($Pages.cname))"
        }
        elseif ($Pages.publishesTo -eq 'default-pages') {
            $lines += "Publishing: default Pages URL ($($Pages.htmlUrl))"
        }
        else {
            $lines += 'Publishing: Pages enabled (publishing target unknown)'
        }
        $lines += "HTTPS enforced: $($Pages.httpsEnforced)"
    }
    else {
        $lines += 'Publishing: GitHub Pages not detected (or API not accessible)'
    }

    if ($null -ne $NonLive) {
        $note = $NonLive.note
        if ([string]::IsNullOrWhiteSpace([string]$note)) {
            $lines += ''
            $lines += 'Non-live: flagged as non-live/pre-launch (expected noise until launch)'
        }
        else {
            $lines += ''
            $lines += "Non-live: $note"
        }

        if ($NonLive.until) {
            $lines += "Until: $($NonLive.until)"
        }

        $lines += ''
        $lines += 'If this incident is expected pre-launch, consider closing it with this note or adjusting smoke checks to skip/target the default Pages URL until go-live.'
    }

    return ($lines -join "`n")
}

function Test-AlreadyAutoTriaged {
    param(
        [Parameter(Mandatory = $true)][string]$Repo,
        [Parameter(Mandatory = $true)][int]$IssueNumber
    )

    try {
        # Robust: paginate through all comments and scan for our marker.
        $bodies = gh api "repos/$Repo/issues/$IssueNumber/comments" --paginate --jq '.[].body' 2>$null
        if ($null -eq $bodies) {
            return $false
        }

        $text = (@($bodies) -join "`n")
        if ([string]::IsNullOrWhiteSpace([string]$text)) {
            return $false
        }

        return ($text -like "*$AutoTriageMarker*")
    }
    catch {
        return $false
    }
}

Assert-GhCli
Assert-GhAuth

$repos = Read-JsonFile -Path $ReposFile
$repos = @($repos)
if ($repos.Count -lt 1) {
    throw "No repos found in $ReposFile"
}

$nonLiveMap = Get-NonLiveMap -Path $NonLiveFile

$results = New-Object System.Collections.Generic.List[object]

foreach ($repo in $repos) {
    if ([string]::IsNullOrWhiteSpace([string]$repo)) { continue }

    try {
        $pages = Get-PagesInfo -Repo $repo
        $latest = Get-LatestRun -Repo $repo -Limit $LimitRuns
        $incidents = @(Get-OpenIncidents -Repo $repo)

        $commentedIncidents = @()

        $nonLive = $null
        if ($nonLiveMap.ContainsKey($repo)) {
            $nonLive = $nonLiveMap[$repo]
        }

        if ($CommentOnIncidents -and $incidents.Count -gt 0) {
            foreach ($issue in $incidents) {
                $body = New-IncidentTriageComment -Repo $repo -Pages $pages -NonLive $nonLive
                $issueNumber = [int]$issue.number

                if ($PSCmdlet.ShouldProcess("$repo#$issueNumber", 'gh issue comment')) {
                    if (Test-AlreadyAutoTriaged -Repo $repo -IssueNumber $issueNumber) {
                        Write-Host "Skip (already auto-triaged): $repo#$issueNumber"
                        continue
                    }

                    $commentOutLines = gh issue comment $issueNumber --repo $repo --body $body 2>&1
                    if ($LASTEXITCODE -eq 0) {
                        $commentedIncidents += $issueNumber
                        Write-Host "Commented: $repo#$issueNumber"
                    }
                    else {
                        $commentError = ($commentOutLines | Out-String).TrimEnd()
                        Write-Host "Comment failed: $repo#$issueNumber - $commentError"
                    }
                }
            }
        }

        $results.Add([pscustomobject]@{
                repo               = $repo
                pages              = $pages
                latestRun          = $latest
                openIncidentCount  = [int]($incidents.Count)
                openIncidents      = $incidents
                nonLive            = $nonLive
                commentedIncidents = $commentedIncidents
            })
    }
    catch {
        $results.Add([pscustomobject]@{
                repo  = $repo
                error = $_.Exception.Message
            })
    }
}

$results | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $OutputFile -Encoding utf8 -WhatIf:$false
Write-Host "Wrote: $OutputFile"

if ($UpdateLatest) {
    $latestPath = Join-Path (Split-Path -Parent $OutputFile) 'post-smoke-triage-latest.json'
    Copy-Item -Force $OutputFile $latestPath -WhatIf:$false
    Write-Host "Updated: $latestPath"
}