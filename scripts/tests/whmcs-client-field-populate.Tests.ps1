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
}
