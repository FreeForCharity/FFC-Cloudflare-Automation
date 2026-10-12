<#
.SYNOPSIS
    Apply the FraudLabs Pro screening results to the unscreened charity-lane
    verdicts and write the final report (no API calls at all).

.DESCRIPTION
    The `finalize` job of workflow 232 (epic #1618, item G1.3b). Inputs are two
    PII-free files: the unscreened report the WHMCS job wrote (every row
    FRAUD_CHECK_NOT_RUN) and the screening job's results. Each row goes through
    Merge-LaneFraudResultSet (scripts/whmcs-charity-lane-verdicts.ps1):

      APPROVE (any score)            -> fraud_check pass, verdict = the checks verdict
      REJECT                         -> ESCALATE FRAUD_FLAGGED
      REVIEW                         -> ESCALATE FRAUD_REVIEW
      anything else / no result      -> ESCALATE FRAUD_CHECK_FAILED

    Fail closed: when -FraudResultsFile is empty, missing or unreadable (the
    screening job failed or was skipped), every row is FRAUD_CHECK_FAILED. So
    no run of 232 can ACCEPT an order FraudLabs did not clear.

    Writes the final charity-lane-verdicts.json + .md and the job summary
    table. Output fields are the report-row fields only.
#>
[CmdletBinding()]
param(
    [Parameter()]
    [string]$VerdictsFile = 'artifacts/whmcs/charity-lane-verdicts-unscreened.json',

    # Empty, missing or unreadable -> every row FRAUD_CHECK_FAILED.
    [Parameter()]
    [string]$FraudResultsFile = '',

    [Parameter()]
    [string]$OutputFile = 'artifacts/whmcs/charity-lane-verdicts.json',

    [Parameter()]
    [string]$MarkdownFile = 'artifacts/whmcs/charity-lane-verdicts.md'
)

$ErrorActionPreference = 'Stop'

# Capture this script's parameters BEFORE dot-sourcing the verdicts script: a
# dot-sourced script binds its own param() block in THIS scope, so its
# defaults for $OutputFile / $MarkdownFile would overwrite the values given here.
$script:MergeRunConfig = @{
    VerdictsFile     = $VerdictsFile
    FraudResultsFile = $FraudResultsFile
    OutputFile       = $OutputFile
    MarkdownFile     = $MarkdownFile
}

# Functions only: its runner is guarded by the dot-source check.
. (Join-Path $PSScriptRoot 'whmcs-charity-lane-verdicts.ps1')

try {
    $cfg = $script:MergeRunConfig
    if (-not (Test-Path -LiteralPath $cfg.VerdictsFile)) { throw "Unscreened verdicts file not found: $($cfg.VerdictsFile)" }
    $unscreened = Get-Content -LiteralPath $cfg.VerdictsFile -Raw | ConvertFrom-Json
    if ([string]$unscreened.schema -ne 'charity-lane-verdicts/v2' -or [string]$unscreened.fraud_check -ne 'not_run') {
        throw "Unexpected input: schema '$($unscreened.schema)', fraud_check '$($unscreened.fraud_check)' (want charity-lane-verdicts/v2, not_run)."
    }
    $rows = @($unscreened.verdicts | Where-Object { $_ })

    $results = $null
    if ([string]::IsNullOrWhiteSpace($cfg.FraudResultsFile)) {
        Write-Output '::warning::No FraudLabs results were handed over; every row is ESCALATE FRAUD_CHECK_FAILED.'
    }
    elseif (-not (Test-Path -LiteralPath $cfg.FraudResultsFile)) {
        Write-Output "::warning::FraudLabs results file not found; every row is ESCALATE FRAUD_CHECK_FAILED."
    }
    else {
        try {
            $text = Get-Content -LiteralPath $cfg.FraudResultsFile -Raw
            # Assigned in each branch, not as `$results = if ...`: an if-expression
            # unrolls an empty array to $null.
            $results = @()
            if (-not [string]::IsNullOrWhiteSpace($text)) { $results = @($text | ConvertFrom-Json) }
        }
        catch {
            $results = $null
            Write-Output '::warning::FraudLabs results file is unreadable; every row is ESCALATE FRAUD_CHECK_FAILED.'
        }
    }

    $screened = Merge-LaneFraudResultSet -Rows $rows -Results $results
    $report = Get-LaneVerdictReport -Rows @($screened) -RunUrl ([string]$unscreened.run_url) `
        -GeneratedAt ((Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')) -FraudCheck 'screened'
    $markdown = Format-LaneVerdictMarkdown -Report $report

    foreach ($path in @($cfg.OutputFile, $cfg.MarkdownFile)) {
        $dir = Split-Path -Parent $path
        if (-not [string]::IsNullOrWhiteSpace($dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    }
    $report | ConvertTo-Json -Depth 8 | Out-File -FilePath $cfg.OutputFile -Encoding utf8
    $markdown | Out-File -FilePath $cfg.MarkdownFile -Encoding utf8
    if (-not [string]::IsNullOrWhiteSpace($env:GITHUB_STEP_SUMMARY)) {
        $markdown | Out-File -FilePath $env:GITHUB_STEP_SUMMARY -Append -Encoding utf8
    }
    Write-Host $markdown
    Write-Host "Wrote $($cfg.OutputFile) ($(@($report.verdicts).Count) screened verdict(s))."
    exit 0
}
catch {
    Write-Output "::error::Charity-lane fraud merge failed: $($_.Exception.Message)"
    exit 1
}
