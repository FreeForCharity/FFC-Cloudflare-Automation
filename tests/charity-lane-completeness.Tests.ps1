#requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0.0' }
<#
    Unit tests for the pre-acceptance completeness review
    (scripts/charity-lane-completeness.ps1), shared by 232 (verdicts) and
    226 approve. Pure functions only: every link probe and GitHub lookup is a
    fixture. The field labels are the live pid 33 labels, HTML-encoded the way
    WHMCS returns them.

    Run: Invoke-Pester tests/charity-lane-completeness.Tests.ps1
#>

BeforeAll {
    # The verdict script loads the triage library, which loads the review.
    . (Join-Path $PSScriptRoot '..' 'scripts' 'whmcs-charity-lane-verdicts.ps1')

    $script:Zeffy = 'https://www.zeffy.com/en-US/donation-form/feed-the-valley'
    $script:Idealist = 'https://www.idealist.org/en/nonprofit/0123456789abcdef-riverbend-food-pantry'
    $script:Candid = 'https://www.guidestar.org/profile/12-3456789'
    $script:Current = 'https://riverbend-pantry.my.canva.site/'

    function New-AppFields {
        param([hashtable]$Override = @{}, [string[]]$Drop = @())
        $map = [ordered]@{
            'Organization&#039;s legal name (exactly as on your IRS letter or state filing)'               = 'Riverbend Food Pantry Inc'
            'What is your organization&#039;s mission? (one or two plain sentences)'                       = 'We feed families in Riverbend County every week.'
            'Year founded'                                                                                 = '2015'
            'Public GuideStar Profile Link'                                                                = $script:Candid
            'Charity Facebook Page URL'                                                                    = 'https://www.facebook.com/riverbendpantry'
            'Charity LinkedIn Page URL'                                                                    = 'https://www.linkedin.com/company/riverbend-pantry/'
            'Board President/Chair LinkedIn Link e.g. (https://www.linkedin.com/in/yourname/)'             = 'https://www.linkedin.com/in/ann-chair/'
            'Board Secretary LinkedIn Link e.g. (https://www.linkedin.com/in/yourname/)'                   = 'https://www.linkedin.com/in/ben-secretary/'
            'Board Treasurer LinkedIn Link e.g. (https://www.linkedin.com/in/yourname/)'                   = 'https://www.linkedin.com/in/cara-treasurer/'
            'Board Vice President / Vice Chair LinkedIn Link e.g. (https://www.linkedin.com/in/yourname/)' = 'https://www.linkedin.com/in/ann-chair/'
            'Technical Contact GitHub username (your website maintainer)'                                  = 'riverbend-web'
            'Public phone number for your website footer'                                                  = '(555) 010-2030'
            'Public email address for your website footer'                                                 = 'info@riverbendpantry.org'
            'City &amp; state to show in your website footer'                                              = 'Riverbend, OH'
            'Do you already own this domain?'                                                              = 'No - please register it for us'
            'Current public website or page, if any (even without your own domain)'                        = $script:Current
            'Which starting point fits your charity?'                                                      = 'Single Page'
            'Site tagline (short slogan)'                                                                  = 'No neighbor goes hungry.'
            'Site description (one or two sentences)'                                                      = 'A volunteer food pantry serving Riverbend County.'
            'Short description (under 200 characters)'                                                     = 'Weekly groceries for local families.'
            'Program 1 - title'                                                                            = 'Weekly pantry'
            'Program 1 - description'                                                                      = 'Groceries every Saturday.'
            'Zeffy donation form URL'                                                                      = $script:Zeffy
            'Idealist volunteer page URL'                                                                  = $script:Idealist
            'Board Chair / President - full name to show on your website'                                  = 'Ann Chair'
            'Board Secretary - full name to show on your website'                                          = 'Ben Secretary'
            'Board Treasurer - full name to show on your website'                                          = 'Cara Treasurer'
            'Our Board Chair, Secretary and Treasurer agree to be listed by name and role on our website'  = 'on'
        }
        foreach ($k in $Override.Keys) { $map[$k] = $Override[$k] }
        foreach ($k in $Drop) { $map.Remove($k) }
        $i = 0
        return @(foreach ($k in $map.Keys) { $i++; [pscustomobject]@{ id = "$i"; name = $k; value = [string]$map[$k] } })
    }

    function Get-OkProbes {
        param([string[]]$Urls = @($script:Zeffy, $script:Idealist, $script:Candid, $script:Current))
        $h = @{}
        foreach ($u in $Urls) { $h[$u] = @{ status = 200; mitigated = ''; host = ([Uri]$u).Host } }
        return $h
    }

    function Get-Codes {
        param([hashtable]$Override = @{}, [string[]]$Drop = @(), [int]$ProductPid = 33, [hashtable]$Probes, [hashtable]$Github = @{ 'riverbend-web' = $true })
        if (-not $Probes) { $Probes = Get-OkProbes }
        return @(Get-LaneCompletenessCodes -Fields (New-AppFields -Override $Override -Drop $Drop) -ProductPid $ProductPid -LinkProbes $Probes -GithubLookup $Github)
    }
}

Describe 'A complete application' {
    It 'passes with no codes when every link opens and the GitHub account exists' {
        Get-Codes | Should -BeNullOrEmpty
    }

    It 'names the links to open and the account to confirm' {
        $t = Get-LaneCompletenessTargets -Fields (New-AppFields) -ProductPid 33
        @($t.Links.Values) | Should -Contain $script:Zeffy
        @($t.Links.Values) | Should -Contain $script:Idealist
        @($t.Links.Values) | Should -Contain $script:Candid
        @($t.Links.Values) | Should -Contain $script:Current
        $t.GithubUser | Should -Be 'riverbend-web'
    }
}

Describe 'WHMCS link-wrapped answers (orders 883 / 886, 2026-11-20)' {
    # GetClientsProducts returns a "link" custom field as
    # <a href="URL" target="_blank">URL</a>, and a blank one as an empty
    # anchor. Every pid 33 application read OFFICER_LINKEDIN_NOT_PROFILE and
    # every one with no current website read CURRENT_SITE_URL_INVALID.
    BeforeAll {
        function Wrap { param([string]$Url) return "<a href=`"$Url`" target=`"_blank`">$Url</a>" }
        $script:ChairLabel = 'Board President/Chair LinkedIn Link e.g. (https://www.linkedin.com/in/yourname/)'
        $script:SecLabel = 'Board Secretary LinkedIn Link e.g. (https://www.linkedin.com/in/yourname/)'
        $script:TreasLabel = 'Board Treasurer LinkedIn Link e.g. (https://www.linkedin.com/in/yourname/)'
        $script:SiteLabel = 'Current public website or page, if any (even without your own domain)'
    }

    It 'three wrapped personal officer profiles pass' {
        Get-Codes -Override @{
            $script:ChairLabel = (Wrap 'https://www.linkedin.com/in/ann-chair/')
            $script:SecLabel   = (Wrap 'https://www.linkedin.com/in/ben-secretary/')
            $script:TreasLabel = (Wrap 'https://www.linkedin.com/in/cara-treasurer/')
        } | Should -BeNullOrEmpty
    }

    It 'a wrapped company page in an officer slot is still not a profile' {
        Get-Codes -Override @{ $script:ChairLabel = (Wrap 'https://www.linkedin.com/company/riverbend-pantry/') } |
            Should -Contain 'OFFICER_LINKEDIN_NOT_PROFILE'
    }

    It 'wrapped duplicate officer profiles are still not distinct' {
        Get-Codes -Override @{
            $script:ChairLabel = (Wrap 'https://www.linkedin.com/in/ann-chair/')
            $script:SecLabel   = (Wrap 'https://www.linkedin.com/in/ann-chair/')
        } | Should -Contain 'OFFICER_LINKEDIN_NOT_DISTINCT'
    }

    It 'a wrapped blank officer link reads as missing' {
        Get-Codes -Override @{ $script:TreasLabel = '<a href="" target="_blank"></a>' } |
            Should -Contain 'OFFICER_LINKEDIN_MISSING'
    }

    It 'a wrapped blank current website is no answer, not an invalid one' {
        $codes = Get-Codes -Override @{ $script:SiteLabel = '<a href="" target="_blank"></a>' } -Probes (Get-OkProbes -Urls @($script:Zeffy, $script:Idealist, $script:Candid))
        $codes | Should -Not -Contain 'CURRENT_SITE_URL_INVALID'
        $codes | Should -BeNullOrEmpty
    }

    It 'a wrapped current website that is not a link is still invalid' {
        Get-Codes -Override @{ $script:SiteLabel = '<a href="not a link" target="_blank">not a link</a>' } |
            Should -Contain 'CURRENT_SITE_URL_INVALID'
    }

    It 'a wrapped blank REQUIRED link is still flagged (as missing)' {
        Get-Codes -Override @{ 'Public GuideStar Profile Link' = '<a href="" target="_blank"></a>' } -Probes (Get-OkProbes -Urls @($script:Zeffy, $script:Idealist, $script:Current)) |
            Should -Contain 'CANDID_URL_MISSING'
    }
}

Describe 'Links that are not public (orders 875 / 876, 2026-11-12)' {
    It '<Name> -> <Code>' -ForEach @(
        @{ Name = 'Zeffy campaigns admin page'; Label = 'Zeffy donation form URL'; Value = 'https://www.zeffy.com/en-US/o/fundraising/campaigns?fromOnboarding=true'; Code = 'DONATION_URL_NOT_PUBLIC' }
        @{ Name = 'Idealist home page'; Label = 'Idealist volunteer page URL'; Value = 'https://www.idealist.org/en'; Code = 'VOLUNTEER_URL_NOT_PUBLIC' }
        @{ Name = 'Idealist admin page'; Label = 'Idealist volunteer page URL'; Value = 'https://www.idealist.org/admin/8a8cb80d/volops/b732af9b'; Code = 'VOLUNTEER_URL_NOT_PUBLIC' }
        @{ Name = 'Candid app login page'; Label = 'Public GuideStar Profile Link'; Value = 'https://app.candid.org/user/profile'; Code = 'CANDID_URL_NOT_PUBLIC' }
        @{ Name = 'LinkedIn page admin dashboard'; Label = 'Charity LinkedIn Page URL'; Value = 'https://www.linkedin.com/company/143949611/admin/dashboard/'; Code = 'LINKEDIN_PAGE_NOT_PUBLIC' }
    ) {
        Get-Codes -Override @{ $Label = $Value } | Should -Contain $Code
    }

    It 'a blank required link is MISSING, not NOT_PUBLIC' {
        $c = Get-Codes -Drop @('Zeffy donation form URL', 'Idealist volunteer page URL', 'Public GuideStar Profile Link')
        $c | Should -Contain 'DONATION_URL_MISSING'
        $c | Should -Contain 'VOLUNTEER_URL_MISSING'
        $c | Should -Contain 'CANDID_URL_MISSING'
        $c | Should -Not -Contain 'DONATION_URL_NOT_PUBLIC'
    }

    It 'pid 16: Zeffy and Idealist are optional, but a bad one is still flagged' {
        Get-Codes -ProductPid 16 -Drop @('Zeffy donation form URL', 'Idealist volunteer page URL', 'Public GuideStar Profile Link') | Should -BeNullOrEmpty
        Get-Codes -ProductPid 16 -Override @{ 'Idealist volunteer page URL' = 'https://www.idealist.org/en' } | Should -Contain 'VOLUNTEER_URL_NOT_PUBLIC'
    }
}

Describe 'Links open for the public' {
    It '404 on <Key> -> <Code>' -ForEach @(
        @{ Key = 'donation'; Code = 'DONATION_URL_NOT_FOUND' }
        @{ Key = 'volunteer'; Code = 'VOLUNTEER_URL_NOT_FOUND' }
        @{ Key = 'candid'; Code = 'CANDID_URL_NOT_FOUND' }
        @{ Key = 'current'; Code = 'CURRENT_SITE_UNREACHABLE' }
    ) {
        $url = @{ donation = $script:Zeffy; volunteer = $script:Idealist; candid = $script:Candid; current = $script:Current }[$Key]
        $p = Get-OkProbes
        $p[$url] = @{ status = 404; mitigated = ''; host = ([Uri]$url).Host }
        Get-Codes -Probes $p | Should -Contain $Code
    }

    It 'a timeout or 5xx holds the order for a human (ESCALATE)' {
        $p = Get-OkProbes
        $p[$script:Idealist] = @{ status = $null; mitigated = ''; host = 'www.idealist.org' }
        Get-Codes -Probes $p | Should -Contain 'LINK_UNVERIFIED'
        $p[$script:Idealist] = @{ status = 503; mitigated = ''; host = 'www.idealist.org' }
        Get-Codes -Probes $p | Should -Contain 'LINK_UNVERIFIED'
        $script:LaneCompletenessCodes['LINK_UNVERIFIED'].severity | Should -Be 'ESCALATE'
    }

    It 'a link that was never probed is unverified, never a pass' {
        Get-Codes -Probes @{ 'https://unrelated.example/' = @{ status = 200 } } | Should -Contain 'LINK_UNVERIFIED'
    }

    It "Zeffy's Cloudflare managed challenge is INFO, and does not block" {
        $p = Get-OkProbes
        $p[$script:Zeffy] = @{ status = 403; mitigated = 'challenge'; host = 'www.zeffy.com' }
        $c = Get-Codes -Probes $p
        $c | Should -Be @('LINK_BOT_WALL')
        Get-LaneCompletenessBlocking -Codes $c | Should -BeNullOrEmpty
    }

    It 'a 403 challenge from any other host is not excused' {
        $p = Get-OkProbes
        $p[$script:Current] = @{ status = 403; mitigated = 'challenge'; host = 'riverbend-pantry.my.canva.site' }
        Get-Codes -Probes $p | Should -Contain 'LINK_UNVERIFIED'
    }
}

Describe 'GitHub maintainer account' {
    It 'missing username' { Get-Codes -Drop @('Technical Contact GitHub username (your website maintainer)') | Should -Contain 'GITHUB_USER_MISSING' }
    It 'not a valid username' { Get-Codes -Override @{ 'Technical Contact GitHub username (your website maintainer)' = 'not a user!' } | Should -Contain 'GITHUB_USER_INVALID' }
    It 'account does not exist (projectherinc, 2026-11-13)' { Get-Codes -Github @{ 'riverbend-web' = $false } | Should -Contain 'GITHUB_USER_NOT_FOUND' }
    It 'lookup failed -> ESCALATE, never a pass' {
        Get-Codes -Github @{ 'riverbend-web' = $null } | Should -Contain 'GITHUB_USER_UNVERIFIED'
        Get-Codes -Github @{} | Should -Contain 'GITHUB_USER_UNVERIFIED'
    }
    It 'matches the username case-insensitively' { Get-Codes -Github @{ 'Riverbend-Web' = $true } | Should -BeNullOrEmpty }
}

Describe 'Three different officers, each with their own LinkedIn' {
    It 'one person in all three slots (876) -> OFFICERS_NOT_DISTINCT' {
        $c = Get-Codes -Override @{
            'Board Chair / President - full name to show on your website' = 'Garyel Tubbs - Executive Director'
            'Board Secretary - full name to show on your website'         = 'Garyel Tubbs'
            'Board Treasurer - full name to show on your website'         = 'garyel  tubbs'
        }
        $c | Should -Contain 'OFFICERS_NOT_DISTINCT'
    }

    It 'one LinkedIn profile for all three (Eastridge PTO) -> OFFICER_LINKEDIN_NOT_DISTINCT' {
        $same = 'https://www.linkedin.com/in/nathan-d/'
        $c = Get-Codes -Override @{
            'Board President/Chair LinkedIn Link e.g. (https://www.linkedin.com/in/yourname/)' = $same
            'Board Secretary LinkedIn Link e.g. (https://www.linkedin.com/in/yourname/)'       = $same
            'Board Treasurer LinkedIn Link e.g. (https://www.linkedin.com/in/yourname/)'       = 'https://linkedin.com/in/Nathan-D'
        }
        $c | Should -Contain 'OFFICER_LINKEDIN_NOT_DISTINCT'
    }

    It 'the Vice Chair row is not read as the Chair' {
        Get-Codes | Should -Not -Contain 'OFFICER_LINKEDIN_NOT_DISTINCT'
    }

    It 'a company page in an officer slot -> OFFICER_LINKEDIN_NOT_PROFILE' {
        Get-Codes -Override @{ 'Board Secretary LinkedIn Link e.g. (https://www.linkedin.com/in/yourname/)' = 'https://www.linkedin.com/company/riverbend-pantry/' } |
            Should -Contain 'OFFICER_LINKEDIN_NOT_PROFILE'
    }

    It 'a blank officer LinkedIn -> OFFICER_LINKEDIN_MISSING' {
        Get-Codes -Drop @('Board Treasurer LinkedIn Link e.g. (https://www.linkedin.com/in/yourname/)') | Should -Contain 'OFFICER_LINKEDIN_MISSING'
    }

    It 'missing officer names and unticked consent' {
        $c = Get-Codes -Drop @('Board Treasurer - full name to show on your website', 'Our Board Chair, Secretary and Treasurer agree to be listed by name and role on our website')
        $c | Should -Contain 'OFFICER_NAMES_MISSING'
        $c | Should -Contain 'OFFICERS_CONSENT_MISSING'
    }
}

Describe 'Required website fields' {
    It 'blank <Label> -> <Code>' -ForEach @(
        @{ Label = 'Site tagline (short slogan)'; Code = 'TAGLINE_MISSING' }
        @{ Label = 'Site description (one or two sentences)'; Code = 'SITE_DESCRIPTION_MISSING' }
        @{ Label = 'Short description (under 200 characters)'; Code = 'SHORT_DESCRIPTION_MISSING' }
        @{ Label = 'Program 1 - title'; Code = 'PROGRAM_1_MISSING' }
        @{ Label = 'Year founded'; Code = 'FOUNDING_YEAR_MISSING' }
        @{ Label = 'Which starting point fits your charity?'; Code = 'TEMPLATE_CHOICE_MISSING' }
        @{ Label = 'Public email address for your website footer'; Code = 'PUBLIC_EMAIL_MISSING' }
        @{ Label = 'Charity Facebook Page URL'; Code = 'FACEBOOK_URL_MISSING' }
    ) {
        Get-Codes -Drop @($Label) | Should -Contain $Code
    }

    It 'optional fields can stay blank' {
        Get-Codes -Drop @('Current public website or page, if any (even without your own domain)') | Should -BeNullOrEmpty
    }
}

Describe 'Catalog, blocking and the ticket text' {
    It 'every code the review can return is in the catalog with a severity' {
        foreach ($k in $script:LaneCompletenessCodes.Keys) {
            @('INFO', 'NEED_INFO', 'ESCALATE') | Should -Contain $script:LaneCompletenessCodes[$k].severity
        }
        foreach ($r in $script:LaneRequiredSiteFields) {
            $script:LaneCompletenessCodes.Contains($r.missing) | Should -BeTrue
            $script:LaneCompletenessCodes.Contains($r.invalid) | Should -BeTrue
        }
    }

    It 'every NEED_INFO code has a plain-English line for the ticket' {
        foreach ($k in $script:LaneCompletenessCodes.Keys) {
            if ($script:LaneCompletenessCodes[$k].severity -eq 'NEED_INFO') { [string]$script:LaneCompletenessCodes[$k].applicant | Should -Not -BeNullOrEmpty }
        }
    }

    It 'an unknown code blocks (fail closed)' {
        Get-LaneCompletenessBlocking -Codes @('SOMETHING_NEW') | Should -Be @('SOMETHING_NEW')
    }

    It 'the fix list carries no applicant value' {
        $codes = Get-Codes -Override @{ 'Zeffy donation form URL' = 'https://www.zeffy.com/en-US/o/fundraising/campaigns' } -Github @{ 'riverbend-web' = $false }
        $text = (Get-LaneApplicantFixList -Codes $codes) -join "`n"
        $text | Should -Match 'Zeffy'
        $text | Should -Not -Match 'riverbend'
        $text | Should -Not -Match 'campaigns'
    }

    It 'the fix list joins as text when captured the way 226 captures it' {
        # 226 approve once wrapped this call in @(), which kept the comma-returned
        # string[] as ONE element and printed "System.String[]" in the gap warning.
        [string[]]$fix = Get-LaneApplicantFixList -Codes @('TAGLINE_MISSING', 'MISSION_MISSING')
        $fix.Count | Should -Be 2
        ($fix -join ' ') | Should -Not -Match 'System\.String'
        ($fix -join ' ') | Should -Match 'tagline'
    }

    It 'the verdict catalog carries every review code' {
        foreach ($k in $script:LaneCompletenessCodes.Keys) { $script:LaneReasonCodes.Contains($k) | Should -BeTrue }
    }
}

Describe 'Officer name and LinkedIn keys' {
    It '<In> -> <Out>' -ForEach @(
        @{ In = 'Garyel Tubbs - Executive Director'; Out = 'garyel tubbs' }
        @{ In = 'Dr. Jane Q. Doe, Chair'; Out = 'jane q doe' }
        @{ In = '  Ann   Chair  '; Out = 'ann chair' }
        @{ In = 'Mary-Jane Smith (Treasurer)'; Out = 'mary jane smith' }
    ) { ConvertTo-LaneOfficerKey -Name $In | Should -Be $Out }

    It 'profile slugs compare case-insensitively and ignore the trailing slash' {
        ConvertTo-LaneLinkedInProfileKey -Url 'https://www.linkedin.com/in/Nathan-D/' | Should -Be 'in/nathan-d'
        ConvertTo-LaneLinkedInProfileKey -Url 'https://linkedin.com/in/nathan-d?trk=x' | Should -Be 'in/nathan-d'
        ConvertTo-LaneLinkedInProfileKey -Url 'https://www.linkedin.com/company/x/' | Should -Be ''
    }
}

Describe 'Get-LaneAdjudicatedOrders' {
    BeforeAll {
        $script:AdjDir = Join-Path ([System.IO.Path]::GetTempPath()) ("adj-" + [guid]::NewGuid())
        New-Item -ItemType Directory -Path $script:AdjDir | Out-Null
        function Write-Adj([string]$Json) {
            $p = Join-Path $script:AdjDir ([guid]::NewGuid().ToString() + '.json')
            Set-Content -LiteralPath $p -Value $Json -Encoding utf8
            return $p
        }
    }

    It 'ships an adjudicated-orders file that loads' {
        $p = Join-Path $PSScriptRoot '..' 'config' 'charity-lane-adjudicated-orders.json'
        { Get-LaneAdjudicatedOrders -Path $p } | Should -Not -Throw
    }

    It 'a missing file means no overrides' {
        (Get-LaneAdjudicatedOrders -Path (Join-Path $script:AdjDir 'none.json')).Count | Should -Be 0
    }

    It 'keys a valid entry by its order id' {
        $p = Write-Adj '{"orders":[{"orderid":"753","adjudicatedOn":"2026-11-13","reason":"older form","decision":"Clarke, project thread"}]}'
        $m = Get-LaneAdjudicatedOrders -Path $p
        $m.ContainsKey('753') | Should -BeTrue
        $m.ContainsKey('754') | Should -BeFalse
    }

    It 'refuses <Name>' -ForEach @(
        @{ Name = 'a file with no orders list'; Json = '{"x":[]}' }
        @{ Name = 'a non-numeric order id'; Json = '{"orders":[{"orderid":"75a","adjudicatedOn":"2026-11-13","reason":"r","decision":"d"}]}' }
        @{ Name = 'a missing decision'; Json = '{"orders":[{"orderid":"753","adjudicatedOn":"2026-11-13","reason":"r"}]}' }
        @{ Name = 'a bad date'; Json = '{"orders":[{"orderid":"753","adjudicatedOn":"Nov 13","reason":"r","decision":"d"}]}' }
        @{ Name = 'a duplicate order'; Json = '{"orders":[{"orderid":"753","adjudicatedOn":"2026-11-13","reason":"r","decision":"d"},{"orderid":"753","adjudicatedOn":"2026-11-13","reason":"r","decision":"d"}]}' }
        @{ Name = 'an email address'; Json = '{"orders":[{"orderid":"753","adjudicatedOn":"2026-11-13","reason":"r","decision":"from jo@example.org"}]}' }
        @{ Name = 'a phone number'; Json = '{"orders":[{"orderid":"753","adjudicatedOn":"2026-11-13","reason":"call 555-123-4567","decision":"d"}]}' }
    ) {
        $p = Write-Adj $Json
        { Get-LaneAdjudicatedOrders -Path $p } | Should -Throw
    }
}

Describe 'Candid profile links on app.candid.org (orders 883 / 886, 2026-11-21)' {
    BeforeAll {
        $script:AppCandid = 'https://app.candid.org/profile/15935725/restorative-expressive-arts-corp-99-1579845'
        $script:AppCandidShared = 'https://app.candid.org/profile/16766185/ritual-azteca-inc/?pkId=e0adf022-4e46-48f4-baa3-8488ece7c3f4&isActive=true'
        $script:AppCandidSharedEin = 'https://app.candid.org/profile/15935725/restorative-expressive-arts-corp-99-1579845/?pkId=e115d60f-1499-4a15-9aea-56fe7d8b413d'
    }

    It 'an app.candid.org profile link without its shareable pkId is NOT_PUBLIC (it opens only signed in)' {
        $p = Get-OkProbes -Urls @($script:Zeffy, $script:Idealist, $script:AppCandid, $script:Current)
        Get-Codes -Override @{ 'Public GuideStar Profile Link' = $script:AppCandid } -Probes $p | Should -Contain 'CANDID_URL_NOT_PUBLIC'
    }

    It 'a shared app.candid.org profile link with its pkId is accepted when it opens' {
        $p = Get-OkProbes -Urls @($script:Zeffy, $script:Idealist, $script:AppCandidShared, $script:Current)
        Get-Codes -Override @{ 'Public GuideStar Profile Link' = $script:AppCandidShared } -Probes $p | Should -BeNullOrEmpty
    }

    It 'the EIN in an app.candid.org slug gives the GuideStar profile-by-EIN page' {
        Get-LaneCandidEinProfileUrl -CandidUrl $script:AppCandid -Ein '' | Should -Be 'https://www.guidestar.org/profile/99-1579845'
        Get-LaneCandidEinProfileUrl -CandidUrl 'https://www.guidestar.org/profile/991579845' -Ein '' | Should -Be 'https://www.guidestar.org/profile/99-1579845'
    }

    It "without an EIN in the link, the application's EIN is used" {
        Get-LaneCandidEinProfileUrl -CandidUrl $script:AppCandidShared -Ein '12-3456789' | Should -Be 'https://www.guidestar.org/profile/12-3456789'
        Get-LaneCandidEinProfileUrl -CandidUrl $script:AppCandidShared -Ein '' | Should -Be ''
    }

    It "an EIN in the link that disagrees with the application's EIN gets no fallback (#1793 review)" {
        Get-LaneCandidEinProfileUrl -CandidUrl $script:AppCandid -Ein '12-3456789' | Should -Be ''
        Get-LaneCandidEinProfileUrl -CandidUrl $script:AppCandid -Ein '991579845' | Should -Be 'https://www.guidestar.org/profile/99-1579845'
    }

    It 'a bare 9-digit Candid id is never read as an EIN (#1793 review)' {
        Get-LaneCandidEinProfileUrl -CandidUrl 'https://app.candid.org/profile/123456789/?pkId=a' -Ein '' | Should -Be ''
    }

    It 'a profile link that will not answer a signed-out GET passes when the GuideStar EIN page opens' {
        $p = Get-OkProbes -Urls @($script:Zeffy, $script:Idealist, $script:Current, 'https://www.guidestar.org/profile/99-1579845')
        $p[$script:AppCandidSharedEin] = @{ status = 403; mitigated = ''; host = 'app.candid.org' }
        Get-Codes -Override @{ 'Public GuideStar Profile Link' = $script:AppCandidSharedEin } -Probes $p | Should -BeNullOrEmpty
    }

    It 'the runner is asked to open the GuideStar EIN page too' {
        $t = Get-LaneCompletenessTargets -Fields (New-AppFields -Override @{ 'Public GuideStar Profile Link' = $script:AppCandidSharedEin }) -ProductPid 33
        @($t.Links.Values) | Should -Contain 'https://www.guidestar.org/profile/99-1579845'
    }

    It 'still unverified when neither the link nor the GuideStar EIN page opens' {
        $p = Get-OkProbes -Urls @($script:Zeffy, $script:Idealist, $script:Current)
        $p[$script:AppCandidSharedEin] = @{ status = 403; mitigated = ''; host = 'app.candid.org' }
        $p['https://www.guidestar.org/profile/99-1579845'] = @{ status = $null; mitigated = ''; host = 'www.guidestar.org' }
        Get-Codes -Override @{ 'Public GuideStar Profile Link' = $script:AppCandidSharedEin } -Probes $p | Should -Contain 'LINK_UNVERIFIED'
    }

    It 'a 404 on the given profile link stays NOT_FOUND even if the EIN page opens' {
        $p = Get-OkProbes -Urls @($script:Zeffy, $script:Idealist, $script:Current, 'https://www.guidestar.org/profile/99-1579845')
        $p[$script:AppCandidSharedEin] = @{ status = 404; mitigated = ''; host = 'app.candid.org' }
        Get-Codes -Override @{ 'Public GuideStar Profile Link' = $script:AppCandidSharedEin } -Probes $p | Should -Contain 'CANDID_URL_NOT_FOUND'
    }

    It 'with both answers given, the shareable link is chosen over the signed-in-only one (order 883)' {
        $fields = New-AppFields -Override @{
            'Public GuideStar Profile Link'                              = $script:AppCandid
            'Candid / GuideStar direct (shared) profile link (optional)' = $script:AppCandidSharedEin
        }
        $t = Get-LaneCompletenessTargets -Fields $fields -ProductPid 33
        $t.Links['candidProfileUrl'] | Should -Be $script:AppCandidSharedEin
    }
}

Describe 'Candid seal widget answers (workflow 803, measured 2026-11-21)' {
    BeforeAll {
        $script:SealShared = 'https://app.candid.org/profile/16766185/ritual-azteca-inc/?pkId=e0adf022-4e46-48f4-baa3-8488ece7c3f4&isActive=true'
        # Shapes copied from what Candid returned to a GitHub runner.
        $script:GoldSvg = @'
<?xml version="1.0" encoding="UTF-8"?>
<svg id="Layer_1" data-name="Layer 1" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 108 108" height="108" width="108">
  <defs>
    <style>
      .cls-1 {
        fill: #d3aa41;
      }
      .cls-3 {
        fill: #fff;
      }
    </style>
  </defs>
    <rect class="cls-1" x="0" width="108" height="108"/>
    <rect class="cls-3" x="9.45" y="9.45" width="89.1" height="89.1"/>
</svg>
'@
        $script:EmptySvg = @'
<?xml version="1.0"?>
    <svg xmlns="http://www.w3.org/2000/svg" width="200" height="200">
    <path fill="none" stroke="#fff" stroke-width="2" d="M1,1V199H199V1z"/>
</svg>
'@
        function New-Fetch { param($Status, $Type, $Body) [pscustomobject]@{ status = $Status; contentType = $Type; body = $Body } }
    }

    It 'a coloured 108x108 seal is ok, and names its level' {
        $v = Get-CandidSealVerdict -Fetch (New-Fetch 200 'image/svg+xml' $script:GoldSvg)
        $v.verdict | Should -Be 'ok'
        $v.level | Should -Be 'Gold'
        (Get-CandidSealVerdict -Fetch (New-Fetch 200 'image/svg+xml' ($script:GoldSvg -replace '#d3aa41', '#a6c0ce'))).level | Should -Be 'Platinum'
    }

    It 'the 200 empty white outline Candid serves for an org with no seal is no_seal, not ok' {
        (Get-CandidSealVerdict -Fetch (New-Fetch 200 'image/svg+xml' $script:EmptySvg)).verdict | Should -Be 'no_seal'
        (Get-CandidSealVerdict -Fetch (New-Fetch 200 'image/svg+xml' ($script:GoldSvg -replace '#d3aa41', '#fff'))).verdict | Should -Be 'no_seal'
    }

    It 'a 404 is no_org, a non-SVG is not_svg, a transport error is unverified' {
        (Get-CandidSealVerdict -Fetch (New-Fetch 404 'application/json' '{"Message":"Organization 999999999 not found"}')).verdict | Should -Be 'no_org'
        (Get-CandidSealVerdict -Fetch (New-Fetch 200 'text/html' '<html></html>')).verdict | Should -Be 'not_svg'
        (Get-CandidSealVerdict -Fetch (New-Fetch $null '' '')).verdict | Should -Be 'unverified'
        (Get-CandidSealVerdict -Fetch (New-Fetch 503 'text/html' '')).verdict | Should -Be 'unverified'
    }

    It 'the review flags a Candid link whose profile id Candid does not know as NOT_FOUND' {
        $p = Get-OkProbes -Urls @($script:Zeffy, $script:Idealist, $script:SealShared, $script:Current)
        $codes = @(Get-LaneCompletenessCodes -Fields (New-AppFields -Override @{ 'Public GuideStar Profile Link' = $script:SealShared }) -ProductPid 33 -LinkProbes $p -GithubLookup @{ 'riverbend-web' = $true } -CandidSeals @{ '16766185' = 'no_org' })
        $codes | Should -Contain 'CANDID_URL_NOT_FOUND'
    }

    It 'no seal yet is INFO only and does not block acceptance' {
        $p = Get-OkProbes -Urls @($script:Zeffy, $script:Idealist, $script:SealShared, $script:Current)
        $codes = @(Get-LaneCompletenessCodes -Fields (New-AppFields -Override @{ 'Public GuideStar Profile Link' = $script:SealShared }) -ProductPid 33 -LinkProbes $p -GithubLookup @{ 'riverbend-web' = $true } -CandidSeals @{ '16766185' = 'no_seal' })
        $codes | Should -Contain 'CANDID_SEAL_NONE'
        @(Get-LaneCompletenessBlocking -Codes $codes) | Should -Not -Contain 'CANDID_SEAL_NONE'
    }

    It 'a visible seal adds nothing' {
        $p = Get-OkProbes -Urls @($script:Zeffy, $script:Idealist, $script:SealShared, $script:Current)
        $codes = @(Get-LaneCompletenessCodes -Fields (New-AppFields -Override @{ 'Public GuideStar Profile Link' = $script:SealShared }) -ProductPid 33 -LinkProbes $p -GithubLookup @{ 'riverbend-web' = $true } -CandidSeals @{ '16766185' = 'ok' })
        $codes | Should -Not -Contain 'CANDID_SEAL_NONE'
        $codes | Should -Not -Contain 'CANDID_URL_NOT_FOUND'
    }

    It 'a rendered shareable profile is the full page; the plain link is only a summary signed out' {
        $full = 'Skip to content Donate to Candid Log in/Register Ritual Azteca Inc Documents Info Summary Programs Impact People Forms 990 EIN 42-4285263 IRS subsection 501(c)(3) Public Charity Mission ...'
        $stub = 'Candid search Ritual Azteca Inc Location: Los Angeles, CA EIN: 42-4285263 Tax status: 501(c)(3) Public Charity Suggest an edit Do you manage Ritual Azteca Inc? Claim it here. Summary Forms 990 Programs'
        Get-CandidProfilePageVerdict -Text $full | Should -Be 'full'
        Get-CandidProfilePageVerdict -Text $stub | Should -Be 'summary_only'
        Get-CandidProfilePageVerdict -Text '' | Should -Be 'unverified'
        # The full page can carry the summary page's wording further down.
        Get-CandidProfilePageVerdict -Text ($full + ' People CEO ... Suggest an edit') | Should -Be 'full'
    }

    It 'a footer badge works only with a visible seal, a rendered image and a full signed-out page' {
        (Get-CandidBadgeVerdict -SealVerdict 'ok' -FooterWidth 108 -PageVerdict 'full').ok | Should -BeTrue
        $v = Get-CandidBadgeVerdict -SealVerdict 'no_seal' -FooterWidth 200 -PageVerdict 'full'
        $v.ok | Should -BeFalse
        $v.why | Should -Match 'seal no_seal'
        (Get-CandidBadgeVerdict -SealVerdict 'ok' -FooterWidth 0 -PageVerdict 'full').why | Should -Match 'did not render'
        (Get-CandidBadgeVerdict -SealVerdict 'ok' -FooterWidth 108 -PageVerdict 'summary_only').why | Should -Match 'summary_only'
    }
}
