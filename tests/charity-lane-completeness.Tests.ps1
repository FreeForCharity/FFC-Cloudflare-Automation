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
