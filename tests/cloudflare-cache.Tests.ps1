# Unit tests for the pure helpers in the Cloudflare cache scripts.
#
# WHY THESE FOUR: they are the parts that decide what gets sent to Cloudflare,
# and each has a failure mode that is silent rather than loud.
#
#   Split-IntoBatch      - PowerShell unrolls an array returned to the pipeline,
#                          so the single-batch case (the common one) can quietly
#                          degrade from "one request with N URLs" to "N requests
#                          with one URL each". Nothing errors; the purge still
#                          works; it just costs N times the API calls and can
#                          trip rate limits. Only a nesting assertion catches it.
#   Resolve-UrlTarget    - parses the workflow_dispatch `urls` input. Lives in
#                          the script rather than the YAML precisely so it can be
#                          tested here.
#   Test-AbsoluteHttpUrl - Cloudflare rejects bare paths with a generic 400; this
#                          is what turns that into a message naming the value.
#   Test-CoversErrorStatus - decides whether a zone is protected against the
#                          cached-error failure mode (the 2026-08-07 incident).
#                          A wrong `true` here reports a zone as safe when it is
#                          not, which is the worst direction for this to fail.
#
# The scripts run on load (top-level try/catch), so the functions are lifted out
# by AST rather than dot-sourced -- same technique as
# tests/cloudflare-txt-records.Tests.ps1.

BeforeAll {
    $script:PurgePath = (Resolve-Path (Join-Path $PSScriptRoot '..' 'scripts' 'cloudflare-cache-purge.ps1')).Path
    $script:RulesPath = (Resolve-Path (Join-Path $PSScriptRoot '..' 'scripts' 'cloudflare-cache-rules-get.ps1')).Path

    function Get-FunctionFromFile {
        param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$Name)
        $ast = [System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$null, [ref]$null)
        $fn = $ast.Find({
                param($n)
                $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $Name
            }, $true)
        if (-not $fn) { throw "$Name not found in $Path" }
        return $fn
    }

    foreach ($n in @('Split-IntoBatch', 'Test-AbsoluteHttpUrl', 'Resolve-UrlTarget')) {
        . ([scriptblock]::Create((Get-FunctionFromFile -Path $script:PurgePath -Name $n).Extent.Text))
    }
    . ([scriptblock]::Create((Get-FunctionFromFile -Path $script:RulesPath -Name 'Test-CoversErrorStatus').Extent.Text))
}

Describe 'Split-IntoBatch' {
    It 'keeps a single batch nested rather than unrolling it' {
        # The regression guard. If `return , $batches` is reduced to
        # `return $batches`, this returns 3 strings instead of 1 batch of 3.
        $result = Split-IntoBatch -Items @('a', 'b', 'c') -Size 30
        $result.Count | Should -Be 1
        @($result[0]).Count | Should -Be 3
    }

    It 'splits at the batch size boundary' {
        $items = 1..65 | ForEach-Object { "https://example.org/$_" }
        $result = Split-IntoBatch -Items $items -Size 30
        $result.Count | Should -Be 3
        @($result[0]).Count | Should -Be 30
        @($result[1]).Count | Should -Be 30
        @($result[2]).Count | Should -Be 5
    }

    It 'produces exactly one batch when the count equals the size' {
        $items = 1..30 | ForEach-Object { "https://example.org/$_" }
        $result = Split-IntoBatch -Items $items -Size 30
        $result.Count | Should -Be 1
        @($result[0]).Count | Should -Be 30
    }

    It 'loses no items across the split' {
        $items = 1..47 | ForEach-Object { "https://example.org/$_" }
        $flat = (Split-IntoBatch -Items $items -Size 30) | ForEach-Object { $_ }
        @($flat).Count | Should -Be 47
        (@($flat) | Select-Object -Unique).Count | Should -Be 47
    }
}

Describe 'Test-AbsoluteHttpUrl' {
    It 'accepts absolute http and https URLs' {
        Test-AbsoluteHttpUrl -Value 'https://freeforcharity.org/Svgs/a.svg' | Should -BeTrue
        Test-AbsoluteHttpUrl -Value 'http://freeforcharity.org/a.js' | Should -BeTrue
    }

    It 'rejects a bare path' {
        # Cloudflare answers these with a generic 400; catching it here is the
        # whole point of the helper.
        Test-AbsoluteHttpUrl -Value '/Svgs/a.svg' | Should -BeFalse
    }

    It 'rejects a host with no scheme' {
        Test-AbsoluteHttpUrl -Value 'freeforcharity.org/a.svg' | Should -BeFalse
    }

    It 'rejects a non-http scheme' {
        Test-AbsoluteHttpUrl -Value 'ftp://freeforcharity.org/a.svg' | Should -BeFalse
    }
}

Describe 'Resolve-UrlTarget' {
    It 'splits a newline-separated workflow input' {
        $result = @(Resolve-UrlTarget -FromString "https://a.org/1`nhttps://a.org/2")
        $result.Count | Should -Be 2
        $result[0] | Should -Be 'https://a.org/1'
    }

    It 'splits comma-separated and whitespace-separated input' {
        @(Resolve-UrlTarget -FromString 'https://a.org/1, https://a.org/2').Count | Should -Be 2
        @(Resolve-UrlTarget -FromString 'https://a.org/1 https://a.org/2').Count | Should -Be 2
    }

    It 'handles CRLF, which is what a pasted textarea actually sends' {
        @(Resolve-UrlTarget -FromString "https://a.org/1`r`nhttps://a.org/2").Count | Should -Be 2
    }

    It 'de-duplicates across both parameters' {
        $result = @(Resolve-UrlTarget -FromArray @('https://a.org/1') -FromString 'https://a.org/1, https://a.org/2')
        $result.Count | Should -Be 2
    }

    It 'returns an empty list for empty, whitespace and null input' {
        @(Resolve-UrlTarget -FromString '').Count | Should -Be 0
        @(Resolve-UrlTarget -FromString "  `n  ").Count | Should -Be 0
        @(Resolve-UrlTarget).Count | Should -Be 0
    }
}

Describe 'Test-CoversErrorStatus' {
    It 'detects a single 4xx/5xx status code' {
        Test-CoversErrorStatus -Entry ([pscustomobject]@{ status_code = 429 }) | Should -BeTrue
        Test-CoversErrorStatus -Entry ([pscustomobject]@{ status_code = 503 }) | Should -BeTrue
    }

    It 'does not treat a 2xx/3xx cap as error coverage' {
        # The dangerous direction: reporting a zone protected when it is not.
        Test-CoversErrorStatus -Entry ([pscustomobject]@{ status_code = 200 }) | Should -BeFalse
        Test-CoversErrorStatus -Entry ([pscustomobject]@{ status_code = 301 }) | Should -BeFalse
    }

    It 'detects a range that overlaps 400-599' {
        $entry = [pscustomobject]@{ status_code_range = [pscustomobject]@{ from = 400; to = 599 } }
        Test-CoversErrorStatus -Entry $entry | Should -BeTrue
    }

    It 'detects a range that only partially overlaps' {
        $entry = [pscustomobject]@{ status_code_range = [pscustomobject]@{ from = 300; to = 404 } }
        Test-CoversErrorStatus -Entry $entry | Should -BeTrue
    }

    It 'rejects a range entirely below 400' {
        $entry = [pscustomobject]@{ status_code_range = [pscustomobject]@{ from = 200; to = 399 } }
        Test-CoversErrorStatus -Entry $entry | Should -BeFalse
    }

    It 'treats an open-ended range as reaching the missing bound' {
        # `from` only: Cloudflare reads this as from..599.
        $openTop = [pscustomobject]@{ status_code_range = [pscustomobject]@{ from = 500 } }
        Test-CoversErrorStatus -Entry $openTop | Should -BeTrue

        # `to` only: 100..to, so a low `to` must NOT count as error coverage.
        $openBottom = [pscustomobject]@{ status_code_range = [pscustomobject]@{ to = 399 } }
        Test-CoversErrorStatus -Entry $openBottom | Should -BeFalse
    }

    It 'returns false for an entry with neither field' {
        Test-CoversErrorStatus -Entry ([pscustomobject]@{ value = 0 }) | Should -BeFalse
    }
}

Describe 'The -DryRun guard (end-to-end through the real script)' {
    <#
        WHY THIS EXISTS: every other case in this file is a pure-function test
        that never reaches the script's main flow, so `if ($DryRun)` at the
        branch deciding whether production edge cache is actually purged had
        zero coverage -- neutering it to `if ($false)` left the suite fully
        green. The workflow's own gate (`apply` is `if: inputs.dry_run == false`
        on cloudflare-prod-write) protects the CI path, but the console path the
        script's own .EXAMPLE advertises has nothing between the operator and a
        full-zone purge except this switch.

        HOW: the real script is copied byte-for-byte into a sandbox next to a
        STUB cloudflare-api-common.ps1. The script resolves its library via
        `. (Join-Path $PSScriptRoot 'cloudflare-api-common.ps1')`, so the stub
        is what swaps out the HTTP boundary -- nothing in the file under test is
        modified, and the branch runs for real. The stub's Invoke-CfApi appends
        to a log, which makes "no purge was issued" an observation rather than
        an inference.
    #>

    BeforeAll {
        $script:Sandbox = Join-Path ([System.IO.Path]::GetTempPath()) ('cfpurge-' + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $script:Sandbox -Force | Out-Null

        $script:SandboxScript = Join-Path $script:Sandbox 'cloudflare-cache-purge.ps1'
        Copy-Item -Path $script:PurgePath -Destination $script:SandboxScript

        # Guard the guard: if the copy ever drifts from the real file, this
        # suite would be testing a stale artifact and reporting a pass about it.
        (Get-FileHash -Path $script:SandboxScript -Algorithm SHA256).Hash |
            Should -Be (Get-FileHash -Path $script:PurgePath -Algorithm SHA256).Hash

        $script:CallLog = Join-Path $script:Sandbox 'calls.jsonl'

        $stub = @'
function Resolve-CfZone {
    param([string]$Domain, [array]$Tokens)
    return [pscustomobject]@{ Account = 'FFC'; Token = 'stub-token'; ZoneId = 'zone123'; ZoneName = $Domain }
}
function Invoke-CfApi {
    param([string]$Method, [string]$Path, [string]$Token, $Body, [int]$TimeoutSec = 30)
    (([ordered]@{ method = $Method; path = $Path }) | ConvertTo-Json -Compress) |
        Out-File -FilePath $env:CF_CALL_LOG -Append -Encoding utf8
    return [pscustomobject]@{ success = $true; result = [pscustomobject]@{ id = 'purge-1' } }
}
'@
        Set-Content -Path (Join-Path $script:Sandbox 'cloudflare-api-common.ps1') -Value $stub -Encoding utf8

        function Invoke-PurgeScript {
            param([string[]]$ScriptArgs)
            if (Test-Path $script:CallLog) { Remove-Item $script:CallLog -Force }
            $env:CF_CALL_LOG = $script:CallLog
            $errFile = Join-Path $script:Sandbox 'stderr.txt'
            $out = & pwsh -NoProfile -File $script:SandboxScript @ScriptArgs 2> $errFile
            return ($out | Out-String)
        }

        function Get-LoggedCall {
            if (-not (Test-Path $script:CallLog)) { return @() }
            return @(Get-Content -Path $script:CallLog | Where-Object { $_ } | ForEach-Object { $_ | ConvertFrom-Json })
        }
    }

    AfterAll {
        Remove-Item -Path $script:Sandbox -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item Env:\CF_CALL_LOG -ErrorAction SilentlyContinue
    }

    It 'issues NO purge call and reports dryRun=true when -DryRun is passed' {
        $json = Invoke-PurgeScript -ScriptArgs @('-Domain', 'example.org', '-Url', 'https://example.org/a.svg', '-DryRun')
        $verdict = $json | ConvertFrom-Json

        $verdict.dryRun | Should -BeTrue
        @($verdict.purged).Count | Should -Be 0
        # The assertion that actually discriminates: nothing reached the API.
        @(Get-LoggedCall).Count | Should -Be 0
    }

    It 'issues NO purge call for -All -DryRun either' {
        # The whole-zone shape is the one with the worst blast radius, so it
        # gets its own case rather than riding on the -Url one.
        $json = Invoke-PurgeScript -ScriptArgs @('-Domain', 'example.org', '-All', '-DryRun')
        $verdict = $json | ConvertFrom-Json

        $verdict.dryRun | Should -BeTrue
        $verdict.mode | Should -Be 'everything'
        @(Get-LoggedCall).Count | Should -Be 0
    }

    It 'DOES issue a purge call when -DryRun is absent' {
        # The mirror. Without this, a script that never purged at all would also
        # pass the two cases above.
        $json = Invoke-PurgeScript -ScriptArgs @('-Domain', 'example.org', '-Url', 'https://example.org/a.svg')
        $verdict = $json | ConvertFrom-Json

        $verdict.dryRun | Should -BeFalse
        @($verdict.purged).Count | Should -Be 1
        $verdict.success | Should -BeTrue

        $calls = @(Get-LoggedCall)
        $calls.Count | Should -Be 1
        $calls[0].method | Should -Be 'POST'
        $calls[0].path | Should -Be '/zones/zone123/purge_cache'
    }

    It 'batches one call per 30 URLs on the live path' {
        # Ties the batch splitter to the wire: 65 URLs must be 3 calls, not 65.
        # Uses -UrlList, not repeated -Url, for the reason asserted below.
        $urls = 1..65 | ForEach-Object { "https://example.org/$_.svg" }
        $json = Invoke-PurgeScript -ScriptArgs @('-Domain', 'example.org', '-UrlList', ($urls -join ','))
        $verdict = $json | ConvertFrom-Json

        @($verdict.purged).Count | Should -Be 65
        @(Get-LoggedCall).Count | Should -Be 3
    }

    It 'binds only ONE value from repeated -Url under pwsh -File, which is why -UrlList exists' {
        <#
            Not a defect in the script -- a property of `pwsh -File`, which does
            not collect multiple values into a [string[]] parameter. It is
            pinned here because it is silent and it loses data: an operator
            passing three -Url flags purges ONE URL and gets a verdict that
            truthfully reports success for the one it saw. docs/cloudflare-cache.md
            showed exactly that shape until this test was written.
        #>
        $json = Invoke-PurgeScript -ScriptArgs @(
            '-Domain', 'example.org',
            '-Url', 'https://example.org/1.svg',
            '-Url', 'https://example.org/2.svg'
        )
        $verdict = $json | ConvertFrom-Json

        @($verdict.purged).Count | Should -Be 1

        # ... whereas the documented -UrlList form carries both.
        $json2 = Invoke-PurgeScript -ScriptArgs @(
            '-Domain', 'example.org',
            '-UrlList', 'https://example.org/1.svg,https://example.org/2.svg'
        )
        @((($json2 | ConvertFrom-Json)).purged).Count | Should -Be 2
    }
}
