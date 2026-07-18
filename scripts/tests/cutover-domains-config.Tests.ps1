<#
.SYNOPSIS
    Pester (v5) tests for config/ffc-ex-cutover-domains.json -- the single
    source of truth for the FFC-EX fleet cutover default domain list.

.DESCRIPTION
    Workflows 120 (bulk cutover) and 121 (fleet preflight) used to carry four
    hardcoded copies of the same 13-domain default list, which drifted. The
    list now lives ONLY in config/ffc-ex-cutover-domains.json and the
    workflows read it at runtime. These tests keep it that way:

      - the config parses and holds a sane, deduped, lowercase domain list;
      - both workflows reference the config file;
      - neither workflow re-embeds any domain literal from the list (so a
        divergent hardcoded copy can never sneak back in).

    Run locally: Invoke-Pester -Path scripts/tests -Output Detailed
#>

BeforeAll {
    $script:RepoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
    $script:ConfigPath = Join-Path $script:RepoRoot 'config/ffc-ex-cutover-domains.json'
    $script:WorkflowPaths = @(
        (Join-Path $script:RepoRoot '.github/workflows/120-bulk-cutover-to-github-pages.yml'),
        (Join-Path $script:RepoRoot '.github/workflows/121-fleet-cutover-preflight.yml')
    )
}

Describe 'config/ffc-ex-cutover-domains.json' {
    It 'exists and parses as JSON' {
        Test-Path $script:ConfigPath | Should -BeTrue
        { Get-Content -Raw $script:ConfigPath | ConvertFrom-Json } | Should -Not -Throw
    }

    It 'holds a non-empty, deduped list of lowercase root domains' {
        $config = Get-Content -Raw $script:ConfigPath | ConvertFrom-Json
        $config.domains.Count | Should -BeGreaterThan 0
        foreach ($domain in $config.domains) {
            $domain | Should -Match '^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}$'
        }
        ($config.domains | Select-Object -Unique).Count | Should -Be $config.domains.Count
    }
}

Describe 'workflows 120/121 use the config as the single source of the list' {
    It 'workflow <_> reads config/ffc-ex-cutover-domains.json' -ForEach @(
        '120-bulk-cutover-to-github-pages.yml',
        '121-fleet-cutover-preflight.yml'
    ) {
        $path = Join-Path $script:RepoRoot ".github/workflows/$_"
        Get-Content -Raw $path | Should -Match 'config/ffc-ex-cutover-domains\.json'
    }

    It 'no workflow re-embeds a hardcoded copy of the fleet list' {
        $config = Get-Content -Raw $script:ConfigPath | ConvertFrom-Json
        foreach ($workflowPath in $script:WorkflowPaths) {
            $content = Get-Content -Raw $workflowPath
            foreach ($domain in $config.domains) {
                if ($content -match [regex]::Escape($domain)) {
                    # Fail with a pointed message naming the offender.
                    $leaf = Split-Path $workflowPath -Leaf
                    throw ("$leaf embeds the domain literal '$domain'. The fleet default list " +
                        'lives only in config/ffc-ex-cutover-domains.json -- read it at runtime ' +
                        'instead of re-embedding (that is how the 120/121 lists drifted).')
                }
            }
        }
    }
}
