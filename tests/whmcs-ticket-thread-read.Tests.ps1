#requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0.0' }
<#
    Unit tests for scripts/whmcs-ticket-thread-read.ps1 (workflow 235).
    Dot-sourced, so NO WHMCS call is made: GetTickets / GetTicket /
    GetTicketNotes payloads are fixtures fed through -InvokeApi.

    Covers: ticket id parsing, recipient-key validation (private keys and
    weak keys refused, refusals never echo the input), thread record shape,
    client-id expansion, the ticket cap, and the envelope contract (no
    plaintext, fingerprint bound in, decrypts back to the document).

    Run: Invoke-Pester tests/whmcs-ticket-thread-read.Tests.ps1
#>

BeforeAll {
    . (Join-Path $PSScriptRoot '..' 'scripts' 'whmcs-ticket-thread-read.ps1')

    $script:Rsa = [System.Security.Cryptography.RSA]::Create(3072)
    $script:PubB64 = [Convert]::ToBase64String($script:Rsa.ExportSubjectPublicKeyInfo())

    $script:Sentinel = 'SENTINEL-4417 jane.doe@example.org'
    function New-TicketFixture([string]$Id) {
        [pscustomobject]@{
            result = 'success'; ticketid = $Id; tid = "T$Id"; userid = '77'; deptname = 'Support'
            subject = "Payment Blocked $Id"; status = 'Open'; priority = 'High'; date = '2026-11-09 21:52:45'
            lastreply = '2026-11-09 22:06:00'; name = 'Jane Doe'; email = 'jane.doe@example.org'
            replies = [pscustomobject]@{ reply = @(
                    [pscustomobject]@{ replyid = '0'; date = '2026-11-09 21:52:45'; admin = ''; name = 'Jane Doe'; email = 'jane.doe@example.org'; message = $script:Sentinel; attachment = '' }
                    [pscustomobject]@{ replyid = '9'; date = '2026-11-09 23:00:00'; admin = 'Clarke Moyer'; name = ''; email = ''; message = 'We are looking'; attachment = '' }
                ) 
            }
        }
    }
    $script:Calls = [System.Collections.Generic.List[string]]::new()
    $script:Api = {
        param([hashtable]$Body)
        $script:Calls.Add("$($Body.action):$($Body.ticketid)$($Body.clientid)")
        switch ($Body.action) {
            'GetTickets' { [pscustomobject]@{ result = 'success'; totalresults = '2'; tickets = [pscustomobject]@{ ticket = @([pscustomobject]@{ id = '281' }, [pscustomobject]@{ id = '279' }) } } }
            'GetTicket' { New-TicketFixture ([string]$Body.ticketid) }
            'GetTicketNotes' { [pscustomobject]@{ result = 'success'; notes = [pscustomobject]@{ note = @([pscustomobject]@{ date = 'd'; admin = 'Clarke Moyer'; message = 'internal note' }) } } }
            default { throw "unexpected action $($Body.action)" }
        }
    }

    function Unprotect-Envelope($Envelope, [System.Security.Cryptography.RSA]$Rsa) {
        $key = $Rsa.Decrypt([Convert]::FromBase64String($Envelope.wrapped_key), [System.Security.Cryptography.RSAEncryptionPadding]::OaepSHA256)
        $cipher = [Convert]::FromBase64String($Envelope.ciphertext)
        $plain = [byte[]]::new($cipher.Length)
        $aad = [System.Text.Encoding]::UTF8.GetBytes("$($Envelope.format)|$($Envelope.key_fingerprint)")
        $gcm = [System.Security.Cryptography.AesGcm]::new($key, 16)
        $gcm.Decrypt([Convert]::FromBase64String($Envelope.nonce), $cipher, [Convert]::FromBase64String($Envelope.tag), $plain, $aad)
        return [System.Text.Encoding]::UTF8.GetString($plain)
    }
}

Describe 'ConvertTo-TicketIdList' {
    It 'parses commas and spaces, dedupes' {
        (ConvertTo-TicketIdList '281, 280 279,281') -join ',' | Should -Be '281,280,279'
    }
    It 'returns empty for blank' {
        @(ConvertTo-TicketIdList '  ').Count | Should -Be 0
    }
    It 'refuses a non-numeric token without echoing it' {
        { ConvertTo-TicketIdList '281,$(evil)' } | Should -Throw -ExpectedMessage '*positive integers*'
        try { ConvertTo-TicketIdList '281,$(evil)' } catch { $_.Exception.Message | Should -Not -BeLike '*evil*' }
    }
    It 'enforces the cap' {
        { ConvertTo-TicketIdList '1,2,3' -Max 2 } | Should -Throw -ExpectedMessage '*At most 2*'
    }
}

Describe 'Import-RecipientPublicKey' {
    It 'accepts a 3072-bit SubjectPublicKeyInfo' {
        (Import-RecipientPublicKey -Base64 $script:PubB64).KeySize | Should -Be 3072
    }
    It 'accepts PEM armour around the same key' {
        $pem = "-----BEGIN PUBLIC KEY-----`n$($script:PubB64)`n-----END PUBLIC KEY-----"
        (Import-RecipientPublicKey -Base64 $pem).KeySize | Should -Be 3072
    }
    It 'refuses a PEM private key and does not echo it' {
        $pem = $script:Rsa.ExportPkcs8PrivateKeyPem()
        { Import-RecipientPublicKey -Base64 $pem } | Should -Throw -ExpectedMessage '*PRIVATE*'
        try { Import-RecipientPublicKey -Base64 $pem } catch { $_.Exception.Message | Should -Not -BeLike '*MII*' }
    }
    It 'refuses a bare base64 DER private key (no PEM word to match)' {
        $der = [Convert]::ToBase64String($script:Rsa.ExportPkcs8PrivateKey())
        { Import-RecipientPublicKey -Base64 $der } | Should -Throw -ExpectedMessage '*PRIVATE key*'
    }
    It 'refuses a key below the minimum size' {
        $small = [System.Security.Cryptography.RSA]::Create(2048)
        { Import-RecipientPublicKey -Base64 ([Convert]::ToBase64String($small.ExportSubjectPublicKeyInfo())) } | Should -Throw -ExpectedMessage '*3072*'
    }
    It 'refuses blank and non-base64 input' {
        { Import-RecipientPublicKey -Base64 '' } | Should -Throw -ExpectedMessage '*required*'
        { Import-RecipientPublicKey -Base64 'not!base64' } | Should -Throw -ExpectedMessage '*base64*'
    }
}

Describe 'Invoke-TicketThreadRead' {
    BeforeEach { $script:Calls.Clear() }

    It 'reads explicit ids with replies and notes, oldest reply first' {
        $doc = Invoke-TicketThreadRead -TicketIds @(281) -InvokeApi $script:Api
        $doc.ticket_count | Should -Be 1
        $t = $doc.tickets[0]
        $t.id | Should -Be '281'
        $t.subject | Should -Be 'Payment Blocked 281'
        $t.replies.Count | Should -Be 2
        $t.replies[0].message | Should -Be $script:Sentinel
        $t.replies[0].from_admin | Should -Be ''
        $t.replies[1].from_admin | Should -Be 'Clarke Moyer'
        $t.notes[0].message | Should -Be 'internal note'
        $script:Calls | Should -Contain 'GetTicketNotes:281'
    }
    It 'expands a client id and merges with explicit ids, without duplicates' {
        $doc = Invoke-TicketThreadRead -TicketIds @(281, 300) -ClientId 77 -InvokeApi $script:Api
        ($doc.tickets.id -join ',') | Should -Be '279,281,300'
    }
    It 'uses only read actions' {
        Invoke-TicketThreadRead -ClientId 77 -InvokeApi $script:Api | Out-Null
        ($script:Calls | ForEach-Object { ($_ -split ':')[0] } | Sort-Object -Unique) -join ',' | Should -Be 'GetTicket,GetTicketNotes,GetTickets'
    }
    It 'refuses more tickets than the cap' {
        { Invoke-TicketThreadRead -TicketIds @(1, 2, 3) -InvokeApi $script:Api -MaxTickets 2 } | Should -Throw -ExpectedMessage '*at most 2*'
    }
    It 'delivers an empty thread and no notes as empty arrays, never [null]' {
        foreach ($shape in @($null, '', [pscustomobject]@{ reply = @() })) {
            $empty = {
                param([hashtable]$Body)
                switch ($Body.action) {
                    'GetTicket' { [pscustomobject]@{ result = 'success'; ticketid = '5'; replies = $shape } }
                    'GetTicketNotes' { [pscustomobject]@{ result = 'success'; notes = [pscustomobject]@{ note = @() } } }
                }
            }
            $doc = Invoke-TicketThreadRead -TicketIds @(5) -InvokeApi $empty
            $json = $doc | ConvertTo-Json -Depth 8 -Compress
            $json | Should -Not -BeLike '*null*'
            $json | Should -BeLike '*"replies":`[`]*'
            $json | Should -BeLike '*"notes":`[`]*'
            @($doc.tickets[0].replies).Count | Should -Be 0
        }
    }
    It 'stops paging GetTickets once the cap is exceeded' {
        $script:Pages = 0
        $many = {
            param([hashtable]$Body)
            if ($Body.action -ne 'GetTickets') { throw 'no thread read expected' }
            $script:Pages++
            $base = [int]$Body.limitstart
            [pscustomobject]@{ result = 'success'; totalresults = '900'; tickets = [pscustomobject]@{ ticket = @(1..100 | ForEach-Object { [pscustomobject]@{ id = [string]($base + $_) } }) } }
        }
        { Invoke-TicketThreadRead -ClientId 77 -InvokeApi $many -MaxTickets 50 } | Should -Throw -ExpectedMessage '*more than 50*'
        $script:Pages | Should -Be 1
    }
    It 'refuses an empty selection' {
        { Invoke-TicketThreadRead -InvokeApi $script:Api } | Should -Throw -ExpectedMessage '*No tickets*'
    }
}

Describe 'Protect-ThreadPayload' {
    It 'carries no plaintext and round-trips to the document' {
        $doc = Invoke-TicketThreadRead -TicketIds @(281) -InvokeApi $script:Api
        $json = $doc | ConvertTo-Json -Depth 8
        $rsa = Import-RecipientPublicKey -Base64 $script:PubB64
        $env = Protect-ThreadPayload -Plain ([System.Text.Encoding]::UTF8.GetBytes($json)) -Rsa $rsa
        $serialized = $env | ConvertTo-Json -Depth 3
        foreach ($s in @('SENTINEL-4417', 'jane.doe', 'Jane Doe', 'Payment Blocked', 'internal note')) {
            $serialized | Should -Not -BeLike "*$s*"
        }
        $env.format | Should -Be 'ffc-whmcs-ticket-threads-v1'
        $env.key_fingerprint | Should -Be (Get-RecipientKeyFingerprint -Rsa $rsa)
        Unprotect-Envelope $env $script:Rsa | Should -Be $json
    }
    It 'fails authentication if the fingerprint is altered' {
        $rsa = Import-RecipientPublicKey -Base64 $script:PubB64
        $env = Protect-ThreadPayload -Plain ([byte[]](1, 2, 3)) -Rsa $rsa
        $env.key_fingerprint = 'SHA256:00'
        { Unprotect-Envelope $env $script:Rsa } | Should -Throw
    }
}

Describe 'Get-ThreadSafeError' {
    It 'cuts response excerpts' {
        Get-ThreadSafeError -Message 'WHMCS error. Response: {"message":"secret thread"}' | Should -Not -BeLike '*secret thread*'
    }
}
