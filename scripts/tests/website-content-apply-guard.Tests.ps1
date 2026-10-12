<#
.SYNOPSIS
    Pester (v5) unit tests for website-content-apply-guard.ps1 (workflow 707).

.DESCRIPTION
    Dot-sources the guard and exercises the offline decision logic: the target
    repo-name regex, the path allowlist and its hard refusals, the payload byte
    budget, duplicate and case-colliding keys, and the per-file content checks.
    Loads the REAL config/website-content-apply-paths.json, so a config edit that
    widens the allowlist past a hard refusal is caught here. No network, no git.

    Run locally: Invoke-Pester -Path scripts/tests -Output Detailed
#>

BeforeAll {
    $scriptsDir = Split-Path $PSScriptRoot -Parent
    . (Join-Path $scriptsDir 'website-content-apply-guard.ps1')
    $script:ConfigPath = Join-Path (Split-Path $scriptsDir -Parent) 'config/website-content-apply-paths.json'
    $script:Policy = Get-ContentApplyPolicy -Path $script:ConfigPath

    function ConvertTo-PayloadJson {
        param([System.Collections.IDictionary]$Map)
        return ($Map | ConvertTo-Json -Depth 3 -Compress)
    }
}

Describe 'Test-ContentApplyRepoName' {
    It 'accepts <_>' -ForEach @('FFC-EX-example.org', 'FFC-EX-iwilf.org', 'FFC-EX-my-charity.org', 'FFC-EX-canary') {
        Test-ContentApplyRepoName -Name $_ | Should -BeNullOrEmpty
    }

    It 'refuses <_>' -ForEach @(
        'FFC-IN-ffcadmin.org',          # internal lane, never written here
        'FFC-Cloudflare-Automation',    # this hub
        'FFC-EX-SRRN.net',              # mixed case: outside the regex
        'ffc-ex-example.org',           # prefix is case-sensitive
        'FreeForCharity/FFC-EX-a.org',  # owner-qualified
        'https://github.com/FreeForCharity/FFC-EX-a.org',
        'FFC-EX-',                      # no domain at all
        'FFC-EX-a_b.org',               # underscore
        'FFC-EX-a.org ',                # trailing space
        'FFC-EX-.org',                  # leading dot
        'FFC-EX-a..org',                # empty label
        'FFC-EX-a.org-'                 # trailing dash
    ) {
        Test-ContentApplyRepoName -Name $_ | Should -Not -BeNullOrEmpty
    }

    It 'refuses an empty or missing name' {
        Test-ContentApplyRepoName -Name '' | Should -Match 'required'
        Test-ContentApplyRepoName -Name $null | Should -Match 'required'
    }
}

Describe 'Get-ContentApplyPolicy (the shipped config)' {
    It 'caps the payload at 60 KB' {
        $script:Policy.MaxTotalBytes | Should -Be 61440
    }

    It 'allows only text extensions' {
        $script:Policy.AllowedExtensions | Should -Be @('.ts', '.json')
    }

    It 'refuses to load a policy with an empty allow list' {
        $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("cap-policy-{0}.json" -f [guid]::NewGuid())
        try {
            '{"max_total_bytes":100,"max_files":5,"allowed_extensions":[".ts"],"allow":[],"deny":[]}' | Set-Content -LiteralPath $tmp -Encoding utf8
            { Get-ContentApplyPolicy -Path $tmp } | Should -Throw '*empty allow list*'
        }
        finally {
            Remove-Item -LiteralPath $tmp -ErrorAction SilentlyContinue
        }
    }
}

Describe 'ConvertTo-ContentApplyGlobRegex' {
    It 'matches zero or more segments for **/' {
        $rx = ConvertTo-ContentApplyGlobRegex -Glob 'src/data/**/*.json'
        'src/data/a.json' | Should -Match $rx
        'src/data/team/a.json' | Should -Match $rx
        'src/data/x/y/a.json' | Should -Match $rx
        'src/database/a.json' | Should -Not -Match $rx
    }

    It 'keeps * inside one segment' {
        $rx = ConvertTo-ContentApplyGlobRegex -Glob 'src/data/*.ts'
        'src/data/team.ts' | Should -Match $rx
        'src/data/team/x.ts' | Should -Not -Match $rx
    }

    It 'treats regex metacharacters as literals' {
        $rx = ConvertTo-ContentApplyGlobRegex -Glob 'src/lib/site.config.ts'
        'src/lib/siteXconfigXts' | Should -Not -Match $rx
    }
}

Describe 'Test-ContentApplyPath' {
    It 'allows <_>' -ForEach @(
        'src/lib/site.config.ts',
        'src/data/team.ts',
        'src/data/faqs.ts',
        'src/data/results.ts',
        'src/data/testimonials.ts',
        'src/data/team/jane-doe.json',
        'src/data/faqs/what-we-do.json',
        'src/data/testimonials/testimonial-1.json'
    ) {
        Test-ContentApplyPath -Path $_ -Policy $script:Policy | Should -BeNullOrEmpty
    }

    It 'refuses <Path> (<Why>)' -ForEach @(
        @{ Path = '.github/workflows/ci.yml'; Why = 'workflow' }
        @{ Path = '.github/CODEOWNERS'; Why = '.github' }
        @{ Path = 'src/data/.github/x.json'; Why = 'hidden directory at depth' }
        @{ Path = 'src/data/team/.env.json'; Why = 'hidden file' }
        @{ Path = 'public/CNAME'; Why = 'CNAME' }
        @{ Path = 'CNAME'; Why = 'CNAME' }
        @{ Path = 'src/data/CNAME'; Why = 'CNAME anywhere' }
        @{ Path = 'package.json'; Why = 'manifest' }
        @{ Path = 'src/data/package.json'; Why = 'manifest anywhere' }
        @{ Path = 'package-lock.json'; Why = 'lockfile' }
        @{ Path = 'pnpm-lock.yaml'; Why = 'lockfile' }
        @{ Path = 'src/data/../../.github/workflows/x.json'; Why = 'dot-dot' }
        @{ Path = 'src/data/team/../../../x.json'; Why = 'dot-dot' }
        @{ Path = 'src/data/./team.ts'; Why = 'dot segment' }
        @{ Path = '/src/data/team.ts'; Why = 'absolute' }
        @{ Path = 'C:/src/data/team.ts'; Why = 'drive letter' }
        @{ Path = 'src\data\team.ts'; Why = 'backslash' }
        @{ Path = 'src/data//team.ts'; Why = 'empty segment' }
        @{ Path = 'src/data/team/a b.json'; Why = 'space' }
        @{ Path = 'src/data/team/a%2e.json'; Why = 'percent' }
        @{ Path = 'src/data/logo.png'; Why = 'binary' }
        @{ Path = 'src/data/team/photo.webp'; Why = 'binary' }
        @{ Path = 'src/data/team.tsx'; Why = 'component extension' }
        @{ Path = 'src/data/run.js'; Why = 'script extension' }
        @{ Path = 'src/data/events.generated.json'; Why = 'generated, on deny list' }
        @{ Path = 'src/app/page.tsx'; Why = 'not allowlisted' }
        @{ Path = 'src/lib/analytics.config.ts'; Why = 'not allowlisted' }
        @{ Path = 'next.config.ts'; Why = 'not allowlisted' }
        @{ Path = 'SRC/data/team.ts'; Why = 'case differs from the allowlist' }
        @{ Path = 'src/data/team/workflows/x.yml'; Why = 'yaml' }
        @{ Path = ''; Why = 'empty' }
    ) {
        Test-ContentApplyPath -Path $Path -Policy $script:Policy | Should -Not -BeNullOrEmpty
    }

    It 'keeps the hard refusals even when a config allows everything' {
        $wide = [pscustomobject]@{
            MaxTotalBytes     = 61440
            MaxFiles          = 50
            AllowedExtensions = @('.ts', '.json', '.yml')
            Allow             = @('**/*')
            AllowRegex        = @('^.*$')
            Deny              = @()
            DenyRegex         = @()
        }
        Test-ContentApplyPath -Path '.github/workflows/x.yml' -Policy $wide | Should -Match '\.github/'
        Test-ContentApplyPath -Path 'package.json' -Policy $wide | Should -Match 'package\.json'
        Test-ContentApplyPath -Path 'public/CNAME' -Policy $wide | Should -Not -BeNullOrEmpty
        Test-ContentApplyPath -Path 'a/../b.ts' -Policy $wide | Should -Match '\.\.'
        Test-ContentApplyPath -Path 'ci/workflows/deploy.yml' -Policy $wide | Should -Match 'workflow'
    }
}

Describe 'Test-ContentApplyFileContent' {
    It 'accepts a valid JSON record' {
        Test-ContentApplyFileContent -Path 'src/data/team/a.json' -Content '{"name":"Jane Doe","role":"Founder"}' | Should -BeNullOrEmpty
    }

    It 'refuses invalid JSON in a .json file' {
        Test-ContentApplyFileContent -Path 'src/data/team/a.json' -Content '{"name":' | Should -Match 'not valid JSON'
    }

    It 'refuses an empty file' {
        Test-ContentApplyFileContent -Path 'src/data/team.ts' -Content '  ' | Should -Match 'empty'
    }

    It 'refuses a NUL byte' {
        Test-ContentApplyFileContent -Path 'src/data/team.ts' -Content ("a" + [char]0 + "b") | Should -Match 'NUL'
    }

    It "accepts the template's own NEXT_PUBLIC_BASE_PATH read and local imports" {
        $ts = @'
import { siteConfig } from '@/lib/site.config'
import faq1 from './faqs/one.json'
import type { Faq } from '../types'
export const basePath = process.env.NEXT_PUBLIC_BASE_PATH || ''
export const PENDING_TEXT = 'Awaiting information from the charity'
export const note = 'Imported "from" nowhere'
'@
        Test-ContentApplyFileContent -Path 'src/lib/site.config.ts' -Content $ts | Should -BeNullOrEmpty
    }

    It 'refuses <Why> in a .ts file' -ForEach @(
        @{ Why = 'a dynamic import'; Ts = "const m = await import('fs')" }
        @{ Why = 'require'; Ts = "const fs = require('fs')" }
        @{ Why = 'eval'; Ts = "eval('1')" }
        @{ Why = 'new Function'; Ts = "const f = new Function('return 1')" }
        @{ Why = 'a secret env read'; Ts = 'export const t = process.env.GITHUB_TOKEN' }
        @{ Why = 'an indexed env read'; Ts = "export const t = process.env['NEXT_PUBLIC_X']" }
        @{ Why = 'child_process'; Ts = "import { execSync } from 'child_process'" }
        @{ Why = 'a node: import'; Ts = "import fs from 'node:fs'" }
        @{ Why = 'a package import'; Ts = "import x from 'left-pad'" }
        @{ Why = 'a multi-line package import'; Ts = "import {`n  a,`n  b,`n} from 'some-pkg'" }
        @{ Why = 'a re-export of a package'; Ts = "export * from 'some-pkg'" }
        @{ Why = 'a side-effect import'; Ts = "import 'some-pkg/register'" }
        @{ Why = 'a script tag'; Ts = "export const x = '<script>alert(1)</script>'" }
    ) {
        Test-ContentApplyFileContent -Path 'src/data/team.ts' -Content $Ts | Should -Not -BeNullOrEmpty
    }
}

Describe 'Test-ContentApplyPayload' {
    It 'accepts a well-formed payload and reports each file' {
        $json = ConvertTo-PayloadJson ([ordered]@{
                'src/data/team/jane-doe.json' = '{"name":"Jane Doe","role":"Founder"}'
                'src/data/team.ts'            = "import jane from './team/jane-doe.json'`nexport const team = [jane]`n"
            })
        $r = Test-ContentApplyPayload -ContentJson $json -Policy $script:Policy
        $r.Errors | Should -BeNullOrEmpty
        @($r.Files).Count | Should -Be 2
        $r.Files[0].Path | Should -Be 'src/data/team/jane-doe.json'
        $r.TotalBytes | Should -BeGreaterThan 0
    }

    It 'measures the payload in UTF-8 bytes, not characters' {
        # Built from a code point so this file stays ASCII (no BOM needed).
        $name = 'Zo' + [char]0x00EB
        $json = ConvertTo-PayloadJson ([ordered]@{ 'src/data/team/a.json' = ('{"name":"' + $name + '"}') })
        $r = Test-ContentApplyPayload -ContentJson $json -Policy $script:Policy
        $r.PayloadBytes | Should -Be ([System.Text.Encoding]::UTF8.GetByteCount($json))
        $r.PayloadBytes | Should -BeGreaterThan $json.Length
    }

    It 'refuses a payload over 60 KB before parsing it' {
        $big = '{"note":"' + ('x' * 61500) + '"}'
        $json = ConvertTo-PayloadJson ([ordered]@{ 'src/data/team/a.json' = $big })
        $r = Test-ContentApplyPayload -ContentJson $json -Policy $script:Policy
        ($r.Errors -join "`n") | Should -Match 'limit is 61440 bytes'
        @($r.Files).Count | Should -Be 0
    }

    It 'accepts a payload just under the budget' {
        $prefix = ConvertTo-PayloadJson ([ordered]@{ 'src/data/team/a.json' = '{"note":""}' })
        $room = $script:Policy.MaxTotalBytes - [System.Text.Encoding]::UTF8.GetByteCount($prefix)
        $json = ConvertTo-PayloadJson ([ordered]@{ 'src/data/team/a.json' = '{"note":"' + ('x' * $room) + '"}' })
        [System.Text.Encoding]::UTF8.GetByteCount($json) | Should -Be $script:Policy.MaxTotalBytes
        (Test-ContentApplyPayload -ContentJson $json -Policy $script:Policy).Errors | Should -BeNullOrEmpty
    }

    It 'refuses a duplicate key that ConvertFrom-Json would silently collapse' {
        $json = '{"src/data/team/a.json":"{\"name\":\"shown\"}","src/data/team/a.json":"{\"name\":\"written\"}"}'
        $r = Test-ContentApplyPayload -ContentJson $json -Policy $script:Policy
        ($r.Errors -join "`n") | Should -Match 'more than once'
    }

    It 'refuses two paths that differ only by case' {
        $json = '{"src/data/team/a.json":"{}","src/data/team/A.json":"{}"}'
        $r = Test-ContentApplyPayload -ContentJson $json -Policy $script:Policy
        ($r.Errors -join "`n") | Should -Match 'collides'
    }

    It 'refuses <Why>' -ForEach @(
        @{ Why = 'an empty input'; Json = '' }
        @{ Why = 'invalid JSON'; Json = '{"a":' }
        @{ Why = 'a JSON array'; Json = '["src/data/team.ts"]' }
        @{ Why = 'a JSON string'; Json = '"src/data/team.ts"' }
        @{ Why = 'an empty object'; Json = '{}' }
        @{ Why = 'a non-string value'; Json = '{"src/data/team/a.json":{"name":"x"}}' }
        @{ Why = 'a null value'; Json = '{"src/data/team/a.json":null}' }
        @{ Why = 'a refused path'; Json = '{".github/workflows/x.yml":"on: push"}' }
    ) {
        (Test-ContentApplyPayload -ContentJson $Json -Policy $script:Policy).Errors | Should -Not -BeNullOrEmpty
    }

    It 'refuses more files than max_files' {
        $map = [ordered]@{}
        1..($script:Policy.MaxFiles + 1) | ForEach-Object { $map["src/data/team/m$_.json"] = '{}' }
        $r = Test-ContentApplyPayload -ContentJson (ConvertTo-PayloadJson $map) -Policy $script:Policy
        ($r.Errors -join "`n") | Should -Match "limit is $($script:Policy.MaxFiles)"
    }

    It 'reports every refused path, not just the first' {
        $json = ConvertTo-PayloadJson ([ordered]@{ 'package.json' = '{}'; 'public/CNAME' = 'x.org'; 'src/data/team/a.json' = '{}' })
        $r = Test-ContentApplyPayload -ContentJson $json -Policy $script:Policy
        @($r.Errors).Count | Should -Be 2
        @($r.Files).Count | Should -Be 1
    }
}

Describe 'Invoke-ContentApplyValidation' {
    BeforeAll {
        $script:GoodJson = '{"src/data/team/a.json":"{\"name\":\"Jane\"}"}'
    }

    It 'passes good inputs and defaults a blank title' {
        $v = Invoke-ContentApplyValidation -TargetRepo 'FFC-EX-example.org' -ContentJson $script:GoodJson -Title '' -IssueUrl '' -Policy $script:Policy
        $v.Ok | Should -BeTrue
        $v.Title | Should -Be 'Update site content'
    }

    It 'accepts a FreeForCharity issue URL' {
        $v = Invoke-ContentApplyValidation -TargetRepo 'FFC-EX-example.org' -ContentJson $script:GoodJson -Title 'Initial content' -IssueUrl 'https://github.com/FreeForCharity/FFC-IN-ffcadmin.org/issues/42' -Policy $script:Policy
        $v.Ok | Should -BeTrue
        $v.Title | Should -Be 'Initial content'
    }

    It 'refuses <Why>' -ForEach @(
        @{ Why = 'a non-FFC-EX repo'; Repo = 'FFC-IN-ffcadmin.org'; Title = 't'; Issue = '' }
        @{ Why = 'an issue URL outside the org'; Repo = 'FFC-EX-a.org'; Title = 't'; Issue = 'https://github.com/evil/repo/issues/1' }
        @{ Why = 'an issue URL that is not an issue'; Repo = 'FFC-EX-a.org'; Title = 't'; Issue = 'https://github.com/FreeForCharity/x/pull/1' }
        @{ Why = 'a multi-line title'; Repo = 'FFC-EX-a.org'; Title = "a`nb"; Issue = '' }
        @{ Why = 'an over-long title'; Repo = 'FFC-EX-a.org'; Title = ('t' * 201); Issue = '' }
    ) {
        $v = Invoke-ContentApplyValidation -TargetRepo $Repo -ContentJson $script:GoodJson -Title $Title -IssueUrl $Issue -Policy $script:Policy
        $v.Ok | Should -BeFalse
        $v.Errors | Should -Not -BeNullOrEmpty
    }
}

Describe 'Write-ContentApplyFileSet' {
    BeforeEach {
        $script:Repo = Join-Path ([System.IO.Path]::GetTempPath()) ('cap-repo-{0}' -f [guid]::NewGuid())
        New-Item -ItemType Directory -Path (Join-Path $script:Repo 'src/data/team') -Force | Out-Null
        [System.IO.File]::WriteAllText((Join-Path $script:Repo 'src/data/team.ts'), "export const team = []`n")
        [System.IO.File]::WriteAllText((Join-Path $script:Repo 'src/data/team/a.json'), '{"name":"A"}')
    }
    AfterEach {
        Remove-Item -LiteralPath $script:Repo -Recurse -Force -ErrorAction SilentlyContinue
    }

    It 'reports create, update and unchanged and writes UTF-8 without a BOM' {
        $files = @(
            [pscustomobject]@{ Path = 'src/data/team/b.json'; Bytes = 12; Content = '{"name":"B"}' }
            [pscustomobject]@{ Path = 'src/data/team.ts'; Bytes = 10; Content = "export const team = [1]`n" }
            [pscustomobject]@{ Path = 'src/data/team/a.json'; Bytes = 12; Content = '{"name":"A"}' }
        )
        $report = Write-ContentApplyFileSet -RepoPath $script:Repo -Files $files
        @($report | ForEach-Object { $_.Status }) | Should -Be @('create', 'update', 'unchanged')
        $bytes = [System.IO.File]::ReadAllBytes((Join-Path $script:Repo 'src/data/team/b.json'))
        $bytes[0] | Should -Be ([byte][char]'{')
        [System.IO.File]::ReadAllText((Join-Path $script:Repo 'src/data/team.ts')) | Should -BeExactly "export const team = [1]`n"
    }

    It 'creates missing directories under the allowlisted root' {
        $files = @([pscustomobject]@{ Path = 'src/data/faqs/q1.json'; Bytes = 2; Content = '{}' })
        $report = Write-ContentApplyFileSet -RepoPath $script:Repo -Files $files
        $report[0].Status | Should -Be 'create'
        Test-Path -LiteralPath (Join-Path $script:Repo 'src/data/faqs/q1.json') | Should -BeTrue
    }

    It 'refuses a path that resolves outside the checkout' {
        $files = @([pscustomobject]@{ Path = '../escape.json'; Bytes = 2; Content = '{}' })
        { Write-ContentApplyFileSet -RepoPath $script:Repo -Files $files } | Should -Throw '*outside the checkout*'
    }

    It 'refuses to write over a directory' {
        $files = @([pscustomobject]@{ Path = 'src/data/team'; Bytes = 2; Content = '{}' })
        { Write-ContentApplyFileSet -RepoPath $script:Repo -Files $files } | Should -Throw '*directory already exists*'
    }

    It 'refuses to write through a symbolic link in the target repo' {
        $outside = Join-Path ([System.IO.Path]::GetTempPath()) ('cap-outside-{0}' -f [guid]::NewGuid())
        New-Item -ItemType Directory -Path $outside | Out-Null
        $link = Join-Path $script:Repo 'src/data/linked'
        try {
            $made = $true
            try {
                New-Item -ItemType SymbolicLink -Path $link -Target $outside -ErrorAction Stop | Out-Null
            }
            catch {
                $made = $false
            }
            if (-not $made) {
                Set-ItResult -Skipped -Because 'this host cannot create symbolic links (CI on Linux can)'
                return
            }
            $files = @([pscustomobject]@{ Path = 'src/data/linked/x.json'; Bytes = 2; Content = '{}' })
            { Write-ContentApplyFileSet -RepoPath $script:Repo -Files $files } | Should -Throw '*symbolic link*'
            Test-Path -LiteralPath (Join-Path $outside 'x.json') | Should -BeFalse
        }
        finally {
            Remove-Item -LiteralPath $outside -Recurse -Force -ErrorAction SilentlyContinue
        }
    }

    It 'writes nothing under -WhatIf but still reports the plan' {
        $files = @([pscustomobject]@{ Path = 'src/data/team/c.json'; Bytes = 2; Content = '{}' })
        $report = Write-ContentApplyFileSet -RepoPath $script:Repo -Files $files -WhatIf
        $report[0].Status | Should -Be 'create'
        Test-Path -LiteralPath (Join-Path $script:Repo 'src/data/team/c.json') | Should -BeFalse
    }
}

Describe 'Get-ContentApplyAutoMergePlan' {
    BeforeAll {
        # Shapes as the REST API returns them. The FFC-EX org ruleset read on
        # 2026-10-11 (FFC-EX-iwilf.org, FFC-EX-canary): allow_auto_merge false,
        # a pull_request rule with allowed_merge_methods ['merge'], and no
        # required status checks.
        $script:RepoOn = [pscustomobject]@{ allow_auto_merge = $true; allow_squash_merge = $true; allow_merge_commit = $true; allow_rebase_merge = $true }
        $script:RepoOff = [pscustomobject]@{ allow_auto_merge = $false; allow_squash_merge = $true; allow_merge_commit = $true; allow_rebase_merge = $true }
        $script:PrMergeOnly = [pscustomobject]@{ type = 'pull_request'; parameters = [pscustomobject]@{ allowed_merge_methods = @('merge') } }
        $script:Checks = [pscustomobject]@{ type = 'required_status_checks'; parameters = [pscustomobject]@{ required_status_checks = @([pscustomobject]@{ context = 'CI' }) } }
    }

    It 'does nothing when auto_merge was not requested' {
        $p = Get-ContentApplyAutoMergePlan -Requested $false -Repo $script:RepoOn -Rules @($script:Checks)
        $p.Enable | Should -BeFalse
        $p.Reason | Should -Match 'auto_merge=false'
    }

    It 'falls back when the repo does not allow auto-merge (the FFC-EX setting today)' {
        $p = Get-ContentApplyAutoMergePlan -Requested $true -Repo $script:RepoOff -Rules @($script:Checks, $script:PrMergeOnly)
        $p.Enable | Should -BeFalse
        $p.Reason | Should -Match 'not enabled on this repository'
    }

    It 'refuses when the branch requires no status checks (auto-merge would merge before CI)' {
        $p = Get-ContentApplyAutoMergePlan -Requested $true -Repo $script:RepoOn -Rules @($script:PrMergeOnly)
        $p.Enable | Should -BeFalse
        $p.Reason | Should -Match 'requires no status checks'
    }

    It 'refuses when classic protection could not be read and no ruleset requires checks' {
        $p = Get-ContentApplyAutoMergePlan -Requested $true -Repo $script:RepoOn -Rules @() -ClassicProtectionKnown $false
        $p.Enable | Should -BeFalse
        $p.Reason | Should -Match 'unknown'
    }

    It 'honours the ruleset merge-method restriction over the repo preference' {
        $p = Get-ContentApplyAutoMergePlan -Requested $true -Repo $script:RepoOn -Rules @($script:Checks, $script:PrMergeOnly)
        $p.Enable | Should -BeTrue
        $p.Method | Should -Be 'merge'
    }

    It 'prefers squash when everything allows it' {
        $p = Get-ContentApplyAutoMergePlan -Requested $true -Repo $script:RepoOn -Rules @($script:Checks)
        $p.Method | Should -Be 'squash'
    }

    It 'accepts classic branch-protection checks as the CI requirement' {
        $p = Get-ContentApplyAutoMergePlan -Requested $true -Repo $script:RepoOn -Rules @() -ClassicRequiredChecks @('build')
        $p.Enable | Should -BeTrue
    }

    It 'accepts a required-workflows rule as the CI requirement' {
        $wf = [pscustomobject]@{ type = 'workflows'; parameters = [pscustomobject]@{ workflows = @([pscustomobject]@{ path = '.github/workflows/ci.yml' }) } }
        (Get-ContentApplyAutoMergePlan -Requested $true -Repo $script:RepoOn -Rules @($wf)).Enable | Should -BeTrue
    }

    It 'refuses when no merge method survives settings and rulesets' {
        $repo = [pscustomobject]@{ allow_auto_merge = $true; allow_squash_merge = $true; allow_merge_commit = $false; allow_rebase_merge = $false }
        $p = Get-ContentApplyAutoMergePlan -Requested $true -Repo $repo -Rules @($script:Checks, $script:PrMergeOnly)
        $p.Enable | Should -BeFalse
        $p.Reason | Should -Match 'no merge method'
    }
}

Describe 'Get-ContentApplyLandDecision' {
    BeforeAll {
        $script:Sha = 'abc123'
        function New-Pull([string]$State = 'open', [bool]$Merged = $false, $Mergeable = $true, [string]$Head = 'abc123') {
            [pscustomobject]@{ state = $State; merged = $Merged; mergeable = $Mergeable; mergeable_state = 'clean'; head = [pscustomobject]@{ sha = $Head } }
        }
        function New-Run([string]$Name, [string]$Status = 'completed', [string]$Conclusion = 'success') {
            [pscustomobject]@{ name = $Name; status = $Status; conclusion = $Conclusion }
        }
    }

    It 'merges when every check passed and the PR is open, mergeable and unchanged' {
        $d = Get-ContentApplyLandDecision -Pull (New-Pull) -ExpectedSha $Sha -CheckRuns @((New-Run 'build'), (New-Run 'lint' -Conclusion 'skipped'))
        $d.Action | Should -Be 'merge'
    }
    It 'waits while no check has reported yet (never merges on zero evidence)' {
        (Get-ContentApplyLandDecision -Pull (New-Pull) -ExpectedSha $Sha).Action | Should -Be 'wait'
    }
    It 'waits while a check is still running' {
        (Get-ContentApplyLandDecision -Pull (New-Pull) -ExpectedSha $Sha -CheckRuns @((New-Run 'build' -Status 'in_progress' -Conclusion $null))).Action | Should -Be 'wait'
    }
    It 'waits while a commit status is pending' {
        (Get-ContentApplyLandDecision -Pull (New-Pull) -ExpectedSha $Sha -Statuses @([pscustomobject]@{ context = 'ci'; state = 'pending' })).Action | Should -Be 'wait'
    }
    It 'waits while mergeability is not computed' {
        (Get-ContentApplyLandDecision -Pull (New-Pull -Mergeable $null) -ExpectedSha $Sha -CheckRuns @((New-Run 'build'))).Action | Should -Be 'wait'
    }
    It 'leaves the PR when a check failed' {
        $d = Get-ContentApplyLandDecision -Pull (New-Pull) -ExpectedSha $Sha -CheckRuns @((New-Run 'build' -Conclusion 'failure'), (New-Run 'lint'))
        $d.Action | Should -Be 'leave'
        $d.Reason | Should -Match 'build=failure'
    }
    It 'leaves the PR when a check timed out or was cancelled' {
        foreach ($c in 'timed_out', 'cancelled', 'action_required') {
            (Get-ContentApplyLandDecision -Pull (New-Pull) -ExpectedSha $Sha -CheckRuns @((New-Run 'build' -Conclusion $c))).Action | Should -Be 'leave'
        }
    }
    It 'leaves the PR when a commit status failed' {
        (Get-ContentApplyLandDecision -Pull (New-Pull) -ExpectedSha $Sha -Statuses @([pscustomobject]@{ context = 'ci'; state = 'error' })).Action | Should -Be 'leave'
    }
    It 'leaves the PR when its head moved after 707 pushed' {
        $d = Get-ContentApplyLandDecision -Pull (New-Pull -Head 'def456') -ExpectedSha $Sha -CheckRuns @((New-Run 'build'))
        $d.Action | Should -Be 'leave'
        $d.Reason | Should -Match 'head moved'
    }
    It 'leaves a closed or already merged PR' {
        (Get-ContentApplyLandDecision -Pull (New-Pull -State 'closed') -ExpectedSha $Sha -CheckRuns @((New-Run 'build'))).Action | Should -Be 'leave'
        (Get-ContentApplyLandDecision -Pull (New-Pull -Merged $true) -ExpectedSha $Sha -CheckRuns @((New-Run 'build'))).Action | Should -Be 'leave'
    }
    It 'leaves a PR GitHub reports as not mergeable' {
        (Get-ContentApplyLandDecision -Pull (New-Pull -Mergeable $false) -ExpectedSha $Sha -CheckRuns @((New-Run 'build'))).Action | Should -Be 'leave'
    }
}
