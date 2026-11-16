<#
.SYNOPSIS
    Pester (v5/v6) tests for #1756: the four self-contained WHMCS scripts must not
    serialize a WHMCS response body into a thrown error.

.DESCRIPTION
    Regression cover for #1756, the narrower half of #1753.

    #1753/#1755 closed the LOGGED disclosure in the two files that carry the retry
    loop (scripts/whmcs-api-common.ps1 and its verbatim copy
    scripts/whmcs-domain-export.ps1): Invoke-WhmcsApi's retry Write-Warning could
    echo up to 800 characters of a response body into a public Actions log.

    These four scripts have no retry loop and no Write-Warning, so three of the four
    source guards in whmcs-api-transient-retry.Tests.ps1 do not apply to them. What
    they did carry is the same 800-character serialization, reached once, on a
    terminal failure:

        $diag = $null
        try { $diag = ($resp | ConvertTo-Json -Depth 6 -Compress) } catch {}
        if (-not [string]::IsNullOrWhiteSpace($diag) -and $diag.Length -gt 800) {
            $diag = $diag.Substring(0, 800) + '...'
        }
        $msg = 'Unknown WHMCS API error.' + (if ($diag) { " Response: $diag" } else { '' })

    This repository is `visibility: public`, so an Actions error is as world-readable
    as an Actions warning, and a products/domains/nameservers/payment-methods
    response still carries client names, domains and billing figures. The
    precondition is a non-success response with NO message, errormessage or error
    field, so this is a failure-path disclosure rather than a routine one.

    The fix reports the response's FIELD NAMES instead of its body, which is the
    inline form #1755 applied to whmcs-domain-export.ps1. Field names are schema;
    field values are donor and client data.

    HOW THESE SCRIPTS ARE TESTED. They are self-contained by design - each duplicates
    Resolve-WhmcsCredentials and Invoke-WhmcsApi rather than dot-sourcing
    whmcs-api-common.ps1 - and each has a mandatory param block and runs a live sweep
    at top level, so none can be dot-sourced the way whmcs-api-common.ps1 is. #1755
    therefore asserted whmcs-domain-export.ps1 on its SOURCE only.

    This file does that too (the last Describe), but it also gets a real behavioural
    test out of them: the PowerShell parser yields each file's top-level function
    definitions, and only those are dot-sourced, so Invoke-WhmcsApi can be exercised
    against a mocked Invoke-RestMethod while the file's top-level sweep never runs. A
    source guard alone cannot tell "the body is gone" from "the body is gone and the
    error is now undiagnosable", and the second criterion in #1756 is exactly that
    the failure stay diagnosable.

    Run locally: Invoke-Pester -Path scripts/tests -Output Detailed
#>

BeforeDiscovery {
    # The four files from #1756. Each carried the serialization in its own copy of
    # Invoke-WhmcsApi. A fifth copy appearing later belongs in this list.
    $script:SelfContainedFiles = @(
        'whmcs-products-export.ps1',
        'whmcs-domain-nameservers-update.ps1',
        'whmcs-domain-exists.ps1',
        'whmcs-payment-methods-export.ps1'
    )
}

BeforeAll {
    $script:ScriptsDir = Split-Path $PSScriptRoot -Parent

    # In the host allowlist Invoke-WhmcsApi enforces, so the credential guard passes
    # and the request reaches the mocked Invoke-RestMethod.
    $script:ApiUrl = 'https://freeforcharity.org/hub/includes/api.php'

    # Stand-ins for everything a WHMCS response can carry that must never be logged.
    # Deliberately NOT substrings of any field name in the responses below, so an
    # assertion cannot pass or fail by accident.
    $script:SensitiveEmail = 'donor@example.invalid'
    $script:SensitiveDomain = 'secret-charity.invalid'
    $script:SensitiveId = '8675309'

    function Get-PowerShellCodeOnly {
        <#
            Return a file's CODE with comments removed, via the PowerShell tokenizer.

            Raw text is the wrong surface: this very file quotes the pre-fix pattern
            in its .DESCRIPTION to explain it, and so do the four scripts' comments,
            so a raw-text guard makes a fix's own documentation indistinguishable
            from the defect. Parsing also means a guard can never pass on a file that
            does not compile. Same helper as whmcs-api-transient-retry.Tests.ps1.
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

    function Get-SelfContainedFunctionText {
        <#
            Return the source text of a script's top-level function definitions, and
            nothing else.

            This is what makes a behavioural test possible on a script that cannot be
            dot-sourced: the param block, $ErrorActionPreference and the live sweep
            at the bottom are all excluded, so dot-sourcing the result defines
            Invoke-WhmcsApi (and the helpers it calls) without performing a sweep or
            demanding a -Domain argument.
        #>
        param([Parameter(Mandatory = $true)][string]$Name)

        $path = Join-Path $script:ScriptsDir $Name
        $errors = $null
        $ast = [System.Management.Automation.Language.Parser]::ParseFile($path, [ref]$null, [ref]$errors)
        if ($errors -and @($errors).Count -gt 0) {
            throw "PowerShell parse errors in ${path}: $(@($errors)[0].Message)"
        }

        # Top-level only: a nested function is already inside its parent's text.
        $functions = @($ast.EndBlock.Statements |
                Where-Object { $_ -is [System.Management.Automation.Language.FunctionDefinitionAst] })
        if ($functions.Count -eq 0) {
            throw "No top-level function definitions found in ${path} - the extraction is broken, not the script."
        }
        if (-not ($functions.Name -contains 'Invoke-WhmcsApi')) {
            throw "No Invoke-WhmcsApi in ${path} - this file no longer carries its own copy; re-check the list."
        }

        return (($functions | ForEach-Object { $_.Extent.Text }) -join "`n`n")
    }
}

Describe 'Invoke-WhmcsApi in <_>' -ForEach $script:SelfContainedFiles {

    BeforeAll {
        # Captured per Describe so each file's copy is tested in isolation rather
        # than whichever one was defined last.
        $script:FunctionText = Get-SelfContainedFunctionText $_
    }

    BeforeEach {
        # Dot-source into the It's own scope. Mock applies because Invoke-RestMethod
        # resolves through the session state either way.
        . ([scriptblock]::Create($script:FunctionText))
    }

    Context 'a non-success response carrying no message field' {

        It 'keeps every response VALUE out of the thrown error' {
            # #1756's precondition: result != success and no message/errormessage/
            # error field, which is the only path that reached the serialization.
            Mock Invoke-RestMethod {
                @{
                    result   = 'error'
                    clientid = 8675309
                    email    = 'donor@example.invalid'
                    domain   = 'secret-charity.invalid'
                } | ConvertTo-Json | ConvertFrom-Json
            }

            $err = $null
            try { Invoke-WhmcsApi -ApiUrl $script:ApiUrl -Body @{ action = 'GetClientsProducts' } }
            catch { $err = "$($_.Exception.Message)" }

            $err | Should -Not -BeNullOrEmpty -Because 'an unrecognised response must still fail'
            $err | Should -Not -BeLike "*$script:SensitiveEmail*"
            $err | Should -Not -BeLike "*$script:SensitiveDomain*"
            $err | Should -Not -BeLike "*$script:SensitiveId*"
            # The serialization itself, in case a future shape leaks it another way.
            $err | Should -Not -BeLike '*{*'
        }

        It 'still reports the field NAMES, so the failure stays diagnosable' {
            # The other half of the acceptance criteria. Suppressing the body without
            # this would trade a disclosure for an undebuggable production failure.
            Mock Invoke-RestMethod {
                @{
                    result   = 'error'
                    clientid = 8675309
                    email    = 'donor@example.invalid'
                    domain   = 'secret-charity.invalid'
                } | ConvertTo-Json | ConvertFrom-Json
            }

            $err = $null
            try { Invoke-WhmcsApi -ApiUrl $script:ApiUrl -Body @{ action = 'GetClientsProducts' } }
            catch { $err = "$($_.Exception.Message)" }

            $err | Should -BeLike '*clientid*'
            $err | Should -BeLike '*email*'
            $err | Should -BeLike '*domain*'
            $err | Should -BeLike '*Unknown WHMCS API error*'
        }
    }

    Context 'behaviour that must not regress' {

        It "surfaces WHMCS's own error message when the response carries one" {
            # A message field is WHMCS's error report. It is not a serialization of
            # the response, and suppressing it would make every WHMCS failure
            # undiagnosable - so the fix must leave this path alone.
            Mock Invoke-RestMethod {
                @{ result = 'error'; message = 'Invalid IP 203.0.113.7' } | ConvertTo-Json | ConvertFrom-Json
            }

            $err = $null
            try { Invoke-WhmcsApi -ApiUrl $script:ApiUrl -Body @{ action = 'GetClientsProducts' } }
            catch { $err = "$($_.Exception.Message)" }

            $err | Should -BeLike '*Invalid IP 203.0.113.7*'
            Should -Invoke Invoke-RestMethod -Times 1 -Exactly
        }

        It 'returns the response unchanged on success' {
            Mock Invoke-RestMethod {
                @{ result = 'success'; totalresults = 2 } | ConvertTo-Json | ConvertFrom-Json
            }

            $out = Invoke-WhmcsApi -ApiUrl $script:ApiUrl -Body @{ action = 'GetClientsProducts' }

            $out.result | Should -Be 'success'
            $out.totalresults | Should -Be 2
        }

        # NOT asserted here: that Invoke-WhmcsApi refuses an -ApiUrl outside the
        # credential host allowlist. whmcs-products-export.ps1 and
        # whmcs-payment-methods-export.ps1 carry that guard; the first draft of this
        # file asserted it for all four and the other two FAILED, because
        # whmcs-domain-exists.ps1 and whmcs-domain-nameservers-update.ps1 have no
        # allowlist at all. That is a different defect class from #1756 - a
        # credential-redirect gap, not a disclosure - so it is filed separately
        # rather than widened into this PR, and an assertion here would only encode
        # the gap as expected. See the issue referenced on the #1756 PR.
    }
}

Describe 'Source guards over every self-contained copy' {

    BeforeAll {
        function Get-SelfContainedCode {
            param([Parameter(Mandatory = $true)][string]$Name)
            return Get-PowerShellCodeOnly (Join-Path (Split-Path $PSScriptRoot -Parent) $Name)
        }
    }

    It 'does not serialize a response into an error string in <_>' -ForEach $script:SelfContainedFiles {
        # The exact pre-fix shapes. #1756's closing acceptance criterion is that
        # `grep -rln 'Substring(0, 800)' --include=*.ps1 .` returns nothing.
        $code = Get-SelfContainedCode $_
        $code | Should -Not -Match 'Substring\s*\(\s*0\s*,\s*800\s*\)'
        $code.Contains('Response: $diag') | Should -BeFalse
        $code.Contains('$diag') | Should -BeFalse -Because 'the diagnostic variable held the serialized body'
    }

    It 'no .ps1 in the repository invokes a command named "if"' {
        # WHY THIS GUARD EXISTS, and why it is repo-wide rather than scoped to the
        # four files. The code #1756 asked to remove ended in:
        #
        #     $msg = 'Unknown WHMCS API error.' + (if ($diag) { " Response: $diag" } else { '' })
        #
        # A plain ( ) grouping expression takes a PIPELINE, not a statement, so that
        # `if` is parsed as a COMMAND INVOCATION. It parses cleanly - PSScriptAnalyzer
        # reports nothing, and CI's ParseError gate cannot see it - and then fails at
        # RUNTIME with CommandNotFoundException: "The term 'if' is not recognized".
        # Measured under these scripts' own $ErrorActionPreference = 'Stop', that
        # THROWS, so the four scripts did not disclose the body #1756 described; they
        # died on the line that would have built the message, reporting a missing
        # command instead of a WHMCS error. The disclosure was latent and would have
        # gone live the moment someone "fixed" ( to $(.
        #
        # $( ) and @( ) are subexpression operators and DO accept statements, so the
        # @(if ...) at scripts/powershell-command-facts.ps1:210 is correct and must
        # not be flagged. Hence an AST test for the command invocation itself rather
        # than a text pattern: PowerShell has no `if` cmdlet, so a CommandAst named
        # `if` is always this bug and never a false positive.
        $repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
        $exclude = '([/\\]node_modules[/\\]|[/\\]\.git[/\\]|[/\\]\.venv[/\\])'
        $files = @(Get-ChildItem -Path $repoRoot -Filter *.ps1 -File -Recurse -ErrorAction Stop |
                Where-Object { $_.FullName -notmatch $exclude })

        $files.Count | Should -BeGreaterThan 0 -Because 'a guard that scanned no files is not a pass'

        $offenders = @()
        foreach ($file in $files) {
            $errors = $null
            $ast = [System.Management.Automation.Language.Parser]::ParseFile(
                $file.FullName, [ref]$null, [ref]$errors)
            if ($errors -and @($errors).Count -gt 0) { continue }  # the ParseError gate owns these

            $bad = @($ast.FindAll({
                        param($n)
                        $n -is [System.Management.Automation.Language.CommandAst] -and
                        $n.GetCommandName() -eq 'if'
                    }, $true))

            foreach ($b in $bad) {
                $offenders += "$($file.Name):$($b.Extent.StartLineNumber)"
            }
        }

        $offenders -join '; ' | Should -BeNullOrEmpty -Because @"
a plain ( ) grouping cannot contain an if STATEMENT, so these parse as a call to a
command named 'if' and throw CommandNotFoundException at runtime. Use `$( ) or @( ).
"@
    }

    It 'reports the response shape by field name in <_>' -ForEach $script:SelfContainedFiles {
        # Asserts the replacement is present, not merely that the defect is absent -
        # deleting the diagnostic entirely would also pass the guard above.
        # Get-PowerShellCodeOnly joins TOKENS with a space, so a dotted member access
        # reads as '$resp . PSObject . Properties'. A pattern written as
        # 'PSObject\.Properties' therefore never matches, and - because this is an
        # assertion that something is PRESENT - it fails loudly rather than passing
        # vacuously. The negative guards above are the ones to keep an eye on.
        $code = Get-SelfContainedCode $_
        $code.Contains('Response keys:') | Should -BeTrue
        $code | Should -Match 'PSObject\s*\.\s*Properties'
    }
}
