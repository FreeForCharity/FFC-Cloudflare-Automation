# Regression tests for Test-HasProperty in scripts/google-telemetry-reachability.ps1.
#
# READ THIS BEFORE EDITING: every behavioural test here goes through
# Invoke-HasPropertyUnderStrictMode, which sets `Set-StrictMode -Version Latest`
# immediately before calling. That is not ceremony — it is the entire test.
#
# StrictMode is DYNAMICALLY scoped: it must be in effect in the caller's scope at the
# moment of the call, not merely somewhere in the file. Pester runs each It block in
# its own scope with StrictMode reset, so a `Set-StrictMode` at the top of this file
# has no effect inside the tests. A first draft of these tests did exactly that and
# passed 9/9 against the BROKEN implementation — certifying the bug rather than
# catching it. Calling directly is worse than having no test at all.
#
# The condition being reproduced: google-api-common.ps1 sets
# `Set-StrictMode -Version Latest` at module scope, and google-telemetry-reachability.ps1
# inherits it by dot-sourcing. Under StrictMode, enumerating `.Name` across an EMPTY
# PSMemberInfoCollection throws "The property 'Name' cannot be found on this object".

BeforeAll {
    $scriptPath = Join-Path $PSScriptRoot '..' 'scripts' 'google-telemetry-reachability.ps1'
    $script:SourcePath = (Resolve-Path $scriptPath).Path

    # Extract the function rather than dot-sourcing the script, which would run a full
    # live API sweep. Parsed from the AST rather than regexed so the test cannot drift
    # into asserting against a stale copy of the source.
    $ast = [System.Management.Automation.Language.Parser]::ParseFile(
        $script:SourcePath, [ref]$null, [ref]$null)
    $fn = $ast.Find({
            param($n)
            $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
            $n.Name -eq 'Test-HasProperty'
        }, $true)

    if (-not $fn) { throw 'Test-HasProperty not found in google-telemetry-reachability.ps1' }
    . ([scriptblock]::Create($fn.Extent.Text))

    # The only correct way to call it from here. See the file header.
    function Invoke-HasPropertyUnderStrictMode {
        param($Object, [Parameter(Mandatory)][string]$Name)
        Set-StrictMode -Version Latest
        Test-HasProperty -Object $Object -Name $Name
    }
}

Describe 'Test-HasProperty' {

    Context 'the empty-object case that broke the GA4 inventory' {
        # The GA4 Admin API returns {} for a property with no data streams. One such
        # property aborted the entire GA4 sweep, so fleet traffic ranking reported
        # nothing for weeks while GTM and Search Console reported fine.
        It 'returns false instead of throwing for an empty JSON object' {
            $empty = '{}' | ConvertFrom-Json
            { Invoke-HasPropertyUnderStrictMode -Object $empty -Name 'dataStreams' } |
                Should -Not -Throw
            Invoke-HasPropertyUnderStrictMode -Object $empty -Name 'dataStreams' |
                Should -BeFalse
        }

        It 'returns false for an empty PSCustomObject built directly' {
            Invoke-HasPropertyUnderStrictMode -Object ([pscustomobject]@{}) -Name 'anything' |
                Should -BeFalse
        }
    }

    Context 'ordinary cases still work' {
        It 'finds a property that is present' {
            $o = '{"dataStreams":[1,2]}' | ConvertFrom-Json
            Invoke-HasPropertyUnderStrictMode -Object $o -Name 'dataStreams' | Should -BeTrue
        }

        It 'does not find a property absent from a populated object' {
            $o = '{"other":1}' | ConvertFrom-Json
            Invoke-HasPropertyUnderStrictMode -Object $o -Name 'dataStreams' | Should -BeFalse
        }

        It 'is case-insensitive, matching PowerShell property access' {
            $o = '{"nextPageToken":"abc"}' | ConvertFrom-Json
            Invoke-HasPropertyUnderStrictMode -Object $o -Name 'nextpagetoken' | Should -BeTrue
        }
    }

    Context 'the null and array cases the guard was originally written for' {
        It 'returns false for $null' {
            Invoke-HasPropertyUnderStrictMode -Object $null -Name 'accounts' | Should -BeFalse
        }

        It 'returns false for an empty array' {
            Invoke-HasPropertyUnderStrictMode -Object @() -Name 'accounts' | Should -BeFalse
        }

        It 'does not throw for an array of objects' {
            $arr = '[{"a":1},{"a":2}]' | ConvertFrom-Json
            { Invoke-HasPropertyUnderStrictMode -Object $arr -Name 'a' } | Should -Not -Throw
        }

        It 'returns false for a scalar' {
            Invoke-HasPropertyUnderStrictMode -Object 'a string' -Name 'accounts' | Should -BeFalse
        }
    }

    Context 'the implementation avoids the enumerating form' {
        # Once both forms are correct under the tests above, behaviour alone cannot tell
        # them apart — but the enumerating form is a live trap under StrictMode. Assert
        # it is gone so it cannot return as a "simplification".
        It 'uses the indexer, not .Properties.Name' {
            $ast = [System.Management.Automation.Language.Parser]::ParseFile(
                $script:SourcePath, [ref]$null, [ref]$null)
            $fn = $ast.Find({
                    param($n)
                    $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
                    $n.Name -eq 'Test-HasProperty'
                }, $true)

            # Body only — the docstring names the broken form in order to explain it.
            $body = $fn.Body.EndBlock.Extent.Text
            $body | Should -Not -Match '\.Properties\.Name'
            $body | Should -Match '\.Properties\['
        }
    }
}
