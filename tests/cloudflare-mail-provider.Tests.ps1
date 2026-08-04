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

    foreach ($name in @('Normalize-TxtContent', 'Get-MailProviderProfile', 'Resolve-MailProvider', 'Get-SpfWithInclude')) {
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
        $records = @([pscustomobject]@{ type = 'MX'; content = 'smtp.google.com' })
        Resolve-MailProvider -Records $records -ZoneName 'example.org' -Fallback 'Microsoft365' |
            Should -Be 'Google'
    }

    It 'detects Google from the legacy ASPMX layout' {
        # A zone set up years ago still has ALT1-4. Auditing it as Microsoft
        # would report a working Google tenant as entirely non-compliant.
        $records = @(
            [pscustomobject]@{ type = 'MX'; content = 'aspmx.l.google.com' },
            [pscustomobject]@{ type = 'MX'; content = 'alt1.aspmx.l.google.com' }
        )
        Resolve-MailProvider -Records $records -ZoneName 'example.org' -Fallback 'Microsoft365' |
            Should -Be 'Google'
    }

    It 'detects Microsoft 365 from the tenant mail host' {
        $records = @([pscustomobject]@{ type = 'MX'; content = 'example-org.mail.protection.outlook.com' })
        Resolve-MailProvider -Records $records -ZoneName 'example.org' -Fallback 'Google' |
            Should -Be 'Microsoft365'
    }

    It 'falls back when the zone has no MX at all' {
        $records = @([pscustomobject]@{ type = 'A'; content = '203.0.113.10' })
        Resolve-MailProvider -Records $records -ZoneName 'example.org' -Fallback 'Microsoft365' |
            Should -Be 'Microsoft365'
    }

    It 'falls back when the MX belongs to neither provider' {
        $records = @([pscustomobject]@{ type = 'MX'; content = 'mx1.mailgun.org' })
        Resolve-MailProvider -Records $records -ZoneName 'example.org' -Fallback 'Microsoft365' |
            Should -Be 'Microsoft365'
    }

    It 'accepts an empty record set' {
        Resolve-MailProvider -Records @() -ZoneName 'example.org' -Fallback 'Google' | Should -Be 'Google'
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
