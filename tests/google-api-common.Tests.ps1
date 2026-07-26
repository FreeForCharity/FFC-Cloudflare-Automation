# Regression tests for Get-GoogleRows in scripts/google-api-common.ps1.
#
# As in google-telemetry-reachability.Tests.ps1: every call goes through a helper that sets
# `Set-StrictMode -Version Latest` immediately before invoking. StrictMode is dynamically
# scoped and Pester resets it per It block, so setting it at file top does nothing inside the
# tests — and without it, every case here passes against the broken implementation.
#
# Two defects are covered, both of which reached production:
#
#   1. `.PSObject.Properties.Name -contains 'rows'` threw on a response with no properties.
#   2. `return @()` returns $null, not an empty array — PowerShell unrolls it on the way out.
#      The caller's `$rows.Count` then threw, so a property with genuinely ZERO sessions was
#      reported as a probe FAILURE. In a rollout ordered by traffic that is the expensive
#      direction to be wrong in: a quiet site and an unmeasured site are handled differently.

BeforeAll {
    . (Join-Path $PSScriptRoot '..' 'scripts' 'google-api-common.ps1')

    function Invoke-GetGoogleRowsUnderStrictMode {
        param([Parameter(Mandatory)]$Response)
        Set-StrictMode -Version Latest
        # .Count is deliberately touched HERE, in the caller, because that is where the second
        # defect surfaced — the helper returned $null and the caller blew up on it.
        $rows = Get-GoogleRows -Response $Response
        return $rows.Count
    }
}

Describe 'Get-GoogleRows' {

    Context 'responses that carry no rows' {
        It 'returns an empty array for a bare {} response' {
            # The shape that threw "The property 'Name' cannot be found on this object".
            Invoke-GetGoogleRowsUnderStrictMode -Response ('{}' | ConvertFrom-Json) | Should -Be 0
        }

        It 'returns an empty array when the rows field is absent' {
            # What the GA4 Data API actually sends for a property with no data in range.
            Invoke-GetGoogleRowsUnderStrictMode -Response ('{"metadata":{"currencyCode":"USD"}}' | ConvertFrom-Json) |
                Should -Be 0
        }

        It 'returns an empty array when rows is present but empty' {
            Invoke-GetGoogleRowsUnderStrictMode -Response ('{"rows":[]}' | ConvertFrom-Json) | Should -Be 0
        }
    }

    Context 'responses that carry rows' {
        It 'returns one row without unrolling it to a scalar' {
            # A single-element array unrolls to a bare object unless re-wrapped, and .Count on
            # a PSCustomObject is 1 by coincidence rather than by correctness — assert the type.
            Set-StrictMode -Version Latest
            $rows = Get-GoogleRows -Response ('{"rows":[{"a":1}]}' | ConvertFrom-Json)
            $rows | Should -BeOfType [System.Object]
            @($rows).Count | Should -Be 1
            Invoke-GetGoogleRowsUnderStrictMode -Response ('{"rows":[{"a":1}]}' | ConvertFrom-Json) |
                Should -Be 1
        }

        It 'returns every row for a multi-row response' {
            Invoke-GetGoogleRowsUnderStrictMode -Response ('{"rows":[{"a":1},{"a":2},{"a":3}]}' | ConvertFrom-Json) |
                Should -Be 3
        }
    }

    Context 'the return value is a real array, not $null' {
        It 'does not unroll the empty case away' {
            # The heart of defect 2. `return @()` would make this $null.
            Set-StrictMode -Version Latest
            $rows = Get-GoogleRows -Response ('{}' | ConvertFrom-Json)

            # NOT `Should -Not -BeNullOrEmpty` — that treats an empty array as empty and fails
            # against the CORRECT implementation, which is exactly what an empty array is
            # supposed to be. The distinction under test is $null vs empty-array, so assert on
            # the type: only a real array has .IsArray, and only a non-$null value has a type.
            $null -eq $rows | Should -BeFalse -Because '@() unrolls to $null on return; ,@() does not'
            $rows.GetType().IsArray | Should -BeTrue
            $rows.Count | Should -Be 0
        }
    }

    Context 'the implementation avoids the forms that threw' {
        It 'uses the indexer and the comma operator' {
            $src = Get-Content -Raw (Join-Path $PSScriptRoot '..' 'scripts' 'google-api-common.ps1')
            $ast = [System.Management.Automation.Language.Parser]::ParseInput($src, [ref]$null, [ref]$null)
            $fn = $ast.Find({
                    param($n)
                    $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
                    $n.Name -eq 'Get-GoogleRows'
                }, $true)

            $body = $fn.Body.EndBlock.Extent.Text
            $body | Should -Not -Match '\.Properties\.Name'
            $body | Should -Not -Match 'return\s+@\(\)'
        }
    }
}
