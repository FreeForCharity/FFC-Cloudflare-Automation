<#
.SYNOPSIS
    Pester (v5/v6) tests for #1762: a CALLER of Invoke-WhmcsApi must not serialize the
    response it got back into a thrown error.

.DESCRIPTION
    Regression cover for #1762, the last instance of the #1753 disclosure class in the
    WHMCS scripts, and the one the three landed PRs each correctly scoped themselves
    out of:

      - #1753 / #1755 fixed the retry loop inside Invoke-WhmcsApi. This leak is in the
        CALLER, after a successful return, so the shared helper never sees it.
      - #1756 / #1757 fixed the four SELF-CONTAINED exporters. This script is not one:
        it dot-sources whmcs-api-common.ps1.
      - #1758 / #1761 pinned the credential host, a different property entirely.

    The defect, at scripts/whmcs-import-cloudflare-domain.ps1:

        $order = Invoke-WhmcsApi -ApiUrl $api -Body $b
        $domainId = ("$($order.domainids)").Split(',')[0].Trim()
        if ([string]::IsNullOrWhiteSpace($domainId)) {
            throw "AddOrder did not return a domainid for $domainLc (response: $($order | ConvertTo-Json -Compress))"
        }

    223-whmcs-import-cloudflare-domains.yml runs that script and this repository is
    `visibility: public`, so the throw is world-readable. An AddOrder response carries
    orderid, invoiceid, clientid and serviceids, and on an error shape WHMCS's own
    message/error text. Lower severity than #1753 (identifiers, not ticket text or
    donor email) with an identical mechanism and remedy: report the response's FIELD
    NAMES via Get-WhmcsResponseShape, which the script already has in scope.

    HOW THE BEHAVIOURAL TEST WORKS, and why it is not the #1756 shape. The four
    self-contained scripts carry the leak inside a FUNCTION, so #1757 could extract
    their top-level function definitions and dot-source those. This leak is in
    TOP-LEVEL inline code in a script with a mandatory param block that runs a live
    sweep, so there is no function to extract and the file cannot be dot-sourced at
    all. Instead the parser locates the `throw` statement itself and only that
    statement is executed, in a scope holding a synthetic AddOrder response. That
    exercises the real shipped expression - not a copy of it in this file - which is
    what makes the case fail against the pre-fix line.

    The last Describe adds the source guard #1762 AC3 asks for. It is an AST guard,
    not a grep, for the reasons measured on the issue: this file necessarily quotes
    the pre-fix pattern (above), as do the comments in the scripts #1756 touched, so
    raw text cannot tell a fix's documentation from the defect.

    Run locally: Invoke-Pester -Path scripts/tests -Output Detailed
#>

BeforeAll {
    $script:ScriptsDir = Split-Path $PSScriptRoot -Parent
    $script:ImportScript = Join-Path $script:ScriptsDir 'whmcs-import-cloudflare-domain.ps1'

    # Get-WhmcsResponseShape (#1755) lives here, and the script under test dot-sources
    # this same file, so the extracted statement resolves it exactly as it does in
    # production.
    . (Join-Path $script:ScriptsDir 'whmcs-api-common.ps1')

    # Stand-ins for everything an AddOrder response carries that must not be logged.
    # Deliberately NOT substrings of each other or of any field name below, so no
    # assertion can pass or fail by accident.
    $script:SensitiveOrderId = '8675309'
    $script:SensitiveInvoiceId = '4242424'
    $script:SensitiveClientId = '1230321'
    $script:SensitiveEmail = 'donor@example.invalid'
    $script:SensitiveDomain = 'secret-charity.invalid'

    function Get-DomainidlessOrderResponse {
        <#
            #1762's precondition: AddOrder returned, but with no domainids field. Built
            through JSON so it is a PSCustomObject, which is what Invoke-WhmcsApi
            actually returns (Get-WhmcsResponseShape reads PSObject.Properties for one
            and .Keys for a hashtable, so the distinction is load-bearing).
        #>
        return @{
            result     = 'success'
            orderid    = 8675309
            invoiceid  = 4242424
            clientid   = 1230321
            serviceids = '55443322'
            message    = 'donor@example.invalid ordered secret-charity.invalid'
        } | ConvertTo-Json | ConvertFrom-Json
    }

    function Get-AddOrderThrowStatement {
        <#
            Return the source text of the one `throw` in the import script that reports
            a missing domainid.

            Anchored on the AST rather than on a line number so moving the statement
            does not silently retarget the test, and asserted to be unique so a second
            copy appearing is a failure rather than a coin flip.
        #>
        param([Parameter(Mandatory = $true)][string]$Path)

        $errors = $null
        $ast = [System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$null, [ref]$errors)
        if ($errors -and @($errors).Count -gt 0) {
            throw "PowerShell parse errors in ${Path}: $(@($errors)[0].Message)"
        }

        $throws = @($ast.FindAll({
                    param($n)
                    $n -is [System.Management.Automation.Language.ThrowStatementAst] -and
                    $n.Extent.Text -match 'AddOrder did not return a domainid'
                }, $true))

        if ($throws.Count -ne 1) {
            throw ("Expected exactly 1 'AddOrder did not return a domainid' throw in ${Path}, found " +
                "$($throws.Count) - the extraction is broken, or the statement was moved/duplicated.")
        }
        return $throws[0].Extent.Text
    }

    function Get-PowerShellCodeOnly {
        <#
            Return a file's CODE with comments removed, via the PowerShell tokenizer.
            Same helper as whmcs-api-transient-retry.Tests.ps1 and
            whmcs-selfcontained-response-disclosure.Tests.ps1. Kept because parsing also
            means a guard can never pass on a file that does not compile.

            NOTE it joins TOKENS with a space, so a dotted member access reads as
            '$order . PSObject . Properties'; anchor any member pattern with '\s*\.\s*'.
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
}

Describe 'The AddOrder domainid failure in whmcs-import-cloudflare-domain.ps1' {

    BeforeAll {
        $script:ThrowText = Get-AddOrderThrowStatement -Path $script:ImportScript
    }

    It 'executes the statement that actually ships, not a copy of it' {
        # Guards the test itself: if the extraction ever returns something that is not
        # the throw under test, every assertion below would pass vacuously.
        $script:ThrowText | Should -Match '^\s*throw\b'
        $script:ThrowText | Should -Match 'AddOrder did not return a domainid'
    }

    It 'keeps every response VALUE out of the thrown error' {
        # $order and $domainLc are read by the extracted statement, which resolves them
        # from this scope at invocation time. PSScriptAnalyzer cannot see through a
        # [scriptblock]::Create, so it reports PSUseDeclaredVarsMoreThanAssignments
        # here and in the two cases below - a warning, which CI prints and does not
        # fail on, not a sign the fixture is dead.
        $order = Get-DomainidlessOrderResponse
        $domainLc = 'example-charity.invalid'

        $err = $null
        try { & ([scriptblock]::Create($script:ThrowText)) } catch { $err = "$($_.Exception.Message)" }

        $err | Should -Not -BeNullOrEmpty -Because 'a domainid-less AddOrder must still fail'
        $err | Should -Not -BeLike "*$script:SensitiveOrderId*"
        $err | Should -Not -BeLike "*$script:SensitiveInvoiceId*"
        $err | Should -Not -BeLike "*$script:SensitiveClientId*"
        $err | Should -Not -BeLike "*$script:SensitiveEmail*"
        $err | Should -Not -BeLike "*$script:SensitiveDomain*"
        # The serialization itself, in case a future shape leaks it another way.
        $err | Should -Not -BeLike '*{*'
    }

    It 'still reports the field NAMES, so the failure stays diagnosable' {
        # The other half of the criterion. Suppressing the response without this trades
        # a disclosure for an undebuggable production failure in a gated write lane.
        $order = Get-DomainidlessOrderResponse
        $domainLc = 'example-charity.invalid'

        $err = $null
        try { & ([scriptblock]::Create($script:ThrowText)) } catch { $err = "$($_.Exception.Message)" }

        $err | Should -BeLike '*orderid*'
        $err | Should -BeLike '*invoiceid*'
        $err | Should -BeLike '*clientid*'
        $err | Should -BeLike '*serviceids*'
        # The domain being imported is the script's own argument, not response data,
        # and naming it is how an operator finds the failing row.
        $err | Should -BeLike '*example-charity.invalid*'
    }

    It 'would FAIL against the pre-fix statement (the assertions discriminate)' {
        # #1762 AC2 asks for a case that fails before the fix. Rather than mutating the
        # file on disk - a restore defined against the wrong baseline is its own
        # hazard - the pre-fix statement is reconstructed verbatim here and run through
        # the same fixture. Single-quoted so nothing interpolates at parse time.
        $preFix = 'throw "AddOrder did not return a domainid for $domainLc (response: $($order | ConvertTo-Json -Compress))"'

        $order = Get-DomainidlessOrderResponse
        $domainLc = 'example-charity.invalid'

        $err = $null
        try { & ([scriptblock]::Create($preFix)) } catch { $err = "$($_.Exception.Message)" }

        # If any of these stop holding, the fixture no longer reaches the defect and
        # the three cases above prove nothing.
        $err | Should -BeLike "*$script:SensitiveOrderId*"
        $err | Should -BeLike "*$script:SensitiveEmail*"
        $err | Should -BeLike "*$script:SensitiveDomain*"
        $err | Should -BeLike '*{*'
    }
}

Describe 'Source guard: no WHMCS response is serialized into a log sink' {

    BeforeAll {
        function Get-ResponseSerializationFinding {
            <#
                Report every place a ConvertTo-Json result flows into a log sink while
                serializing something OTHER than an API error-descriptor array.

                WHY AN AST GUARD AND NOT THE ISSUE'S GREP. #1762 AC3 offers either, and
                a text guard fails on arrival here for three measured reasons: this
                file quotes the pre-fix statement in its .DESCRIPTION; the fix in the
                script carries the pattern's vocabulary in a comment; and
                whmcs-selfcontained-response-disclosure.Tests.ps1 both documents and
                regex-guards the #1756 shape. A repository that guards a pattern
                necessarily contains that pattern.

                WHAT COUNTS AS A SINK: throw, Write-Warning, Write-Error, Write-Host -
                the four in #1762's enumeration. Write-Output is deliberately NOT a
                sink: it is a script's product rather than a failure diagnostic, and
                including it flags five deliberate reports (the dry-run bodies in
                cloudflare-ai-crawlers.ps1 and cloudflare-custom-nameservers.ps1, and
                Invoke-DependabotAutoMerge.ps1's results object) which are a different
                question from this one. Measured, not assumed, and recorded here so the
                next session does not have to re-derive it.

                WHAT IS ALLOWED: an expression whose final member is `errors`. The three
                Cloudflare sites #1762 scoped out serialize `$resp.errors` - API error
                descriptors, not customer records. Discriminating on WHAT is serialized
                rather than on a file path means a fourth Cloudflare site passes on
                arrival while a fourth WHMCS site does not.

                Handles both call forms: `$x | ConvertTo-Json` and `ConvertTo-Json $x`.
                The second is not hypothetical - Update-StagingDns.ps1 uses it - and the
                issue's grep pattern cannot see it at all.
            #>
            param(
                [Parameter(Mandatory = $true)][string]$Code,
                [Parameter(Mandatory = $true)][string]$Name
            )

            $errors = $null
            $ast = [System.Management.Automation.Language.Parser]::ParseInput($Code, [ref]$null, [ref]$errors)
            if ($errors -and @($errors).Count -gt 0) {
                throw "PowerShell parse errors in ${Name}: $(@($errors)[0].Message)"
            }

            $calls = @($ast.FindAll({
                        param($n)
                        $n -is [System.Management.Automation.Language.CommandAst] -and
                        $n.GetCommandName() -eq 'ConvertTo-Json'
                    }, $true))

            $offenders = @()
            foreach ($call in $calls) {
                # 1. Does the result reach a log sink?
                $sink = $null
                $node = $call.Parent
                while ($node) {
                    if ($node -is [System.Management.Automation.Language.ThrowStatementAst]) { $sink = 'throw'; break }
                    if ($node -is [System.Management.Automation.Language.CommandAst]) {
                        $cmd = $node.GetCommandName()
                        if ($cmd -and $cmd -match '^Write-(Warning|Error|Host)$') { $sink = $cmd; break }
                    }
                    $node = $node.Parent
                }
                if (-not $sink) { continue }

                # 2. What is being serialized - the piped expression, or the first
                #    positional argument of the command form.
                $subject = $null
                $pipeline = $call.Parent
                if ($pipeline -is [System.Management.Automation.Language.PipelineAst]) {
                    $elements = @($pipeline.PipelineElements)
                    $index = $elements.IndexOf($call)
                    if ($index -gt 0) { $subject = $elements[$index - 1].Extent.Text }
                }
                if (-not $subject) {
                    # NOT $args: that is an automatic variable, and PSScriptAnalyzer
                    # flags assigning to it (PSAvoidAssignmentToAutomaticVariable).
                    $positional = @($call.CommandElements | Select-Object -Skip 1 |
                            Where-Object { $_ -isnot [System.Management.Automation.Language.CommandParameterAst] })
                    if ($positional.Count -gt 0) { $subject = $positional[0].Extent.Text }
                }
                # Nothing identifiable is serialized (e.g. piped from a command): report
                # it rather than pass it, so an unreadable shape is never a silent pass.
                if (-not $subject) { $subject = '<unresolved>' }

                if ($subject -match '\.\s*errors\s*$') { continue }

                $offenders += "${Name}:$($call.Extent.StartLineNumber) serializes $subject into $sink"
            }
            return $offenders
        }
    }

    It 'flags a response serialized into a throw, in either call form' {
        # Discrimination, in memory: a guard that has never seen an offender is not
        # known to detect one. Both forms, because the grep this replaces sees one.
        $piped = 'throw "AddOrder failed (response: $($order | ConvertTo-Json -Compress))"'
        $positional = 'if (-not $resp.success) { throw "GET failed: $(ConvertTo-Json $resp)" }'

        @(Get-ResponseSerializationFinding -Code $piped -Name 'fixture-piped.ps1').Count |
            Should -Be 1
        @(Get-ResponseSerializationFinding -Code $positional -Name 'fixture-positional.ps1').Count |
            Should -Be 1
    }

    It 'does not flag an error-descriptor array, or a serialization that reaches no sink' {
        $cloudflare = 'throw "Cloudflare registrar API error: $($resp.errors | ConvertTo-Json -Compress)"'
        $notASink = '$body = $plan | ConvertTo-Json -Compress; Invoke-RestMethod -Body $body -Uri $u'

        @(Get-ResponseSerializationFinding -Code $cloudflare -Name 'fixture-cf.ps1').Count |
            Should -Be 0
        @(Get-ResponseSerializationFinding -Code $notASink -Name 'fixture-body.ps1').Count |
            Should -Be 0
    }

    It 'finds no offender in any scripts/*.ps1' {
        # The lane #1762 enumerated. scripts/tests/ is excluded by construction (this
        # directory is not scanned): a guard's own fixtures contain the shape it
        # rejects, which is the trap the issue comment measured on #1755's first draft.
        #
        # OUT OF LANE and deliberately not fixed here: Update-StagingDns.ps1 at the
        # repository root serializes a whole Cloudflare API response into a throw three
        # times, in the positional form, which #1762's grep could not see on either
        # count (wrong directory, wrong syntax). No workflow invokes that script - CI
        # only syntax-validates it (722-ci.yml) - so its throw reaches an operator's
        # console rather than a public Actions log, which makes it consistency work
        # rather than a disclosure. Filed separately rather than widened into this PR.
        $files = @(Get-ChildItem -Path $script:ScriptsDir -Filter *.ps1 -File |
                Sort-Object Name)

        $files.Count | Should -BeGreaterThan 0 -Because 'a guard that scanned no files is not a pass'

        $offenders = @()
        foreach ($file in $files) {
            $code = Get-Content -LiteralPath $file.FullName -Raw
            $offenders += @(Get-ResponseSerializationFinding -Code $code -Name $file.Name)
        }

        $offenders -join '; ' | Should -BeNullOrEmpty -Because @'
a WHMCS/API response serialized into a throw or Write-* reaches a world-readable
Actions log (#1753, #1756, #1762). Report field names with Get-WhmcsResponseShape
instead, or pipe only the API's .errors descriptors.
'@
    }

    It 'reports the response shape by field name in whmcs-import-cloudflare-domain.ps1' {
        # Asserts the replacement is PRESENT, not merely that the defect is absent:
        # deleting the diagnostic entirely would also satisfy the guard above. Same
        # polarity point #1763 makes about this family of guards.
        $code = Get-PowerShellCodeOnly -Path $script:ImportScript
        $code | Should -Match 'Get-WhmcsResponseShape\s+\$order'
        $code.Contains('response keys:') | Should -BeTrue
    }
}
