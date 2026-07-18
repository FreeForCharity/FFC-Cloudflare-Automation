<#
.SYNOPSIS
    Idempotently set branch protection on the default branch of an FFC repo.

.DESCRIPTION
    Applies the FFC-standard branch-protection rules to the named branch of
    the named repo. Designed for single-committer admin repos (no required
    approval count) while still gating on CI status checks, CODEOWNERS
    review, and blocking force-pushes / deletions.

    The rules applied:
      - Pull request required before merge (0 approvals by default).
      - CODEOWNERS review: NOT required by default. Pass
        -RequireCodeOwnerReviews to enable. See note below — turning this
        on with a single committer + enforce_admins=true creates an
        unsatisfiable gate (no second person to approve, admins can't
        bypass).
      - Status checks must pass before merge. Default check set tuned to
        what this repo runs today: lint-format, CodeQL, Analyze (actions),
        Analyze (python). Override with -StatusCheckContexts.
      - require_branches_to_be_up_to_date: $true (strict).
      - Force-pushes blocked.
      - Branch deletion blocked.
      - enforce_admins: $true (rules apply to admins as well).
      - required_conversation_resolution: $true.
      - Signed commits: not required by default. Pass -RequireSignedCommits
        to enable (D3 #67 enables this on CBMadmin).

    SINGLE-COMMITTER NOTE: With enforce_admins=true and
    require_code_owner_reviews=true, a solo committer cannot merge any
    PR touching a CODEOWNERS-owned path — no one else exists to approve,
    and admins can't bypass. CODEOWNERS still drives the
    auto-requested-reviewer UX on new PRs even when this gate is off, so
    keeping it off until a second committer joins doesn't lose the
    routing benefit.

    The script is idempotent: re-running with the same parameters yields no
    change. Run with -WhatIf to preview the API payload without sending it.

.PARAMETER Owner
    GitHub org / user that owns the repo. Default: FreeForCharity.

.PARAMETER Repo
    Repo name. Required.

.PARAMETER Branch
    Branch to protect. Default: main.

.PARAMETER StatusCheckContexts
    Required status check names. Default: the names emitted by this repo's
    CI today. Inspect via 'gh pr checks <prNumber> --repo <owner>/<repo>' to find
    the right names for a different repo.

.PARAMETER RequireSignedCommits
    If set, requires signed commits on the branch. Off by default; D3 (#67)
    sets this on CBMadmin.

.PARAMETER RequiredApprovingReviewCount
    Number of PR approvals required before merge. Default: 0 (single-
    committer ergonomics).

.PARAMETER RequireCodeOwnerReviews
    If set, every PR touching a path listed in .github/CODEOWNERS requires
    explicit approval from the listed owner before merge. Off by default.
    See SINGLE-COMMITTER NOTE in DESCRIPTION above — turn on only after a
    second committer joins, otherwise PRs become unmergeable.

.EXAMPLE
    # Apply default FFC protection to CBMadmin's main
    pwsh -File scripts/github/Set-MainBranchProtection.ps1 -Repo FFC-IN-ClarkeMoyerAdmin

.EXAMPLE
    # Apply with signed commits required (D3)
    pwsh -File scripts/github/Set-MainBranchProtection.ps1 -Repo FFC-IN-ClarkeMoyerAdmin -RequireSignedCommits

.EXAMPLE
    # After a second committer joins, enforce CODEOWNERS review
    pwsh -File scripts/github/Set-MainBranchProtection.ps1 -Repo FFC-IN-ClarkeMoyerAdmin -RequireCodeOwnerReviews

.EXAMPLE
    # Preview without sending the PUT
    pwsh -File scripts/github/Set-MainBranchProtection.ps1 -Repo FFC-IN-ClarkeMoyerAdmin -WhatIf
#>

# SupportsShouldProcess is intentionally NOT declared. Windows PowerShell
# 5.1 doesn't reliably populate $PSCmdlet at script scope, so
# $PSCmdlet.ShouldProcess() throws NullReferenceException at the moment
# we'd actually need it. Use an explicit -WhatIf switch parameter
# instead (handled below as `if ($WhatIf) { ... }`). Reversible: the
# PUT can be rolled back by re-running with different params, or by
# DELETEing the protection rule.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [ValidateNotNullOrEmpty()]
    [string]$Owner = 'FreeForCharity',

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$Repo,

    [Parameter(Mandatory = $false)]
    [ValidateNotNullOrEmpty()]
    [string]$Branch = 'main',

    [Parameter(Mandatory = $false)]
    [string[]]$StatusCheckContexts = @(
        'lint-format',
        'CodeQL',
        'Analyze (actions)',
        'Analyze (python)'
    ),

    [Parameter(Mandatory = $false)]
    [switch]$RequireSignedCommits,

    [Parameter(Mandatory = $false)]
    [ValidateRange(0, 6)]
    [int]$RequiredApprovingReviewCount = 0,

    [Parameter(Mandatory = $false)]
    [switch]$RequireCodeOwnerReviews,

    [Parameter(Mandatory = $false)]
    [switch]$WhatIf
)

$ErrorActionPreference = 'Stop'

function Test-GhCli {
    if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
        throw "GitHub CLI (gh) not found. Install gh and run 'gh auth login' before using this script."
    }
    $auth = gh auth status 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) {
        throw "gh is not authenticated. Run: gh auth login`n$auth"
    }
}

Test-GhCli

# Verify the branch exists before trying to protect it.
$null = gh api "repos/$Owner/$Repo/branches/$Branch" --jq '.name' 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "Branch '$Branch' not found on $Owner/$Repo. Confirm the repo and branch exist and your token has read access."
}

# Build the protection payload.
$payload = [ordered]@{
    required_status_checks           = [ordered]@{
        strict   = $true
        # @(...) forces array shape so a single-element -StatusCheckContexts
        # still serializes as a JSON array (GitHub requires contexts to be an
        # array; ConvertTo-Json can otherwise unwrap a 1-element array to a
        # scalar and the PUT would 422).
        contexts = @($StatusCheckContexts)
    }
    enforce_admins                   = $true
    required_pull_request_reviews    = [ordered]@{
        required_approving_review_count = $RequiredApprovingReviewCount
        require_code_owner_reviews      = [bool]$RequireCodeOwnerReviews
        dismiss_stale_reviews           = $true
        require_last_push_approval      = $false
    }
    restrictions                     = $null
    allow_force_pushes               = $false
    allow_deletions                  = $false
    block_creations                  = $false
    required_conversation_resolution = $true
    required_linear_history          = $false
    # required_signatures is intentionally NOT in this payload. GitHub
    # exposes signed-commit protection via the separate endpoint
    # /repos/{owner}/{repo}/branches/{branch}/protection/required_signatures
    # (POST to enable, DELETE to disable). Setting it here is silently
    # ignored or rejected by API validation. Handled below after the
    # main PUT succeeds.
}

$payloadJson = $payload | ConvertTo-Json -Depth 6 -Compress:$false

Write-Host "Payload:" -ForegroundColor Cyan
Write-Host $payloadJson

if ($WhatIf) {
    Write-Host "(WhatIf) Would PUT branch protection on $Owner/$Repo (branch: $Branch). Skipping." -ForegroundColor Yellow
    return
}

# gh api accepts JSON on stdin via --input -
$tempFile = $null
try {
    $tempFile = Join-Path ([IO.Path]::GetTempPath()) ("branch-protection-{0}.json" -f ([Guid]::NewGuid().ToString('n')))
    [System.IO.File]::WriteAllText($tempFile, $payloadJson, (New-Object System.Text.UTF8Encoding($false)))

    $response = gh api `
        --method PUT `
        "repos/$Owner/$Repo/branches/$Branch/protection" `
        --input $tempFile

    if ($LASTEXITCODE -ne 0) {
        throw "gh api PUT failed (exit $LASTEXITCODE). Response: $response"
    }

    # required_signatures is set via its own endpoint, not in the main
    # protection PUT (see comment above the payload). POST to enable,
    # DELETE to disable. Idempotent — re-running with the same desired
    # state is a no-op from GitHub's perspective.
    if ($RequireSignedCommits) {
        Write-Host "Enabling required_signatures (separate endpoint)..." -ForegroundColor Cyan
        gh api --method POST "repos/$Owner/$Repo/branches/$Branch/protection/required_signatures" -H 'Accept: application/vnd.github.zzzax-preview+json' --silent
        if ($LASTEXITCODE -ne 0) { throw "Failed to enable required_signatures (exit $LASTEXITCODE)." }
    }
    else {
        # Best-effort disable. Endpoint returns 404 if it was already off, which is fine.
        gh api --method DELETE "repos/$Owner/$Repo/branches/$Branch/protection/required_signatures" --silent 2>$null
    }

    Write-Host "Branch protection applied." -ForegroundColor Green
    Write-Host "Verify:" -ForegroundColor Cyan
    gh api "repos/$Owner/$Repo/branches/$Branch/protection" --jq '{
        required_status_checks: .required_status_checks,
        enforce_admins: .enforce_admins.enabled,
        required_pull_request_reviews: .required_pull_request_reviews,
        allow_force_pushes: .allow_force_pushes.enabled,
        allow_deletions: .allow_deletions.enabled,
        required_signatures: .required_signatures.enabled,
        required_conversation_resolution: .required_conversation_resolution.enabled
    }'
}
finally {
    if ($tempFile -and (Test-Path -LiteralPath $tempFile)) {
        Remove-Item -LiteralPath $tempFile -Force -ErrorAction SilentlyContinue
    }
}
