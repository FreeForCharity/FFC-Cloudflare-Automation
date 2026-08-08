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
