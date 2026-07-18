#Requires -Version 5.1
<#
.SYNOPSIS
    Opt in to the FFC shared git hooks (secret / sensitive-file pre-commit scan).
.DESCRIPTION
    Sets core.hooksPath to .githooks for this clone. Safe to re-run.
    Undo with: git config --unset core.hooksPath
#>
$ErrorActionPreference = 'Stop'

$root = (git rev-parse --show-toplevel).Trim()
git -C $root config core.hooksPath .githooks
Write-Host "✓ Enabled FFC git hooks (core.hooksPath=.githooks)." -ForegroundColor Green
Write-Host "  Pre-commit scans staged changes for secrets and sensitive files."
Write-Host "  Bypass a confirmed false positive with: git commit --no-verify"
