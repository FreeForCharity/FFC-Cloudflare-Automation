#requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0.0' }
<#
    Unit tests for scripts/whmcs-charity-lane-ticket-reply.ps1 (workflow 233).
    The script is dot-sourced (its runner is guarded by
    `if ($MyInvocation.InvocationName -eq '.') { return }`), so NO WHMCS call is
    made: GetTickets / GetTicket payloads are fixtures, fed through the
    injectable -InvokeApi scriptblock.

    Covers: the classifier (phrasings, mixed signals, negation, empty input,
    HTML and quoted-reply noise), client-vs-admin reply selection, ticket
    selection by subject prefix, and the output contract (no reply text, no
    names / emails / phones / subject beyond the fixed prefix).

    Run: Invoke-Pester tests/whmcs-charity-lane-ticket-reply.Tests.ps1
#>

BeforeAll {
    . (Join-Path $PSScriptRoot '..' 'scripts' 'whmcs-charity-lane-ticket-reply.ps1')

    function Get-Class {
        param([string[]]$Messages)
        return (Get-ReplyClassification -Messages $Messages).Classification
    }

    # The preview_ready body, as it would appear if a mail client quoted it.
    # It carries "changes" and "instead", so a quote that leaks through the
    # cleaner can never read as APPROVED.
    $script:TemplateBody = (Get-Content -Raw (Join-Path $PSScriptRoot '..' 'config' 'whmcs-ticket-templates.json') | ConvertFrom-Json).templates.preview_ready.body

    function New-Reply {
        param([string]$Id, [string]$Date, [string]$Admin = '', [string]$Message = 'x')
        $r = [ordered]@{ date = $Date; admin = $Admin; message = $Message; name = 'Jane Doe'; email = 'jane.doe@example.org' }
        if ($Id -ne '') { $r = [ordered]@{ replyid = $Id } + $r }
        return [pscustomobject]$r
    }

    function New-TicketDetail {
        param([object[]]$Replies, [string]$Date = '2026-10-01 10:00:00', [string]$Status = 'Customer-Reply')
        return [pscustomobject]@{
            result = 'success'; id = '501'; tid = 'ABC-123456'; userid = '701'; date = $Date; status = $Status
            subject = '[Website Preview] Your new website is ready to review'; name = 'Jane Doe'; email = 'jane.doe@example.org'
            replies = [pscustomobject]@{ reply = @($Replies) }
        }
    }

    $script:Opening = New-Reply -Id '0' -Date '2026-10-01 10:00:00' -Message $script:TemplateBody
}

Describe 'ConvertTo-ReplyPlainText' {
    It 'returns empty for null, empty and whitespace' {
        ConvertTo-ReplyPlainText -Message $null | Should -Be ''
        ConvertTo-ReplyPlainText -Message '' | Should -Be ''
        ConvertTo-ReplyPlainText -Message "  `r`n " | Should -Be ''
    }
    It 'strips tags, decodes entities, lower-cases and collapses whitespace' {
        ConvertTo-ReplyPlainText -Message '<p>Looks&nbsp;<b>GREAT</b></p><br/>Thanks &amp; regards' | Should -Be 'looks great thanks & regards'
    }
    It 'decodes entity-encoded markup before stripping it' {
        ConvertTo-ReplyPlainText -Message '&lt;p&gt;Approved&lt;/p&gt;' | Should -Be 'approved'
    }
    It 'drops <blockquote> content, nested or not' {
        ConvertTo-ReplyPlainText -Message '<div>Approved</div><blockquote>old <blockquote>older change</blockquote> text</blockquote>' | Should -Be 'approved'
    }
    It 'cuts at a Gmail quote container' {
        ConvertTo-ReplyPlainText -Message '<div dir="ltr">Perfect</div><br><div class="gmail_quote"><div>On Mon wrote:</div>please change</div>' | Should -Be 'perfect'
    }
    It 'cuts at a single-line "On ... wrote:" header' {
        ConvertTo-ReplyPlainText -Message "Approved`r`n`r`nOn Mon, Oct 5, 2026 at 3:00 PM Free For Charity <support@freeforcharity.org> wrote:`r`n> please reply with changes" | Should -Be 'approved'
    }
    It 'cuts at a wrapped "On ... wrote:" header' {
        ConvertTo-ReplyPlainText -Message "Go ahead!`nOn Mon, Oct 5, 2026 at 3:00 PM Free For Charity <`nsupport@freeforcharity.org> wrote:`nchanges instead" | Should -Be 'go ahead!'
    }
    It 'cuts at an Outlook header block' {
        ConvertTo-ReplyPlainText -Message "Looks good`r`n`r`nFrom: Free For Charity <support@freeforcharity.org>`r`nSent: Monday, October 5, 2026 3:00 PM`r`nSubject: [Website Preview]" | Should -Be 'looks good'
    }
    It 'keeps a "From:" line that is not a mail header' {
        ConvertTo-ReplyPlainText -Message "From: our board, approved.`nThanks" | Should -Be 'from: our board, approved. thanks'
    }
    It 'cuts at Original Message, underscores, signature delimiter and mobile footers' {
        ConvertTo-ReplyPlainText -Message "Approved`n-----Original Message-----`nchange" | Should -Be 'approved'
        ConvertTo-ReplyPlainText -Message "Approved`n________________________________`nchange" | Should -Be 'approved'
        ConvertTo-ReplyPlainText -Message "Approved`n-- `nJane Doe | Changing lives" | Should -Be 'approved'
        ConvertTo-ReplyPlainText -Message "Perfect`n`nSent from my iPhone" | Should -Be 'perfect'
        ConvertTo-ReplyPlainText -Message "Perfect`nGet Outlook for Android" | Should -Be 'perfect'
        ConvertTo-ReplyPlainText -Message "Approved`n##- Please type your reply above this line -##`nchange" | Should -Be 'approved'
    }
    It 'drops ">" quoted lines' {
        ConvertTo-ReplyPlainText -Message "Approved`n> reply with any changes`n>> instead" | Should -Be 'approved'
    }
    It 'normalizes curly apostrophes so contractions match' {
        ConvertTo-ReplyPlainText -Message "Don$([char]0x2019)t change anything" | Should -Be "don't change anything"
    }
    It 'turns a no-break space into a space and a full-width question mark into "?"' {
        ConvertTo-ReplyPlainText -Message "Looks$([char]0x00A0)good" | Should -Be 'looks good'
        ConvertTo-ReplyPlainText -Message "Approved$([char]0xFF1F)" | Should -Be 'approved?'
    }
}

Describe 'Get-ReplyClassification: APPROVED' {
    It 'approves "<Text>"' -ForEach @(
        @{ Text = 'Approved' }
        @{ Text = 'approved!' }
        @{ Text = 'APPROVED.' }
        @{ Text = 'We approve the website. Thank you so much!' }
        @{ Text = 'I approve' }
        @{ Text = 'Looks good' }
        @{ Text = 'Looks great, thank you!' }
        @{ Text = 'It looks really great.' }
        @{ Text = 'Everything looks perfect.' }
        @{ Text = 'Go ahead' }
        @{ Text = 'Yes, go ahead!' }
        @{ Text = 'Perfect, thank you!' }
        @{ Text = 'No changes' }
        @{ Text = 'No changes needed. Thanks!' }
        @{ Text = 'No further changes are needed.' }
        @{ Text = 'Nothing to change, great work.' }
        @{ Text = "Don't change anything - we love it" }
        @{ Text = 'Good to go!' }
        @{ Text = 'All good, please proceed.' }
        @{ Text = 'LGTM' }
        @{ Text = 'Looks great! Let us know if you need anything else from us.' }
        @{ Text = 'Approved, no issues.' }
    ) {
        Get-Class -Messages @($Text) | Should -Be 'APPROVED'
    }

    It 'reports the approval labels it matched' {
        $c = Get-ReplyClassification -Messages @('No changes, looks great!')
        $c.Approve | Should -Contain 'no changes'
        $c.Approve | Should -Contain 'looks good'
        $c.Change | Should -BeNullOrEmpty
        $c.Hold | Should -BeNullOrEmpty
    }
}

Describe 'Get-ReplyClassification: CHANGES' {
    It 'asks for changes: "<Text>"' -ForEach @(
        @{ Text = 'Please change the phone number to the office line.'; Signal = 'change' }
        @{ Text = 'Can you update our mission statement'; Signal = 'update' }
        @{ Text = 'Please fix the spelling of our name'; Signal = 'fix' }
        @{ Text = 'The address on the contact page is wrong'; Signal = 'wrong' }
        @{ Text = 'Replace the header photo with the attached one'; Signal = 'replace' }
        @{ Text = 'Please add our donate link'; Signal = 'add' }
        @{ Text = 'Remove the events page'; Signal = 'remove' }
        @{ Text = 'Our board list is missing'; Signal = 'missing' }
        @{ Text = 'Use the blue logo instead'; Signal = 'instead' }
        @{ Text = 'There is a typo in the footer'; Signal = 'typo' }
        @{ Text = 'Where is the donate button?'; Signal = 'question mark' }
        @{ Text = 'The hours should be 9 to 5'; Signal = 'should be' }
        @{ Text = 'Just one thing: the email bounces'; Signal = 'one thing' }
    ) {
        $c = Get-ReplyClassification -Messages @($Text)
        $c.Classification | Should -Be 'CHANGES'
        $c.Change | Should -Contain $Signal
    }

    It 'stays CHANGES when a negation sits alongside a change request' {
        Get-Class -Messages @("Not yet - we still need to add our board members") | Should -Be 'CHANGES'
    }
}

Describe 'Get-ReplyClassification: UNCLEAR (fails closed)' {
    It 'is UNCLEAR for mixed or empty signals: "<Text>"' -ForEach @(
        @{ Text = 'Looks good, but can you update the logo?' }
        @{ Text = 'Approved with one small change to the footer' }
        @{ Text = 'Perfect except the phone number' }
        @{ Text = 'No changes needed but the email is wrong' }
        @{ Text = 'Go ahead and change the header photo' }
        @{ Text = 'Approved?' }
        @{ Text = 'Looks good to me, however our board will review it' }
        @{ Text = 'Yes' }
        @{ Text = 'OK' }
        @{ Text = 'Thanks!' }
        @{ Text = 'We have not had a chance to look yet' }
        @{ Text = 'Not approved' }
        @{ Text = 'It does not look good' }
        @{ Text = "It doesn't look good" }
        @{ Text = 'It doesnt look good' }
        @{ Text = 'I cannot approve this' }
        @{ Text = 'Looks good if the photo is swapped' }
        @{ Text = 'Looks great, the logo needs to be bigger' }
        @{ Text = 'Looks great, we would like a bigger logo' }
        @{ Text = 'Please wait, our director wants to look first. Looks good so far' }
        @{ Text = 'Approved, but only after the board meets' }
        @{ Text = 'Perfect. Please hold off until next week.' }
    ) {
        Get-Class -Messages @($Text) | Should -Be 'UNCLEAR'
    }

    It 'is UNCLEAR for an empty or whitespace reply' {
        Get-Class -Messages @('') | Should -Be 'UNCLEAR'
        Get-Class -Messages @("   `r`n  ") | Should -Be 'UNCLEAR'
    }

    It 'is UNCLEAR when the only text was quoted (nothing of the client''s own)' {
        Get-Class -Messages @("On Mon, Oct 5, 2026 Free For Charity <s@f.org> wrote:`n> approved") | Should -Be 'UNCLEAR'
    }

    It 'never approves a long reply even with only approval words' {
        $long = 'Looks great. ' + (('Thank you all so much for the wonderful work on our website ' * 8).Trim())
        $c = Get-ReplyClassification -Messages @($long)
        $c.Classification | Should -Be 'UNCLEAR'
        $c.Hold | Should -Contain 'long reply'
    }

    It 'cannot approve when the quoted template leaks through unstripped' {
        # No quote header at all, so the cleaner keeps the template text: its
        # change words must keep the result away from APPROVED.
        Get-Class -Messages @("Approved`n$($script:TemplateBody)") | Should -Not -Be 'APPROVED'
    }
}

Describe 'Get-ReplyClassification: HTML and quoted-reply noise' {
    It 'approves a Gmail HTML reply whose quote holds change words' {
        $m = '<div dir="ltr">Looks great, thank you!</div><br><div class="gmail_quote"><div dir="ltr" class="gmail_attr">On Mon, Oct 5, 2026 at 3:00 PM Free For Charity &lt;support@freeforcharity.org&gt; wrote:<br></div><blockquote class="gmail_quote">' + $script:TemplateBody + '</blockquote></div>'
        Get-Class -Messages @($m) | Should -Be 'APPROVED'
    }
    It 'approves a plain-text reply with the template quoted below an On/wrote header' {
        $quoted = ($script:TemplateBody -split '(?<=\.) ' | ForEach-Object { "> $_" }) -join "`r`n"
        Get-Class -Messages @("Approved, thank you!`r`n`r`nOn Mon, Oct 5, 2026 at 3:00 PM Free For Charity <support@freeforcharity.org> wrote:`r`n$quoted") | Should -Be 'APPROVED'
    }
    It 'approves an Outlook reply with the header block and quote below' {
        Get-Class -Messages @("Perfect!`r`n`r`n________________________________`r`nFrom: Free For Charity`r`nSent: Monday`r`nTo: Jane`r`n$($script:TemplateBody)") | Should -Be 'APPROVED'
    }
    It 'reads a change request inside HTML markup' {
        Get-Class -Messages @('<p>Please <b>change</b> the logo</p><p>Thanks</p>') | Should -Be 'CHANGES'
    }
    It 'does not let markup attribute text count as a signal' {
        Get-Class -Messages @('<p class="fix-update-add" title="change?">Approved</p>') | Should -Be 'APPROVED'
    }
}

Describe 'Get-ReplyClassification: NO_REPLY and multi-message turns' {
    It 'is NO_REPLY when there are no messages' {
        Get-Class -Messages @() | Should -Be 'NO_REPLY'
        Get-Class -Messages $null | Should -Be 'NO_REPLY'
    }
    It 'keeps an earlier change request in the same turn' {
        Get-Class -Messages @('Please change the phone number', 'Otherwise looks great') | Should -Be 'UNCLEAR'
    }
    It 'approves a turn whose every message approves' {
        Get-Class -Messages @('Approved', 'Thank you!') | Should -Be 'APPROVED'
    }
}

Describe 'Select-ClientReplyTurn (client vs admin)' {
    It 'is $null for a null ticket, no replies, or an empty-string replies node' {
        Select-ClientReplyTurn -Ticket $null | Should -BeNullOrEmpty
        Select-ClientReplyTurn -Ticket ([pscustomobject]@{ date = '2026-10-01 10:00:00'; replies = '' }) | Should -BeNullOrEmpty
        Select-ClientReplyTurn -Ticket ([pscustomobject]@{ date = '2026-10-01 10:00:00'; replies = [pscustomobject]@{ reply = '' } }) | Should -BeNullOrEmpty
    }

    It 'never treats FFC''s opening message as the client reply, though its admin field is empty' {
        # 205 opens the ticket as the client: replyid 0, admin ''.
        $t = New-TicketDetail -Replies @($script:Opening)
        Select-ClientReplyTurn -Ticket $t | Should -BeNullOrEmpty
    }

    It 'recognizes the opening by replyid 0 even when its date differs from the ticket date' {
        $opening = New-Reply -Id '0' -Date '2026-10-01 10:00:02' -Message $script:TemplateBody
        $t = New-TicketDetail -Date '2026-10-01 10:00:00' -Replies @($opening)
        Select-ClientReplyTurn -Ticket $t | Should -BeNullOrEmpty
    }

    It 'is $null when only admins replied after the opening' {
        $t = New-TicketDetail -Replies @($script:Opening, (New-Reply -Id '11' -Date '2026-10-02 09:00:00' -Admin 'Clarke Moyer' -Message 'Following up'))
        Select-ClientReplyTurn -Ticket $t | Should -BeNullOrEmpty
    }

    It 'picks the client reply after the opening' {
        $t = New-TicketDetail -Replies @($script:Opening, (New-Reply -Id '12' -Date '2026-10-02 09:00:00' -Message 'Approved'))
        $turn = Select-ClientReplyTurn -Ticket $t
        $turn.Count | Should -Be 1
        $turn.Messages | Should -Be @('Approved')
        $turn.LatestDate | Should -Be '2026-10-02 09:00:00'
    }

    It 'takes only the client turn after the latest admin reply' {
        $t = New-TicketDetail -Replies @(
            $script:Opening
            (New-Reply -Id '12' -Date '2026-10-02 09:00:00' -Message 'Please change the phone')
            (New-Reply -Id '13' -Date '2026-10-03 09:00:00' -Admin 'Volunteer' -Message 'Done, please look again')
            (New-Reply -Id '14' -Date '2026-10-04 09:00:00' -Message 'Approved')
        )
        $turn = Select-ClientReplyTurn -Ticket $t
        $turn.Messages | Should -Be @('Approved')
        (Get-ReplyClassification -Messages $turn.Messages).Classification | Should -Be 'APPROVED'
    }

    It 'groups consecutive client replies into one turn' {
        $t = New-TicketDetail -Replies @(
            $script:Opening
            (New-Reply -Id '12' -Date '2026-10-02 09:00:00' -Message 'Approved')
            (New-Reply -Id '13' -Date '2026-10-02 09:05:00' -Message 'Oh wait, please fix the logo')
        )
        $turn = Select-ClientReplyTurn -Ticket $t
        $turn.Count | Should -Be 2
        $turn.LatestDate | Should -Be '2026-10-02 09:05:00'
        (Get-ReplyClassification -Messages $turn.Messages).Classification | Should -Be 'UNCLEAR'
    }

    It 'still reports the latest client turn when an admin answered after it' {
        $t = New-TicketDetail -Replies @(
            $script:Opening
            (New-Reply -Id '12' -Date '2026-10-02 09:00:00' -Message 'Please add our logo')
            (New-Reply -Id '13' -Date '2026-10-03 09:00:00' -Admin 'Volunteer' -Message 'On it')
        )
        $turn = Select-ClientReplyTurn -Ticket $t
        $turn.Messages | Should -Be @('Please add our logo')
        $turn.LatestDate | Should -Be '2026-10-02 09:00:00'
    }

    It 'orders replies by date even when the API returns them newest first' {
        $t = New-TicketDetail -Replies @(
            (New-Reply -Id '14' -Date '2026-10-04 09:00:00' -Message 'Looks good')
            (New-Reply -Id '13' -Date '2026-10-03 09:00:00' -Admin 'Volunteer' -Message 'Updated')
            (New-Reply -Id '12' -Date '2026-10-02 09:00:00' -Message 'Change it')
            $script:Opening
        )
        (Select-ClientReplyTurn -Ticket $t).Messages | Should -Be @('Looks good')
    }

    It 'handles a single reply object that is not wrapped in an array' {
        $t = [pscustomobject]@{ date = '2026-10-01 10:00:00'; replies = [pscustomobject]@{ reply = $script:Opening } }
        Select-ClientReplyTurn -Ticket $t | Should -BeNullOrEmpty
    }

    It 'without replyid, sets aside the first entry dated at the ticket date' {
        $t = New-TicketDetail -Replies @(
            (New-Reply -Id '' -Date '2026-10-01 10:00:00' -Message $script:TemplateBody)
            (New-Reply -Id '' -Date '2026-10-02 09:00:00' -Message 'Approved')
        )
        (Select-ClientReplyTurn -Ticket $t).Messages | Should -Be @('Approved')
    }

    It 'without replyid, keeps the first entry when it is not at the ticket date' {
        $t = New-TicketDetail -Date '2026-10-01 10:00:00' -Replies @(
            (New-Reply -Id '' -Date '2026-10-02 09:00:00' -Message 'Please change the logo')
            (New-Reply -Id '' -Date '2026-10-02 10:00:00' -Message 'Otherwise approved')
        )
        (Select-ClientReplyTurn -Ticket $t).Count | Should -Be 2
    }

    It 'without replyid or a ticket date, sets aside the first entry (fail closed)' {
        $t = [pscustomobject]@{ replies = [pscustomobject]@{ reply = @((New-Reply -Id '' -Date '2026-10-01 10:00:00' -Message 'opening')) } }
        Select-ClientReplyTurn -Ticket $t | Should -BeNullOrEmpty
    }
}

Describe 'Select-LatestTicket' {
    BeforeAll {
        $script:Tickets = @(
            [pscustomobject]@{ id = '40'; userid = '701'; subject = '[Website Preview] Your new website is ready to review'; status = 'Closed' }
            [pscustomobject]@{ id = '55'; userid = '701'; subject = '[Website Preview] Your new website is ready to review'; status = 'Customer-Reply' }
            [pscustomobject]@{ id = '60'; userid = '701'; subject = '[Application] A few details are needed'; status = 'Open' }
            [pscustomobject]@{ id = '70'; userid = '701'; subject = 'Re: question about [Website Preview]'; status = 'Open' }
            [pscustomobject]@{ id = '80'; userid = '999'; subject = '[Website Preview] other client'; status = 'Open' }
        )
    }
    It 'picks the highest id whose subject starts with the prefix' {
        (Select-LatestTicket -Tickets $script:Tickets -SubjectPrefix '[Website Preview]' -ClientId 701).id | Should -Be '55'
    }
    It 'selects by the [Application] prefix independently' {
        (Select-LatestTicket -Tickets $script:Tickets -SubjectPrefix '[Application]' -ClientId 701).id | Should -Be '60'
    }
    It 'ignores a prefix that is not at the start, and another client''s ticket' {
        $only = @($script:Tickets[3], $script:Tickets[4])
        Select-LatestTicket -Tickets $only -SubjectPrefix '[Website Preview]' -ClientId 701 | Should -BeNullOrEmpty
    }
    It 'matches case-insensitively and through HTML entities' {
        $t = @([pscustomobject]@{ id = '9'; subject = '&#91;website preview&#93; ready' })
        (Select-LatestTicket -Tickets $t -SubjectPrefix '[Website Preview]' -ClientId 701).id | Should -Be '9'
    }
    It 'returns $null for no tickets and skips non-numeric ids' {
        Select-LatestTicket -Tickets @() -SubjectPrefix '[Website Preview]' -ClientId 701 | Should -BeNullOrEmpty
        Select-LatestTicket -Tickets @([pscustomobject]@{ id = 'x'; subject = '[Website Preview] a' }) -SubjectPrefix '[Website Preview]' -ClientId 701 | Should -BeNullOrEmpty
    }
}

Describe 'Small helpers' {
    It 'converts a WHMCS date to UTC under the assumed zone' {
        ConvertTo-TicketReplyDateUtc -WhmcsDate '2026-10-02 09:05:00' -TimeZoneId 'UTC' | Should -Be '2026-10-02T09:05:00Z'
        ConvertTo-TicketReplyDateUtc -WhmcsDate '0000-00-00 00:00:00' | Should -Be ''
        ConvertTo-TicketReplyDateUtc -WhmcsDate '' | Should -Be ''
    }
    It 'passes a WHMCS status name and replaces anything else with OTHER' {
        Format-TicketReplyStatus -Status 'Customer-Reply' | Should -Be 'Customer-Reply'
        Format-TicketReplyStatus -Status 'On Hold' | Should -Be 'On Hold'
        Format-TicketReplyStatus -Status '<script>x</script>' | Should -Be 'OTHER'
        Format-TicketReplyStatus -Status '' | Should -BeNullOrEmpty
    }
    It 'cuts a response excerpt out of a WHMCS error' {
        $e = Get-TicketReplySafeError -Message 'WHMCS API returned a non-JSON response: <b>Notice</b>{"replies":{"reply":[{"message":"SECRET-REPLY-TEXT"}]}}'
        $e | Should -Not -Match 'SECRET-REPLY-TEXT'
        $e | Should -Match 'body withheld'
        $e2 = Get-TicketReplySafeError -Message 'Unknown WHMCS API error. Response: {"message":"SECRET-REPLY-TEXT"}'
        $e2 | Should -Not -Match 'SECRET-REPLY-TEXT'
        Get-TicketReplySafeError -Message 'WHMCS API error: Ticket ID Not Found' | Should -Be 'WHMCS API error: Ticket ID Not Found'
    }
}

Describe 'Invoke-TicketReplyLookup (fixture API, output contract)' {
    BeforeAll {
        $script:Sentinel = 'SENTINEL-7731 Jane Doe jane.doe@example.org (555) 123-4567'

        function New-FixtureApi {
            param([object[]]$Tickets, $Detail)
            $script:Calls = [System.Collections.Generic.List[hashtable]]::new()
            $script:FxTickets = $Tickets
            $script:FxDetail = $Detail
            return {
                param([hashtable]$Body)
                $script:Calls.Add($Body.Clone())
                switch ($Body.action) {
                    'GetTickets' {
                        return [pscustomobject]@{ result = 'success'; totalresults = "$(@($script:FxTickets).Count)"; tickets = [pscustomobject]@{ ticket = @($script:FxTickets) } }
                    }
                    'GetTicket' { return $script:FxDetail }
                    default { throw "unexpected action $($Body.action)" }
                }
            }
        }

        $script:PreviewTicket = [pscustomobject]@{
            id = '501'; tid = 'ABC-123456'; userid = '701'; status = 'Customer-Reply'; name = 'Jane Doe'; email = 'jane.doe@example.org'
            subject = '[Website Preview] Your new website is ready to review SENTINEL-SUBJECT'; date = '2026-10-01 10:00:00'
        }
    }

    It 'is TICKET_NOT_FOUND with no ticket id when nothing matches, and never calls GetTicket' {
        $api = New-FixtureApi -Tickets @([pscustomobject]@{ id = '9'; userid = '701'; subject = 'Billing question' }) -Detail $null
        $r = Invoke-TicketReplyLookup -ClientId 701 -SubjectPrefix '[Website Preview]' -InvokeApi $api
        $r.classification | Should -Be 'TICKET_NOT_FOUND'
        $r.ticket_id | Should -BeNullOrEmpty
        @($script:Calls | ForEach-Object { $_.action }) | Should -Be @('GetTickets')
        $script:Calls[0].clientid | Should -Be 701
    }

    It 'is TICKET_NOT_FOUND for a client with no tickets at all' {
        $api = New-FixtureApi -Tickets @() -Detail $null
        (Invoke-TicketReplyLookup -ClientId 701 -SubjectPrefix '[Application]' -InvokeApi $api).classification | Should -Be 'TICKET_NOT_FOUND'
    }

    It 'is NO_REPLY when the thread holds only FFC''s opening message' {
        $api = New-FixtureApi -Tickets @($script:PreviewTicket) -Detail (New-TicketDetail -Replies @($script:Opening) -Status 'Open')
        $r = Invoke-TicketReplyLookup -ClientId 701 -SubjectPrefix '[Website Preview]' -InvokeApi $api
        $r.classification | Should -Be 'NO_REPLY'
        $r.ticket_id | Should -Be '501'
        $r.ticket_status | Should -Be 'Open'
        $r.reply_date_utc | Should -BeNullOrEmpty
    }

    It 'asks GetTicket for that ticket id, oldest reply first' {
        $api = New-FixtureApi -Tickets @($script:PreviewTicket) -Detail (New-TicketDetail -Replies @($script:Opening))
        $null = Invoke-TicketReplyLookup -ClientId 701 -SubjectPrefix '[Website Preview]' -InvokeApi $api
        $get = $script:Calls | Where-Object { $_.action -eq 'GetTicket' }
        $get.ticketid | Should -Be 501
        $get.repliessort | Should -Be 'ASC'
        @($script:Calls | ForEach-Object { $_.action } | Sort-Object -Unique) | Should -Be @('GetTicket', 'GetTickets')
    }

    Context 'an APPROVED reply carrying names, emails, phones and a sentinel' {
        BeforeAll {
            $reply = New-Reply -Id '12' -Date '2026-10-02 09:05:00' -Message "Approved! $($script:Sentinel)"
            $api = New-FixtureApi -Tickets @($script:PreviewTicket) -Detail (New-TicketDetail -Replies @($script:Opening, $reply))
            $script:Result = Invoke-TicketReplyLookup -ClientId 701 -SubjectPrefix '[Website Preview]' -InvokeApi $api -RunUrl 'https://example.test/run/1' -GeneratedAt '2026-10-11T00:00:00Z'
            $script:Json = $script:Result | ConvertTo-Json -Depth 4
            $script:Md = Format-TicketReplyMarkdown -Result $script:Result
        }

        It 'classifies it APPROVED with its date in UTC' {
            $script:Result.classification | Should -Be 'APPROVED'
            $script:Result.reply_date_utc | Should -Be '2026-10-02T09:05:00Z'
            $script:Result.ticket_status | Should -Be 'Customer-Reply'
            $script:Result.approve_signals | Should -Be @('approved')
        }

        It 'emits exactly the allowed fields' {
            @($script:Result.PSObject.Properties.Name) | Should -Be @(
                'schema', 'client_id', 'subject_prefix', 'ticket_id', 'ticket_status', 'reply_date_utc', 'classification',
                'approve_signals', 'change_signals', 'hold_signals', 'whmcs_timezone_assumed', 'read_only', 'run_url', 'generated_at'
            )
        }

        It 'never emits the reply text, a name, an email, a phone, the tid or the subject text' {
            foreach ($out in @($script:Json, $script:Md)) {
                foreach ($needle in @('SENTINEL', 'Jane', 'Doe', 'jane.doe', 'example.org', '555', '123-4567', 'ABC-123456', 'ready to review', 'Your new website')) {
                    $out | Should -Not -Match ([regex]::Escape($needle))
                }
            }
        }

        It 'carries the issue-comment marker and the fixed prefix only' {
            $script:Md | Should -Match '<!-- charity-lane:ticket-reply -->'
            $script:Md | Should -Match ([regex]::Escape('`[Website Preview]`'))
            $script:Md | Should -Match '\*\*APPROVED\*\*'
        }
    }

    It 'emits only fixed-list labels for a CHANGES reply, never the words around them' {
        $reply = New-Reply -Id '12' -Date '2026-10-02 09:05:00' -Message 'Please change the PHONE to 555-987-6543 and add Bob Smith'
        $api = New-FixtureApi -Tickets @($script:PreviewTicket) -Detail (New-TicketDetail -Replies @($script:Opening, $reply))
        $r = Invoke-TicketReplyLookup -ClientId 701 -SubjectPrefix '[Website Preview]' -InvokeApi $api
        $r.classification | Should -Be 'CHANGES'
        $r.change_signals | Should -Be @('change', 'add')
        $out = ($r | ConvertTo-Json -Depth 4) + (Format-TicketReplyMarkdown -Result $r)
        $out | Should -Not -Match 'PHONE|Bob|Smith|987'
    }

    It 'refuses a prefix outside the fixed choices before any API call' {
        $api = New-FixtureApi -Tickets @() -Detail $null
        { Invoke-TicketReplyLookup -ClientId 701 -SubjectPrefix '[Anything]' -InvokeApi $api } | Should -Throw
        $script:Calls.Count | Should -Be 0
    }

    It 'drops a signal label that is not in the fixed lists' {
        $r = ConvertTo-TicketReplyResult -ClientId 1 -SubjectPrefix '[Website Preview]' -Classification 'CHANGES' -ChangeSignals @('change', 'free text from a reply')
        $r.change_signals | Should -Be @('change')
    }

    It 'rejects an unknown classification' {
        { ConvertTo-TicketReplyResult -ClientId 1 -SubjectPrefix '[Website Preview]' -Classification 'MAYBE' } | Should -Throw
    }
}
