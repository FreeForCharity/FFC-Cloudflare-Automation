# Multi-character-string TXT handling in Update-CloudflareDns.ps1.
#
# WHY: a TXT character-string maxes out at 255 bytes (RFC 1035 3.3.14), so a
# 2048-bit DKIM key is stored as SEVERAL of them, joined with no separator.
# Cloudflare therefore hands back text that never equals the text that was sent.
#
# The consequence, before this was fixed: 105's TXT match compared raw strings,
# so a DKIM key read as ABSENT on every re-run and got appended again. Two TXT
# records at one selector does not degrade gracefully — verifiers see an
# ambiguous key and signatures fail, so the second write breaks mail
# authentication rather than merely duplicating a record.
#
# The fixture is the real google._domainkey.slopestohope.com value, read from
# public DNS on 2026-08-04. A DKIM *public* key is public by definition; there
# is nothing secret here, and using the real thing is the point — a synthetic
# 300-char string would not have caught the mid-base64 split.

BeforeAll {
    $script:SourcePath = (Resolve-Path (Join-Path $PSScriptRoot '..' 'Update-CloudflareDns.ps1')).Path

    function Get-FunctionFromFile {
        param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$Name)
        $ast = [System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$null, [ref]$null)
        $fn = $ast.Find({
                param($n)
                $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $Name
            }, $true)
        if (-not $fn) { throw "$Name not found in $Path" }
        return $fn
    }
    # Test-DnsContentMatch and Get-DnsRecordPayload are the two pieces of the
    # SET path that this PR actually changes. They exist as named functions
    # precisely so this extractor can reach them -- while the logic was inline
    # in the Where-Object and the payload literal, reverting either fix left
    # this suite at 19 passed / 0 failed. Green testified about the helpers and
    # nothing about the bug.
    foreach ($n in @('Normalize-TxtContent', 'Is-TxtQuoted', 'Quote-TxtContent',
            'Test-DnsContentMatch', 'Get-DnsRecordPayload', 'Test-DnsRecordShouldRemove')) {
        . ([scriptblock]::Create((Get-FunctionFromFile -Path $script:SourcePath -Name $n).Extent.Text))
    }

    $script:DQ = [char]34

    # Segment 1 is exactly 255 characters — the split lands mid-base64.
    $script:DkimSeg1 = 'v=DKIM1; k=rsa; p=MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAwpVEwmdI7SGsLrwrKQ4hL//PE8BzoTDc4KrEWPzsDesggKbCrx97HTDFXzrcTI4aMjiPO4h8l4F1KVXglDbEHJ0FxK0TqUNcQITsVYi0ut9e1qaToIAP6D/RGyiHRZm4nJiBa12HFZWkZwrMh16tpD5Gl8S3dclWXShEceKW20Cmtrwef/SuToytQm2emcIGs'
    $script:DkimSeg2 = 'Z+XWAcTVmtxDq8oU5ctVfudxAuMfQlXKVhXZjzPSYZ0e92EuF9IhwnokP107q6NRi3za/bF7YKqUcPD3hAcTUmC1f7/3jmFCtFeM2crfb8WJ9/Cp23bBg/3MT3PsgEA9fOVlkBZqyRKAYhwx9tMzQIDAQAB'

    # How DNS / Cloudflare hand it back:  "seg1" "seg2"
    $script:DkimStored = "$($script:DQ)$($script:DkimSeg1)$($script:DQ) $($script:DQ)$($script:DkimSeg2)$($script:DQ)"
    # How a human pastes it out of the Google Admin console: one unbroken string.
    $script:DkimPasted = $script:DkimSeg1 + $script:DkimSeg2
}

Describe 'Normalize-TxtContent with multiple character-strings' {
    It 'joins segments with NO separator' {
        # A space here would corrupt the base64 and silently break DKIM.
        Normalize-TxtContent -Value $script:DkimStored | Should -Be $script:DkimPasted
    }

    It 'recovers a key that is still valid base64 after the join' {
        $v = Normalize-TxtContent -Value $script:DkimStored
        $v | Should -BeLike 'v=DKIM1; k=rsa; p=*'
        $p = ($v -split 'p=', 2)[1]
        { [Convert]::FromBase64String($p) } | Should -Not -Throw
        ([Convert]::FromBase64String($p)).Length | Should -BeGreaterThan 250   # 2048-bit SPKI
    }

    It 'leaves a single-segment value alone' {
        Normalize-TxtContent -Value "$($script:DQ)v=spf1 include:_spf.google.com ~all$($script:DQ)" |
            Should -Be 'v=spf1 include:_spf.google.com ~all'
    }

    It 'leaves an unquoted value alone' {
        Normalize-TxtContent -Value 'v=DMARC1; p=none' | Should -Be 'v=DMARC1; p=none'
    }

    It 'joins a split SPF record without inserting a space' {
        # Long SPF records get split too, and the split can land mid-mechanism.
        $split = "$($script:DQ)v=spf1 include:a.example.com inc$($script:DQ) $($script:DQ)lude:b.example.com ~all$($script:DQ)"
        Normalize-TxtContent -Value $split | Should -Be 'v=spf1 include:a.example.com include:b.example.com ~all'
    }

    It 'is idempotent' {
        $once = Normalize-TxtContent -Value $script:DkimStored
        Normalize-TxtContent -Value $once | Should -Be $once
    }
}

Describe 'Quote-TxtContent chunking' {
    It 'leaves a short value as one character-string' {
        $q = Quote-TxtContent -Value 'v=DMARC1; p=none'
        $q | Should -Be "$($script:DQ)v=DMARC1; p=none$($script:DQ)"
    }

    It 'splits an over-length value into legal character-strings' {
        $q = Quote-TxtContent -Value $script:DkimPasted
        $segs = [regex]::Matches($q, "$($script:DQ)([^$($script:DQ)]*)$($script:DQ)")
        $segs.Count | Should -Be 2
        foreach ($s in $segs) { $s.Groups[1].Value.Length | Should -BeLessOrEqual 255 }
    }

    It 'round-trips: quoting the logical value reproduces the stored form' {
        Quote-TxtContent -Value $script:DkimPasted | Should -Be $script:DkimStored
    }

    It 'round-trips the stored form back to itself' {
        Quote-TxtContent -Value $script:DkimStored | Should -Be $script:DkimStored
    }

    It 'never emits a character-string over the 255-byte limit' {
        # The pre-fix behaviour: one 410-char string, which is not a legal record.
        $q = Quote-TxtContent -Value $script:DkimPasted
        foreach ($m in [regex]::Matches($q, "$($script:DQ)([^$($script:DQ)]*)$($script:DQ)")) {
            $m.Groups[1].Value.Length | Should -BeLessOrEqual 255
        }
    }
}

Describe 'Idempotency across input shapes' {
    It 'sees the stored record and a fresh paste as the same value' {
        # THE regression. 105 matched raw content, so these compared unequal and
        # it appended a second DKIM record at the same selector.
        ($script:DkimStored -eq $script:DkimPasted) | Should -BeFalse -Because 'the raw forms genuinely differ'
        (Normalize-TxtContent -Value $script:DkimStored) |
            Should -Be (Normalize-TxtContent -Value $script:DkimPasted)
    }

    It 'still treats a genuinely different key as different' {
        # The fix must not make everything compare equal.
        $other = $script:DkimPasted -replace 'IDAQAB$', 'IDAQAC'
        (Normalize-TxtContent -Value $script:DkimStored) |
            Should -Not -Be (Normalize-TxtContent -Value $other)
    }

    It 'keeps Is-TxtQuoted true for the multi-segment form' {
        # The enforce path re-quotes anything unquoted; a segmented record must
        # not be mistaken for unquoted and rewritten on every run.
        Is-TxtQuoted -Value $script:DkimStored | Should -BeTrue
    }
}

Describe 'Test-DnsContentMatch — the SET path comparison' {
    # THE bug. 105's SET path decides create-vs-skip here, and it compared raw
    # content: a stored multi-segment DKIM key never equalled the freshly pasted
    # one, so every re-run appended a SECOND record at the same selector.
    # Two keys at one selector is not a harmless duplicate — verifiers see an
    # ambiguous key and signatures fail.

    It 'matches a stored multi-segment record against a freshly pasted key' {
        $rec = [pscustomobject]@{ content = $script:DkimStored; priority = $null }
        Test-DnsContentMatch -Type 'TXT' -Record $rec -DesiredContent $script:DkimPasted |
            Should -BeTrue
    }

    It 'matches when BOTH sides are already in stored form' {
        $rec = [pscustomobject]@{ content = $script:DkimStored; priority = $null }
        Test-DnsContentMatch -Type 'TXT' -Record $rec -DesiredContent $script:DkimStored |
            Should -BeTrue
    }

    It 'still refuses a genuinely different key' {
        # The fix must not make everything compare equal — otherwise a rotated
        # DKIM key would silently never be written.
        $other = $script:DkimPasted -replace 'IDAQAB$', 'IDAQAC'
        $rec = [pscustomobject]@{ content = $script:DkimStored; priority = $null }
        Test-DnsContentMatch -Type 'TXT' -Record $rec -DesiredContent $other |
            Should -BeFalse
    }

    It 'matches a single-segment TXT record' {
        $rec = [pscustomobject]@{ content = "$($script:DQ)v=spf1 include:_spf.google.com ~all$($script:DQ)" }
        Test-DnsContentMatch -Type 'TXT' -Record $rec -DesiredContent 'v=spf1 include:_spf.google.com ~all' |
            Should -BeTrue
    }

    It 'compares non-TXT types on raw content' {
        $rec = [pscustomobject]@{ content = 'freeforcharity.github.io' }
        Test-DnsContentMatch -Type 'CNAME' -Record $rec -DesiredContent 'freeforcharity.github.io' |
            Should -BeTrue
        Test-DnsContentMatch -Type 'CNAME' -Record $rec -DesiredContent 'elsewhere.github.io' |
            Should -BeFalse
    }

    It 'treats the same MX host at a different priority as NOT a match' {
        # Matching MX on content alone would leave a stale preference forever.
        $rec = [pscustomobject]@{ content = 'smtp.google.com'; priority = 10 }
        Test-DnsContentMatch -Type 'MX' -Record $rec -DesiredContent 'smtp.google.com' -Priority 1 |
            Should -BeFalse
        Test-DnsContentMatch -Type 'MX' -Record $rec -DesiredContent 'smtp.google.com' -Priority 10 |
            Should -BeTrue
    }

    It 'ignores priority entirely when the caller supplies none' {
        # The REMOVE path deletes by name+type+content. If it forwarded
        # $Priority's default of 10, an MX at any other preference would be
        # skipped and the delete would silently do nothing.
        $rec = [pscustomobject]@{ content = 'smtp.google.com'; priority = 1 }
        Test-DnsContentMatch -Type 'MX' -Record $rec -DesiredContent 'smtp.google.com' |
            Should -BeTrue
    }
}

Describe 'Test-DnsRecordShouldRemove — the DELETE path decision' {
    # The same defect as the SET path, in the DELETE path, found while wiring
    # cleanup for the live probe. It failed CLOSED: a raw -ne against a stored
    # multi-segment record matched nothing, so the delete silently removed
    # nothing and reported a content mismatch between two strings that look
    # identical. The dangerous part is the workaround that invites -- dropping
    # -Content to force it deletes EVERY record of that type at the name.
    #
    # These assert on Test-DnsRecordShouldRemove, NOT on Test-DnsContentMatch.
    # The first version of this block called the latter, and reverting the
    # remove path to a raw -ne left it 33/0 green: the comparison was exercised
    # while the CALL SITE that was wrong was not. Same trap as the SET path,
    # one level down.

    It 'finds a stored multi-segment record from its logical value' {
        $rec = [pscustomobject]@{ type = 'TXT'; content = $script:DkimStored }
        Test-DnsRecordShouldRemove -Record $rec -DesiredContent $script:DkimPasted |
            Should -BeTrue -Because 'otherwise the record can never be deleted by the value you can see'
    }

    It 'still declines to delete a record whose value genuinely differs' {
        $rec = [pscustomobject]@{ type = 'TXT'; content = $script:DkimStored }
        $other = $script:DkimPasted -replace 'IDAQAB$', 'IDAQAC'
        Test-DnsRecordShouldRemove -Record $rec -DesiredContent $other | Should -BeFalse
    }

    It 'deletes an MX regardless of its preference' {
        # Deletion is by name+type+content. Folding in $Priority's default of
        # 10 would silently refuse to delete an MX at any other preference.
        $rec = [pscustomobject]@{ type = 'MX'; content = 'smtp.google.com'; priority = 1 }
        Test-DnsRecordShouldRemove -Record $rec -DesiredContent 'smtp.google.com' | Should -BeTrue
    }

    It 'deletes everything of the type when no content filter is given' {
        # The documented behaviour, and exactly why 105 refuses to dispatch a
        # contentless remove rather than passing a blank through.
        $rec = [pscustomobject]@{ type = 'TXT'; content = 'anything at all' }
        Test-DnsRecordShouldRemove -Record $rec -DesiredContent '' | Should -BeTrue
        Test-DnsRecordShouldRemove -Record $rec -DesiredContent $null | Should -BeTrue
    }

    It 'does not treat a non-matching record as deletable just because TXT normalizes' {
        $rec = [pscustomobject]@{ type = 'CNAME'; content = 'freeforcharity.github.io' }
        Test-DnsRecordShouldRemove -Record $rec -DesiredContent 'elsewhere.example.com' | Should -BeFalse
    }
}

Describe 'Get-DnsRecordPayload — what actually goes on the wire' {
    It 'quotes and chunks a TXT value' {
        # Pre-fix this shipped one 410-char character-string, which is not a
        # legal record whatever Cloudflare chooses to do with it.
        $p = Get-DnsRecordPayload -Type 'TXT' -RecordName 'google._domainkey.x.org' `
            -Content $script:DkimPasted -Ttl 1
        $p['content'] | Should -Be $script:DkimStored
        foreach ($m in [regex]::Matches($p['content'], "$($script:DQ)([^$($script:DQ)]*)$($script:DQ)")) {
            [System.Text.Encoding]::UTF8.GetByteCount($m.Groups[1].Value) | Should -BeLessOrEqual 255
        }
    }

    It 'round-trips: the payload it builds is what the matcher then accepts' {
        # Write-then-read idempotency, end to end across both functions. If
        # these two ever disagree, 105 appends a duplicate on every run.
        $p = Get-DnsRecordPayload -Type 'TXT' -RecordName 'x' -Content $script:DkimPasted -Ttl 1
        $stored = [pscustomobject]@{ content = $p['content'] }
        Test-DnsContentMatch -Type 'TXT' -Record $stored -DesiredContent $script:DkimPasted |
            Should -BeTrue
    }

    It 'leaves a non-TXT value unquoted' {
        $p = Get-DnsRecordPayload -Type 'CNAME' -RecordName 'www.x.org' `
            -Content 'freeforcharity.github.io' -Ttl 1
        $p['content'] | Should -Be 'freeforcharity.github.io'
    }

    It 'omits proxied for MX and TXT, and sets it otherwise' {
        # Sending 'proxied' for MX/TXT is rejected by the API.
        (Get-DnsRecordPayload -Type 'TXT' -RecordName 'x' -Content 'v=spf1 ~all' -Ttl 1).ContainsKey('proxied') |
            Should -BeFalse
        (Get-DnsRecordPayload -Type 'MX' -RecordName 'x' -Content 'smtp.google.com' -Ttl 1 -Priority 1).ContainsKey('proxied') |
            Should -BeFalse
        (Get-DnsRecordPayload -Type 'A' -RecordName 'x' -Content '1.2.3.4' -Ttl 1 -Proxied $true)['proxied'] |
            Should -BeTrue
    }

    It 'carries priority only for MX' {
        (Get-DnsRecordPayload -Type 'MX' -RecordName 'x' -Content 'smtp.google.com' -Ttl 1 -Priority 1)['priority'] |
            Should -Be 1
        (Get-DnsRecordPayload -Type 'TXT' -RecordName 'x' -Content 'v=spf1 ~all' -Ttl 1).ContainsKey('priority') |
            Should -BeFalse
    }
}

Describe 'Chunking measures BYTES, not characters' {
    # RFC 1035 3.3.14 caps a character-string at 255 BYTES. Everything this repo
    # writes is ASCII, where bytes and characters coincide — but chunking on
    # .Length would silently emit an illegal record the first time a non-ASCII
    # value turned up, and nothing else in the suite would notice.
    It 'keeps every chunk within 255 BYTES for a multi-byte value' {
        # 'é' is 2 bytes in UTF-8, so 200 of them is 400 bytes: one character
        # chunk under the old rule, two byte chunks under the correct one.
        $multi = ([string][char]0x00E9) * 200
        $q = Quote-TxtContent -Value $multi
        $segs = [regex]::Matches($q, "$($script:DQ)([^$($script:DQ)]*)$($script:DQ)")
        $segs.Count | Should -BeGreaterThan 1
        foreach ($s in $segs) {
            [System.Text.Encoding]::UTF8.GetByteCount($s.Groups[1].Value) | Should -BeLessOrEqual 255
        }
    }

    It 'round-trips a multi-byte value without loss' {
        $multi = ([string][char]0x00E9) * 200
        Normalize-TxtContent -Value (Quote-TxtContent -Value $multi) | Should -Be $multi
    }

    It 'never splits a surrogate pair' {
        # U+1F600 is a surrogate pair in UTF-16 and 4 bytes in UTF-8. Splitting
        # between the halves produces an unpaired surrogate and a corrupt record.
        $emoji = [char]::ConvertFromUtf32(0x1F600)
        $long = $emoji * 100          # 400 bytes
        $q = Quote-TxtContent -Value $long
        foreach ($m in [regex]::Matches($q, "$($script:DQ)([^$($script:DQ)]*)$($script:DQ)")) {
            $v = $m.Groups[1].Value
            [char]::IsLowSurrogate($v[0]) | Should -BeFalse -Because 'a chunk must not begin mid-pair'
            [char]::IsHighSurrogate($v[$v.Length - 1]) | Should -BeFalse -Because 'a chunk must not end mid-pair'
        }
        Normalize-TxtContent -Value $q | Should -Be $long
    }

    It 'leaves an ASCII value chunked exactly as before' {
        # The DKIM fixture must be unaffected by the byte-based rule.
        Quote-TxtContent -Value $script:DkimPasted | Should -Be $script:DkimStored
    }

    It 'treats a 255-byte ASCII value as a single character-string' {
        $exact = 'a' * 255
        Quote-TxtContent -Value $exact | Should -Be "$($script:DQ)$exact$($script:DQ)"
    }
}
