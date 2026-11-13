<#
.SYNOPSIS
    Pre-acceptance completeness review for a charity-lane application
    (pid 16 / 33). Dot-source only: functions, no runner.
.DESCRIPTION
    The application inventory (FFC-IN-ffcadmin.org
    docs/application-prerequisites-inventory.md section 5) says every check must
    resolve to a documented pass before an offer is made. Until 2026-11-13 the
    Gate 1 verdict (232) only ran the eligibility checks (EIN, Facebook,
    LinkedIn, attestations, duplicates, fraud), so orders 875 and 876 were
    accepted with admin links in required fields, one person in all three
    officer slots and a GitHub username that does not exist. Clarke,
    2026-11-13: "Make sure that we are absolutely not approving applications
    that are not complete."

    This file is the standard, in one place, shared by both accept paths:
      - 232 (scripts/whmcs-charity-lane-verdicts.ps1) adds these codes to every
        Gate 1 row, so the scheduled routine never sees ACCEPT for an
        incomplete application;
      - 226 approve (scripts/whmcs-application-triage.ps1) re-runs the review
        for each order it is asked to accept and refuses any order that does
        not pass, so a by-hand accept is gated too.

    The standard:
      1. Every required website field is answered with a usable value. Values
         are read and validated exactly as 234 reads them (the allowlist in
         config/charity-lane-site-fields.json), so an admin, dashboard,
         login, share or generic home-page link counts as NOT answered.
      2. Every link the site will publish opens for a member of the public:
         the Zeffy donation form, the Idealist page, the Candid profile and the
         current website, when given. A 404 / 410 is the charity's to fix; a
         link we could not open for another reason holds the order for a human
         (ESCALATE). Zeffy's Cloudflare managed challenge (403 with
         `cf-mitigated: challenge`) is Zeffy refusing robots, not a dead link,
         and is recorded as INFO, the same ruling Clarke made for LinkedIn's
         999 on 2026-11-12.
      3. The maintainer's GitHub account exists.
      4. Three DIFFERENT officers (Chair, Secretary, Treasurer), each named for
         the website and each with their OWN personal LinkedIn profile, and the
         officers' consent to be listed is ticked.

    Nothing here prints a field value. The review returns reason codes only;
    the plain-English text for the charity's ticket is in the code catalog.
#>

$script:LaneCompletenessRoot = $PSScriptRoot

# The site-fields reader (234) has a param() block. Dot-sourcing binds that
# block in the CALLER's scope, so pass the caller's current values straight
# back in: nothing the caller was given is reset to the reader's defaults.
. (Join-Path $PSScriptRoot 'whmcs-charity-lane-site-fields.ps1') `
    -ClientId $ClientId -ConfigPath $(if ($ConfigPath) { $ConfigPath } else { 'config/charity-lane-site-fields.json' }) `
    -ApiUrl $ApiUrl -OutputFile $OutputFile -MarkdownFile $MarkdownFile -RunUrl $RunUrl

# --------------------------------------------------------------------------
# The standard: required website fields.
# key = config/charity-lane-site-fields.json key. Pids = where it is required.
# A field that is given but fails 234's validation gets the _NOT_PUBLIC /
# _INVALID code instead of _MISSING, so the ticket says what to change.
# --------------------------------------------------------------------------
$script:LaneRequiredSiteFields = @(
    @{ key = 'charityName'; pids = @(16, 33); missing = 'ORG_LEGAL_NAME_MISSING'; invalid = 'ORG_LEGAL_NAME_MISSING' }
    @{ key = 'mission'; pids = @(16, 33); missing = 'MISSION_MISSING'; invalid = 'MISSION_MISSING' }
    @{ key = 'foundingYear'; pids = @(16, 33); missing = 'FOUNDING_YEAR_MISSING'; invalid = 'FOUNDING_YEAR_INVALID' }
    @{ key = 'publicEmail'; pids = @(16, 33); missing = 'PUBLIC_EMAIL_MISSING'; invalid = 'PUBLIC_EMAIL_INVALID' }
    @{ key = 'publicPhone'; pids = @(16, 33); missing = 'PUBLIC_PHONE_MISSING'; invalid = 'PUBLIC_PHONE_INVALID' }
    @{ key = 'footerLocation'; pids = @(16, 33); missing = 'FOOTER_LOCATION_MISSING'; invalid = 'FOOTER_LOCATION_INVALID' }
    @{ key = 'templateChoice'; pids = @(16, 33); missing = 'TEMPLATE_CHOICE_MISSING'; invalid = 'TEMPLATE_CHOICE_MISSING' }
    @{ key = 'domainOwnership'; pids = @(16, 33); missing = 'DOMAIN_OWNERSHIP_MISSING'; invalid = 'DOMAIN_OWNERSHIP_MISSING' }
    @{ key = 'tagline'; pids = @(16, 33); missing = 'TAGLINE_MISSING'; invalid = 'TAGLINE_MISSING' }
    @{ key = 'siteDescription'; pids = @(16, 33); missing = 'SITE_DESCRIPTION_MISSING'; invalid = 'SITE_DESCRIPTION_MISSING' }
    @{ key = 'siteShortDescription'; pids = @(16, 33); missing = 'SHORT_DESCRIPTION_MISSING'; invalid = 'SHORT_DESCRIPTION_MISSING' }
    @{ key = 'program1Title'; pids = @(16, 33); missing = 'PROGRAM_1_MISSING'; invalid = 'PROGRAM_1_MISSING' }
    @{ key = 'program1Description'; pids = @(16, 33); missing = 'PROGRAM_1_MISSING'; invalid = 'PROGRAM_1_MISSING' }
    @{ key = 'facebookUrl'; pids = @(16, 33); missing = 'FACEBOOK_URL_MISSING'; invalid = 'FACEBOOK_PAGE_NOT_PUBLIC' }
    @{ key = 'linkedinUrl'; pids = @(16, 33); missing = 'LINKEDIN_URL_MISSING'; invalid = 'LINKEDIN_PAGE_NOT_PUBLIC' }
    @{ key = 'maintainerGithub'; pids = @(16, 33); missing = 'GITHUB_USER_MISSING'; invalid = 'GITHUB_USER_INVALID' }
    @{ key = 'leader1Name'; pids = @(16, 33); missing = 'OFFICER_NAMES_MISSING'; invalid = 'OFFICER_NAMES_MISSING' }
    @{ key = 'leader2Name'; pids = @(16, 33); missing = 'OFFICER_NAMES_MISSING'; invalid = 'OFFICER_NAMES_MISSING' }
    @{ key = 'leader3Name'; pids = @(16, 33); missing = 'OFFICER_NAMES_MISSING'; invalid = 'OFFICER_NAMES_MISSING' }
    @{ key = 'donationUrl'; pids = @(33); missing = 'DONATION_URL_MISSING'; invalid = 'DONATION_URL_NOT_PUBLIC' }
    @{ key = 'volunteerUrl'; pids = @(33); missing = 'VOLUNTEER_URL_MISSING'; invalid = 'VOLUNTEER_URL_NOT_PUBLIC' }
    @{ key = 'candidProfileUrl'; pids = @(33); missing = 'CANDID_URL_MISSING'; invalid = 'CANDID_URL_NOT_PUBLIC' }
)

# Optional fields: blank is fine, but a value that is given must be usable.
$script:LaneOptionalCheckedFields = @(
    @{ key = 'donationUrl'; pids = @(16); invalid = 'DONATION_URL_NOT_PUBLIC' }
    @{ key = 'volunteerUrl'; pids = @(16); invalid = 'VOLUNTEER_URL_NOT_PUBLIC' }
    @{ key = 'candidProfileUrl'; pids = @(16); invalid = 'CANDID_URL_NOT_PUBLIC' }
    @{ key = 'currentWebsiteUrl'; pids = @(16, 33); invalid = 'CURRENT_SITE_URL_INVALID' }
)

# Links that must open. key -> the code for "the link is dead" (404/410).
$script:LaneProbedLinkFields = [ordered]@{
    donationUrl       = 'DONATION_URL_NOT_FOUND'
    volunteerUrl      = 'VOLUNTEER_URL_NOT_FOUND'
    candidProfileUrl  = 'CANDID_URL_NOT_FOUND'
    currentWebsiteUrl = 'CURRENT_SITE_UNREACHABLE'
}

# Officer LinkedIn fields (the form's "Board President/Chair LinkedIn Link ...",
# "Board Secretary LinkedIn Link ...", "Board Treasurer LinkedIn Link ...").
# The Chair pattern excludes the Vice Chair row.
$script:LaneOfficerLinkedInPatterns = [ordered]@{
    chair     = '^(?!.*vice)(?=.*(president|chair))(?=.*linkedin)'
    secretary = '^(?=.*secretary)(?=.*linkedin)'
    treasurer = '^(?=.*treasurer)(?=.*linkedin)'
}
$script:LaneOfficerConsentPattern = 'agree to be listed'

# --------------------------------------------------------------------------
# Code catalog. Severity as in the verdict script: NEED_INFO = the charity can
# fix it (ticket), ESCALATE = a human must look, INFO = recorded only.
# `applicant` is the line the onboarding_need_info ticket uses, verbatim.
# --------------------------------------------------------------------------
$script:LaneCompletenessCodes = [ordered]@{
    COMPLETENESS_NOT_CHECKED      = @{ severity = 'ESCALATE'; description = 'The pre-acceptance completeness review did not run for this order. Never ACCEPT without it.'; applicant = '' }
    ORG_LEGAL_NAME_MISSING        = @{ severity = 'NEED_INFO'; description = "Organization's legal name not answered."; applicant = "Your organization's legal name, exactly as on your IRS letter or state filing." }
    MISSION_MISSING               = @{ severity = 'NEED_INFO'; description = 'Mission not answered.'; applicant = "Your organization's mission, in one or two plain sentences." }
    FOUNDING_YEAR_MISSING         = @{ severity = 'NEED_INFO'; description = 'Founding year not answered.'; applicant = 'The year your organization was founded (for example 2015).' }
    FOUNDING_YEAR_INVALID         = @{ severity = 'NEED_INFO'; description = 'Founding year is not a 4-digit year.'; applicant = 'The year your organization was founded, as a 4-digit year (for example 2015).' }
    PUBLIC_EMAIL_MISSING          = @{ severity = 'NEED_INFO'; description = 'Public footer email not answered.'; applicant = 'The public email address to show in your website footer.' }
    PUBLIC_EMAIL_INVALID          = @{ severity = 'NEED_INFO'; description = 'Public footer email is not an email address.'; applicant = 'The public email address for your website footer (the answer we have is not an email address).' }
    PUBLIC_PHONE_MISSING          = @{ severity = 'NEED_INFO'; description = 'Public footer phone not answered.'; applicant = 'The public phone number to show in your website footer.' }
    PUBLIC_PHONE_INVALID          = @{ severity = 'NEED_INFO'; description = 'Public footer phone is not a phone number.'; applicant = 'The public phone number for your website footer (the answer we have is not a phone number).' }
    FOOTER_LOCATION_MISSING       = @{ severity = 'NEED_INFO'; description = 'Footer city & state not answered.'; applicant = 'The city and state to show in your website footer (for example Raleigh, NC).' }
    FOOTER_LOCATION_INVALID       = @{ severity = 'NEED_INFO'; description = 'Footer city & state is not a city and state.'; applicant = 'The city and state for your website footer, city and state only (put a street address in the separate optional street-address question).' }
    TEMPLATE_CHOICE_MISSING       = @{ severity = 'NEED_INFO'; description = 'Website starting point not chosen.'; applicant = 'Which website starting point fits your charity (Single Page or Footer-Only).' }
    DOMAIN_OWNERSHIP_MISSING      = @{ severity = 'NEED_INFO'; description = 'Domain-ownership question not answered.'; applicant = 'Whether you already own your website domain.' }
    TAGLINE_MISSING               = @{ severity = 'NEED_INFO'; description = 'Site tagline not answered.'; applicant = 'A short tagline (slogan) for your website.' }
    SITE_DESCRIPTION_MISSING      = @{ severity = 'NEED_INFO'; description = 'Site description not answered.'; applicant = 'A one- or two-sentence description of your charity for your website.' }
    SHORT_DESCRIPTION_MISSING     = @{ severity = 'NEED_INFO'; description = 'Short description not answered.'; applicant = 'A short description of your charity, under 200 characters.' }
    PROGRAM_1_MISSING             = @{ severity = 'NEED_INFO'; description = 'Program 1 title or description not answered.'; applicant = 'The title and a one-sentence description of your main program or service.' }
    GITHUB_USER_MISSING           = @{ severity = 'NEED_INFO'; description = 'Maintainer GitHub username not answered.'; applicant = 'The GitHub username of your website maintainer (create a free account at https://github.com/signup if needed).' }
    GITHUB_USER_INVALID           = @{ severity = 'NEED_INFO'; description = 'Maintainer GitHub username is not a valid GitHub username.'; applicant = 'The GitHub username of your website maintainer: just the username, for example janedoe.' }
    GITHUB_USER_NOT_FOUND         = @{ severity = 'NEED_INFO'; description = 'No GitHub account exists with the maintainer username given.'; applicant = 'The GitHub username of your website maintainer: no GitHub account exists with the username you gave. Please check the spelling, or create the account at https://github.com/signup.' }
    GITHUB_USER_UNVERIFIED        = @{ severity = 'ESCALATE'; description = 'The GitHub account lookup failed, so the maintainer account could not be confirmed.'; applicant = '' }
    OFFICER_NAMES_MISSING         = @{ severity = 'NEED_INFO'; description = 'Board Chair, Secretary and Treasurer website names not all answered.'; applicant = 'The full names of your Board Chair, Secretary and Treasurer, to show on your website.' }
    OFFICERS_NOT_DISTINCT         = @{ severity = 'NEED_INFO'; description = 'The same person is named in more than one of the Chair / Secretary / Treasurer slots.'; applicant = 'Three different people as Board Chair, Secretary and Treasurer. One person cannot hold more than one of these three roles for your application.' }
    OFFICERS_CONSENT_MISSING      = @{ severity = 'NEED_INFO'; description = 'Officers-consent-to-be-listed box not ticked.'; applicant = 'Confirmation that your Chair, Secretary and Treasurer agree to be listed by name and role on your website (the consent box).' }
    OFFICER_LINKEDIN_MISSING      = @{ severity = 'NEED_INFO'; description = 'A Chair / Secretary / Treasurer LinkedIn link is missing.'; applicant = 'A personal LinkedIn profile link for each of your Board Chair, Secretary and Treasurer.' }
    OFFICER_LINKEDIN_NOT_PROFILE  = @{ severity = 'NEED_INFO'; description = 'A Chair / Secretary / Treasurer LinkedIn link is not a personal LinkedIn profile (an /in/ link).'; applicant = 'A personal LinkedIn profile link (https://www.linkedin.com/in/...) for each of your Board Chair, Secretary and Treasurer. A company page or an admin link does not work here.' }
    OFFICER_LINKEDIN_NOT_DISTINCT = @{ severity = 'NEED_INFO'; description = 'Two or more officers share the same LinkedIn profile.'; applicant = "Each officer's OWN LinkedIn profile: your Chair, Secretary and Treasurer each need a different profile link." }
    FACEBOOK_URL_MISSING          = @{ severity = 'NEED_INFO'; description = 'Organization Facebook Page URL missing.'; applicant = "Your charity's public Facebook Page link (the Page category must be Nonprofit Organization; a personal profile does not qualify)." }
    FACEBOOK_PAGE_NOT_PUBLIC      = @{ severity = 'NEED_INFO'; description = 'Facebook answer is not a public page link (admin, share or login link).'; applicant = "Your charity's PUBLIC Facebook Page link, the one anyone can open. The link we have is an admin, share or login link." }
    LINKEDIN_URL_MISSING          = @{ severity = 'NEED_INFO'; description = 'Organization LinkedIn Page URL missing.'; applicant = "Your charity's LinkedIn organization page link, for example https://www.linkedin.com/company/your-org." }
    LINKEDIN_PAGE_NOT_PUBLIC      = @{ severity = 'NEED_INFO'; description = 'LinkedIn answer is not the public organization page (an admin/dashboard link or a personal profile).'; applicant = "Your charity's PUBLIC LinkedIn organization page link, for example https://www.linkedin.com/company/your-org. The link we have is an admin or dashboard page that only page admins can see." }
    DONATION_URL_MISSING          = @{ severity = 'NEED_INFO'; description = 'Zeffy donation form URL not answered.'; applicant = 'Your public Zeffy donation form link (set it up at https://ffcadmin.org/guides/zeffy/).' }
    DONATION_URL_NOT_PUBLIC       = @{ severity = 'NEED_INFO'; description = 'Zeffy answer is not a public donation-form link (admin/dashboard/campaigns or home page).'; applicant = 'Your PUBLIC Zeffy donation form link, the one donors open. The link we have is an admin or dashboard page that only you can see.' }
    DONATION_URL_NOT_FOUND        = @{ severity = 'NEED_INFO'; description = 'Zeffy donation form URL answers 404/410.'; applicant = 'Your Zeffy donation form link: the link we have does not open (page not found).' }
    VOLUNTEER_URL_MISSING         = @{ severity = 'NEED_INFO'; description = 'Idealist volunteer URL not answered.'; applicant = 'Your public Idealist organization or volunteer-opportunity link (set it up at https://ffcadmin.org/guides/idealist/).' }
    VOLUNTEER_URL_NOT_PUBLIC      = @{ severity = 'NEED_INFO'; description = "Idealist answer is not your organization's public page (admin link or the Idealist home page)."; applicant = "Your organization's PUBLIC Idealist page or volunteer-opportunity link. The link we have is an admin page or the Idealist home page." }
    VOLUNTEER_URL_NOT_FOUND       = @{ severity = 'NEED_INFO'; description = 'Idealist URL answers 404/410.'; applicant = 'Your Idealist link: the link we have does not open (page not found).' }
    CANDID_URL_MISSING            = @{ severity = 'NEED_INFO'; description = 'Candid / GuideStar public profile link not answered.'; applicant = "Your organization's public Candid (GuideStar) profile link." }
    CANDID_URL_NOT_PUBLIC         = @{ severity = 'NEED_INFO'; description = "Candid answer is not the organization's public profile (an app.candid.org, login or user page)."; applicant = "Your organization's PUBLIC Candid (GuideStar) profile link, for example https://www.guidestar.org/profile/12-3456789. The link we have is a Candid app or login page that the public cannot open." }
    CANDID_URL_NOT_FOUND          = @{ severity = 'NEED_INFO'; description = 'Candid profile URL answers 404/410.'; applicant = 'Your Candid (GuideStar) profile link: the link we have does not open (page not found).' }
    CURRENT_SITE_URL_INVALID      = @{ severity = 'NEED_INFO'; description = 'Current-website answer is not a usable public link.'; applicant = 'The full public link to your current website or page (or leave that question blank if you have none).' }
    CURRENT_SITE_UNREACHABLE      = @{ severity = 'NEED_INFO'; description = 'Current website URL answers 404/410.'; applicant = 'Your current website link: the link we have does not open. Please send the working link, or tell us you have no current site.' }
    LINK_UNVERIFIED               = @{ severity = 'ESCALATE'; description = 'A required public link (Zeffy, Idealist, Candid or current site) did not answer 2xx/3xx or 404/410 (timeout, server error, block); a human must open it.'; applicant = '' }
    LINK_BOT_WALL                 = @{ severity = 'INFO'; description = "A link answered with the host's robot check (Zeffy's Cloudflare managed challenge). The link is well formed and public; not blocking."; applicant = '' }
}

# --------------------------------------------------------------------------
# Pure functions.
# --------------------------------------------------------------------------

function Get-LaneSiteFieldsConfig {
    # The 234 allowlist, loaded once per process.
    if (-not $script:LaneSiteFieldsConfigCache) {
        $path = Join-Path $script:LaneCompletenessRoot '..' 'config' 'charity-lane-site-fields.json'
        $script:LaneSiteFieldsConfigCache = Read-SiteFieldsConfig -ConfigObject (Get-Content -Raw -LiteralPath $path | ConvertFrom-Json)
    }
    return $script:LaneSiteFieldsConfigCache
}

function Resolve-LaneApplicationSiteFields {
    <#
    .SYNOPSIS
        Read one application's answers the way 234 reads them. Pure.
    .DESCRIPTION
        $Fields is the verdict/triage field list (@{ id; name; value }). Returns
        234's Resolve-SiteFields document: `fields` (valid values only),
        `missing` and `rejected` (field key + reason codes, never a value).
    #>
    param(
        [Parameter()]$Fields,
        [Parameter(Mandatory = $true)][int]$ProductPid,
        [Parameter()]$Config
    )
    if (-not $Config) { $Config = Get-LaneSiteFieldsConfig }
    $answers = @(foreach ($f in @($Fields)) {
            if (-not $f) { continue }
            $n = [string]$f.name
            if ([string]::IsNullOrWhiteSpace($n)) { continue }
            [pscustomobject]@{ name = $n; value = [string]$f.value }
        })
    $set = [pscustomobject]@{ serviceId = 0; pid = $ProductPid; status = 'Pending'; answers = $answers }
    return Resolve-SiteFields -Config $Config -ClientValues @{} -ProductSets @($set) -ClientId 0
}

function ConvertTo-LaneOfficerKey {
    # "Garyel Tubbs - Executive Director" / "Dr. Jane Q. Doe, Chair" -> "garyel tubbs" / "jane q doe".
    param([string]$Name)
    if ([string]::IsNullOrWhiteSpace($Name)) { return '' }
    $n = $Name.ToLowerInvariant()
    # Cut a trailing role: "Name - Role", "Name, Role", "Name (Role)", "Name / Role".
    # A hyphen inside a name (Mary-Jane) has no spaces round it and is kept.
    $n = ($n -split '\s*[,(|/]|\s+[-\u2013\u2014]\s+')[0]
    $n = $n -replace '^(dr|mr|mrs|ms|mx|rev|pastor)\.?\s+', ''
    $n = $n -replace '[^a-z0-9 ]', ' '
    return (($n -split '\s+') | Where-Object { $_ }) -join ' '
}

function ConvertTo-LaneLinkedInProfileKey {
    # A personal profile URL -> its slug ("in/jane-doe"), else ''.
    param([string]$Url)
    if ([string]::IsNullOrWhiteSpace($Url)) { return '' }
    $m = [regex]::Match($Url.Trim(), '(?i)^https?://([a-z]{2,3}\.|www\.)?linkedin\.com/in/([^/?#\s]+)')
    if (-not $m.Success) { return '' }
    return 'in/' + [Uri]::UnescapeDataString($m.Groups[2].Value).ToLowerInvariant()
}

function Test-LaneLinkAnswered {
    # Probe result -> 'ok' | 'dead' | 'botwall' | 'unverified'. Pure.
    # $Probe = @{ status = <int or $null>; mitigated = <string>; host = <string> }
    param($Probe)
    if ($null -eq $Probe -or $null -eq $Probe.status) { return 'unverified' }
    $s = [int]$Probe.status
    if ($s -ge 200 -and $s -lt 400) { return 'ok' }
    if ($s -eq 404 -or $s -eq 410) { return 'dead' }
    if ($s -eq 403 -and [string]$Probe.mitigated -match '(?i)^\s*challenge\s*$' -and [string]$Probe.host -match '(?i)(^|\.)zeffy\.com$') { return 'botwall' }
    return 'unverified'
}

function Get-LaneCompletenessTargets {
    <#
    .SYNOPSIS
        What the runner must look up for one application: the links to open
        and the GitHub username to confirm. Pure.
    #>
    param([Parameter()]$Fields, [Parameter(Mandatory = $true)][int]$ProductPid, [Parameter()]$Config)
    $doc = Resolve-LaneApplicationSiteFields -Fields $Fields -ProductPid $ProductPid -Config $Config
    $links = [ordered]@{}
    foreach ($k in $script:LaneProbedLinkFields.Keys) {
        if ($doc.fields.Contains($k)) { $links[$k] = [string]$doc.fields[$k] }
    }
    $gh = if ($doc.fields.Contains('maintainerGithub')) { [string]$doc.fields['maintainerGithub'] } else { '' }
    return [pscustomobject]@{ Links = $links; GithubUser = $gh }
}

function Get-LaneCompletenessCodes {
    <#
    .SYNOPSIS
        The pre-acceptance review for ONE pid 16 / 33 application. Pure.
    .DESCRIPTION
        $LinkProbes: hashtable URL -> @{ status; mitigated; host } (from
        Invoke-LaneLinkProbe). A link with no probe entry is unverified.
        $GithubLookup: hashtable username -> $true (exists), $false (404), or
        $null (lookup failed). A username with no entry is unverified.
        Returns the reason codes (unique, catalog order).
    #>
    param(
        [Parameter()]$Fields,
        [Parameter(Mandatory = $true)][int]$ProductPid,
        [Parameter()][hashtable]$LinkProbes = @{},
        [Parameter()][hashtable]$GithubLookup = @{},
        [Parameter()]$Config
    )
    $codes = [System.Collections.Generic.List[string]]::new()
    $add = { param($c) if (-not $codes.Contains($c)) { $codes.Add($c) } }
    $doc = Resolve-LaneApplicationSiteFields -Fields $Fields -ProductPid $ProductPid -Config $Config
    $rejectedKeys = @($doc.rejected | ForEach-Object { [string]$_.field })

    # 1. Required fields, valid as 234 reads them.
    foreach ($r in $script:LaneRequiredSiteFields) {
        if ($r.pids -notcontains $ProductPid) { continue }
        if ($doc.fields.Contains($r.key)) { continue }
        if ($rejectedKeys -contains $r.key) { & $add $r.invalid } else { & $add $r.missing }
    }
    foreach ($o in $script:LaneOptionalCheckedFields) {
        if ($o.pids -notcontains $ProductPid) { continue }
        if (-not $doc.fields.Contains($o.key) -and $rejectedKeys -contains $o.key) { & $add $o.invalid }
    }

    # 2. Links open.
    foreach ($k in $script:LaneProbedLinkFields.Keys) {
        if (-not $doc.fields.Contains($k)) { continue }
        $url = [string]$doc.fields[$k]
        $probe = if ($LinkProbes.ContainsKey($url)) { $LinkProbes[$url] } else { $null }
        switch (Test-LaneLinkAnswered -Probe $probe) {
            'ok' { }
            'dead' { & $add $script:LaneProbedLinkFields[$k] }
            'botwall' { & $add 'LINK_BOT_WALL' }
            default { & $add 'LINK_UNVERIFIED' }
        }
    }

    # 3. GitHub account exists.
    if ($doc.fields.Contains('maintainerGithub')) {
        $u = [string]$doc.fields['maintainerGithub']
        $hit = $null
        foreach ($k in $GithubLookup.Keys) { if ([string]$k -ieq $u) { $hit = $GithubLookup[$k]; break } }
        if ($hit -eq $true) { }
        elseif ($hit -eq $false) { & $add 'GITHUB_USER_NOT_FOUND' }
        else { & $add 'GITHUB_USER_UNVERIFIED' }
    }

    # 4. Three distinct officers, each with their own LinkedIn, and consent.
    $names = @('leader1Name', 'leader2Name', 'leader3Name' | ForEach-Object {
            if ($doc.fields.Contains($_)) { ConvertTo-LaneOfficerKey -Name ([string]$doc.fields[$_]) } else { '' }
        })
    $given = @($names | Where-Object { $_ })
    if ($given.Count -ne @($given | Select-Object -Unique).Count) { & $add 'OFFICERS_NOT_DISTINCT' }

    $profiles = @()
    foreach ($role in $script:LaneOfficerLinkedInPatterns.Keys) {
        $raw = Get-FieldValue -Fields $Fields -Pattern $script:LaneOfficerLinkedInPatterns[$role]
        if ([string]::IsNullOrWhiteSpace($raw)) { & $add 'OFFICER_LINKEDIN_MISSING'; continue }
        $key = ConvertTo-LaneLinkedInProfileKey -Url ([System.Net.WebUtility]::HtmlDecode($raw))
        if (-not $key) { & $add 'OFFICER_LINKEDIN_NOT_PROFILE'; continue }
        $profiles += $key
    }
    if ($profiles.Count -ne @($profiles | Select-Object -Unique).Count) { & $add 'OFFICER_LINKEDIN_NOT_DISTINCT' }

    $consent = Get-FieldValue -Fields $Fields -Pattern $script:LaneOfficerConsentPattern
    if (-not (Test-TruthyTick -Value $consent)) { & $add 'OFFICERS_CONSENT_MISSING' }

    $order = @($script:LaneCompletenessCodes.Keys)
    return @($codes | Sort-Object { $order.IndexOf($_) })
}

function Get-LaneCompletenessBlocking {
    # The codes that stop an accept: everything that is not INFO.
    param([string[]]$Codes)
    # An unknown code blocks (fail closed).
    return @(@($Codes) | Where-Object { $_ -and -not ($script:LaneCompletenessCodes.Contains($_) -and $script:LaneCompletenessCodes[$_].severity -eq 'INFO') })
}

function Get-LaneApplicantFixList {
    # Codes -> the plain-English lines for the onboarding_need_info ticket. Pure.
    param([string[]]$Codes)
    $out = [System.Collections.Generic.List[string]]::new()
    foreach ($c in @($Codes)) {
        if (-not $script:LaneCompletenessCodes.Contains($c)) { continue }
        $t = [string]$script:LaneCompletenessCodes[$c].applicant
        if ($t -and -not $out.Contains($t)) { $out.Add($t) }
    }
    return , $out.ToArray()
}

function Get-LaneAdjudicatedOrders {
    # config/charity-lane-adjudicated-orders.json -> @{ '<orderid>' = entry }.
    # Orders Clarke adjudicated himself (older application form): 226 approve
    # accepts them despite completeness gaps and owes the gap ticket. A missing
    # file means no overrides; a malformed one throws, because a silently
    # ignored list would turn an adjudicated accept into a refusal or, worse, a
    # typo into an override. Ids and dates only (the repository is public).
    param([string]$Path)
    $map = @{}
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return $map }
    $doc = Get-Content -LiteralPath $Path -Raw -Encoding utf8 | ConvertFrom-Json
    if ($null -eq $doc.PSObject.Properties['orders']) { throw "Adjudicated-orders file $Path has no 'orders' list." }
    foreach ($e in @($doc.orders)) {
        $id = [string]$e.orderid
        if ($id -notmatch '^[0-9]{1,9}$') { throw "Adjudicated-orders entry has an invalid orderid '$id'." }
        if ([string]$e.adjudicatedOn -notmatch '^\d{4}-\d{2}-\d{2}$') { throw "Adjudicated order $id needs adjudicatedOn as YYYY-MM-DD." }
        if ([string]::IsNullOrWhiteSpace([string]$e.decision)) { throw "Adjudicated order $id needs 'decision' (where Clarke gave it)." }
        if ([string]::IsNullOrWhiteSpace([string]$e.reason)) { throw "Adjudicated order $id needs a 'reason'." }
        $text = ($e | ConvertTo-Json -Compress -Depth 4)
        if ($text -match '@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' -or $text -match '\d{3}[ .-]\d{3}[ .-]\d{4}') {
            throw "Adjudicated order $id carries contact details; this file is public, list ids and dates only."
        }
        if ($map.ContainsKey($id)) { throw "Adjudicated order $id is listed twice." }
        $map[$id] = $e
    }
    return $map
}

# --------------------------------------------------------------------------
# Network helpers (read-only public GETs; the runners call these).
# --------------------------------------------------------------------------

function Invoke-LaneLinkProbe {
    # One GET as a browser would send it, following up to 5 redirects. Returns
    # @{ status; mitigated; host } with status $null on a transport error.
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][hashtable]$Cache,
        [int]$TimeoutSec = 15
    )
    if ($Cache.ContainsKey($Url)) { return $Cache[$Url] }
    Start-Sleep -Milliseconds 250
    $result = @{ status = $null; mitigated = ''; host = '' }
    try { $result.host = ([Uri]$Url).Host } catch { }
    $handler = [System.Net.Http.HttpClientHandler]::new()
    $handler.AllowAutoRedirect = $true
    $handler.MaxAutomaticRedirections = 5
    $client = [System.Net.Http.HttpClient]::new($handler)
    try {
        $client.Timeout = [TimeSpan]::FromSeconds($TimeoutSec)
        $req = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::Get, $Url)
        [void]$req.Headers.TryAddWithoutValidation('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36 FFC-Charity-Lane-Review/1.0')
        [void]$req.Headers.TryAddWithoutValidation('Accept', 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8')
        $resp = $client.SendAsync($req).GetAwaiter().GetResult()
        $result.status = [int]$resp.StatusCode
        $vals = $null
        if ($resp.Headers.TryGetValues('cf-mitigated', [ref]$vals)) { $result.mitigated = ($vals -join ',') }
        $resp.Dispose()
    }
    catch { $result.status = $null }
    finally { $client.Dispose() }
    $Cache[$Url] = $result
    return $result
}

function Test-LaneGithubUser {
    # $true = the account exists, $false = 404, $null = the lookup failed.
    # Uses GH_TOKEN / GITHUB_TOKEN when present (rate limit), else anonymous.
    param(
        [Parameter(Mandatory = $true)][string]$User,
        [Parameter(Mandatory = $true)][hashtable]$Cache
    )
    $k = $User.ToLowerInvariant()
    if ($Cache.ContainsKey($k)) { return $Cache[$k] }
    $answer = $null
    $headers = @{ 'User-Agent' = 'FFC-Charity-Lane-Review'; 'Accept' = 'application/vnd.github+json' }
    $tok = if ($env:GH_TOKEN) { $env:GH_TOKEN } elseif ($env:GITHUB_TOKEN) { $env:GITHUB_TOKEN } else { '' }
    if ($tok) { $headers['Authorization'] = "Bearer $tok" }
    try {
        $r = Invoke-WebRequest -Uri ("https://api.github.com/users/" + [Uri]::EscapeDataString($User)) -Headers $headers -SkipHttpErrorCheck -TimeoutSec 15 -UseBasicParsing
        if ([int]$r.StatusCode -eq 200) { $answer = $true }
        elseif ([int]$r.StatusCode -eq 404) { $answer = $false }
    }
    catch { $answer = $null }
    $Cache[$k] = $answer
    return $answer
}

function Invoke-LaneCompletenessReview {
    # Runner helper: look up everything one application needs, then review it.
    # Returns @{ Codes; Blocking }.
    param(
        [Parameter()]$Fields,
        [Parameter(Mandatory = $true)][int]$ProductPid,
        [Parameter(Mandatory = $true)][hashtable]$LinkCache,
        [Parameter(Mandatory = $true)][hashtable]$GithubCache
    )
    $t = Get-LaneCompletenessTargets -Fields $Fields -ProductPid $ProductPid
    $probes = @{}
    foreach ($u in $t.Links.Values) { $probes[$u] = Invoke-LaneLinkProbe -Url $u -Cache $LinkCache }
    $gh = @{}
    if ($t.GithubUser) { $gh[$t.GithubUser] = Test-LaneGithubUser -User $t.GithubUser -Cache $GithubCache }
    $codes = Get-LaneCompletenessCodes -Fields $Fields -ProductPid $ProductPid -LinkProbes $probes -GithubLookup $gh
    return [pscustomobject]@{ Codes = @($codes); Blocking = @(Get-LaneCompletenessBlocking -Codes $codes) }
}
