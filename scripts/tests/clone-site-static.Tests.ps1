#Requires -Modules Pester

<#
.SYNOPSIS
    Runs the clone-site-static.mjs self-test under Pester so CI covers the
    legacy-URL localization logic.

.DESCRIPTION
    The localization regex is the subtle part of the cloner: it has to stop
    matching at an HTML-entity boundary. A pattern that runs past `&quot;`
    swallows the rest of an escaped-JSON blob, and because a global replace
    resumes after the whole match, every following URL in that blob silently
    goes unrewritten — the failure mode behind FFC-Cloudflare-Automation#902.

    Mirrors the preflight-cutover.Tests.ps1 convention: the .mjs owns its cases
    behind --self-test, and Pester just asserts the exit code.
#>

BeforeAll {
    $script:RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
    $script:Script = Join-Path $RepoRoot 'scripts/clone-site-static.mjs'
    $script:Verify = Join-Path $RepoRoot 'scripts/verify-no-legacy.mjs'
}

Describe 'clone-site-static.mjs' {
    It 'has the script present' {
        Test-Path -LiteralPath $script:Script | Should -BeTrue
    }

    It 'passes its localization self-test' {
        $output = & node $script:Script --self-test 2>&1
        $exit = $LASTEXITCODE
        if ($exit -ne 0) {
            Write-Host ($output -join "`n")
        }
        $exit | Should -Be 0
    }

    It 'localizes every URL inside an entity-escaped JSON blob' {
        # Guards the specific regression: the second URL must be rewritten too.
        $output = (& node $script:Script --self-test 2>&1) -join "`n"
        $output | Should -Match 'ok\s+entity-escaped JSON'
    }
}

Describe 'verify-no-legacy.mjs' {
    It 'has the script present' {
        Test-Path -LiteralPath $script:Verify | Should -BeTrue
    }

    It 'passes its host-matching and path-containment self-test' {
        $output = & node $script:Verify --self-test 2>&1
        $exit = $LASTEXITCODE
        if ($exit -ne 0) {
            Write-Host ($output -join "`n")
        }
        $exit | Should -Be 0
    }

    It 'does not treat the domain appearing in a query string as a legacy host' {
        # Guards against reverting to a substring test, which would abort
        # unrelated third-party requests.
        $output = (& node $script:Verify --self-test 2>&1) -join "`n"
        $output | Should -Match 'ok\s+domain in a query string is NOT legacy'
    }

    It 'contains path traversal that a startsWith prefix test would allow' {
        $output = (& node $script:Verify --self-test 2>&1) -join "`n"
        $output | Should -Match 'ok\s+sibling-dir traversal is contained'
    }
}
