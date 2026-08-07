<#
.SYNOPSIS
    Pester (v5) unit tests for whmcs-client-field-populate.ps1.

.DESCRIPTION
    Dot-sources the script (which returns after its function definitions when
    dot-sourced) and tests the pure mapping/staging logic: pipe-name
    normalisation, map building, and the write/skip/nochange/overwrite decisions.
    No network or WHMCS calls are made.

    Run locally: Invoke-Pester -Path scripts/tests -Output Detailed
#>

BeforeAll {
    . (Join-Path (Split-Path $PSScriptRoot -Parent) 'whmcs-client-field-populate.ps1')
}

Describe 'Get-NormNames' {
    It 'yields raw, before-pipe, and after-pipe forms, casefolded + trimmed' {
        $n = Get-NormNames 'brand-color|Brand color (hex)'
        $n | Should -Contain 'brand-color'
        $n | Should -Contain 'brand color (hex)'
        $n | Should -Contain 'brand-color|brand color (hex)'
    }
    It 'dedupes a name with no pipe' {
        @(Get-NormNames 'Mission').Count | Should -Be 1
        Get-NormNames 'Mission' | Should -Be 'mission'
    }
}

Describe 'ConvertTo-MapLookup' {
    It 'skips meta keys and normalises product keys' {
        $obj = [pscustomobject]@{ '_comment' = 'ignore me'; 'mission' = 'Brief mission statement' }
        $lk = ConvertTo-MapLookup -MapObject $obj
        $lk.ContainsKey('_comment') | Should -Be $false
        $lk['mission'] | Should -Be 'Brief mission statement'
    }
}

Describe 'Get-PopulatePlan' {
    BeforeAll {
        $script:clientFields = @{
            'brief mission statement' = @{ id = '189'; value = '' }
            'brand color (hex)'       = @{ id = '222'; value = '#0567B1' }
            'x (twitter) url'         = @{ id = '198'; value = '' }
        }
        $script:map = @{ 'mission' = 'Brief mission statement'; 'brand-color' = 'Brand color (hex)'; 'social-x' = 'X (Twitter) URL' }
    }

    It 'stages a write when the client field is empty' {
        $ans = @{ 'mission' = 'We rescue dogs.' }
        $r = Get-PopulatePlan -Answers $ans -ClientFields $script:clientFields -MapLookup $script:map
        $r.staged['189'] | Should -Be 'We rescue dogs.'
        ($r.plan | Where-Object action -eq 'write').Count | Should -Be 1
    }

    It 'skips (non-destructive) when the client field already has a different value' {
        $ans = @{ 'brand-color' = 'navy blue' }
        $r = Get-PopulatePlan -Answers $ans -ClientFields $script:clientFields -MapLookup $script:map
        $r.staged.Count | Should -Be 0
        ($r.plan | Where-Object action -eq 'skip').reason | Should -Match 'already set'
    }

    It 'overwrites an existing value only with -Overwrite' {
        $ans = @{ 'brand-color' = 'navy blue' }
        $r = Get-PopulatePlan -Answers $ans -ClientFields $script:clientFields -MapLookup $script:map -Overwrite
        $r.staged['222'] | Should -Be 'navy blue'
    }

    It 'reports an unmapped product field instead of guessing' {
        $ans = @{ 'some-unknown-field' = 'x' }
        $r = Get-PopulatePlan -Answers $ans -ClientFields $script:clientFields -MapLookup $script:map
        $r.unmapped | Should -Contain 'some-unknown-field'
        $r.staged.Count | Should -Be 0
    }

    It 'matches a client field by its label side of a machine|Label name' {
        $cf = @{ 'x (twitter) url' = @{ id = '198'; value = '' } }
        $ans = @{ 'social-x' = 'https://x.com/acme' }
        $r = Get-PopulatePlan -Answers $ans -ClientFields $cf -MapLookup @{ 'social-x' = 'X (Twitter) URL' }
        $r.staged['198'] | Should -Be 'https://x.com/acme'
    }

    It 'matches a machine|Label ANSWER against a map keyed on the machine-name' {
        $ans = @{ 'mission|brief mission statement' = 'We rescue dogs.' }
        $r = Get-PopulatePlan -Answers $ans -ClientFields $script:clientFields -MapLookup $script:map
        $r.staged['189'] | Should -Be 'We rescue dogs.'
        $r.unmapped.Count | Should -Be 0
    }

    It 'reports a pipe-named product field ONCE, not once per spelling' {
        # The label side is genuinely absent from the map, but it is the same
        # field as the machine side -- reporting both invites someone to "fix"
        # the map by adding a key that already matches.
        $ans = @{ 'not-in-map|Not In The Map' = 'x' }
        $r = Get-PopulatePlan -Answers $ans -ClientFields $script:clientFields -MapLookup $script:map
        $r.unmapped.Count | Should -Be 1
        $r.unmapped | Should -Contain 'not-in-map|Not In The Map'
    }

    It 'plans one row per client field when two product spellings resolve to the same target' {
        $ans = @{ 'mission' = 'We rescue dogs.'; 'mission|Brief mission statement' = 'We rescue dogs.' }
        $r = Get-PopulatePlan -Answers $ans -ClientFields $script:clientFields -MapLookup $script:map
        @($r.plan | Where-Object { $_.clientFieldId -eq '189' }).Count | Should -Be 1
        $r.staged.Count | Should -Be 1
    }
}

Describe 'whmcs-api-common call sites' {
    # The script's main body runs below the dot-source guard, so Pester cannot
    # execute it and no behavioural test in this repo can reach the call that
    # resolves -Email. It was shipped calling Find-WhmcsClientIdByEmail with
    # -Creds/-AccessKey against a signature that takes -Auth, which throws
    # "A parameter cannot be found that matches parameter name 'Creds'" before
    # any WHMCS call is made -- with the suite green throughout. Bind the
    # contract statically instead, so the class is caught, not the one instance.
    BeforeAll {
        $script:scriptsDir = Split-Path $PSScriptRoot -Parent
        $script:targetScript = Join-Path $script:scriptsDir 'whmcs-client-field-populate.ps1'

        function Get-FunctionParameterMap {
            param([Parameter(Mandatory = $true)][string]$Path)
            $ast = [System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$null, [ref]$null)
            $map = @{}
            $fns = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true)
            foreach ($fn in $fns) {
                $names = @()
                if ($fn.Body.ParamBlock) {
                    $names = @($fn.Body.ParamBlock.Parameters | ForEach-Object { $_.Name.VariablePath.UserPath })
                }
                $map[$fn.Name] = $names
            }
            return $map
        }

        $script:commonFns = Get-FunctionParameterMap -Path (Join-Path $script:scriptsDir 'whmcs-api-common.ps1')
    }

    It 'resolved the helper signatures it is about to check' {
        # Without this, a rename in whmcs-api-common.ps1 would empty the map and
        # the assertion below would pass by having nothing left to inspect.
        $script:commonFns.Keys | Should -Contain 'Find-WhmcsClientIdByEmail'
        $script:commonFns['Find-WhmcsClientIdByEmail'] | Should -Contain 'Auth'
    }

    It 'passes only parameter names the helper actually declares' {
        $commonParams = @(
            'Verbose', 'Debug', 'ErrorAction', 'WarningAction', 'InformationAction',
            'ErrorVariable', 'WarningVariable', 'InformationVariable', 'OutVariable',
            'OutBuffer', 'PipelineVariable', 'WhatIf', 'Confirm'
        )
        $ast = [System.Management.Automation.Language.Parser]::ParseFile($script:targetScript, [ref]$null, [ref]$null)
        $commands = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] }, $true)

        $checked = 0
        $bad = [System.Collections.Generic.List[string]]::new()
        foreach ($cmd in $commands) {
            $name = $cmd.GetCommandName()
            if (-not $name -or -not $script:commonFns.ContainsKey($name)) { continue }
            $valid = @($script:commonFns[$name]) + $commonParams
            foreach ($el in $cmd.CommandElements) {
                if ($el -isnot [System.Management.Automation.Language.CommandParameterAst]) { continue }
                $checked++
                $p = $el.ParameterName
                # PowerShell accepts unambiguous prefixes, so match on prefix.
                if (-not ($valid | Where-Object { $_ -like "$p*" })) {
                    [void]$bad.Add("$name -$p (line $($el.Extent.StartLineNumber))")
                }
            }
        }

        # Guards against the check passing because it inspected nothing.
        $checked | Should -BeGreaterThan 0
        ($bad -join '; ') | Should -BeNullOrEmpty
    }
}
