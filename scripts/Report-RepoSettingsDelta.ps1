[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$TemplateRepo,

    [Parameter(Mandatory = $true)]
    [string]$TargetRepo,

    [Parameter()]
    [string]$OutputFile = '_run_artifacts/repo_settings_report.md'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Require-Command {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command not found on PATH: $Name"
    }
}

function Try-GhApiJson {
    param([string]$Path)
    try {
        return (gh api $Path | ConvertFrom-Json)
    }
    catch {
        return [PSCustomObject]@{ error = $_.Exception.Message }
    }
}

function Pages-Summary {
    param([object]$Pages)
    if ($null -eq $Pages) { return [PSCustomObject]@{ enabled = $false } }
    if ($Pages.PSObject.Properties.Name -contains 'error') { return [PSCustomObject]@{ error = $Pages.error } }

    return [PSCustomObject]@{
        html_url       = $Pages.html_url
        cname          = $Pages.cname
        status         = $Pages.status
        https_enforced = $Pages.https_enforced
        build_type     = $Pages.build_type
    }
}

Require-Command -Name 'gh'

$tplRepo = Try-GhApiJson -Path "repos/$TemplateRepo"
$tgtRepo = Try-GhApiJson -Path "repos/$TargetRepo"

$tplPages = Try-GhApiJson -Path "repos/$TemplateRepo/pages"
$tgtPages = Try-GhApiJson -Path "repos/$TargetRepo/pages"

$tplRulesets = Try-GhApiJson -Path "repos/$TemplateRepo/rulesets?per_page=100"
$tgtRulesets = Try-GhApiJson -Path "repos/$TargetRepo/rulesets?per_page=100"
$orgRulesets = Try-GhApiJson -Path "orgs/FreeForCharity/rulesets?per_page=100"

$repoFields = @(
    'default_branch',
    'visibility',
    'private',
    'has_issues',
    'has_projects',
    'has_wiki',
    'delete_branch_on_merge',
    'allow_squash_merge',
    'allow_merge_commit',
    'allow_rebase_merge',
    'allow_auto_merge',
    'web_commit_signoff_required'
)

$diff = New-Object System.Collections.Generic.List[object]
foreach ($f in $repoFields) {
    $a = $tplRepo.$f
    $b = $tgtRepo.$f
    if ("$a" -ne "$b") {
        $diff.Add([PSCustomObject]@{ area = 'repo'; setting = $f; template = $a; target = $b })
    }
}

$tplPagesSum = Pages-Summary -Pages $tplPages
$tgtPagesSum = Pages-Summary -Pages $tgtPages
foreach ($p in $tplPagesSum.PSObject.Properties.Name) {
    $a = $tplPagesSum.$p
    $b = $tgtPagesSum.$p
    if ("$a" -ne "$b") {
        $diff.Add([PSCustomObject]@{ area = 'pages'; setting = $p; template = $a; target = $b })
    }
}

function Ruleset-Lines {
    param([object]$Rulesets)
    if ($null -eq $Rulesets) { return @() }
    if ($Rulesets.PSObject.Properties.Name -contains 'error') { return @("ERROR: $($Rulesets.error)") }
    return @($Rulesets | ForEach-Object { "[$($_.id)] $($_.name) (source_type=$($_.source_type), target=$($_.target), enforcement=$($_.enforcement))" })
}

$tplRepoScopedNames = @()
$tgtRepoScopedNames = @()
if ($tplRulesets -and -not ($tplRulesets.PSObject.Properties.Name -contains 'error')) {
    $tplRepoScopedNames = @($tplRulesets | Where-Object { $_.source_type -eq 'Repository' } | Select-Object -ExpandProperty name)
}
if ($tgtRulesets -and -not ($tgtRulesets.PSObject.Properties.Name -contains 'error')) {
    $tgtRepoScopedNames = @($tgtRulesets | Where-Object { $_.source_type -eq 'Repository' } | Select-Object -ExpandProperty name)
}

$missingRepoRulesets = @($tplRepoScopedNames | Where-Object { $tgtRepoScopedNames -notcontains $_ })
$extraRepoRulesets = @($tgtRepoScopedNames | Where-Object { $tplRepoScopedNames -notcontains $_ })

$outDir = Split-Path -Parent $OutputFile
if (-not [string]::IsNullOrWhiteSpace($outDir)) {
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null
}

$lines = New-Object System.Collections.Generic.List[string]
$lines.Add('# Full settings report (template vs target repo)')
$lines.Add('')
$lines.Add("Template: $TemplateRepo")
$lines.Add("Target: $TargetRepo")
$lines.Add("Generated: $((Get-Date).ToUniversalTime().ToString('o'))")
$lines.Add('')

$lines.Add('## Repo settings')
foreach ($f in $repoFields) {
    $lines.Add('- `' + $f + '`: template=' + $tplRepo.$f + ' | target=' + $tgtRepo.$f)
}
$lines.Add('')

$lines.Add('## Pages')
$lines.Add("- Template: $($tplPagesSum | ConvertTo-Json -Compress)")
$lines.Add("- Target: $($tgtPagesSum | ConvertTo-Json -Compress)")
$lines.Add('')

$lines.Add('## Rulesets')
$lines.Add('### Org rulesets (apply broadly)')
foreach ($l in (Ruleset-Lines -Rulesets $orgRulesets)) { $lines.Add("- $l") }
$lines.Add('')

$lines.Add('### Template repo rulesets')
foreach ($l in (Ruleset-Lines -Rulesets $tplRulesets)) { $lines.Add("- $l") }
$lines.Add('')

$lines.Add('### Target repo rulesets')
foreach ($l in (Ruleset-Lines -Rulesets $tgtRulesets)) { $lines.Add("- $l") }
$lines.Add('')

$lines.Add('### Repo-scoped ruleset delta (by name)')
$lines.Add("- Missing in target: $([string]::Join(', ', $missingRepoRulesets))")
$lines.Add("- Extra in target: $([string]::Join(', ', $extraRepoRulesets))")
$lines.Add('')

$lines.Add('### Protect Main rules (detail)')
if ($tplRulesets -and $tgtRulesets -and -not ($tplRulesets.PSObject.Properties.Name -contains 'error') -and -not ($tgtRulesets.PSObject.Properties.Name -contains 'error')) {
    $tplProtect = @($tplRulesets | Where-Object { $_.source_type -eq 'Repository' -and $_.name -eq 'Protect Main' })
    $tgtProtect = @($tgtRulesets | Where-Object { $_.source_type -eq 'Repository' -and $_.name -eq 'Protect Main' })

    if ($tplProtect.Count -gt 0 -and $tgtProtect.Count -gt 0) {
        $tplProtectFull = Try-GhApiJson -Path "repos/$TemplateRepo/rulesets/$($tplProtect[0].id)"
        $tgtProtectFull = Try-GhApiJson -Path "repos/$TargetRepo/rulesets/$($tgtProtect[0].id)"

        $tplTypes = @($tplProtectFull.rules | ForEach-Object { $_.type } | Sort-Object -Unique)
        $tgtTypes = @($tgtProtectFull.rules | ForEach-Object { $_.type } | Sort-Object -Unique)

        $missingTypes = @($tplTypes | Where-Object { $tgtTypes -notcontains $_ })
        $extraTypes = @($tgtTypes | Where-Object { $tplTypes -notcontains $_ })

        $lines.Add("- Template rule types: $([string]::Join(', ', $tplTypes))")
        $lines.Add("- Target rule types: $([string]::Join(', ', $tgtTypes))")
        $lines.Add("- Missing in target: $([string]::Join(', ', $missingTypes))")
        $lines.Add("- Extra in target: $([string]::Join(', ', $extraTypes))")
    }
    else {
        $lines.Add('- Protect Main ruleset not found on both repos (or not repo-scoped).')
    }
}
else {
    $lines.Add('- Could not compare Protect Main ruleset details (rulesets API unavailable).')
}
$lines.Add('')

$lines.Add('## Copilot')
$lines.Add('Repo-level Copilot settings are not readable via the probed REST endpoints (returned 404).')
$lines.Add('For this project, Copilot review enforcement is implemented via a **repo-scoped ruleset** (expected name: "Copilot Review - All Branches") targeting `refs/heads/*` with the `copilot_code_review` rule enabled.')
$lines.Add('')

$lines.Add('## Focused delta summary')
if ($diff.Count -eq 0) {
    $lines.Add('No differences detected in the tracked fields.')
}
else {
    foreach ($d in ($diff | Sort-Object area, setting)) {
        $lines.Add("- **$($d.area)** $($d.setting): template=$($d.template) -> target=$($d.target)")
    }
}

$lines | Set-Content -Encoding utf8 $OutputFile
Write-Host "Wrote: $OutputFile" -ForegroundColor Green
