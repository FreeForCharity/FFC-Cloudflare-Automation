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

    It 'accepts punycode TLDs (then needs a token)' {
        { & $script:ScriptPath -SourceDomain 'school.example.xn--p1ai' -TargetDomain 'example.xn--p1ai/school' -DryRun } |
            Should -Throw -ExpectedMessage '*Cloudflare API token not found*'
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

Describe 'Cloudflare API behaviour (Invoke-RestMethod mocked)' {
    BeforeEach {
        $script:saved = @{}
        foreach ($v in 'CLOUDFLARE_API_TOKEN', 'CLOUDFLARE_API_TOKEN_FFC', 'CLOUDFLARE_API_TOKEN_CM') {
            $script:saved[$v] = [Environment]::GetEnvironmentVariable($v)
            [Environment]::SetEnvironmentVariable($v, $null)
        }
        # One candidate token: Get-AuthToken returns it without an access probe.
        [Environment]::SetEnvironmentVariable('CLOUDFLARE_API_TOKEN', 'test-token')

        # Mock bodies run in Pester's scope, not this file's, so $script: state is
        # invisible there (CI: "cannot call a method on a null-valued expression").
        # Share it through one global, removed in AfterEach.
        $global:RRT = @{}
        $global:RRT.calls = [System.Collections.Generic.List[object]]::new()
        $global:RRT.putSucceeds = $true
        # school.example.org is NOT a zone; example.org is (id z1).
        # school has a DNS-only A record (r1), www.school a DNS-only CNAME (r2),
        # and an unrelated already-proxied record exists elsewhere in the zone (never fetched).
        $global:RRT.dns = @{
            'school.example.org'     = @([pscustomobject]@{ id = 'r1'; type = 'A'; name = 'school.example.org'; content = '192.0.2.10'; proxied = $false })
            'www.school.example.org' = @([pscustomobject]@{ id = 'r2'; type = 'CNAME'; name = 'www.school.example.org'; content = 'school.example.org'; proxied = $false })
        }

        Mock Invoke-RestMethod {
            $m = "$Method".ToUpperInvariant()
            $global:RRT.calls.Add([pscustomobject]@{ Method = $m; Uri = "$Uri"; Body = $Body })
            switch -Regex ("$m $Uri") {
                '^GET .*/zones\?name=example\.org$' { return [pscustomobject]@{ success = $true; result = @([pscustomobject]@{ id = 'z1' }) } }
                '^GET .*/zones\?name=' { return [pscustomobject]@{ success = $true; result = @() } }
                '^GET .*/dns_records\?name=([^&]+)' {
                    $n = [uri]::UnescapeDataString($Matches[1])
                    $r = if ($global:RRT.dns.ContainsKey($n)) { $global:RRT.dns[$n] } else { @() }
                    return [pscustomobject]@{ success = $true; result = @($r) }
                }
                '^GET .*/phases/http_request_dynamic_redirect/entrypoint$' {
                    return [pscustomobject]@{ success = $true; result = [pscustomobject]@{ rules = @([pscustomobject]@{ description = 'Existing unrelated rule'; expression = 'true' }) } }
                }
                '^PUT ' { return [pscustomobject]@{ success = $global:RRT.putSucceeds; result = [pscustomobject]@{ id = 'rs1' }; errors = @('boom') } }
                '^PATCH ' { return [pscustomobject]@{ success = $true; result = $null } }
                default { throw "unexpected call: $m $Uri" }
            }
        }
    }
    AfterEach {
        foreach ($v in $script:saved.Keys) { [Environment]::SetEnvironmentVariable($v, $script:saved[$v]) }
        Remove-Variable -Name RRT -Scope Global -ErrorAction SilentlyContinue
    }

    It 'dry run makes no writes, even with -ProxySourceRecords' {
        & $script:ScriptPath -SourceDomain 'school.example.org' -TargetDomain 'example.org/school' -ProxySourceRecords -DryRun 6>$null | Out-Null
        @($global:RRT.calls | Where-Object { $_.Method -ne 'GET' }).Count | Should -Be 0
    }

    It 'selects the parent zone and writes a path-prefixed target, keeping existing rules' {
        & $script:ScriptPath -SourceDomain 'school.example.org' -TargetDomain 'example.org/school' 6>$null | Out-Null
        $put = @($global:RRT.calls | Where-Object { $_.Method -eq 'PUT' })
        $put.Count | Should -Be 1
        $put[0].Uri | Should -BeLike '*/zones/z1/rulesets/phases/http_request_dynamic_redirect/entrypoint'
        $rules = ($put[0].Body | ConvertFrom-Json).rules
        @($rules).Count | Should -Be 2
        $ours = $rules | Where-Object { $_.description -eq 'Repoint school.example.org to example.org/school' }
        $ours.expression | Should -Be '(http.host eq "school.example.org") or (http.host eq "www.school.example.org")'
        $ours.action_parameters.from_value.target_url.expression | Should -Be 'concat("https://example.org/school", http.request.uri.path)'
    }

    It 'without -ProxySourceRecords never touches DNS records' {
        & $script:ScriptPath -SourceDomain 'school.example.org' -TargetDomain 'example.org/school' 6>$null | Out-Null
        @($global:RRT.calls | Where-Object { $_.Method -eq 'PATCH' -or $_.Uri -like '*dns_records*' }).Count | Should -Be 0
    }

    It 'proxies only the source and www records, and only after the rule is written' {
        & $script:ScriptPath -SourceDomain 'school.example.org' -TargetDomain 'example.org/school' -ProxySourceRecords 6>$null | Out-Null
        $writes = @($global:RRT.calls | Where-Object { $_.Method -in 'PUT', 'POST', 'PATCH' })
        $writes[0].Method | Should -Be 'PUT'
        $patches = @($writes | Where-Object { $_.Method -eq 'PATCH' })
        @($patches.Uri | ForEach-Object { ($_ -split '/')[-1] } | Sort-Object) | Should -Be @('r1', 'r2')
        foreach ($p in $patches) { ($p.Body | ConvertFrom-Json).proxied | Should -BeTrue }
    }

    It 'skips records that are already proxied' {
        $global:RRT.dns['www.school.example.org'][0].proxied = $true
        & $script:ScriptPath -SourceDomain 'school.example.org' -TargetDomain 'example.org/school' -ProxySourceRecords 6>$null | Out-Null
        @($global:RRT.calls | Where-Object { $_.Method -eq 'PATCH' } | ForEach-Object { ($_.Uri -split '/')[-1] }) | Should -Be @('r1')
    }

    It 'fails before any write when the source host has no record' {
        $global:RRT.dns.Remove('school.example.org')
        { & $script:ScriptPath -SourceDomain 'school.example.org' -TargetDomain 'example.org/school' -ProxySourceRecords 6>$null } |
            Should -Throw -ExpectedMessage '*no A/AAAA/CNAME record exists*'
        @($global:RRT.calls | Where-Object { $_.Method -ne 'GET' }).Count | Should -Be 0
    }

    It 'does not proxy anything when the ruleset write fails' {
        $global:RRT.putSucceeds = $false
        { & $script:ScriptPath -SourceDomain 'school.example.org' -TargetDomain 'example.org/school' -ProxySourceRecords 6>$null } |
            Should -Throw -ExpectedMessage '*Cloudflare API returned failure*'
        @($global:RRT.calls | Where-Object { $_.Method -eq 'PUT' }).Count | Should -Be 1
        @($global:RRT.calls | Where-Object { $_.Method -eq 'PATCH' }).Count | Should -Be 0
    }
}
