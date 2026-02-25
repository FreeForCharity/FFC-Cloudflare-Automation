<#
.SYNOPSIS
    Creates a new GitHub repository from a template and configures settings.

.DESCRIPTION
    This script acts as a wrapper around the GitHub CLI (gh) to streamline the creation
    of new repositories from a template. It handles:
    1. Creating the repository from a specified template.
    2. Setting visibility (Public/Private).
    3. Enabling/Disabling features (Issues, Projects, Wiki).
    4. configuring Merge Strategies (Squash, Merge, Rebase).
    5. Setting "Auto-delete head branches".
    6. Configuring GitHub Pages (optionally).
    7. Configuring Branch Protection (via API, if supported).

.PARAMETER RepoName
    The name of the new repository to create (e.g., "my-new-repo").

.PARAMETER Description
    A description for the new repository.

.PARAMETER TemplateRepo
    The "owner/repo" string of the template repository to use.

.PARAMETER Visibility
    "public", "private", or "internal". Default: "public".

.PARAMETER EnableIssues
    Switch to enable Issues. Default: $true.

.PARAMETER EnableProjects
    Switch to enable Projects. Default: $false.

.PARAMETER EnableWiki
    Switch to enable Wiki. Default: $false.

.PARAMETER AllowSquashMerge
    Switch to allow squash merging. Default: $true.

.PARAMETER AllowMergeCommit
    Switch to allow merge commits. Default: $true.

.PARAMETER AllowRebaseMerge
    Switch to allow rebase merging. Default: $false.

.PARAMETER DeleteBranchOnMerge
    Switch to automatically delete heade branches after merging. Default: $true.

.PARAMETER EnablePages
    Switch to enable GitHub Pages on the default branch (usually 'main') and root ('/') folder.

.PARAMETER PagesDomainType
    Specifies how to configure the custom domain for GitHub Pages:
    - "apex": Uses the domain as-is (e.g., example.org)
    - "staging": Adds 'staging.' prefix (e.g., staging.example.org)
    - "github-default": No custom domain, uses GitHub-provided URL

.PARAMETER CNAME
    Custom domain (CNAME) for GitHub Pages. If not provided, will be auto-detected based on PagesDomainType.

.PARAMETER DryRun
    If set, only prints the commands that would be executed.

.EXAMPLE
    .\Create-GitHubRepo.ps1 -RepoName "FFC-EX-slopestohope.org" -TemplateRepo "FreeForCharity/FFC-IN-Single_Page_Template_Jekell" -EnablePages -DryRun
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RepoName,

    [Parameter(Mandatory = $false)]
    [string]$Organization = "FreeForCharity",

    [Parameter(Mandatory = $false)]
    [string]$Description = "Created via automation",

    [Parameter(Mandatory = $true)]
    [string]$TemplateRepo,

    [Parameter(Mandatory = $false)]
    [ValidateSet("public", "private", "internal")]
    [string]$Visibility = "public",

    [Parameter(Mandatory = $false)]
    [bool]$EnableIssues = $true,

    [Parameter(Mandatory = $false)]
    [bool]$EnableProjects = $true,

    [Parameter(Mandatory = $false)]
    [bool]$EnableWiki = $true,

    [Parameter(Mandatory = $false)]
    [bool]$AllowSquashMerge = $true,

    [Parameter(Mandatory = $false)]
    [bool]$AllowMergeCommit = $true,

    [Parameter(Mandatory = $false)]
    [bool]$AllowRebaseMerge = $true,

    [Parameter(Mandatory = $false)]
    [bool]$DeleteBranchOnMerge = $true,

    [Parameter(Mandatory = $false)]
    [switch]$EnablePages,

    [Parameter(Mandatory = $false)]
    [ValidateSet("apex", "staging", "github-default")]
    [string]$PagesDomainType = "apex",

    [Parameter(Mandatory = $false)]
    [string]$CNAME,

    [Parameter(Mandatory = $false)]
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Invoke-GhCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Args
    )

    # Invoke the GitHub CLI using an argument array (no Invoke-Expression).

    function Format-GhArgs {
        param(
            [Parameter(Mandatory = $true)]
            [string[]]$Args
        )

        return (
            $Args |
                ForEach-Object {
                    if ($_ -match '\s|["'']') {
                        '"' + ($_ -replace '"', '``"') + '"'
                    }
                    else {
                        $_
                    }
                }
        ) -join ' '
    }

    $formatted = Format-GhArgs -Args $Args

    if ($DryRun) {
        Write-Host "[DRY RUN] gh $formatted" -ForegroundColor Cyan
        return $null
    }

    Write-Host "Running: gh $formatted" -ForegroundColor Gray
    $result = & gh @Args

    if ($LASTEXITCODE -ne 0) {
        throw "gh command failed with exit code $($LASTEXITCODE): gh $formatted"
    }

    return $result
}

function Invoke-NativeNonTerminating {
    param(
        [Parameter(Mandatory = $true)]
        [scriptblock]$ScriptBlock
    )

    $oldEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'

    $hadNativePref = $false
    $oldNativePref = $null
    try {
        $nativePrefVar = Get-Variable -Name 'PSNativeCommandUseErrorActionPreference' -Scope Global -ErrorAction SilentlyContinue
        if ($null -ne $nativePrefVar) {
            $hadNativePref = $true
            $oldNativePref = $global:PSNativeCommandUseErrorActionPreference
            $global:PSNativeCommandUseErrorActionPreference = $false
        }
    }
    catch {
        # best-effort; continue
    }

    try {
        & $ScriptBlock
    }
    finally {
        $ErrorActionPreference = $oldEap
        if ($hadNativePref) {
            $global:PSNativeCommandUseErrorActionPreference = $oldNativePref
        }
    }
}

function Copy-RepoScopedRulesetsFromTemplate {
    param(
        [string]$TemplateRepoNameWithOwner,
        [string]$TargetRepoNameWithOwner
    )

    if ($DryRun) {
        Write-Host "[DRY RUN] would copy repo-scoped rulesets from $TemplateRepoNameWithOwner to $TargetRepoNameWithOwner" -ForegroundColor Cyan
        return
    }

    if ([string]::IsNullOrWhiteSpace($TemplateRepoNameWithOwner) -or [string]::IsNullOrWhiteSpace($TargetRepoNameWithOwner)) {
        Write-Warning "Ruleset sync skipped (missing template or target repo nameWithOwner)."
        return
    }

    Write-Host "Syncing repo-scoped rulesets from template..." -ForegroundColor Gray

    $tplRulesets = $null
    $tgtRulesets = $null
    try {
        $tplRulesets = gh api "repos/$TemplateRepoNameWithOwner/rulesets" --paginate 2>&1 | ConvertFrom-Json
        if ($LASTEXITCODE -ne 0) { throw "gh api template rulesets failed" }
    }
    catch {
        Write-Warning "Could not list template rulesets for $TemplateRepoNameWithOwner. Skipping ruleset sync."
        $global:LASTEXITCODE = 0
        return
    }

    try {
        $tgtRulesets = gh api "repos/$TargetRepoNameWithOwner/rulesets" --paginate 2>&1 | ConvertFrom-Json
        if ($LASTEXITCODE -ne 0) { throw "gh api target rulesets failed" }
    }
    catch {
        Write-Warning "Could not list target rulesets for $TargetRepoNameWithOwner. Skipping ruleset sync."
        $global:LASTEXITCODE = 0
        return
    }

    $tgtNames = @{}
    foreach ($r in $tgtRulesets) {
        if ($r.name -and -not $tgtNames.ContainsKey($r.name)) { $tgtNames[$r.name] = $true }
    }

    $tplRepoScoped = @($tplRulesets | Where-Object { $_.source_type -eq 'Repository' })
    foreach ($rs in $tplRepoScoped) {
        if ($tgtNames.ContainsKey($rs.name)) {
            Write-Host "Ruleset already present: $($rs.name)" -ForegroundColor Gray
            continue
        }

        $details = $null
        try {
            $details = gh api "repos/$TemplateRepoNameWithOwner/rulesets/$($rs.id)" 2>&1 | ConvertFrom-Json
            if ($LASTEXITCODE -ne 0) { throw "gh api ruleset details failed" }
        }
        catch {
            Write-Warning "Could not fetch template ruleset $($rs.id). Skipping."
            $global:LASTEXITCODE = 0
            continue
        }

        # Some rule types may not be creatable via REST API for all repos/tokens.
        $rulesFiltered = @($details.rules | Where-Object { $_.type -ne 'copilot_code_review_analysis_tools' })
        if ($rulesFiltered.Count -ne @($details.rules).Count) {
            Write-Warning "Template ruleset '$($details.name)' contains rule type 'copilot_code_review_analysis_tools' which is not creatable via this API; omitting it on creation."
        }

        $payload = [ordered]@{
            name = $details.name
            target = $details.target
            enforcement = $details.enforcement
            bypass_actors = $details.bypass_actors
            conditions = $details.conditions
            rules = $rulesFiltered
        }

        $payloadJson = $payload | ConvertTo-Json -Depth 80
        try {
            $payloadJson | gh api -X POST "repos/$TargetRepoNameWithOwner/rulesets" --input - 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "gh api create ruleset failed" }
            Write-Host "Created ruleset: $($details.name)" -ForegroundColor Green
        }
        catch {
            Write-Warning "Failed to create ruleset '$($details.name)' on $TargetRepoNameWithOwner."
            $global:LASTEXITCODE = 0
        }
    }
}

function Ensure-CopilotReviewAllBranchesRuleset {
    param(
        [string]$TargetRepoNameWithOwner
    )

    if ($DryRun) {
        Write-Host "[DRY RUN] would ensure Copilot review ruleset on $TargetRepoNameWithOwner" -ForegroundColor Cyan
        return
    }

    if ([string]::IsNullOrWhiteSpace($TargetRepoNameWithOwner)) {
        Write-Warning "Copilot ruleset ensure skipped (missing target repo nameWithOwner)."
        return
    }

    $rulesetName = 'Copilot Review - All Branches'

    $existing = $null
    try {
        $existing = gh api "repos/$TargetRepoNameWithOwner/rulesets" --paginate 2>&1 | ConvertFrom-Json
        if ($LASTEXITCODE -ne 0) { throw "gh api rulesets list failed" }
    }
    catch {
        Write-Warning "Could not list rulesets for $TargetRepoNameWithOwner. Skipping Copilot ruleset ensure."
        $global:LASTEXITCODE = 0
        return
    }

    $match = $existing | Where-Object { $_.source_type -eq 'Repository' -and $_.target -eq 'branch' -and $_.name -eq $rulesetName } | Select-Object -First 1

    $payload = [ordered]@{
        name = $rulesetName
        target = 'branch'
        enforcement = 'active'
        bypass_actors = @()
        conditions = @{ ref_name = @{ include = @('refs/heads/*'); exclude = @() } }
        rules = @(
            @{ type = 'copilot_code_review'; parameters = @{ review_on_push = $true; review_draft_pull_requests = $true } }
        )
    }

    $payloadJson = $payload | ConvertTo-Json -Depth 20

    if ($null -eq $match) {
        try {
            Write-Host "Creating ruleset: $rulesetName" -ForegroundColor Gray
            $payloadJson | gh api -X POST "repos/$TargetRepoNameWithOwner/rulesets" --input - 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "gh api create ruleset failed" }
            Write-Host "Created ruleset: $rulesetName" -ForegroundColor Green
        }
        catch {
            Write-Warning "Failed to create Copilot ruleset '$rulesetName' on $TargetRepoNameWithOwner."
            $global:LASTEXITCODE = 0
        }
    }
    else {
        # Best-effort update: GitHub supports updating rulesets via REST, but if it fails we keep the existing.
        try {
            Write-Host "Updating ruleset: $rulesetName" -ForegroundColor Gray
            $payloadJson | gh api -X PUT "repos/$TargetRepoNameWithOwner/rulesets/$($match.id)" --input - 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "gh api update ruleset failed" }
            Write-Host "Updated ruleset: $rulesetName" -ForegroundColor Green
        }
        catch {
            Write-Warning "Could not update existing Copilot ruleset '$rulesetName' on $TargetRepoNameWithOwner."
            $global:LASTEXITCODE = 0
        }
    }
}

Write-Host "Starting repository creation for '$RepoName' from template '$TemplateRepo'..." -ForegroundColor Green

if ($RepoName -notmatch "^FFC-EX-") {
    Write-Warning "The repository name '$RepoName' does not follow the recommended convention 'FFC-EX-<domainname>'."
    Write-Warning "Example: 'FFC-EX-slopestohope.org'"
    # We continue execution, assuming the user might intentionally want a different name.
}

# 1. Create Repo from Template
# Propagate Organization if not in RepoName
if ($RepoName -notmatch "/") {
    $TargetRepo = "$Organization/$RepoName"
}
else {
    $TargetRepo = $RepoName
}

# gh repo create <name> --template <template> --<visibility> --description <desc>
$visibilityArg = "--$Visibility"
$createArgs = @(
    'repo', 'create',
    $TargetRepo,
    '--template', $TemplateRepo,
    $visibilityArg,
    '--description', $Description
)

$repoExists = $false
if ($DryRun) {
    Write-Host "[DRY RUN] would check whether repo exists: $TargetRepo" -ForegroundColor Cyan
}
else {
    # gh repo view returns exit code 1 if missing; do not treat as fatal.
    Invoke-NativeNonTerminating { gh repo view "$TargetRepo" --json nameWithOwner 1>$null 2>$null }
    if ($LASTEXITCODE -eq 0) {
        $repoExists = $true
    }
    $global:LASTEXITCODE = 0
}

if ($repoExists) {
    Write-Host "Repo already exists: $TargetRepo. Skipping creation." -ForegroundColor Yellow
}
else {
    Invoke-GhCommand -Args $createArgs

    # Wait a moment for propagation if not dry run
    if (-not $DryRun) { Start-Sleep -Seconds 5 }
}

# 2. Configure General Settings (Issues, Projects, Wiki, Merge Types, Auto-Delete)
# We use `gh repo edit` for some, `gh api` for others.
# gh repo edit allows: --enable-issues, --enable-projects, --enable-wiki
# --allow-squash-merge, --allow-merge-commit, --allow-rebase-merge
# --delete-branch-on-merge

$editArgs = @('repo', 'edit', $TargetRepo)

if ($EnableIssues) { $editArgs += '--enable-issues' } else { $editArgs += '--enable-issues=false' }
if ($EnableProjects) { $editArgs += '--enable-projects' } else { $editArgs += '--enable-projects=false' }
if ($EnableWiki) { $editArgs += '--enable-wiki' } else { $editArgs += '--enable-wiki=false' }

if ($AllowSquashMerge) { $editArgs += '--enable-squash-merge' } else { $editArgs += '--enable-squash-merge=false' }
if ($AllowMergeCommit) { $editArgs += '--enable-merge-commit' } else { $editArgs += '--enable-merge-commit=false' }
if ($AllowRebaseMerge) { $editArgs += '--enable-rebase-merge' } else { $editArgs += '--enable-rebase-merge=false' }

if ($DeleteBranchOnMerge) { $editArgs += '--delete-branch-on-merge' } else { $editArgs += '--delete-branch-on-merge=false' }

Invoke-GhCommand -Args $editArgs

# 2b. Sync template repo-scoped rulesets (branch protection, merge queue, etc.)
if (-not $DryRun) {
    try {
        $tplJson = gh repo view "$TemplateRepo" --json nameWithOwner | ConvertFrom-Json
        $tgtJson = gh repo view "$TargetRepo" --json nameWithOwner | ConvertFrom-Json
        Copy-RepoScopedRulesetsFromTemplate -TemplateRepoNameWithOwner $tplJson.nameWithOwner -TargetRepoNameWithOwner $tgtJson.nameWithOwner

        # 2c. Ensure Copilot review runs on all branches and draft PRs
        Ensure-CopilotReviewAllBranchesRuleset -TargetRepoNameWithOwner $tgtJson.nameWithOwner
    }
    catch {
        Write-Warning "Ruleset sync skipped due to an error resolving repo names."
        $global:LASTEXITCODE = 0
    }
}

# 3. Configure GitHub Pages
# gh api repos/{owner}/{repo}/pages -X POST -f "source[branch]=main" -f "source[path]=/"
if ($EnablePages) {
    Write-Host "Enabling GitHub Pages on 'main' branch, root folder..."
    
    # 3a. Validate PagesDomainType and CNAME compatibility
    if ($PagesDomainType -eq "github-default" -and -not [string]::IsNullOrWhiteSpace($CNAME)) {
        Write-Warning "PagesDomainType is set to 'github-default' (no custom domain), but CNAME parameter was provided. Ignoring CNAME and using GitHub-provided URL."
        $CNAME = $null
    }
    
    # 3b. Auto-detect CNAME if not provided based on PagesDomainType
    if ([string]::IsNullOrWhiteSpace($CNAME)) {
        if ($PagesDomainType -eq "github-default") {
            # No custom domain needed
            Write-Host "Using GitHub-provided URL (no custom domain)" -ForegroundColor Cyan
        }
        elseif ($RepoName -match "^FFC-EX-(.+)$") {
            $detectedDomain = $matches[1]
            
            if ($PagesDomainType -eq "staging") {
                $CNAME = "staging.$detectedDomain"
                Write-Host "Auto-detected staging subdomain (CNAME): $CNAME" -ForegroundColor Cyan
            }
            else {
                # apex domain
                $CNAME = $detectedDomain
                Write-Host "Auto-detected apex domain (CNAME): $CNAME" -ForegroundColor Cyan
            }
        }
        else {
            # Repo name doesn't match expected pattern for auto-detection
            if ($PagesDomainType -ne "github-default") {
                Write-Warning "Cannot auto-detect domain: repository name '$RepoName' does not match 'FFC-EX-<domain>' pattern. No custom domain will be configured. Please provide a manual CNAME or use 'github-default' PagesDomainType."
            }
        }
    }

    # Need to know the owner. Assuming current user context or org from RepoName if "Org/Repo" format.
    # If RepoName is just "name", it's created under current auth context.
    # We will try to get the full name from `gh repo view` if not dry run.
    
    if ($DryRun) {
        Write-Host "[DRY RUN] gh api repos/:owner/$TargetRepo/pages -X POST -F 'source[branch]=main' -F 'source[path]=/'" -ForegroundColor Cyan
        if ($CNAME) {
            Write-Host "[DRY RUN] gh api repos/:owner/$TargetRepo/pages -X PUT -F 'cname=$CNAME'" -ForegroundColor Cyan
            Write-Host "[DRY RUN] gh api repos/:owner/$TargetRepo/pages -X PUT -F 'https_enforced=true'" -ForegroundColor Cyan
        }
    }
    else {
        # Get full name (owner/repo)
        $json = gh repo view "$TargetRepo" --json nameWithOwner | ConvertFrom-Json
        $fullRepoName = $json.nameWithOwner
        
        # Enable Pages (Source = Workflow)
        # The template 'FreeForCharity/FFC-IN-Single_Page_Template_Jekell' uses GitHub Actions ('deploy.yml').
        # So we must set build_type=workflow.
        
        Write-Host "Enabling Pages with build_type=workflow..."
        $pagesOutput = Invoke-NativeNonTerminating { gh api "repos/$fullRepoName/pages" -X POST -F "build_type=workflow" 2>&1 }
        if ($LASTEXITCODE -ne 0) {
            # Idempotency: GitHub returns conflict when Pages is already enabled.
            $pagesText = [string]($pagesOutput | Out-String)
            if ($pagesText -match '(?i)already\s+enabled|already\s+exists|conflict') {
                Write-Warning "GitHub Pages appears to already be enabled for $fullRepoName. Continuing."
                $global:LASTEXITCODE = 0
            }
            else {
                throw "Failed to enable GitHub Pages for $fullRepoName. Output: $pagesText"
            }
        }
        
        # Configure CNAME and Enforce HTTPS
        if ($CNAME) {
            Write-Host "Setting CNAME to $CNAME..."
            # 1. Set CNAME first (without HTTPS enforcement to avoid 'Certificate not ready' errors)
            $cnameArgs = @('api', "repos/$fullRepoName/pages", '-X', 'PUT', '-F', "cname=$CNAME")
            Invoke-GhCommand -Args $cnameArgs

            # 2. Try to Enforce HTTPS (This often fails immediately after CNAME set due to cert provisioning)
            Write-Host "Attempting to enforce HTTPS (may fail if cert is not yet provisioned)..."
            if ($DryRun) {
                Write-Host "[DRY RUN] gh api repos/$fullRepoName/pages -X PUT -F 'https_enforced=true'" -ForegroundColor Cyan
            }
            else {
                # We use a try/catch equivalent logic or just allow it to fail non-fatally
                try {
                    gh api repos/$fullRepoName/pages -X PUT -F "https_enforced=true" 2>&1 | Out-Null
                    if ($LASTEXITCODE -ne 0) {
                        Write-Warning "Could not enforce HTTPS immediately (Certificate likely provisioning). Please enable it later in Settings."
                        $global:LASTEXITCODE = 0
                    }
                    else {
                        Write-Host "HTTPS Enforcement Enabled." -ForegroundColor Green
                    }
                }
                catch {
                    Write-Warning "Could not enforce HTTPS immediately (Certificate likely provisioning). Please enable it later in Settings."
                    $global:LASTEXITCODE = 0
                }
            }
        }
    }
}

Write-Host "Repository setup complete!" -ForegroundColor Green

# Avoid leaking a non-fatal native exit code (e.g., from HTTPS enforcement)
$global:LASTEXITCODE = 0
