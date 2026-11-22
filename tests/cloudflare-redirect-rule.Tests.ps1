# Set-CloudflareRedirectRule.ps1: subdomain sources and path-prefixed targets.
#
# WHY: after a charity's apex moves to GitHub Pages, its old WordPress subdomains
# (school.<domain>, radio.<domain>, ...) still point at the legacy host. Their
# content now lives under <domain>/<section>/, so each subdomain should 301 to
# that prefix. 111 could only redirect a zone apex. These tests cover the two
# pieces that make a subdomain source possible without a Cloudflare call:
# finding the zone by walking up the labels, and refusing input that would be
# interpolated into a rule expression unsafely.

BeforeAll {
    $script:ScriptPath = (Resolve-Path (Join-Path $PSScriptRoot '..' 'scripts' 'Set-CloudflareRedirectRule.ps1')).Path

    $ast = [System.Management.Automation.Language.Parser]::ParseFile($script:ScriptPath, [ref]$null, [ref]$null)
    $fn = $ast.Find({
            param($n)
            $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Get-ZoneCandidates'
        }, $true)
    if (-not $fn) { throw "Get-ZoneCandidates not found in $script:ScriptPath" }
    . ([scriptblock]::Create($fn.Extent.Text))
}

Describe 'Get-ZoneCandidates' {
    It 'returns the host itself and each parent, most specific first, stopping at two labels' {
        @(Get-ZoneCandidates -HostName 'school.newheightseducation.org') |
            Should -Be @('school.newheightseducation.org', 'newheightseducation.org')
    }

    It 'returns only the apex for a two-label zone' {
        @(Get-ZoneCandidates -HostName 'ffcsites.org') | Should -Be @('ffcsites.org')
    }

    It 'walks every level of a deeper host' {
        @(Get-ZoneCandidates -HostName 'a.b.example.org') |
            Should -Be @('a.b.example.org', 'b.example.org', 'example.org')
    }
}

Describe 'Input validation (runs before any token or API call)' {
    BeforeEach {
        # No token anywhere, so a run that got past validation would throw the
        # token error instead. Asserting on the message tells the two apart.
        $script:saved = @{}
        foreach ($v in 'CLOUDFLARE_API_TOKEN', 'CLOUDFLARE_API_TOKEN_FFC', 'CLOUDFLARE_API_TOKEN_CM') {
            $script:saved[$v] = [Environment]::GetEnvironmentVariable($v)
            [Environment]::SetEnvironmentVariable($v, $null)
        }
    }
    AfterEach {
        foreach ($v in $script:saved.Keys) { [Environment]::SetEnvironmentVariable($v, $script:saved[$v]) }
    }

    It 'rejects a source host containing a quote (would break out of the rule expression)' {
        { & $script:ScriptPath -SourceDomain 'evil.org") or (true' -TargetDomain 'example.org' -DryRun } |
            Should -Throw -ExpectedMessage "*is not a valid host name*"
    }

    It 'rejects a target containing a quote' {
        { & $script:ScriptPath -SourceDomain 'school.example.org' -TargetDomain 'example.org/x"' -DryRun } |
            Should -Throw -ExpectedMessage "*must be a host with an optional path prefix*"
    }

    It 'rejects a target with a scheme' {
        { & $script:ScriptPath -SourceDomain 'school.example.org' -TargetDomain 'https://example.org/school' -DryRun } |
            Should -Throw -ExpectedMessage "*must be a host with an optional path prefix*"
    }

    It 'accepts a subdomain source and a path-prefixed target (then needs a token)' {
        { & $script:ScriptPath -SourceDomain 'school.example.org' -TargetDomain 'example.org/school/' -DryRun } |
            Should -Throw -ExpectedMessage "*Cloudflare API token not found*"
    }
}
