# Unit tests for the mail-provider support in Update-CloudflareDns.ps1:
# Get-MailProviderProfile, Resolve-MailProvider and Get-SpfWithInclude.
#
# WHY THESE THREE: they are the pure, decidable core of a change whose blast
# radius is a charity's inbound mail. -EnforceStandard itself cannot be unit
# tested without a live Cloudflare zone, but every decision it makes about
# WHICH records are right, WHICH provider a zone is already on, and WHAT the
# rewritten SPF should say comes out of these functions.
#
# The SPF cases carry the most weight. A provider cutover must REWRITE the
# existing v=spf1 record, because two SPF TXT records on one name is a
# permerror (RFC 7208 s.4.5) that breaks authentication for every sender, not
# just the new provider. The pre-existing code path only knew how to "create if
# the include is missing", which on a cutover would have appended a second
# record. That is the regression these tests pin.
#
# Functions are extracted from the AST rather than dot-sourced: the script has a
# mandatory -Zone and would resolve a zone against the live API on load.

BeforeAll {
    $scriptPath = Join-Path $PSScriptRoot '..' 'Update-CloudflareDns.ps1'
    $script:SourcePath = (Resolve-Path $scriptPath).Path

    function Get-FunctionFromFile {
        param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$Name)
        $ast = [System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$null, [ref]$null)
        $fn = $ast.Find({
                param($n)
                $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
                $n.Name -eq $Name
            }, $true)
        if (-not $fn) { throw "$Name not found in $Path" }
        return $fn
    }

    $script:RegistryPath = (Resolve-Path (Join-Path $PSScriptRoot '..' 'data' 'mail-providers.json')).Path

    foreach ($name in @('Normalize-TxtContent', 'Get-MailProviderProfile', 'Resolve-MailProvider', 'Get-SpfWithInclude', 'Get-ForeignMailRecord', 'Get-ProviderMxRecord', 'ConvertTo-MailProviderName', 'Get-MailProviderRegistry', 'Resolve-MailProviderForZone', 'Test-MailProviderChangeConfirmed', 'Resolve-ApexSpfAction', 'Resolve-MultiValueRecordMatch')) {
        . ([scriptblock]::Create((Get-FunctionFromFile -Path $script:SourcePath -Name $name).Extent.Text))
    }
}

Describe 'Get-MailProviderProfile' {
    It 'returns Google a single smtp.google.com MX at priority 1' {
        $p = Get-MailProviderProfile -Provider 'Google' -ZoneName 'example.org'
        @($p.MxRecords).Count | Should -Be 1
        $p.MxRecords[0].Content | Should -Be 'smtp.google.com'
        $p.MxRecords[0].Priority | Should -Be 1
    }

    It 'gives Google no service CNAMEs or SRVs' {
        # Google Workspace needs no autodiscover/sip equivalents. If this ever
        # grows entries, the audit's "required CNAME" loop starts demanding them.
        $p = Get-MailProviderProfile -Provider 'Google' -ZoneName 'example.org'
        @($p.Cnames).Count | Should -Be 0
        @($p.Srvs).Count | Should -Be 0
    }

    It 'derives the per-tenant M365 MX host from the zone name' {
        $p = Get-MailProviderProfile -Provider 'Microsoft365' -ZoneName 'slopestohope.org'
        $p.MxRecords[0].Content | Should -Be 'slopestohope-org.mail.protection.outlook.com'
    }

    It 'keeps the full M365 service record set' {
        $p = Get-MailProviderProfile -Provider 'Microsoft365' -ZoneName 'example.org'
        @($p.Cnames).Count | Should -Be 5
        @($p.Srvs).Count | Should -Be 2
        ($p.Cnames.Name | Sort-Object) -join ',' |
            Should -Be 'autodiscover,enterpriseenrollment,enterpriseregistration,lyncdiscover,sip'
    }

    It 'gives the two providers non-overlapping MX matchers' {
        # The cutover deletes records matching the OTHER provider's MxMatch. If
        # these ever overlap, enforcing one provider would delete the MX it just
        # created.
        $g = Get-MailProviderProfile -Provider 'Google' -ZoneName 'example.org'
        $m = Get-MailProviderProfile -Provider 'Microsoft365' -ZoneName 'example.org'
        $g.MxRecords[0].Content | Should -Not -BeLike $m.MxMatch
        $m.MxRecords[0].Content | Should -Not -BeLike $g.MxMatch
    }

    It 'ships the correct SPF include for each provider' {
        # Added after a mutation run: changing Google's SpfInclude to a bogus
        # value left all 21 tests green, because every SPF test passes the
        # include in as a parameter and none pinned what the profile carries.
        # A wrong value here is invisible — enforcement writes the bogus include
        # and the audit then looks for the same bogus include, so the zone reads
        # as compliant while no Google mail is authorised.
        (Get-MailProviderProfile -Provider 'Google' -ZoneName 'example.org').SpfInclude |
            Should -Be 'include:_spf.google.com'
        (Get-MailProviderProfile -Provider 'Microsoft365' -ZoneName 'example.org').SpfInclude |
            Should -Be 'include:spf.protection.outlook.com'
    }

    It 'keeps SpfContent and SpfInclude consistent' {
        foreach ($provider in @('Google', 'Microsoft365')) {
            $p = Get-MailProviderProfile -Provider $provider -ZoneName 'example.org'
            $p.SpfContent | Should -BeLike "v=spf1*$($p.SpfInclude)*"
            $p.SpfContent | Should -Match '\s[-~?+]?all$'
        }
    }

    It 'matches its own MX content with its own MxMatch' {
        # MxMatch drives both "is the right MX present" and "is the foreign MX
        # here". A profile whose matcher does not match its own record would
        # recreate the MX on every run.
        foreach ($provider in @('Google', 'Microsoft365')) {
            $p = Get-MailProviderProfile -Provider $provider -ZoneName 'example.org'
            foreach ($mx in $p.MxRecords) { $mx.Content | Should -BeLike $p.MxMatch }
        }
    }

    It 'rejects an unknown provider' {
        { Get-MailProviderProfile -Provider 'Fastmail' -ZoneName 'example.org' } | Should -Throw
    }
}

Describe 'Resolve-MailProvider' {
    It 'detects Google from a modern smtp.google.com MX' {
        $records = @([pscustomobject]@{ type = 'MX'; name = 'example.org'; content = 'smtp.google.com' })
        Resolve-MailProvider -Records $records -ZoneName 'example.org' -Fallback 'Microsoft365' |
            Should -Be 'Google'
    }

    It 'detects Google from the legacy ASPMX layout' {
        # A zone set up years ago still has ALT1-4. Auditing it as Microsoft
        # would report a working Google tenant as entirely non-compliant.
        $records = @(
            [pscustomobject]@{ type = 'MX'; name = 'example.org'; content = 'aspmx.l.google.com' },
            [pscustomobject]@{ type = 'MX'; name = 'example.org'; content = 'alt1.aspmx.l.google.com' }
        )
        Resolve-MailProvider -Records $records -ZoneName 'example.org' -Fallback 'Microsoft365' |
            Should -Be 'Google'
    }

    It 'detects Microsoft 365 from the tenant mail host' {
        $records = @([pscustomobject]@{ type = 'MX'; name = 'example.org'; content = 'example-org.mail.protection.outlook.com' })
        Resolve-MailProvider -Records $records -ZoneName 'example.org' -Fallback 'Google' |
            Should -Be 'Microsoft365'
    }

    It 'falls back when the zone has no MX at all' {
        $records = @([pscustomobject]@{ type = 'A'; name = 'example.org'; content = '203.0.113.10' })
        Resolve-MailProvider -Records $records -ZoneName 'example.org' -Fallback 'Microsoft365' |
            Should -Be 'Microsoft365'
    }

    It 'falls back when the MX belongs to neither provider' {
        $records = @([pscustomobject]@{ type = 'MX'; name = 'example.org'; content = 'mx1.mailgun.org' })
        Resolve-MailProvider -Records $records -ZoneName 'example.org' -Fallback 'Microsoft365' |
            Should -Be 'Microsoft365'
    }

    It 'accepts an empty record set' {
        Resolve-MailProvider -Records @() -ZoneName 'example.org' -Fallback 'Google' | Should -Be 'Google'
    }

    It 'does not mistake a lookalike domain for Google' {
        # Regression, PR #1050 review. MxMatch was '*google.com', and -like is
        # anchored at the end, so 'notgoogle.com' matched. That is not just a
        # misdetection: on a Microsoft cutover the delete list is built from
        # this same matcher, so a third party's MX would have been removed.
        # NB: not $host — that is a read-only PowerShell automatic variable.
        foreach ($mxHost in @('notgoogle.com', 'mygoogle.com', 'evilgoogle.com')) {
            $records = @([pscustomobject]@{ type = 'MX'; name = 'example.org'; content = $mxHost })
            Resolve-MailProvider -Records $records -ZoneName 'example.org' -Fallback 'Microsoft365' |
                Should -Be 'Microsoft365' -Because "$mxHost is not a Google host"
        }
    }
}

Describe 'Get-ForeignMailRecord' {
    BeforeAll {
        $script:M365 = Get-MailProviderProfile -Provider 'Microsoft365' -ZoneName 'example.org'
        $script:Goog = Get-MailProviderProfile -Provider 'Google' -ZoneName 'example.org'
    }

    It 'selects the M365 MX, service CNAMEs and SRVs for a Google cutover' {
        $records = @(
            [pscustomobject]@{ id = '1'; type = 'MX'; name = 'example.org'; content = 'example-org.mail.protection.outlook.com' }
            [pscustomobject]@{ id = '2'; type = 'CNAME'; name = 'autodiscover.example.org'; content = 'autodiscover.outlook.com' }
            [pscustomobject]@{ id = '3'; type = 'SRV'; name = '_sip._tls.example.org'; data = @{ target = 'sipdir.online.lync.com' } }
        )
        (Get-ForeignMailRecord -Records $records -ForeignProfile $script:M365 -ZoneName 'example.org').id |
            Sort-Object | Should -Be @('1', '2', '3')
    }

    It 'leaves records that are not the foreign provider alone' {
        # The apex A, the www CNAME, SPF/DMARC TXT and a third-party MX must all
        # survive a cutover. This is the blast-radius test.
        $records = @(
            [pscustomobject]@{ id = 'a'; type = 'A'; name = 'example.org'; content = '185.199.108.153' }
            [pscustomobject]@{ id = 'b'; type = 'CNAME'; name = 'www.example.org'; content = 'freeforcharity.github.io' }
            [pscustomobject]@{ id = 'c'; type = 'TXT'; name = 'example.org'; content = 'v=spf1 include:_spf.google.com ~all' }
            [pscustomobject]@{ id = 'd'; type = 'MX'; name = 'example.org'; content = 'mx1.mailgun.org' }
            [pscustomobject]@{ id = 'e'; type = 'CNAME'; name = 'hfqjxalfj5ef.example.org'; content = 'gv-2wbkdyugdfdd33.dv.googlehosted.com' }
        )
        Get-ForeignMailRecord -Records $records -ForeignProfile $script:M365 -ZoneName 'example.org' |
            Should -BeNullOrEmpty
    }

    It 'matches a service CNAME exactly, not by substring' {
        # Regression, PR #1050 review. The matcher was -like "*<target>*", so a
        # CNAME at the same name pointing somewhere that merely CONTAINS the
        # M365 target was swept into the delete list.
        $records = @(
            [pscustomobject]@{ id = 'x'; type = 'CNAME'; name = 'autodiscover.example.org'; content = 'autodiscover.outlook.com.cdn.example.net' }
        )
        Get-ForeignMailRecord -Records $records -ForeignProfile $script:M365 -ZoneName 'example.org' |
            Should -BeNullOrEmpty
    }

    It 'does not delete an SRV at the right name pointing at the wrong target' {
        $records = @(
            [pscustomobject]@{ id = 'y'; type = 'SRV'; name = '_sip._tls.example.org'; data = @{ target = 'sip.someone-else.example' } }
        )
        Get-ForeignMailRecord -Records $records -ForeignProfile $script:M365 -ZoneName 'example.org' |
            Should -BeNullOrEmpty
    }

    It 'does not select a lookalike domain when cutting over to Microsoft' {
        $records = @([pscustomobject]@{ id = 'z'; type = 'MX'; name = 'example.org'; content = 'notgoogle.com' })
        Get-ForeignMailRecord -Records $records -ForeignProfile $script:Goog -ZoneName 'example.org' |
            Should -BeNullOrEmpty
    }

    It 'selects Google MX in both modern and legacy layouts for a Microsoft cutover' {
        $records = @(
            [pscustomobject]@{ id = 'm'; type = 'MX'; name = 'example.org'; content = 'smtp.google.com' }
            [pscustomobject]@{ id = 'l'; type = 'MX'; name = 'example.org'; content = 'alt1.aspmx.l.google.com' }
        )
        (Get-ForeignMailRecord -Records $records -ForeignProfile $script:Goog -ZoneName 'example.org').id |
            Sort-Object | Should -Be @('l', 'm')
    }

    It 'scopes service CNAMEs to the zone being enforced' {
        # Same record name under a different zone must not be selected.
        $records = @(
            [pscustomobject]@{ id = 'o'; type = 'CNAME'; name = 'autodiscover.other.org'; content = 'autodiscover.outlook.com' }
        )
        Get-ForeignMailRecord -Records $records -ForeignProfile $script:M365 -ZoneName 'example.org' |
            Should -BeNullOrEmpty
    }
}

Describe 'Get-SpfWithInclude' {
    It 'swaps the Microsoft include for the Google one on cutover' {
        Get-SpfWithInclude -SpfContent 'v=spf1 include:spf.protection.outlook.com -all' `
            -DesiredInclude 'include:_spf.google.com' `
            -RemoveIncludes @('include:spf.protection.outlook.com') |
            Should -Be 'v=spf1 include:_spf.google.com -all'
    }

    It 'preserves unrelated senders and their order' {
        # A charity's newsletter/CRM includes must survive a mail migration.
        # Dropping one silently starts failing that sender's mail days later.
        Get-SpfWithInclude -SpfContent 'v=spf1 include:spf.protection.outlook.com include:mailgun.org ip4:203.0.113.9 ~all' `
            -DesiredInclude 'include:_spf.google.com' `
            -RemoveIncludes @('include:spf.protection.outlook.com') |
            Should -Be 'v=spf1 include:mailgun.org ip4:203.0.113.9 include:_spf.google.com ~all'
    }

    It 'inserts the include BEFORE the all-qualifier' {
        # A mechanism after the catch-all is never evaluated, so appending
        # blindly would produce a record that parses but authorises nothing.
        $out = Get-SpfWithInclude -SpfContent 'v=spf1 ip4:203.0.113.9 -all' `
            -DesiredInclude 'include:_spf.google.com' -RemoveIncludes @()
        $out | Should -Be 'v=spf1 ip4:203.0.113.9 include:_spf.google.com -all'
        $terms = $out -split ' '
        [array]::IndexOf($terms, 'include:_spf.google.com') |
            Should -BeLessThan ([array]::IndexOf($terms, '-all'))
    }

    It 'is idempotent when the desired include is already present' {
        # Re-running enforcement must not rewrite the record, or every run
        # reports a change and the "[OK]" signal becomes meaningless.
        $spf = 'v=spf1 include:_spf.google.com ~all'
        Get-SpfWithInclude -SpfContent $spf -DesiredInclude 'include:_spf.google.com' `
            -RemoveIncludes @('include:spf.protection.outlook.com') | Should -Be $spf
    }

    It 'strips Cloudflare TXT quoting before rewriting' {
        Get-SpfWithInclude -SpfContent '"v=spf1 include:spf.protection.outlook.com -all"' `
            -DesiredInclude 'include:_spf.google.com' `
            -RemoveIncludes @('include:spf.protection.outlook.com') |
            Should -Be 'v=spf1 include:_spf.google.com -all'
    }

    It 'appends when the record has no all-qualifier' {
        Get-SpfWithInclude -SpfContent 'v=spf1 ip4:203.0.113.9' `
            -DesiredInclude 'include:_spf.google.com' -RemoveIncludes @() |
            Should -Be 'v=spf1 ip4:203.0.113.9 include:_spf.google.com'
    }

    It 'collapses irregular whitespace' {
        Get-SpfWithInclude -SpfContent "v=spf1   include:spf.protection.outlook.com    -all" `
            -DesiredInclude 'include:_spf.google.com' `
            -RemoveIncludes @('include:spf.protection.outlook.com') |
            Should -Be 'v=spf1 include:_spf.google.com -all'
    }

    It 'handles every all-qualifier form' {
        foreach ($q in @('-all', '~all', '?all', '+all', 'all')) {
            Get-SpfWithInclude -SpfContent "v=spf1 $q" -DesiredInclude 'include:_spf.google.com' -RemoveIncludes @() |
                Should -Be "v=spf1 include:_spf.google.com $q"
        }
    }

    It 'never emits two v=spf1 records worth of content' {
        # The permerror this whole code path exists to avoid.
        $out = Get-SpfWithInclude -SpfContent 'v=spf1 include:spf.protection.outlook.com -all' `
            -DesiredInclude 'include:_spf.google.com' `
            -RemoveIncludes @('include:spf.protection.outlook.com')
        ([regex]::Matches($out, 'v=spf1')).Count | Should -Be 1
        ([regex]::Matches($out, 'include:')).Count | Should -Be 1
    }
}

Describe 'ConvertTo-MailProviderName' {
    It 'canonicalises the spellings a human might write' {
        foreach ($v in @('google', 'Google', ' GOOGLE ', 'gsuite', 'google-workspace')) {
            ConvertTo-MailProviderName -Value $v | Should -Be 'Google'
        }
        foreach ($v in @('microsoft365', 'M365', 'o365', 'microsoft')) {
            ConvertTo-MailProviderName -Value $v | Should -Be 'Microsoft365'
        }
    }

    It 'returns null for anything it does not recognise' {
        # Must NOT fall back to a default: silently picking a provider for an
        # unrecognised value would choose a charity's mail host for them.
        foreach ($v in @('fastmail', '', 'goo', $null)) {
            ConvertTo-MailProviderName -Value $v | Should -BeNullOrEmpty
        }
    }
}

Describe 'Get-MailProviderRegistry' {
    It 'reads the tracked registry that ships with the repo' {
        $reg = Get-MailProviderRegistry -Path $script:RegistryPath
        $reg.Default | Should -Be 'Unchanged'
        $reg.Domains['slopestohope.org'] | Should -Be 'Google'
        # Populated from the 2026-08-04 fleet survey, not a stub.
        $reg.Domains.Count | Should -BeGreaterThan 40
    }

    It 'defaults to NO CHANGE, never to a provider' {
        # The single most dangerous value in this file. A Microsoft default
        # would impose Microsoft mail on every unlisted zone -- 35 live Google
        # domains and 46 on other hosts, per the same survey.
        (Get-MailProviderRegistry -Path $script:RegistryPath).Default | Should -Be 'Unchanged'
    }

    It 'treats a missing file as manage-nobody' {
        $reg = Get-MailProviderRegistry -Path (Join-Path $TestDrive 'does-not-exist.json')
        $reg.Default | Should -Be 'Unchanged'
        $reg.Domains.Count | Should -Be 0
    }

    It 'every shipped entry is a real provider, never Unchanged' {
        $reg = Get-MailProviderRegistry -Path $script:RegistryPath
        foreach ($kv in $reg.Domains.GetEnumerator()) {
            $kv.Value | Should -BeIn @('Microsoft365', 'Google')
        }
    }

    It 'THROWS on an unknown provider rather than falling back' {
        # The dangerous failure mode. A typo'd value that quietly resolved to the
        # default would cut every Google domain over to Microsoft on the next
        # enforcement run.
        $bad = Join-Path $TestDrive 'bad.json'
        '{"default":"microsoft365","domains":{"a.org":"gmial"}}' | Set-Content -Path $bad -Encoding utf8
        { Get-MailProviderRegistry -Path $bad } | Should -Throw '*gmial*'
    }

    It 'THROWS on an unknown default' {
        $bad = Join-Path $TestDrive 'baddefault.json'
        '{"default":"yahoo","domains":{}}' | Set-Content -Path $bad -Encoding utf8
        { Get-MailProviderRegistry -Path $bad } | Should -Throw '*yahoo*'
    }

    It 'is case-insensitive on domain keys' {
        $f = Join-Path $TestDrive 'case.json'
        '{"default":"microsoft365","domains":{"MiXeD.OrG":"google"}}' | Set-Content -Path $f -Encoding utf8
        (Get-MailProviderRegistry -Path $f).Domains['mixed.org'] | Should -Be 'Google'
    }
}

Describe 'Resolve-MailProviderForZone' {
    BeforeAll {
        $script:Reg = Get-MailProviderRegistry -Path $script:RegistryPath
    }

    It 'returns the registry entry for a listed domain' {
        Resolve-MailProviderForZone -ZoneName 'slopestohope.org' -Registry $script:Reg -Explicit '' |
            Should -Be 'Google'
    }

    It 'leaves an unlisted domain UNCHANGED, not Microsoft' {
        Resolve-MailProviderForZone -ZoneName 'some-other-charity.org' -Registry $script:Reg -Explicit '' |
            Should -Be 'Unchanged'
    }

    It 'leaves every surveyed non-FFC-mail domain unchanged' {
        # Spot-check domains the survey found on other providers. These must
        # never resolve to a provider, or enforcement would clobber live mail.
        foreach ($d in @('ptuganda.org', 'exceptionalridersprogram.com', 'ffc.ngo', 'clarasbridge.org')) {
            Resolve-MailProviderForZone -ZoneName $d -Registry $script:Reg -Explicit '' |
                Should -Be 'Unchanged' -Because "$d is on a third-party mail host"
        }
    }

    It 'lets an explicit choice override the registry' {
        Resolve-MailProviderForZone -ZoneName 'slopestohope.org' -Registry $script:Reg -Explicit 'Microsoft365' |
            Should -Be 'Microsoft365'
    }

    It 'is case-insensitive on the zone name' {
        Resolve-MailProviderForZone -ZoneName 'SlopesToHope.ORG' -Registry $script:Reg -Explicit '' |
            Should -Be 'Google'
    }

    It 'throws on an unknown explicit value' {
        { Resolve-MailProviderForZone -ZoneName 'a.org' -Registry $script:Reg -Explicit 'fastmail' } |
            Should -Throw '*fastmail*'
    }
}

Describe 'Exactly-one provider (set-and-match)' {
    It 'produces disjoint record sets for the two providers' {
        # The core invariant. Whatever one provider enforces, the other must
        # consider foreign, or a zone could satisfy both at once.
        $zone = 'example.org'
        $g = Get-MailProviderProfile -Provider 'Google' -ZoneName $zone
        $m = Get-MailProviderProfile -Provider 'Microsoft365' -ZoneName $zone

        foreach ($mx in $g.MxRecords) { $mx.Content | Should -Not -BeLike $m.MxMatch }
        foreach ($mx in $m.MxRecords) { $mx.Content | Should -Not -BeLike $g.MxMatch }
        $g.SpfInclude | Should -Not -Be $m.SpfInclude
    }

    It 'selects the whole M365 set as foreign when the domain resolves to Google' {
        $zone = 'example.org'
        $m = Get-MailProviderProfile -Provider 'Microsoft365' -ZoneName $zone
        $records = @(
            [pscustomobject]@{ id = 'mx'; type = 'MX'; name = $zone; content = 'example-org.mail.protection.outlook.com' }
        )
        foreach ($c in $m.Cnames) {
            $records += [pscustomobject]@{ id = "c-$($c.Name)"; type = 'CNAME'; name = "$($c.Name).$zone"; content = $c.Content }
        }
        foreach ($s in $m.Srvs) {
            $records += [pscustomobject]@{ id = "s-$($s.Name)"; type = 'SRV'; name = "$($s.Name).$zone"; data = @{ target = $s.Data.target } }
        }

        # 1 MX + 5 CNAMEs + 2 SRVs: the entire set goes, not a subset.
        @(Get-ForeignMailRecord -Records $records -ForeignProfile $m -ZoneName $zone).Count |
            Should -Be $records.Count
    }

    It 'sweeps up a split-brain zone so exactly one provider survives' {
        # Both providers present -> enforcing Google must select every Microsoft
        # record for removal and no Google one.
        $zone = 'example.org'
        $m = Get-MailProviderProfile -Provider 'Microsoft365' -ZoneName $zone
        $records = @(
            [pscustomobject]@{ id = 'goog'; type = 'MX'; name = $zone; content = 'smtp.google.com' }
            [pscustomobject]@{ id = 'ms'; type = 'MX'; name = $zone; content = 'example-org.mail.protection.outlook.com' }
        )
        $foreign = @(Get-ForeignMailRecord -Records $records -ForeignProfile $m -ZoneName $zone)
        $foreign.id | Should -Be @('ms')
    }
}

Describe 'Apex scoping' {
    # Regression, PR #1050 second review. Every provider question was matched on
    # MX content alone, so a subdomain MX could answer for the zone. Inbound mail
    # is decided by the APEX MX; a subdomain's is a different mail stream that
    # this tooling has no mandate over.
    BeforeAll {
        $script:Zone = 'example.org'
        $script:Goog = Get-MailProviderProfile -Provider 'Google' -ZoneName $script:Zone
        $script:M365 = Get-MailProviderProfile -Provider 'Microsoft365' -ZoneName $script:Zone
        # A Google-hosted subdomain alongside a Microsoft apex — the exact shape
        # that used to misreport, falsely pass, and get deleted.
        $script:Mixed = @(
            [pscustomobject]@{ id = 'apex'; type = 'MX'; name = 'example.org'; content = 'example-org.mail.protection.outlook.com' }
            [pscustomobject]@{ id = 'sub'; type = 'MX'; name = 'mail.example.org'; content = 'smtp.google.com' }
        )
    }

    It 'Get-ProviderMxRecord returns only apex records' {
        (Get-ProviderMxRecord -Records $script:Mixed -MailProfile $script:M365 -ZoneName $script:Zone).id |
            Should -Be @('apex')
    }

    It 'ignores a subdomain MX when detecting the provider' {
        Resolve-MailProvider -Records $script:Mixed -ZoneName $script:Zone -Fallback 'Google' |
            Should -Be 'Microsoft365'
    }

    It 'never puts a subdomain MX on the cutover delete list' {
        # The serious one: a Microsoft cutover would have deleted the Google MX
        # of an unrelated subdomain.
        Get-ForeignMailRecord -Records $script:Mixed -ForeignProfile $script:Goog -ZoneName $script:Zone |
            Should -BeNullOrEmpty
    }

    It 'does not let a subdomain MX satisfy the apex presence check' {
        $subOnly = @([pscustomobject]@{ id = 's'; type = 'MX'; name = 'mail.example.org'; content = 'smtp.google.com' })
        Get-ProviderMxRecord -Records $subOnly -MailProfile $script:Goog -ZoneName $script:Zone |
            Should -BeNullOrEmpty
        Resolve-MailProvider -Records $subOnly -ZoneName $script:Zone -Fallback 'Microsoft365' |
            Should -Be 'Microsoft365'
    }

    It 'still selects a genuine apex MX for deletion' {
        # The scoping must not be so tight that the cutover stops working.
        (Get-ForeignMailRecord -Records $script:Mixed -ForeignProfile $script:M365 -ZoneName $script:Zone).id |
            Should -Be @('apex')
    }
}

Describe 'Test-MailProviderChangeConfirmed' {
    # Moving a domain between providers changes where its inbound mail is
    # delivered, so it needs an explicit per-run confirmation that the registry
    # alone cannot supply. Typed rather than boolean on purpose: a -Force flag
    # gets carried across domains by muscle memory; a domain name cannot.
    It 'accepts the exact zone name' {
        Test-MailProviderChangeConfirmed -ZoneName 'slopestohope.org' -Confirmation 'slopestohope.org' |
            Should -BeTrue
    }

    It 'is case- and whitespace-insensitive' {
        Test-MailProviderChangeConfirmed -ZoneName 'slopestohope.org' -Confirmation '  SlopesToHope.ORG ' |
            Should -BeTrue
    }

    It 'rejects blank, which is the default' {
        # The whole safety property rests on this: an operator who does not fill
        # the field in cannot move anything.
        foreach ($v in @('', '   ', $null)) {
            Test-MailProviderChangeConfirmed -ZoneName 'slopestohope.org' -Confirmation $v | Should -BeFalse
        }
    }

    It 'rejects a confirmation for a DIFFERENT domain' {
        # Stops a value pasted from a previous run authorising this one.
        Test-MailProviderChangeConfirmed -ZoneName 'slopestohope.org' -Confirmation 'nochaos.org' |
            Should -BeFalse
    }

    It 'rejects generic affirmatives' {
        foreach ($v in @('yes', 'true', 'y', 'confirm', '*')) {
            Test-MailProviderChangeConfirmed -ZoneName 'slopestohope.org' -Confirmation $v | Should -BeFalse
        }
    }

    It 'rejects a partial or superstring match' {
        foreach ($v in @('slopestohope', 'slopestohope.org.uk', 'xslopestohope.org')) {
            Test-MailProviderChangeConfirmed -ZoneName 'slopestohope.org' -Confirmation $v | Should -BeFalse
        }
    }
}

Describe 'Registry reflects the surveyed fleet' {
    BeforeAll { $script:Reg2 = Get-MailProviderRegistry -Path $script:RegistryPath }

    It 'records the known Google zones' {
        foreach ($d in @('thekccf.org', 'tamkeensports.org', 'bintobetter.org', 'fencingtogether.org')) {
            $script:Reg2.Domains[$d] | Should -Be 'Google' -Because "$d has Google MX live"
        }
    }

    It 'records the known Microsoft zones' {
        foreach ($d in @('freeforcharity.org', 'ffcadmin.org', 'nochaos.org', 'amargraves.org')) {
            $script:Reg2.Domains[$d] | Should -Be 'Microsoft365' -Because "$d has M365 MX live"
        }
    }

    It 'keeps slopestohope.org recorded as the pending Google move' {
        $script:Reg2.Domains['slopestohope.org'] | Should -Be 'Google'
    }
}


Describe 'Resolve-ApexSpfAction — removing a stale foreign include' {
    # THE BUG. The old enforce logic asked only "does the record carry OUR
    # include?" and treated a yes as done. A zone carrying BOTH providers'
    # includes answers yes, so the branch that strips the foreign one — which
    # only ran when ours was ABSENT — could never fire.
    #
    # slopestohope.org was left in exactly that state by its Google cutover:
    #   v=spf1 include:spf.protection.outlook.com include:_spf.google.com ~all
    # and no number of enforcement runs would ever have cleaned it up.

    BeforeAll {
        # Pester 5 runs discovery and execution in separate passes, so helpers
        # defined at Describe scope are gone by the time It bodies run.
        $script:Google = 'include:_spf.google.com'
        $script:Outlook = 'include:spf.protection.outlook.com'
        function New-SpfRecord { param([string]$Content) [pscustomobject]@{ content = $Content } }
    }

    It 'rewrites a record that carries BOTH includes' {
        $rec = New-SpfRecord 'v=spf1 include:spf.protection.outlook.com include:_spf.google.com ~all'
        $r = Resolve-ApexSpfAction -ExistingSpf $rec -DesiredInclude $script:Google `
            -RemoveIncludes @($script:Outlook) -Cutover $true
        $r.Action | Should -Be 'update' -Because 'the stale foreign include must be removable'
        $r.Content | Should -Be 'v=spf1 include:_spf.google.com ~all'
    }

    It 'leaves a record that is already exactly right' {
        # Must not rewrite on every run — that would be endless churn.
        $rec = New-SpfRecord 'v=spf1 include:_spf.google.com ~all'
        $r = Resolve-ApexSpfAction -ExistingSpf $rec -DesiredInclude $script:Google `
            -RemoveIncludes @($script:Outlook) -Cutover $true
        $r.Action | Should -Be 'none'
    }

    It 'still adds a missing include (the original cutover case)' {
        $rec = New-SpfRecord 'v=spf1 include:spf.protection.outlook.com ~all'
        $r = Resolve-ApexSpfAction -ExistingSpf $rec -DesiredInclude $script:Google `
            -RemoveIncludes @($script:Outlook) -Cutover $true
        $r.Action | Should -Be 'update'
        $r.Content | Should -Be 'v=spf1 include:_spf.google.com ~all'
    }

    It 'preserves unrelated mechanisms while stripping the foreign include' {
        # A charity's own sender (Mailgun, an ip4, whatever) must survive.
        $rec = New-SpfRecord 'v=spf1 include:spf.protection.outlook.com include:mailgun.org ip4:203.0.113.9 include:_spf.google.com ~all'
        $r = Resolve-ApexSpfAction -ExistingSpf $rec -DesiredInclude $script:Google `
            -RemoveIncludes @($script:Outlook) -Cutover $true
        $r.Action | Should -Be 'update'
        $r.Content | Should -Be 'v=spf1 include:mailgun.org ip4:203.0.113.9 include:_spf.google.com ~all'
    }

    It 'refuses to rewrite without a cutover mandate' {
        # No authority to edit someone else's SPF — and creating a second
        # v=spf1 is a permerror, so 'none' is the only safe answer.
        $rec = New-SpfRecord 'v=spf1 include:spf.protection.outlook.com include:_spf.google.com ~all'
        $r = Resolve-ApexSpfAction -ExistingSpf $rec -DesiredInclude $script:Google `
            -RemoveIncludes @($script:Outlook) -Cutover $false
        $r.Action | Should -Be 'none'
    }

    It 'asks the caller to create when the zone has no SPF at all' {
        $r = Resolve-ApexSpfAction -ExistingSpf $null -DesiredInclude 'include:_spf.google.com' `
            -RemoveIncludes @('include:spf.protection.outlook.com') -Cutover $true
        $r.Action | Should -Be 'create'
    }

    It 'is idempotent — rewriting its own output is a no-op' {
        $rec = New-SpfRecord 'v=spf1 include:spf.protection.outlook.com include:_spf.google.com ~all'
        $once = (Resolve-ApexSpfAction -ExistingSpf $rec -DesiredInclude $script:Google `
                -RemoveIncludes @($script:Outlook) -Cutover $true).Content
        $again = Resolve-ApexSpfAction -ExistingSpf (New-SpfRecord $once) -DesiredInclude $script:Google `
            -RemoveIncludes @($script:Outlook) -Cutover $true
        $again.Action | Should -Be 'none'
    }

    It 'handles a multi-segment stored SPF record' {
        # Long SPF records come back from Cloudflare as several quoted
        # character-strings; the comparison must run on the LOGICAL value or
        # every run would rewrite the record forever.
        $dq = [char]34
        $stored = "$dq" + 'v=spf1 include:spf.protection.outlook.com inc' + "$dq $dq" + 'lude:_spf.google.com ~all' + "$dq"
        $r = Resolve-ApexSpfAction -ExistingSpf (New-SpfRecord $stored) -DesiredInclude $script:Google `
            -RemoveIncludes @($script:Outlook) -Cutover $true
        $r.Action | Should -Be 'update'
        $r.Content | Should -Be 'v=spf1 include:_spf.google.com ~all'
    }

    It 'removes the foreign include even when it is the ONLY difference' {
        # Distinct from the "both includes" case: here ours is already present
        # and first, so a naive "is our include there?" check passes outright.
        $rec = New-SpfRecord 'v=spf1 include:_spf.google.com include:spf.protection.outlook.com -all'
        $r = Resolve-ApexSpfAction -ExistingSpf $rec -DesiredInclude $script:Google `
            -RemoveIncludes @($script:Outlook) -Cutover $true
        $r.Action | Should -Be 'update'
        $r.Content | Should -Be 'v=spf1 include:_spf.google.com -all'
    }
}

Describe 'Resolve-MultiValueRecordMatch — the A/AAAA proxy flip (#1053)' {
    # The enforce path required content AND proxy flag to match, and set no
    # update candidate when only the flag differed — so it fell through to
    # CREATE a record whose name+type+content already existed. Cloudflare
    # rejects that as a duplicate; $ErrorActionPreference='Stop' turns the
    # resulting Write-Error into a terminating one, and the standards loop
    # ABORTS partway.
    #
    # Ordering is what makes that dangerous. Mail records are applied before
    # the Pages records and the cutover deletes run after the loop, so aborting
    # at the A record leaves BOTH providers' MX in place and an SPF authorising
    # only the new one — worse than either endpoint.

    BeforeAll {
        function New-ARecord {
            param([string]$Content, [bool]$Proxied)
            [pscustomobject]@{ type = 'A'; content = $Content; proxied = $Proxied; id = "id-$Content-$Proxied" }
        }
        $script:Pages = @('185.199.108.153', '185.199.109.153', '185.199.110.153', '185.199.111.153')
    }

    It 'UPDATES rather than creates when only the proxy flag differs' {
        # THE bug. Pre-fix this returned no match and no update candidate, so
        # the caller POSTed a duplicate and the run aborted.
        $existing = @($script:Pages | ForEach-Object { New-ARecord -Content $_ -Proxied $true })
        $m = Resolve-MultiValueRecordMatch -Candidates $existing -DesiredContent '185.199.108.153' -DesiredProxied $false
        $m.Found | Should -BeNullOrEmpty
        $m.UpdateCandidate | Should -Not -BeNullOrEmpty
        $m.UpdateCandidate.content | Should -Be '185.199.108.153'
    }

    It 'reports a match when content and proxy flag both agree' {
        $existing = @(New-ARecord -Content '185.199.108.153' -Proxied $false)
        $m = Resolve-MultiValueRecordMatch -Candidates $existing -DesiredContent '185.199.108.153' -DesiredProxied $false
        $m.Found | Should -Not -BeNullOrEmpty
        $m.UpdateCandidate | Should -BeNullOrEmpty
    }

    It 'never swaps an arbitrary record when the value is genuinely absent' {
        # This is what the original create-only branch was guarding, and the
        # guard has to survive the fix: updating some other A record to the
        # missing IP would silently drop one of the four Pages addresses.
        $existing = @(New-ARecord -Content '203.0.113.1' -Proxied $false)
        $m = Resolve-MultiValueRecordMatch -Candidates $existing -DesiredContent '185.199.108.153' -DesiredProxied $false
        $m.Found | Should -BeNullOrEmpty
        $m.UpdateCandidate | Should -BeNullOrEmpty -Because 'a missing value must be CREATED, not swapped in'
    }

    It 'flips in the other direction too' {
        $existing = @(New-ARecord -Content '185.199.108.153' -Proxied $false)
        $m = Resolve-MultiValueRecordMatch -Candidates $existing -DesiredContent '185.199.108.153' -DesiredProxied $true
        $m.UpdateCandidate | Should -Not -BeNullOrEmpty
    }

    It 'accepts any proxy state when none is required' {
        $existing = @(New-ARecord -Content '185.199.108.153' -Proxied $true)
        $m = Resolve-MultiValueRecordMatch -Candidates $existing -DesiredContent '185.199.108.153' -DesiredProxied $null
        $m.Found | Should -Not -BeNullOrEmpty
        $m.UpdateCandidate | Should -BeNullOrEmpty
    }

    It 'handles an empty zone' {
        $m = Resolve-MultiValueRecordMatch -Candidates @() -DesiredContent '185.199.108.153' -DesiredProxied $false
        $m.Found | Should -BeNullOrEmpty
        $m.UpdateCandidate | Should -BeNullOrEmpty
    }

    It 'resolves the whole slopestohope.org case without a single create' {
        # All four IPs present and proxied, desired DNS-only. Every one must
        # become an UPDATE; a create anywhere is the abort.
        $existing = @($script:Pages | ForEach-Object { New-ARecord -Content $_ -Proxied $true })
        foreach ($ip in $script:Pages) {
            $m = Resolve-MultiValueRecordMatch -Candidates $existing -DesiredContent $ip -DesiredProxied $false
            $m.UpdateCandidate | Should -Not -BeNullOrEmpty -Because "$ip must be updated, not created"
            $m.UpdateCandidate.content | Should -Be $ip -Because 'the update must target the record that already holds this value'
        }
    }
}

Describe 'Resolve-MultiValueRecordMatch — zones with nothing to match' {
    # The caller builds $candidates from an unwrapped pipeline, so it is $null
    # when the zone has no record of this type at this name. That is the
    # PROVISIONING path: a brand new zone, or any zone whose GitHub Pages
    # records do not exist yet.
    #
    # A Mandatory [object[]] parameter rejects that with "Cannot bind argument
    # ... because it is null" and aborts enforcement outright. Every existing
    # test supplied a populated zone, so the whole suite stayed green.

    It 'creates rather than throwing when the zone has no records of this type' {
        # @($null) is what `@($candidates)` produces at the call site when the
        # pipeline matched nothing — a 1-element array holding $null.
        $m = $null
        { $m = Resolve-MultiValueRecordMatch -Candidates @($null) -DesiredContent '185.199.108.153' -DesiredProxied $false } |
            Should -Not -Throw
        $m.Found | Should -BeNullOrEmpty
        $m.UpdateCandidate | Should -BeNullOrEmpty
    }

    It 'accepts a bare $null just as readily' {
        $m = $null
        { $m = Resolve-MultiValueRecordMatch -Candidates $null -DesiredContent '185.199.108.153' -DesiredProxied $false } |
            Should -Not -Throw
        $m.Found | Should -BeNullOrEmpty
    }

    It 'accepts a single record that is not wrapped in an array' {
        # A one-match pipeline yields a scalar, not an array.
        $one = [pscustomobject]@{ type = 'A'; content = '185.199.108.153'; proxied = $true }
        $m = Resolve-MultiValueRecordMatch -Candidates $one -DesiredContent '185.199.108.153' -DesiredProxied $false
        $m.UpdateCandidate | Should -Not -BeNullOrEmpty -Because 'a lone proxied record must still be flipped, not duplicated'
    }

    It 'ignores stray nulls mixed into a populated set' {
        $recs = @($null, [pscustomobject]@{ type = 'A'; content = '185.199.108.153'; proxied = $false }, $null)
        $m = Resolve-MultiValueRecordMatch -Candidates $recs -DesiredContent '185.199.108.153' -DesiredProxied $false
        $m.Found | Should -Not -BeNullOrEmpty
    }
}
