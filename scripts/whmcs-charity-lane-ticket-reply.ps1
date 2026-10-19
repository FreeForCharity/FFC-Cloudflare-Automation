<#
.SYNOPSIS
    Charity-lane ticket reply (READ-ONLY): did the charity APPROVE its website
    preview or ask for CHANGES? Classifies the reply without ever printing it.

.DESCRIPTION
    Step G3.6 of the charity-lane pipeline (docs/charity-lane-pipeline.md, epic
    #1618). The Onboarding Conductor sends a charity a `preview_ready` ticket
    (workflow 205, subject "[Website Preview] Your new website is ready to
    review") and then has to learn from the reply whether to complete Gate 3 or
    send the site back to the worker. This script answers that one question:

      1. GetTickets filtered by clientid; the most recent ticket (highest id)
         whose subject starts with -SubjectPrefix.
      2. GetTicket for that ticket's thread. FFC's opening message is set aside
         (replyid 0, or the first entry dated at the ticket's own date): 205
         opens tickets AS the client, so its admin field is empty and it would
         otherwise read as the charity's reply.
      3. The client's latest turn: the latest reply whose `admin` field is
         empty, plus any client replies immediately before it with no admin
         reply in between ("change X" ... "otherwise looks great" is read as
         one turn, so the earlier change request is not lost).
      4. A small deterministic classifier over that text, after stripping HTML,
         quoted earlier messages and signatures:

           APPROVED          a clear approval signal AND no change signal AND
                             no hold signal (negation / hesitation) AND a short
                             reply (<= 60 words).
           CHANGES           change signals and no approval signal.
           UNCLEAR           both, or neither, or hold signals only.
           NO_REPLY          no client reply after FFC's opening message.
           TICKET_NOT_FOUND  no ticket for the client with that subject prefix.

         It fails closed: anything it is unsure of is UNCLEAR, never APPROVED.

    PUBLIC REPO -- the reply text is read in memory and NEVER written: not to
    the log, the job summary, the JSON file, an artifact or an issue comment.
    The only values emitted are client id, subject prefix (a fixed choice),
    ticket id, ticket status, reply date (UTC), classification, and the signal
    LABELS that matched, which come from the fixed lists below and never from
    the reply itself. No subject text beyond the prefix, no names, emails or
    phones. Error messages from the WHMCS layer are cut before any response
    excerpt (Get-TicketReplySafeError).

    Pure functions live above the dot-source guard and are unit-tested with
    fixture API payloads (tests/whmcs-charity-lane-ticket-reply.Tests.ps1); the
    live runner below the guard performs only the GetTickets / GetTicket reads.
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

    [Parameter()]
    [int]$ClientId,

    [Parameter()]
    [string]$SubjectPrefix = '[Website Preview]',

    [Parameter()]
    [string]$OutputFile = 'charity-lane-ticket-reply.json',

    # Markdown copy of the result (the workflow posts it as an issue comment).
    [Parameter()]
    [string]$MarkdownFile = 'charity-lane-ticket-reply.md',

    # WHMCS returns ticket dates in the WHMCS system time zone with no offset.
    # The conversion to UTC assumes this zone, and the result names it.
    [Parameter()]
    [string]$WhmcsTimeZoneId = 'UTC',

    [Parameter()]
    [ValidateRange(1, 250)]
    [int]$PageSize = 100
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'whmcs-api-common.ps1')

# --------------------------------------------------------------------------
# Fixed vocabularies. The KEYS are the only signal text that is ever emitted.
# --------------------------------------------------------------------------

$script:TicketReplySubjectPrefixes = @('[Website Preview]', '[Application]')
$script:TicketReplyClassifications = @('APPROVED', 'CHANGES', 'UNCLEAR', 'NO_REPLY', 'TICKET_NOT_FOUND')

# A longer reply is more likely to carry nuance an approval phrase hides.
$script:TicketReplyMaxApprovedWords = 60

# Approval phrases. Matched FIRST and removed from the text before the change
# and hold lists are scanned, so "no changes" or "don't change anything" does
# not also count as the change word it contains. Order matters only where
# phrases overlap: the negated forms come before the plain ones.
$script:TicketReplyApproveSignals = [ordered]@{
    'no changes'            = '\bno\s+(?:further\s+|more\s+|other\s+|additional\s+)?(?:changes?|edits?|corrections?|revisions?|updates?)\b(?:\s+(?:are\s+|is\s+)?(?:needed|required|necessary))?'
    'nothing to change'     = '\bnothing\s+(?:else\s+)?to\s+(?:change|fix|add|edit|update|correct)\b'
    "don't change anything" = "\b(?:don't|dont|do\s+not)\s+change\s+(?:anything|a\s+thing)\b"
    'no issues'             = '\bno\s+(?:issues?|problems?|concerns?|notes?|complaints?)\b'
    'approved'              = '\b(?:approved?|approves|approving)\b'
    'looks good'            = '\b(?:looks?|looking|looked)\s+(?:really\s+|very\s+|so\s+|absolutely\s+|just\s+)?(?:good|great|perfect|fine|wonderful|amazing|fantastic|beautiful|excellent|awesome|right|correct|terrific|lovely|nice)\b'
    'everything is correct' = '\b(?:everything|all)\s+(?:is\s+|looks\s+|seems\s+)?(?:correct|right|good|great|perfect|fine)\b'
    'go ahead'              = '\bgo\s+ahead\b'
    'good to go'            = '\b(?:good|ready|ok|okay)\s+to\s+go\b'
    'proceed'               = '\bproceed\b'
    'publish it'            = '\b(?:publish|launch)\s+(?:it|the\s+(?:site|website))\b'
    'perfect'               = '\bperfect(?:ly)?\b'
    'love it'               = '\b(?:love|loving)\s+(?:it|the\s+(?:site|website|design)|this)\b'
    'lgtm'                  = '\blgtm\b'
}

# Change-request signals. Any one of them rules out APPROVED.
$script:TicketReplyChangeSignals = [ordered]@{
    'change'        = '\bchang(?:e|es|ed|ing)\b'
    'update'        = '\bupdat(?:e|es|ed|ing)\b'
    'fix'           = '\bfix(?:es|ed|ing)?\b'
    'wrong'         = '\bwrong\b'
    'replace'       = '\breplac(?:e|es|ed|ing|ement)\b'
    'add'           = '\badd(?:s|ed|ing|ition|itional)?\b'
    'remove'        = '\bremov(?:e|es|ed|ing|al)\b'
    'missing'       = '\bmissing\b'
    'instead'       = '\binstead\b'
    'but'           = '\bbut\b'
    'typo'          = '\btypos?\b'
    'question mark' = '\?'
    'edit'          = '\bedit(?:s|ed|ing)?\b'
    'revise'        = '\brevis(?:e|es|ed|ion|ions)\b'
    'incorrect'     = '\bincorrect(?:ly)?\b'
    'mistake'       = '\bmistakes?\b'
    'error'         = '\berrors?\b'
    'delete'        = '\bdelet(?:e|es|ed|ing)\b'
    'misspelled'    = '\bmisspel\w*'
    'adjust'        = '\badjust(?:s|ed|ing|ment|ments)?\b'
    'modify'        = '\bmodif(?:y|ies|ied|ying|ication|ications)\b'
    'tweak'         = '\btweak(?:s|ed|ing)?\b'
    'swap'          = '\bswap(?:s|ped|ping)?\b'
    'move'          = '\bmov(?:e|es|ed|ing)\b'
    'except'        = '\bexcept\b'
    'however'       = '\bhowever\b'
    'although'      = '\b(?:although|though)\b'
    'issue'         = '\b(?:issues?|problems?)\b'
    'broken'        = '\bbroken\b'
    'should be'     = '\bshould\s+(?:be|say|read|show)\b'
    'one thing'     = '\b(?:one|two|a\s+few|a\s+couple(?:\s+of)?|small|minor)\s+(?:more\s+)?(?:things?|items?|requests?)\b'
}

# Hold signals: negation or hesitation. They never make a reply CHANGES on
# their own, but they rule out APPROVED ("not approved", "doesn't look good",
# "looks good if ...", "we have not reviewed it yet").
$script:TicketReplyHoldSignals = [ordered]@{
    'not'        = '\bnot\b'
    'no'         = '\bno\b'
    "n't"        = "\b\w+n't\b|\b(?:dont|doesnt|didnt|isnt|arent|wasnt|cant|wont|wouldnt|shouldnt|havent|hasnt)\b"
    'cannot'     = '\bcannot\b'
    'never'      = '\bnever\b'
    'wait'       = '\bwait(?:ing)?\b'
    'hold'       = '\bhold\b'
    'yet'        = '\byet\b'
    'unsure'     = '\bunsure\b'
    'if'         = '\bif\b'
    'need'       = '\bneed(?:s|ed)?\b'
    'would like' = '\bwould\s+like\b|\bwant(?:s|ed)?\b'
    'make'       = '\bmake\b'
    'only'       = '\bonly\b'
    'question'   = '\bquestions?\b'
    'concern'    = '\bconcern(?:s|ed)?\b'
}

# Courtesy phrases removed before any list is scanned, so a sign-off such as
# "let us know if you need anything else" does not read as a hold ("if",
# "need"). Never emitted, so they carry no label.
$script:TicketReplyNeutralPhrases = @(
    '\blet\s+(?:me|us)\s+know\s+if\s+you\s+need\s+(?:anything|something)(?:\s+else)?(?:\s+from\s+(?:me|us))?\b'
    '\bif\s+you\s+(?:have|need)\s+(?:any\s+)?(?:questions?|anything(?:\s+else)?)\b'
)

# Fixed label used when the reply is too long to call an approval.
$script:TicketReplyLongReplyLabel = 'long reply'

# --------------------------------------------------------------------------
# Text cleaning
# --------------------------------------------------------------------------

function ConvertTo-ReplyPlainText {
    <#
    .SYNOPSIS
        Reduce one WHMCS reply message to the charity's own words, normalized
        for matching: HTML removed, quoted earlier messages and signatures cut,
        lower-cased, whitespace collapsed. Returns '' for an empty message.
    #>
    [OutputType([string])]
    param([Parameter(Position = 0)][AllowNull()][AllowEmptyString()][string]$Message)

    if ([string]::IsNullOrWhiteSpace($Message)) { return '' }
    $t = $Message
    if ($t.Length -gt 20000) { $t = $t.Substring(0, 20000) }

    # Entity-encoded markup (&lt;p&gt;) becomes real markup first, so it is stripped.
    $t = [System.Net.WebUtility]::HtmlDecode($t)

    # Mail clients' quote containers: everything from the first one on is an
    # earlier message (Gmail, Yahoo, Outlook web, Thunderbird).
    $quote = [regex]::Match($t, '(?is)<(?:div|span|p)\b[^>]*(?:gmail_quote|yahoo_quoted|divRplyFwdMsg|appendonsend|moz-cite-prefix|OutlookMessageHeader)[^>]*>')
    if ($quote.Success) { $t = $t.Substring(0, $quote.Index) }

    # <blockquote> is quoted text wherever it sits; remove innermost first.
    do {
        $before = $t
        $t = [regex]::Replace($t, '(?is)<blockquote\b[^>]*>(?:(?!<blockquote\b).)*?</blockquote>', ' ')
    } while ($t -ne $before)
    $open = [regex]::Match($t, '(?i)<blockquote\b')
    if ($open.Success) { $t = $t.Substring(0, $open.Index) }

    $t = [regex]::Replace($t, '(?is)<(script|style|head)\b.*?</\1\s*>', ' ')
    $t = [regex]::Replace($t, '(?i)<br\s*/?>|</(?:p|div|li|tr|h[1-6])\s*>', "`n")
    $t = [regex]::Replace($t, '<[^>]*>', ' ')
    $t = [System.Net.WebUtility]::HtmlDecode($t)
    $t = $t -replace "`r`n?", "`n"

    $cutLine = '(?i)^\s*(?:-{2,}\s*(?:original message|forwarded message)\s*-{2,}|[-_=]{8,}\s*$|--\s*$|sent from my\b|sent from (?:outlook|mail|yahoo|gmail)\b|get outlook for\b|begin forwarded message|.*reply above this line)'
    $lines = $t -split "`n"
    $kept = [System.Collections.Generic.List[string]]::new()
    for ($i = 0; $i -lt $lines.Count; $i++) {
        $line = $lines[$i]
        if ($line -match $cutLine) { break }

        # "On <date>, <name> <address> wrote:" -- often wrapped over 2-4 lines.
        $wrote = $false
        if ($line -match '^\s*On\s') {
            for ($k = 0; $k -le 3 -and ($i + $k) -lt $lines.Count; $k++) {
                if ($lines[$i + $k] -match '\bwrote:\s*$') { $wrote = $true; break }
            }
        }
        if ($wrote -or $line -match '^.{0,200}\bwrote:\s*$') { break }

        # Outlook's plain-text header block: "From: ..." followed by Sent/To/Subject.
        if ($line -match '^\s*\*?From:\*?\s') {
            $header = $false
            for ($k = 1; $k -le 5 -and ($i + $k) -lt $lines.Count; $k++) {
                if ($lines[$i + $k] -match '^\s*\*?(?:Sent|Date|To|Subject|Cc):') { $header = $true; break }
            }
            if ($header) { break }
        }

        if ($line -match '^\s*>') { continue }
        $kept.Add($line)
    }

    $text = ($kept -join ' ')
    # Curly / prime apostrophes and quotes, the full-width question mark and the
    # no-break space, built from code points so this file stays ASCII.
    $apostrophes = '[{0}{1}{2}{3}{4}`]' -f [char]0x2018, [char]0x2019, [char]0x201B, [char]0x2032, [char]0x00B4
    $text = $text -replace $apostrophes, "'"
    $text = $text -replace ('[{0}{1}]' -f [char]0x201C, [char]0x201D), '"'
    $text = $text.Replace([string][char]0xFF1F, '?')
    $text = $text.Replace([string][char]0x00A0, ' ')
    $text = $text -replace '\s+', ' '
    return $text.Trim().ToLowerInvariant()
}

# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------

function Get-ReplySignal {
    <#
    .SYNOPSIS
        Signal LABELS (fixed-list keys, never reply text) found in normalized
        reply text: approve / change / hold.
    #>
    [OutputType([pscustomobject])]
    param([Parameter(Position = 0)][AllowEmptyString()][AllowNull()][string]$Text)

    $approve = [System.Collections.Generic.List[string]]::new()
    $change = [System.Collections.Generic.List[string]]::new()
    $hold = [System.Collections.Generic.List[string]]::new()
    $work = if ($null -eq $Text) { '' } else { [string]$Text }
    foreach ($neutral in $script:TicketReplyNeutralPhrases) {
        $work = [regex]::Replace($work, $neutral, ' ', 'IgnoreCase')
    }

    foreach ($label in $script:TicketReplyApproveSignals.Keys) {
        $re = [regex]::new($script:TicketReplyApproveSignals[$label], 'IgnoreCase')
        if ($re.IsMatch($work)) {
            $approve.Add($label)
            $work = $re.Replace($work, ' ')
        }
    }
    foreach ($label in $script:TicketReplyChangeSignals.Keys) {
        if ([regex]::IsMatch($work, $script:TicketReplyChangeSignals[$label], 'IgnoreCase')) { $change.Add($label) }
    }
    foreach ($label in $script:TicketReplyHoldSignals.Keys) {
        if ([regex]::IsMatch($work, $script:TicketReplyHoldSignals[$label], 'IgnoreCase')) { $hold.Add($label) }
    }
    $words = @(([string]$Text) -split '\s+' | Where-Object { $_ }).Count
    if ($words -gt $script:TicketReplyMaxApprovedWords) { $hold.Add($script:TicketReplyLongReplyLabel) }

    return [pscustomobject]@{
        Approve = [string[]]$approve.ToArray()
        Change  = [string[]]$change.ToArray()
        Hold    = [string[]]$hold.ToArray()
    }
}

function Get-ReplyClassification {
    <#
    .SYNOPSIS
        Classify the client's latest turn (one or more raw WHMCS messages).
        Returns Classification + the three signal-label arrays. Fails closed:
        APPROVED needs an approval signal and nothing else.
    #>
    [OutputType([pscustomobject])]
    param([Parameter(Position = 0)][AllowNull()][AllowEmptyCollection()][string[]]$Messages)

    if ($null -eq $Messages -or @($Messages).Count -eq 0) {
        return [pscustomobject]@{ Classification = 'NO_REPLY'; Approve = [string[]]@(); Change = [string[]]@(); Hold = [string[]]@() }
    }
    $text = (@($Messages | ForEach-Object { ConvertTo-ReplyPlainText -Message $_ }) -join ' ').Trim()
    $sig = Get-ReplySignal -Text $text

    $class = 'UNCLEAR'
    if ($sig.Approve.Count -gt 0 -and $sig.Change.Count -eq 0 -and $sig.Hold.Count -eq 0) { $class = 'APPROVED' }
    elseif ($sig.Change.Count -gt 0 -and $sig.Approve.Count -eq 0) { $class = 'CHANGES' }

    return [pscustomobject]@{ Classification = $class; Approve = $sig.Approve; Change = $sig.Change; Hold = $sig.Hold }
}

# --------------------------------------------------------------------------
# Ticket + reply selection (fixture-testable: plain API payload objects in)
# --------------------------------------------------------------------------

function Get-TicketReplyProp {
    param($Object, [Parameter(Mandatory = $true)][string]$Name)
    if ($null -eq $Object -or $Object -is [string]) { return $null }
    if ($Object -is [System.Collections.IDictionary]) { return $Object[$Name] }
    $p = $Object.PSObject.Properties[$Name]
    if ($p) { return $p.Value }
    return $null
}

function Select-LatestTicket {
    <#
    .SYNOPSIS
        The client's most recent ticket (highest numeric id) whose subject
        starts with the prefix. Tickets carrying a different userid are ignored
        (GetTickets is already filtered by clientid; this is belt and braces).
    #>
    param(
        [Parameter()][AllowNull()][AllowEmptyCollection()][object[]]$Tickets,
        [Parameter(Mandatory = $true)][string]$SubjectPrefix,
        [Parameter(Mandatory = $true)][int]$ClientId
    )
    $best = $null
    $bestId = 0
    foreach ($t in @($Tickets)) {
        if ($null -eq $t -or $t -is [string]) { continue }
        $id = 0
        if (-not [int]::TryParse([string](Get-TicketReplyProp $t 'id'), [ref]$id) -or $id -lt 1) { continue }
        $owner = [string](Get-TicketReplyProp $t 'userid')
        if (-not [string]::IsNullOrWhiteSpace($owner) -and $owner.Trim() -ne [string]$ClientId) { continue }
        $subject = [System.Net.WebUtility]::HtmlDecode([string](Get-TicketReplyProp $t 'subject')).Trim()
        if (-not $subject.StartsWith($SubjectPrefix, [System.StringComparison]::OrdinalIgnoreCase)) { continue }
        if ($id -gt $bestId) { $best = $t; $bestId = $id }
    }
    return $best
}

function Select-ClientReplyTurn {
    <#
    .SYNOPSIS
        From a GetTicket payload, the client's latest turn after FFC's opening
        message. Returns $null when there is no client reply; otherwise
        Messages (raw text, kept in memory only), LatestDate and Count.

    .DESCRIPTION
        Client vs admin: WHMCS puts the admin's name in a reply's `admin` field
        and leaves it empty for a client reply. The opening message is NOT
        judged by that field -- 205 opens tickets as the client, so it carries
        an empty `admin` -- but by position: the entry with replyid 0, else the
        first entry when it is dated at the ticket's own date (or the ticket
        has no date, failing closed toward "no reply").
    #>
    param([Parameter(Mandatory = $true)][AllowNull()]$Ticket)

    if ($null -eq $Ticket) { return $null }
    $raw = @(Get-WhmcsNodeList -Node (Get-TicketReplyProp $Ticket 'replies') -ChildName 'reply')
    $entries = for ($i = 0; $i -lt $raw.Count; $i++) {
        $r = $raw[$i]
        if ($null -eq $r -or $r -is [string]) { continue }
        $rid = 0
        $hasRid = [int]::TryParse([string](Get-TicketReplyProp $r 'replyid'), [ref]$rid)
        [pscustomobject]@{
            Index   = $i
            HasRid  = $hasRid
            ReplyId = $(if ($hasRid) { $rid } else { [int]::MaxValue })
            Date    = ([string](Get-TicketReplyProp $r 'date')).Trim()
            IsAdmin = -not [string]::IsNullOrWhiteSpace([string](Get-TicketReplyProp $r 'admin'))
            Message = [string](Get-TicketReplyProp $r 'message')
        }
    }
    $entries = @($entries | Sort-Object -Property Date, ReplyId, Index)
    if ($entries.Count -eq 0) { return $null }

    $opening = $entries | Where-Object { $_.HasRid -and $_.ReplyId -eq 0 } | Select-Object -First 1
    if (-not $opening) {
        $ticketDate = ([string](Get-TicketReplyProp $Ticket 'date')).Trim()
        if ([string]::IsNullOrWhiteSpace($ticketDate) -or $entries[0].Date -eq $ticketDate) { $opening = $entries[0] }
    }

    $after = @($entries | Where-Object {
            $null -eq $opening -or ($_.Index -ne $opening.Index -and [string]::CompareOrdinal($_.Date, $opening.Date) -ge 0)
        })

    $last = -1
    for ($i = $after.Count - 1; $i -ge 0; $i--) {
        if (-not $after[$i].IsAdmin) { $last = $i; break }
    }
    if ($last -lt 0) { return $null }

    $turn = [System.Collections.Generic.List[string]]::new()
    $first = $last
    while ($first -ge 0 -and -not $after[$first].IsAdmin) { $first-- }
    for ($i = $first + 1; $i -le $last; $i++) { $turn.Add($after[$i].Message) }

    return [pscustomobject]@{
        Messages   = [string[]]$turn.ToArray()
        LatestDate = $after[$last].Date
        Count      = $turn.Count
    }
}

# --------------------------------------------------------------------------
# Output (the ONLY shape that leaves this script)
# --------------------------------------------------------------------------

function ConvertTo-TicketReplyDateUtc {
    # WHMCS "yyyy-MM-dd HH:mm:ss" in the WHMCS time zone -> ISO-8601 UTC, or ''.
    [OutputType([string])]
    param([AllowNull()][AllowEmptyString()][string]$WhmcsDate, [string]$TimeZoneId = 'UTC')
    if ([string]::IsNullOrWhiteSpace($WhmcsDate)) { return '' }
    $dt = [datetime]::MinValue
    $ok = [datetime]::TryParseExact($WhmcsDate.Trim(), 'yyyy-MM-dd HH:mm:ss', [System.Globalization.CultureInfo]::InvariantCulture, [System.Globalization.DateTimeStyles]::None, [ref]$dt)
    if (-not $ok) { return '' }
    $tz = [System.TimeZoneInfo]::FindSystemTimeZoneById($TimeZoneId)
    $utc = [System.TimeZoneInfo]::ConvertTimeToUtc([datetime]::SpecifyKind($dt, [System.DateTimeKind]::Unspecified), $tz)
    return $utc.ToString('yyyy-MM-ddTHH:mm:ssZ', [System.Globalization.CultureInfo]::InvariantCulture)
}

function Format-TicketReplyStatus {
    # A WHMCS status name ("Open", "Customer-Reply", "On Hold"); anything else is OTHER.
    [OutputType([string])]
    param([AllowNull()][AllowEmptyString()][string]$Status)
    if ([string]::IsNullOrWhiteSpace($Status)) { return $null }
    $s = $Status.Trim()
    if ($s -cmatch '^[A-Za-z][A-Za-z0-9 _-]{0,39}$') { return $s }
    return 'OTHER'
}

function ConvertTo-TicketReplyResult {
    <#
    .SYNOPSIS
        Assemble the result object. Every field is either a validated id, a
        fixed choice, or a label from the fixed lists; signal labels not in
        those lists are dropped rather than emitted.
    #>
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)][int]$ClientId,
        [Parameter(Mandatory = $true)][string]$SubjectPrefix,
        [Parameter(Mandatory = $true)][string]$Classification,
        [AllowNull()][AllowEmptyString()][string]$TicketId,
        [AllowNull()][AllowEmptyString()][string]$TicketStatus,
        [AllowNull()][AllowEmptyString()][string]$ReplyDateUtc,
        [AllowNull()][AllowEmptyCollection()][string[]]$ApproveSignals = @(),
        [AllowNull()][AllowEmptyCollection()][string[]]$ChangeSignals = @(),
        [AllowNull()][AllowEmptyCollection()][string[]]$HoldSignals = @(),
        [string]$TimeZoneId = 'UTC',
        [string]$RunUrl = '',
        [string]$GeneratedAt = ''
    )
    if ($script:TicketReplyClassifications -notcontains $Classification) { throw "Unknown classification '$Classification'." }
    if ($script:TicketReplySubjectPrefixes -cnotcontains $SubjectPrefix) { throw 'Subject prefix is not one of the fixed choices.' }
    $tid = $null
    if ($TicketId -match '^\d{1,10}$') { $tid = $TicketId }
    $date = $null
    if ($ReplyDateUtc -match '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$') { $date = $ReplyDateUtc }
    $holdKeys = @($script:TicketReplyHoldSignals.Keys) + @($script:TicketReplyLongReplyLabel)

    return [pscustomobject][ordered]@{
        schema                 = 'charity-lane-ticket-reply/v1'
        client_id              = $ClientId
        subject_prefix         = $SubjectPrefix
        ticket_id              = $tid
        ticket_status          = (Format-TicketReplyStatus -Status $TicketStatus)
        reply_date_utc         = $date
        classification         = $Classification
        approve_signals        = [string[]]@(@($ApproveSignals) | Where-Object { $_ -and $script:TicketReplyApproveSignals.Contains($_) })
        change_signals         = [string[]]@(@($ChangeSignals) | Where-Object { $_ -and $script:TicketReplyChangeSignals.Contains($_) })
        hold_signals           = [string[]]@(@($HoldSignals) | Where-Object { $_ -and $holdKeys -contains $_ })
        whmcs_timezone_assumed = $TimeZoneId
        read_only              = $true
        run_url                = $RunUrl
        generated_at           = $GeneratedAt
    }
}

function Format-TicketReplyMarkdown {
    # Job-summary / issue-comment body. Only result fields appear.
    [OutputType([string])]
    param([Parameter(Mandatory = $true)]$Result)

    $fmt = {
        param([string]$Kind, $Labels)
        $l = @($Labels | Where-Object { $_ })
        if ($l.Count -eq 0) { return $null }
        return "${Kind}: " + (($l | ForEach-Object { '`' + $_ + '`' }) -join ', ')
    }
    $sig = @(
        (& $fmt 'approve' $Result.approve_signals),
        (& $fmt 'change' $Result.change_signals),
        (& $fmt 'hold' $Result.hold_signals)
    ) | Where-Object { $_ }
    $sigCell = if (@($sig).Count -gt 0) { $sig -join '; ' } else { '-' }
    $ticketCell = if ($Result.ticket_id) { [string]$Result.ticket_id } else { '-' }
    $statusCell = if ($Result.ticket_status) { [string]$Result.ticket_status } else { '-' }
    $dateCell = if ($Result.reply_date_utc) { [string]$Result.reply_date_utc } else { '-' }
    $fence = [string][char]0x60 * 3

    $lines = [System.Collections.Generic.List[string]]::new()
    $lines.Add('<!-- charity-lane:ticket-reply -->')
    $lines.Add('## Charity-lane ticket reply (read-only)')
    $lines.Add('')
    if ($Result.run_url) { $lines.Add("Run: $($Result.run_url)"); $lines.Add('') }
    $lines.Add('| Client | Ticket | Status | Reply date (UTC) | Classification | Signals |')
    $lines.Add('| ------ | ------ | ------ | ---------------- | -------------- | ------- |')
    $lines.Add("| $($Result.client_id) | $ticketCell | $statusCell | $dateCell | **$($Result.classification)** | $sigCell |")
    $lines.Add('')
    $lines.Add("Subject prefix: ``$($Result.subject_prefix)``. The reply text is never printed: signals are labels from fixed word lists in ``scripts/whmcs-charity-lane-ticket-reply.ps1``. Dates assume the WHMCS time zone is ``$($Result.whmcs_timezone_assumed)``.")
    $lines.Add('')
    $lines.Add($fence + 'json')
    $lines.Add(($Result | ConvertTo-Json -Depth 4))
    $lines.Add($fence)
    return ($lines -join "`n")
}

function Get-TicketReplySafeError {
    # A WHMCS-layer error message with any response excerpt cut off: the
    # non-JSON and "Response:" diagnostics in Invoke-WhmcsApi can quote the
    # API body, which for GetTicket would be the thread.
    [OutputType([string])]
    param([AllowNull()][AllowEmptyString()][string]$Message)
    if ([string]::IsNullOrWhiteSpace($Message)) { return 'unknown error' }
    $m = ($Message -split "`r?`n")[0]
    $i = $m.IndexOf('non-JSON response', [System.StringComparison]::OrdinalIgnoreCase)
    if ($i -ge 0) { $m = $m.Substring(0, $i + 'non-JSON response'.Length) + ' (body withheld)' }
    $j = $m.IndexOf('Response:', [System.StringComparison]::OrdinalIgnoreCase)
    if ($j -ge 0) { $m = $m.Substring(0, $j) + '(body withheld)' }
    if ($m.Length -gt 300) { $m = $m.Substring(0, 300) + '...' }
    return $m
}

function Invoke-TicketReplyLookup {
    <#
    .SYNOPSIS
        The whole read: GetTickets (clientid) -> latest prefixed ticket ->
        GetTicket -> client turn -> classification -> result object.
        -InvokeApi is a scriptblock taking one hashtable (the action fields;
        the caller adds auth), so tests run it on fixture payloads.
    #>
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)][int]$ClientId,
        [Parameter(Mandatory = $true)][string]$SubjectPrefix,
        [Parameter(Mandatory = $true)][scriptblock]$InvokeApi,
        [int]$Size = 100,
        [string]$TimeZoneId = 'UTC',
        [string]$RunUrl = '',
        [string]$GeneratedAt = ''
    )
    if ($ClientId -lt 1) { throw 'ClientId must be a positive integer.' }
    if ($script:TicketReplySubjectPrefixes -cnotcontains $SubjectPrefix) {
        throw "SubjectPrefix must be one of: $($script:TicketReplySubjectPrefixes -join ', ')."
    }

    # 1) The client's tickets.
    $tickets = [System.Collections.Generic.List[object]]::new()
    $start = 0
    while ($true) {
        $body = @{}
        $body.action = 'GetTickets'
        $body.clientid = $ClientId
        $body.limitstart = $start
        $body.limitnum = $Size
        $resp = & $InvokeApi $body
        $page = @(Get-WhmcsNodeList -Node (Get-TicketReplyProp $resp 'tickets') -ChildName 'ticket')
        foreach ($p in $page) { $tickets.Add($p) }
        if ($page.Count -le 0) { break }
        $start += $page.Count
        $total = 0
        [void][int]::TryParse([string](Get-TicketReplyProp $resp 'totalresults'), [ref]$total)
        if ($total -gt 0 -and $start -ge $total) { break }
        if ($start -ge 5000) { break }
    }

    $common = @{ ClientId = $ClientId; SubjectPrefix = $SubjectPrefix; TimeZoneId = $TimeZoneId; RunUrl = $RunUrl; GeneratedAt = $GeneratedAt }
    $ticket = Select-LatestTicket -Tickets $tickets.ToArray() -SubjectPrefix $SubjectPrefix -ClientId $ClientId
    if ($null -eq $ticket) {
        return ConvertTo-TicketReplyResult @common -Classification 'TICKET_NOT_FOUND'
    }
    $ticketId = [string](Get-TicketReplyProp $ticket 'id')

    # 2) Its thread.
    $body = @{}
    $body.action = 'GetTicket'
    $body.ticketid = [int]$ticketId
    $body.repliessort = 'ASC'
    $detail = & $InvokeApi $body
    $status = [string](Get-TicketReplyProp $detail 'status')
    if ([string]::IsNullOrWhiteSpace($status)) { $status = [string](Get-TicketReplyProp $ticket 'status') }

    $turn = Select-ClientReplyTurn -Ticket $detail
    if ($null -eq $turn) {
        return ConvertTo-TicketReplyResult @common -Classification 'NO_REPLY' -TicketId $ticketId -TicketStatus $status
    }
    $c = Get-ReplyClassification -Messages $turn.Messages
    $date = ConvertTo-TicketReplyDateUtc -WhmcsDate $turn.LatestDate -TimeZoneId $TimeZoneId
    return ConvertTo-TicketReplyResult @common -Classification $c.Classification -TicketId $ticketId -TicketStatus $status `
        -ReplyDateUtc $date -ApproveSignals $c.Approve -ChangeSignals $c.Change -HoldSignals $c.Hold
}

# When dot-sourced (unit tests), stop here: only the functions are needed.
if ($MyInvocation.InvocationName -eq '.') { return }

# ==========================================================================
# Runner (live, READ-ONLY): GetTickets + GetTicket. No WHMCS write action.
# ==========================================================================

try {
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
    $result = Invoke-TicketReplyLookup -ClientId $ClientId -SubjectPrefix $SubjectPrefix -InvokeApi $invoke -Size $PageSize `
        -TimeZoneId $WhmcsTimeZoneId -RunUrl $runUrl -GeneratedAt ((Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ'))
    $markdown = Format-TicketReplyMarkdown -Result $result

    foreach ($path in @($OutputFile, $MarkdownFile)) {
        $dir = Split-Path -Parent $path
        if (-not [string]::IsNullOrWhiteSpace($dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    }
    $result | ConvertTo-Json -Depth 4 | Out-File -FilePath $OutputFile -Encoding utf8
    $markdown | Out-File -FilePath $MarkdownFile -Encoding utf8
    if (-not [string]::IsNullOrWhiteSpace($env:GITHUB_STEP_SUMMARY)) {
        $markdown | Out-File -FilePath $env:GITHUB_STEP_SUMMARY -Append -Encoding utf8
    }
    Write-Host $markdown
    Write-Host "CLASSIFICATION client=$($result.client_id) ticket=$($result.ticket_id) $($result.classification)"
    exit 0
}
catch {
    # ::error:: annotation + explicit exit (not Write-Error, which under
    # $ErrorActionPreference = 'Stop' would throw past the exit code).
    Write-Output "::error::Charity-lane ticket reply lookup failed: $(Get-TicketReplySafeError -Message $_.Exception.Message)"
    exit 1
}
