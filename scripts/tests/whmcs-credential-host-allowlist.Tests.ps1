<#
.SYNOPSIS
    Pester (v5/v6) tests for #1758: every copy of Invoke-WhmcsApi / Invoke-WhmcsApiXml
    must refuse to send the WHMCS credential to a host outside the allowlist.

.DESCRIPTION
    THE DEFECT. These scripts are self-contained by design - each duplicates
    Resolve-WhmcsCredentials and its own Invoke-WhmcsApi rather than dot-sourcing
    scripts/whmcs-api-common.ps1. Every copy attaches the WHMCS identifier/secret
    (POST body fields) and, in most copies, the APIM subscription key (a request
    header) to a request whose URI comes from a -ApiUrl parameter, which workflows
    feed from $env:WHMCS_API_URL - and in 213 that env var is a workflow_dispatch
    input. Seven copies validated the host before sending; six did not, so a
    dispatcher could redirect live production credentials to an arbitrary host, over
    plain http if they chose.

    WHY THE POPULATION IS DISCOVERED HERE RATHER THAN LISTED. #1758 named two files.
    It found two because it enumerated with
    `grep -rln 'allowedHosts' --include='whmcs-*.ps1' scripts/`, and that scoping
    misses the three XML copies (the function there is named Invoke-WhmcsApiXml) and
    domain-transfer-epp-probe.ps1 (no `whmcs-` filename prefix). A list in a test file
    inherits exactly that blind spot and goes quietly stale the next time a copy is
    made, which is how this gap was created in the first place. So the tests below
    derive their own population from the AST: every top-level function in scripts/
    named Invoke-WhmcsApi or Invoke-WhmcsApiXml. A seventh copy added tomorrow is
    tested the moment it lands, and `Expected-CopyFloor` makes a matcher that stops
    matching fail instead of passing over an empty set.

    HOW A SELF-CONTAINED SCRIPT IS EXERCISED. None of these files can be dot-sourced:
    each has a mandatory param block and runs a live sweep at top level. The
    PowerShell parser yields each file's top-level function definitions and only
    those are dot-sourced, so Invoke-WhmcsApi can be called against a mocked
    Invoke-RestMethod while the file's own sweep never runs. Same technique as
    scripts/tests/whmcs-selfcontained-response-disclosure.Tests.ps1 (#1757).

    WHY EACH COPY GETS A POSITIVE CONTROL. A guard that refuses everything passes
    both refusal assertions, so each copy is also asserted to let an allowlisted
    https URL through to the request. The checks pass by discrimination, not
    permissiveness. The positive control asserts only that Invoke-RestMethod was
    reached - what happens to the mocked response afterwards differs per copy (JSON
    vs XML) and is not this file's subject, so a downstream throw is caught and
    ignored deliberately.

    Run locally: Invoke-Pester -Path scripts/tests -Output Detailed
#>

# The floor, not the count. 13 copies carry one of these functions on the tree this
# file was written against (7 already guarded, 6 fixed by #1758). Adding a copy should
# not touch this number; a discovery that returns FEWER than this means the matcher
# broke, and a broken matcher must fail rather than report a clean sweep over nothing.
$script:ExpectedCopyFloor = 13

BeforeDiscovery {
    function Get-WhmcsCredentialSender {
        <#
            Return one record per top-level Invoke-WhmcsApi / Invoke-WhmcsApiXml
            definition under scripts/: the file, the function name, and the name of
            the parameter carrying the URL (whmcs-invoices-export.ps1 calls its
            $ResolvedApiUrl, so the parameter name is read rather than assumed).
        #>
        param([Parameter(Mandatory = $true)][string]$Root)

        $found = @()
        $files = @(Get-ChildItem -Path $Root -Filter *.ps1 -File -Recurse -ErrorAction Stop |
                Where-Object { $_.FullName -notlike '*tests*' })

        foreach ($file in $files) {
            $errors = $null
            $ast = [System.Management.Automation.Language.Parser]::ParseFile(
                $file.FullName, [ref]$null, [ref]$errors)
            if ($errors -and @($errors).Count -gt 0) { continue }
            if (-not $ast.EndBlock) { continue }

            $functions = @($ast.EndBlock.Statements |
                    Where-Object { $_ -is [System.Management.Automation.Language.FunctionDefinitionAst] } |
                    Where-Object { $_.Name -in @('Invoke-WhmcsApi', 'Invoke-WhmcsApiXml') })

            foreach ($fn in $functions) {
                $urlParam = 'ApiUrl'
                $params = @($fn.Body.ParamBlock.Parameters)
                $stringParams = @($params |
                        Where-Object { $_.StaticType -eq [string] } |
                        ForEach-Object { $_.Name.VariablePath.UserPath })
                if ($stringParams.Count -gt 0) { $urlParam = $stringParams[0] }

                # A HASHTABLE, not a pscustomobject: Pester 5 exposes a -ForEach
                # hashtable's keys as variables ($File, $Function, $UrlParam) and
                # expands <Name> in the Describe title. A pscustomobject provides only
                # $_, so the block below would run 13 times against nothing.
                $found += @{
                    Name     = "$($file.Name)::$($fn.Name)"
                    File     = $file.Name
                    Function = $fn.Name
                    UrlParam = $urlParam
                }
            }
        }

        return $found
    }

    # Pester discovers and runs in the SAME process but NOT the same scope: a function
    # or $script: variable created here is gone by the time an It executes - measured,
    # the function raised CommandNotFoundException from BeforeAll, and before that the
    # list read back as @($null), which made one assertion fail outright and would
    # have made a count assertion pass over nothing. What DOES cross the boundary is
    # -ForEach data, which is how File/Function/UrlParam reach the blocks below, so
    # the population summary is handed to the population Describe the same way rather
    # than through a global (which PSScriptAnalyzer flags, rightly).
    $script:CredentialSenders = @(Get-WhmcsCredentialSender -Root (Split-Path $PSScriptRoot -Parent))

    $script:PopulationFacts = @(
        @{
            CopyCount = @($script:CredentialSenders).Count
            Files     = @($script:CredentialSenders | ForEach-Object { $_.File } | Sort-Object -Unique)
        }
    )
}

BeforeAll {
    $script:ScriptsDir = Split-Path $PSScriptRoot -Parent

    # In the allowlist every copy enforces, so the positive control gets through.
    $script:GoodUrl = 'https://freeforcharity.org/hub/includes/api.php'

    # Outside it. `.invalid` is reserved by RFC 2606, so a test that somehow did
    # reach the network could not reach a real host.
    $script:BadHostUrl = 'https://attacker.invalid/hub/includes/api.php'

    # In the allowlist but cleartext: the credential must not be sent over http
    # either, which is why the guard pins the scheme as well as the host.
    $script:CleartextUrl = 'http://freeforcharity.org/hub/includes/api.php'

    function Get-TopLevelFunctionText {
        <#
            The source text of a script's top-level function definitions and nothing
            else, so dot-sourcing it defines the functions without running the file's
            param block, $ErrorActionPreference or live sweep.
        #>
        param([Parameter(Mandatory = $true)][string]$FileName)

        $path = Join-Path $script:ScriptsDir $FileName
        $errors = $null
        $ast = [System.Management.Automation.Language.Parser]::ParseFile($path, [ref]$null, [ref]$errors)
        if ($errors -and @($errors).Count -gt 0) {
            throw "PowerShell parse errors in ${path}: $(@($errors)[0].Message)"
        }

        $functions = @($ast.EndBlock.Statements |
                Where-Object { $_ -is [System.Management.Automation.Language.FunctionDefinitionAst] })
        if ($functions.Count -eq 0) {
            throw "No top-level function definitions in ${path} - the extraction is broken, not the script."
        }

        return (($functions | ForEach-Object { $_.Extent.Text }) -join "`n`n")
    }

    function Get-SenderFunctionAst {
        param(
            [Parameter(Mandatory = $true)][string]$FileName,
            [Parameter(Mandatory = $true)][string]$FunctionName
        )

        $path = Join-Path $script:ScriptsDir $FileName
        $errors = $null
        $ast = [System.Management.Automation.Language.Parser]::ParseFile($path, [ref]$null, [ref]$errors)
        if ($errors -and @($errors).Count -gt 0) {
            throw "PowerShell parse errors in ${path}: $(@($errors)[0].Message)"
        }

        $fn = @($ast.EndBlock.Statements |
                Where-Object { $_ -is [System.Management.Automation.Language.FunctionDefinitionAst] } |
                Where-Object { $_.Name -eq $FunctionName })
        if ($fn.Count -ne 1) {
            throw "Expected exactly one top-level $FunctionName in ${path}, found $($fn.Count)."
        }
        return $fn[0]
    }

    function Get-AllowedHostList {
        <#
            The hosts a copy actually allows, read from the AST rather than by regex,
            so `$allowedHosts = @('a', 'b')` is compared as a set of values and a
            copy cannot quietly widen it.
        #>
        param([Parameter(Mandatory = $true)]$FunctionAst)

        $assignments = @($FunctionAst.FindAll({
                    param($n)
                    $n -is [System.Management.Automation.Language.AssignmentStatementAst] -and
                    $n.Left -is [System.Management.Automation.Language.VariableExpressionAst] -and
                    $n.Left.VariablePath.UserPath -eq 'allowedHosts'
                }, $true))

        if ($assignments.Count -eq 0) { return @() }

        $values = @()
        foreach ($a in $assignments) {
            $strings = @($a.Right.FindAll({
                        param($n) $n -is [System.Management.Automation.Language.StringConstantExpressionAst]
                    }, $true))
            $values += @($strings | ForEach-Object { $_.Value })
        }
        return @($values | Sort-Object -Unique)
    }
}

Describe 'The population of WHMCS credential senders' -ForEach $script:PopulationFacts {

    It 'discovers at least every copy that existed when this guard was written' {
        # Guards the discovery itself. Without this, a matcher that stopped matching
        # would turn every per-copy test below into a vacuous pass.
        $CopyCount |
            Should -BeGreaterOrEqual $script:ExpectedCopyFloor -Because @"
discovery found $CopyCount copies of Invoke-WhmcsApi /
Invoke-WhmcsApiXml under scripts/, below the floor of $($script:ExpectedCopyFloor)
recorded when #1758 was fixed. Either a copy was removed (lower the floor in the same
PR) or the AST matcher no longer matches (fix the matcher - an empty sweep is not a pass).
"@
    }

    It 'finds the copies #1758 named, plus the four its grep could not see' {
        # Named explicitly because the scoping miss is the point of this file: three
        # of these define Invoke-WhmcsApiXml and one has no `whmcs-` filename prefix,
        # so `--include='whmcs-*.ps1'` plus the name Invoke-WhmcsApi found two of six.
        foreach ($expected in @(
                'whmcs-domain-exists.ps1',
                'whmcs-domain-nameservers-update.ps1',
                'whmcs-invoices-export.ps1',
                'whmcs-invoices-lookup.ps1',
                'whmcs-transactions-export.ps1',
                'domain-transfer-epp-probe.ps1')) {
            $Files | Should -Contain $expected
        }
    }
}

Describe '<Name>' -ForEach $script:CredentialSenders {

    BeforeAll {
        $script:FunctionText = Get-TopLevelFunctionText -FileName $File
        $script:FunctionAst = Get-SenderFunctionAst -FileName $File -FunctionName $Function
    }

    BeforeEach {
        # Dot-source into the It's own scope; Invoke-RestMethod resolves through the
        # session state, so the mock applies.
        . ([scriptblock]::Create($script:FunctionText))
    }

    Context 'refuses to send the credential anywhere but a known WHMCS host' {

        It 'rejects a host outside the allowlist, before any request is made' {
            Mock Invoke-RestMethod { throw 'the allowlist must reject before any request is made' }

            # Splatted because the URL parameter is named per copy (ApiUrl, or
            # ResolvedApiUrl in whmcs-invoices-export.ps1), read from the AST at
            # discovery rather than assumed.
            $callArgs = @{ $UrlParam = $script:BadHostUrl; Body = @{ action = 'GetClients' } }

            $err = $null
            try { & $Function @callArgs }
            catch { $err = "$($_.Exception.Message)" }

            # The message is asserted, not just the throw: the mock throws too, so a
            # bare "it threw" cannot tell a working guard from a reached request.
            $err | Should -BeLike '*not in the allowlist*'
            $err | Should -BeLike "*$script:BadHostUrl*"
            Should -Invoke Invoke-RestMethod -Times 0 -Exactly
        }

        It 'rejects http on an allowlisted host, so the credential is never sent in cleartext' {
            Mock Invoke-RestMethod { throw 'the allowlist must reject before any request is made' }

            $callArgs = @{ $UrlParam = $script:CleartextUrl; Body = @{ action = 'GetClients' } }

            $err = $null
            try { & $Function @callArgs }
            catch { $err = "$($_.Exception.Message)" }

            $err | Should -BeLike '*not in the allowlist*'
            Should -Invoke Invoke-RestMethod -Times 0 -Exactly
        }

        It 'lets an allowlisted https URL through to the request' {
            # The positive control. A guard that refused everything would satisfy both
            # assertions above; this is what makes them discriminating.
            Mock Invoke-RestMethod { return '{"result":"success"}' }

            # Deliberately tolerant about what happens AFTER the request: these copies
            # parse JSON or XML and this file's subject is the gate, not the parser.
            $callArgs = @{ $UrlParam = $script:GoodUrl; Body = @{ action = 'GetClients' } }
            # A downstream error is deliberately discarded: it would come from the
            # copy's own JSON/XML handling of a stub response, which is another file's
            # subject. Bound to a variable so the catch is not silently empty.
            $ignored = $null
            try { & $Function @callArgs } catch { $ignored = $_ }
            $null = $ignored

            Should -Invoke Invoke-RestMethod -Times 1 -Exactly
        }
    }

    Context 'the guard in source' {

        It 'allows exactly the two known WHMCS hosts' {
            # A copy that widened its own allowlist would still pass every behavioural
            # test above, because `attacker.invalid` is not what it would have added.
            $allowed = Get-AllowedHostList -FunctionAst $script:FunctionAst
            $allowed | Should -Be @('apim-ffc-gateway-prod.azure-api.net', 'freeforcharity.org')
        }

        It 'refuses before the request, not after it' {
            # Ordering is the whole guard: a host check placed after Invoke-RestMethod
            # would pass a "does it throw" test while the credential had already left.
            $throws = @($script:FunctionAst.FindAll({
                        param($n)
                        $n -is [System.Management.Automation.Language.ThrowStatementAst] -and
                        $n.Extent.Text -like '*not in the allowlist*'
                    }, $true))
            $throws.Count | Should -BeGreaterThan 0 -Because 'the refusal must be a throw, not a warning'

            $requests = @($script:FunctionAst.FindAll({
                        param($n)
                        $n -is [System.Management.Automation.Language.CommandAst] -and
                        $n.GetCommandName() -eq 'Invoke-RestMethod'
                    }, $true))
            $requests.Count | Should -BeGreaterThan 0 -Because 'this function is supposed to make a request'

            $firstThrow = (@($throws | ForEach-Object { $_.Extent.StartOffset }) | Sort-Object)[0]
            $firstRequest = (@($requests | ForEach-Object { $_.Extent.StartOffset }) | Sort-Object)[0]
            $firstThrow | Should -BeLessThan $firstRequest
        }

        It 'pins the scheme to https as well as the host' {
            $script:FunctionAst.Extent.Text | Should -BeLike "*Scheme*https*"
        }
    }
}
