#requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0.0' }
<#
    Unit tests for the PURE verdict functions of
    scripts/whmcs-charity-lane-verdicts.ps1 (workflow 232). The script is
    dot-sourced (its runner body is guarded by
    `if ($MyInvocation.InvocationName -eq '.') { return }`, as is the triage
    library it dot-sources in turn), so NO WHMCS, ProPublica or social-page
    call is made here: every live fact is a fixture on the application object.

    Run: Invoke-Pester tests/whmcs-charity-lane-verdicts.Tests.ps1
#>

BeforeAll {
    . (Join-Path $PSScriptRoot '..' 'scripts' 'whmcs-charity-lane-verdicts.ps1')

    function New-Fields {
        param([System.Collections.Specialized.OrderedDictionary]$Map)
        $i = 0
        $out = @()
        foreach ($k in $Map.Keys) {
            $i++
            $out += [pscustomobject]@{ id = "$i"; name = $k; value = [string]$Map[$k] }
        }
        return $out
    }

    $script:Mission = 'We provide free after-school tutoring, nutritious meals, and caring mentorship to underserved children across three counties so that every child can reach their full potential regardless of family income.'
    $script:Fb = 'https://www.facebook.com/brightfuturefdn'
    $script:Li = 'https://www.linkedin.com/company/bright-future-foundation'

    function New-Gate1Fields {
        param([hashtable]$Override = @{}, [string[]]$Drop = @())
        $map = [ordered]@{
            'What is the legal status of your organization?'                                       = 'pre-501c(3) Nonprofit'
            "What is your organization's mission?"                                                 = $script:Mission
            'Do you have a domain name?'                                                           = 'Yes'
            'What is your current/desired domain name?'                                            = 'https://www.BrightFuture.org/'
            'What is your GuideStar.org URL?'                                                      = 'https://www.guidestar.org/profile/12-3456789'
            "What is your organization's EIN number?"                                              = '12-3456789'
            'What phone number do you want us to reach you at?'                                    = '555-123-4567'
            'Primary Contact Individual Email'                                                     = 'jane.doe@example.com'
            'facebook-page|Charity Facebook Page URL'                                              = $script:Fb
            'linkedin-page|Charity LinkedIn Page URL'                                              = $script:Li
            'I certify our organization agrees to the Free For Charity Terms of Service'           = 'on'
            'I certify our organization is legally established and operating in the United States' = 'on'
        }
        foreach ($k in $Override.Keys) { $map[$k] = $Override[$k] }
        foreach ($k in $Drop) { $map.Remove($k) }
        return New-Fields $map
    }

    function New-Gate1App {
        param(
            [int]$ProductPid = 16,
            [hashtable]$Override = @{},
            [string[]]$Drop = @(),
            [hashtable]$Set = @{}
        )
        $fields = New-Gate1Fields -Override $Override -Drop $Drop
        $app = [pscustomobject]@{
            orderid              = '9001'
            clientid             = '701'
            pid                  = $ProductPid
            fields               = $fields
            companyName          = 'Bright Future Foundation'
            applicantFullName    = 'Jane Doe'
            serviceDomain        = ''
            orderLaneItemCount   = 1
            verification         = @{
                einChecked = $true; einVerified = $true; einIs501c3 = ($ProductPid -eq 33)
                subsectionCode = $(if ($ProductPid -eq 33) { 3 } else { 4 }); nameMismatch = $false
                guidestarChecked = $true; guidestarLive = $true; guidestarEinMismatch = $false; einSource = 'field'
            }
            urlProbes            = @{ $script:Fb = 200; $script:Li = 302 }
            otherClientsSameOrg  = 0
            domainOwnerClientIds = @()
        }
        if ($ProductPid -eq 33) {
            $merged = @{ 'What is the legal status of your organization?' = '501c(3) Nonprofit' }
            foreach ($k in $Override.Keys) { $merged[$k] = $Override[$k] }
            $app.fields = New-Gate1Fields -Override $merged -Drop $Drop
        }
        foreach ($k in $Set.Keys) { $app.$k = $Set[$k] }
        return $app
    }

    function New-Svc {
        param([string]$Id, [string]$OrderId, [string]$ClientId, [int]$ProductPid, [string]$Status, [string]$Domain = '')
        return [pscustomobject]@{ id = $Id; orderid = $OrderId; clientid = $ClientId; pid = $ProductPid; status = $Status; desiredDomain = $Domain; serviceDomain = '' }
    }

    # The pending application itself is in the service history (as it is live).
    $script:Ctx = [pscustomobject]@{
        OnboardingServices = @(New-Svc -Id '5001' -OrderId '9001' -ClientId '701' -ProductPid 16 -Status 'Pending' -Domain 'brightfuture.org')
        WebsiteServices    = @()
    }

    $script:Gate2Ref = 'Approved onboarding application reference (service or order ID, or charity name)'
    $script:Gate2Att = 'Our charity onboarding application has been fully approved by Free For Charity'

    function New-Gate2App {
        param([string]$Reference = '', [string]$Attest = 'on', [string]$ClientId = '801', [switch]$NoFields)
        $fields = if ($NoFields) { @() } else { New-Fields ([ordered]@{ $script:Gate2Ref = $Reference; $script:Gate2Att = $Attest }) }
        return [pscustomobject]@{
            orderid = '9100'; clientid = $ClientId; pid = 40; fields = $fields
            companyName = 'Riverbend Food Pantry Inc'; applicantFullName = 'Sam Roe'; serviceDomain = ''
            orderLaneItemCount = 1; verification = $null; urlProbes = @{}
            otherClientsSameOrg = $null; domainOwnerClientIds = $null
        }
    }

    function New-Gate2Ctx {
        param([string]$Gate1Status = 'Active', [string]$Gate1Client = '801', [object[]]$Website = @())
        return [pscustomobject]@{
            OnboardingServices = @(New-Svc -Id '6001' -OrderId '8001' -ClientId $Gate1Client -ProductPid 33 -Status $Gate1Status -Domain 'riverbendpantry.org')
            WebsiteServices    = @(New-Svc -Id '6100' -OrderId '9100' -ClientId '801' -ProductPid 40 -Status 'Pending') + @($Website)
        }
    }
}

Describe 'Get-LaneVerdictFromCodes' {
    It 'is ACCEPT with no codes or only INFO codes' {
        Get-LaneVerdictFromCodes -Codes @() | Should -Be 'ACCEPT'
        Get-LaneVerdictFromCodes -Codes @('FRAUD_CHECK_NOT_RUN', 'DESIRED_DOMAIN_MISSING') | Should -Be 'ACCEPT'
    }
    It 'is NEED_INFO when the worst code is NEED_INFO' {
        Get-LaneVerdictFromCodes -Codes @('FRAUD_CHECK_NOT_RUN', 'TOS_NOT_ATTESTED') | Should -Be 'NEED_INFO'
    }
    It 'is ESCALATE when any code is ESCALATE' {
        Get-LaneVerdictFromCodes -Codes @('TOS_NOT_ATTESTED', 'EIN_NOT_501C3') | Should -Be 'ESCALATE'
    }
    It 'treats an unknown code as ESCALATE (fail closed)' {
        Get-LaneVerdictFromCodes -Codes @('SOMETHING_NEW') | Should -Be 'ESCALATE'
    }
    It 'every catalog severity is one of INFO / NEED_INFO / ESCALATE' {
        foreach ($k in $script:LaneReasonCodes.Keys) {
            @('INFO', 'NEED_INFO', 'ESCALATE') | Should -Contain $script:LaneReasonCodes[$k].severity
        }
    }
}

Describe 'ConvertTo-LaneDomain' {
    It 'reduces <In> to <Out>' -ForEach @(
        @{ In = 'https://www.BrightFuture.org/'; Out = 'brightfuture.org' }
        @{ In = 'brightfuture.org'; Out = 'brightfuture.org' }
        @{ In = 'http://sub.example.co.uk:8080/path?q=1'; Out = 'sub.example.co.uk' }
        @{ In = '<a href="https://example.org">x</a>'; Out = 'example.org' }
        @{ In = 'jane.doe@example.com'; Out = '' }
        @{ In = 'none'; Out = '' }
        @{ In = 'We do not have one yet'; Out = '' }
        @{ In = ''; Out = '' }
    ) {
        ConvertTo-LaneDomain -Value $In | Should -Be $Out
    }
}

Describe 'Test-LaneProbeAnswered' {
    It 'answers <Status> -> <Expected>' -ForEach @(
        @{ Status = 200; Expected = $true }
        @{ Status = 301; Expected = $true }
        @{ Status = 399; Expected = $true }
        @{ Status = 400; Expected = $false }
        @{ Status = 404; Expected = $false }
        @{ Status = 999; Expected = $false }
        @{ Status = $null; Expected = $false }
    ) {
        Test-LaneProbeAnswered -Status $Status | Should -Be $Expected
    }
}

Describe 'Get-LaneOrgName' {
    It 'prints a normal org name' {
        $r = Get-LaneOrgName -CompanyName 'Bright Future Foundation' -ApplicantFullName 'Jane Doe' -Fields @()
        $r.Status | Should -Be 'ok'
        $r.Name | Should -Be 'Bright Future Foundation'
    }
    It "flags the applicant's own name as 'applicant' and prints nothing" {
        $r = Get-LaneOrgName -CompanyName 'jane doe' -ApplicantFullName 'Jane Doe' -Fields @()
        $r.Status | Should -Be 'applicant'
        $r.Name | Should -Be ''
    }
    It "treats an org name made only of the applicant's own name words as the applicant" {
        (Get-LaneOrgName -CompanyName 'James' -ApplicantFullName 'James Smith' -Fields @()).Status | Should -Be 'applicant'
        (Get-LaneOrgName -CompanyName 'Smith, James' -ApplicantFullName 'James Smith' -Fields @()).Status | Should -Be 'applicant'
        (Get-LaneOrgName -CompanyName 'James' -ApplicantFullName 'James Smith' -Fields @()).Name | Should -Be ''
    }
    It 'keeps a real org name that merely shares a word with the applicant' {
        $r = Get-LaneOrgName -CompanyName 'James Smith Foundation' -ApplicantFullName 'James Smith' -Fields @()
        $r.Status | Should -Not -Be 'applicant'
        (Get-LaneOrgName -CompanyName 'Hope House' -ApplicantFullName 'Hope Jones' -Fields @()).Status | Should -Be 'ok'
    }
    It 'withholds a person-like or contact-bearing name' {
        (Get-LaneOrgName -CompanyName 'JOHN SMITH' -ApplicantFullName 'Jane Doe' -Fields @()).Status | Should -Be 'withheld'
        (Get-LaneOrgName -CompanyName 'Call 555 123 4567 Relief' -ApplicantFullName '' -Fields @()).Status | Should -Be 'withheld'
        (Get-LaneOrgName -CompanyName 'help@relief.org Relief' -ApplicantFullName '' -Fields @()).Name | Should -Be ''
    }
    It 'never falls back to the applicant name when companyname is blank' {
        $r = Get-LaneOrgName -CompanyName '' -ApplicantFullName 'Jane Doe' -Fields @()
        $r.Status | Should -Be 'missing'
        $r.Name | Should -Be ''
    }
    It 'strips markdown-structural characters from a printed name' {
        (Get-LaneOrgName -CompanyName 'Hope | <Relief> Society' -ApplicantFullName '' -Fields @()).Name | Should -Be 'Hope Relief Society'
    }
}

Describe 'Get-CharityLaneVerdict - Gate 1 (pid 16 / 33)' {
    It 'ACCEPTs a complete pid 16 application' {
        $r = Get-CharityLaneVerdict -Application (New-Gate1App) -Context $script:Ctx
        $r.verdict | Should -Be 'ACCEPT'
        $r.reason_codes | Should -Be @('FRAUD_CHECK_NOT_RUN')
        $r.gate | Should -Be 1
        $r.org_name | Should -Be 'Bright Future Foundation'
        $r.desired_domain | Should -Be 'brightfuture.org'
        $r.readiness_score | Should -BeOfType [int]
        $r.fraud_check | Should -Be 'not_run'
    }

    It 'ACCEPTs a complete pid 33 application whose EIN ProPublica lists as a 501(c)(3)' {
        $r = Get-CharityLaneVerdict -Application (New-Gate1App -ProductPid 33) -Context $script:Ctx
        $r.verdict | Should -Be 'ACCEPT'
    }

    It 'reuses the 226 readiness score unchanged' {
        $app = New-Gate1App -ProductPid 33
        $expected = (Get-ApplicationScore -Application ([pscustomobject]@{
                    orderid = '9001'; ordernum = ''; clientid = '701'; pid = 33; productName = ''
                    charityName = 'Bright Future Foundation'; fields = $app.fields
                    einChecked = $true; einVerified = $true; einIs501c3 = $true; guidestarLive = $true
                })).ffcReadinessScore
        (Get-CharityLaneVerdict -Application $app -Context $script:Ctx).readiness_score | Should -Be $expected
    }

    It 'NEED_INFO for a missing Facebook page and a personal LinkedIn profile' {
        $app = New-Gate1App -Override @{ 'linkedin-page|Charity LinkedIn Page URL' = 'https://www.linkedin.com/in/janedoe' } -Drop @('facebook-page|Charity Facebook Page URL')
        $r = Get-CharityLaneVerdict -Application $app -Context $script:Ctx
        $r.verdict | Should -Be 'NEED_INFO'
        $r.reason_codes | Should -Contain 'FACEBOOK_URL_MISSING'
        $r.reason_codes | Should -Contain 'LINKEDIN_URL_NOT_ORG_PAGE'
    }

    It 'never reads a board-member LinkedIn as the org page' {
        $app = New-Gate1App -Drop @('linkedin-page|Charity LinkedIn Page URL') -Override @{ 'Board President/Chair LinkedIn Link' = 'https://www.linkedin.com/company/not-the-org' }
        (Get-CharityLaneVerdict -Application $app -Context $script:Ctx).reason_codes | Should -Contain 'LINKEDIN_URL_MISSING'
    }

    It 'ESCALATEs (never NEED_INFO) when a social page does not answer 2xx/3xx: <Status>' -ForEach @(
        @{ Status = 999 }, @{ Status = 404 }, @{ Status = 400 }, @{ Status = $null }
    ) {
        $app = New-Gate1App
        $app.urlProbes[$script:Li] = $Status
        $r = Get-CharityLaneVerdict -Application $app -Context $script:Ctx
        $r.verdict | Should -Be 'ESCALATE'
        $r.reason_codes | Should -Contain 'LINKEDIN_URL_UNVERIFIED'
        $r.reason_codes | Should -Not -Contain 'LINKEDIN_URL_NOT_ORG_PAGE'
    }

    It 'ESCALATEs when the social pages were never probed' {
        $r = Get-CharityLaneVerdict -Application (New-Gate1App -Set @{ urlProbes = @{} }) -Context $script:Ctx
        $r.reason_codes | Should -Contain 'FACEBOOK_URL_UNVERIFIED'
        $r.reason_codes | Should -Contain 'LINKEDIN_URL_UNVERIFIED'
    }

    It 'NEED_INFO when ToS and US attestations are not ticked' {
        $app = New-Gate1App -Override @{
            'I certify our organization agrees to the Free For Charity Terms of Service'           = ''
            'I certify our organization is legally established and operating in the United States' = 'off'
        }
        $r = Get-CharityLaneVerdict -Application $app -Context $script:Ctx
        $r.verdict | Should -Be 'NEED_INFO'
        $r.reason_codes | Should -Contain 'TOS_NOT_ATTESTED'
        $r.reason_codes | Should -Contain 'US_NOT_ATTESTED'
    }

    It 'NEED_INFO when the attestation fields are absent (pre-hardening application)' {
        $app = New-Gate1App -Drop @('I certify our organization agrees to the Free For Charity Terms of Service', 'I certify our organization is legally established and operating in the United States')
        $r = Get-CharityLaneVerdict -Application $app -Context $script:Ctx
        $r.reason_codes | Should -Contain 'TOS_NOT_ATTESTED'
        $r.reason_codes | Should -Contain 'US_NOT_ATTESTED'
    }

    It 'NEED_INFO TRACK_MISMATCH: pid 16 reporting a full 501(c)(3)' {
        $app = New-Gate1App -Override @{ 'What is the legal status of your organization?' = '501c(3) Nonprofit' }
        $r = Get-CharityLaneVerdict -Application $app -Context $script:Ctx
        $r.verdict | Should -Be 'NEED_INFO'
        $r.reason_codes | Should -Contain 'TRACK_MISMATCH'
    }

    It 'TRACK_MISMATCH: pid 16 whose EIN ProPublica lists as a 501(c)(3)' {
        $app = New-Gate1App
        $app.verification.einIs501c3 = $true
        (Get-CharityLaneVerdict -Application $app -Context $script:Ctx).reason_codes | Should -Contain 'TRACK_MISMATCH'
    }

    It 'TRACK_MISMATCH: pid 33 reporting pre-501(c)(3)' {
        $app = New-Gate1App -ProductPid 33 -Override @{ 'What is the legal status of your organization?' = 'pre-501c(3) Nonprofit' }
        (Get-CharityLaneVerdict -Application $app -Context $script:Ctx).reason_codes | Should -Contain 'TRACK_MISMATCH'
    }

    It 'NEED_INFO for a missing legal status, ESCALATE for an unrecognized one' {
        $missing = New-Gate1App -Override @{ 'What is the legal status of your organization?' = '' }
        (Get-CharityLaneVerdict -Application $missing -Context $script:Ctx).reason_codes | Should -Contain 'LEGAL_STATUS_MISSING'
        $odd = New-Gate1App -Override @{ 'What is the legal status of your organization?' = 'Sole proprietorship' }
        $r = Get-CharityLaneVerdict -Application $odd -Context $script:Ctx
        $r.verdict | Should -Be 'ESCALATE'
        $r.reason_codes | Should -Contain 'LEGAL_STATUS_UNRECOGNIZED'
    }

    It 'NEED_INFO EIN_MISSING when neither the field nor the Candid link carries an EIN' {
        $app = New-Gate1App -Override @{ "What is your organization's EIN number?" = ''; 'What is your GuideStar.org URL?' = 'none.org' }
        $r = Get-CharityLaneVerdict -Application $app -Context $script:Ctx
        $r.verdict | Should -Be 'NEED_INFO'
        $r.reason_codes | Should -Contain 'EIN_MISSING'
    }

    It 'pid 33: ESCALATE EIN_NOT_501C3 when ProPublica lists a different subsection' {
        $app = New-Gate1App -ProductPid 33
        $app.verification.einIs501c3 = $false
        $app.verification.subsectionCode = 4
        $r = Get-CharityLaneVerdict -Application $app -Context $script:Ctx
        $r.verdict | Should -Be 'ESCALATE'
        $r.reason_codes | Should -Contain 'EIN_NOT_501C3'
    }

    It 'pid 33: ESCALATE EIN_NOT_VERIFIED when ProPublica did not find it or did not run' {
        $notFound = New-Gate1App -ProductPid 33
        $notFound.verification.einVerified = $false
        $notFound.verification.einIs501c3 = $null
        (Get-CharityLaneVerdict -Application $notFound -Context $script:Ctx).reason_codes | Should -Contain 'EIN_NOT_VERIFIED'
        $notRun = New-Gate1App -ProductPid 33 -Set @{ verification = $null }
        (Get-CharityLaneVerdict -Application $notRun -Context $script:Ctx).reason_codes | Should -Contain 'EIN_NOT_VERIFIED'
    }

    It 'pid 16 does not require ProPublica confirmation' {
        $app = New-Gate1App -Set @{ verification = $null }
        (Get-CharityLaneVerdict -Application $app -Context $script:Ctx).reason_codes | Should -Not -Contain 'EIN_NOT_VERIFIED'
    }

    It 'ESCALATEs an IRS name mismatch and a Candid/EIN mismatch' {
        $app = New-Gate1App -ProductPid 33
        $app.verification.nameMismatch = $true
        $app.verification.guidestarEinMismatch = $true
        $r = Get-CharityLaneVerdict -Application $app -Context $script:Ctx
        $r.reason_codes | Should -Contain 'EIN_NAME_MISMATCH'
        $r.reason_codes | Should -Contain 'CANDID_EIN_MISMATCH'
    }

    It "NEED_INFO ORG_NAME_IS_APPLICANT when the company name is the applicant's own" {
        $r = Get-CharityLaneVerdict -Application (New-Gate1App -Set @{ companyName = 'Jane Doe' }) -Context $script:Ctx
        $r.verdict | Should -Be 'NEED_INFO'
        $r.reason_codes | Should -Contain 'ORG_NAME_IS_APPLICANT'
        $r.org_name | Should -Be ''
    }

    It 'withholds a person-like company name without changing the verdict (INFO)' {
        $r = Get-CharityLaneVerdict -Application (New-Gate1App -Set @{ companyName = 'JOHN SMITH' }) -Context $script:Ctx
        $r.verdict | Should -Be 'ACCEPT'
        $r.reason_codes | Should -Contain 'ORG_NAME_WITHHELD'
        $r.org_name | Should -Be ''
    }

    It 'records a missing desired domain as INFO only' {
        $app = New-Gate1App -Drop @('What is your current/desired domain name?')
        $r = Get-CharityLaneVerdict -Application $app -Context $script:Ctx
        $r.verdict | Should -Be 'ACCEPT'
        $r.reason_codes | Should -Contain 'DESIRED_DOMAIN_MISSING'
        $r.desired_domain | Should -Be ''
    }

    It 'withholds a desired-domain answer that is not a hostname' {
        $app = New-Gate1App -Override @{ 'What is your current/desired domain name?' = 'jane.doe@example.com' }
        $r = Get-CharityLaneVerdict -Application $app -Context $script:Ctx
        $r.reason_codes | Should -Contain 'DESIRED_DOMAIN_INVALID'
        $r.desired_domain | Should -Be ''
    }

    It 'ESCALATEs an order that carries more than one lane item' {
        $r = Get-CharityLaneVerdict -Application (New-Gate1App -Set @{ orderLaneItemCount = 2 }) -Context $script:Ctx
        $r.reason_codes | Should -Contain 'ORDER_HAS_MULTIPLE_LANE_ITEMS'
    }

    It 'ESCALATEs a service with no custom fields at all' {
        $r = Get-CharityLaneVerdict -Application (New-Gate1App -Set @{ fields = @() }) -Context $script:Ctx
        $r.verdict | Should -Be 'ESCALATE'
        $r.reason_codes | Should -Contain 'NO_APPLICATION_FIELDS'
    }
}

Describe 'Get-CharityLaneVerdict - duplicates' {
    It 'ESCALATEs another Pending onboarding order on the same client' {
        $ctx = [pscustomobject]@{
            OnboardingServices = @($script:Ctx.OnboardingServices) + @(New-Svc -Id '5002' -OrderId '9002' -ClientId '701' -ProductPid 33 -Status 'Pending')
            WebsiteServices    = @()
        }
        $r = Get-CharityLaneVerdict -Application (New-Gate1App) -Context $ctx
        $r.verdict | Should -Be 'ESCALATE'
        $r.reason_codes | Should -Contain 'DUPLICATE_ORDER_SAME_CLIENT'
    }

    It 'ESCALATEs a client that already holds an Active onboarding service' {
        $ctx = [pscustomobject]@{
            OnboardingServices = @($script:Ctx.OnboardingServices) + @(New-Svc -Id '4000' -OrderId '7000' -ClientId '701' -ProductPid 16 -Status 'Active')
            WebsiteServices    = @()
        }
        (Get-CharityLaneVerdict -Application (New-Gate1App) -Context $ctx).reason_codes | Should -Contain 'CLIENT_ALREADY_ACTIVE'
    }

    It 'ESCALATEs another client applying for the same domain, but ignores a cancelled one' {
        $live = [pscustomobject]@{
            OnboardingServices = @($script:Ctx.OnboardingServices) + @(New-Svc -Id '5003' -OrderId '9003' -ClientId '999' -ProductPid 16 -Status 'Pending' -Domain 'brightfuture.org')
            WebsiteServices    = @()
        }
        (Get-CharityLaneVerdict -Application (New-Gate1App) -Context $live).reason_codes | Should -Contain 'DUPLICATE_DOMAIN_OTHER_CLIENT'
        $dead = [pscustomobject]@{
            OnboardingServices = @($script:Ctx.OnboardingServices) + @(New-Svc -Id '5003' -OrderId '9003' -ClientId '999' -ProductPid 16 -Status 'Cancelled' -Domain 'brightfuture.org')
            WebsiteServices    = @()
        }
        (Get-CharityLaneVerdict -Application (New-Gate1App) -Context $dead).reason_codes | Should -Not -Contain 'DUPLICATE_DOMAIN_OTHER_CLIENT'
    }

    It 'ESCALATEs a domain registered to another client' {
        $r = Get-CharityLaneVerdict -Application (New-Gate1App -Set @{ domainOwnerClientIds = @('701', '555') }) -Context $script:Ctx
        $r.reason_codes | Should -Contain 'DUPLICATE_DOMAIN_OTHER_CLIENT'
    }

    It 'ESCALATEs another client with the same org name' {
        (Get-CharityLaneVerdict -Application (New-Gate1App -Set @{ otherClientsSameOrg = 1 }) -Context $script:Ctx).reason_codes | Should -Contain 'DUPLICATE_ORG_OTHER_CLIENT'
    }

    It 'ESCALATEs DUPLICATE_CHECK_FAILED when a duplicate lookup did not complete' {
        $r = Get-CharityLaneVerdict -Application (New-Gate1App -Set @{ otherClientsSameOrg = $null; domainOwnerClientIds = $null }) -Context $script:Ctx
        $r.verdict | Should -Be 'ESCALATE'
        $r.reason_codes | Should -Contain 'DUPLICATE_CHECK_FAILED'
    }

    It 'normalizes org names for the duplicate key' {
        ConvertTo-LaneOrgKey -Name 'The Bright Future Foundation, Inc.' | Should -Be (ConvertTo-LaneOrgKey -Name 'bright future foundation')
        ConvertTo-LaneOrgKey -Name 'Hope & Light' | Should -Be (ConvertTo-LaneOrgKey -Name 'Hope and Light')
    }
}

Describe 'Get-CharityLaneVerdict - Gate 2 (pid 40)' {
    It "ACCEPTs when the client's own onboarding service is Active and the attestation is ticked" {
        $r = Get-CharityLaneVerdict -Application (New-Gate2App) -Context (New-Gate2Ctx)
        $r.verdict | Should -Be 'ACCEPT'
        $r.gate | Should -Be 2
        $r.desired_domain | Should -Be 'riverbendpantry.org'
        $r.org_name | Should -Be 'Riverbend Food Pantry Inc'
        $r.readiness_score | Should -BeNullOrEmpty
    }

    It 'ACCEPTs a numeric reference to the Gate 1 order id on the same client' {
        (Get-CharityLaneVerdict -Application (New-Gate2App -Reference '8001') -Context (New-Gate2Ctx)).verdict | Should -Be 'ACCEPT'
        (Get-CharityLaneVerdict -Application (New-Gate2App -Reference '#6001') -Context (New-Gate2Ctx)).verdict | Should -Be 'ACCEPT'
    }

    It 'NEED_INFO while the Gate 1 application is still Pending' {
        $r = Get-CharityLaneVerdict -Application (New-Gate2App) -Context (New-Gate2Ctx -Gate1Status 'Pending')
        $r.verdict | Should -Be 'NEED_INFO'
        $r.reason_codes | Should -Contain 'GATE1_SERVICE_PENDING'
    }

    It 'ESCALATEs a cancelled Gate 1 service' {
        $r = Get-CharityLaneVerdict -Application (New-Gate2App) -Context (New-Gate2Ctx -Gate1Status 'Cancelled')
        $r.verdict | Should -Be 'ESCALATE'
        $r.reason_codes | Should -Contain 'GATE1_SERVICE_INACTIVE'
    }

    It 'NEED_INFO when the client has no onboarding application at all' {
        $r = Get-CharityLaneVerdict -Application (New-Gate2App) -Context (New-Gate2Ctx -Gate1Client '999')
        $r.verdict | Should -Be 'NEED_INFO'
        $r.reason_codes | Should -Contain 'GATE1_SERVICE_NOT_FOUND'
    }

    It "ESCALATEs a numeric reference to another client's application" {
        $r = Get-CharityLaneVerdict -Application (New-Gate2App -Reference '8001') -Context (New-Gate2Ctx -Gate1Client '999')
        $r.verdict | Should -Be 'ESCALATE'
        $r.reason_codes | Should -Contain 'GATE2_REFERENCE_OTHER_CLIENT'
    }

    It 'ESCALATEs a numeric reference that matches nothing' {
        (Get-CharityLaneVerdict -Application (New-Gate2App -Reference '123456') -Context (New-Gate2Ctx)).reason_codes | Should -Contain 'GATE2_REFERENCE_NOT_FOUND'
    }

    It 'NEED_INFO when the approval attestation is not ticked' {
        $r = Get-CharityLaneVerdict -Application (New-Gate2App -Attest '') -Context (New-Gate2Ctx)
        $r.verdict | Should -Be 'NEED_INFO'
        $r.reason_codes | Should -Contain 'GATE2_ATTESTATION_MISSING'
    }

    It 'ESCALATEs a pid 40 order with neither proof field (possible 224 marker order)' {
        $r = Get-CharityLaneVerdict -Application (New-Gate2App -NoFields) -Context (New-Gate2Ctx)
        $r.verdict | Should -Be 'ESCALATE'
        $r.reason_codes | Should -Contain 'GATE2_FIELDS_ABSENT'
    }

    It 'ESCALATEs a client that already has an Active website service' {
        $ctx = New-Gate2Ctx -Website @(New-Svc -Id '6050' -OrderId '8050' -ClientId '801' -ProductPid 40 -Status 'Active')
        (Get-CharityLaneVerdict -Application (New-Gate2App) -Context $ctx).reason_codes | Should -Contain 'CLIENT_ALREADY_ACTIVE'
    }

    It 'ESCALATEs a non-lane product' {
        $app = New-Gate2App
        $app.pid = 39
        (Get-CharityLaneVerdict -Application $app -Context (New-Gate2Ctx)).reason_codes | Should -Contain 'UNSUPPORTED_PID'
    }
}

Describe 'PII-free output' {
    BeforeAll {
        $script:Rows = @(
            (Get-CharityLaneVerdict -Application (New-Gate1App) -Context $script:Ctx),
            (Get-CharityLaneVerdict -Application (New-Gate1App -Set @{ companyName = 'Jane Doe' }) -Context $script:Ctx),
            (Get-CharityLaneVerdict -Application (New-Gate2App) -Context (New-Gate2Ctx))
        )
        $script:Report = Get-LaneVerdictReport -Rows $script:Rows -RunUrl 'https://example.invalid/run/1' -GeneratedAt '2026-10-11T00:00:00Z'
        $script:Json = $script:Report | ConvertTo-Json -Depth 8
        $script:Md = Format-LaneVerdictMarkdown -Report $script:Report
    }

    It 'emits exactly the allowed row fields' {
        foreach ($r in $script:Rows) {
            @($r.PSObject.Properties.Name) | Should -Be @('order_id', 'client_id', 'pid', 'gate', 'org_name', 'desired_domain', 'verdict', 'reason_codes', 'readiness_score', 'fraud_check')
        }
    }

    It 'never writes applicant names, emails, phones, URLs or EINs: <Pattern>' -ForEach @(
        @{ Pattern = '\bJane\b' }, @{ Pattern = '\bDoe\b' }, @{ Pattern = '\bSam\b' }, @{ Pattern = '\bRoe\b' },
        @{ Pattern = 'example\.com' }, @{ Pattern = '555-123-4567' }, @{ Pattern = 'facebook\.com' },
        @{ Pattern = 'linkedin\.com' }, @{ Pattern = '12-?3456789' }, @{ Pattern = 'guidestar\.org' }
    ) {
        $script:Json | Should -Not -Match $Pattern
        $script:Md | Should -Not -Match $Pattern
    }

    It 'counts verdicts and sorts ESCALATE, NEED_INFO, ACCEPT' {
        $script:Report.counts.ACCEPT | Should -Be 2
        $script:Report.counts.NEED_INFO | Should -Be 1
        @($script:Report.verdicts)[0].verdict | Should -Be 'NEED_INFO'
        $script:Report.schema | Should -Be 'charity-lane-verdicts/v1'
        $script:Report.fraud_check | Should -Be 'not_run'
        $script:Report.reason_code_catalog.Contains('FRAUD_CHECK_NOT_RUN') | Should -BeTrue
    }

    It 'renders a summary table, and a plain line when nothing is pending' {
        $script:Md | Should -Match '\| Order \| Client \| pid \| Gate \|'
        $script:Md | Should -Match 'Bright Future Foundation'
        $empty = Format-LaneVerdictMarkdown -Report (Get-LaneVerdictReport -Rows @())
        $empty | Should -Match 'No Pending pid 16 / 33 / 40 orders\.'
    }
}
