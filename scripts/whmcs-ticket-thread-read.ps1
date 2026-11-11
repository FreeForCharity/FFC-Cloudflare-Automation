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

    The envelope is the ONLY output that carries ticket content. The log names
    ticket counts and the recipient key's SHA-256 fingerprint, nothing else: no
    subject, name, email or message. Decrypt with
    scripts/whmcs-ticket-thread-decrypt.py, which also generates the key pair.

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
    $replies = @(Get-WhmcsNodeList -Node (Get-ThreadProp $Ticket 'replies') -ChildName 'reply') | ForEach-Object {
        [pscustomobject]@{
            replyid     = [string](Get-ThreadProp $_ 'replyid')
            date        = [string](Get-ThreadProp $_ 'date')
            from_admin  = [string](Get-ThreadProp $_ 'admin')
            name        = [string](Get-ThreadProp $_ 'name')
            email       = [string](Get-ThreadProp $_ 'email')
            message     = [string](Get-ThreadProp $_ 'message')
            attachments = [string](Get-ThreadProp $_ 'attachment')
        }
    }
    $noteList = @(Get-WhmcsNodeList -Node (Get-ThreadProp $Notes 'notes') -ChildName 'note') | ForEach-Object {
        [pscustomobject]@{
            date    = [string](Get-ThreadProp $_ 'date')
            admin   = [string](Get-ThreadProp $_ 'admin')
            message = [string](Get-ThreadProp $_ 'message')
        }
    }
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
        [int]$ClientId = 0,
        [Parameter(Mandatory = $true)][scriptblock]$InvokeApi,
        [int]$MaxTickets = 50,
        [string]$GeneratedAt = '',
        [string]$RunUrl = ''
    )
    $ids = [System.Collections.Generic.List[int]]::new()
    foreach ($i in @($TicketIds)) { if (-not $ids.Contains($i)) { $ids.Add($i) } }
    if ($ClientId -gt 0) {
        $start = 0
        while ($true) {
            $resp = & $InvokeApi @{ action = 'GetTickets'; clientid = $ClientId; limitstart = $start; limitnum = 100; ignore_dept_assignments = $true }
            $page = @(Get-WhmcsNodeList -Node (Get-ThreadProp $resp 'tickets') -ChildName 'ticket')
            foreach ($listed in $page) {
                $listedId = 0
                if ([int]::TryParse([string](Get-ThreadProp $listed 'id'), [ref]$listedId) -and $listedId -gt 0 -and -not $ids.Contains($listedId)) { $ids.Add($listedId) }
            }
            if ($page.Count -le 0) { break }
            $start += $page.Count
            $total = 0
            [void][int]::TryParse([string](Get-ThreadProp $resp 'totalresults'), [ref]$total)
            if ($total -gt 0 -and $start -ge $total) { break }
            if ($start -ge 1000) { break }
        }
    }
    if ($ids.Count -eq 0) { throw 'No tickets to read: give ticket_ids and/or a client_id that has tickets.' }
    if ($ids.Count -gt $MaxTickets) { throw "That selects $($ids.Count) tickets; at most $MaxTickets per run. Narrow it with ticket_ids." }

    $threads = foreach ($id in ($ids | Sort-Object)) {
        $ticketResp = & $InvokeApi @{ action = 'GetTicket'; ticketid = $id; repliessort = 'ASC' }
        $notesResp = & $InvokeApi @{ action = 'GetTicketNotes'; ticketid = $id }
        ConvertTo-ThreadRecord -Ticket $ticketResp -Notes $notesResp
    }
    return [pscustomobject]@{
        generated_at = $GeneratedAt
        run_url      = $RunUrl
        client_id    = $ClientId
        ticket_count = @($threads).Count
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
    $idList = ConvertTo-TicketIdList -Text $TicketIds -Max $MaxTickets
    if ($idList.Count -eq 0 -and $ClientId -lt 1) { throw 'Give ticket_ids, client_id, or both.' }

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
    $doc = Invoke-TicketThreadRead -TicketIds $idList -ClientId $ClientId -InvokeApi $invoke -MaxTickets $MaxTickets `
        -GeneratedAt ((Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')) -RunUrl $runUrl

    $plain = [System.Text.Encoding]::UTF8.GetBytes(($doc | ConvertTo-Json -Depth 8))
    $envelope = Protect-ThreadPayload -Plain $plain -Rsa $rsa
    [Array]::Clear($plain, 0, $plain.Length)

    $dir = Split-Path -Parent $OutputFile
    if (-not [string]::IsNullOrWhiteSpace($dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $envelope | ConvertTo-Json -Depth 3 | Out-File -FilePath $OutputFile -Encoding utf8

    $replyCount = (@($doc.tickets) | ForEach-Object { @($_.replies).Count } | Measure-Object -Sum).Sum
    $summary = "Encrypted $($doc.ticket_count) ticket thread(s), $replyCount message(s), to recipient key $($envelope.key_fingerprint)."
    Write-Host $summary
    if (-not [string]::IsNullOrWhiteSpace($env:GITHUB_STEP_SUMMARY)) {
        @('## WHMCS ticket threads (encrypted)', '', $summary, '',
            'Decrypt: `python3 scripts/whmcs-ticket-thread-decrypt.py decrypt --key <private.pem> <envelope.json>`') |
            Out-File -FilePath $env:GITHUB_STEP_SUMMARY -Append -Encoding utf8
    }
    exit 0
}
catch {
    Write-Output "::error::Ticket thread read failed: $(Get-ThreadSafeError -Message $_.Exception.Message)"
    exit 1
}
