<#
.SYNOPSIS
    Audit the GitHub security & analysis settings of one or more FFC repos.

.DESCRIPTION
    Reads each repo's '.security_and_analysis' block via 'gh api' and reports
    whether expected features are enabled. Used to verify D4 (#68) state on
    CBMadmin, and as a periodic audit hook across FFC repos.

    Default expected baseline (every feature should be 'enabled'):
      secret_scanning
      secret_scanning_push_protection
      dependabot_security_updates

    'code_security' is reported but not asserted because it requires GHAS on
    private repos (see docs/decisions/D5-ghas-pricing-decision.md). On orgs
    with the GitHub for Nonprofits Teams plan it may show 'enabled' without
    a separate GHAS purchase; on other tiers it may not be available.

    Exit code 0 = every checked feature on every repo is enabled.
    Exit code 1 = at least one expected feature is not enabled.

.PARAMETER Owner
    GitHub org / user that owns the repo(s). Default: FreeForCharity.

.PARAMETER Repo
    One or more repo names. If omitted, audits all repos in the owner org.

.PARAMETER Expected
    Features expected to be enabled. Default set above. Pass an empty array
    to report-only without assertion.

.EXAMPLE
    # Audit CBMadmin (the D4 use case)
    pwsh -File scripts/github/Test-RepoSecuritySettings.ps1 -Repo FFC-IN-ClarkeMoyerAdmin

.EXAMPLE
    # Audit every repo in the org (slower; rate-limit aware)
    pwsh -File scripts/github/Test-RepoSecuritySettings.ps1

.EXAMPLE
    # Report-only, no assertion failures
    pwsh -File scripts/github/Test-RepoSecuritySettings.ps1 -Repo FFC-IN-ClarkeMoyerAdmin -Expected @()
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [ValidateNotNullOrEmpty()]
    [string]$Owner = 'FreeForCharity',

    [Parameter(Mandatory = $false)]
    [string[]]$Repo,

    [Parameter(Mandatory = $false)]
    [string[]]$Expected = @(
        'secret_scanning',
        'secret_scanning_push_protection',
        'dependabot_security_updates'
    )
)

$ErrorActionPreference = 'Stop'

function Test-GhCli {
    if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
        throw "GitHub CLI (gh) not found. Install gh and run 'gh auth login' before using this script."
    }
    $null = gh auth status 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "gh is not authenticated. Run: gh auth login"
    }
}

Test-GhCli

# Resolve repo list. If -Repo not passed, list all NON-ARCHIVED, NON-FORK
# repos in the owner org. Forks and archived repos are excluded because
# the security baseline doesn't apply to them — archived repos are
# intentionally frozen, and forks inherit settings from their source.
# Limit raised to 1000 (FFC has ~30 repos today; 1000 leaves comfortable
# headroom and is well under GitHub's per-call max).
if (-not $Repo -or $Repo.Count -eq 0) {
    Write-Host "Listing all non-archived, non-fork repos owned by $Owner..." -ForegroundColor Cyan
    # gh CLI emits CRLF on Windows. Split on either CR+LF or bare LF (regex),
    # then Trim() each entry to strip any trailing whitespace that survives.
    # Without this, the trailing \r would end up in the URL of the next
    # `gh api repos/$Owner/$r` call and every per-repo lookup would 404.
    $Repo = (gh repo list $Owner --no-archived --source --limit 1000 --json name --jq '.[].name') -split "`r?`n" |
        ForEach-Object { $_.Trim() } |
        Where-Object { $_ }
    Write-Host "Found $($Repo.Count) repo(s)." -ForegroundColor Cyan
}

$findings = @()
$failures = 0

foreach ($r in $Repo) {
    Write-Host "`n--- $Owner/$r ---" -ForegroundColor Cyan
    $sa = gh api "repos/$Owner/$r" --jq '.security_and_analysis' 2>$null
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($sa)) {
        Write-Host "  ERROR: could not read security_and_analysis (gh exit $LASTEXITCODE)" -ForegroundColor Red
        $findings += [pscustomobject]@{
            Repo     = $r
            Feature  = '<read>'
            Status   = 'error'
            Expected = 'enabled'
            Pass     = $false
        }
        $failures++
        continue
    }

    # `gh api ... --jq '.security_and_analysis'` returns the literal string
    # 'null' (not empty) when the field is absent. Happens for public repos
    # where the caller lacks admin scope, or when the field simply isn't
    # populated. Without this check the script would silently report 0
    # findings and mark the repo as "passed."
    if ($sa.Trim() -eq 'null') {
        Write-Host "  ERROR: security_and_analysis block is null - caller may lack admin scope, or the repo has no security settings at all. Cannot verify baseline." -ForegroundColor Red
        $findings += [pscustomobject]@{
            Repo     = $r
            Feature  = '<security_and_analysis>'
            Status   = 'null'
            Expected = 'enabled'
            Pass     = $false
        }
        $failures++
        continue
    }

    $obj = $sa | ConvertFrom-Json

    # Iterate the known feature keys present in security_and_analysis.
    $featureNames = $obj.PSObject.Properties.Name | Sort-Object
    foreach ($f in $featureNames) {
        $status = $obj.$f.status
        $isExpected = $Expected -contains $f
        $pass = (-not $isExpected) -or ($status -eq 'enabled')
        $marker = if ($pass) { '  OK ' } else { '  !! ' }
        $expectedLabel = if ($isExpected) { 'expected:enabled' } else { 'optional' }
        Write-Host ("{0}{1,-50} {2,-10} [{3}]" -f $marker, $f, $status, $expectedLabel) `
            -ForegroundColor $(if ($pass) { 'Green' } else { 'Red' })

        $findings += [pscustomobject]@{
            Repo     = $r
            Feature  = $f
            Status   = $status
            Expected = if ($isExpected) { 'enabled' } else { 'optional' }
            Pass     = $pass
        }
        if (-not $pass) { $failures++ }
    }
}

Write-Host "`n=== Summary ===" -ForegroundColor Cyan
Write-Host ("Repos checked: {0}" -f $Repo.Count)
Write-Host ("Findings:      {0}" -f $findings.Count)
Write-Host ("Failures:      {0}" -f $failures) -ForegroundColor $(if ($failures -gt 0) { 'Red' } else { 'Green' })

if ($failures -gt 0) {
    Write-Host "`nFailing items:" -ForegroundColor Red
    $findings | Where-Object { -not $_.Pass } | Format-Table -AutoSize | Out-String | Write-Host
    exit 1
}
