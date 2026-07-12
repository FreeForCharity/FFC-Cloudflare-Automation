<#
.SYNOPSIS
    Rank PENDING charity onboarding applications by completeness/quality and
    (optionally) accept specific orders via the WHMCS API.

.DESCRIPTION
    Deterministic, re-runnable, API-only triage runner. There is NO web UI.

    Two independent groups are ranked separately:
      - pre-501(c)(3)  = WHMCS product pid 16
      - full 501(c)(3) = WHMCS product pid 33

    Data path (mirrors 225-whmcs-domain-order-url-verify.ps1):
      1. GetOrders status=Pending (paged) -> the set of orders awaiting a
         decision, keyed by orderid.
      2. GetClientsProducts pid=<16|33> (paged) -> the onboarding services with
         their captured custom-field VALUES; each service carries the orderid
         that links it back to a pending order. (Application answers live in
         product custom fields, NOT client-level fields -- companyname is empty;
         see repo CLAUDE.md "Where application answers live".)
      3. Score each application 0-100 against a documented rubric (see
         Get-ApplicationScore) and emit a ranked table per group.

    MODES (-Mode):
      report  (DEFAULT)  Read-only. Never calls AcceptOrder. Emits a ranked
                         markdown table per group to GITHUB_STEP_SUMMARY (when
                         set) + stdout, writes a JSON artifact, and prints the
                         top-1 of each group with its rationale.
      approve            Requires -OrderIds (comma list). Refuses to run without
                         it. For each listed id it prints a pre-accept summary,
                         re-checks the order is Pending and is an onboarding
                         order (pid 16/33), then calls WHMCS AcceptOrder
                         orderid=<id> sendemail=true autosetup=true. These are
                         $0 "No Payment Required" onboarding orders, so
                         acceptance never attempts payment collection. ONLY the
                         explicitly listed ids are accepted -- no accept-all,
                         no accept-top-N.

    SAFETY: default read-only; approve accepts only explicitly-listed orderids;
    secrets/credentials are never printed. Pure scoring/ranking functions live
    above the dot-source guard and are unit-tested with mocked data
    (tests/whmcs-application-triage.Tests.ps1).
#>
[CmdletBinding()]
param(
    [Parameter()]
    [ValidateSet('report', 'approve')]
    [string]$Mode = 'report',

    # approve-only: comma-separated WHMCS order ids to accept. Required (and only
    # honoured) in approve mode. Empty in report mode.
    [Parameter()]
    [string]$OrderIds = '',

    [Parameter()]
    [string]$ApiUrl,

    [Parameter()]
    [string]$Identifier,

    [Parameter()]
    [string]$Secret,

    [Parameter()]
    [string]$CredentialsJson,

    [Parameter()]
    [string]$AccessKey,

    # The two onboarding product ids: 16 = pre-501(c)(3), 33 = full 501(c)(3).
    [Parameter()]
    [int]$PreProductId = 16,

    [Parameter()]
    [int]$FullProductId = 33,

    [Parameter()]
    [string]$OutputFile = 'artifacts/whmcs/whmcs_application_triage.json',

    # approve-only: send the WHMCS acceptance email to the charity (default on --
    # the whole point is that the charity gets the corrected acceptance email).
    [Parameter()]
    [bool]$SendEmail = $true,

    # approve-only: run product auto-setup on acceptance (matches the repo's
    # order flows).
    [Parameter()]
    [bool]$AutoSetup = $true,

    [Parameter()]
    [ValidateRange(1, 250)]
    [int]$PageSize = 250
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'whmcs-api-common.ps1')

# --------------------------------------------------------------------------
# WHMCS JSON list normalization (same hardened pattern as 225 / 219).
# --------------------------------------------------------------------------

function Get-WhmcsListNode {
    param($Node, [Parameter(Mandatory = $true)][string]$ChildName)
    if ($null -eq $Node -or $Node -is [string]) { return @() }
    if ($Node -is [System.Array]) { return @($Node | Where-Object { $null -ne $_ }) }
    if ($Node.PSObject.Properties[$ChildName]) { return @($Node.$ChildName | Where-Object { $null -ne $_ }) }
    return @()
}

function Get-OrdersFromResponse {
    param([Parameter(Mandatory = $true)]$Response)
    return Get-WhmcsListNode -Node $Response.orders -ChildName 'order'
}

function Get-ProductsFromResponse {
    param([Parameter(Mandatory = $true)]$Response)
    return Get-WhmcsListNode -Node $Response.products -ChildName 'product'
}

function Get-CustomFieldNodes {
    # Returns @( @{ id; name; value } ) from a service node's customfields.
    param($Node)
    if (-not $Node) { return @() }
    $cf = $null
    if ($Node.PSObject.Properties['customfields']) { $cf = $Node.customfields }
    $entries = Get-WhmcsListNode -Node $cf -ChildName 'customfield'
    $out = @()
    foreach ($e in $entries) {
        if (-not $e) { continue }
        $id = if ($e.PSObject.Properties['id']) { [string]$e.id } else { $null }
        $name = if ($e.PSObject.Properties['name'] -and $e.name) { [string]$e.name }
        elseif ($e.PSObject.Properties['translated_name'] -and $e.translated_name) { [string]$e.translated_name }
        else { $null }
        $value = if ($e.PSObject.Properties['value']) { [string]$e.value } else { $null }
        if ([string]::IsNullOrWhiteSpace($name) -and [string]::IsNullOrWhiteSpace($id)) { continue }
        $out += [pscustomobject]@{ id = $id; name = $name; value = $value }
    }
    return $out
}

# --------------------------------------------------------------------------
# Field-value helpers (pure).
# --------------------------------------------------------------------------

function Get-HrefOrRaw {
    # WHMCS stores URL fields HTML-wrapped (<a href="URL">..</a>); unwrap the
    # href when present, else return the raw trimmed value.
    [OutputType([string])]
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return '' }
    $m = [regex]::Match($Value, 'href=["'']([^"'']+)["'']', 'IgnoreCase')
    if ($m.Success) { return $m.Groups[1].Value.Trim() }
    return $Value.Trim()
}

function Get-FieldValue {
    # First populated custom-field value whose NAME matches $Pattern (regex).
    [OutputType([string])]
    param(
        [Parameter()]$Fields,
        [Parameter(Mandatory = $true)][string]$Pattern
    )
    foreach ($f in @($Fields)) {
        if (-not $f) { continue }
        $name = [string]$f.name
        if ($name -match $Pattern) {
            $v = [string]$f.value
            if (-not [string]::IsNullOrWhiteSpace($v)) { return $v.Trim() }
        }
    }
    return ''
}

function Get-FieldValues {
    # ALL populated values whose NAME matches $Pattern (regex).
    [OutputType([string[]])]
    param(
        [Parameter()]$Fields,
        [Parameter(Mandatory = $true)][string]$Pattern
    )
    $out = [System.Collections.Generic.List[string]]::new()
    foreach ($f in @($Fields)) {
        if (-not $f) { continue }
        if ([string]$f.name -match $Pattern) {
            $v = [string]$f.value
            if (-not [string]::IsNullOrWhiteSpace($v)) { $out.Add($v.Trim()) }
        }
    }
    return $out.ToArray()
}

function Test-TruthyTick {
    # A WHMCS tickbox / yes-no field is "ticked" when its value is an
    # affirmative token. Empty / "no" / "off" / "0" are not.
    [OutputType([bool])]
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return $false }
    $v = $Value.Trim().ToLowerInvariant()
    return @('1', 'yes', 'y', 'on', 'true', 'checked', 'agree', 'agreed', 'i agree', 'accept', 'accepted') -contains $v
}

function Test-ValidEin {
    # US EIN: NN-NNNNNNN (a hyphen is conventional but tolerated absent).
    [OutputType([bool])]
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return $false }
    return [bool]([regex]::IsMatch($Value, '\b\d{2}-?\d{7}\b'))
}

function Test-GuidestarUrl {
    # Well-formed Candid/GuideStar profile URL (host candid.org / guidestar.org).
    [OutputType([bool])]
    param([string]$Value)
    $candidate = Get-HrefOrRaw -Value $Value
    if ([string]::IsNullOrWhiteSpace($candidate)) { return $false }
    $u = $null
    if (-not [Uri]::TryCreate($candidate, [UriKind]::Absolute, [ref]$u)) { return $false }
    if (@('http', 'https') -notcontains $u.Scheme) { return $false }
    return [bool]([regex]::IsMatch($u.Host, '(^|\.)(candid\.org|guidestar\.org)$', 'IgnoreCase'))
}

function Test-FacebookPageUrl {
    # A facebook.com page URL (any facebook.com host).
    [OutputType([bool])]
    param([string]$Value)
    $candidate = Get-HrefOrRaw -Value $Value
    if ([string]::IsNullOrWhiteSpace($candidate)) { return $false }
    $u = $null
    if (-not [Uri]::TryCreate($candidate, [UriKind]::Absolute, [ref]$u)) { return $false }
    if (@('http', 'https') -notcontains $u.Scheme) { return $false }
    if (-not [regex]::IsMatch($u.Host, '(^|\.)facebook\.com$', 'IgnoreCase')) { return $false }
    # A bare facebook.com root with no page path is not a page.
    return ($u.AbsolutePath.Trim('/').Length -gt 0)
}

function Test-LinkedInOrgUrl {
    # A LinkedIn ORGANIZATION page: linkedin.com/company/... (a personal
    # /in/... profile is NOT an org page and is rejected).
    [OutputType([bool])]
    param([string]$Value)
    $candidate = Get-HrefOrRaw -Value $Value
    if ([string]::IsNullOrWhiteSpace($candidate)) { return $false }
    $u = $null
    if (-not [Uri]::TryCreate($candidate, [UriKind]::Absolute, [ref]$u)) { return $false }
    if (@('http', 'https') -notcontains $u.Scheme) { return $false }
    if (-not [regex]::IsMatch($u.Host, '(^|\.)linkedin\.com$', 'IgnoreCase')) { return $false }
    return [bool]([regex]::IsMatch($u.AbsolutePath, '^/(company|school|showcase)/', 'IgnoreCase'))
}

function Test-PlaceholderLinkedIn {
    # The founder's own profile (https://www.linkedin.com/in/clarkemoyer/) is a
    # PLACEHOLDER copied into board slots, not a real member. Any /in/ personal
    # profile in a board slot is treated as a placeholder / not-a-real-member.
    [OutputType([bool])]
    param([string]$Value)
    $candidate = Get-HrefOrRaw -Value $Value
    if ([string]::IsNullOrWhiteSpace($candidate)) { return $false }
    if ([regex]::IsMatch($candidate, 'linkedin\.com/in/clarkemoyer', 'IgnoreCase')) { return $true }
    if ([regex]::IsMatch($candidate, 'linkedin\.com/in/(your-?profile|placeholder|example|username|name-here)', 'IgnoreCase')) { return $true }
    return $false
}

function Test-UsTimezone {
    # A US time-zone answer (Eastern/Central/Mountain/Pacific/Alaska/Hawaii or a
    # US/... / America/... IANA zone, or a UTC-5..-10 offset).
    [OutputType([bool])]
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return $false }
    $v = $Value.Trim()
    if ([regex]::IsMatch($v, 'eastern|central|mountain|pacific|alaska|hawaii|\bEST\b|\bEDT\b|\bCST\b|\bCDT\b|\bMST\b|\bMDT\b|\bPST\b|\bPDT\b', 'IgnoreCase')) { return $true }
    if ([regex]::IsMatch($v, '^(US|America)/', 'IgnoreCase')) { return $true }
    if ([regex]::IsMatch($v, 'UTC\s*-\s*(5|6|7|8|9|10)\b', 'IgnoreCase')) { return $true }
    return $false
}

function Test-PlaceholderValue {
    # Obvious placeholder / non-answers that should not count as "filled".
    [OutputType([bool])]
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return $true }
    $v = $Value.Trim().ToLowerInvariant()
    if ($v.Length -le 1) { return $true }
    return @('n/a', 'na', 'none', 'tbd', 'todo', 'test', 'xxx', 'placeholder', '.', '-', '--', 'null', 'unknown') -contains $v
}

function Test-PersonNameCompany {
    # Detects a person's name sitting in the organization/charity name field.
    # Returns @{ IsPerson = <bool>; IsAllCaps = <bool> }. A real org name usually
    # carries an org keyword (Foundation, Inc, Society, Ministries, ...) or 4+
    # words; a 2-3 word Title-Case / ALLCAPS string with none of those reads as
    # a personal name.
    param([string]$Name)
    $res = [pscustomobject]@{ IsPerson = $false; IsAllCaps = $false }
    if ([string]::IsNullOrWhiteSpace($Name)) { return $res }
    $t = $Name.Trim()
    $orgKw = 'foundation|fund|inc\b|incorporated|llc|corp|company|society|ministr|church|association|assoc\b|institute|coalition|alliance|council|network|project|initiative|center|centre|charity|charities|nonprofit|non-profit|org\b|organization|organisation|group|services|community|club|league|team|academy|school|trust|guild|shelter|rescue|relief|outreach|mission|house|home|works|collective|partnership|federation'
    if ([regex]::IsMatch($t, $orgKw, 'IgnoreCase')) { return $res }
    $words = @($t -split '\s+' | Where-Object { $_ })
    if ($words.Count -lt 2 -or $words.Count -gt 3) { return $res }
    # ALLCAPS person-like (e.g. "JOHN SMITH").
    if ($t -cmatch '^[A-Z][A-Z.''\-]+(\s+[A-Z][A-Z.''\-]+){1,2}$') {
        $res.IsPerson = $true
        $res.IsAllCaps = $true
        return $res
    }
    # Title-Case person-like (e.g. "Jane A Doe").
    if ($t -cmatch '^[A-Z][a-z]+(\s+([A-Z]\.?|[A-Z][a-z]+)){1,2}$') {
        $res.IsPerson = $true
    }
    return $res
}

function Get-MissionQuality {
    # 0..1 substance score for a free-text mission statement. Rewards length +
    # word count; a short/blank/placeholder mission scores near 0.
    [OutputType([double])]
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return 0.0 }
    if (Test-PlaceholderValue -Value $Value) { return 0.0 }
    $clean = ($Value -replace '\s+', ' ').Trim()
    $len = $clean.Length
    $wordCount = @($clean -split '\s+' | Where-Object { $_ }).Count
    # Full credit at ~200 chars AND >= 25 words; scales linearly below.
    $byLen = [math]::Min(1.0, $len / 200.0)
    $byWords = [math]::Min(1.0, $wordCount / 25.0)
    return [math]::Round([math]::Min($byLen, $byWords), 3)
}

# --------------------------------------------------------------------------
# Field-name matching vocabulary (regex against the custom-field NAME). Robust
# to exact label wording -- mirrors the name-regex approach used by workflow
# 219 and the ffcadmin whmcs-applications.mjs enumerator.
# --------------------------------------------------------------------------

$script:FieldPatterns = @{
    OrgName   = 'organi[sz]ation.*name|charity.*name|legal.*name|nonprofit.*name|name of (your )?(the )?(organi[sz]ation|charity|nonprofit)'
    Ein       = '\bEIN\b|employer identification|tax\s*id'
    Guidestar = 'guidestar|candid'
    Facebook  = 'facebook'
    LinkedIn  = 'linkedin'
    UsAttest  = 'attest|united states|based in the us|u\.s\.\s*(based|attestation)|501\(c\)'
    Tos       = 'terms of service|terms & conditions|terms and conditions|\btos\b|\bt&c\b|agree to the terms'
    Mission   = 'mission|about (your|the) (organi[sz]ation|charity)|purpose|what (does|do) (your|the)'
    Hosting   = 'hosting|current (web)?site|existing (web)?site|website url|do you (have|need)'
    Timezone  = 'time\s*zone'
    President = 'president'
    Secretary = 'secretary'
    Treasurer = 'treasurer'
    Primary   = 'primary contact|main contact'
    Technical = 'technical contact|tech contact'
    Phone     = 'phone|mobile|cell|tel\b'
    Email     = 'e-?mail'
}

function Get-BoardRosterScore {
    # pid-33 board completeness. President / Secretary / Treasurer each need a
    # real LinkedIn (org or personal, but NOT a placeholder), a phone, and an
    # email -> 3 roles x 3 slots = 9 sub-slots. Returns @{ Fraction; Placeholder }
    # where Placeholder is $true if any board LinkedIn is the founder placeholder.
    param([Parameter()]$Fields)
    $roles = @('President', 'Secretary', 'Treasurer')
    $filled = 0
    $total = 9
    $placeholder = $false
    foreach ($role in $roles) {
        $rolePat = $script:FieldPatterns[$role]
        # LinkedIn slot for the role: field name mentions both the role and linkedin.
        $li = Get-FieldValue -Fields $Fields -Pattern ("(?=.*$rolePat)(?=.*linkedin)")
        if (-not [string]::IsNullOrWhiteSpace($li)) {
            if (Test-PlaceholderLinkedIn -Value $li) { $placeholder = $true }
            elseif (-not (Test-PlaceholderValue -Value $li)) { $filled++ }
        }
        $ph = Get-FieldValue -Fields $Fields -Pattern ("(?=.*$rolePat)(?=.*(phone|mobile|cell|tel))")
        if (-not (Test-PlaceholderValue -Value $ph)) { $filled++ }
        $em = Get-FieldValue -Fields $Fields -Pattern ("(?=.*$rolePat)(?=.*e-?mail)")
        if (-not (Test-PlaceholderValue -Value $em)) { $filled++ }
    }
    return [pscustomobject]@{ Fraction = [math]::Round($filled / $total, 3); Placeholder = $placeholder }
}

function Get-ContactsScore {
    # pid-33 primary + technical contact presence -> 0..1 (0.5 each).
    [OutputType([double])]
    param([Parameter()]$Fields)
    $score = 0.0
    $primary = Get-FieldValue -Fields $Fields -Pattern $script:FieldPatterns.Primary
    if (-not (Test-PlaceholderValue -Value $primary)) { $score += 0.5 }
    $technical = Get-FieldValue -Fields $Fields -Pattern $script:FieldPatterns.Technical
    if (-not (Test-PlaceholderValue -Value $technical)) { $score += 0.5 }
    return $score
}

function Get-FieldFillRate {
    # Fraction of returned custom fields that carry a real (non-placeholder)
    # value. 0 when there are no fields.
    [OutputType([double])]
    param([Parameter()]$Fields)
    $all = @($Fields | Where-Object { $_ })
    if ($all.Count -eq 0) { return 0.0 }
    $filled = @($all | Where-Object { -not (Test-PlaceholderValue -Value ([string]$_.value)) }).Count
    return [math]::Round($filled / $all.Count, 3)
}

# --------------------------------------------------------------------------
# The rubric (documented weights, each component contributes weight x fill,
# where fill is 0..1). Weights per group sum to 100.
# --------------------------------------------------------------------------

$script:Rubric = @{
    16 = [ordered]@{
        fillRate  = 30
        ein       = 12
        guidestar = 10
        facebook  = 8
        linkedin  = 8
        usAttest  = 8
        tos       = 8
        mission   = 10
        hosting   = 6
    }
    33 = [ordered]@{
        fillRate  = 20
        ein       = 8
        guidestar = 8
        facebook  = 5
        linkedin  = 5
        usAttest  = 6
        tos       = 6
        mission   = 8
        hosting   = 4
        board     = 18
        contacts  = 8
        timezone  = 4
    }
}

function Get-ApplicationScore {
    <#
    .SYNOPSIS
        Score one application 0-100 with a per-component breakdown, a top-gaps
        list, and a one-line rationale.
    .DESCRIPTION
        $Application is a pscustomobject with: orderid, clientid, pid,
        productName, charityName, fields (@{id;name;value} nodes).

        RUBRIC (weights sum to 100 per group; each component = weight x fill,
        fill in 0..1):
          Both groups: fillRate, valid EIN, well-formed GuideStar/Candid URL,
          facebook.com page, linkedin.com/company org page, US-attestation tick,
          ToS tick, mission substance, hosting answered.
          pid 33 additionally: board roster (President/Secretary/Treasurer each
          LinkedIn+phone+email, founder's own /in/clarkemoyer treated as a
          PLACEHOLDER), primary+technical contacts, US timezone, and BOTH
          GuideStar links (guidestar component = min(2,validLinks)/2).

        QUALITY PENALTIES (subtracted, score floored at 0):
          -15 person's name in the charity field in ALLCAPS; -8 person-like
          Title-Case; -10 a placeholder LinkedIn used for a board member;
          -6 duplicate contact email/phone reused across roles.
    #>
    [OutputType([pscustomobject])]
    param([Parameter(Mandatory = $true)]$Application)

    $productPid = [int]$Application.pid
    $fields = @($Application.fields)
    $weights = $script:Rubric[$productPid]
    if (-not $weights) { $weights = $script:Rubric[16] }

    $fill = [ordered]@{}

    $fill.fillRate = Get-FieldFillRate -Fields $fields
    $fill.ein = if (Test-ValidEin -Value (Get-FieldValue -Fields $fields -Pattern $script:FieldPatterns.Ein)) { 1.0 } else { 0.0 }

    $gsValues = @(Get-FieldValues -Fields $fields -Pattern $script:FieldPatterns.Guidestar)
    $gsValid = @($gsValues | Where-Object { Test-GuidestarUrl -Value $_ }).Count
    if ($productPid -eq 33) {
        $fill.guidestar = [math]::Round([math]::Min(2, $gsValid) / 2.0, 3)
    }
    else {
        $fill.guidestar = if ($gsValid -ge 1) { 1.0 } else { 0.0 }
    }

    $fbValue = Get-FieldValue -Fields $fields -Pattern $script:FieldPatterns.Facebook
    $fill.facebook = if (Test-FacebookPageUrl -Value $fbValue) { 1.0 } else { 0.0 }

    # LinkedIn ORG page: prefer a field whose name mentions linkedin but NOT a
    # board role (President/Secretary/Treasurer) so a board member's profile
    # never satisfies the org-page signal.
    $liOrg = Get-FieldValue -Fields $fields -Pattern '(?=.*linkedin)(?!.*(president|secretary|treasurer|board))'
    if ([string]::IsNullOrWhiteSpace($liOrg)) { $liOrg = Get-FieldValue -Fields $fields -Pattern 'linkedin.*(page|company|organi)' }
    $fill.linkedin = if (Test-LinkedInOrgUrl -Value $liOrg) { 1.0 } else { 0.0 }

    $fill.usAttest = if (Test-TruthyTick -Value (Get-FieldValue -Fields $fields -Pattern $script:FieldPatterns.UsAttest)) { 1.0 } else { 0.0 }
    $fill.tos = if (Test-TruthyTick -Value (Get-FieldValue -Fields $fields -Pattern $script:FieldPatterns.Tos)) { 1.0 } else { 0.0 }
    $fill.mission = Get-MissionQuality -Value (Get-FieldValue -Fields $fields -Pattern $script:FieldPatterns.Mission)
    $fill.hosting = if (-not (Test-PlaceholderValue -Value (Get-FieldValue -Fields $fields -Pattern $script:FieldPatterns.Hosting))) { 1.0 } else { 0.0 }

    $boardPlaceholder = $false
    if ($productPid -eq 33) {
        $board = Get-BoardRosterScore -Fields $fields
        $fill.board = $board.Fraction
        $boardPlaceholder = $board.Placeholder
        $fill.contacts = Get-ContactsScore -Fields $fields
        $fill.timezone = if (Test-UsTimezone -Value (Get-FieldValue -Fields $fields -Pattern $script:FieldPatterns.Timezone)) { 1.0 } else { 0.0 }
    }

    # Weighted base score.
    $base = 0.0
    $breakdown = [ordered]@{}
    foreach ($key in $weights.Keys) {
        $w = [double]$weights[$key]
        $f = [double]($fill[$key])
        $pts = [math]::Round($w * $f, 1)
        $base += $pts
        $breakdown[$key] = [pscustomobject]@{ weight = $w; fill = [math]::Round($f, 3); points = $pts }
    }

    # Quality penalties.
    $penalties = [System.Collections.Generic.List[string]]::new()
    $penalty = 0.0
    $person = Test-PersonNameCompany -Name ([string]$Application.charityName)
    if ($person.IsPerson) {
        if ($person.IsAllCaps) { $penalty += 15; $penalties.Add('charity name is a person (ALLCAPS) -15') }
        else { $penalty += 8; $penalties.Add('charity name looks like a person -8') }
    }
    if ($boardPlaceholder) { $penalty += 10; $penalties.Add('placeholder LinkedIn in a board slot -10') }

    # Duplicate contact values reused across roles (emails/phones).
    $contactVals = @(Get-FieldValues -Fields $fields -Pattern '(president|secretary|treasurer|primary contact|technical contact).*(e-?mail|phone|mobile|cell)')
    $normalized = @($contactVals | ForEach-Object { $_.ToLowerInvariant().Trim() } | Where-Object { -not (Test-PlaceholderValue -Value $_) })
    $dupes = @($normalized | Group-Object | Where-Object { $_.Count -gt 1 })
    if ($dupes.Count -gt 0) { $penalty += 6; $penalties.Add('duplicate contact value reused across roles -6') }

    $score = [math]::Round([math]::Max(0.0, [math]::Min(100.0, $base - $penalty)), 1)

    # Top gaps: highest-weighted components with the least fill.
    $gapLabels = @{
        fillRate  = 'incomplete fields'
        ein       = 'missing/invalid EIN'
        guidestar = 'GuideStar/Candid link'
        facebook  = 'Facebook page'
        linkedin  = 'LinkedIn org page'
        usAttest  = 'US attestation'
        tos       = 'ToS not accepted'
        mission   = 'weak mission'
        hosting   = 'hosting not answered'
        board     = 'incomplete board roster'
        contacts  = 'missing primary/technical contact'
        timezone  = 'US timezone'
    }
    $gaps = foreach ($key in $weights.Keys) {
        $f = [double]($fill[$key])
        if ($f -lt 0.75) {
            [pscustomobject]@{ label = $gapLabels[$key]; missingPts = [math]::Round([double]$weights[$key] * (1 - $f), 1) }
        }
    }
    $topGaps = @($gaps | Sort-Object -Property missingPts -Descending | Select-Object -First 3 | ForEach-Object { $_.label })

    $strong = @(foreach ($key in $weights.Keys) { if ([double]($fill[$key]) -ge 0.99) { $gapLabels[$key] -replace '^(missing/invalid |missing |weak |incomplete )', '' } })
    $strongTxt = if ($strong.Count -gt 0) { ($strong | Select-Object -First 3) -join ', ' } else { 'few complete fields' }
    $gapTxt = if ($topGaps.Count -gt 0) { $topGaps -join ', ' } else { 'none' }
    $penTxt = if ($penalties.Count -gt 0) { ' Penalties: ' + ($penalties -join '; ') + '.' } else { '' }
    $rationale = "Score $score/100 (pid $productPid). Strong: $strongTxt. Gaps: $gapTxt.$penTxt"

    return [pscustomobject]@{
        orderid     = [string]$Application.orderid
        ordernum    = [string]$Application.ordernum
        clientid    = [string]$Application.clientid
        pid         = $productPid
        productName = [string]$Application.productName
        charityName = [string]$Application.charityName
        score       = $score
        breakdown   = $breakdown
        penalties   = $penalties.ToArray()
        topGaps     = $topGaps
        rationale   = $rationale
    }
}

function Get-ApplicationRanking {
    # Deterministic ranking: score DESC, then orderid ASC (numeric) as a stable
    # tie-break. Returns the scored objects in ranked order.
    [OutputType([object[]])]
    param([Parameter()]$ScoredApplications)
    return @($ScoredApplications | Sort-Object -Property `
        @{ Expression = 'score'; Descending = $true }, `
        @{ Expression = { [int64]($_.orderid) }; Descending = $false })
}

function Get-CharityDisplayName {
    # Resolve the charity/org name from the onboarding custom fields (companyname
    # is empty on these applications). Falls back to the order name (the
    # applicant's own name) prefixed so it is never mistaken for the org.
    [OutputType([string])]
    param([Parameter()]$Fields, [string]$FallbackName)
    $org = Get-FieldValue -Fields $Fields -Pattern $script:FieldPatterns.OrgName
    if (-not (Test-PlaceholderValue -Value $org)) { return $org }
    if (-not [string]::IsNullOrWhiteSpace($FallbackName)) { return $FallbackName }
    return '(unknown org)'
}

# When dot-sourced (unit tests), stop here: only the functions are needed.
if ($MyInvocation.InvocationName -eq '.') { return }

# ==========================================================================
# Runner (live) -- everything below performs / orchestrates API calls.
# ==========================================================================

function New-DirectoryForFile {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return }
    $dir = Split-Path -Parent $Path
    if (-not [string]::IsNullOrWhiteSpace($dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
}

function Write-Summary {
    param([string]$Text)
    if (-not [string]::IsNullOrWhiteSpace($env:GITHUB_STEP_SUMMARY)) {
        $Text | Out-File -FilePath $env:GITHUB_STEP_SUMMARY -Append -Encoding utf8
    }
    Write-Host $Text
}

function Get-PendingOnboardingApplications {
    # Reads the live pending onboarding applications (both pids) and returns a
    # list of application objects ready for scoring. Read-only.
    param(
        [Parameter(Mandatory = $true)][string]$Api,
        [Parameter(Mandatory = $true)][hashtable]$Auth,
        [Parameter(Mandatory = $true)][int[]]$ProductPids,
        [int]$PageSizeInner = 250
    )

    # 1) Pending orders -> orderid -> order metadata.
    $pendingOrders = @{}
    $start = 0
    while ($true) {
        $body = $Auth.Clone()
        $body.action = 'GetOrders'
        $body.status = 'Pending'
        $body.limitstart = $start
        $body.limitnum = $PageSizeInner
        $resp = Invoke-WhmcsApi -ApiUrl $Api -Body $body
        $page = Get-OrdersFromResponse -Response $resp
        if ($page.Count -le 0) { break }
        foreach ($o in $page) {
            $oid = [string]$o.id
            if (-not [string]::IsNullOrWhiteSpace($oid)) { $pendingOrders[$oid] = $o }
        }
        $start += $page.Count
        $total = 0
        if ($resp.totalresults) { [void][int]::TryParse($resp.totalresults.ToString(), [ref]$total) }
        if ($total -gt 0 -and $start -ge $total) { break }
    }
    Write-Host "Pending orders found: $($pendingOrders.Count)"

    # 2) Onboarding services per pid, joined to pending orders via orderid.
    $apps = [System.Collections.Generic.List[object]]::new()
    foreach ($productPid in $ProductPids) {
        $start = 0
        while ($true) {
            $body = $Auth.Clone()
            $body.action = 'GetClientsProducts'
            $body.pid = $productPid
            $body.limitstart = $start
            $body.limitnum = $PageSizeInner
            $resp = Invoke-WhmcsApi -ApiUrl $Api -Body $body
            $page = Get-ProductsFromResponse -Response $resp
            if ($page.Count -le 0) { break }
            foreach ($svc in $page) {
                $orderId = [string]$svc.orderid
                if ([string]::IsNullOrWhiteSpace($orderId) -or -not $pendingOrders.ContainsKey($orderId)) { continue }
                $order = $pendingOrders[$orderId]
                $fields = Get-CustomFieldNodes -Node $svc
                $charity = Get-CharityDisplayName -Fields $fields -FallbackName ''
                $apps.Add([pscustomobject]@{
                        orderid     = $orderId
                        ordernum    = [string]$order.ordernum
                        clientid    = [string]$order.userid
                        pid         = [int]$productPid
                        productName = [string]$svc.name
                        charityName = $charity
                        fields      = $fields
                    })
            }
            $start += $page.Count
            $total = 0
            if ($resp.totalresults) { [void][int]::TryParse($resp.totalresults.ToString(), [ref]$total) }
            if ($total -gt 0 -and $start -ge $total) { break }
        }
    }
    return $apps
}

function Get-WhmcsOrderStatus {
    [OutputType([string])]
    param(
        [Parameter(Mandatory = $true)][string]$Api,
        [Parameter(Mandatory = $true)][hashtable]$Auth,
        [Parameter(Mandatory = $true)][string]$OrderId
    )
    $body = $Auth.Clone()
    $body.action = 'GetOrders'
    $body.id = $OrderId
    $resp = Invoke-WhmcsApi -ApiUrl $Api -Body $body
    foreach ($o in (Get-OrdersFromResponse -Response $resp)) {
        if ([string]$o.id -eq $OrderId) { return [string]$o.status }
    }
    return $null
}

try {
    $api = Resolve-WhmcsApiUrl -ApiUrlParam $ApiUrl
    $creds = Resolve-WhmcsCredentials -IdentifierParam $Identifier -SecretParam $Secret -CredentialsJsonParam $CredentialsJson
    $resolvedAccessKey = Resolve-WhmcsAccessKey -AccessKeyParam $AccessKey
    $auth = New-WhmcsAuthBody -Creds $creds -AccessKey $resolvedAccessKey

    $productPids = @($PreProductId, $FullProductId)

    # Read + score the live pending onboarding applications (both modes need the
    # ranking; approve additionally uses it to describe what it is accepting).
    $rawApps = Get-PendingOnboardingApplications -Api $api -Auth $auth -ProductPids $productPids -PageSizeInner $PageSize
    $scored = foreach ($a in $rawApps) { Get-ApplicationScore -Application $a }

    $groups = [ordered]@{
        "$PreProductId"  = @{ label = "pre-501(c)(3) (pid $PreProductId)"; items = @() }
        "$FullProductId" = @{ label = "full 501(c)(3) (pid $FullProductId)"; items = @() }
    }
    foreach ($s in $scored) {
        $k = "$([int]$s.pid)"
        if ($groups.Contains($k)) { $groups[$k].items = @(Get-ApplicationRanking -ScoredApplications (@($groups[$k].items) + $s)) }
    }

    # ------- Ranked report (both modes emit it) -------
    Write-Summary "## WHMCS application triage ($Mode)"
    Write-Summary ''
    foreach ($k in $groups.Keys) {
        $g = $groups[$k]
        $items = @($g.items)
        Write-Summary "### $($g.label) - $($items.Count) pending application(s)"
        Write-Summary ''
        if ($items.Count -eq 0) {
            Write-Summary '_No pending applications in this group._'
            Write-Summary ''
            continue
        }
        Write-Summary '| rank | score | orderid | charity | top gaps |'
        Write-Summary '| --- | --- | --- | --- | --- |'
        $rank = 0
        foreach ($it in $items) {
            $rank++
            $charity = ([string]$it.charityName) -replace '\|', '\\|'
            $gaps = (@($it.topGaps) -join ', ') -replace '\|', '\\|'
            if ([string]::IsNullOrWhiteSpace($gaps)) { $gaps = '-' }
            Write-Summary "| $rank | $($it.score) | $($it.orderid) | $charity | $gaps |"
        }
        Write-Summary ''
        $top = $items[0]
        Write-Summary "**Top of group:** order $($top.orderid) - $($top.charityName)"
        Write-Summary ''
        Write-Summary "> $($top.rationale)"
        Write-Summary ''
    }

    # ------- JSON artifact (deterministic) -------
    New-DirectoryForFile -Path $OutputFile
    $report = [pscustomobject]@{
        mode        = $Mode
        generatedAt = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        groups      = @(
            foreach ($k in $groups.Keys) {
                [pscustomobject]@{
                    pid          = [int]$k
                    label        = $groups[$k].label
                    applications = @($groups[$k].items)
                }
            }
        )
    }
    $report | ConvertTo-Json -Depth 12 | Out-File -FilePath $OutputFile -Encoding utf8
    Write-Host "Report written: $OutputFile"

    if ($Mode -eq 'report') {
        exit 0
    }

    # ==================== approve mode ====================
    $ids = @(($OrderIds -split ',') | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    if ($ids.Count -eq 0) {
        throw "approve mode requires -OrderIds (a comma-separated list of order ids). Refusing to run without an explicit list -- there is no accept-all and no accept-top-N."
    }

    # Index the pending onboarding orders we just scored for a pre-accept summary
    # and to confirm each requested id really is a pending onboarding order.
    $byOrder = @{}
    foreach ($s in $scored) { $byOrder[[string]$s.orderid] = $s }

    Write-Summary "### Acceptance (approve mode)"
    Write-Summary ''
    $accepted = [System.Collections.Generic.List[object]]::new()
    foreach ($id in $ids) {
        $summaryItem = $byOrder[$id]
        if ($summaryItem) {
            Write-Summary "- **order $id** - $($summaryItem.charityName) (pid $($summaryItem.pid), score $($summaryItem.score))"
            Write-Host "Pre-accept: order $id => $($summaryItem.rationale)"
        }
        else {
            Write-Summary "- **order $id** - not found among pending onboarding (pid $PreProductId/$FullProductId) applications"
        }

        # Safety re-check against the live API (never accept a non-Pending order,
        # and only accept ids confirmed as onboarding orders in this run).
        $current = Get-WhmcsOrderStatus -Api $api -Auth $auth -OrderId $id
        if ($null -eq $current) {
            Write-Summary "  - SKIP: order $id not found via GetOrders."
            $accepted.Add([pscustomobject]@{ orderid = $id; result = 'skipped'; reason = 'not-found' })
            continue
        }
        if ($current -ne 'Pending') {
            Write-Summary "  - SKIP: order $id is '$current', not Pending."
            $accepted.Add([pscustomobject]@{ orderid = $id; result = 'skipped'; reason = "already-$($current.ToLowerInvariant())" })
            continue
        }
        if (-not $summaryItem) {
            Write-Summary "  - SKIP: order $id is Pending but is not a pid $PreProductId/$FullProductId onboarding order in this run."
            $accepted.Add([pscustomobject]@{ orderid = $id; result = 'skipped'; reason = 'not-onboarding-order' })
            continue
        }

        # $0 "No Payment Required" onboarding order: AcceptOrder does not collect
        # payment. sendemail=true so the charity receives the acceptance email.
        $body = $auth.Clone()
        $body.action = 'AcceptOrder'
        $body.orderid = $id
        $body.sendemail = [bool]$SendEmail
        if ($AutoSetup) { $body.autosetup = $true }
        [void](Invoke-WhmcsApi -ApiUrl $api -Body $body)
        Write-Summary "  - ACCEPTED: order $id (sendemail=$SendEmail, autosetup=$AutoSetup)."
        $accepted.Add([pscustomobject]@{ orderid = $id; result = 'accepted'; pid = $summaryItem.pid; charity = $summaryItem.charityName })
    }

    $acceptFile = ($OutputFile -replace '\.json$', '') + '.accept-results.json'
    $accepted | ConvertTo-Json -Depth 6 | Out-File -FilePath $acceptFile -Encoding utf8
    Write-Host "Accept results written: $acceptFile"
    exit 0
}
catch {
    Write-Error $_
    exit 1
}
