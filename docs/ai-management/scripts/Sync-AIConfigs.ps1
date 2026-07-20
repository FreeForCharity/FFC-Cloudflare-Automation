<#
.SYNOPSIS
    Syncs AI configuration templates to target FFC repositories.

.DESCRIPTION
    Reads the inventory from repos.json and pushes assembled AI config files
    (from templates/base + templates/overlays) to target repos via GitHub API.
    Creates a PR branch "chore/sync-ai-configs" rather than pushing to main.

.PARAMETER RepoName
    Sync to a single repo by name.

.PARAMETER RepoType
    Sync to all repos of a given type (e.g. "base", "powershell-infra").

.PARAMETER All
    Sync to every repo in the inventory.

.PARAMETER DryRun
    Show what would be changed without making any modifications.

.PARAMETER Organization
    GitHub organization. Defaults to "FreeForCharity".

.EXAMPLE
    .\Sync-AIConfigs.ps1 -RepoName "FFC-EX-legioninthewoods.org" -DryRun
    .\Sync-AIConfigs.ps1 -RepoType "base" -DryRun
    .\Sync-AIConfigs.ps1 -All
#>

[CmdletBinding()]
param(
    [string]$RepoName,
    [string]$RepoType,
    [switch]$All,
    [switch]$DryRun,
    [string]$Organization = "FreeForCharity"
)

# Import helper
. "$PSScriptRoot\Get-RepoType.ps1"

# ---------- Validate parameters ----------
if (-not $RepoName -and -not $RepoType -and -not $All) {
    Write-Error "Specify one of -RepoName, -RepoType, or -All."
    return
}

# ---------- Load inventory ----------
$inventoryPath = Join-Path $PSScriptRoot "..\inventory\repos.json"
if (-not (Test-Path $inventoryPath)) {
    Write-Error "Inventory not found at $inventoryPath. Run Audit-AIConfigs.ps1 first."
    return
}

$inventory = Get-Content $inventoryPath -Raw | ConvertFrom-Json

# ---------- Filter target repos ----------
$targets = @()

foreach ($repo in $inventory.repos) {
    if ($RepoName -and $repo.name -eq $RepoName) {
        $targets += $repo
    }
    elseif ($RepoType -and $repo.type -eq $RepoType) {
        $targets += $repo
    }
    elseif ($All) {
        $targets += $repo
    }
}

if ($targets.Count -eq 0) {
    Write-Host "No repos matched the filter criteria." -ForegroundColor Yellow
    return
}

Write-Host "Targets: $($targets.Count) repo(s)" -ForegroundColor Cyan

# ---------- Template paths ----------
$templateRoot = Join-Path $PSScriptRoot "..\templates"
$basePath      = Join-Path $templateRoot "base"

# ---------- Helper: read a local template file ----------
function Get-TemplateContent {
    param([string]$FilePath)
    if (Test-Path $FilePath) {
        return Get-Content $FilePath -Raw
    }
    return $null
}

# ---------- Helper: collect files from a directory recursively ----------
function Get-TemplateFiles {
    param([string]$Dir)
    $files = @{}
    if (Test-Path $Dir) {
        $resolved = (Resolve-Path $Dir).Path.TrimEnd('\', '/')
        Get-ChildItem $resolved -Recurse -File | ForEach-Object {
            $full = $_.FullName
            if ($full.Length -gt ($resolved.Length + 1)) {
                $relative = $full.Substring($resolved.Length + 1) -replace '\\', '/'
            } else {
                $relative = $_.Name
            }
            $files[$relative] = Get-Content $_.FullName -Raw -Encoding UTF8
        }
    }
    return $files
}

# ---------- Helper: replace template variables ----------
function Resolve-TemplateVars {
    param(
        [string]$Content,
        [PSCustomObject]$Repo
    )

    $result = $Content
    $result = $result -replace '\{\{REPO_NAME\}\}',   $Repo.name
    $domain = if ($Repo.domain) { $Repo.domain } else { "" }
    $domainSuffix = if ($Repo.domain) { " for $($Repo.domain)" } else { "" }
    $base   = if ($Repo.basePath) { $Repo.basePath } else { "/$($Repo.name)" }
    $result = $result -replace '\{\{DOMAIN_SUFFIX\}\}', $domainSuffix
    $result = $result -replace '\{\{DOMAIN_NAME\}\}',   $domain
    $result = $result -replace '\{\{BASE_PATH\}\}',     $base

    # Trim trailing whitespace per line (prevents Prettier failures when variables resolve to empty)
    $lines = $result -split "`n" | ForEach-Object { $_.TrimEnd() }
    # Remove trailing blank lines, then ensure exactly one trailing newline
    while ($lines.Count -gt 0 -and $lines[-1] -eq '') { $lines = $lines[0..($lines.Count - 2)] }
    $result = ($lines -join "`n") + "`n"

    return $result
}

# ---------- Helper: create or update a file via GitHub API ----------
function Set-RepoFile {
    param(
        [string]$Org,
        [string]$RepoName,
        [string]$Branch,
        [string]$FilePath,
        [string]$Content,
        [string]$Message
    )

    # Check if file already exists on the branch
    $existingSha = $null
    try {
        $existing = gh api "repos/$Org/$RepoName/contents/$FilePath`?ref=$Branch" 2>&1
        if ($LASTEXITCODE -eq 0) {
            $parsed = $existing | ConvertFrom-Json -ErrorAction SilentlyContinue
            $existingSha = $parsed.sha
        }
    }
    catch { }

    # Normalize line endings to LF and base64-encode
    $normalized = $Content -replace "`r`n", "`n"
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($normalized)
    $base64 = [Convert]::ToBase64String($bytes)

    $body = @{
        message = $Message
        content = $base64
        branch  = $Branch
    }

    if ($existingSha) {
        $body.sha = $existingSha
    }

    $bodyJson = $body | ConvertTo-Json -Compress
    $tmpFile = [System.IO.Path]::GetTempFileName()
    try {
        [System.IO.File]::WriteAllText($tmpFile, $bodyJson)
        gh api "repos/$Org/$RepoName/contents/$FilePath" --method PUT --input $tmpFile 2>&1 | Out-Null

        if ($LASTEXITCODE -eq 0) {
            Write-Host "    Updated: $FilePath" -ForegroundColor Green
        }
        else {
            Write-Host "    FAILED:  $FilePath" -ForegroundColor Red
        }
    }
    finally {
        Remove-Item $tmpFile -ErrorAction SilentlyContinue
    }
}

# ---------- Process each target repo ----------
foreach ($repo in $targets) {
    $org  = if ($repo.org) { $repo.org } else { $Organization }
    $name = $repo.name
    $type = if ($repo.type) { $repo.type } else { Get-RepoType -RepoName $name }

    Write-Host "`nProcessing $org/$name (type: $type) ..." -ForegroundColor Cyan

    # 1. Collect base template files
    $files = Get-TemplateFiles -Dir $basePath

    # 2. Collect overlay files (overwrite or add to base set)
    $overlayDir = Join-Path (Join-Path $templateRoot "overlays") $type
    if (Test-Path $overlayDir) {
        $overlayFiles = Get-TemplateFiles -Dir $overlayDir
        foreach ($key in $overlayFiles.Keys) {
            if ($key -like "*.patch") {
                # .patch files get appended to the corresponding base file
                $baseKey = $key -replace '\.patch$', ''
                if ($files.ContainsKey($baseKey)) {
                    $files[$baseKey] += "`n" + $overlayFiles[$key]
                }
                else {
                    # No base file to patch; create as new file without .patch extension
                    $files[$baseKey] = $overlayFiles[$key]
                }
            }
            else {
                $files[$key] = $overlayFiles[$key]
            }
        }
    }

    # 3. Apply template variable replacement
    $resolvedFiles = @{}
    foreach ($key in $files.Keys) {
        $resolvedFiles[$key] = Resolve-TemplateVars -Content $files[$key] -Repo $repo
    }

    # 4. Dry-run mode: just print
    if ($DryRun) {
        Write-Host "  [DRY RUN] Would sync $($resolvedFiles.Count) file(s):" -ForegroundColor Yellow
        foreach ($f in $resolvedFiles.Keys | Sort-Object) {
            $preview = $resolvedFiles[$f].Substring(0, [Math]::Min(80, $resolvedFiles[$f].Length)) -replace "`n", " "
            Write-Host "    $f  ($($resolvedFiles[$f].Length) bytes)  $preview..." -ForegroundColor Gray
        }
        continue
    }

    # 5. Create sync branch from default branch
    $branchName = "chore/sync-ai-configs"

    # Get default branch ref
    try {
        $defaultRef = gh api "repos/$org/$name/git/ref/heads/main" 2>&1 | ConvertFrom-Json
        $baseSha = $defaultRef.object.sha
    }
    catch {
        Write-Host "  Could not find default branch for $org/$name. Skipping." -ForegroundColor Yellow
        continue
    }

    # Create or reset the sync branch
    $branchTmp = [System.IO.Path]::GetTempFileName()
    try {
        $createJson = @{ ref = "refs/heads/$branchName"; sha = $baseSha } | ConvertTo-Json -Compress
        [System.IO.File]::WriteAllText($branchTmp, $createJson)
        gh api "repos/$org/$name/git/refs" --method POST --input $branchTmp 2>&1 | Out-Null

        if ($LASTEXITCODE -eq 0) {
            Write-Host "  Created branch: $branchName" -ForegroundColor Green
        }
        else {
            # Branch may already exist; reset it
            $resetJson = @{ sha = $baseSha; force = $true } | ConvertTo-Json -Compress
            [System.IO.File]::WriteAllText($branchTmp, $resetJson)
            gh api "repos/$org/$name/git/refs/heads/$branchName" --method PATCH --input $branchTmp 2>&1 | Out-Null

            if ($LASTEXITCODE -eq 0) {
                Write-Host "  Reset branch: $branchName" -ForegroundColor Yellow
            }
            else {
                Write-Host "  WARNING: Could not create/reset branch. Skipping $name." -ForegroundColor Red
                continue
            }
        }
    }
    finally {
        Remove-Item $branchTmp -ErrorAction SilentlyContinue
    }

    # 6. Push each file
    foreach ($filePath in $resolvedFiles.Keys | Sort-Object) {
        Set-RepoFile -Org $org -RepoName $name -Branch $branchName `
            -FilePath $filePath -Content $resolvedFiles[$filePath] `
            -Message "chore: sync AI config - $filePath"
    }

    # 7. Create pull request
    Write-Host "  Creating pull request ..." -ForegroundColor Cyan
    $prBody = @"
## AI Config Sync

Automated sync of AI configuration files from **FFC-IN-AI-Management** templates.

### Files updated
$( ($resolvedFiles.Keys | Sort-Object | ForEach-Object { "- ``$_``" }) -join "`n" )

### Template type
- Base: ``templates/base/``
- Overlay: ``templates/overlays/$type/``

---
*Created by ``scripts/Sync-AIConfigs.ps1``*
"@

    gh pr create --repo "$org/$name" `
        --head $branchName `
        --base main `
        --title "chore: sync AI configuration files" `
        --body $prBody 2>&1 | ForEach-Object {
        Write-Host "  $_" -ForegroundColor Green
    }
}

Write-Host "`nSync complete." -ForegroundColor Cyan
