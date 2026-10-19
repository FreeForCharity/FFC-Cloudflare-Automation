#requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0.0' }
<#
    Unit tests for scripts/whmcs-charity-lane-site-fields.ps1 (workflow 234).

    The script is dot-sourced (its runner sits below
    `if ($MyInvocation.InvocationName -eq '.') { return }`), so no WHMCS call
    is made: the API payloads are fixtures under
    tests/fixtures/charity-lane-site-fields/. The end-to-end cases copy the
    script next to a STUB whmcs-api-common.ps1 that serves those fixtures, so
    the real runner writes its real JSON + markdown with no network.

    What is pinned: allowlist enforcement (a personal, board, address or
    non-allowlisted field never appears even when WHMCS holds it), every
    validator, the fallback order client field -> product answer, HTML and
    control-character stripping, and the shipped allowlist itself.

    Run: Invoke-Pester tests/whmcs-charity-lane-site-fields.Tests.ps1
#>

BeforeAll {
    $script:RepoRoot = Join-Path $PSScriptRoot '..'
    $script:ScriptPath = Join-Path $script:RepoRoot 'scripts' 'whmcs-charity-lane-site-fields.ps1'
    $script:ConfigPath = Join-Path $script:RepoRoot 'config' 'charity-lane-site-fields.json'
    $script:FixtureDir = Join-Path $PSScriptRoot 'fixtures' 'charity-lane-site-fields'
    . $script:ScriptPath

    $script:RawConfig = Get-Content -Raw -LiteralPath $script:ConfigPath | ConvertFrom-Json
    $script:Config = Read-SiteFieldsConfig -ConfigObject $script:RawConfig
    $script:Detail = Get-Content -Raw -LiteralPath (Join-Path $script:FixtureDir 'GetClientsDetails.json') | ConvertFrom-Json
    $script:Products = Get-Content -Raw -LiteralPath (Join-Path $script:FixtureDir 'GetClientsProducts.json') | ConvertFrom-Json

    function Get-Field {
        param([string]$Key)
        return $script:Config.fields | Where-Object { $_.key -eq $Key } | Select-Object -First 1
    }

    function New-Set {
        param([int]$ServiceId = 1, [int]$ProductPid = 16, [string]$Status = 'Active', [System.Collections.IDictionary]$Answers = @{})
        $a = foreach ($k in $Answers.Keys) { [pscustomobject]@{ name = $k; value = [string]$Answers[$k] } }
        return [pscustomobject]@{ serviceId = $ServiceId; pid = $ProductPid; status = $Status; answers = @($a) }
    }

    function Resolve-Fixture {
        $client = Get-SiteFieldClientValues -Detail $script:Detail
        $sets = Get-SiteFieldProductSets -ProductsResponse $script:Products -Config $script:Config
        return Resolve-SiteFields -Config $script:Config -ClientValues $client.values -ProductSets $sets -ClientId 4242 -ClientFieldsNamed $client.named
    }

    function Copy-ConfigWith {
        param([scriptblock]$Mutate)
        $c = Get-Content -Raw -LiteralPath $script:ConfigPath | ConvertFrom-Json
        & $Mutate $c
        return $c
    }

    # Personal / board / address / non-allowlisted values present in the
    # fixtures. None may appear anywhere in the output.
    $script:Forbidden = @(
        'janet.applicantson@gmail.com', 'Janet', 'Applicantson', '555-867-5309', '867-5309',
        'Evergreen', '62701', 'Boardwalker', 'roberta.board@gmail.com', 'Carol Teamworth',
        'Danny Donorfield', 'food,pantry,seniors', 'SECRET123', 'linkedin.com/in/', '555-000-1111',
        'other@example.com', 'insecure.example.org', '217-555-0199', '<script', 'alert('
    )
}

Describe 'Shipped allowlist (config/charity-lane-site-fields.json)' {
    It 'loads and covers every publication field the brief needs' {
        $keys = @($script:Config.fields.key)
        foreach ($k in 'publicEmail', 'publicPhone', 'footerLocation', 'mission', 'tagline', 'siteDescription',
            'siteShortDescription', 'program1Title', 'program1Description', 'brandColor', 'logoUrl', 'facebookUrl',
            'linkedinUrl', 'instagramUrl', 'xUrl', 'youtubeUrl', 'donationUrl', 'volunteerUrl', 'foundingYear',
            'alternateNames', 'ein', 'candidProfileUrl', 'templateChoice', 'charityName', 'maintainerGithub',
            'domainOwnership', 'domainRegistrar', 'seoKeywords', 'leadershipLines', 'currentWebsiteUrl', 'leader1Name', 'leader2Name', 'leader3Name') {
            $keys | Should -Contain $k
        }
        $script:Config.productIds | Should -Be @(16, 33, 40)
    }

    It 'names no personal, board, team, contact or address field' {
        foreach ($f in $script:Config.fields) {
            foreach ($n in @($f.clientField) + $f.productFields) {
                $n | Should -Not -Match '(?i)mailing|address|contact|board|officer|team|testimonial|individual|first name|last name'
            }
        }
    }

    It 'agrees with the populate map wherever both name the same product field' {
        $map = Get-Content -Raw -LiteralPath (Join-Path $script:RepoRoot 'config' 'whmcs-client-field-populate-map.json') | ConvertFrom-Json
        $checked = 0
        foreach ($f in $script:Config.fields) {
            foreach ($slug in $f.productFields) {
                $p = $map.PSObject.Properties[$slug]
                if ($p) {
                    $p.Value | Should -Be $f.clientField -Because "populate map sends '$slug' to '$($p.Value)'"
                    $checked++
                }
            }
        }
        $checked | Should -BeGreaterThan 20
    }

    It 'does not take the public email from primary-contact-email (a personal contact)' {
        (Get-Field 'publicEmail').productFields | Should -Not -Contain 'primary-contact-email'
    }

    It 'refuses a config that adds the mailing address' {
        $c = Copy-ConfigWith { param($c) $c.fields += [pscustomobject]@{ key = 'mailingAddress'; clientField = 'Mailing address (full)'; productFields = @('mailing-address'); type = 'text' } }
        { Read-SiteFieldsConfig -ConfigObject $c } | Should -Throw '*denied*'
    }

    It 'refuses a config that adds a team-member or board field' {
        $c = Copy-ConfigWith { param($c) $c.fields += [pscustomobject]@{ key = 'team1Name'; clientField = 'Team member 1 - name'; productFields = @('team-1-name'); type = 'text' } }
        { Read-SiteFieldsConfig -ConfigObject $c } | Should -Throw '*denied*'
        $c = Copy-ConfigWith { param($c) $c.fields += [pscustomobject]@{ key = 'president'; clientField = 'Website footer note'; productFields = @('board-president-name'); type = 'text' } }
        { Read-SiteFieldsConfig -ConfigObject $c } | Should -Throw '*denied*'
    }

    It 'refuses a config with no denied-name guard, an unknown type, or a duplicate key' {
        $c = Copy-ConfigWith { param($c) $c.deniedNamePatterns = @() }
        { Read-SiteFieldsConfig -ConfigObject $c } | Should -Throw '*deniedNamePatterns*'
        $c = Copy-ConfigWith { param($c) $c.fields[0].type = 'raw' }
        { Read-SiteFieldsConfig -ConfigObject $c } | Should -Throw '*unknown type*'
        $c = Copy-ConfigWith { param($c) $c.fields[1].key = $c.fields[0].key }
        { Read-SiteFieldsConfig -ConfigObject $c } | Should -Throw '*duplicate key*'
    }
}

Describe 'Allowlist enforcement on the fixture client' {
    BeforeAll {
        $script:Result = Resolve-Fixture
        $script:Json = $script:Result | ConvertTo-Json -Depth 8
        $script:Md = Format-SiteFieldsMarkdown -Result $script:Result -Json $script:Json -RunUrl 'https://example.invalid/run/1'
    }

    It 'emits only allowlisted keys, and accounts for every allowlisted key' {
        $allowed = @($script:Config.fields.key)
        foreach ($k in $script:Result.fields.Keys) { $allowed | Should -Contain $k }
        foreach ($k in $script:Result.fieldSources.Keys) { $allowed | Should -Contain $k }
        (@($script:Result.fields.Keys) + @($script:Result.missing) | Sort-Object) | Should -Be ($allowed | Sort-Object)
    }

    It 'never prints a personal, board, address, non-allowlisted or rejected value (<_>)' -ForEach @(
        'janet.applicantson@gmail.com', 'Janet', 'Applicantson', '555-867-5309', '867-5309',
        'Evergreen', '62701', 'Boardwalker', 'roberta.board@gmail.com', 'Carol Teamworth',
        'Danny Donorfield', 'food,pantry,seniors', 'SECRET123', 'linkedin.com/in/', '555-000-1111',
        'other@example.com', 'insecure.example.org', '217-555-0199', '<script', 'alert('
    ) {
        $script:Json | Should -Not -BeLike "*$_*"
        $script:Md | Should -Not -BeLike "*$_*"
    }

    It 'resolves the public fields from the right source' {
        $f = $script:Result.fields
        $f.publicEmail | Should -Be 'info@brightfuture.org'
        $f.publicPhone | Should -Be '(555) 010-2000'
        $f.footerLocation | Should -Be 'Springfield, IL'
        $f.mission | Should -Be 'We feed families & seniors in Sangamon County.'
        $f.brandColor | Should -Be '#0567B1'
        $f.logoUrl | Should -Be 'https://cdn.example.org/bff/logo.png'
        $f.facebookUrl | Should -Be 'https://www.facebook.com/brightfuture'
        $f.ein | Should -Be '12-3456789'
        $f.candidProfileUrl | Should -Be 'https://www.guidestar.org/profile/12-3456789'
        $f.volunteerUrl | Should -Be 'https://www.idealist.org/en/nonprofit/bright-future'
        $f.foundingYear | Should -Be '2015'
        $f.alternateNames | Should -Be @('BFF', 'Bright Future')
        $f.templateChoice | Should -Be 'Single Page'
        $f.siteDescription | Should -Be "Bright Future runs a weekly food pantry`nand a senior meal delivery route."
        $script:Result.fieldSources.publicEmail | Should -Be 'product:pid16:service501'
        $script:Result.fieldSources.publicPhone | Should -Be 'client_field'
        $script:Result.fieldSources.tagline | Should -Be 'product:pid40:service777'
    }

    It 'lists unsupplied and rejected fields as missing, with reason codes only' {
        $m = $script:Result.missing
        foreach ($k in 'linkedinUrl', 'donationUrl', 'instagramUrl', 'xUrl', 'youtubeUrl', 'program2Title', 'siteShortDescription') {
            $m | Should -Contain $k
        }
        $li = $script:Result.rejected | Where-Object { $_.field -eq 'linkedinUrl' }
        $li.reasons | Should -Contain 'product:pid16:url_path_not_allowed'
        $don = $script:Result.rejected | Where-Object { $_.field -eq 'donationUrl' }
        $don.reasons | Should -Contain 'product:pid40:url_query_secret'
    }

    It 'redacts contact details typed into free text and says so' {
        $script:Result.fields.program1Description | Should -Be 'Open every Saturday. Questions? Call [removed] or email [removed].'
        $script:Result.redacted | Should -Contain 'program1Description'
    }

    It 'skips Cancelled services and non-lane products' {
        $ids = @($script:Result.sources.services | ForEach-Object { $_.serviceId })
        $ids | Should -Be @(777, 501)
    }

    It 'renders the marker, the missing list and the JSON block' {
        # .Contains, not -BeLike: a backtick is the wildcard escape character.
        $script:Md.StartsWith('<!-- charity-lane:site-fields -->') | Should -BeTrue
        $script:Md.Contains('**Missing (mark `pending`):**') | Should -BeTrue
        $script:Md.Contains('`linkedinUrl`') | Should -BeTrue
        $script:Md.Contains("``````json`n") | Should -BeTrue
        $script:Md.Contains('"publicEmail": "info@brightfuture.org"') | Should -BeTrue
        $script:Md.Contains('Run: https://example.invalid/run/1') | Should -BeTrue
    }
}

Describe 'Fallback order: client field -> product answer' {
    It 'a valid client field beats the product answer' {
        $r = Resolve-SiteFields -Config $script:Config -ClientValues @{ 'site tagline' = 'From the client field' } -ProductSets @(New-Set -ProductPid 40 -Answers @{ 'tagline|Site tagline' = 'From the product' }) -ClientId 1
        $r.fields.tagline | Should -Be 'From the client field'
        $r.fieldSources.tagline | Should -Be 'client_field'
    }

    It 'an empty client field falls back to the product answer' {
        $r = Resolve-SiteFields -Config $script:Config -ClientValues @{ 'site tagline' = '  ' } -ProductSets @(New-Set -ProductPid 40 -Answers @{ 'tagline|Site tagline' = 'From the product' }) -ClientId 1
        $r.fields.tagline | Should -Be 'From the product'
    }

    It 'an invalid client field falls back too, and the rejection is recorded' {
        $r = Resolve-SiteFields -Config $script:Config -ClientValues @{ 'brand color (hex)' = 'navy blue' } -ProductSets @(New-Set -ProductPid 40 -Answers @{ 'brand-color' = '#112233' }) -ClientId 1
        $r.fields.brandColor | Should -Be '#112233'
        $r.rejected | Should -BeNullOrEmpty
        $r2 = Resolve-SiteFields -Config $script:Config -ClientValues @{ 'brand color (hex)' = 'navy blue' } -ProductSets @() -ClientId 1
        $r2.missing | Should -Contain 'brandColor'
        ($r2.rejected | Where-Object { $_.field -eq 'brandColor' }).reasons | Should -Be @('client_field:not_a_hex_color')
        ($r2 | ConvertTo-Json -Depth 8) | Should -Not -BeLike '*navy blue*'
    }

    It 'Active services outrank others, then the newest service wins' {
        $c = Get-SiteFieldProductSets -Config $script:Config -ProductsResponse ([pscustomobject]@{
                products = [pscustomobject]@{ product = @(
                        [pscustomobject]@{ id = '10'; pid = '40'; status = 'Active'; customfields = [pscustomobject]@{ customfield = @([pscustomobject]@{ name = 'tagline'; value = 'old active' }) } }
                        [pscustomobject]@{ id = '30'; pid = '40'; status = 'Pending'; customfields = [pscustomobject]@{ customfield = @([pscustomobject]@{ name = 'tagline'; value = 'newest pending' }) } }
                        [pscustomobject]@{ id = '20'; pid = '40'; status = 'Active'; customfields = [pscustomobject]@{ customfield = @([pscustomobject]@{ name = 'tagline'; value = 'new active' }) } }
                        [pscustomobject]@{ id = '40'; pid = '40'; status = 'Terminated'; customfields = [pscustomobject]@{ customfield = @([pscustomobject]@{ name = 'tagline'; value = 'terminated' }) } }
                    )
                }
            })
        @($c.serviceId) | Should -Be @(20, 10, 30)
        $r = Resolve-SiteFields -Config $script:Config -ClientValues @{} -ProductSets $c -ClientId 1
        $r.fields.tagline | Should -Be 'new active'
    }

    It 'client custom fields returned WITHOUT names are never used' {
        $d = [pscustomobject]@{ customfields = [pscustomobject]@{ customfield = @(
                    [pscustomobject]@{ id = '11'; value = 'jane@gmail.com' }
                    [pscustomobject]@{ id = '12'; value = 'Our tagline' }
                )
            }
        }
        $cv = Get-SiteFieldClientValues -Detail $d
        $cv.named | Should -BeFalse
        $cv.values.Count | Should -Be 0
        $r = Resolve-SiteFields -Config $script:Config -ClientValues $cv.values -ProductSets @() -ClientId 1 -ClientFieldsNamed $cv.named
        $r.fields.Count | Should -Be 0
        $r.sources.clientFieldsNamed | Should -BeFalse
        (Format-SiteFieldsMarkdown -Result $r -Json '{}') | Should -BeLike '*without names*'
    }

    It 'a machine-name match is tried before a legacy label match' {
        $set = New-Set -Answers ([ordered]@{ "What is your organization's EIN number?" = '98-7654321'; 'ein|EIN' = '12-3456789' })
        $r = Resolve-SiteFields -Config $script:Config -ClientValues @{} -ProductSets @($set) -ClientId 1
        $r.fields.ein | Should -Be '12-3456789'
    }

    It 'a legacy label match on a board / contact field is never a candidate' {
        $set = New-Set -Answers @{ 'Board member mission alignment statement' = 'Roberta has served since 2019.'; 'Primary Contact EIN note' = '12-3456789' }
        $r = Resolve-SiteFields -Config $script:Config -ClientValues @{} -ProductSets @($set) -ClientId 1
        $r.missing | Should -Contain 'mission'
        $r.missing | Should -Contain 'ein'
    }

    It 'an invalid first candidate does not stop a valid later one' {
        $set = New-Set -Answers ([ordered]@{ 'Do you have an EIN?' = 'Yes'; "What is your organization's EIN number?" = '12 3456789'; 'EIN (IRS tax ID)' = '12-3456789' })
        $r = Resolve-SiteFields -Config $script:Config -ClientValues @{} -ProductSets @($set) -ClientId 1
        $r.fields.ein | Should -Be '12-3456789'
    }
}

Describe 'Live pid 33 display labels (GetClientsProducts returns the label, HTML-encoded)' {
    BeforeAll {
        # Every question on a real pid 33 service as the live API names it
        # (display label only, no "machine|" prefix, entities left encoded).
        # The labels are the public application form; the values are synthetic.
        $script:LiveAnswers = [ordered]@{
            'Organization&#039;s legal name (exactly as on your IRS letter or state filing)' = 'Bright Future Fund'
            'What is your organization&#039;s mission? (one or two plain sentences)'         = 'We feed families.'
            'What is your organization&#039;s EIN? (format NN-NNNNNNN)'                      = '12-3456789'
            'Public email address for your website footer'                                   = 'Info@BrightFuture.org'
            'Public phone number for your website footer'                                    = '(555) 010-2000'
            'City &amp; state to show in your website footer'                                = 'Shaw AFB, SC'
            'Instagram URL (optional)'                                                       = 'https://www.instagram.com/brightfuture'
            'YouTube URL (optional)'                                                         = 'https://www.youtube.com/@brightfuture'
            'X (Twitter) URL (optional)'                                                     = 'https://x.com/brightfuture'
            'Charity Facebook Page URL'                                                      = 'https://www.facebook.com/brightfuture'
            'Site tagline (short slogan)'                                                    = 'Food for every table'
            'Site description (one or two sentences)'                                        = 'A weekly food pantry.'
            'Short description (under 200 characters)'                                       = 'Weekly pantry.'
            'Program 1 - title'                                                              = 'Pantry'
            'Program 1 - description'                                                        = 'Open Saturdays.'
            'Program 2 - title (optional)'                                                   = 'Meals'
            'Program 2 - description (optional)'                                             = 'Senior meal route.'
            'Program 3 - title (optional)'                                                   = 'Garden'
            'Program 3 - description (optional)'                                             = 'Community garden.'
            'Brand color (hex preferred, e.g. #0567B1)'                                      = '#0567B1'
            'Logo image URL (optional)'                                                      = 'https://cdn.example.org/logo.png'
            'Zeffy donation form URL'                                                        = 'https://www.zeffy.com/en-US/donation-form/bff'
            'Idealist volunteer page URL'                                                    = 'https://www.idealist.org/en/nonprofit/bff'
            'Year founded'                                                                   = '2015'
            'Also known as / abbreviations (optional)'                                       = 'BFF'
            'SEO keywords (comma-separated)'                                                 = 'food pantry, meals'
            'Which starting point fits your charity?'                                        = 'Single Page'
            'Do you already own this domain?'                                                = 'Yes'
            'Where is your domain registered? (if you own it)'                               = 'Cloudflare'
            'Current public website or page, if any (even without your own domain)'          = 'https://brightfuture.example.org'
            'Other leaders to show on your website (optional, one per line: Name - Role)'    = 'Pat Example - Volunteer Lead'
            'Public GuideStar Profile Link'                                                  = 'https://www.guidestar.org/profile/12-3456789'
            # Personal / board / contact questions: none of these values may surface.
            'Primary Contact Individual Email'                                               = 'janet.applicantson@gmail.com'
            'Primary Contact Phone Number (###) ###-####'                                    = '555-867-5309'
            'Primary Contact LinkedIn Link e.g. (https://www.linkedin.com/in/yourname/)'     = 'https://www.linkedin.com/in/janet'
            'Technical Contact Individual Email'                                             = 'tech.person@gmail.com'
            'Technical Contact Phone Number (###) ###-####'                                  = '555-000-1111'
            'Technical Contact GitHub username (your website maintainer)'                    = 'techperson'
            'Board President/Chair Individual Email'                                         = 'roberta.board@gmail.com'
            'Board President/Chair Phone Number (###) ###-####'                              = '217-555-0199'
            'Board Chair / President - full name to show on your website'                    = 'Roberta Boardwalker'
            'Board Treasurer - full name to show on your website'                            = 'Carol Teamworth'
            'Best times to contact?'                                                         = 'Evenings 555-222-3333'
        }
        $set = New-Set -ProductPid 33 -ServiceId 680 -Answers $script:LiveAnswers
        $script:LiveResult = Resolve-SiteFields -Config $script:Config -ClientValues @{} -ProductSets @($set) -ClientId 499 -ClientFieldsNamed $false
        $script:LiveJson = $script:LiveResult | ConvertTo-Json -Depth 8
    }

    It 'fills the footer email, phone and city & state from their labels' {
        $script:LiveResult.fields.publicEmail | Should -Be 'info@brightfuture.org'
        $script:LiveResult.fields.publicPhone | Should -Be '(555) 010-2000'
        $script:LiveResult.fields.footerLocation | Should -Be 'Shaw AFB, SC'
        $script:LiveResult.fieldSources.footerLocation | Should -Be 'product:pid33:service680'
    }

    It 'fills the "(optional)" social URLs' {
        $script:LiveResult.fields.instagramUrl | Should -Be 'https://www.instagram.com/brightfuture'
        $script:LiveResult.fields.youtubeUrl | Should -Be 'https://www.youtube.com/@brightfuture'
        $script:LiveResult.fields.xUrl | Should -Be 'https://x.com/brightfuture'
    }

    It 'decodes &#039; so the legal-name label matches' {
        $script:LiveResult.fields.charityName | Should -Be 'Bright Future Fund'
    }

    It 'fills every allowlisted site-content field the live form asks for' {
        foreach ($k in 'mission', 'ein', 'facebookUrl', 'candidProfileUrl', 'tagline', 'siteDescription', 'siteShortDescription',
            'program1Title', 'program1Description', 'program2Title', 'program2Description', 'program3Title', 'program3Description',
            'brandColor', 'logoUrl', 'donationUrl', 'volunteerUrl', 'foundingYear', 'alternateNames', 'seoKeywords',
            'templateChoice', 'domainOwnership', 'domainRegistrar', 'currentWebsiteUrl', 'leadershipLines') {
            $script:LiveResult.missing | Should -Not -Contain $k
        }
    }

    It 'leaves the board / contact-labelled fields missing (the deny guard still holds)' {
        foreach ($k in 'maintainerGithub', 'leader1Name', 'leader2Name', 'leader3Name') {
            $script:LiveResult.missing | Should -Contain $k
        }
    }

    It 'never prints a personal, board or contact value (<_>)' -ForEach @(
        'janet.applicantson', '555-867-5309', 'linkedin.com/in/', 'tech.person', '555-000-1111', 'techperson',
        'roberta.board', '217-555-0199', 'Boardwalker', 'Teamworth', '555-222-3333'
    ) {
        $script:LiveJson | Should -Not -BeLike "*$_*"
    }

    It 'a broad pattern still cannot reach a contact, board or mailing-address email' {
        $c = Read-SiteFieldsConfig -ConfigObject (Copy-ConfigWith { param($c) ($c.fields | Where-Object { $_.key -eq 'publicEmail' }).productLabelPatterns = @('e-?mail') })
        $set = New-Set -ProductPid 33 -Answers ([ordered]@{
                'Primary Contact Individual Email'       = 'janet.applicantson@gmail.com'
                'Board President/Chair Individual Email' = 'roberta.board@gmail.com'
                'Your mailing address or email address'  = 'other@example.com'
            })
        $r = Resolve-SiteFields -Config $c -ClientValues @{} -ProductSets @($set) -ClientId 1
        $r.missing | Should -Contain 'publicEmail'
    }
}

Describe 'Label normalisation and the label deny guard' {
    It '<raw> -> <want>' -ForEach @(
        @{ raw = 'City &amp; state to show in your website footer'; want = 'City & state to show in your website footer' }
        @{ raw = 'Organization&#039;s legal name'; want = "Organization's legal name" }
        @{ raw = "Organization$([char]0x2019)s legal name"; want = "Organization's legal name" }
        @{ raw = 'Instagram URL (optional)'; want = 'Instagram URL' }
        @{ raw = 'YouTube URL  (Optional) '; want = 'YouTube URL' }
        @{ raw = 'Program 2 - title (optional, if any)'; want = 'Program 2 - title (optional, if any)' }
        @{ raw = "  Public   phone`tnumber "; want = 'Public phone number' }
    ) {
        Get-SiteFieldLabelText $raw | Should -Be $want
    }

    It 'lets "public email address" past the address fragment' {
        Test-SiteFieldDeniedLabel -Label 'Public email address for your website footer' -DeniedPatterns $script:Config.deniedNamePatterns | Should -BeFalse
        Test-SiteFieldDeniedLabel -Label 'Public e-mail address for your website footer' -DeniedPatterns $script:Config.deniedNamePatterns | Should -BeFalse
    }

    It 'still refuses <_>' -ForEach @(
        'Your email address', 'Contact email address', 'Mailing address', 'Public email address and mailing address',
        'Board Treasurer public email address', 'Public email address of the primary contact', 'Street address'
    ) {
        Test-SiteFieldDeniedLabel -Label $_ -DeniedPatterns $script:Config.deniedNamePatterns | Should -BeTrue
    }
}

Describe 'Validators' {
    Context 'email' {
        It 'accepts and lower-cases <_>' -ForEach @('Info@Charity.org', 'hello+web@sub.charity.org') {
            (Test-SiteFieldValue -Value $_ -Field (Get-Field 'publicEmail')).value | Should -Be $_.ToLowerInvariant()
        }
        It 'rejects <_>' -ForEach @('info at charity.org', 'info@charity', 'a@b@c.org', 'info@charity.org extra') {
            $r = Test-SiteFieldValue -Value $_ -Field (Get-Field 'publicEmail')
            $r.ok | Should -BeFalse
            $r.Keys | Should -Not -Contain 'value'
        }
    }

    Context 'phone' {
        It 'accepts <_>' -ForEach @('(555) 010-2000', '+1 555.010.2000', '555-0100') {
            (Test-SiteFieldValue -Value $_ -Field (Get-Field 'publicPhone')).ok | Should -BeTrue
        }
        It 'rejects <v> (<why>)' -ForEach @(
            @{ v = '555-01'; why = 'phone_digit_count' }
            @{ v = ('5' * 31); why = 'too_long' }
            @{ v = '1234567890123456789012'; why = 'phone_digit_count' }
            @{ v = '+1 (555) 010-2000 x 12 ext'; why = 'not_a_phone' }
            @{ v = 'call me'; why = 'not_a_phone' }
        ) {
            (Test-SiteFieldValue -Value $v -Field (Get-Field 'publicPhone')).reason | Should -Be $why
        }
        It 'counts digits, not characters (21 digits is too many)' {
            $f = (Get-Field 'publicPhone').PSObject.Copy()
            $f.maxLength = 0
            (Test-SiteFieldValue -Value '123456789012345678901' -Field $f).reason | Should -Be 'phone_digit_count'
        }
    }

    Context 'footer location' {
        It 'accepts city & state' {
            (Test-SiteFieldValue -Value 'Springfield, IL' -Field (Get-Field 'footerLocation')).value | Should -Be 'Springfield, IL'
            (Test-SiteFieldValue -Value 'Springfield, IL 62701' -Field (Get-Field 'footerLocation')).ok | Should -BeTrue
        }
        It 'rejects a street address: <_>' -ForEach @('742 Evergreen Terrace, Springfield, IL', 'PO Box 12, Springfield', 'Springfield, Suite 4', 'info@charity.org') {
            (Test-SiteFieldValue -Value $_ -Field (Get-Field 'footerLocation')).ok | Should -BeFalse
        }
        It 'rejects an over-long value' {
            (Test-SiteFieldValue -Value ('A' * 81) -Field (Get-Field 'footerLocation')).reason | Should -Be 'too_long'
        }
    }

    Context 'hex color' {
        It '<v> -> <want>' -ForEach @(
            @{ v = '#0567b1'; want = '#0567B1' }
            @{ v = '0567B1'; want = '#0567B1' }
            @{ v = '#fff'; want = '#FFF' }
        ) {
            (Test-SiteFieldValue -Value $v -Field (Get-Field 'brandColor')).value | Should -Be $want
        }
        It 'rejects <_>' -ForEach @('navy', '#12345', '#GGGGGG', 'rgb(1,2,3)') {
            (Test-SiteFieldValue -Value $_ -Field (Get-Field 'brandColor')).reason | Should -Be 'not_a_hex_color'
        }
    }

    Context 'URLs' {
        It 'accepts an https URL on an allowed host, including a subdomain' {
            (Test-SiteFieldValue -Value 'https://m.facebook.com/brightfuture' -Field (Get-Field 'facebookUrl')).ok | Should -BeTrue
            (Test-SiteFieldValue -Value 'https://www.linkedin.com/company/bright-future' -Field (Get-Field 'linkedinUrl')).ok | Should -BeTrue
            (Test-SiteFieldValue -Value 'https://www.zeffy.com/en-US/donation-form/bff?utm_source=site' -Field (Get-Field 'donationUrl')).ok | Should -BeTrue
        }
        It 'rejects <v> (<why>)' -ForEach @(
            @{ k = 'logoUrl'; v = 'http://cdn.example.org/logo.png'; why = 'url_not_https' }
            @{ k = 'logoUrl'; v = 'javascript:alert(1)'; why = 'url_not_https' }
            @{ k = 'logoUrl'; v = 'https://user:pass@cdn.example.org/logo.png'; why = 'url_has_credentials' }
            @{ k = 'logoUrl'; v = 'https://cdn.example.org/logo.png?sig=abc'; why = 'url_query_secret' }
            @{ k = 'logoUrl'; v = 'https://cdn.example.org/a b.png'; why = 'url_has_whitespace' }
            @{ k = 'logoUrl'; v = 'not a url'; why = 'url_has_whitespace' }
            @{ k = 'logoUrl'; v = 'https://localhost/logo.png'; why = 'url_bad_host' }
            @{ k = 'donationUrl'; v = 'https://www.zeffy.com/form?access_token=x'; why = 'url_query_secret' }
            @{ k = 'facebookUrl'; v = 'https://facebook.com.evil.example/page'; why = 'url_host_not_allowed' }
            @{ k = 'facebookUrl'; v = 'https://notfacebook.com/page'; why = 'url_host_not_allowed' }
            @{ k = 'linkedinUrl'; v = 'https://www.linkedin.com/in/some-person'; why = 'url_path_not_allowed' }
            @{ k = 'candidProfileUrl'; v = 'https://www.example.org/profile/12-3456789'; why = 'url_host_not_allowed' }
        ) {
            (Test-SiteFieldValue -Value $v -Field (Get-Field $k)).reason | Should -Be $why
        }
        It 'unwraps the anchor tag WHMCS puts around URL answers' {
            $v = '<a href="https://www.instagram.com/bff" target="_blank">https://www.instagram.com/bff</a>'
            (Test-SiteFieldValue -Value $v -Field (Get-Field 'instagramUrl')).value | Should -Be 'https://www.instagram.com/bff'
        }
    }

    Context 'year, EIN, choice, names' {
        It 'year: accepts 4 digits up to this year' {
            (Test-SiteFieldValue -Value '1998' -Field (Get-Field 'foundingYear')).value | Should -Be '1998'
            (Test-SiteFieldValue -Value '98' -Field (Get-Field 'foundingYear')).reason | Should -Be 'not_a_year'
            (Test-SiteFieldValue -Value 'founded 1998' -Field (Get-Field 'foundingYear')).reason | Should -Be 'not_a_year'
            $next = [string]((Get-Date).ToUniversalTime().Year + 1)
            (Test-SiteFieldValue -Value $next -Field (Get-Field 'foundingYear')).reason | Should -Be 'year_out_of_range'
        }
        It 'EIN: normalises to NN-NNNNNNN' {
            (Test-SiteFieldValue -Value '123456789' -Field (Get-Field 'ein')).value | Should -Be '12-3456789'
            (Test-SiteFieldValue -Value '12-3456789' -Field (Get-Field 'ein')).value | Should -Be '12-3456789'
            (Test-SiteFieldValue -Value '1-23456789' -Field (Get-Field 'ein')).reason | Should -Be 'not_an_ein'
            (Test-SiteFieldValue -Value 'pending' -Field (Get-Field 'ein')).reason | Should -Be 'not_an_ein'
        }
        It 'template choice: short plain text only' {
            (Test-SiteFieldValue -Value 'Single Page' -Field (Get-Field 'templateChoice')).value | Should -Be 'Single Page'
            (Test-SiteFieldValue -Value 'Single Page {{x}}' -Field (Get-Field 'templateChoice')).reason | Should -Be 'not_a_choice'
            (Test-SiteFieldValue -Value ('x' * 61) -Field (Get-Field 'templateChoice')).reason | Should -Be 'too_long'
        }
        It 'maintainer GitHub user: bare, @-prefixed or profile URL; anything else refused' {
            $f = Get-Field 'maintainerGithub'
            (Test-SiteFieldValue -Value 'jane-doe' -Field $f).value | Should -Be 'jane-doe'
            (Test-SiteFieldValue -Value '@jane-doe' -Field $f).value | Should -Be 'jane-doe'
            (Test-SiteFieldValue -Value 'https://github.com/jane-doe/' -Field $f).value | Should -Be 'jane-doe'
            (Test-SiteFieldValue -Value 'jane doe' -Field $f).reason | Should -Be 'not_a_github_user'
            (Test-SiteFieldValue -Value 'jane@example.org' -Field $f).reason | Should -Be 'not_a_github_user'
            (Test-SiteFieldValue -Value '-jane' -Field $f).reason | Should -Be 'not_a_github_user'
            (Test-SiteFieldValue -Value ('a' * 40) -Field $f).reason | Should -Be 'not_a_github_user'
        }
        It 'domain ownership: the dropdown answers pass as a choice' {
            $f = Get-Field 'domainOwnership'
            (Test-SiteFieldValue -Value 'Yes - and it has a live website' -Field $f).value | Should -Be 'Yes - and it has a live website'
            (Test-SiteFieldValue -Value 'No - please register it for us' -Field $f).value | Should -Be 'No - please register it for us'
            (Test-SiteFieldValue -Value 'Yes' -Field $f).value | Should -Be 'Yes'
        }
        It 'current website: a free-host page without its own domain is accepted' {
            (Test-SiteFieldValue -Value 'https://hope-pantry.my.canva.site/' -Field (Get-Field 'currentWebsiteUrl')).ok | Should -BeTrue
            (Test-SiteFieldValue -Value 'https://brand.page/hopepantry' -Field (Get-Field 'currentWebsiteUrl')).ok | Should -BeTrue
        }
        It 'SEO keywords: split on commas and capped' {
            $r = Test-SiteFieldValue -Value 'food pantry, seniors, Raleigh, food pantry' -Field (Get-Field 'seoKeywords')
            $r.value | Should -Be @('food pantry', 'seniors', 'Raleigh')
        }
        It 'leadership lines: kept as text, embedded contact details redacted' {
            $r = Test-SiteFieldValue -Value "Jane Doe - Board President`nSam Roe - Treasurer, sam@example.org" -Field (Get-Field 'leadershipLines')
            $r.value | Should -Match 'Jane Doe - Board President'
            $r.value | Should -Not -Match 'sam@example.org'
            $r.redacted | Should -BeTrue
        }
        It 'alternate names: split, de-duplicated, capped, contact-looking entries dropped' {
            $r = Test-SiteFieldValue -Value 'BFF; Bright Future, BFF, jane@gmail.com, A, B, C, D, E' -Field (Get-Field 'alternateNames')
            $r.value | Should -Be @('BFF', 'Bright Future', 'A', 'B', 'C')
            $r.redacted | Should -BeTrue
        }
    }

    Context 'free text: HTML, control characters, length' {
        It 'strips tags, script blocks and decoded tags' {
            $v = '<p>Feed <b>everyone</b></p><script>alert(1)</script> &lt;img src=x onerror=alert(2)&gt;now'
            (Test-SiteFieldValue -Value $v -Field (Get-Field 'tagline')).value | Should -Be 'Feed everyone now'
        }
        It 'removes control, zero-width and bidi-override characters' {
            $v = "Feed`0every" + [char]0x202E + 'one' + [char]0x200B + "`tnow`a"
            (Test-SiteFieldValue -Value $v -Field (Get-Field 'tagline')).value | Should -Be 'Feedeveryone now'
        }
        It 'keeps paragraph breaks only where the field is multiline' {
            (Test-SiteFieldValue -Value "One<br>Two`r`n`r`n`r`nThree" -Field (Get-Field 'mission')).value | Should -Be "One`nTwo`n`nThree"
            (Test-SiteFieldValue -Value "One<br>Two`r`nThree" -Field (Get-Field 'tagline')).value | Should -Be 'One Two Three'
        }
        It 'caps the length on a word boundary and flags it' {
            $long = ((1..60) | ForEach-Object { 'word' }) -join ' '
            $r = Test-SiteFieldValue -Value $long -Field (Get-Field 'tagline')
            $r.value.Length | Should -BeLessOrEqual 150
            $r.value | Should -Match 'word$'
            $r.truncated | Should -BeTrue
        }
        It 'redacts an embedded email or 10+ digit phone, but keeps an EIN and a year range' {
            $r = Test-SiteFieldValue -Value 'EIN 12-3456789, active 2010-2020. Call (217) 555-0199 or write a.b@gmail.com.' -Field (Get-Field 'mission')
            $r.value | Should -Be 'EIN 12-3456789, active 2010-2020. Call [removed] or write [removed].'
            $r.redacted | Should -BeTrue
        }
        It 'treats an all-markup value as empty' {
            (Test-SiteFieldValue -Value '<br /> <p></p>' -Field (Get-Field 'mission')).reason | Should -Be 'empty'
        }
    }
}

Describe 'Runner, end to end against fixtures (no network)' {
    BeforeAll {
        $script:Work = Join-Path ([System.IO.Path]::GetTempPath()) ("site-fields-" + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path (Join-Path $script:Work 'scripts') -Force | Out-Null
        New-Item -ItemType Directory -Path (Join-Path $script:Work 'config') -Force | Out-Null
        Copy-Item $script:ScriptPath (Join-Path $script:Work 'scripts')
        Copy-Item $script:ConfigPath (Join-Path $script:Work 'config')
        # Stub of whmcs-api-common.ps1: same function names, fixture answers,
        # and a log of every action requested.
        $stub = @'
function Resolve-WhmcsCredentials { param($IdentifierParam, $SecretParam, $CredentialsJsonParam) @{ Identifier = 'id'; Secret = 'sec' } }
function Resolve-WhmcsApiUrl { param($ApiUrlParam) if ($ApiUrlParam) { $ApiUrlParam } else { 'https://apim-ffc-gateway-prod.azure-api.net/whmcs/api.php' } }
function Resolve-WhmcsAccessKey { param($AccessKeyParam) $null }
function New-WhmcsAuthBody { param($Creds, $AccessKey) @{ identifier = $Creds.Identifier; secret = $Creds.Secret; responsetype = 'json' } }
function Invoke-WhmcsApi {
    param([string]$ApiUrl, [hashtable]$Body)
    Add-Content -LiteralPath $env:STUB_LOG -Value "$($Body.action) clientid=$($Body.clientid)"
    Get-Content -Raw -LiteralPath (Join-Path $env:STUB_FIXTURES "$($Body.action).json") | ConvertFrom-Json
}
'@
        Set-Content -LiteralPath (Join-Path $script:Work 'scripts' 'whmcs-api-common.ps1') -Value $stub
        $script:Log = Join-Path $script:Work 'api.log'

        function Invoke-Runner {
            param([string]$ClientId)
            $env:STUB_LOG = $script:Log
            $env:STUB_FIXTURES = $script:FixtureDir
            Push-Location $script:Work
            try {
                $out = & pwsh -NoProfile -File (Join-Path $script:Work 'scripts' 'whmcs-charity-lane-site-fields.ps1') -ClientId $ClientId -RunUrl 'https://example.invalid/run/2' 2>&1
                return @{ out = ($out -join "`n"); rc = $LASTEXITCODE }
            }
            finally { Pop-Location }
        }
    }

    AfterAll {
        Remove-Item -LiteralPath $script:Work -Recurse -Force -ErrorAction SilentlyContinue
    }

    It 'writes the JSON artifact and markdown, with only read actions and no forbidden value' {
        $r = Invoke-Runner -ClientId '4242'
        $r.rc | Should -Be 0 -Because $r.out
        $json = Get-Content -Raw -LiteralPath (Join-Path $script:Work 'artifacts' 'whmcs' 'charity-lane-site-fields.json')
        $md = Get-Content -Raw -LiteralPath (Join-Path $script:Work 'artifacts' 'whmcs' 'charity-lane-site-fields.md')
        ($json | ConvertFrom-Json).fields.publicEmail | Should -Be 'info@brightfuture.org'
        $md | Should -BeLike '<!-- charity-lane:site-fields -->*'
        foreach ($bad in $script:Forbidden) {
            $json | Should -Not -BeLike "*$bad*"
            $md | Should -Not -BeLike "*$bad*"
            $r.out | Should -Not -BeLike "*$bad*"
        }
        $actions = Get-Content -LiteralPath $script:Log
        $actions | Should -Be @('GetClientsDetails clientid=4242', 'GetClientsProducts clientid=4242')
    }

    It 'refuses a non-digit client id before any WHMCS call' {
        Remove-Item -LiteralPath $script:Log -ErrorAction SilentlyContinue
        $r = Invoke-Runner -ClientId '42; rm -rf /'
        $r.rc | Should -Be 1
        $r.out | Should -BeLike '*::error::client_id must be digits*'
        Test-Path -LiteralPath $script:Log | Should -BeFalse
    }
}
