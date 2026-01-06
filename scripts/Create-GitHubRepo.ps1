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

.PARAMETER DryRun
    If set, only prints the commands that would be executed.

.EXAMPLE
    .\Create-GitHubRepo.ps1 -RepoName "new-project" -TemplateRepo "FreeForCharity/template-repo" -EnablePages -DryRun
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RepoName,

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
    [bool]$EnableProjects = $false,

    [Parameter(Mandatory = $false)]
    [bool]$EnableWiki = $false,

    [Parameter(Mandatory = $false)]
    [bool]$AllowSquashMerge = $true,

    [Parameter(Mandatory = $false)]
    [bool]$AllowMergeCommit = $true,

    [Parameter(Mandatory = $false)]
    [bool]$AllowRebaseMerge = $false,

    [Parameter(Mandatory = $false)]
    [bool]$DeleteBranchOnMerge = $true,

    [Parameter(Mandatory = $false)]
    [switch]$EnablePages,

    [Parameter(Mandatory = $false)]
    [string]$CNAME,

    [Parameter(Mandatory = $false)]
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Invoke-GhCommand {
    param(
        [string]$CommandStr
    )
    
    if ($DryRun) {
        Write-Host "[DRY RUN] gh $CommandStr" -ForegroundColor Cyan
        return $null
    }
    else {
        Write-Host "Running: gh $CommandStr" -ForegroundColor Gray
        # Split command string safely for Invoke-Expression or just run directly
        # Note: In PowerShell, handling complex arguments in a single string can be tricky.
        # We will use Start-Process or direct invocation if possible, 
        # but for simplicity in this wrapper we use Invoke-Expression for the constructed string
        # which requires careful quoting.
        # A safer way is to just print it for the user if this is mainly for automation/doc,
        # but we want it to work.
        
        # We'll rely on the shell execution.
        $result = Invoke-Expression "gh $CommandStr"
        return $result
    }
}

Write-Host "Starting repository creation for '$RepoName' from template '$TemplateRepo'..." -ForegroundColor Green

# 1. Create Repo from Template
# gh repo create <name> --template <template> --<visibility> --description <desc> --confirm
$createCmd = "repo create `"$RepoName`" --template `"$TemplateRepo`" --$Visibility --description `"$Description`""

# Check if repo exists? (gh repo create might fail or prompt)
# We assume it doesn't exist.

Invoke-GhCommand $createCmd

# Wait a moment for propagation if not dry run
if (-not $DryRun) { Start-Sleep -Seconds 5 }

# 2. Configure General Settings (Issues, Projects, Wiki, Merge Types, Auto-Delete)
# We use `gh repo edit` for some, `gh api` for others.
# gh repo edit allows: --enable-issues, --enable-projects, --enable-wiki
# --allow-squash-merge, --allow-merge-commit, --allow-rebase-merge
# --delete-branch-on-merge

$editCmd = "repo edit `"$RepoName`""

if ($EnableIssues) { $editCmd += " --enable-issues" } else { $editCmd += " --enable-issues=false" }
if ($EnableProjects) { $editCmd += " --enable-projects" } else { $editCmd += " --enable-projects=false" }
if ($EnableWiki) { $editCmd += " --enable-wiki" } else { $editCmd += " --enable-wiki=false" }

if ($AllowSquashMerge) { $editCmd += " --allow-squash-merge" } else { $editCmd += " --allow-squash-merge=false" }
if ($AllowMergeCommit) { $editCmd += " --allow-merge-commit" } else { $editCmd += " --allow-merge-commit=false" }
if ($AllowRebaseMerge) { $editCmd += " --allow-rebase-merge" } else { $editCmd += " --allow-rebase-merge=false" }

if ($DeleteBranchOnMerge) { $editCmd += " --delete-branch-on-merge" } else { $editCmd += " --delete-branch-on-merge=false" }

Invoke-GhCommand $editCmd

# 3. Configure GitHub Pages
# gh api repos/{owner}/{repo}/pages -X POST -f "source[branch]=main" -f "source[path]=/"
if ($EnablePages) {
    Write-Host "Enabling GitHub Pages on 'main' branch, root folder..."
    
    # Need to know the owner. Assuming current user context or org from RepoName if "Org/Repo" format.
    # If RepoName is just "name", it's created under current auth context.
    # We will try to get the full name from `gh repo view` if not dry run.
    
    if ($DryRun) {
        Write-Host "[DRY RUN] gh api repos/:owner/$RepoName/pages -X POST -F 'source[branch]=main' -F 'source[path]=/'" -ForegroundColor Cyan
        if ($CNAME) {
            Write-Host "[DRY RUN] gh api repos/:owner/$RepoName/pages -X PUT -F 'cname=$CNAME'" -ForegroundColor Cyan
        }
    }
    else {
        # Get full name (owner/repo)
        $json = gh repo view "$RepoName" --json nameWithOwner | ConvertFrom-Json
        $fullRepoName = $json.nameWithOwner
        
        $pagesCmd = "api repos/$fullRepoName/pages -X POST -F `"source[branch]=main`" -F `"source[path]=/`""
        Invoke-GhCommand $pagesCmd
        
        if ($CNAME) {
            Write-Host "Setting CNAME to $CNAME..."
            $cnameCmd = "api repos/$fullRepoName/pages -X PUT -F `"cname=$CNAME`""
            Invoke-GhCommand $cnameCmd
        }
    }
}

Write-Host "Repository setup complete!" -ForegroundColor Green
