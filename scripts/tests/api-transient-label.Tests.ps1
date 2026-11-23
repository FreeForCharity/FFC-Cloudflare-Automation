<#
.SYNOPSIS
    Pester (v5) unit tests for Get-ApiTransientLabel and for the log-safety property
    the non-WHMCS retry loops in scripts/ have to hold.

.DESCRIPTION
    Regression cover for #1766, the non-WHMCS half of #1753.

    scripts/candid-api-common.ps1 and scripts/fraudlabspro-api-common.ps1 each carried
    a retry loop with two defects feeding each other, the same pair #1753 fixed for
    WHMCS:

    1. The transient signature ended in \b(429|502|503|504)\b, so a bare status number
       anywhere in prose classified a PERMANENT error as transient - punctuation and
       spaces satisfy \b. Measured on realistic exception messages: 'Order 502 declined
       by rule set' and 'Unauthorized: subscription key 503 is not valid' were each
       retried five times with exponential backoff.

    2. Every one of those attempts interpolated $_.Exception.Message into a
       Write-Warning, and FraudLabs Pro also rebuilt it into the terminal throw. This
       repository is public, so those Actions logs are world-readable. FraudLabs Pro is
       the fraud-screening path behind #1445 and handles order and IP data.

    The fix routes both the decision and the log through Get-ApiTransientLabel
    (scripts/api-transient-label.ps1), which returns a label from a closed set of
    literals and never any part of its input, and shape-anchors the status rules so a
    code counts only in a status-line shape.

    WHY A THIRD FILE IS IN SCOPE. The source guard at the end of this file discovers its
    population from the AST instead of from a hardcoded list, which is #1766's AC4 and
    the #1758/#1761 lesson. Discovery immediately found an instance no enumeration in
    the #1753 family could reach: scripts/cloudflare-api-common.ps1 logged $detail -
    which carries $_.Exception.Message and up to 500 characters of the response body -
    in its retry warning. Every earlier sweep scoped itself by WHMCS and then by the
    loose status alternation, and that file carries neither: its own status test
    ($statusCode -ge 500) was already structural and correct. A discovery guard cannot
    land while a discovered instance violates it, and the alternative - a file list - is
    exactly what AC4 rejects, so the warning was fixed there too. Its terminal throw is
    deliberately left alone and is out of this guard's scope; see Get-RetryLoopWarningLeak.

    Run locally: Invoke-Pester -Path scripts/tests -Output Detailed
#>

BeforeAll {
    $script:ScriptsDir = Split-Path $PSScriptRoot -Parent
    . (Join-Path $script:ScriptsDir 'api-transient-label.ps1')
    . (Join-Path $script:ScriptsDir 'candid-api-common.ps1')
    . (Join-Path $script:ScriptsDir 'fraudlabspro-api-common.ps1')

    # Stands in for everything an exception message can carry back from a response that
    # must never be repeated into a public log.
    $script:Sensitive = 'donor@example.org'

    # In the helper's host allowlist, so the credential guard passes and the request
    # reaches the mocked Invoke-RestMethod.
    $script:CandidUri = 'https://api.candid.org/essentials/v3'

    # The complete set of labels Get-ApiTransientLabel may return. The security property
    # is that the reason is drawn from THIS set rather than from the input, so the set is
    # asserted closed.
    $script:AllowedLabels = @(
        'rate limited',
        'gateway error',
        'service unavailable',
        'request timed out'
    )

    # A direct read of exception TEXT. Note what is excluded on purpose:
    # $_.Exception.Response.StatusCode is an integer off the response object, not text
    # from it, and it is the signal these loops are supposed to keep logging.
    $script:ExceptionTextRe = '\$_\.Exception\.Message|\$_\.ErrorDetails\.Message|\$_\.Exception\.ToString\(\)|\$_\.Exception\.InnerException\.Message'

    # Routing exception text through one of these is the remedy, so a value that came
    # out of one is clean however it was built.
    $script:SanitizerRe = 'Get-ApiTransientLabel|Get-WhmcsTransientLabel|Get-WhmcsResponseShape'

    # Sinks that run and then let the loop go round again. Write-Output is excluded for
    # the #1771 reason: it flags deliberate report objects, a different question.
    $script:SinkNames = @('Write-Warning', 'Write-Host', 'Write-Information', 'Write-Error')

    function Get-LoopCounterName {
        <#
            The variables a loop's body INCREMENTS: $x++, $x += n, and $x = $x + n.
            These are the attempt counters a backoff delay can grow with.

            Deliberately read from the loop BODY only, not from a `for` statement's
            iterator. m365-dkim.ps1:327 is `for ($i = 0; $i -lt 12; $i++)`, a poll loop
            whose counter lives in the iterator - so ignoring the iterator costs
            nothing here and keeps one more throttle shape out of the population.
        #>
        param([Parameter(Mandatory = $true)]$Loop)

        $names = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)

        foreach ($unary in $Loop.Body.FindAll({ param($n)
                    $n -is [System.Management.Automation.Language.UnaryExpressionAst] }, $true)) {
            if ($unary.TokenKind -notin @('PostfixPlusPlus', 'PlusPlus')) { continue }
            if ($unary.Child -is [System.Management.Automation.Language.VariableExpressionAst]) {
                $null = $names.Add($unary.Child.VariablePath.UserPath)
            }
        }

        foreach ($assign in $Loop.Body.FindAll({ param($n)
                    $n -is [System.Management.Automation.Language.AssignmentStatementAst] }, $true)) {
            if ($assign.Left -isnot [System.Management.Automation.Language.VariableExpressionAst]) { continue }
            $name = $assign.Left.VariablePath.UserPath
            if ($assign.Operator -eq 'PlusEquals') { $null = $names.Add($name); continue }
            # $x = $x + 1: self-referential, so it accumulates across iterations.
            if ($assign.Operator -eq 'Equals' -and
                $assign.Right.Extent.Text -match ('\$' + [regex]::Escape($name) + '\b')) {
                $null = $names.Add($name)
            }
        }

        return $names
    }

    function Test-AttemptDerivedDelay {
        <#
            True when a Start-Sleep's duration grows with a counter its loop increments
            - directly ($attempt), or through assignments inside the loop
            ($delay = [math]::Pow(2, $attempt); Start-Sleep -Seconds $delay). This is
            what makes a sleep a BACKOFF rather than a throttle.

            A constant duration returns false, which is the point: `Start-Sleep -Seconds
            5` and `-Milliseconds $ThrottleMs` are pacing, and a parameter the loop
            never assigns cannot grow with the attempt count. Depth-limited, so a
            pathological assignment chain cannot hang the guard.
        #>
        param(
            [Parameter(Mandatory = $true)]$Loop,
            [Parameter(Mandatory = $true)]$SleepCommand
        )

        $counters = Get-LoopCounterName -Loop $Loop
        if ($counters.Count -eq 0) { return $false }

        $frontier = [System.Collections.Generic.List[string]]::new()
        foreach ($element in $SleepCommand.CommandElements) {
            foreach ($var in $element.FindAll({ param($n)
                        $n -is [System.Management.Automation.Language.VariableExpressionAst] }, $true)) {
                $frontier.Add($var.VariablePath.UserPath)
            }
        }
        if ($frontier.Count -eq 0) { return $false }

        $seen = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
        $assignments = @($Loop.Body.FindAll({ param($n)
                    $n -is [System.Management.Automation.Language.AssignmentStatementAst] }, $true))

        for ($depth = 0; $depth -lt 4 -and $frontier.Count -gt 0; $depth++) {
            $next = [System.Collections.Generic.List[string]]::new()
            foreach ($name in $frontier) {
                if ($counters.Contains($name)) { return $true }
                if (-not $seen.Add($name)) { continue }
                foreach ($assign in $assignments) {
                    if ($assign.Left -isnot [System.Management.Automation.Language.VariableExpressionAst]) { continue }
                    if ($assign.Left.VariablePath.UserPath -ne $name) { continue }
                    foreach ($var in $assign.Right.FindAll({ param($n)
                                $n -is [System.Management.Automation.Language.VariableExpressionAst] }, $true)) {
                        $next.Add($var.VariablePath.UserPath)
                    }
                }
            }
            $frontier = $next
        }

        return $false
    }

    function Get-RetryLoopCatchNode {
        <#
            The RETRY catches of a file: a catch lexically inside a loop whose own body
            contains a backoff (Start-Sleep), or whose loop body backs off on the
            catch's fall-through path. Parses, and throws on a parse error, so no guard
            built on this can report a verdict about a file that does not compile.

            THE BACKOFF IS WHAT MAKES IT A RETRY, and requiring it is not a detail - the
            first draft of this guard asked only for a catch inside a loop and reported
            14 findings across 6 files that are not retry loops at all. A catch inside
            `foreach ($z in $zones)` logs ONCE PER ITEM, which is a per-item error
            report and the normal way these sweeps work
            (bulk-replace-a-record-ip.ps1:91, whmcs-invoices-lookup.ps1:255 and so on).
            #1766 is about a sink that repeats for ONE operation, so an iteration loop is
            a different question, and a guard that flags 14 working sites gets switched
            off rather than fixed.

            Measured on the real tree: this predicate finds exactly one retry catch in
            each of candid-api-common.ps1, fraudlabspro-api-common.ps1 and
            cloudflare-api-common.ps1, and it also reaches the retry loops in
            google-gtm-backup.ps1 and whmcs-application-triage.ps1, which were already
            compliant - so the clean verdict below is a real-tree control and not only
            an assertion about the three files this change touched.

            A retry loop with no backoff at all would be missed. That is accepted: a
            retry that hammers without backing off is its own defect, and a predicate
            anchored on an attempt-counter name would be a file list wearing a regex.

            THE BACKOFF IS NOT ALWAYS IN THE CATCH (#1783). The two canonical files of
            this whole family - whmcs-api-common.ps1 and whmcs-domain-export.ps1, the
            ones #1753/#1755 remediated - put the log and the backoff in the WHILE BODY,
            after the catch closes, and the catch's else branch throws so that falling
            through to them IS the transient path. Requiring the sleep inside the catch
            missed both of them, so the family's own two files had no discovered cover.
            A second kind is therefore accepted: a fall-through backoff. Each returned
            item carries its Kind, 'catch-backoff' or 'fallthrough-backoff'.

            WHY NOT JUST ACCEPT A SLEEP ANYWHERE IN THE LOOP. Because a throttle sleep
            in an iteration loop is syntactically identical to a backoff, and that
            widening re-introduces exactly the false positives above: measured on this
            tree it pulls in five pacing loops (whmcs-invoices-lookup.ps1:207,
            whmcs-clients-export.ps1:261, whmcs-client-fields-survey.ps1:131,
            whmcs-application-triage.ps1:2071, m365-dkim.ps1:327), each asserted
            rejected by name below. Note that two of those five are `for` loops, one of
            them counting a variable actually named $attempt - so "while/do versus
            foreach" does NOT separate them, which is the plausible-looking rule this
            predicate deliberately does not use.

            The discriminator is that the delay GROWS WITH THE ATTEMPT COUNT, on the
            error path: the sleep must sit after the owning try, and its duration must
            derive from a variable the loop increments (both real cases compute
            [math]::Pow(2, $attempt)). All five throttles sleep for a constant or for a
            fixed parameter, and four of them sleep before the try. A retry whose
            backoff is a constant is missed by this, the same accepted miss as a retry
            with no backoff at all: over-reporting is what gets a guard switched off.
        #>
        param(
            [Parameter(Mandatory = $true)][string]$Code,
            [Parameter(Mandatory = $true)][string]$Name
        )

        $tokens = $null
        $errors = $null
        $ast = [System.Management.Automation.Language.Parser]::ParseInput($Code, [ref]$tokens, [ref]$errors)
        if ($errors -and @($errors).Count -gt 0) {
            throw "PowerShell parse errors in ${Name}: $(@($errors)[0].Message)"
        }

        $found = [System.Collections.ArrayList]::new()
        $catches = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.CatchClauseAst] }, $true)
        foreach ($catch in $catches) {
            $loop = $null
            $parent = $catch.Parent
            while ($parent) {
                if ($parent -is [System.Management.Automation.Language.LoopStatementAst]) { $loop = $parent; break }
                $parent = $parent.Parent
            }
            if (-not $loop) { continue }

            $backoff = @($catch.Body.FindAll({ param($n)
                        $n -is [System.Management.Automation.Language.CommandAst] -and
                        $n.GetCommandName() -eq 'Start-Sleep'
                    }, $true))
            if ($backoff.Count -gt 0) {
                $null = $found.Add([pscustomobject]@{
                        Catch = $catch
                        Loop  = $loop
                        Try   = $catch.Parent
                        Kind  = 'catch-backoff'
                    })
                continue
            }

            # A fall-through backoff. The owning try must be the loop's own try and not
            # one nested inside another try: the inner `try { ConvertFrom-Json } catch`
            # of whmcs-api-common.ps1 shares this loop and this fall-through region, and
            # selecting it would put a per-parse error report in scope of a guard about
            # a per-ATTEMPT repeat.
            $try = $catch.Parent
            if ($try -isnot [System.Management.Automation.Language.TryStatementAst]) { continue }
            $nested = $false
            $node = $try.Parent
            while ($node -and $node -ne $loop) {
                if ($node -is [System.Management.Automation.Language.TryStatementAst]) { $nested = $true; break }
                $node = $node.Parent
            }
            if ($nested) { continue }

            foreach ($sleep in $loop.Body.FindAll({ param($n)
                        $n -is [System.Management.Automation.Language.CommandAst] -and
                        $n.GetCommandName() -eq 'Start-Sleep'
                    }, $true)) {
                if ($sleep.Extent.StartOffset -lt $try.Extent.EndOffset) { continue }
                if (-not (Test-AttemptDerivedDelay -Loop $loop -SleepCommand $sleep)) { continue }
                $null = $found.Add([pscustomobject]@{
                        Catch = $catch
                        Loop  = $loop
                        Try   = $try
                        Kind  = 'fallthrough-backoff'
                    })
                break
            }
        }
        return $found.ToArray()
    }

    function Get-RetryLoopWarningLeak {
        <#
        .SYNOPSIS
            Find retry-loop catch blocks that log exception text, by AST.

        .DESCRIPTION
            Returns one finding per offending sink. A file with no retry loop returns
            nothing, and so does a compliant one, so an empty result is only meaningful
            next to the assertion that the discovered population was non-empty.

            The analysis is a single FORWARD pass in source order, which is deliberate
            and is the one subtlety here. A fixpoint pass would be wrong:
            cloudflare-api-common snapshots $safeDetail = $detail BEFORE $detail
            accumulates the exception text, so re-running to a fixpoint reports the
            repaired code as still broken. Straight-line, in-order propagation models
            that correctly. It is an approximation across branches and nested loops, and
            it over-reports rather than under-reports, which is the safe direction.

            A `throw` is deliberately NOT a sink here. A throw leaves the loop, so it
            fires once and is the error report itself; suppressing it would make every
            failure in these helpers undiagnosable. What this guard is about is the
            PER-ATTEMPT repeat. The terminal throw of the two files #1766 names is
            asserted separately, in the last Describe.
        #>
        param(
            [Parameter(Mandatory = $true)][string]$Code,
            [Parameter(Mandatory = $true)][string]$Name
        )

        $findings = [System.Collections.ArrayList]::new()

        foreach ($retry in @(Get-RetryLoopCatchNode -Code $Code -Name $Name)) {
            $tainted = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)

            $interesting = {
                param($n)
                $n -is [System.Management.Automation.Language.AssignmentStatementAst] -or
                $n -is [System.Management.Automation.Language.CommandAst]
            }

            $nodes = @($retry.Catch.Body.FindAll($interesting, $true) | Sort-Object { $_.Extent.StartOffset })

            # THE RETRY PATH CAN LEAVE THE CATCH (#1783). In whmcs-api-common.ps1 and
            # whmcs-domain-export.ps1 the catch only sets $transientReason and the
            # Write-Warning that logs it sits in the while body, after the catch closes,
            # so a scan of catch.Body alone examines the assignment and never the sink.
            # The fall-through region is appended in source order with the SAME taint
            # set, which is what carries a tainted $transientReason from the catch to
            # the warning. Only for a fall-through backoff: in a catch-backoff loop the
            # code after the try is not the error path, and scanning it would report
            # per-item sinks in working files.
            if ($retry.Kind -eq 'fallthrough-backoff') {
                $after = @($retry.Loop.Body.FindAll($interesting, $true) |
                        Where-Object { $_.Extent.StartOffset -ge $retry.Try.Extent.EndOffset } |
                        Sort-Object { $_.Extent.StartOffset })
                $nodes = @($nodes) + @($after)
            }

            foreach ($node in $nodes) {
                if ($node -is [System.Management.Automation.Language.AssignmentStatementAst]) {
                    $rhs = $node.Right.Extent.Text
                    if ($rhs -match $script:SanitizerRe) { continue }

                    $carries = $rhs -match $script:ExceptionTextRe
                    if (-not $carries) {
                        foreach ($t in $tainted) {
                            if ($rhs -match ('\$' + [regex]::Escape($t) + '\b')) { $carries = $true; break }
                        }
                    }
                    if ($carries -and $node.Left -is [System.Management.Automation.Language.VariableExpressionAst]) {
                        $null = $tainted.Add($node.Left.VariablePath.UserPath)
                    }
                    continue
                }

                $cmdName = $node.GetCommandName()
                if (-not $cmdName -or $script:SinkNames -notcontains $cmdName) { continue }

                $text = $node.Extent.Text
                if ($text -match $script:SanitizerRe) { continue }

                $leaked = $null
                if ($text -match $script:ExceptionTextRe) {
                    $leaked = 'the exception message inline'
                }
                else {
                    foreach ($t in $tainted) {
                        if ($text -match ('\$' + [regex]::Escape($t) + '\b')) { $leaked = "`$$t"; break }
                    }
                }

                if ($leaked) {
                    $null = $findings.Add([pscustomobject]@{
                            File   = $Name
                            Line   = $node.Extent.StartLineNumber
                            Sink   = $cmdName
                            Leaked = $leaked
                        })
                }
            }
        }

        return $findings.ToArray()
    }

    function Get-RetryLoopWarningLeakForFile {
        param([Parameter(Mandatory = $true)][string]$Path)
        return Get-RetryLoopWarningLeak -Code (Get-Content -Raw -LiteralPath $Path) -Name (Split-Path $Path -Leaf)
    }

    function Get-PowerShellCodeOnly {
        <#
            A file's CODE with comments removed, via the tokenizer. A raw-text guard
            fails on this very commit: the fixed files quote the pre-fix pattern in a
            comment that explains why it was wrong, so raw text cannot tell the fix's
            own documentation from the defect. Same helper as
            whmcs-api-transient-retry.Tests.ps1 (#1753).
        #>
        param([Parameter(Mandatory = $true)][string]$Path)

        $tokens = $null
        $errors = $null
        $null = [System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$tokens, [ref]$errors)
        if ($errors -and @($errors).Count -gt 0) {
            throw "PowerShell parse errors in ${Path}: $(@($errors)[0].Message)"
        }
        return ((@($tokens) | Where-Object { $_.Kind -ne 'Comment' } | ForEach-Object { $_.Text }) -join ' ')
    }

    # The POPULATION IS DISCOVERED, which is #1766's AC4. Top-level scripts/ only:
    # scripts/tests/ is excluded because a test that guards a pattern necessarily
    # contains that pattern (#1771).
    $script:RetryLoopFiles = @(
        Get-ChildItem -LiteralPath $script:ScriptsDir -Filter '*.ps1' -File |
            Where-Object {
                @(Get-RetryLoopCatchNode -Code (Get-Content -Raw -LiteralPath $_.FullName) -Name $_.Name).Count -gt 0
            } |
            Select-Object -ExpandProperty Name
    )

    # The FALL-THROUGH half of that population, kept separately because it is the half
    # #1783 added and the half a permissive widening would inflate. A throttle loop
    # wrongly read as a retry can only ever arrive here - a pacing sleep is never
    # inside a catch - so asserting this set exactly is what makes the five named
    # pacing loops rejected rather than merely absent.
    $script:FallThroughRetryFiles = @(
        Get-ChildItem -LiteralPath $script:ScriptsDir -Filter '*.ps1' -File |
            Where-Object {
                @(Get-RetryLoopCatchNode -Code (Get-Content -Raw -LiteralPath $_.FullName) -Name $_.Name |
                        Where-Object { $_.Kind -eq 'fallthrough-backoff' }).Count -gt 0
                } |
                Select-Object -ExpandProperty Name
    )
}

Describe 'Get-ApiTransientLabel' {
    It 'is defined and resolves as a command after dot-sourcing the shared file' {
        (Get-Command Get-ApiTransientLabel -ErrorAction SilentlyContinue) | Should -Not -BeNullOrEmpty
    }

    It 'resolves after dot-sourcing <_> alone, so no caller has to know it exists' -ForEach @(
        'candid-api-common.ps1', 'fraudlabspro-api-common.ps1'
    ) {
        # The dot-source chain is the whole reason the helper can be shared: a caller
        # dot-sources the *-api-common file, and that file pulls this in from
        # $PSScriptRoot. Run in a child pwsh so the already-loaded function in this
        # session cannot make a broken chain look fine.
        $file = Join-Path $script:ScriptsDir $_
        $out = pwsh -NoProfile -NonInteractive -Command ". '$file'; [bool](Get-Command Get-ApiTransientLabel -ErrorAction SilentlyContinue)"
        $LASTEXITCODE | Should -Be 0
        "$out".Trim() | Should -Be 'True'
    }

    Context 'a bare status number in prose is NOT transient (the #1766 measurement)' {
        # Every one of these matched the pre-fix alternation. The first two are the
        # measured false positives: each cost five attempts with exponential backoff
        # and printed the raw message on every one.
        It 'classifies a declined order mentioning 502 as permanent' {
            Get-ApiTransientLabel -Text 'Order 502 declined by rule set' | Should -BeNullOrEmpty
        }

        It 'classifies an auth error mentioning 503 as permanent' {
            Get-ApiTransientLabel -Text 'Unauthorized: subscription key 503 is not valid' |
                Should -BeNullOrEmpty
        }

        It 'classifies a 404 as permanent so a missing charity fails fast' {
            Get-ApiTransientLabel -Text 'The remote server returned an error: (404) Not Found.' |
                Should -BeNullOrEmpty
        }

        It 'classifies a 401 status line as permanent' {
            Get-ApiTransientLabel -Text 'Response status code does not indicate success: 401 (Unauthorized).' |
                Should -BeNullOrEmpty
        }

        It 'does not fire on a status number embedded in a longer digit run' {
            # \b does not split 50-2503001 or 1429, so these never fired even pre-fix.
            # Pinned so a future rewrite does not make them start firing.
            Get-ApiTransientLabel -Text 'Invalid EIN 50-2503001: organization not found' | Should -BeNullOrEmpty
            Get-ApiTransientLabel -Text 'Request failed for transaction 1429 (invalid key)' | Should -BeNullOrEmpty
        }

        It 'classifies an EIN-shaped payload as permanent' {
            Get-ApiTransientLabel -Text '{"ein":"50-2503001","status":"not found","total":"503.00"}' |
                Should -BeNullOrEmpty
        }
    }

    Context 'genuine transient signatures still classify (discrimination, not permissiveness)' {
        It 'labels a PowerShell 7 status-line 502' {
            Get-ApiTransientLabel -Text 'Response status code does not indicate success: 502 (Bad Gateway).' |
                Should -Be 'gateway error'
        }

        It 'labels a PowerShell 7 status-line 429' {
            Get-ApiTransientLabel -Text 'Response status code does not indicate success: 429 (Too Many Requests).' |
                Should -Be 'rate limited'
        }

        It 'labels the PowerShell 5.1 parenthesised 503 form' {
            # AC3. windows-latest runners and the Conductor host produce this spelling,
            # so a rule anchored only on the PS 7 wording regresses there.
            Get-ApiTransientLabel -Text 'The remote server returned an error: (503) Service Unavailable.' |
                Should -Be 'service unavailable'
        }

        It 'labels the PowerShell 5.1 parenthesised 502 form' {
            Get-ApiTransientLabel -Text 'The remote server returned an error: (502) Bad Gateway.' |
                Should -Be 'gateway error'
        }

        It 'labels a raw HTTP status line' {
            Get-ApiTransientLabel -Text 'HTTP/1.1 503 Service Unavailable' | Should -Be 'service unavailable'
        }

        It 'labels a .NET timeout' {
            Get-ApiTransientLabel -Text 'The operation has timed out' | Should -Be 'request timed out'
        }

        It 'labels a rate-limit message' {
            Get-ApiTransientLabel -Text 'Too many requests' | Should -Be 'rate limited'
        }

        It 'labels a proxy-tunnel gateway failure' {
            # Measured in the cloud sandbox, where every outbound call goes through a
            # proxy whose failures arrive in this shape.
            Get-ApiTransientLabel -Text "The proxy tunnel request to proxy 'http://127.0.0.1:34571/' failed with status code '502'." |
                Should -Be 'gateway error'
        }
    }

    Context 'the status code is the primary signal and is structural' {
        It 'labels status <_> from the response object alone' -ForEach @(429, 502, 503, 504) {
            # No transient signature in the text at all: the integer decides.
            Get-ApiTransientLabel -Text 'Order declined' -Status $_ | Should -BeIn $script:AllowedLabels
        }

        It 'leaves a permanent status <_> permanent' -ForEach @(400, 401, 403, 404, 422) {
            Get-ApiTransientLabel -Text 'Order declined' -Status $_ | Should -BeNullOrEmpty
        }

        It 'takes the status over a misleading message' {
            # A real 429 whose body happens to read like a permanent error is still
            # rate limiting, because the status is not text from the body.
            Get-ApiTransientLabel -Text 'Not Found' -Status 429 | Should -Be 'rate limited'
        }
    }

    Context 'it never returns any part of its input' {
        It 'returns a label from the closed set for <_>' -ForEach @(
            'Too many requests', 'Bad Gateway', 'Service Unavailable', 'The operation has timed out',
            'Gateway Timeout', 'temporarily unavailable'
        ) {
            $text = "The API said $_ about $script:Sensitive"
            $label = Get-ApiTransientLabel -Text $text
            $label | Should -Not -BeNullOrEmpty -Because "'$_' must still be retried"
            $label | Should -Not -BeLike "*$script:Sensitive*"
            $script:AllowedLabels | Should -Contain $label
        }

        It 'returns nothing for null, empty and whitespace' {
            Get-ApiTransientLabel -Text $null | Should -BeNullOrEmpty
            Get-ApiTransientLabel -Text '' | Should -BeNullOrEmpty
            Get-ApiTransientLabel -Text '   ' | Should -BeNullOrEmpty
        }

        It 'returns nothing when called with no arguments at all' {
            Get-ApiTransientLabel | Should -BeNullOrEmpty
        }
    }
}

Describe 'Invoke-CandidApi retry path' {
    BeforeEach {
        $script:Warnings = [System.Collections.ArrayList]::new()
        Mock Start-Sleep { }
        Mock Write-Warning { $null = $script:Warnings.Add($Message) }
    }

    AfterEach {
        $env:CANDID_API_MAX_ATTEMPTS = $null
    }

    It 'fails fast on an auth error whose only 503 is in the key description' {
        # #1766's acceptance criterion. Pre-fix this was retried five times, printing
        # the message on each attempt.
        Mock Invoke-RestMethod { throw "Unauthorized: subscription key 503 is not valid for $script:Sensitive" }

        { Invoke-CandidApi -Uri $script:CandidUri -ApiKey 'k' } | Should -Throw

        Should -Invoke Invoke-RestMethod -Times 1 -Exactly
        $script:Warnings.Count | Should -Be 0
    }

    It 'retries a genuine gateway error but logs only the fixed label' {
        $env:CANDID_API_MAX_ATTEMPTS = '3'
        Mock Invoke-RestMethod {
            throw "Response status code does not indicate success: 502 (Bad Gateway). $script:Sensitive"
        }

        { Invoke-CandidApi -Uri $script:CandidUri -ApiKey 'k' } | Should -Throw

        Should -Invoke Invoke-RestMethod -Times 3 -Exactly
        # One warning per attempt except the last, which throws instead.
        $script:Warnings.Count | Should -Be 2
        foreach ($w in $script:Warnings) {
            $w | Should -Not -BeLike "*$script:Sensitive*"
            $w | Should -BeLike '*gateway error*'
            $w | Should -Not -BeLike '*Response status code*'
        }
    }

    It 'returns the response unchanged on success without warning' {
        Mock Invoke-RestMethod { '{"ein":"46-2471893"}' | ConvertFrom-Json }

        $out = Invoke-CandidApi -Uri $script:CandidUri -ApiKey 'k'

        $out.ein | Should -Be '46-2471893'
        Should -Invoke Invoke-RestMethod -Times 1 -Exactly
        $script:Warnings.Count | Should -Be 0
    }
}

Describe 'Invoke-FraudLabsProApi retry path' {
    BeforeEach {
        $script:Warnings = [System.Collections.ArrayList]::new()
        Mock Start-Sleep { }
        Mock Write-Warning { $null = $script:Warnings.Add($Message) }
    }

    AfterEach {
        $env:FRAUDLABSPRO_API_MAX_ATTEMPTS = $null
    }

    It 'fails fast on a declined order whose only 502 is in the rule-set text' {
        # #1766's measured false positive, verbatim.
        Mock Invoke-RestMethod { throw "Order 502 declined by rule set for $script:Sensitive" }

        { Invoke-FraudLabsProApi -OrderId '1429' -ApiKey 'k' } | Should -Throw

        Should -Invoke Invoke-RestMethod -Times 1 -Exactly
        $script:Warnings.Count | Should -Be 0
    }

    It 'keeps the exception text out of the terminal throw it BUILDS' {
        # AC1's "or a re-thrown error". This throw composes a message rather than
        # re-throwing, so the exception text had to be removed from it explicitly - and
        # the redaction it exists for only ever covered the URL this function composed,
        # never a URL quoted back inside the exception.
        Mock Invoke-RestMethod { throw "Order declined, see https://api.fraudlabspro.com/v2/order/result?key=SUPERSECRET&id=1429 for $script:Sensitive" }

        $err = $null
        try { Invoke-FraudLabsProApi -OrderId '1429' -ApiKey 'SUPERSECRET' }
        catch { $err = "$($_.Exception.Message)" }

        $err | Should -Not -BeNullOrEmpty
        $err | Should -Not -BeLike "*$script:Sensitive*"
        $err | Should -Not -BeLike '*SUPERSECRET*'
        $err | Should -Not -BeLike '*Order declined*'
        # The redacted URL still names the request, which is what makes it diagnosable.
        $err | Should -BeLike '*key=***'
        $err | Should -BeLike '*id=1429*'
    }

    It 'retries a genuine service outage but logs only the fixed label' {
        $env:FRAUDLABSPRO_API_MAX_ATTEMPTS = '2'
        Mock Invoke-RestMethod {
            throw "The remote server returned an error: (503) Service Unavailable. $script:Sensitive"
        }

        { Invoke-FraudLabsProApi -OrderId '1429' -ApiKey 'k' } | Should -Throw

        Should -Invoke Invoke-RestMethod -Times 2 -Exactly
        $script:Warnings.Count | Should -Be 1
        $script:Warnings[0] | Should -Not -BeLike "*$script:Sensitive*"
        $script:Warnings[0] | Should -BeLike '*service unavailable*'
        $script:Warnings[0] | Should -Not -BeLike '*remote server returned*'
    }

    It 'names the transient label, not "permanent error", when attempts run out' {
        $env:FRAUDLABSPRO_API_MAX_ATTEMPTS = '1'
        Mock Invoke-RestMethod { throw 'HTTP/1.1 503 Service Unavailable' }

        $err = $null
        try { Invoke-FraudLabsProApi -OrderId '1429' -ApiKey 'k' }
        catch { $err = "$($_.Exception.Message)" }

        $err | Should -BeLike '*service unavailable*'
    }

    It 'returns the response unchanged on success without warning' {
        Mock Invoke-RestMethod { '{"fraudlabspro_status":"APPROVE"}' | ConvertFrom-Json }

        $out = Invoke-FraudLabsProApi -OrderId '1429' -ApiKey 'k'

        $out.fraudlabspro_status | Should -Be 'APPROVE'
        $script:Warnings.Count | Should -Be 0
    }
}

Describe 'Source guard: retry loops are DISCOVERED, not listed' {
    It 'discovered a non-empty population' {
        # A guard whose discovery silently returns nothing passes every assertion below
        # it. The count is asserted so "0 files" cannot read as a clean pass.
        $script:RetryLoopFiles.Count | Should -BeGreaterThan 0
    }

    It 'discovered <_>, a retry loop it is meant to cover' -ForEach @(
        'candid-api-common.ps1', 'fraudlabspro-api-common.ps1', 'cloudflare-api-common.ps1',
        'whmcs-api-common.ps1', 'whmcs-domain-export.ps1'
    ) {
        # Pinned so a discovery that stops reaching a known instance fails here rather
        # than reporting the file clean. cloudflare-api-common.ps1 is on this list
        # because discovery found it, not because anyone enumerated it.
        #
        # The two whmcs files are the #1783 addition, and they are the reason this
        # pin matters rather than being decoration: they are the files #1753/#1755
        # remediated - the canonical pair this whole family exists for - and the
        # catch-only predicate reached neither of them, so the family's own two files
        # were the only ones with no discovered cover.
        $script:RetryLoopFiles | Should -Contain $_
    }

    Context 'the fall-through backoff shape (#1783)' {
        It 'discovered the fall-through retry in <_>' -ForEach @(
            'whmcs-api-common.ps1', 'whmcs-domain-export.ps1'
        ) {
            # These log and back off in the WHILE BODY, after the catch closes. Measured
            # against the catch-only predicate: 0 retry catches in either file.
            $script:FallThroughRetryFiles | Should -Contain $_
        }

        It 'did not read the pacing loop in <_> as a retry backoff' -ForEach @(
            'whmcs-invoices-lookup.ps1', 'whmcs-clients-export.ps1',
            'whmcs-client-fields-survey.ps1', 'whmcs-application-triage.ps1', 'm365-dkim.ps1'
        ) {
            # The five loops that a sleep-anywhere-in-the-loop widening pulls in. Each is
            # a throttle: it paces a sweep over a collection, or polls for a value to
            # populate. Asserted PER FILE and by name, because a widening that passes the
            # set-equality test below by being permissive has to fail somewhere specific.
            #
            # Two of these are `for` loops, and whmcs-application-triage.ps1 counts a
            # variable actually called $attempt, so this is not the foreach-versus-while
            # distinction it looks like. m365-dkim.ps1 and whmcs-application-triage.ps1
            # are in the overall population already, on genuine retry catches elsewhere
            # in the file - which is exactly why the claim has to be about the
            # fall-through half and not about whether the file appears at all.
            $script:FallThroughRetryFiles | Should -Not -Contain $_
        }

        It 'the fall-through population is exactly the two files of the #1753 family' {
            # Set equality, so a future permissive widening fails here even if it adds a
            # file nobody thought to name above.
            ($script:FallThroughRetryFiles | Sort-Object) -join ', ' |
                Should -Be 'whmcs-api-common.ps1, whmcs-domain-export.ps1'
        }

        It 'goes red when the #1753 defect is restored in whmcs-api-common.ps1' {
            # THE MUTATION, run in CI rather than recorded in a PR body: put the raw
            # exception message back into the logged reason and the guard must fail.
            # Measured against the catch-only predicate, this mutation produced 0
            # findings - the guard was green against the defect it exists to catch,
            # because the sink is in the loop body and only catch.Body was scanned.
            #
            # In memory: the file on disk is never written, so no restore can go wrong
            # and a failing assertion cannot leave the tree mutated.
            $path = Join-Path $script:ScriptsDir 'whmcs-api-common.ps1'
            $clean = Get-Content -Raw -LiteralPath $path

            $anchor = '$transientReason = $emLabel'
            # Assert the anchor BEFORE substituting: a refactor that moved this line
            # must fail loudly here instead of silently testing nothing.
            $clean | Should -BeLike "*$anchor*" -Because 'the mutation anchor must exist or this test proves nothing'

            $mutated = $clean.Replace($anchor, '$transientReason = "$($_.Exception.Message)"')
            $mutated | Should -Not -Be $clean

            @(Get-RetryLoopWarningLeak -Code $clean -Name 'whmcs-api-common.ps1').Count |
                Should -Be 0 -Because 'the file on disk is compliant'

            $caught = @(Get-RetryLoopWarningLeak -Code $mutated -Name 'whmcs-api-common.ps1')
            $caught.Count | Should -BeGreaterThan 0
            $caught[0].Sink | Should -Be 'Write-Warning'
            $caught[0].Leaked | Should -Be '$transientReason'
        }
    }

    It 'does not log exception text on retry in any discovered retry loop' {
        $all = [System.Collections.ArrayList]::new()
        foreach ($f in $script:RetryLoopFiles) {
            foreach ($leak in @(Get-RetryLoopWarningLeakForFile -Path (Join-Path $script:ScriptsDir $f))) {
                $null = $all.Add($leak)
            }
        }
        $rendered = ($all | ForEach-Object { "$($_.File):$($_.Line) $($_.Sink) leaks $($_.Leaked)" }) -join '; '
        $all.Count | Should -Be 0 -Because "a retry warning repeats once per attempt: $rendered"
    }

    Context 'the guard discriminates (in-memory fixtures)' {
        It 'flags a retry warning built from the exception message' {
            $code = @'
while ($true) {
    try { Invoke-RestMethod -Uri $u }
    catch {
        $msg = $_.Exception.Message
        Write-Warning "transient: $msg"
        Start-Sleep -Seconds 1
    }
}
'@
            $f = @(Get-RetryLoopWarningLeak -Code $code -Name 'fixture')
            $f.Count | Should -Be 1
            $f[0].Sink | Should -Be 'Write-Warning'
            $f[0].Leaked | Should -Be '$msg'
        }

        It 'flags the exception message interpolated inline, with no variable at all' {
            $code = @'
while ($true) {
    try { Invoke-RestMethod -Uri $u }
    catch {
        Write-Warning "transient: $($_.Exception.Message)"
        Start-Sleep -Seconds 1
    }
}
'@
            $f = @(Get-RetryLoopWarningLeak -Code $code -Name 'fixture')
            $f.Count | Should -Be 1
            $f[0].Leaked | Should -Be 'the exception message inline'
        }

        It 'flags text laundered through two assignments' {
            # The pre-fix cloudflare shape: $exMsg -> $detail -> the warning.
            $code = @'
for ($i = 0; $i -lt 2; $i++) {
    try { Invoke-RestMethod -Uri $u }
    catch {
        $exMsg = $_.Exception.Message
        $detail = "failed"
        $detail += ": $exMsg"
        Write-Warning "$detail - retrying"
        Start-Sleep -Seconds 2
    }
}
'@
            $f = @(Get-RetryLoopWarningLeak -Code $code -Name 'fixture')
            $f.Count | Should -Be 1
            $f[0].Leaked | Should -Be '$detail'
        }

        It 'passes a snapshot taken BEFORE the text was appended' {
            # The repaired cloudflare shape, and the reason the pass is forward-only: a
            # fixpoint analysis reports this as leaking.
            $code = @'
for ($i = 0; $i -lt 2; $i++) {
    try { Invoke-RestMethod -Uri $u }
    catch {
        $exMsg = $_.Exception.Message
        $detail = "failed"
        $safeDetail = $detail
        $detail += ": $exMsg"
        Write-Warning "$safeDetail - retrying"
        Start-Sleep -Seconds 2
        throw $detail
    }
}
'@
            @(Get-RetryLoopWarningLeak -Code $code -Name 'fixture').Count | Should -Be 0
        }

        It 'passes a reason routed through the label helper' {
            $code = @'
while ($true) {
    try { Invoke-RestMethod -Uri $u }
    catch {
        $label = Get-ApiTransientLabel -Text "$($_.Exception.Message)" -Status $status
        Write-Warning "transient: $label"
        Start-Sleep -Seconds 1
    }
}
'@
            @(Get-RetryLoopWarningLeak -Code $code -Name 'fixture').Count | Should -Be 0
        }

        It 'passes a status code read off the response object' {
            # The signal these loops MUST keep logging: an integer, not text.
            $code = @'
while ($true) {
    try { Invoke-RestMethod -Uri $u }
    catch {
        $status = [int]$_.Exception.Response.StatusCode
        Write-Warning "transient, status: $status"
        Start-Sleep -Seconds 1
    }
}
'@
            @(Get-RetryLoopWarningLeak -Code $code -Name 'fixture').Count | Should -Be 0
        }

        It 'ignores a catch that is not in a loop, which is a different question' {
            # A one-shot catch logs once and is the error report. It sleeps here so the
            # case turns on the LOOP and not on the backoff.
            $code = @'
try { Invoke-RestMethod -Uri $u }
catch {
    $msg = $_.Exception.Message
    Write-Warning "failed: $msg"
    Start-Sleep -Seconds 1
}
'@
            @(Get-RetryLoopWarningLeak -Code $code -Name 'fixture').Count | Should -Be 0
        }

        It 'ignores a per-item catch in an ITERATION loop, which logs once per item' {
            # The measured false positive. The first draft of this guard reported 14 of
            # these across 6 working files; see Get-RetryLoopCatchNode. A sweep that
            # reports a failed domain and moves on is not retrying anything, so the
            # warning does not repeat for one operation.
            $code = @'
foreach ($z in $zones) {
    try { Invoke-RestMethod -Uri $z.url }
    catch {
        Write-Warning "[$($z.name)] List failed: $($_.Exception.Message)"
        continue
    }
}
'@
            @(Get-RetryLoopWarningLeak -Code $code -Name 'fixture').Count | Should -Be 0
        }

        It 'ignores a throw, which leaves the loop and reports once' {
            $code = @'
while ($true) {
    try { Invoke-RestMethod -Uri $u }
    catch {
        $msg = $_.Exception.Message
        Start-Sleep -Seconds 1
        throw "failed: $msg"
    }
}
'@
            @(Get-RetryLoopWarningLeak -Code $code -Name 'fixture').Count | Should -Be 0
        }

        It 'flags a FALL-THROUGH warning built from the exception message (#1783)' {
            # The whmcs shape with the #1753 defect restored: the catch sets the reason
            # from the raw message and the sink is in the while body, past the catch.
            # The catch-only scan examined the assignment and never reached the sink.
            $code = @'
$attempt = 0
while ($true) {
    $attempt++
    try {
        $resp = Invoke-RestMethod -Uri $u
        return $resp
    }
    catch {
        $transientReason = "$($_.Exception.Message)"
    }

    if ($attempt -ge $maxAttempts) { throw "gave up" }
    $delay = [int][math]::Min(30, [math]::Pow(2, $attempt))
    Write-Warning "transient (attempt $attempt): $transientReason. Retrying in ${delay}s..."
    Start-Sleep -Seconds $delay
}
'@
            $f = @(Get-RetryLoopWarningLeak -Code $code -Name 'fixture')
            $f.Count | Should -Be 1
            $f[0].Sink | Should -Be 'Write-Warning'
            $f[0].Leaked | Should -Be '$transientReason'
        }

        It 'passes the compliant fall-through shape, where the reason is a label' {
            # The same shape as it stands on main: discrimination, not permissiveness.
            $code = @'
$attempt = 0
while ($true) {
    $attempt++
    try {
        $resp = Invoke-RestMethod -Uri $u
        return $resp
    }
    catch {
        $emLabel = Get-WhmcsTransientLabel "$($_.Exception.Message)"
        if ($emLabel) { $transientReason = $emLabel } else { throw }
    }

    if ($attempt -ge $maxAttempts) { throw "gave up" }
    $delay = [int][math]::Min(30, [math]::Pow(2, $attempt))
    Write-Warning "transient (attempt $attempt): $transientReason. Retrying in ${delay}s..."
    Start-Sleep -Seconds $delay
}
'@
            @(Get-RetryLoopWarningLeak -Code $code -Name 'fixture').Count | Should -Be 0
        }

        It 'ignores an ITERATION loop that paces at the END of each iteration' {
            # The false positive a position-only rule would produce, and the most
            # plausible shape to appear in this tree next: a sweep that reports a failed
            # item and then paces before the next one. The sleep IS after the try, so
            # position alone does not reject it - the delay being a constant is what
            # does.
            $code = @'
foreach ($z in $zones) {
    try { Invoke-RestMethod -Uri $z.url }
    catch {
        Write-Warning "[$($z.name)] failed: $($_.Exception.Message)"
    }
    Start-Sleep -Milliseconds 200
}
'@
            @(Get-RetryLoopCatchNode -Code $code -Name 'fixture').Count | Should -Be 0
            @(Get-RetryLoopWarningLeak -Code $code -Name 'fixture').Count | Should -Be 0
        }

        It 'ignores a COUNTED poll loop whose sleep is a constant (the m365-dkim shape)' {
            # for ($i = 0; $i -lt 12; $i++) with Start-Sleep -Seconds 5: attempt-counted,
            # not a foreach, and not a retry. The counter lives in the for iterator and
            # the delay never grows, so neither signal reads this as a backoff.
            $code = @'
for ($i = 0; $i -lt 12; $i++) {
    if ($ready) { break }
    Start-Sleep -Seconds 5
    try { $config = Get-Thing -Id $d }
    catch { break }
}
'@
            @(Get-RetryLoopCatchNode -Code $code -Name 'fixture').Count | Should -Be 0
        }

        It 'ignores a pace taken from a fixed parameter, not from the attempt count' {
            # Start-Sleep -Milliseconds $ThrottleMs. The argument is a variable, so "not
            # a literal" is not the test; the test is that it never grows with a counter
            # the loop increments.
            $code = @'
foreach ($cid in $ids) {
    $seen++
    try { $d = Invoke-WhmcsGet -ApiUrl $api -Body $b }
    catch { Write-Warning "lookup failed" }
    Start-Sleep -Milliseconds $ThrottleMs
}
'@
            @(Get-RetryLoopCatchNode -Code $code -Name 'fixture').Count | Should -Be 0
        }

        It 'does not select a catch nested inside the retry try, which reports per parse' {
            # whmcs-api-common.ps1 has an inner try/catch around ConvertFrom-Json that
            # shares the while loop and the same fall-through region. Selecting it would
            # bring a once-per-response error report into a guard about a per-ATTEMPT
            # repeat - so only the loop's own try is eligible. One retry catch, not two.
            $code = @'
$attempt = 0
while ($true) {
    $attempt++
    try {
        $raw = Invoke-RestMethod -Uri $u
        try { $resp = $raw | ConvertFrom-Json }
        catch { throw "WHMCS API returned unparseable content" }
        return $resp
    }
    catch {
        $transientReason = 'gateway error'
    }

    $delay = [int][math]::Pow(2, $attempt)
    Write-Warning "transient: $transientReason"
    Start-Sleep -Seconds $delay
}
'@
            $found = @(Get-RetryLoopCatchNode -Code $code -Name 'fixture')
            $found.Count | Should -Be 1
            $found[0].Kind | Should -Be 'fallthrough-backoff'
        }

        It 'refuses to report a verdict on code that does not parse' {
            # Fails closed. A guard that returns "no findings" for unparseable input
            # reports every malformed file as clean.
            { Get-RetryLoopWarningLeak -Code 'function {' -Name 'broken' } | Should -Throw
        }
    }
}

Describe 'Source guard: the loose status alternation is gone' {
    It 'has no bare numeric status alternation in <_>' -ForEach @(
        'candid-api-common.ps1', 'fraudlabspro-api-common.ps1'
    ) {
        # Scoped to the two files #1766 names, and asserted on CODE rather than raw
        # text: both now quote the pre-fix pattern in a comment explaining why it was
        # wrong, so a raw-text guard cannot tell the documentation from the defect.
        $code = Get-PowerShellCodeOnly (Join-Path $script:ScriptsDir $_)
        $code.Contains('\b(429|502|503|504)\b') | Should -BeFalse
        $code | Should -Not -Match '\$transientRe\b'
    }

    It 'routes the retry reason through the shared label helper in <_>' -ForEach @(
        'candid-api-common.ps1', 'fraudlabspro-api-common.ps1'
    ) {
        $code = Get-PowerShellCodeOnly (Join-Path $script:ScriptsDir $_)
        $code.Contains('Get-ApiTransientLabel') | Should -BeTrue
        $code.Contains('api-transient-label.ps1') | Should -BeTrue
    }

    It 'never assigns raw exception text to the logged reason in <_>' -ForEach @(
        'candid-api-common.ps1', 'fraudlabspro-api-common.ps1'
    ) {
        # The boundary matters: a naive check for '= $_.Exception.Message' also matches
        # the fixed call that passes it INTO the label helper, so this asserts on the
        # bare assignment shape only.
        $code = Get-PowerShellCodeOnly (Join-Path $script:ScriptsDir $_)
        $code | Should -Not -Match '\$msg\s*=\s*\$_\s*\.\s*Exception\s*\.\s*Message'
    }
}
