<#
.SYNOPSIS
    Pester (v5) unit tests for the transient-retry path of Invoke-WhmcsApi, and for
    the log-safety property it has to hold.

.DESCRIPTION
    Regression cover for #1753. Invoke-WhmcsApi's retry loop wrote its reason to a
    GitHub Actions log with Write-Warning and repeated it in the terminal throw. This
    repository is public, so those logs are world-readable, and WHMCS responses carry
    ticket text, donor email addresses, client names and billing figures.

    Two defects fed each other:

    1. On a non-success response with no message/errormessage/error field, the error
       string was built by serializing the whole response (up to 800 characters), and
       that string was then assigned to $transientReason, logged on every attempt.

    2. The transient signature ended in \b(429|502|503|504)\b. Applied to a
       serialized JSON body that matches ANY numeric field holding one of those
       values - a ticket id, a client id, an invoice total - because ':' and '}' are
       non-word characters and so satisfy \b. So an ordinary permanent error was
       classified transient, retried five times, and its body printed each time.

    The fix routes every reason through Get-WhmcsTransientLabel, which returns a
    label from a closed set of literals and never any part of its input, and stops
    testing (or logging) a serialization of the response at all.

    The source-guard block at the end covers scripts/whmcs-domain-export.ps1 too:
    that script is self-contained by design and carries its own copy of this retry
    loop, so a fix to the shared helper alone would leave the same hole open.

    Run locally: Invoke-Pester -Path scripts/tests -Output Detailed
#>

BeforeAll {
    $script:ScriptsDir = Split-Path $PSScriptRoot -Parent
    . (Join-Path $script:ScriptsDir 'whmcs-api-common.ps1')

    # In the host allowlist Invoke-WhmcsApi enforces, so the credential guard passes
    # and the request reaches the mocked Invoke-RestMethod.
    $script:ApiUrl = 'https://freeforcharity.org/hub/includes/api.php'

    # Stands in for everything a WHMCS response can carry that must never be logged.
    $script:Sensitive = 'donor@example.org'

    # The complete set of labels Get-WhmcsTransientLabel may return. The security
    # property is that the reason is drawn from THIS set rather than from the
    # response, so the set is asserted to be closed.
    $script:AllowedLabels = @(
        'bot-protection challenge',
        'rate limited',
        'gateway error',
        'service unavailable',
        'request timed out',
        'connection dropped (read)'
    )
}

Describe 'Get-WhmcsTransientLabel' {
    It 'is defined and resolves as a command after dot-sourcing the common module' {
        (Get-Command Get-WhmcsTransientLabel -ErrorAction SilentlyContinue) | Should -Not -BeNullOrEmpty
    }

    Context 'a serialized response body is not transient on the strength of a numeric data field' {
        # The table from #1753. Each of these matched the old bare alternation.
        It 'does not classify a ticket id of 502 as transient' {
            Get-WhmcsTransientLabel '{"result":"error","ticketid":502}' | Should -BeNullOrEmpty
        }

        It 'does not classify a client id of 429 as transient' {
            Get-WhmcsTransientLabel '{"result":"error","clientid":429,"email":"donor@example.org"}' |
                Should -BeNullOrEmpty
        }

        It 'does not classify an invoice total of 503.00 as transient' {
            Get-WhmcsTransientLabel '{"result":"error","total":"503.00"}' | Should -BeNullOrEmpty
        }

        It 'does not classify an unrelated numeric field as transient' {
            Get-WhmcsTransientLabel '{"result":"error","numreplies":3}' | Should -BeNullOrEmpty
        }

        It 'does not classify a WHMCS message that merely contains a status number' {
            Get-WhmcsTransientLabel 'Invoice 502 not found for client 429' | Should -BeNullOrEmpty
        }
    }

    Context 'genuine transient signatures still classify' {
        It 'labels the Imunify360 challenge' {
            Get-WhmcsTransientLabel 'Access denied by Imunify360 bot-protection' |
                Should -Be 'bot-protection challenge'
        }

        It 'labels a PowerShell 7 status-line 502' {
            Get-WhmcsTransientLabel 'Response status code does not indicate success: 502 (Bad Gateway).' |
                Should -Be 'gateway error'
        }

        It 'labels a PowerShell 7 status-line 429' {
            Get-WhmcsTransientLabel 'Response status code does not indicate success: 429 (Too Many Requests).' |
                Should -Be 'rate limited'
        }

        It 'labels the PowerShell 5.1 parenthesised status form' {
            # The Conductor host and windows-latest runners produce this shape, so a
            # status rule anchored only on the PS 7 wording would regress there.
            Get-WhmcsTransientLabel 'The remote server returned an error: (503) Service Unavailable.' |
                Should -Be 'service unavailable'
        }

        It 'labels a raw HTTP status line' {
            Get-WhmcsTransientLabel 'HTTP/1.1 503 Service Unavailable' | Should -Be 'service unavailable'
        }

        It 'labels a .NET timeout' {
            Get-WhmcsTransientLabel 'The operation has timed out' | Should -Be 'request timed out'
        }

        It 'labels a rate-limit message' {
            Get-WhmcsTransientLabel 'Too many requests' | Should -Be 'rate limited'
        }

        It 'labels a temporarily-unavailable message' {
            Get-WhmcsTransientLabel 'Service temporarily unavailable' | Should -Be 'service unavailable'
        }
    }

    Context 'the return value is a fixed label, never the input' {
        It 'returns nothing for null, empty and whitespace' {
            Get-WhmcsTransientLabel $null | Should -BeNullOrEmpty
            Get-WhmcsTransientLabel '' | Should -BeNullOrEmpty
            Get-WhmcsTransientLabel '   ' | Should -BeNullOrEmpty
        }

        It 'never echoes any part of a matching input, for any transient signature' {
            # The property that closes the disclosure: a caller cannot get its own
            # text back out, so it cannot be logged by routing it through here.
            foreach ($signature in @(
                    'Imunify360 bot-protection',
                    'Too many requests',
                    'Bad Gateway',
                    'Service Unavailable',
                    'The operation has timed out'
                )) {
                $text = "WHMCS said $signature about $script:Sensitive"
                $label = Get-WhmcsTransientLabel $text
                $label | Should -Not -BeNullOrEmpty -Because "'$signature' must still be retried"
                $label | Should -Not -BeLike "*$script:Sensitive*"
                $script:AllowedLabels | Should -Contain $label
            }
        }
    }
}

Describe 'Get-WhmcsResponseShape' {
    It 'reports field names and none of their values' {
        $resp = '{"result":"error","ticketid":502,"email":"donor@example.org"}' | ConvertFrom-Json
        $shape = Get-WhmcsResponseShape $resp
        $shape | Should -Be 'result, ticketid, email'
        $shape | Should -Not -BeLike '*donor@example.org*'
        $shape | Should -Not -BeLike '*502*'
    }

    It 'reports hashtable keys' {
        Get-WhmcsResponseShape @{ alpha = 'x' } | Should -Be 'alpha'
    }

    It 'returns nothing for a bare string, which is the payload it must not echo' {
        Get-WhmcsResponseShape $script:Sensitive | Should -Be ''
    }

    It 'returns nothing for null, a number and an array' {
        Get-WhmcsResponseShape $null | Should -Be ''
        Get-WhmcsResponseShape 502 | Should -Be ''
        Get-WhmcsResponseShape @(1, 2, 3) | Should -Be ''
    }

    It 'caps a wide response rather than flooding the log' {
        $wide = [ordered]@{}
        1..40 | ForEach-Object { $wide["field$_"] = 'v' }
        $shape = Get-WhmcsResponseShape $wide
        $shape | Should -BeLike '*, ...'
        $shape.Split(',').Count | Should -BeLessOrEqual 21
    }
}

Describe 'Invoke-WhmcsApi retry path' {
    BeforeEach {
        $script:Warnings = [System.Collections.ArrayList]::new()
        Mock Start-Sleep { }
        Mock Write-Warning { $null = $script:Warnings.Add($Message) }
    }

    AfterEach {
        $env:WHMCS_API_MAX_ATTEMPTS = $null
    }

    It 'fails fast on a non-success response whose only 502 is a ticket id' {
        # #1753's acceptance criterion. Before the fix this was retried 5 times.
        Mock Invoke-RestMethod {
            '{"result":"error","ticketid":502,"email":"donor@example.org"}' | ConvertFrom-Json
        }

        { Invoke-WhmcsApi -ApiUrl $script:ApiUrl -Body @{ action = 'GetTicket' } } | Should -Throw

        Should -Invoke Invoke-RestMethod -Times 1 -Exactly
        $script:Warnings.Count | Should -Be 0
    }

    It 'keeps the response body out of the error raised for an unrecognised response' {
        Mock Invoke-RestMethod {
            '{"result":"error","ticketid":502,"email":"donor@example.org","message":""}' | ConvertFrom-Json
        }

        $err = $null
        try { Invoke-WhmcsApi -ApiUrl $script:ApiUrl -Body @{ action = 'GetTicket' } }
        catch { $err = "$($_.Exception.Message)" }

        $err | Should -Not -BeNullOrEmpty
        $err | Should -Not -BeLike "*$script:Sensitive*"
        # The field names survive, because they are schema and they are what makes
        # the failure diagnosable at all.
        $err | Should -BeLike '*ticketid*'
    }

    It 'retries a genuine transient but logs only the fixed label' {
        $env:WHMCS_API_MAX_ATTEMPTS = '3'
        Mock Invoke-RestMethod {
            '{"result":"error","message":"Service temporarily unavailable for donor@example.org"}' |
                ConvertFrom-Json
        }

        $err = $null
        try { Invoke-WhmcsApi -ApiUrl $script:ApiUrl -Body @{ action = 'GetTicket' } }
        catch { $err = "$($_.Exception.Message)" }

        Should -Invoke Invoke-RestMethod -Times 3 -Exactly
        # One warning per attempt except the last, which throws instead.
        $script:Warnings.Count | Should -Be 2
        foreach ($w in $script:Warnings) {
            $w | Should -Not -BeLike "*$script:Sensitive*"
            $w | Should -BeLike '*service unavailable*'
        }
        # And the terminal throw, which #1753 names separately.
        $err | Should -Not -BeLike "*$script:Sensitive*"
    }

    It 'still retries a transport-level gateway error' {
        # The catch path. Tightening the status rules must not stop a real 502 from
        # being retried, which is the behaviour the loose form was reaching for.
        $env:WHMCS_API_MAX_ATTEMPTS = '2'
        Mock Invoke-RestMethod {
            throw 'Response status code does not indicate success: 502 (Bad Gateway).'
        }

        { Invoke-WhmcsApi -ApiUrl $script:ApiUrl -Body @{ action = 'GetTicket' } } | Should -Throw

        Should -Invoke Invoke-RestMethod -Times 2 -Exactly
        $script:Warnings.Count | Should -Be 1
        $script:Warnings[0] | Should -BeLike '*gateway error*'
    }

    It 'fails fast and without retrying on a permanent authentication error' {
        Mock Invoke-RestMethod {
            '{"result":"error","message":"Invalid IP 203.0.113.7"}' | ConvertFrom-Json
        }

        $err = $null
        try { Invoke-WhmcsApi -ApiUrl $script:ApiUrl -Body @{ action = 'GetTicket' } }
        catch { $err = "$($_.Exception.Message)" }

        Should -Invoke Invoke-RestMethod -Times 1 -Exactly
        $script:Warnings.Count | Should -Be 0
        # WHMCS's own error message is still surfaced: it is the error report, and
        # suppressing it would make every WHMCS failure undiagnosable.
        $err | Should -BeLike '*Invalid IP*'
    }

    It 'retries a read whose response ended prematurely, logging only the fixed label' {
        $env:WHMCS_API_MAX_ATTEMPTS = '3'
        $script:Calls = 0
        Mock Invoke-RestMethod {
            $script:Calls++
            if ($script:Calls -lt 3) { throw 'The response ended prematurely. (ResponseEnded) donor@example.org' }
            '{"result":"success","totalresults":1}' | ConvertFrom-Json
        }

        $out = Invoke-WhmcsApi -ApiUrl $script:ApiUrl -Body @{ action = 'GetClientsProducts' }

        $out.result | Should -Be 'success'
        Should -Invoke Invoke-RestMethod -Times 3 -Exactly
        $script:Warnings.Count | Should -Be 2
        foreach ($w in $script:Warnings) {
            $w | Should -BeLike '*connection dropped (read)*'
            $w | Should -Not -BeLike "*$script:Sensitive*"
        }
    }

    It 'retries a read when the drop text is only on the inner exception (drop before headers)' {
        # The real shape from run 43832248360: PowerShell 7 raises
        # HttpRequestException('An error occurred while sending the request.') and the
        # 'response ended prematurely' text sits on the inner IOException.
        $env:WHMCS_API_MAX_ATTEMPTS = '2'
        $script:Calls = 0
        Mock Invoke-RestMethod {
            $script:Calls++
            if ($script:Calls -lt 2) {
                $inner = [System.IO.IOException]::new('The response ended prematurely. (ResponseEnded)')
                throw [System.Net.Http.HttpRequestException]::new('An error occurred while sending the request.', $inner)
            }
            '{"result":"success"}' | ConvertFrom-Json
        }

        $out = Invoke-WhmcsApi -ApiUrl $script:ApiUrl -Body @{ action = 'GetClientsProducts' }

        $out.result | Should -Be 'success'
        Should -Invoke Invoke-RestMethod -Times 2 -Exactly
        $script:Warnings.Count | Should -Be 1
        $script:Warnings[0] | Should -BeLike '*connection dropped (read)*'
    }

    It 'does not treat a server-supplied error body as a dropped connection' {
        # On an HTTP error status Invoke-RestMethod puts the RESPONSE BODY on
        # $_.ErrorDetails.Message and its own text on the exception. Measured against
        # a real socket: a 400 whose body read 'upstream says connection reset' was
        # classified as a dropped connection and retried while the predicate read
        # ErrorDetails. A body is proof the connection did NOT drop, so body text must
        # never reach the predicate - otherwise the host decides our retry policy.
        $env:WHMCS_API_MAX_ATTEMPTS = '2'
        $script:Calls = 0
        Mock Invoke-RestMethod {
            $script:Calls++
            $er = [System.Management.Automation.ErrorRecord]::new(
                [System.Exception]::new('Response status code does not indicate success: 400 (Bad Request).'),
                'WhmcsHttpError', [System.Management.Automation.ErrorCategory]::InvalidOperation, $null)
            $er.ErrorDetails = [System.Management.Automation.ErrorDetails]::new('upstream says connection reset')
            throw $er
        }

        { Invoke-WhmcsApi -ApiUrl $script:ApiUrl -Body @{ action = 'GetClientsProducts' } } | Should -Throw
        Should -Invoke Invoke-RestMethod -Times 1 -Exactly
        $script:Warnings.Count | Should -Be 0
    }

    It 'does not retry a write whose response ended prematurely: <_>' -ForEach @('AcceptOrder', 'OpenTicket', 'UpdateClient') {
        $action = $_
        Mock Invoke-RestMethod { throw 'The response ended prematurely. (ResponseEnded)' }

        { Invoke-WhmcsApi -ApiUrl $script:ApiUrl -Body @{ action = $action } } | Should -Throw

        Should -Invoke Invoke-RestMethod -Times 1 -Exactly
        $script:Warnings.Count | Should -Be 0
    }

    It 'returns the response unchanged on success' {
        Mock Invoke-RestMethod { '{"result":"success","totalresults":2}' | ConvertFrom-Json }

        $out = Invoke-WhmcsApi -ApiUrl $script:ApiUrl -Body @{ action = 'GetTickets' }

        $out.result | Should -Be 'success'
        $out.totalresults | Should -Be 2
        Should -Invoke Invoke-RestMethod -Times 1 -Exactly
        $script:Warnings.Count | Should -Be 0
    }
}

Describe 'Source guards over every copy of the retry loop' {
    # scripts/whmcs-domain-export.ps1 is self-contained by design - it duplicates
    # Resolve-WhmcsCredentials and Invoke-WhmcsApi rather than dot-sourcing them - so
    # it carried the identical defect. It cannot be dot-sourced here (mandatory param
    # block, runs a live sweep), so it is asserted on its source. A future copy should
    # be added to this list.
    BeforeDiscovery {
        $script:RetryLoopFiles = @('whmcs-api-common.ps1', 'whmcs-domain-export.ps1')
    }

    BeforeAll {
        function Get-PowerShellCodeOnly {
            <#
                Return a file's CODE with comments removed, via the PowerShell
                tokenizer.

                The first draft of these guards scanned the raw text and failed on
                this very commit: both files legitimately quote the pre-fix pattern
                in a .DESCRIPTION block that explains why it was wrong, so a
                raw-text guard makes the fix's own documentation indistinguishable
                from the defect. Parsing also means a guard can never pass on a file
                that does not compile.
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

        function Get-RetryLoopCode {
            param([Parameter(Mandatory = $true)][string]$Name)
            return Get-PowerShellCodeOnly (Join-Path (Split-Path $PSScriptRoot -Parent) $Name)
        }
    }

    It 'never classifies a transient on a serialized response body in <_>' -ForEach $script:RetryLoopFiles {
        # The retry decision must be driven by transport facts, not by anything the
        # host sends in a response body. ErrorDetails.Message IS that body for an HTTP
        # error status, so reading it hands an outside party a lever on our retry
        # policy and on what the exhaustion throw then reports.
        #
        # Asserted on comment-stripped code: the fix documents the trap by NAME in
        # whmcs-api-common.ps1, so a raw-text guard would flag the explanation.
        $code = Get-RetryLoopCode $_
        $code | Should -Not -Match 'ErrorDetails'
    }

    It 'has no loose status alternation in <_>' -ForEach $script:RetryLoopFiles {
        # The exact pre-fix shape, which matched any numeric data field.
        (Get-RetryLoopCode $_).Contains('\b(429|502|503|504)\b') | Should -BeFalse
    }

    It 'does not serialize a response into an error string in <_>' -ForEach $script:RetryLoopFiles {
        $code = Get-RetryLoopCode $_
        $code | Should -Not -Match 'Substring\s*\(\s*0\s*,\s*800\s*\)'
        $code.Contains('Response: $diag') | Should -BeFalse
    }

    It 'never assigns response or exception text to the logged reason in <_>' -ForEach $script:RetryLoopFiles {
        # $transientReason is interpolated into Write-Warning and into the terminal
        # throw, so these two assignments were the disclosure itself.
        #
        # The boundary matters: a naive Contains('$transientReason = $msg') also
        # matches the fixed '$transientReason = $msgLabel', so the first draft of
        # this guard reported the repaired code as still broken.
        $code = Get-RetryLoopCode $_
        $code | Should -Not -Match '\$transientReason\s*=\s*\$msg\b'
        $code | Should -Not -Match '\$transientReason\s*=\s*\$em\b'
    }

    It 'routes the retry reason through the label helper in <_>' -ForEach $script:RetryLoopFiles {
        (Get-RetryLoopCode $_).Contains('Get-WhmcsTransientLabel') | Should -BeTrue
    }
}
