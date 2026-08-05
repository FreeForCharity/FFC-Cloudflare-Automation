# Regression tests for the StrictMode-safety of the Google telemetry helpers:
# Test-HasProperty in scripts/google-telemetry-reachability.ps1, and Get-GoogleRows in
# scripts/google-api-common.ps1.
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
    $commonPath = Join-Path $PSScriptRoot '..' 'scripts' 'google-api-common.ps1'
    $script:CommonPath = (Resolve-Path $commonPath).Path

    # Extract the function rather than dot-sourcing the script, which would run a full
    # live API sweep. Parsed from the AST rather than regexed so the test cannot drift
    # into asserting against a stale copy of the source.
    function Get-FunctionFromFile {
        param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$Name)
        $ast = [System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$null, [ref]$null)
        $fn = $ast.Find({
                param($n)
                $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
                $n.Name -eq $Name
            }, $true)
        if (-not $fn) { throw "$Name not found in $Path" }
        return $fn
    }

    . ([scriptblock]::Create((Get-FunctionFromFile -Path $script:SourcePath -Name 'Test-HasProperty').Extent.Text))
    . ([scriptblock]::Create((Get-FunctionFromFile -Path $script:CommonPath -Name 'Get-GoogleRows').Extent.Text))
    . ([scriptblock]::Create((Get-FunctionFromFile -Path $script:SourcePath -Name 'Invoke-GooglePagedApi').Extent.Text))

    # Stub for the one call Invoke-GooglePagedApi makes. $script:Pages is the queue of responses it
    # serves, one per call, so a test can drive multi-page and empty-body cases without a network.
    #
    # Each page is BOXED as a hashtable with a 'body' key. That is not decoration: a 200 with an
    # empty body is one of the cases under test, and PowerShell's parameter binder unwraps a
    # single-element array and then refuses to bind the $null inside it, so a bare @($null) page
    # list cannot be passed at all. The box carries the $null through intact.
    #
    # Every requested URI is recorded, so a test can assert the page token was actually sent
    # rather than trusting that two calls happened.
    function Invoke-GoogleApi {
        param([string]$Uri, [string]$AccessToken)
        $script:CalledUris += $Uri
        $script:CalledTokens += $AccessToken
        $i = $script:PageIndex
        $script:PageIndex++
        if ($i -ge $script:Pages.Count) { throw "stub: unexpected extra API call (page $i)" }
        return $script:Pages[$i].body
    }

    function Measure-PagedApiUnderStrictMode {
        param([Parameter(Mandatory)][hashtable[]]$Pages, [string]$CollectionName = 'accounts')
        Set-StrictMode -Version Latest
        $script:Pages = $Pages
        $script:PageIndex = 0
        $script:CalledUris = @()
        $script:CalledTokens = @()
        $items = Invoke-GooglePagedApi -Uri 'https://example.invalid/x' -AccessToken 't' -CollectionName $CollectionName
        # .Count is read exactly as Get-GtmInventory reads it — that access IS the failure mode.
        return [pscustomobject]@{
            items   = $items
            count   = $items.Count
            isArray = ($items -is [array])
            uris    = $script:CalledUris
            tokens  = $script:CalledTokens
        }
    }

    # The only correct way to call it from here. See the file header.
    function Invoke-HasPropertyUnderStrictMode {
        param($Object, [Parameter(Mandatory)][string]$Name)
        Set-StrictMode -Version Latest
        Test-HasProperty -Object $Object -Name $Name
    }

    # Same discipline for Get-GoogleRows, and for the same reason: the defect it guards
    # against only exists under StrictMode, and StrictMode is dynamically scoped.
    function Invoke-GoogleRowsUnderStrictMode {
        param($Response)
        Set-StrictMode -Version Latest
        Get-GoogleRows -Response $Response
    }

    # Reads .Count the way the production call sites do — Get-Ga4Sessions' `if ($rows.Count -gt 0)`
    # and 502's `if ($respRows.Count)`. That property access IS the failure, so a test that only
    # calls the function and never touches .Count would pass against the broken implementation.
    function Measure-GoogleRowsUnderStrictMode {
        param($Response)
        Set-StrictMode -Version Latest
        $rows = Get-GoogleRows -Response $Response
        return $rows.Count
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

Describe 'Get-GoogleRows' {
    # The crash behind #875: 506 failed on every scheduled run because two charity properties had
    # no sessions in the lookback window. `return @()` unrolls to $null on the way out of a
    # PowerShell function, so `$rows.Count` in Get-Ga4Sessions threw PropertyNotFoundException
    # instead of reading 0 — and a property with genuinely zero traffic was reported as a failed
    # probe, which is the one thing this report exists not to do. 502's Get-HeadlineMetrics reads
    # `$respRows.Count` off the same helper and had the same latent crash.

    Context 'the empty-result shapes that broke 506' {
        It 'returns a real, countable array when the response omits rows entirely' {
            # What the Data API actually sends for a range with no data.
            $resp = '{"metadata":{"currencyCode":"USD"}}' | ConvertFrom-Json
            { Measure-GoogleRowsUnderStrictMode -Response $resp } | Should -Not -Throw
            Measure-GoogleRowsUnderStrictMode -Response $resp | Should -Be 0
        }

        It 'returns a real, countable array for an explicitly empty rows list' {
            $resp = '{"rows":[]}' | ConvertFrom-Json
            Measure-GoogleRowsUnderStrictMode -Response $resp | Should -Be 0
        }

        It 'returns a real, countable array for an empty JSON object' {
            # `{}` is the second trap in the same function: `.PSObject.Properties.Name -contains`
            # ENUMERATES, and enumerating an empty PSMemberInfoCollection throws under StrictMode —
            # so the presence guard was itself the thing that threw. Same finding as
            # Test-HasProperty above; this helper never got the fix.
            $resp = '{}' | ConvertFrom-Json
            { Measure-GoogleRowsUnderStrictMode -Response $resp } | Should -Not -Throw
            Measure-GoogleRowsUnderStrictMode -Response $resp | Should -Be 0
        }

        It 'is an array, not merely something with a Count' {
            # The distinction the bug turned on: $null and an empty array are both "empty" to
            # -BeNullOrEmpty, so the type check is the assertion that can tell them apart.
            $resp = '{"metadata":{}}' | ConvertFrom-Json
            $rows = Invoke-GoogleRowsUnderStrictMode -Response $resp
            $null -ne $rows | Should -BeTrue -Because 'an empty ARRAY is not $null, and that is the whole defect'
            $rows -is [array] | Should -BeTrue
            $rows.Count | Should -Be 0
        }
    }

    Context 'populated responses still read correctly' {
        It 'returns a one-element array for a single row' {
            # Pre-fix this returned a bare PSCustomObject and `$rows[0]` worked only by
            # PowerShell's scalar-indexing courtesy. The indexing the call sites do must be
            # indexing an array, not relying on that.
            $resp = '{"rows":[{"metricValues":[{"value":"7"},{"value":"3"}]}]}' | ConvertFrom-Json
            $rows = Invoke-GoogleRowsUnderStrictMode -Response $resp
            $rows -is [array] | Should -BeTrue
            $rows.Count | Should -Be 1
            $rows[0].metricValues[0].value | Should -Be '7'
        }

        It 'preserves every row and their order for a multi-row response' {
            $resp = '{"rows":[{"metricValues":[{"value":"7"}]},{"metricValues":[{"value":"9"}]}]}' | ConvertFrom-Json
            $rows = Invoke-GoogleRowsUnderStrictMode -Response $resp
            $rows.Count | Should -Be 2
            $rows[0].metricValues[0].value | Should -Be '7'
            $rows[1].metricValues[0].value | Should -Be '9'
        }
    }

    Context 'a null response is rejected, not silently counted as zero rows' {
        It 'refuses $null rather than reporting it as an empty result' {
            # A 200 with an EMPTY body deserializes to $null. That is an unexpected response, NOT
            # evidence of zero traffic — so it must surface as a probe error rather than become a
            # fabricated "0 sessions" in the traffic ranking. Mandatory is what enforces that, and
            # this test exists because the obvious "make it forgiving" edit ([AllowNull()] plus a
            # $null branch) would quietly convert an unreadable property into a measured zero.
            # I wrote exactly that branch first; it was unreachable, and this assertion is why.
            { Measure-GoogleRowsUnderStrictMode -Response $null } |
                Should -Throw -ExpectedMessage '*null*'
        }
    }

    Context 'the implementation keeps both remedies' {
        # Behaviour alone cannot distinguish the fixed form from a future "simplification" that
        # drops the unary comma, because a caller that only ever sees populated responses will
        # never notice. Pin both remedies in the source.
        # Every helper in this path that returns a collection, named explicitly. An exact list
        # rather than a heuristic: adding a sixth array-returning helper should be a deliberate
        # edit here, not something a pattern silently does or does not cover. Get-GoogleRows is
        # the one that actually crashed 506; the other four are the same shape, and two of them
        # (Invoke-GooglePagedApi, Get-GtmInventory) sit on a live `.Count`.
        It 'returns with the unary comma on every return path — <Function>' -ForEach @(
            @{ File = 'common'; Function = 'Get-GoogleRows' }
            @{ File = 'source'; Function = 'Invoke-GooglePagedApi' }
            @{ File = 'source'; Function = 'Get-GtmInventory' }
            @{ File = 'source'; Function = 'Get-Ga4Inventory' }
            @{ File = 'source'; Function = 'Get-GscInventory' }
        ) {
            $path = if ($File -eq 'common') { $script:CommonPath } else { $script:SourcePath }
            $fn = Get-FunctionFromFile -Path $path -Name $Function
            $body = $fn.Body.EndBlock.Extent.Text
            $returns = @([regex]::Matches($body, '(?m)^\s*return\s+[^\r\n]+') | ForEach-Object { $_.Value.Trim() })
            $returns.Count | Should -BeGreaterThan 0 -Because "$Function must have at least one return to check"
            foreach ($r in $returns) {
                $r | Should -Match '^return\s+,' -Because "$Function must wrap its collection: $r"
            }
        }

        It 'uses the indexer, not the enumerating .Properties.Name form' {
            $fn = Get-FunctionFromFile -Path $script:CommonPath -Name 'Get-GoogleRows'
            $body = $fn.Body.EndBlock.Extent.Text
            $body | Should -Not -Match '\.Properties\.Name'
            $body | Should -Match '\.Properties\['
        }
    }
}

Describe 'Invoke-GooglePagedApi' {
    # The same empty-array-unrolls-to-$null defect, one file over and still latent: Get-GtmInventory
    # reads `$raw.Count` off this function's return value. It has never crashed only because the GTM
    # and GA4 Admin list endpoints have always returned something. An account with no containers —
    # a new tenant, or a token whose grants were narrowed — is all it takes, and the failure would
    # look identical to #875's: a PropertyNotFoundException from a line that merely counts.

    Context 'empty results are still arrays' {
        It 'returns an empty array when the API answers with an empty body' {
            # A 200 with an empty body deserializes to $null. The function treats that as a clean
            # finish, so the result is zero items — and must be countable.
            $r = Measure-PagedApiUnderStrictMode -Pages @(@{ body = $null })
            $r.count | Should -Be 0
            $r.isArray | Should -BeTrue
        }

        It 'returns an empty array when the response omits the collection entirely' {
            $r = Measure-PagedApiUnderStrictMode -Pages @(@{ body = ('{"metadata":{}}' | ConvertFrom-Json) })
            $r.count | Should -Be 0
            $r.isArray | Should -BeTrue
        }

        It 'does not throw on .Count for an empty result' {
            # Stated separately from the count assertions because this is the production symptom:
            # not a wrong number, but an exception out of a counting expression.
            { Measure-PagedApiUnderStrictMode -Pages @(@{ body = ('{}' | ConvertFrom-Json) }) } | Should -Not -Throw
        }
    }

    Context 'populated and paginated results are unchanged' {
        It 'returns a one-element array for a single item' {
            $r = Measure-PagedApiUnderStrictMode -Pages @(@{ body = ('{"accounts":[{"name":"a/1"}]}' | ConvertFrom-Json) })
            $r.count | Should -Be 1
            $r.isArray | Should -BeTrue
            $r.items[0].name | Should -Be 'a/1'
        }

        It 'concatenates every page and stops when nextPageToken is gone' {
            $r = Measure-PagedApiUnderStrictMode -Pages @(
                @{ body = ('{"accounts":[{"name":"a/1"}],"nextPageToken":"t2"}' | ConvertFrom-Json) },
                @{ body = ('{"accounts":[{"name":"a/2"},{"name":"a/3"}]}' | ConvertFrom-Json) }
            )
            $r.count | Should -Be 3
            @($r.items | ForEach-Object { $_.name }) | Should -Be @('a/1', 'a/2', 'a/3')
            # Two calls is not evidence the token was followed — assert it was actually sent, and
            # that the first call did NOT carry one.
            $r.uris.Count | Should -Be 2
            $r.uris[0] | Should -Not -Match 'pageToken'
            $r.uris[1] | Should -Match 'pageToken=t2'
            $r.tokens | Should -Be @('t', 't')
        }
    }
}
