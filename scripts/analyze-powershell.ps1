$ErrorActionPreference = 'Stop'

Import-Module PSScriptAnalyzer

$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

$results = Invoke-ScriptAnalyzer -Path $root -Recurse -Severity @('Error', 'Warning') |
    Where-Object { $_.ScriptPath -like '*.ps1' }
$errors = $results | Where-Object { $_.Severity -eq 'Error' }
$warnings = $results | Where-Object { $_.Severity -eq 'Warning' }

Write-Host ("Errors: {0}" -f ($errors | Measure-Object).Count)
Write-Host ("Warnings: {0}" -f ($warnings | Measure-Object).Count)

if ($errors) {
    $errors | Select-Object RuleName, Severity, Message, Line | Format-Table -AutoSize | Out-String | Write-Host
    exit 1
}

if ($warnings) {
    $warnings | Select-Object RuleName, Severity, Message, Line | Format-Table -AutoSize | Out-String | Write-Host
}
