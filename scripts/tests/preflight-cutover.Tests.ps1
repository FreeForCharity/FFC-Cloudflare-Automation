<#
.SYNOPSIS
    Pester (v5) tests for scripts/preflight-cutover.mjs (workflow 121).

.DESCRIPTION
    The verdict / classification logic lives in the Node script itself and is
    exercised by its offline --self-test mode (host classification, CAA policy,
    default-origin derivation, domain-list parsing, and the four go/no-go
    verdict outcomes). No network calls are made. These tests also assert the
    usage-error contract so the workflow fails fast on bad inputs.

    Run locally: Invoke-Pester -Path scripts/tests -Output Detailed
#>

BeforeAll {
    $script:PreflightScript = Join-Path (Split-Path $PSScriptRoot -Parent) 'preflight-cutover.mjs'
    $script:NodeCmd = Get-Command node -ErrorAction SilentlyContinue
}

Describe 'preflight-cutover.mjs' {
    It 'has node available on the test runner' {
        $script:NodeCmd | Should -Not -BeNullOrEmpty
    }

    It 'passes its offline verdict-logic self-test (exit 0)' {
        $output = & node $script:PreflightScript --self-test 2>&1
        $LASTEXITCODE | Should -Be 0
        ($output -join "`n") | Should -Match 'self-test OK'
    }

    It 'exits 2 with usage when no domains are given' {
        & node $script:PreflightScript 2>&1 | Out-Null
        $LASTEXITCODE | Should -Be 2
    }

    It 'exits 2 when --origin is combined with multiple domains' {
        & node $script:PreflightScript '--domains=a.org,b.org' '--origin=https://example.com/' 2>&1 |
            Out-Null
        $LASTEXITCODE | Should -Be 2
    }
}
