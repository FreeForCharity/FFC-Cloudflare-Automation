<#
.SYNOPSIS
    Pester (v5) tests for scripts/preflight-cutover.mjs (workflow 121).

.DESCRIPTION
    The verdict / classification logic lives in the Node script itself and is
    exercised two ways, both offline (no network calls):

      1. The script's own --self-test mode (host classification, CAA policy,
         origin derivation, domain-list parsing, and the go/no-go verdict
         outcomes).
      2. Direct imports of the exported pure functions with mocked shapes --
         mixed-type DoH answer sections, an iodef-only CAA RRset, a redirecting
         Pages origin, and the post-cutover www verdict rule. The module only
         runs main() when executed as the entrypoint, so importing it is safe.

    These tests also assert the usage-error contract so the workflow fails
    fast on bad inputs.

    Run locally: Invoke-Pester -Path scripts/tests -Output Detailed
#>

BeforeAll {
    $script:PreflightScript = Join-Path (Split-Path $PSScriptRoot -Parent) 'preflight-cutover.mjs'
    $script:NodeCmd = Get-Command node -ErrorAction SilentlyContinue

    # Evaluate a JS boolean expression against the imported module ($m) and
    # return $true when it holds. The module path travels via an env var (not
    # argv) so the script's entrypoint guard doesn't mistake the import for a
    # CLI run.
    function Test-PreflightExpr([string]$Expr) {
        $js = @(
            "const { pathToFileURL } = await import('node:url');"
            'const m = await import(pathToFileURL(process.env.PREFLIGHT_MJS).href);'
            "process.exit((${Expr}) ? 0 : 1);"
        ) -join "`n"
        $env:PREFLIGHT_MJS = $script:PreflightScript
        try {
            & node --input-type=module -e $js 2>&1 | Out-Null
            return ($LASTEXITCODE -eq 0)
        }
        finally {
            Remove-Item Env:PREFLIGHT_MJS -ErrorAction SilentlyContinue
        }
    }
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

Describe 'preflight-cutover.mjs exported logic (mocked shapes)' {
    It 'is importable without running main (entrypoint guard)' {
        Test-PreflightExpr 'typeof m.computeVerdict === "function"' | Should -BeTrue
    }

    Context 'CAA answers are type-filtered (mixed-type DoH answer sections)' {
        It 'keeps only type-257 records from a mixed CNAME + CAA answer' {
            $expr = 'JSON.stringify(m.caaRecordsFromAnswers([' +
            '{type:5,data:"alias.example.net"},' +
            '{type:257,data:"0 issue \"letsencrypt.org\""},' +
            '{type:46,data:"sig"}' +
            '])) === JSON.stringify(["0 issue \"letsencrypt.org\""])'
            Test-PreflightExpr $expr | Should -BeTrue
        }

        It 'yields an empty CAA set from a CNAME-only answer (no false block)' {
            $expr = 'm.caaOutcome({records: m.caaRecordsFromAnswers([' +
            '{type:5,data:"alias.example.net"}])}).ok === true'
            Test-PreflightExpr $expr | Should -BeTrue
        }
    }

    Context 'CAA issuance policy (RFC 8659)' {
        It 'treats an iodef-only RRset as allowing issuance' {
            Test-PreflightExpr 'm.caaAllowsLetsEncrypt(["0 iodef \"mailto:sec@x.org\""]) === true' |
                Should -BeTrue
            Test-PreflightExpr 'm.caaOutcome({records:["0 iodef \"mailto:sec@x.org\""]}).ok === true' |
                Should -BeTrue
        }

        It 'still blocks when issue/issuewild exist and none permits letsencrypt.org' {
            $expr = 'm.caaAllowsLetsEncrypt(["0 iodef \"mailto:sec@x.org\"",' +
            '"0 issue \"digicert.com\""]) === false'
            Test-PreflightExpr $expr | Should -BeTrue
        }

        It 'accepts an issuewild grant for letsencrypt.org' {
            Test-PreflightExpr 'm.caaAllowsLetsEncrypt(["0 issuewild \"letsencrypt.org\""]) === true' |
                Should -BeTrue
        }
    }

    Context 'Pages origin probe (redirect: manual)' {
        It 'treats HTTP 200 as healthy' {
            Test-PreflightExpr 'm.originProbeOutcome({domain:"x.org",status:200}).healthy === true' |
                Should -BeTrue
        }

        It 'treats a 301 to the repo''s configured Pages domain as healthy and records the target' {
            $expr = '(() => { const r = m.originProbeOutcome({domain:"x.org",status:301,' +
            'location:"https://staging.x.org/"}); return r.healthy === true && ' +
            'r.redirectTarget === "https://staging.x.org/" && /origin healthy/.test(r.detail); })()'
            Test-PreflightExpr $expr | Should -BeTrue
        }

        It 'treats a redirect to an unrelated host as unhealthy' {
            $expr = 'm.originProbeOutcome({domain:"x.org",status:301,' +
            'location:"https://evil.example.com/"}).healthy === false'
            Test-PreflightExpr $expr | Should -BeTrue
        }

        It 'does not let a suffix-similar host impersonate the domain' {
            $expr = 'm.originProbeOutcome({domain:"x.org",status:301,' +
            'location:"https://evilx.org/"}).healthy === false'
            Test-PreflightExpr $expr | Should -BeTrue
        }

        It 'treats non-200 non-redirect statuses and probe errors as unhealthy' {
            Test-PreflightExpr 'm.originProbeOutcome({domain:"x.org",status:404}).healthy === false' |
                Should -BeTrue
            Test-PreflightExpr 'm.originProbeOutcome({domain:"x.org",error:"timeout"}).healthy === false' |
                Should -BeTrue
        }
    }

    Context 'origin derivation preserves repo-name case' {
        It 'builds the origin from the repo''s real (mixed-case) name' {
            $expr = 'm.originForRepo("FFC-EX-AllTypeTowing.com") === ' +
            '"https://freeforcharity.github.io/FFC-EX-AllTypeTowing.com/"'
            Test-PreflightExpr $expr | Should -BeTrue
        }
    }

    Context 'basePath artifact check (issue #748)' {
        It 'flags root-relative /FFC-EX-… href/src refs as a blocker' {
            $expr = '(() => { const r = m.basePathOutcome({body:' +
            '"<link href=\"/FFC-EX-x.org/_next/x.css\">"}); ' +
            'return r.ok === false && r.refs.length === 1 && /404 at the apex root/.test(r.detail); })()'
            Test-PreflightExpr $expr | Should -BeTrue
        }

        It 'passes an export with no basePath (root-relative) refs' {
            Test-PreflightExpr 'm.basePathOutcome({body:"<link href=\"/_next/x.css\">"}).ok === true' |
                Should -BeTrue
        }

        It 'exempts an absolute external URL that merely contains the repo name' {
            $expr = 'm.basePathArtifactRefs(' +
            '"<a href=\"https://github.com/FreeForCharity/FFC-EX-x.org/blob/main/LICENSE\">L</a>"' +
            ').length === 0'
            Test-PreflightExpr $expr | Should -BeTrue
        }

        It 'warns (does not block) when the export body cannot be fetched' {
            Test-PreflightExpr 'm.basePathOutcome({error:"timeout"}).ok === "warn"' | Should -BeTrue
        }
    }

    Context 'post-cutover www requirement' {
        It 'fails the verdict when the apex is on Pages but www is unhealthy' {
            $expr = '(() => { const v = m.computeVerdict({originHealthy:true,pointedAtPages:true,' +
            'blockerCount:0,wwwHealthy:false}); ' +
            'return v.code === "CUTOVER_INCOMPLETE_WWW" && v.ok === false; })()'
            Test-PreflightExpr $expr | Should -BeTrue
        }

        It 'keeps pre-cutover verdicts unaffected by www (informational only)' {
            $expr = 'm.computeVerdict({originHealthy:true,pointedAtPages:false,' +
            'blockerCount:0,wwwHealthy:false}).code === "READY"'
            Test-PreflightExpr $expr | Should -BeTrue
        }
    }
}
