<#
.SYNOPSIS
    Read full WHMCS ticket threads (READ-ONLY) and deliver them ENCRYPTED to a
    key only the requester holds.

.DESCRIPTION
    Workflow 235. Answering a client properly needs the whole conversation:
    every message, who wrote it, and FFC's internal notes. 208 exports subjects
    only and 233 reads a thread but by design never emits a word of it, because
    this repo is PUBLIC -- run logs, job summaries and artifacts can be read by
    anyone. So this script reads the threads and emits them only as ciphertext.

      1. The ticket set: explicit -TicketIds (WHMCS internal ids, the `id`
         column of 208's CSV -- not the 6-digit `tid` shown to the client),
         and/or every ticket of -ClientId (GetTickets by clientid).
      2. For each ticket: GetTicket (all replies, oldest first) and
         GetTicketNotes (FFC's internal notes).
      3. One JSON document holding every thread, encrypted with a fresh
         AES-256-GCM key; that key is wrapped with RSA-OAEP-SHA256 to
         -RecipientPublicKey (base64 DER SubjectPublicKeyInfo, RSA >= 3072).

    The envelope is the ONLY output that carries ticket content. It goes to
    the artifact AND, as the same base64 ciphertext between markers, to the log
    (a cloud session cannot download artifacts; Clarke approved this on
    2026-11-20, #1781). Otherwise the log names ticket counts and the
    recipient key's SHA-256 fingerprint, nothing else: no subject, name, email
    or message in plaintext.

    ticket_ids also takes `#692991`, the number the client sees, resolved with
    GetTicket `ticketnum`, so no 208 export is needed to find the internal id.

    Decrypt with scripts/whmcs-ticket-thread-decrypt.py (`decrypt` for the
    artifact, `decrypt-log` for the job log), which also generates the key pair.

    A private key pasted into the public-key input is refused before any WHMCS
    call, and the refusal does not echo it.

    Pure functions live above the dot-source guard and are unit-tested in
    tests/whmcs-ticket-thread-read.Tests.ps1 (fixture payloads, no network).
#>
[CmdletBinding()]
param(
    [Parameter()]
    [string]$ApiUrl,

    [Parameter()]
    [string]$Identifier,

    [Parameter()]
    [string]$Secret,

    [Parameter()]
    [string]$CredentialsJson,

    [Parameter()]
    [string]$AccessKey,

    # Comma/space separated WHMCS internal ticket ids. May be empty when
    # -ClientId is given.
    [Parameter()]
    [string]$TicketIds = '',

    [Parameter()]
    [int]$ClientId = 0,

    # base64 of the DER SubjectPublicKeyInfo of an RSA key (>= 3072 bits).
    [Parameter()]
    [string]$RecipientPublicKey,

    [Parameter()]
    [string]$OutputFile = 'whmcs-ticket-threads.enc.json',

    [Parameter()]
    [ValidateRange(1, 100)]
    [int]$MaxTickets = 50
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'whmcs-api-common.ps1')

$script:ThreadEnvelopeFormat = 'ffc-whmcs-ticket-threads-v1'
$script:ThreadEnvelopeAlg = 'RSA-OAEP-SHA256+A256GCM'
$script:ThreadMinRsaBits = 3072

function ConvertTo-TicketIdList {
    <#
    .SYNOPSIS
        Parse "281, 280 279" into distinct positive ints. Throws on any token
        that is not 1-9 digits, without echoing the token.
    #>
    [OutputType([int[]])]
    param([AllowNull()][AllowEmptyString()][string]$Text, [int]$Max = 50)
    if ([string]::IsNullOrWhiteSpace($Text)) { return @() }
    $ids = [System.Collections.Generic.List[int]]::new()
    foreach ($tok in ($Text -split '[,\s]+')) {
        if ($tok -eq '') { continue }
        $n = 0
        if ($tok -notmatch '^\d{1,9}$' -or -not [int]::TryParse($tok, [ref]$n) -or $n -lt 1) {
            throw 'ticket_ids must be WHMCS internal ticket ids: positive integers separated by commas or spaces.'
        }
        if (-not $ids.Contains($n)) { $ids.Add($n) }
    }
    if ($ids.Count -gt $Max) { throw "At most $Max tickets per run." }
    return , $ids.ToArray()
}

function Split-TicketRefText {
    <#
    .SYNOPSIS
        Split the ticket_ids text into internal ids and client-facing ticket
        numbers. A token written `#692991` is the number the client sees (WHMCS
        `tid`), looked up with GetTicket's `ticketnum`; a bare token is an
        internal id. Throws on a malformed `#` token without echoing it.
    #>
    [OutputType([hashtable])]
    param([AllowNull()][AllowEmptyString()][string]$Text, [int]$Max = 50)
    $plain = [System.Collections.Generic.List[string]]::new()
    $numbers = [System.Collections.Generic.List[string]]::new()
    if (-not [string]::IsNullOrWhiteSpace($Text)) {
        foreach ($tok in ($Text -split '[,\s]+')) {
            if ($tok -eq '') { continue }
            if ($tok.StartsWith('#')) {
                $n = $tok.Substring(1)
                if ($n -notmatch '^\d{1,12}$') {
                    throw 'A #ticket number must be the digits the client sees, e.g. #692991.'
                }
                if (-not $numbers.Contains($n)) { $numbers.Add($n) }
            }
            else { $plain.Add($tok) }
        }
    }
    # ConvertTo-TicketIdList returns its array as ONE object (`return , ...`);
    # wrapping the call in @() would nest it, so assign it bare.
    [int[]]$ids = ConvertTo-TicketIdList -Text ($plain -join ',') -Max $Max
    if ($null -eq $ids) { $ids = @() }
    if (($ids.Count + $numbers.Count) -gt $Max) { throw "At most $Max tickets per run." }
    return @{ Ids = $ids; Numbers = $numbers.ToArray() }
}

function Format-EnvelopeLogBlock {
    <#
    .SYNOPSIS
        The envelope as log lines, so a session that cannot download artifacts
        (the cloud egress proxy refuses the artifact host) can still read it.
        It is the SAME ciphertext the artifact holds: base64 of the compact
        envelope JSON, in fixed-width chunks between markers, with a SHA-256
        of the base64 so a truncated or masked copy is detected, not decrypted
        wrong. No line starts with `::`, so none is read as a workflow command.
        Clarke approved carrying the ciphertext in the log on 2026-11-20 (#1781).
    #>
    [OutputType([string[]])]
    param([Parameter(Mandatory = $true)]$Envelope, [int]$Width = 4000)
    $b64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes(($Envelope | ConvertTo-Json -Depth 3 -Compress)))
    $sha = [Convert]::ToHexString([System.Security.Cryptography.SHA256]::HashData([System.Text.Encoding]::ASCII.GetBytes($b64))).ToLowerInvariant()
    $lines = [System.Collections.Generic.List[string]]::new()
    $count = [int][Math]::Ceiling($b64.Length / $Width)
    $lines.Add("BEGIN-FFC-TICKET-ENVELOPE sha256=$sha chunks=$count")
    for ($i = 0; $i -lt $b64.Length; $i += $Width) {
        $lines.Add('FFC-ENV ' + $b64.Substring($i, [Math]::Min($Width, $b64.Length - $i)))
    }
    $lines.Add('END-FFC-TICKET-ENVELOPE')
    return $lines.ToArray()
}

function Import-RecipientPublicKey {
    <#
    .SYNOPSIS
        base64 DER SubjectPublicKeyInfo -> RSA object. Refuses private keys,
        PEM armour, non-RSA keys and keys under the minimum size. Messages are
        fixed text: the input is never echoed.
    #>
    [OutputType([System.Security.Cryptography.RSA])]
    param([AllowNull()][AllowEmptyString()][string]$Base64, [int]$MinBits = $script:ThreadMinRsaBits)
    if ([string]::IsNullOrWhiteSpace($Base64)) { throw 'recipient_public_key is required.' }
    if ($Base64 -match 'PRIVATE') {
        throw 'recipient_public_key looks like a PRIVATE key. Never paste a private key into a workflow input; this run has stopped. Generate a new key pair -- treat the pasted one as exposed.'
    }
    $clean = ($Base64 -replace '-----(BEGIN|END) PUBLIC KEY-----', '') -replace '\s', ''
    $der = $null
    try { $der = [Convert]::FromBase64String($clean) } catch { throw 'recipient_public_key is not valid base64.' }
    $rsa = [System.Security.Cryptography.RSA]::Create()
    $read = 0
    try { $rsa.ImportSubjectPublicKeyInfo($der, [ref]$read) } catch {
        # A PKCS#8 / PKCS#1 private key in DER would land here; say so plainly.
        $probe = [System.Security.Cryptography.RSA]::Create()
        $isPrivate = $false
        try { $probe.ImportPkcs8PrivateKey($der, [ref]$read); $isPrivate = $true } catch { }
        try { if (-not $isPrivate) { $probe.ImportRSAPrivateKey($der, [ref]$read); $isPrivate = $true } } catch { }
        if ($isPrivate) {
            throw 'recipient_public_key is a PRIVATE key. Never paste a private key into a workflow input; this run has stopped. Generate a new key pair -- treat the pasted one as exposed.'
        }
        throw 'recipient_public_key is not an RSA SubjectPublicKeyInfo (base64 DER).'
    }
    if ($read -ne $der.Length) { throw 'recipient_public_key has trailing data.' }
    if ($rsa.KeySize -lt $MinBits) { throw "recipient_public_key must be RSA with at least $MinBits bits." }
    return $rsa
}

function Get-RecipientKeyFingerprint {
    [OutputType([string])]
    param([Parameter(Mandatory = $true)][System.Security.Cryptography.RSA]$Rsa)
    $spki = $Rsa.ExportSubjectPublicKeyInfo()
    $h = [System.Security.Cryptography.SHA256]::HashData($spki)
    return 'SHA256:' + ([Convert]::ToHexString($h).ToLowerInvariant())
}

function Protect-ThreadPayload {
    <#
    .SYNOPSIS
        Encrypt bytes with a fresh AES-256-GCM key and wrap that key with
        RSA-OAEP-SHA256. Returns the envelope object (all binary as base64).
        The fingerprint is bound in as additional authenticated data.
    #>
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)][byte[]]$Plain,
        [Parameter(Mandatory = $true)][System.Security.Cryptography.RSA]$Rsa
    )
    $fp = Get-RecipientKeyFingerprint -Rsa $Rsa
    $key = [byte[]]::new(32); [System.Security.Cryptography.RandomNumberGenerator]::Fill($key)
    $nonce = [byte[]]::new(12); [System.Security.Cryptography.RandomNumberGenerator]::Fill($nonce)
    $cipher = [byte[]]::new($Plain.Length)
    $tag = [byte[]]::new(16)
    $aad = [System.Text.Encoding]::UTF8.GetBytes("$($script:ThreadEnvelopeFormat)|$fp")
    $gcm = [System.Security.Cryptography.AesGcm]::new($key, 16)
    try { $gcm.Encrypt($nonce, $Plain, $cipher, $tag, $aad) } finally { $gcm.Dispose() }
    $wrapped = $Rsa.Encrypt($key, [System.Security.Cryptography.RSAEncryptionPadding]::OaepSHA256)
    [Array]::Clear($key, 0, $key.Length)
    return [pscustomobject]@{
        format          = $script:ThreadEnvelopeFormat
        alg             = $script:ThreadEnvelopeAlg
        key_fingerprint = $fp
        wrapped_key     = [Convert]::ToBase64String($wrapped)
        nonce           = [Convert]::ToBase64String($nonce)
        tag             = [Convert]::ToBase64String($tag)
        ciphertext      = [Convert]::ToBase64String($cipher)
    }
}

function Get-ThreadProp {
    param($Node, [string]$Name)
    if ($null -eq $Node) { return $null }
    $p = $Node.PSObject.Properties[$Name]
    if ($null -eq $p) { return $null }
    return $p.Value
}

function ConvertTo-ThreadRecord {
    <#
    .SYNOPSIS
        GetTicket (+ GetTicketNotes) payloads -> one plain record with the
        fields a reply needs. Messages are kept verbatim (WHMCS may return
        HTML); `admin` names the FFC staff member, empty means the client.
    #>
    [OutputType([pscustomobject])]
    param([Parameter(Mandatory = $true)]$Ticket, $Notes)
    # Whole pipeline inside @(), as whmcs-charity-lane-verdicts.ps1 does, so an
    # empty thread is an empty array by construction (pinned by Pester).
    $replies = @(Get-WhmcsNodeList -Node (Get-ThreadProp $Ticket 'replies') -ChildName 'reply' | ForEach-Object {
            [pscustomobject]@{
                replyid     = [string](Get-ThreadProp $_ 'replyid')
                date        = [string](Get-ThreadProp $_ 'date')
                from_admin  = [string](Get-ThreadProp $_ 'admin')
                name        = [string](Get-ThreadProp $_ 'name')
                email       = [string](Get-ThreadProp $_ 'email')
                message     = [string](Get-ThreadProp $_ 'message')
                attachments = [string](Get-ThreadProp $_ 'attachment')
            }
        })
    $noteList = @(Get-WhmcsNodeList -Node (Get-ThreadProp $Notes 'notes') -ChildName 'note' | ForEach-Object {
            [pscustomobject]@{
                date    = [string](Get-ThreadProp $_ 'date')
                admin   = [string](Get-ThreadProp $_ 'admin')
                message = [string](Get-ThreadProp $_ 'message')
            }
        })
    return [pscustomobject]@{
        id        = [string](Get-ThreadProp $Ticket 'ticketid')
        tid       = [string](Get-ThreadProp $Ticket 'tid')
        userid    = [string](Get-ThreadProp $Ticket 'userid')
        deptname  = [string](Get-ThreadProp $Ticket 'deptname')
        subject   = [string](Get-ThreadProp $Ticket 'subject')
        status    = [string](Get-ThreadProp $Ticket 'status')
        priority  = [string](Get-ThreadProp $Ticket 'priority')
        opened    = [string](Get-ThreadProp $Ticket 'date')
        lastreply = [string](Get-ThreadProp $Ticket 'lastreply')
        name      = [string](Get-ThreadProp $Ticket 'name')
        email     = [string](Get-ThreadProp $Ticket 'email')
        replies   = @($replies)
        notes     = @($noteList)
    }
}

function Get-ThreadSafeError {
    # First line only, and nothing after a response excerpt: Invoke-WhmcsApi's
    # diagnostics can quote the API body, which here would be the thread.
    [OutputType([string])]
    param([AllowNull()][AllowEmptyString()][string]$Message)
    if ([string]::IsNullOrWhiteSpace($Message)) { return 'unknown error' }
    $m = ($Message -split "`r?`n")[0]
    foreach ($cut in @('non-JSON response', 'Response:')) {
        $i = $m.IndexOf($cut, [System.StringComparison]::OrdinalIgnoreCase)
        if ($i -ge 0) { $m = $m.Substring(0, $i) + '(body withheld)' }
    }
    if ($m.Length -gt 300) { $m = $m.Substring(0, 300) + '...' }
    return $m
}

function Invoke-TicketThreadRead {
    <#
    .SYNOPSIS
        The whole read. -InvokeApi takes one hashtable of action fields (the
        caller adds auth), so tests run it on fixtures. Returns the plaintext
        document object; the caller encrypts it.
    #>
    [OutputType([pscustomobject])]
    param(
        [int[]]$TicketIds = @(),
        [string[]]$TicketNumbers = @(),
        [int]$ClientId = 0,
        [Parameter(Mandatory = $true)][scriptblock]$InvokeApi,
        [int]$MaxTickets = 50,
        [string]$GeneratedAt = '',
        [string]$RunUrl = ''
    )
    $ids = [System.Collections.Generic.List[int]]::new()
    foreach ($i in @($TicketIds)) { if (-not $ids.Contains($i)) { $ids.Add($i) } }
    # Client-facing numbers resolve to internal ids. An unknown number throws:
    # it was named on purpose, so a silent skip would hide a typo.
    foreach ($num in @($TicketNumbers)) {
        if ([string]::IsNullOrWhiteSpace($num)) { continue }
        $found = & $InvokeApi @{ action = 'GetTicket'; ticketnum = $num; repliessort = 'ASC' }
        $resolved = 0
        if (-not [int]::TryParse([string](Get-ThreadProp $found 'ticketid'), [ref]$resolved) -or $resolved -lt 1) {
            throw 'A #ticket number did not resolve to a ticket.'
        }
        if (-not $ids.Contains($resolved)) { $ids.Add($resolved) }
    }
    # Ids that came only from the client listing may be skipped when WHMCS
    # cannot open them (merged/removed tickets); a named id may not.
    $listedOnly = [System.Collections.Generic.HashSet[int]]::new()
    if ($ClientId -gt 0) {
        $start = 0
        while ($true) {
            $resp = & $InvokeApi @{ action = 'GetTickets'; clientid = $ClientId; limitstart = $start; limitnum = 100; ignore_dept_assignments = $true }
            $page = @(Get-WhmcsNodeList -Node (Get-ThreadProp $resp 'tickets') -ChildName 'ticket')
            foreach ($listed in $page) {
                $listedId = 0
                if ([int]::TryParse([string](Get-ThreadProp $listed 'id'), [ref]$listedId) -and $listedId -gt 0 -and -not $ids.Contains($listedId)) { $ids.Add($listedId); [void]$listedOnly.Add($listedId) }
            }
            if ($page.Count -le 0) { break }
            $start += $page.Count
            $total = 0
            [void][int]::TryParse([string](Get-ThreadProp $resp 'totalresults'), [ref]$total)
            if ($total -gt 0 -and $start -ge $total) { break }
            # Stop paging once the cap is already exceeded: the refusal below
            # fires either way, so further GetTickets pages are wasted calls.
            if ($ids.Count -gt $MaxTickets) { break }
            if ($start -ge 1000) { break }
        }
    }
    if ($ids.Count -eq 0) { throw 'No tickets to read: give ticket_ids and/or a client_id that has tickets.' }
    if ($ids.Count -gt $MaxTickets) { throw "That selects more than $MaxTickets tickets; at most $MaxTickets per run. Narrow it with ticket_ids." }

    $skipped = [System.Collections.Generic.List[int]]::new()
    $threads = foreach ($id in ($ids | Sort-Object)) {
        try {
            $ticketResp = & $InvokeApi @{ action = 'GetTicket'; ticketid = $id; repliessort = 'ASC' }
        }
        catch {
            if ($listedOnly.Contains($id) -and $_.Exception.Message -match 'Ticket ID Not Found') { $skipped.Add($id); continue }
            throw
        }
        $notesResp = & $InvokeApi @{ action = 'GetTicketNotes'; ticketid = $id }
        ConvertTo-ThreadRecord -Ticket $ticketResp -Notes $notesResp
    }
    if (@($threads).Count -eq 0) { throw 'None of the selected tickets could be opened (WHMCS: Ticket ID Not Found).' }
    return [pscustomobject]@{
        generated_at = $GeneratedAt
        run_url      = $RunUrl
        client_id    = $ClientId
        ticket_count = @($threads).Count
        skipped_ids  = @($skipped)
        note         = 'WHMCS dates are in the WHMCS system time zone, unconverted. from_admin empty = written by the client.'
        tickets      = @($threads)
    }
}

# When dot-sourced (unit tests), stop here: only the functions are needed.
if ($MyInvocation.InvocationName -eq '.') { return }

# ==========================================================================
# Runner (live, READ-ONLY): GetTickets, GetTicket, GetTicketNotes.
# ==========================================================================

try {
    # Validate everything that needs no network first, so a bad key or id list
    # costs no WHMCS call.
    $rsa = Import-RecipientPublicKey -Base64 $RecipientPublicKey
    $refs = Split-TicketRefText -Text $TicketIds -Max $MaxTickets
    $idList = @($refs.Ids)
    if ($idList.Count -eq 0 -and @($refs.Numbers).Count -eq 0 -and $ClientId -lt 1) { throw 'Give ticket_ids, client_id, or both.' }

    $api = Resolve-WhmcsApiUrl -ApiUrlParam $ApiUrl
    $creds = Resolve-WhmcsCredentials -IdentifierParam $Identifier -SecretParam $Secret -CredentialsJsonParam $CredentialsJson
    $auth = New-WhmcsAuthBody -Creds $creds -AccessKey (Resolve-WhmcsAccessKey -AccessKeyParam $AccessKey)
    $invoke = {
        param([hashtable]$Body)
        $b = $auth.Clone()
        foreach ($k in $Body.Keys) { $b[$k] = $Body[$k] }
        Invoke-WhmcsApi -ApiUrl $api -Body $b
    }

    $runUrl = ''
    if ($env:GITHUB_RUN_ID) { $runUrl = "$($env:GITHUB_SERVER_URL)/$($env:GITHUB_REPOSITORY)/actions/runs/$($env:GITHUB_RUN_ID)" }
    $doc = Invoke-TicketThreadRead -TicketIds $idList -TicketNumbers @($refs.Numbers) -ClientId $ClientId -InvokeApi $invoke -MaxTickets $MaxTickets `
        -GeneratedAt ((Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')) -RunUrl $runUrl

    $plain = [System.Text.Encoding]::UTF8.GetBytes(($doc | ConvertTo-Json -Depth 8))
    $envelope = Protect-ThreadPayload -Plain $plain -Rsa $rsa
    [Array]::Clear($plain, 0, $plain.Length)

    $dir = Split-Path -Parent $OutputFile
    if (-not [string]::IsNullOrWhiteSpace($dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $envelope | ConvertTo-Json -Depth 3 | Out-File -FilePath $OutputFile -Encoding utf8

    $replyCount = (@($doc.tickets) | ForEach-Object { @($_.replies).Count } | Measure-Object -Sum).Sum
    $skippedCount = @($doc.skipped_ids).Count
    $summary = "Encrypted $($doc.ticket_count) ticket thread(s), $replyCount message(s), to recipient key $($envelope.key_fingerprint). Skipped (not found): $skippedCount."
    Write-Host $summary
    # Same ciphertext as the artifact, for sessions that cannot download artifacts.
    foreach ($envLine in (Format-EnvelopeLogBlock -Envelope $envelope)) { Write-Host $envLine }
    if (-not [string]::IsNullOrWhiteSpace($env:GITHUB_STEP_SUMMARY)) {
        @('## WHMCS ticket threads (encrypted)', '', $summary, '',
            'Decrypt: `python3 scripts/whmcs-ticket-thread-decrypt.py decrypt --key <private.pem> <envelope.json>`',
            'or, from the job log: `python3 scripts/whmcs-ticket-thread-decrypt.py decrypt-log --key <private.pem> <job.log>`') |
            Out-File -FilePath $env:GITHUB_STEP_SUMMARY -Append -Encoding utf8
    }
    exit 0
}
catch {
    Write-Output "::error::Ticket thread read failed: $(Get-ThreadSafeError -Message $_.Exception.Message)"
    exit 1
}
