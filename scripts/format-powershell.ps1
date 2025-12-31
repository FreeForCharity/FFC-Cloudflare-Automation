$ErrorActionPreference = 'Stop'

Set-PSRepository -Name PSGallery -InstallationPolicy Trusted
if (-not (Get-Module -ListAvailable PSScriptAnalyzer)) {
    Install-Module PSScriptAnalyzer -Scope CurrentUser -Force -AllowClobber
}

Import-Module PSScriptAnalyzer

$files = Get-ChildItem -Path $PSScriptRoot\.. -Filter '*.ps1' -File -Recurse |
    Where-Object { $_.FullName -notmatch '\\node_modules\\|\\\.git\\|\\\.venv\\' }

$changed = 0
foreach ($file in $files) {
    $original = Get-Content -Raw -Path $file.FullName
    $formatted = Invoke-Formatter -ScriptDefinition $original

    if ($formatted -ne $original) {
        Set-Content -Path $file.FullName -Value $formatted -NoNewline
        $changed++
    }
}

Write-Host "Formatted $changed PowerShell file(s)."
