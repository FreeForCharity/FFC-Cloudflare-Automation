<#
.SYNOPSIS
    Installs FFC managed settings for Claude Code to C:\Program Files\ClaudeCode\.
.DESCRIPTION
    Copies managed/CLAUDE.md and managed/managed-settings.json to the system-wide
    Claude Code configuration directory. Requires elevated (admin) permissions.
#>

$targetDir = "C:\Program Files\ClaudeCode"
$sourceDir = Join-Path $PSScriptRoot "..\managed"

try {
    if (-not (Test-Path $targetDir)) {
        New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
        Write-Host "Created directory: $targetDir" -ForegroundColor Green
    }

    Copy-Item (Join-Path $sourceDir "CLAUDE.md") (Join-Path $targetDir "CLAUDE.md") -Force
    Write-Host "Installed: CLAUDE.md" -ForegroundColor Green

    Copy-Item (Join-Path $sourceDir "managed-settings.json") (Join-Path $targetDir "managed-settings.json") -Force
    Write-Host "Installed: managed-settings.json" -ForegroundColor Green

    Write-Host "`nManaged settings installed successfully to $targetDir" -ForegroundColor Cyan
    Get-ChildItem $targetDir | Format-Table Name, Length, LastWriteTime -AutoSize
}
catch {
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "This script requires elevated (admin) permissions." -ForegroundColor Yellow
    Write-Host "Run: Start-Process powershell -Verb RunAs -ArgumentList '-File', '$($MyInvocation.MyCommand.Path)'" -ForegroundColor Yellow
    exit 1
}
