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

Describe 'Legal-status normalization' {
    It 'maps the documented values to canonical buckets' {
        Get-NormalizedLegalStatus -Value '501c(3) Nonprofit'            | Should -Be 'full'
        Get-NormalizedLegalStatus -Value '4. 501c3 General Organization' | Should -Be 'full'
        Get-NormalizedLegalStatus -Value 'pre-501c(3) Nonprofit'        | Should -Be 'pre'
        Get-NormalizedLegalStatus -Value 'US State Recognized Nonprofit' | Should -Be 'other'
        Get-NormalizedLegalStatus -Value 'US Not-For-Profit'            | Should -Be 'other'
        Get-NormalizedLegalStatus -Value 'Other Charitable Organization' | Should -Be 'other'
        Get-NormalizedLegalStatus -Value ''                            | Should -Be 'unknown'
    }
}

Describe 'Category mismatch (legal status vs product track)' {
    It 'flags a full-501c3 legal status filed on the pre-501c3 track (pid 16)' {
        $cm = Get-CategoryMismatch -ProductPid 16 -LegalStatus 'full'
        $cm.IsMismatch | Should -BeTrue
        $cm.Note       | Should -Match 'pre-501'
    }

    It 'reinforces the mismatch when an IRS-verified 501(c)(3) EIN sits on the pre track' {
        (Get-CategoryMismatch -ProductPid 16 -LegalStatus 'unknown' -EinIs501c3 $true).IsMismatch | Should -BeTrue
    }

    It 'flags a pre/other legal status filed on the full-501c3 track (pid 33)' {
        (Get-CategoryMismatch -ProductPid 33 -LegalStatus 'pre').IsMismatch   | Should -BeTrue
        (Get-CategoryMismatch -ProductPid 33 -LegalStatus 'other').IsMismatch | Should -BeTrue
    }

    It 'does NOT flag an unknown legal status with no EIN signal' {
        (Get-CategoryMismatch -ProductPid 16 -LegalStatus 'unknown').IsMismatch | Should -BeFalse
        (Get-CategoryMismatch -ProductPid 33 -LegalStatus 'unknown').IsMismatch | Should -BeFalse
    }

    It 'a pre-501c3 order with a full 501(c)(3) legal status is categoryMismatch and excluded from the top pick' {
        # order 695 pattern: filed pre-501c3 (pid 16) but legal status is a full 501(c)(3).
        $misfiled = [pscustomobject]@{
            orderid = '695'; ordernum = 'ORD695'; clientid = '695'; pid = 16
            productName = 'FFC Pre-501c3 Application'; charityName = 'Junior League of Greenville'
            fields = (New-Fields @{
                    'Organization Name'                              = 'Junior League of Greenville'
                    'What is the legal status of your organization?' = '501c(3) Nonprofit'
                    'EIN'                                            = '27-2634719'
                    'Mission'                                        = $script:GoodMission
                })
        }
        $scoredMis = Get-ApplicationScore -Application $misfiled
        $scoredMis.categoryMismatch | Should -BeTrue
        $scoredMis.legalStatus      | Should -Be 'full'

        # A genuinely pre-501c3 application on the same track.
        $scoredOk = Get-ApplicationScore -Application $script:Complete16
        $scoredOk.categoryMismatch  | Should -BeFalse

        $split = Split-ByCategory -ScoredApplications @($scoredMis, $scoredOk)
        @($split.Ok | ForEach-Object { $_.orderid })             | Should -Not -Contain '695'
        @($split.Miscategorized | ForEach-Object { $_.orderid }) | Should -Contain '695'
        # The miscategorized app can never be the group's top pick.
        $split.Ok[0].orderid | Should -Be '1001'
    }
}

Describe 'Live EIN verification (ProPublica, mocked)' {
    It 'treats subsection_code 3 as a verified 501(c)(3)' {
        Mock -CommandName Invoke-RestMethod -MockWith {
            [pscustomobject]@{ organization = [pscustomobject]@{ ein = 272634719; name = 'JUNIOR LEAGUE OF GREENVILLE NC INC'; subsection_code = 3; ruling_date = '201101' } }
        }
        $cache = @{}
        $v = Invoke-ProPublicaEin -Ein '27-2634719' -BaseUrl 'https://stub.test/orgs' -Cache $cache -PaceMs 0
        $v.Verified       | Should -BeTrue
        $v.Is501c3        | Should -BeTrue
        $v.SubsectionCode | Should -Be 3
        $v.Name           | Should -Match 'JUNIOR LEAGUE'
        Should -Invoke -CommandName Invoke-RestMethod -Times 1 -Exactly
    }

    It 'caches per EIN (second lookup makes no call)' {
        Mock -CommandName Invoke-RestMethod -MockWith {
            [pscustomobject]@{ organization = [pscustomobject]@{ subsection_code = 3 } }
        }
        $cache = @{}
        Invoke-ProPublicaEin -Ein '27-2634719' -BaseUrl 'https://stub.test/orgs' -Cache $cache -PaceMs 0 | Out-Null
        Invoke-ProPublicaEin -Ein '27-2634719' -BaseUrl 'https://stub.test/orgs' -Cache $cache -PaceMs 0 | Out-Null
        Should -Invoke -CommandName Invoke-RestMethod -Times 1 -Exactly
    }

    It 'treats a 404 / lookup error as unverified' {
        Mock -CommandName Invoke-RestMethod -MockWith { throw 'Response status code does not indicate success: 404 (Not Found).' }
        $cache = @{}
        $v = Invoke-ProPublicaEin -Ein '99-9999999' -BaseUrl 'https://stub.test/orgs' -Cache $cache -PaceMs 0
        $v.Verified | Should -BeFalse
        $v.Is501c3  | Should -BeNullOrEmpty
    }
}

Describe 'Candid/GuideStar link checks' {
    It 'extracts an EIN from a candid profile slug' {
        Get-EinFromCandidUrl -Value 'https://www.candid.org/profile/12-3456789' | Should -Be '123456789'
        Get-EinFromCandidUrl -Value 'https://www.guidestar.org/profile/no-ein'  | Should -Be ''
    }

    It 'notes a candid-slug EIN that disagrees with the EIN field' {
        Mock -CommandName Invoke-RestMethod -MockWith { [pscustomobject]@{ organization = [pscustomobject]@{ subsection_code = 3; name = 'Bright Future Foundation' } } }
        Mock -CommandName Invoke-WebRequest  -MockWith { [pscustomobject]@{ StatusCode = 200 } }
        $app = [pscustomobject]@{
            orderid = '4100'; charityName = 'Bright Future Foundation'; pid = 16
            fields = (New-Fields @{
                    'EIN'                        = '12-3456789'
                    'GuideStar / Candid Profile' = 'https://www.candid.org/profile/98-7654321'
                })
        }
        $v = Get-ApplicationVerification -Application $app -EinBaseUrl 'https://stub.test/orgs'
        $v.guidestarEinMismatch | Should -BeTrue
    }
}

Describe '-SkipEinVerify makes zero network calls' {
    It 'never calls Invoke-RestMethod or Invoke-WebRequest when skipped' {
        Mock -CommandName Invoke-RestMethod -MockWith { throw 'should not be called' }
        Mock -CommandName Invoke-WebRequest  -MockWith { throw 'should not be called' }
        $app = [pscustomobject]@{
            orderid = '5100'; charityName = 'Skip Test Org'; pid = 16
            fields = (New-Fields @{
                    'EIN'                        = '12-3456789'
                    'GuideStar / Candid Profile' = 'https://www.candid.org/profile/12-3456789'
                })
        }
        $v = Get-ApplicationVerification -Application $app -SkipEinVerify
        $v.einChecked       | Should -BeFalse
        $v.guidestarChecked | Should -BeFalse
        Should -Invoke -CommandName Invoke-RestMethod -Times 0 -Exactly
        Should -Invoke -CommandName Invoke-WebRequest  -Times 0 -Exactly
    }
}

Describe 'Robust EIN resolution by NAME (both products)' {
    It 'reads the pid-16 EIN field (regression: the working case still works)' {
        $info = Get-ApplicationEin -Fields $script:Complete16.fields -ProductPid 16
        $info.Source    | Should -Be 'field'
        $info.Formatted | Should -Be '12-3456789'
    }

    It 'resolves a pid-33 EIN field that uses a DIFFERENT label' {
        # pid 33 does not label it 'EIN'; the value must still resolve by name.
        $fields = New-Fields @{
            'Organization Name'                         = 'Meridian Care Collective'
            "What is your organization's Tax ID (EIN)?" = '81-2345678'
            'GuideStar Profile'                         = 'https://www.guidestar.org/profile/meridian'
        }
        $info = Get-ApplicationEin -Fields $fields -ProductPid 33
        $info.Source    | Should -Be 'field'
        $info.Formatted | Should -Be '81-2345678'
    }

    It 'does NOT match everyday field names that merely contain the letters e-i-n' {
        $fields = New-Fields @{ 'Are you being sponsored by another org?' = 'Yes' }
        Resolve-EinFieldValue -Fields $fields -ProductPid 33 | Should -Be ''
    }

    It 'caches the resolved EIN field id per pid' {
        $cache = @{}
        $fields = New-Fields @{ 'Employer Identification Number' = '81-2345678' }
        Resolve-EinFieldValue -Fields $fields -ProductPid 33 -FieldIdCache $cache | Should -Be '81-2345678'
        $cache.ContainsKey(33) | Should -BeTrue
    }
}

Describe 'Candid-slug EIN fallback (pid 33 blank EIN field)' {
    It 'derives the EIN from the Candid slug when the EIN field is blank' {
        $fields = New-Fields @{
            'Organization Name'                         = 'Harbor Light Mission'
            "What is your organization's Tax ID (EIN)?" = ''
            'Candid Profile'                            = 'https://www.candid.org/profile/8675309/harbor-light-mission-45-6789012'
        }
        $info = Get-ApplicationEin -Fields $fields -ProductPid 33
        $info.Source    | Should -Be 'candid'
        $info.Formatted | Should -Be '45-6789012'
    }

    It 'feeds the Candid-derived EIN into the live verification path' {
        Mock -CommandName Invoke-RestMethod -MockWith {
            [pscustomobject]@{ organization = [pscustomobject]@{ name = 'HARBOR LIGHT MISSION'; subsection_code = 3; ruling_date = '201505' } }
        }
        Mock -CommandName Invoke-WebRequest -MockWith { [pscustomobject]@{ StatusCode = 200 } }
        $app = [pscustomobject]@{
            orderid = '2100'; charityName = 'Harbor Light Mission'; pid = 33
            fields = (New-Fields @{
                    "What is your organization's Tax ID (EIN)?" = ''
                    'Candid Profile'                            = 'https://www.candid.org/profile/8675309/harbor-light-mission-45-6789012'
                })
        }
        $v = Get-ApplicationVerification -Application $app -EinBaseUrl 'https://stub.test/orgs'
        $v.einSource  | Should -Be 'candid'
        $v.einChecked | Should -BeTrue
        $v.einVerified | Should -BeTrue
        $v.einIs501c3 | Should -BeTrue
        Should -Invoke -CommandName Invoke-RestMethod -Times 1 -Exactly
    }

    It 'scores a pid-33 app with a blank EIN field but a Candid EIN as having a valid EIN' {
        $app = [pscustomobject]@{
            orderid = '2101'; ordernum = 'ORD2101'; clientid = '701'; pid = 33
            productName = 'FFC 501c3 Application'; charityName = 'Harbor Light Mission'
            fields = (New-Fields @{
                    'Organization Name'                         = 'Harbor Light Mission'
                    "What is your organization's Tax ID (EIN)?" = ''
                    'Candid Profile'                            = 'https://www.candid.org/profile/8675309/harbor-light-mission-45-6789012'
                })
        }
        $r = Get-ApplicationScore -Application $app
        $r.breakdown.ein.fill | Should -Be 1
        $r.einSource          | Should -Be 'candid'
        $r.topGaps            | Should -Not -Contain 'missing/invalid EIN'
    }

    It 'still flags a genuinely missing EIN (no field, no Candid slug) as a gap' {
        $app = [pscustomobject]@{
            orderid = '2102'; ordernum = 'ORD2102'; clientid = '702'; pid = 33
            productName = 'FFC 501c3 Application'; charityName = 'No Ein Org'
            fields = (New-Fields @{
                    'Organization Name' = 'No Ein Org'
                    'Candid Profile'    = 'https://www.candid.org/profile/no-ein-here'
                })
        }
        $r = Get-ApplicationScore -Application $app
        $r.breakdown.ein.fill | Should -Be 0
        $r.topGaps            | Should -Contain 'missing/invalid EIN'
    }
}

Describe 'FFC readiness scoring (mission-weighted)' {
    It 'classifies a food-pantry mission as basic-needs (+50)' {
        $fields = New-Fields @{ 'Mission' = 'We run a community food pantry providing meals and nutrition to hungry families.' }
        Get-MissionCategory -MissionText 'community food pantry meals' | Should -Be 'basic-needs'
        $r = Get-FfcReadinessScore -Fields $fields -ProductPid 16 -LegalStatus 'unknown'
        $r.Mission                        | Should -Be 'basic-needs'
        $r.Components.mission.points      | Should -Be 50
    }

    It 'classifies a veterans mission as veterans (+30)' {
        $fields = New-Fields @{ 'Mission' = 'We support wounded warrior veterans and military servicemembers returning home.' }
        $r = Get-FfcReadinessScore -Fields $fields -ProductPid 16 -LegalStatus 'unknown'
        $r.Mission                   | Should -Be 'veterans'
        $r.Components.mission.points | Should -Be 30
    }

    It 'classifies a general mission as general (0)' {
        $fields = New-Fields @{ 'Mission' = 'We promote appreciation of classical music through community concerts.' }
        $r = Get-FfcReadinessScore -Fields $fields -ProductPid 16 -LegalStatus 'unknown'
        $r.Mission                   | Should -Be 'general'
        $r.Components.mission.points | Should -Be 0
    }

    It 'prefers basic-needs over veterans on overlap (food for veterans)' {
        Get-MissionCategory -MissionText 'providing food and meals to homeless veterans' | Should -Be 'basic-needs'
    }

    It 'awards charity-stage points from the legal-status bucket' {
        $fields = New-Fields @{ 'Mission' = 'general arts' }
        (Get-FfcReadinessScore -Fields $fields -ProductPid 33 -LegalStatus 'full').Components.charityStage.points | Should -Be 20
        (Get-FfcReadinessScore -Fields $fields -ProductPid 33 -LegalStatus 'pre').Components.charityStage.points  | Should -Be 5
        (Get-FfcReadinessScore -Fields $fields -ProductPid 33 -LegalStatus 'other').Components.charityStage.points | Should -Be 0
    }

    It 'awards the Candid-profile credit only when the profile is live AND the EIN verifies 501(c)(3)' {
        $fields = New-Fields @{ 'Mission' = 'general' }
        (Get-FfcReadinessScore -Fields $fields -ProductPid 33 -LegalStatus 'full' -EinIs501c3 $true -GuidestarLive $true).Components.candidProfile.points | Should -Be 10
        (Get-FfcReadinessScore -Fields $fields -ProductPid 33 -LegalStatus 'full' -EinIs501c3 $true -GuidestarLive $false).Components.candidProfile.points | Should -Be 0
        (Get-FfcReadinessScore -Fields $fields -ProductPid 33 -LegalStatus 'full' -EinIs501c3 $false -GuidestarLive $true).Components.candidProfile.points | Should -Be 0
    }

    It 'lists ffcadmin categories NOT derivable from WHMCS data (excluded, not fabricated)' {
        $r = Get-FfcReadinessScore -Fields (New-Fields @{ 'Mission' = 'general' }) -ProductPid 16 -LegalStatus 'unknown'
        ($r.NotDerivable -join ' ') | Should -Match 'affiliation'
        ($r.NotDerivable -join ' ') | Should -Match 'revenue'
        ($r.NotDerivable -join ' ') | Should -Match 'Form 1023'
    }

    It 'exposes ffcReadinessScore + mission on the scored object and leads the rationale with them' {
        $r = Get-ApplicationScore -Application $script:Complete33
        $r.ffcReadinessScore | Should -BeOfType [int]
        $r.mission           | Should -BeIn @('basic-needs', 'veterans', 'general')
        $r.rationale         | Should -Match '^FFC readiness \d'
    }
}

Describe 'Readiness drives ranking above a more-complete-but-general app' {
    It 'ranks a basic-needs app above a more-complete general app' {
        # A fully-complete GENERAL pre-501c3 app (high completeness score, readiness 5).
        $general = [pscustomobject]@{
            orderid = '9001'; ordernum = 'ORD9001'; clientid = '901'; pid = 16
            productName = 'FFC Pre-501c3 Application'; charityName = 'Symphony Guild'
            fields = (New-Fields @{
                    'Organization Name'                              = 'Symphony Guild'
                    'What is the legal status of your organization?' = 'pre-501c(3) Nonprofit'
                    'EIN'                                            = '12-3456789'
                    'GuideStar / Candid Profile'                     = 'https://www.guidestar.org/profile/12-3456789'
                    'Facebook Page'                                  = 'https://www.facebook.com/symphonyguild'
                    'LinkedIn Organization Page'                     = 'https://www.linkedin.com/company/symphony-guild'
                    'US-based attestation'                           = 'Yes'
                    'Terms of Service agreement'                     = 'I agree'
                    'Mission'                                        = 'We promote classical music education through community concerts and youth orchestra programs across the region.'
                    'Do you need hosting'                            = 'Yes'
                })
        }
        # A sparse BASIC-NEEDS pre-501c3 app (low completeness, readiness 55).
        $basic = [pscustomobject]@{
            orderid = '9002'; ordernum = 'ORD9002'; clientid = '902'; pid = 16
            productName = 'FFC Pre-501c3 Application'; charityName = 'Neighborhood Food Pantry'
            fields = (New-Fields @{
                    'Organization Name'                              = 'Neighborhood Food Pantry'
                    'What is the legal status of your organization?' = 'pre-501c(3) Nonprofit'
                    'Mission'                                        = 'We provide food and meals to hungry families.'
                })
        }
        $sGeneral = Get-ApplicationScore -Application $general
        $sBasic = Get-ApplicationScore -Application $basic
        # Completeness: general is the MORE complete of the two.
        $sGeneral.score              | Should -BeGreaterThan $sBasic.score
        # Readiness: basic-needs outranks general.
        $sBasic.ffcReadinessScore    | Should -BeGreaterThan $sGeneral.ffcReadinessScore
        $ranked = Get-ApplicationRanking -ScoredApplications @($sGeneral, $sBasic)
        $ranked[0].orderid | Should -Be '9002'
        $ranked[1].orderid | Should -Be '9001'
    }
}

Describe 'Reconcile grouping (duplicate + wrong-track)' {
    BeforeAll {
        # Client 700: two onboarding orders (pid 16 #800 + pid 33 #801) -> keeper 801.
        $dupPre = [pscustomobject]@{
            orderid = '800'; ordernum = 'ORD800'; clientid = '700'; pid = 16
            productName = 'FFC Pre-501c3 Application'; charityName = 'Twice Filed Org'
            fields = (New-Fields @{ 'Organization Name' = 'Twice Filed Org'; 'Mission' = 'general help' })
        }
        $dupFull = [pscustomobject]@{
            orderid = '801'; ordernum = 'ORD801'; clientid = '700'; pid = 33
            productName = 'FFC 501c3 Application'; charityName = 'Twice Filed Org'
            fields = (New-Fields @{ 'Organization Name' = 'Twice Filed Org'; 'Mission' = 'general help' })
        }
        # Client 701: single pid-16 order (#802) with a FULL 501(c)(3) legal status -> wrong-track.
        $wrongTrack = [pscustomobject]@{
            orderid = '802'; ordernum = 'ORD802'; clientid = '701'; pid = 16
            productName = 'FFC Pre-501c3 Application'; charityName = 'Misfiled 501c3'
            fields = (New-Fields @{
                    'Organization Name'                              = 'Misfiled 501c3'
                    'What is the legal status of your organization?' = '501c(3) Nonprofit'
                    'Mission'                                        = 'general help'
                })
        }
        # Client 702: single clean pid-16 order (#803) -> keep.
        $clean = [pscustomobject]@{
            orderid = '803'; ordernum = 'ORD803'; clientid = '702'; pid = 16
            productName = 'FFC Pre-501c3 Application'; charityName = 'Clean Applicant'
            fields = (New-Fields @{
                    'Organization Name'                              = 'Clean Applicant'
                    'What is the legal status of your organization?' = 'pre-501c(3) Nonprofit'
                    'Mission'                                        = 'general help'
                })
        }
        $script:ReconScored = @(
            (Get-ApplicationScore -Application $dupPre),
            (Get-ApplicationScore -Application $dupFull),
            (Get-ApplicationScore -Application $wrongTrack),
            (Get-ApplicationScore -Application $clean)
        )
    }

    It 'keeps the highest application type (pid 33) and cancels the duplicate pid 16' {
        $plan = Get-ReconciliationPlan -ScoredApplications $script:ReconScored
        $client700 = @($plan.Clients | Where-Object { $_.clientid -eq '700' })[0]
        $client700.keeper | Should -Be '801'
        $o800 = @($client700.orders | Where-Object { $_.orderid -eq '800' })[0]
        $o801 = @($client700.orders | Where-Object { $_.orderid -eq '801' })[0]
        $o801.disposition | Should -Be 'keep'
        $o800.disposition | Should -Be 'cancel'
        $o800.reason      | Should -Match 'duplicate'
    }

    It 'cancels a wrong-track pre-501c3 order (full 501(c)(3) legal status) as a re-file candidate' {
        $plan = Get-ReconciliationPlan -ScoredApplications $script:ReconScored
        $o802 = @(($plan.CancelCandidates) | Where-Object { $_.orderid -eq '802' })[0]
        $o802             | Should -Not -BeNullOrEmpty
        $o802.reason      | Should -Match 'wrong-track'
        $o802.email       | Should -Match 're-file'
    }

    It 'keeps a lone clean order and never lists it as a cancel candidate' {
        $plan = Get-ReconciliationPlan -ScoredApplications $script:ReconScored
        @($plan.CancelCandidates | ForEach-Object { $_.orderid }) | Should -Not -Contain '803'
        $client702 = @($plan.Clients | Where-Object { $_.clientid -eq '702' })[0]
        @($client702.orders)[0].disposition | Should -Be 'keep'
    }

    It 'lists exactly the two cancel candidates (800 duplicate, 802 wrong-track)' {
        $plan = Get-ReconciliationPlan -ScoredApplications $script:ReconScored
        $ids = @($plan.CancelCandidates | ForEach-Object { $_.orderid } | Sort-Object)
        $ids | Should -Be @('800', '802')
    }
}

Describe 'Re-file email compose ({$prior_answers} injection)' {
    It 'builds a Question: Answer list from the applicant own answers, skipping blanks' {
        $fields = New-Fields @{
            'Organization Name'   = 'Harbor Light Mission'
            'EIN'                 = '45-6789012'
            'Mission'             = ''
            'Do you need hosting' = 'n/a'
        }
        $html = Format-PriorAnswers -Fields $fields
        $html | Should -Match '<li><strong>Organization Name:</strong> Harbor Light Mission</li>'
        $html | Should -Match '<li><strong>EIN:</strong> 45-6789012</li>'
        # Blank Mission and placeholder 'n/a' hosting are skipped.
        $html | Should -Not -Match 'Mission:'
        $html | Should -Not -Match 'hosting'
    }

    It 'injects the prior-answers list into the {$prior_answers} placeholder and leaves other merge fields intact' {
        $template = '<p>Dear {$client_first_name},</p>{$prior_answers}<p>{$signature}</p>'
        $fields = New-Fields @{ 'Organization Name' = 'Test Org' }
        $body = New-RefileEmailBody -TemplateBody $template -Fields $fields
        $body | Should -Match '<li><strong>Organization Name:</strong> Test Org</li>'
        $body | Should -Not -Match ([regex]::Escape('{$prior_answers}'))
        # Native WHMCS merge fields are preserved for WHMCS to resolve.
        $body | Should -Match ([regex]::Escape('{$client_first_name}'))
        $body | Should -Match ([regex]::Escape('{$signature}'))
    }

    It 'HTML-encodes answer values so they cannot break the markup' {
        $fields = New-Fields @{ 'Notes' = 'A & B <script>' }
        $html = Format-PriorAnswers -Fields $fields
        $html | Should -Match 'A &amp; B &lt;script&gt;'
    }
}
