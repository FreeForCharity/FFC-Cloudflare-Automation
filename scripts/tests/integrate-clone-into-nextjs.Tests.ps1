#Requires -Modules Pester

<#
.SYNOPSIS
    Runs the integrate-clone-into-nextjs.mjs self-test under Pester so CI covers
    the route-disabling and sentinel-cleanup logic.

.DESCRIPTION
    Two behaviours are easy to regress and expensive to notice:

    1. Real template `page.*` files must be MOVED to the backup, never deleted —
       they are the repo's original source and only git history would recover
       them.
    2. The `_clone-host` sentinel must be DELETED, not filed into that backup.
       It was written to guarantee `next build` had a route, but underscore
       folders are private in the App Router so it never routed at all
       (FFC-Cloudflare-Automation#901). Because the old code recreated it after
       disabling pages, a repeat dispatch also left a copy inside the backup.

    Mirrors the preflight-cutover.Tests.ps1 convention: the .mjs owns its cases
    behind --self-test, and Pester just asserts the exit code.
#>

BeforeAll {
    $script:RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
    $script:Script = Join-Path $RepoRoot 'scripts/integrate-clone-into-nextjs.mjs'
}

Describe 'integrate-clone-into-nextjs.mjs' {
    It 'has the script present' {
        Test-Path -LiteralPath $script:Script | Should -BeTrue
    }

    It 'passes its integration self-test' {
        $output = & node $script:Script --self-test 2>&1
        $exit = $LASTEXITCODE
        if ($exit -ne 0) {
            Write-Host ($output -join "`n")
        }
        $exit | Should -Be 0
    }

    It 'deletes the dead _clone-host sentinel instead of backing it up' {
        $output = (& node $script:Script --self-test 2>&1) -join "`n"
        $output | Should -Match 'ok\s+the `_clone-host` sentinel is deleted'
    }

    It 'moves real template routes to the backup rather than deleting them' {
        $output = (& node $script:Script --self-test 2>&1) -join "`n"
        $output | Should -Match 'ok\s+real template routes are moved to the backup'
    }

    It 'still reports usage when required arguments are missing' {
        # Guards the ordering trap: --self-test has to run before the usage
        # guard, without weakening the guard itself.
        $output = & node $script:Script 2>&1
        $LASTEXITCODE | Should -Be 2
        ($output -join "`n") | Should -Match 'Usage:'
    }
}
