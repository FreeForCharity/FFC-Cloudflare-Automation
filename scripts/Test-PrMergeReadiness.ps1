<#
.SYNOPSIS
    Reports whether a set of PRs are merge-ready (checks passing, no unresolved review threads).

.DESCRIPTION
    Given a list of PR URLs (one per line), queries GitHub via GraphQL (through the gh CLI)
    and summarizes for each PR:
      - Status check rollup state (SUCCESS / FAILURE / PENDING / ERROR)
      - Review decision (APPROVED / CHANGES_REQUESTED / REVIEW_REQUIRED / null)
      - Merge state status (CLEAN / BEHIND / BLOCKED / etc.)
    - Mergeability (MERGEABLE / CONFLICTING / UNKNOWN)
      - Count of unresolved review threads

    This script is read-only and intended to support bulk triage before merging.

.PARAMETER PrUrlsPath
    Path to a file containing PR URLs (one per line).

.PARAMETER OutputPath
    Optional path to write a JSON report.

.PARAMETER AllowNoReviewDecision
    If set, PRs with a null reviewDecision are allowed to be considered ready.

.PARAMETER AllowBehind
    If set, PRs with mergeStateStatus == BEHIND are allowed to be considered ready.

.EXAMPLE
    ./scripts/Test-PrMergeReadiness.ps1 -PrUrlsPath ./reviews/pr-urls.txt

.EXAMPLE
    ./scripts/Test-PrMergeReadiness.ps1 -PrUrlsPath ./reviews/pr-urls.txt -OutputPath ./reviews/merge-readiness.json
#>

[CmdletBinding()]
param(
    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$PrUrlsPath = './reviews/pr-urls.txt',

    [Parameter()]
    [string]$OutputPath,

    [Parameter()]
    [switch]$AllowNoReviewDecision,

    [Parameter()]
    [switch]$AllowBehind
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

Assert-GhCli
Assert-GhAuth

function Write-Utf8NoBom {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Content
    )

    $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllText($Path, $Content, $utf8NoBom)
}

function Invoke-GhGraphQl {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string]$Query,

        [Parameter(Mandatory = $true)]
        [hashtable]$Variables
    )

    $args = @('api', 'graphql', '-f', "query=$Query")
    foreach ($k in $Variables.Keys) {
        $args += @('-F', "$k=$($Variables[$k])")
    }

    $oldEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $outLines = & gh @args 2>&1
    }
    finally {
        $ErrorActionPreference = $oldEap
    }

    if ($LASTEXITCODE -ne 0) {
        $out = ($outLines | Out-String)
        throw "gh api graphql failed: $out"
    }

    return (($outLines | Out-String) | ConvertFrom-Json)
}

function Parse-PrUrl {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string]$Url
    )

    $m = [regex]::Match($Url.Trim(), 'github\.com/(?<owner>[^/]+)/(?<repo>[^/]+)/pull/(?<num>\d+)')
    if (-not $m.Success) { return $null }

    return [pscustomobject]@{
        Owner = $m.Groups['owner'].Value
        Repo  = $m.Groups['repo'].Value
        Num   = [int]$m.Groups['num'].Value
        Url   = $Url.Trim()
        Full  = "$($m.Groups['owner'].Value)/$($m.Groups['repo'].Value)"
    }
}

function Get-CheckSummary {
    param(
        [Parameter()]
        $Rollup
    )

    if ($null -eq $Rollup) {
        return [pscustomobject]@{ State = $null; Total = 0; Failed = 0; Pending = 0 }
    }

    $failed = 0
    $pending = 0
    $total = 0
    $failedItems = @()
    $pendingItems = @()

    foreach ($ctx in @($Rollup.contexts.nodes)) {
        $total++
        if ($ctx.__typename -eq 'CheckRun') {
            # conclusion: SUCCESS | FAILURE | NEUTRAL | CANCELLED | TIMED_OUT | ACTION_REQUIRED | STALE | SKIPPED
            # status: QUEUED | IN_PROGRESS | COMPLETED
            if ($ctx.status -ne 'COMPLETED') {
                $pending++
                $pendingItems += [pscustomobject]@{
                    Type       = 'CheckRun'
                    Name       = $ctx.name
                    Status     = $ctx.status
                    Conclusion = $ctx.conclusion
                    Url        = $ctx.detailsUrl
                }
                continue
            }
            if ($ctx.conclusion -and $ctx.conclusion -ne 'SUCCESS' -and $ctx.conclusion -ne 'SKIPPED' -and $ctx.conclusion -ne 'NEUTRAL') {
                $failed++
                $failedItems += [pscustomobject]@{
                    Type       = 'CheckRun'
                    Name       = $ctx.name
                    Status     = $ctx.status
                    Conclusion = $ctx.conclusion
                    Url        = $ctx.detailsUrl
                }
            }
        }
        elseif ($ctx.__typename -eq 'StatusContext') {
            # state: EXPECTED | ERROR | FAILURE | PENDING | SUCCESS
            if ($ctx.state -eq 'PENDING' -or $ctx.state -eq 'EXPECTED') {
                $pending++
                $pendingItems += [pscustomobject]@{
                    Type  = 'StatusContext'
                    Name  = $ctx.context
                    State = $ctx.state
                    Url   = $ctx.targetUrl
                }
            }
            elseif ($ctx.state -ne 'SUCCESS') {
                $failed++
                $failedItems += [pscustomobject]@{
                    Type  = 'StatusContext'
                    Name  = $ctx.context
                    State = $ctx.state
                    Url   = $ctx.targetUrl
                }
            }
        }
    }

    return [pscustomobject]@{
        State        = $Rollup.state
        Total        = $total
        Failed       = $failed
        Pending      = $pending
        FailedItems  = $failedItems
        PendingItems = $pendingItems
    }
}

if (-not (Test-Path -LiteralPath $PrUrlsPath)) {
    throw "PR URLs file not found: $PrUrlsPath"
}

$urls = Get-Content -LiteralPath $PrUrlsPath | Where-Object { $_ -and $_.Trim() -and -not $_.Trim().StartsWith('#') }
$prs = @($urls | ForEach-Object { Parse-PrUrl -Url $_ } | Where-Object { $_ -ne $null })

if ($prs.Count -eq 0) {
    throw "No valid PR URLs found in: $PrUrlsPath"
}

$query = @'
query($owner:String!, $name:String!, $number:Int!) {
  repository(owner:$owner, name:$name) {
    pullRequest(number:$number) {
      url
      number
      state
      isDraft
      reviewDecision
      mergeStateStatus
            mergeable
      headRefName
      baseRefName
      commits(last: 1) {
        nodes {
          commit {
            statusCheckRollup {
              state
              contexts(first: 100) {
                nodes {
                  __typename
                  ... on CheckRun {
                    name
                    status
                    conclusion
                    detailsUrl
                  }
                  ... on StatusContext {
                    context
                    state
                    targetUrl
                  }
                }
              }
            }
          }
        }
      }
      reviewThreads(first: 100) {
        nodes {
                    id
          isResolved
          isOutdated
                    comments(last: 20) {
                        nodes {
                            author { login }
                            createdAt
                            bodyText
                        }
                    }
        }
      }
      reviews(last: 50) {
        nodes {
          author { login }
          state
          submittedAt
        }
      }
    }
  }
}
'@

$results = @()

foreach ($pr in $prs) {
    $row = [ordered]@{
        Repo                    = $pr.Full
        Pr                      = $pr.Num
        Url                     = $pr.Url
        State                   = $null
        IsDraft                 = $null
        ReviewDecision          = $null
        MergeStateStatus        = $null
        Mergeable               = $null
        ChecksState             = $null
        ChecksFailed            = 0
        ChecksPending           = 0
        FailedChecks            = @()
        PendingChecks           = @()
        UnresolvedThreads       = 0
        UnresolvedThreadDetails = @()
        Ready                   = $false
        Blockers                = @()
    }

    try {
        $obj = Invoke-GhGraphQl -Query $query -Variables @{ owner = $pr.Owner; name = $pr.Repo; number = $pr.Num }
        $p = $obj.data.repository.pullRequest

        $row.State = $p.state
        $row.IsDraft = [bool]$p.isDraft
        $row.ReviewDecision = $p.reviewDecision
        $row.MergeStateStatus = $p.mergeStateStatus
        $row.Mergeable = $p.mergeable

        $unresolved = 0
        $unresolvedDetails = @()
        foreach ($t in @($p.reviewThreads.nodes)) {
            if ($null -eq $t) { continue }
            if (-not $t.isOutdated -and -not $t.isResolved) {
                $unresolved++
                $latest = $null
                if ($t.comments.nodes.Count -gt 0) {
                    $latest = $t.comments.nodes[-1]
                }
                $unresolvedDetails += [pscustomobject]@{
                    Id         = $t.id
                    LatestBy   = $latest.author.login
                    LatestAt   = $latest.createdAt
                    LatestText = $latest.bodyText
                }
            }
        }
        $row.UnresolvedThreads = $unresolved
        $row.UnresolvedThreadDetails = $unresolvedDetails

        $rollup = $null
        if ($p.commits.nodes.Count -gt 0) {
            $rollup = $p.commits.nodes[0].commit.statusCheckRollup
        }
        $checkSummary = Get-CheckSummary -Rollup $rollup
        $row.ChecksState = $checkSummary.State
        $row.ChecksFailed = $checkSummary.Failed
        $row.ChecksPending = $checkSummary.Pending
        $row.FailedChecks = $checkSummary.FailedItems
        $row.PendingChecks = $checkSummary.PendingItems

        if ($row.State -ne 'OPEN') { $row.Blockers += "state=$($row.State)" }
        if ($row.IsDraft) { $row.Blockers += 'draft' }

        if (-not $AllowNoReviewDecision) {
            if (-not $row.ReviewDecision) { $row.Blockers += 'reviewDecision=null' }
        }
        if ($row.ReviewDecision -eq 'CHANGES_REQUESTED') { $row.Blockers += 'changes_requested' }

        if ($row.UnresolvedThreads -gt 0) { $row.Blockers += "unresolved_threads=$($row.UnresolvedThreads)" }

        if ($row.ChecksState -and $row.ChecksState -ne 'SUCCESS') { $row.Blockers += "checks=$($row.ChecksState)" }
        if ($row.ChecksFailed -gt 0) { $row.Blockers += "checks_failed=$($row.ChecksFailed)" }
        if ($row.ChecksPending -gt 0) { $row.Blockers += "checks_pending=$($row.ChecksPending)" }

        if (-not $AllowBehind) {
            if ($row.MergeStateStatus -eq 'BEHIND') { $row.Blockers += 'behind_base' }
        }

        if ($row.Mergeable -and $row.Mergeable -ne 'MERGEABLE') {
            $row.Blockers += "mergeable=$($row.Mergeable)"
        }

        $row.Ready = ($row.Blockers.Count -eq 0)

        $results += [pscustomobject]$row
    }
    catch {
        $row.Blockers += 'error'
        $row.Blockers += $_.Exception.Message
        $results += [pscustomobject]$row
    }
}

$results | Select-Object Repo, Pr, State, IsDraft, ReviewDecision, MergeStateStatus, Mergeable, ChecksState, ChecksFailed, ChecksPending, UnresolvedThreads, Ready, @{n = 'Blockers'; e = { ($_.Blockers -join '; ') } } | Format-Table -AutoSize -Wrap

if ($OutputPath) {
    Write-Utf8NoBom -Path $OutputPath -Content ($results | ConvertTo-Json -Depth 8)
    Write-Host "Wrote report: $OutputPath"
}

if ($results.Ready -contains $false) {
    exit 2
}
