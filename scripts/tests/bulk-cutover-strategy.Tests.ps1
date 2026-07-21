<#
.SYNOPSIS
    Pester (v5) tests for the CNAME-flip strategy selection in
    scripts/bulk-cutover-to-github-pages.ps1 (workflow 120).

.DESCRIPTION
    Workflow 120's cname-flip half chooses one of two strategies per FFC-EX
    repo (#767):

      - 'switch-style'  when the repo's deploy workflow declares a
        `custom_domain` input (basePath/CNAME are build-derived from the domain
        signal), so the flip sets the CUSTOM_DOMAIN repo variable and commits
        public/CNAME as the source of truth; and
      - 'legacy-commit' otherwise, flipping the committed public/CNAME directly.

    The decision is a pure function (Get-CnameFlipStrategy) so it can be tested
    with no GitHub API calls. The script guards its runner body with the
    dot-source idiom, so importing it here is side-effect free.

    Run locally: Invoke-Pester -Path scripts/tests -Output Detailed
#>

BeforeAll {
    $script:ScriptPath = Join-Path (Split-Path $PSScriptRoot -Parent) 'bulk-cutover-to-github-pages.ps1'
    . $script:ScriptPath
}

Describe 'Get-CnameFlipStrategy (workflow 120 cname-flip)' {
    It 'is defined after dot-sourcing (runner guard exposes the pure helper)' {
        Get-Command Get-CnameFlipStrategy -ErrorAction SilentlyContinue | Should -Not -BeNullOrEmpty
    }

    It 'selects switch-style when deploy.yml declares a custom_domain input' {
        $deploy = 'on: { workflow_dispatch: { inputs: { custom_domain: { type: string } } } }'
        Get-CnameFlipStrategy -DeployWorkflowContent $deploy | Should -Be 'switch-style'
    }

    It 'selects switch-style whenever the custom_domain signal appears anywhere' {
        Get-CnameFlipStrategy -DeployWorkflowContent 'jobs use the custom_domain input' |
            Should -Be 'switch-style'
    }

    It 'selects legacy-commit for a deploy.yml with no custom-domain signal' {
        $deploy = 'on: { push: { branches: [main] } }'
        Get-CnameFlipStrategy -DeployWorkflowContent $deploy | Should -Be 'legacy-commit'
    }

    It 'selects legacy-commit when the repo has no deploy.yml (null content)' {
        Get-CnameFlipStrategy -DeployWorkflowContent $null | Should -Be 'legacy-commit'
    }

    It 'selects legacy-commit for empty or whitespace-only content' {
        Get-CnameFlipStrategy -DeployWorkflowContent '' | Should -Be 'legacy-commit'
        Get-CnameFlipStrategy -DeployWorkflowContent "   `n  " | Should -Be 'legacy-commit'
    }
}

Describe 'workflow 120 cname-flip uses the pure strategy selector' {
    It 'the bulk-cutover script invokes Get-CnameFlipStrategy at a real call site (no drift from the tested logic)' {
        # Match the actual assignment (not a bare substring that a comment or
        # string literal could satisfy) so the test fails if the runner stops
        # calling the tested selector.
        Get-Content -Raw $script:ScriptPath |
            Should -Match '\$flipStrategy\s*=\s*Get-CnameFlipStrategy\s+-DeployWorkflowContent'
    }
}
