<#
.SYNOPSIS
    Audits AI configuration files across all FFC repositories.

.DESCRIPTION
    Scans every repo in the specified GitHub organizations for AI-related
    configuration files (CLAUDE.md, AGENTS.md, GEMINI.md, copilot-instructions,
    .claude/settings.json, etc.) and generates an inventory and audit report.

.PARAMETER Organizations
    GitHub organizations to scan. Defaults to FreeForCharity and
    koenig-childhood-cancer-foundation.

.PARAMETER OutputPath
    Directory for output files (repos.json, audit-report.md).
    Defaults to "inventory".

.EXAMPLE
    .\Audit-AIConfigs.ps1
    .\Audit-AIConfigs.ps1 -Organizations @("FreeForCharity") -OutputPath "output"
#>

[CmdletBinding()]
param(
    [string[]]$Organizations = @("FreeForCharity", "koenig-childhood-cancer-foundation"),
    [string]$OutputPath = "inventory"
)

# Import the repo-type helper
. "$PSScriptRoot\Get-RepoType.ps1"

# Files and directories to check in each repo
$aiConfigFiles = @(
    @{ Path = "CLAUDE.md";                              Type = "file" },
    @{ Path = "AGENTS.md";                              Type = "file" },
    @{ Path = "GEMINI.md";                              Type = "file" },
    @{ Path = ".github/copilot-instructions.md";        Type = "file" },
    @{ Path = ".claude/settings.json";                  Type = "file" },
    @{ Path = ".claude/rules";                          Type = "dir"  },
    @{ Path = ".claude/agents";                         Type = "dir"  },
    @{ Path = ".copilot/mcp-config.json";               Type = "file" },
    @{ Path = ".github/agents/AI_AGENT_INSTRUCTIONS.md"; Type = "file" }
)

# Ensure output directory exists
if (-not (Test-Path $OutputPath)) {
    New-Item -ItemType Directory -Path $OutputPath -Force | Out-Null
}

# ---------- Collect repos from each org ----------
$allRepos = @()

foreach ($org in $Organizations) {
    Write-Host "Listing repos for org: $org ..." -ForegroundColor Cyan

    $repoJson = gh repo list $org --limit 100 --json name 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  WARNING: Could not list repos for $org - $repoJson" -ForegroundColor Yellow
        continue
    }

    $repos = $repoJson | ConvertFrom-Json

    foreach ($repo in $repos) {
        $repoName = $repo.name
        $repoType = Get-RepoType -RepoName $repoName

        # Derive domain from EX repo names (e.g. FFC-EX-legioninthewoods.org -> legioninthewoods.org)
        $domain = $null
        if ($repoName -like "FFC-EX-*") {
            $domain = $repoName -replace "^FFC-EX-", ""
        }

        $basePath = "/$repoName"

        Write-Host "  Auditing $org/$repoName ($repoType) ..." -ForegroundColor Gray

        # Check each AI config file/directory
        $aiConfigs = @{}

        foreach ($config in $aiConfigFiles) {
            $filePath = $config.Path
            $fileType = $config.Type

            try {
                $response = gh api "repos/$org/$repoName/contents/$filePath" 2>&1

                if ($LASTEXITCODE -eq 0) {
                    $parsed = $response | ConvertFrom-Json -ErrorAction SilentlyContinue

                    if ($fileType -eq "dir") {
                        # For directories, the API returns an array of entries
                        if ($parsed -is [System.Array]) {
                            $aiConfigs[$filePath] = @{
                                present = $true
                                type    = "dir"
                                count   = $parsed.Count
                            }
                        }
                        else {
                            $aiConfigs[$filePath] = @{ present = $true; type = "dir" }
                        }
                    }
                    else {
                        $aiConfigs[$filePath] = @{ present = $true; type = "file" }
                    }
                }
                else {
                    $aiConfigs[$filePath] = @{ present = $false }
                }
            }
            catch {
                $aiConfigs[$filePath] = @{ present = $false }
            }
        }

        $allRepos += @{
            org        = $org
            name       = $repoName
            type       = $repoType
            domain     = $domain
            basePath   = $basePath
            aiConfigs  = $aiConfigs
            lastSync   = $null
        }
    }
}

# ---------- Build inventory object ----------
$inventory = @{
    lastAudit     = (Get-Date -Format "o")
    organizations = $Organizations
    repos         = $allRepos
}

# ---------- Save repos.json ----------
$jsonPath = Join-Path $OutputPath "repos.json"
$inventory | ConvertTo-Json -Depth 6 | Set-Content -Path $jsonPath -Encoding UTF8
Write-Host "`nInventory saved to $jsonPath" -ForegroundColor Green

# ---------- Generate audit-report.md ----------
$reportPath = Join-Path $OutputPath "audit-report.md"

$sb = [System.Text.StringBuilder]::new()
[void]$sb.AppendLine("# AI Configuration Audit Report")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("Generated: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## Summary")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("| Org | Repo | Type | CLAUDE.md | AGENTS.md | GEMINI.md | copilot-instructions | .claude/settings | .claude/rules | .claude/agents | mcp-config | AI_AGENT_INSTRUCTIONS |")
[void]$sb.AppendLine("|-----|------|------|-----------|-----------|-----------|---------------------|-----------------|---------------|----------------|------------|----------------------|")

foreach ($repo in $allRepos) {
    $cols = @(
        $repo.org,
        $repo.name,
        $repo.type
    )

    foreach ($config in $aiConfigFiles) {
        $p = $config.Path
        if ($repo.aiConfigs[$p] -and $repo.aiConfigs[$p].present) {
            $cols += "Yes"
        }
        else {
            $cols += "-"
        }
    }

    [void]$sb.AppendLine("| $($cols -join ' | ') |")
}

[void]$sb.AppendLine("")
[void]$sb.AppendLine("## Legend")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("- **Yes**: File or directory is present in the repo")
[void]$sb.AppendLine("- **-**: Not present")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("---")
[void]$sb.AppendLine("*Report generated by ``scripts/Audit-AIConfigs.ps1``*")

$sb.ToString() | Set-Content -Path $reportPath -Encoding UTF8
Write-Host "Audit report saved to $reportPath" -ForegroundColor Green
Write-Host "`nAudit complete. $($allRepos.Count) repos scanned." -ForegroundColor Cyan
