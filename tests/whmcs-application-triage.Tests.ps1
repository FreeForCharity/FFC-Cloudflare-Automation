#requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0.0' }
<#
    Unit tests for the PURE scoring/ranking functions of
    scripts/whmcs-application-triage.ps1. The script is dot-sourced (its runner
    body is guarded by `if ($MyInvocation.InvocationName -eq '.') { return }`),
    so NO WHMCS API call is made here -- fixtures stand in for the live data.

    Fixtures: a complete application, a sparse application, and a
    placeholder-name (person-in-org-field) application, for both pid 16 and
    pid 33. Run: Invoke-Pester tests/whmcs-application-triage.Tests.ps1
#>

BeforeAll {
    . (Join-Path $PSScriptRoot '..' 'scripts' 'whmcs-application-triage.ps1')

    function New-Fields {
        param([hashtable]$Map)
        $i = 0
        $out = @()
        foreach ($k in $Map.Keys) {
            $i++
            $out += [pscustomobject]@{ id = "$i"; name = $k; value = [string]$Map[$k] }
        }
        return $out
    }

    $script:GoodMission = 'We provide free after-school tutoring, nutritious meals, and caring mentorship to underserved children across three counties so that every child can reach their full potential regardless of their family income or background.'

    # --- pid 16 fixtures ---
    $script:Complete16 = [pscustomobject]@{
        orderid = '1001'; ordernum = 'ORD1001'; clientid = '501'; pid = 16
        productName = 'FFC Pre-501c3 Application'; charityName = 'Bright Future Foundation'
        fields = (New-Fields @{
                'Organization Name'          = 'Bright Future Foundation'
                'EIN'                        = '12-3456789'
                'GuideStar / Candid Profile' = 'https://www.guidestar.org/profile/12-3456789'
                'Facebook Page'              = 'https://www.facebook.com/brightfuturefdn'
                'LinkedIn Organization Page' = 'https://www.linkedin.com/company/bright-future-foundation'
                'AI tools you use'           = 'ChatGPT, Canva'
                'AI plan'                    = 'Use AI to draft grant applications and social posts.'
                'US-based attestation'       = 'Yes'
                'Terms of Service agreement' = 'I agree'
                'Mission'                    = $script:GoodMission
                'Do you need hosting'        = 'Yes, we need hosting'
                'Requested domain'           = 'brightfuture.org'
                'Primary contact email'      = 'info@brightfuture.org'
            })
    }

    $script:Sparse16 = [pscustomobject]@{
        orderid = '1002'; ordernum = 'ORD1002'; clientid = '502'; pid = 16
        productName = 'FFC Pre-501c3 Application'; charityName = 'Helping Hands Society'
        fields = (New-Fields @{
                'Organization Name'          = 'Helping Hands Society'
                'EIN'                        = ''
                'GuideStar / Candid Profile' = 'n/a'
                'Facebook Page'              = ''
                'LinkedIn Organization Page' = ''
                'US-based attestation'       = ''
                'Terms of Service agreement' = ''
                'Mission'                    = 'Help people.'
                'Do you need hosting'        = ''
            })
    }

    $script:Placeholder16 = [pscustomobject]@{
        orderid = '1003'; ordernum = 'ORD1003'; clientid = '503'; pid = 16
        productName = 'FFC Pre-501c3 Application'; charityName = 'JOHN SMITH'
        fields = (New-Fields @{
                'Organization Name'          = 'JOHN SMITH'
                'EIN'                        = '12-3456789'
                'GuideStar / Candid Profile' = 'https://www.guidestar.org/profile/12-3456789'
                'Facebook Page'              = 'https://www.facebook.com/johnsmith'
                'LinkedIn Organization Page' = 'https://www.linkedin.com/company/john-smith'
                'US-based attestation'       = 'Yes'
                'Terms of Service agreement' = 'I agree'
                'Mission'                    = $script:GoodMission
                'Do you need hosting'        = 'Yes'
            })
    }

    # --- pid 33 fixtures ---
    $script:Complete33 = [pscustomobject]@{
        orderid = '2001'; ordernum = 'ORD2001'; clientid = '601'; pid = 33
        productName = 'FFC 501c3 Application'; charityName = 'Riverside Relief Fund'
        fields = (New-Fields @{
                'Organization Name'          = 'Riverside Relief Fund'
                'EIN'                        = '98-7654321'
                'GuideStar Profile'          = 'https://www.guidestar.org/profile/98-7654321'
                'Candid Profile'             = 'https://www.candid.org/profile/riverside'
                'Facebook Page'              = 'https://www.facebook.com/riversiderelief'
                'LinkedIn Organization Page' = 'https://www.linkedin.com/company/riverside-relief'
                'US-based attestation'       = 'Yes'
                'Terms of Service agreement' = 'I agree'
                'Mission'                    = $script:GoodMission
                'Do you need hosting'        = 'Yes'
                'Time zone'                  = 'Eastern Time (US)'
                'President LinkedIn'         = 'https://www.linkedin.com/in/jane-rivers'
                'President phone'            = '+1-555-100-2001'
                'President email'            = 'jane@riversiderelief.org'
                'Secretary LinkedIn'         = 'https://www.linkedin.com/in/mark-stone'
                'Secretary phone'            = '+1-555-100-2002'
                'Secretary email'            = 'mark@riversiderelief.org'
                'Treasurer LinkedIn'         = 'https://www.linkedin.com/in/pat-lee'
                'Treasurer phone'            = '+1-555-100-2003'
                'Treasurer email'            = 'pat@riversiderelief.org'
                'Primary contact'            = 'Jane Rivers, jane@riversiderelief.org'
                'Technical contact'          = 'Sam Tech, sam@riversiderelief.org'
            })
    }

    $script:PlaceholderBoard33 = [pscustomobject]@{
        orderid = '2002'; ordernum = 'ORD2002'; clientid = '602'; pid = 33
        productName = 'FFC 501c3 Application'; charityName = 'Coastal Care Alliance'
        fields = (New-Fields @{
                'Organization Name'          = 'Coastal Care Alliance'
                'EIN'                        = '55-5555555'
                'GuideStar Profile'          = 'https://www.guidestar.org/profile/55-5555555'
                'Facebook Page'              = 'https://www.facebook.com/coastalcare'
                'LinkedIn Organization Page' = 'https://www.linkedin.com/company/coastal-care'
                'US-based attestation'       = 'Yes'
                'Terms of Service agreement' = 'I agree'
                'Mission'                    = $script:GoodMission
                'Do you need hosting'        = 'Yes'
                'Time zone'                  = 'Pacific Time'
                'President LinkedIn'         = 'https://www.linkedin.com/in/clarkemoyer/'
                'President phone'            = '+1-555-000-0001'
                'President email'            = 'founder@coastalcare.org'
            })
    }
}

Describe 'Field validators' {
    It 'Test-ValidEin accepts NN-NNNNNNN and rejects junk' {
        Test-ValidEin -Value '12-3456789' | Should -BeTrue
        Test-ValidEin -Value '123456789'  | Should -BeTrue
        Test-ValidEin -Value 'ABC'        | Should -BeFalse
        Test-ValidEin -Value ''           | Should -BeFalse
    }

    It 'Test-GuidestarUrl accepts candid/guidestar and rejects others' {
        Test-GuidestarUrl -Value 'https://www.guidestar.org/profile/1' | Should -BeTrue
        Test-GuidestarUrl -Value 'https://candid.org/x'                | Should -BeTrue
        Test-GuidestarUrl -Value 'https://example.com/x'               | Should -BeFalse
        Test-GuidestarUrl -Value ''                                    | Should -BeFalse
    }

    It 'Test-LinkedInOrgUrl accepts /company/ and rejects personal /in/' {
        Test-LinkedInOrgUrl -Value 'https://www.linkedin.com/company/acme' | Should -BeTrue
        Test-LinkedInOrgUrl -Value 'https://www.linkedin.com/in/johndoe'   | Should -BeFalse
    }

    It 'Test-FacebookPageUrl needs a page path' {
        Test-FacebookPageUrl -Value 'https://facebook.com/somepage' | Should -BeTrue
        Test-FacebookPageUrl -Value 'https://facebook.com/'         | Should -BeFalse
    }

    It 'Test-TruthyTick recognizes affirmatives only' {
        Test-TruthyTick -Value 'Yes'      | Should -BeTrue
        Test-TruthyTick -Value 'I agree'  | Should -BeTrue
        Test-TruthyTick -Value 'no'       | Should -BeFalse
        Test-TruthyTick -Value ''         | Should -BeFalse
    }

    It 'Test-PlaceholderLinkedIn flags the founder profile' {
        Test-PlaceholderLinkedIn -Value 'https://www.linkedin.com/in/clarkemoyer/' | Should -BeTrue
        Test-PlaceholderLinkedIn -Value 'https://www.linkedin.com/in/realmember'   | Should -BeFalse
    }

    It 'Test-PersonNameCompany flags ALLCAPS person names' {
        (Test-PersonNameCompany -Name 'JOHN SMITH').IsPerson  | Should -BeTrue
        (Test-PersonNameCompany -Name 'JOHN SMITH').IsAllCaps | Should -BeTrue
        (Test-PersonNameCompany -Name 'Bright Future Foundation').IsPerson | Should -BeFalse
    }

    It 'Get-HrefOrRaw unwraps anchor tags' {
        Get-HrefOrRaw -Value '<a href="https://x.org/y">label</a>' | Should -Be 'https://x.org/y'
        Get-HrefOrRaw -Value 'https://x.org/y' | Should -Be 'https://x.org/y'
    }
}

Describe 'Get-ApplicationScore (pid 16)' {
    It 'scores a complete application highly' {
        $r = Get-ApplicationScore -Application $script:Complete16
        $r.score | Should -BeGreaterThan 90
        $r.penalties.Count | Should -Be 0
    }

    It 'scores a sparse application low and below a complete one' {
        $complete = Get-ApplicationScore -Application $script:Complete16
        $sparse = Get-ApplicationScore -Application $script:Sparse16
        $sparse.score | Should -BeLessThan 40
        $sparse.score | Should -BeLessThan $complete.score
        $sparse.topGaps | Should -Contain 'missing/invalid EIN'
    }

    It 'penalizes a person name (ALLCAPS) in the charity field' {
        $r = Get-ApplicationScore -Application $script:Placeholder16
        ($r.penalties -join ' ') | Should -Match 'person'
        $complete = Get-ApplicationScore -Application $script:Complete16
        $r.score | Should -BeLessThan $complete.score
    }
}

Describe 'Get-ApplicationScore (pid 33)' {
    It 'scores a complete 501c3 application highly' {
        $r = Get-ApplicationScore -Application $script:Complete33
        $r.score | Should -BeGreaterThan 90
        $r.breakdown.board.fill | Should -Be 1
    }

    It 'penalizes a placeholder founder LinkedIn in a board slot and an incomplete roster' {
        $r = Get-ApplicationScore -Application $script:PlaceholderBoard33
        ($r.penalties -join ' ') | Should -Match 'placeholder LinkedIn'
        $r.breakdown.board.fill | Should -BeLessThan 1
        $r.topGaps | Should -Contain 'incomplete board roster'
    }
}

Describe 'Charity name from client companyname (GetClientsDetails)' {
    It 'Get-ClientNameParts reads companyname from a flat response' {
        $details = [pscustomobject]@{
            result = 'success'; companyname = 'Bright Future Foundation'
            firstname = 'Jane'; lastname = 'Rivers'
        }
        $parts = Get-ClientNameParts -Details $details
        $parts.CompanyName | Should -Be 'Bright Future Foundation'
        $parts.FullName    | Should -Be 'Jane Rivers'
    }

    It 'Get-ClientNameParts reads companyname from a client-nested response' {
        $details = [pscustomobject]@{
            result = 'success'
            client = [pscustomobject]@{ companyname = 'Riverside Relief Fund'; firstname = 'Mark'; lastname = 'Stone' }
        }
        (Get-ClientNameParts -Details $details).CompanyName | Should -Be 'Riverside Relief Fund'
    }

    It 'Get-CharityDisplayName prefers companyname over custom fields and person fallback' {
        $fields = New-Fields @{ 'Organization Name' = '' }
        Get-CharityDisplayName -Fields $fields -CompanyName 'Helping Hands Society' -FallbackName 'Jane Rivers' |
            Should -Be 'Helping Hands Society'
    }

    It 'Get-CharityDisplayName falls back to the applicant name when companyname is blank' {
        $fields = New-Fields @{ 'Organization Name' = '' }
        Get-CharityDisplayName -Fields $fields -CompanyName '' -FallbackName 'Jane Rivers' |
            Should -Be 'Jane Rivers'
    }

    It 'Get-WhmcsClientDetails uses companyname and caches per clientid (mocked GetClientsDetails)' {
        Mock -CommandName Invoke-WhmcsApi -MockWith {
            [pscustomobject]@{ result = 'success'; companyname = 'Coastal Care Alliance'; firstname = 'Pat'; lastname = 'Lee' }
        }
        $cache = @{}
        $d1 = Get-WhmcsClientDetails -Api 'https://freeforcharity.org/hub/includes/api.php' -Auth @{ identifier = 'x'; secret = 'y' } -ClientId '382' -Cache $cache
        $d2 = Get-WhmcsClientDetails -Api 'https://freeforcharity.org/hub/includes/api.php' -Auth @{ identifier = 'x'; secret = 'y' } -ClientId '382' -Cache $cache

        $charity = Get-CharityDisplayName -Fields @() -CompanyName (Get-ClientNameParts -Details $d1).CompanyName -FallbackName (Get-ClientNameParts -Details $d1).FullName
        $charity | Should -Be 'Coastal Care Alliance'
        $charity | Should -Not -Be '(unknown org)'
        $d2 | Should -Be $d1
        # Second lookup is served from the cache -> only one API call.
        Should -Invoke -CommandName Invoke-WhmcsApi -Times 1 -Exactly
    }

    It 'penalizes a companyname that just repeats the applicant name' {
        # A 4-word name is not caught by Test-PersonNameCompany, so this exercises
        # the companyIsPersonName signal in isolation.
        $app = [pscustomobject]@{
            orderid = '3001'; ordernum = 'ORD3001'; clientid = '382'; pid = 16
            productName = 'FFC Pre-501c3 Application'; charityName = 'Maria De La Cruz'
            companyIsPersonName = $true
            fields              = (New-Fields @{
                    'Organization Name' = ''
                    'Mission'           = $script:GoodMission
                })
        }
        $r = Get-ApplicationScore -Application $app
        ($r.penalties -join ' ') | Should -Match 'company name matches applicant name'
    }
}

Describe 'Get-ApplicationRanking' {
    It 'orders by score descending then orderid ascending' {
        $scored = @(
            (Get-ApplicationScore -Application $script:Sparse16),
            (Get-ApplicationScore -Application $script:Complete16)
        )
        $ranked = Get-ApplicationRanking -ScoredApplications $scored
        $ranked[0].orderid | Should -Be '1001'
        $ranked[1].orderid | Should -Be '1002'
    }
}
