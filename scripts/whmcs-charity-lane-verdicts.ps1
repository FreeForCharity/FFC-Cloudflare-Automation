<#
.SYNOPSIS
    Charity-lane Gate 1 / Gate 2 verdicts (READ-ONLY): ACCEPT / NEED_INFO /
    ESCALATE for every Pending onboarding (pid 16 / 33) and website (pid 40)
    order, with machine-readable reason codes.

.DESCRIPTION
    Step G1.3 of the charity-lane pipeline (docs/charity-lane-pipeline.md, epic
    #1618). It runs the machine-checkable subset of the ffcadmin inventory section 5
    validation set against WHMCS data plus public HTTP and says, per order, what
    the Onboarding Conductor should do next:

      ACCEPT     every machine check passed. Acceptance itself is NOT done here
                 (this script never writes); the accept workflow re-runs the
                 policy on its own.
      NEED_INFO  a data gap the applicant can fix (missing page, unticked
                 attestation, track mismatch, missing EIN).
      ESCALATE   anything the checks cannot classify, an eligibility doubt, a
                 duplicate, or a check that could not run. A human decides.

    Gate 1 (pid 16 pre-501(c)(3), pid 33 501(c)(3)) checks:
      - org Facebook + LinkedIn Page URLs present, well-formed, and answering
        HTTP 2xx/3xx (no redirect followed). Any other answer -- including the
        404 / 999 / 400 responses LinkedIn and Facebook hand to non-browser
        clients for pages that DO exist -- is UNVERIFIED -> ESCALATE, never a
        NEED_INFO, so a bot wall can never send an applicant a false ticket;
      - Terms of Service and US-establishment attestations ticked;
      - legal-status answer present, recognized, and matching the track;
      - EIN present; for pid 33 confirmed as a 501(c)(3) on ProPublica (the
        same Invoke-ProPublicaEin / Get-ApplicationVerification path 226 uses),
        with the IRS name and the Candid-slug EIN cross-checked;
      - no duplicate: another Pending onboarding order or an Active onboarding
        service on the same client, another client with the same org name
        (GetClients) or the same desired domain (onboarding services +
        GetClientsDomains).
    Gate 2 (pid 40) checks:
      - the referenced onboarding service (by service/order id, else the
        client's own pid 16/33 service) exists, belongs to this client, and is
        Active;
      - the "fully approved" attestation is ticked;
      - no other Pending pid 40 order and no Active pid 40 on the client.
    Fraud: FraudLabs Pro is readable only on 228's `fraudlabspro-prod-read`
    environment, so this script (on `whmcs-prod-read`) cannot screen. It writes
    every row UNSCREENED: fraud_check `not_run` with FRAUD_CHECK_NOT_RUN, an
    ESCALATE code, so the intermediate report can never say ACCEPT. It also
    writes the FraudLabs lookup keys (-FraudLookupFile: order id -> the
    -OrderIdField value 228 sends). Workflow 232's `fraud_screen` job looks each
    order up exactly as 228 does (scripts/whmcs-charity-lane-fraud-screen.ps1)
    and its `finalize` job applies the results (Merge-LaneFraudOutcome, through
    scripts/whmcs-charity-lane-fraud-merge.ps1). The FraudLabs result is the
    only thing that can lift FRAUD_CHECK_NOT_RUN: APPROVE below the flag score
    -> fraud_check `pass`; REJECT or a high score -> FRAUD_FLAGGED; REVIEW ->
    FRAUD_REVIEW; anything else, including a failed or missing lookup ->
    FRAUD_CHECK_FAILED. All three are ESCALATE (fail closed).

    REUSE: every field reader, EIN resolver, ProPublica lookup and the readiness
    score come from scripts/whmcs-application-triage.ps1 (dot-sourced; its runner
    is guarded). Nothing about scoring is re-implemented here.

    PII: the hub repo is PUBLIC. The ONLY values emitted (JSON artifact, job
    summary, issue comment) are order id, client id, pid, gate, org name,
    desired domain, verdict, reason codes, readiness score, fraud_check and
    fraud_score. The lookup-key file carries only order id + the order field
    228 sends to FraudLabs (WHMCS order number by default), no applicant data;
    the workflow keeps it as a 1-day transport artifact for the screening job. An
    org name that looks like a person's name or carries contact data is
    withheld (ORG_NAME_WITHHELD); a desired domain is emitted only after it
    parses as a bare hostname. Applicant names, emails, phones, addresses, URLs
    and EINs are read in memory and never written.

    Pure decision functions live above the dot-source guard and are unit-tested
    with fixtures (tests/whmcs-charity-lane-verdicts.Tests.ps1); the live runner
    below the guard performs only WHMCS reads and public GETs.
#>
[CmdletBinding()]
param(
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

    [Parameter()]
    [string]$OutputFile = 'artifacts/whmcs/charity-lane-verdicts.json',

    # Markdown copy of the summary table (the workflow posts it as an issue comment).
    [Parameter()]
    [string]$MarkdownFile = 'artifacts/whmcs/charity-lane-verdicts.md',

    [Parameter()]
    [string]$EinApiBaseUrl = 'https://projects.propublica.org/nonprofits/api/v2/organizations',

    # Zero outbound non-WHMCS calls: no ProPublica, no social-page probes. Every
    # check that needed one reports UNVERIFIED (-> ESCALATE). For offline runs.
    [Parameter()]
    [switch]$SkipNetworkChecks,

    [Parameter()]
    [ValidateRange(1, 250)]
    [int]$PageSize = 250,

    # The WHMCS order field sent to FraudLabs Pro as its order id: 228's
    # `order_id_field`, with the same default.
    [Parameter()]
    [ValidateSet('ordernum', 'id')]
    [string]$OrderIdField = 'ordernum',

    # Where to write the FraudLabs lookup keys ([{order_id, lookup_id}]).
    [Parameter()]
    [string]$FraudLookupFile = 'artifacts/whmcs/charity-lane-fraud-lookups.json'
)

$ErrorActionPreference = 'Stop'

# Capture this script's parameters BEFORE dot-sourcing the triage library. A
# dot-sourced script binds its own param() block in THIS scope, so the triage
# defaults for $ApiUrl / $OutputFile / $EinApiBaseUrl / $PageSize (and the
# credential params) would silently overwrite the values this run was given.
$script:LaneRunConfig = @{
    ApiUrl            = $ApiUrl
    Identifier        = $Identifier
    Secret            = $Secret
    CredentialsJson   = $CredentialsJson
    AccessKey         = $AccessKey
    OutputFile        = $OutputFile
    MarkdownFile      = $MarkdownFile
    EinApiBaseUrl     = $EinApiBaseUrl
    SkipNetworkChecks = [bool]$SkipNetworkChecks
    PageSize          = $PageSize
    OrderIdField      = $OrderIdField
    FraudLookupFile   = $FraudLookupFile
}

# Field readers, EIN resolution, ProPublica verification and readiness scoring.
# Its runner is guarded by `if ($MyInvocation.InvocationName -eq '.') { return }`,
# so dot-sourcing defines functions only and makes no API call.
. (Join-Path $PSScriptRoot 'whmcs-application-triage.ps1')

# --------------------------------------------------------------------------
# Reason-code catalog. Severity decides the verdict: any ESCALATE code ->
# ESCALATE; else any NEED_INFO code -> NEED_INFO; else ACCEPT. INFO codes are
# recorded but never block. An unknown code is treated as ESCALATE.
# --------------------------------------------------------------------------

$script:LaneProductPids = @{ Gate1 = @(16, 33); Gate2 = @(40) }

$script:LaneReasonCodes = [ordered]@{
    # Gate 1 -- application data
    ORG_NAME_MISSING              = @{ severity = 'NEED_INFO'; description = 'No organization name on the client record or application.' }
    ORG_NAME_IS_APPLICANT         = @{ severity = 'NEED_INFO'; description = "The organization name is the applicant's own name; withheld from this report." }
    ORG_NAME_WITHHELD             = @{ severity = 'INFO'; description = "The organization name looks like a person's name or carries contact data (heuristic); withheld from this report only. Not an eligibility signal." }
    DESIRED_DOMAIN_MISSING        = @{ severity = 'INFO'; description = 'No desired domain on the application (needed at Gate 3, not Gate 1).' }
    DESIRED_DOMAIN_INVALID        = @{ severity = 'INFO'; description = 'The desired-domain answer does not parse as a hostname; withheld from this report.' }
    FACEBOOK_URL_MISSING          = @{ severity = 'NEED_INFO'; description = 'Organization Facebook Page URL missing.' }
    FACEBOOK_URL_INVALID          = @{ severity = 'NEED_INFO'; description = 'Organization Facebook Page URL is not a Facebook page URL.' }
    FACEBOOK_URL_UNVERIFIED       = @{ severity = 'ESCALATE'; description = 'Facebook Page URL did not answer HTTP 2xx/3xx (or was not probed); a human must open it.' }
    LINKEDIN_URL_MISSING          = @{ severity = 'NEED_INFO'; description = 'Organization LinkedIn Page URL missing.' }
    LINKEDIN_URL_NOT_ORG_PAGE     = @{ severity = 'NEED_INFO'; description = 'LinkedIn URL is not an organization page (a company, school or showcase page).' }
    LINKEDIN_URL_UNVERIFIED       = @{ severity = 'ESCALATE'; description = 'LinkedIn Page URL did not answer HTTP 2xx/3xx (or was not probed); a human must open it.' }
    TOS_NOT_ATTESTED              = @{ severity = 'NEED_INFO'; description = 'Terms of Service certification not ticked.' }
    US_NOT_ATTESTED               = @{ severity = 'NEED_INFO'; description = 'US-establishment attestation not ticked.' }
    LEGAL_STATUS_MISSING          = @{ severity = 'NEED_INFO'; description = 'Legal-status answer missing.' }
    LEGAL_STATUS_UNRECOGNIZED     = @{ severity = 'ESCALATE'; description = 'Legal-status answer present but not a recognized value.' }
    TRACK_MISMATCH                = @{ severity = 'NEED_INFO'; description = 'Filed on the wrong track for the legal-status answer or IRS record (pid 16 vs 33).' }
    EIN_MISSING                   = @{ severity = 'NEED_INFO'; description = 'No valid EIN on the application (field or Candid link).' }
    EIN_NOT_VERIFIED              = @{ severity = 'ESCALATE'; description = 'pid 33: EIN not confirmed on ProPublica (not found, lookup failed, or not run).' }
    EIN_NOT_501C3                 = @{ severity = 'ESCALATE'; description = 'pid 33: ProPublica lists the EIN, but not as a 501(c)(3).' }
    EIN_NAME_MISMATCH             = @{ severity = 'ESCALATE'; description = 'The IRS name for the EIN shares no significant word with the application org name.' }
    CANDID_EIN_MISMATCH           = @{ severity = 'ESCALATE'; description = 'The EIN in the Candid/GuideStar link differs from the EIN field.' }
    NO_APPLICATION_FIELDS         = @{ severity = 'ESCALATE'; description = 'The service carries no custom fields; the application cannot be read.' }
    # Duplicates (both gates)
    DUPLICATE_ORDER_SAME_CLIENT   = @{ severity = 'ESCALATE'; description = 'The same client has another Pending order on this gate.' }
    CLIENT_ALREADY_ACTIVE         = @{ severity = 'ESCALATE'; description = 'The client already holds an Active service on this gate.' }
    DUPLICATE_ORG_OTHER_CLIENT    = @{ severity = 'ESCALATE'; description = 'Another client has the same organization name.' }
    DUPLICATE_DOMAIN_OTHER_CLIENT = @{ severity = 'ESCALATE'; description = 'Another client has applied for, or holds, the same domain.' }
    DUPLICATE_CHECK_FAILED        = @{ severity = 'ESCALATE'; description = 'A duplicate lookup failed, so duplicates could not be ruled out.' }
    # Gate 2 -- website order
    GATE2_FIELDS_ABSENT           = @{ severity = 'ESCALATE'; description = 'pid 40 order carries neither the application reference nor the approval attestation (possibly a 224 marker order).' }
    GATE2_ATTESTATION_MISSING     = @{ severity = 'NEED_INFO'; description = "The 'onboarding application fully approved' attestation is not ticked." }
    GATE2_REFERENCE_NOT_FOUND     = @{ severity = 'ESCALATE'; description = 'The referenced service/order id matches no onboarding service.' }
    GATE2_REFERENCE_OTHER_CLIENT  = @{ severity = 'ESCALATE'; description = 'The referenced onboarding service belongs to a different client.' }
    GATE1_SERVICE_NOT_FOUND       = @{ severity = 'NEED_INFO'; description = 'The client has no pid 16/33 onboarding application.' }
    GATE1_SERVICE_PENDING         = @{ severity = 'NEED_INFO'; description = 'The Gate 1 application is still Pending; re-check after it is decided.' }
    GATE1_SERVICE_INACTIVE        = @{ severity = 'ESCALATE'; description = 'The Gate 1 service exists but is neither Active nor Pending (cancelled, suspended, terminated, fraud).' }
    # Order shape / tooling
    ORDER_HAS_MULTIPLE_LANE_ITEMS = @{ severity = 'ESCALATE'; description = 'One order carries more than one onboarding/website item; accepting it accepts all of them.' }
    UNSUPPORTED_PID               = @{ severity = 'ESCALATE'; description = 'Not a charity-lane product.' }
    # FraudLabs Pro screening (applied by Merge-LaneFraudOutcome). All ESCALATE:
    # nothing is ACCEPTed without fraud_check = pass.
    FRAUD_CHECK_NOT_RUN           = @{ severity = 'ESCALATE'; description = 'FraudLabs Pro screening has not been applied to this row (the unscreened report the WHMCS job writes). Never ACCEPT without it.' }
    FRAUD_CHECK_FAILED            = @{ severity = 'ESCALATE'; description = 'The FraudLabs Pro lookup failed or gave no usable verdict (request error, FraudLabs error code, no stored result, unknown status, or an APPROVE without a score). Fail closed.' }
    FRAUD_FLAGGED                 = @{ severity = 'ESCALATE'; description = 'FraudLabs Pro verdict REJECT, or a FraudLabs score at or above the flag threshold (80).' }
    FRAUD_REVIEW                  = @{ severity = 'ESCALATE'; description = 'FraudLabs Pro verdict REVIEW.' }
}

# Every fraud code: Merge-LaneFraudOutcome replaces whichever of these a row
# carries, so applying an outcome is idempotent.
$script:LaneFraudCodes = @('FRAUD_CHECK_NOT_RUN', 'FRAUD_CHECK_FAILED', 'FRAUD_FLAGGED', 'FRAUD_REVIEW')

# FraudLabs scores run 1-100 (100 = highest risk). An APPROVE at or above this
# score is still FRAUD_FLAGGED.
$script:LaneFraudScoreFlagAt = 80

$script:LaneFieldPatterns = @{
    # Org social PAGE fields. The 'page' suffix mirrors ffcadmin's
    # whmcs-applications.mjs (fields slugged facebook-page / linkedin-page) and
    # keeps board-member and applicant profile fields out by construction; the
    # fallback excludes every role/person field explicitly.
    FacebookPage     = 'facebook[\s_-]*page'
    FacebookFallback = '^(?!.*(president|vice|secretary|treasurer|board|chair|member|primary|technical|personal|your own)).*facebook'
    LinkedInPage     = 'linkedin[\s_-]*page'
    LinkedInFallback = '^(?!.*(president|vice|secretary|treasurer|board|chair|member|primary|technical|personal|your own)).*linkedin'
    # 'I certify our organization is legally established and operating in the United States'
    UsAttest         = 'legally established|operating in the united states|based in the (us|united states)|(?=.*(certify|attest))(?=.*(united states|\bu\.?s\.?\b))'
    # 'What is your current/desired domain name?' (pid 16). pid 33 has no domain question.
    DesiredDomain    = '(desired|requested|current).*domain|domain.*(desired|requested)'
    # pid 40 Gate-2 proof fields (#671): the approved-application reference and
    # the "fully approved" checkbox. The reference pattern excludes the checkbox
    # label, which also says "onboarding application".
    Gate2Attest      = 'fully approved|has been approved|been approved by'
    Gate2Reference   = '^(?!.*(fully approved|has been approved|been approved by)).*(reference|onboarding application|application (id|number)|service id|order id)'
}

$script:InactiveServiceStatuses = @('Cancelled', 'Terminated')

# --------------------------------------------------------------------------
# Pure helpers.
# --------------------------------------------------------------------------

function Get-LaneGate {
    [OutputType([int])]
    param([int]$ProductPid)
    if ($script:LaneProductPids.Gate1 -contains $ProductPid) { return 1 }
    if ($script:LaneProductPids.Gate2 -contains $ProductPid) { return 2 }
    return 0
}

function Get-LaneSeverityRank {
    [OutputType([int])]
    param([string]$Severity)
    switch ($Severity) {
        'INFO' { return 0 }
        'NEED_INFO' { return 1 }
        default { return 2 }
    }
}

function Get-LaneCodeSeverity {
    # Unknown code => ESCALATE (fail closed: a code the catalog does not know is
    # by definition something this script cannot classify).
    [OutputType([string])]
    param([string]$Code)
    if ($script:LaneReasonCodes.Contains($Code)) { return [string]$script:LaneReasonCodes[$Code].severity }
    return 'ESCALATE'
}

function Get-LaneVerdictFromCodes {
    [OutputType([string])]
    param([string[]]$Codes)
    $max = 0
    foreach ($c in @($Codes | Where-Object { $_ })) {
        $r = Get-LaneSeverityRank -Severity (Get-LaneCodeSeverity -Code $c)
        if ($r -gt $max) { $max = $r }
    }
    switch ($max) {
        0 { return 'ACCEPT' }
        1 { return 'NEED_INFO' }
        default { return 'ESCALATE' }
    }
}

function Get-SortedLaneCodes {
    # Most severe first, then alphabetical; de-duplicated.
    [OutputType([string[]])]
    param([string[]]$Codes)
    $unique = @($Codes | Where-Object { $_ } | Select-Object -Unique)
    return @($unique | Sort-Object -Property @{ Expression = { - (Get-LaneSeverityRank -Severity (Get-LaneCodeSeverity -Code $_)) } }, @{ Expression = { $_ } })
}

function ConvertTo-LaneDomain {
    # A desired-domain answer reduced to a bare lowercase hostname, or '' when it
    # does not parse as one. Anything carrying '@' is rejected outright (an email
    # address is never a domain answer, and is PII).
    [OutputType([string])]
    param([string]$Value)
    $v = Get-HrefOrRaw -Value $Value
    if ([string]::IsNullOrWhiteSpace($v)) { return '' }
    $v = $v.Trim().ToLowerInvariant()
    if ($v.Contains('@')) { return '' }
    $v = $v -replace '^[a-z][a-z0-9+.-]*://', ''
    $v = $v -replace '[/?#].*$', ''
    $v = $v -replace ':\d+$', ''
    $v = $v.TrimEnd('.')
    if ($v.StartsWith('www.')) { $v = $v.Substring(4) }
    if ($v.Length -lt 4 -or $v.Length -gt 253) { return '' }
    if ($v -notmatch '^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$') { return '' }
    return $v
}

function ConvertTo-LaneOrgKey {
    # Normalized org-name key for duplicate comparison: lowercase alphanumerics,
    # a leading 'the' and trailing legal suffixes removed.
    [OutputType([string])]
    param([string]$Name)
    if ([string]::IsNullOrWhiteSpace($Name)) { return '' }
    $t = $Name.ToLowerInvariant() -replace '&', ' and '
    $t = $t -replace '[^a-z0-9\s]', ' '
    $words = @($t -split '\s+' | Where-Object { $_ })
    if ($words.Count -gt 1 -and $words[0] -eq 'the') { $words = @($words | Select-Object -Skip 1) }
    while ($words.Count -gt 1 -and @('inc', 'incorporated', 'llc', 'corp', 'corporation', 'co', 'ltd', 'nfp', 'npc') -contains $words[-1]) {
        $words = @($words | Select-Object -First ($words.Count - 1))
    }
    return ($words -join '')
}

function Get-LaneOrgName {
    # The org name this report may print, or why it may not. Sources: the client
    # companyname (authoritative), then an org-name custom field. The applicant's
    # own name is NEVER a fallback (unlike 226's display name): it is PII.
    # Returns @{ Name; Raw; Status = 'ok' | 'missing' | 'applicant' | 'withheld' }.
    # 'applicant' = the name IS the applicant's own (certain); 'withheld' = a
    # heuristic says it may be a person or contact data (uncertain). Raw is for
    # in-memory comparison only and must never be written.
    [OutputType([pscustomobject])]
    param(
        [Parameter()][string]$CompanyName,
        [Parameter()][string]$ApplicantFullName,
        [Parameter()]$Fields
    )
    $raw = ''
    if (-not (Test-PlaceholderValue -Value $CompanyName)) { $raw = $CompanyName.Trim() }
    else {
        $fromField = Get-FieldValue -Fields $Fields -Pattern $script:FieldPatterns.OrgName
        if (-not (Test-PlaceholderValue -Value $fromField)) { $raw = $fromField.Trim() }
    }
    if ([string]::IsNullOrWhiteSpace($raw)) {
        return [pscustomobject]@{ Name = ''; Raw = ''; Status = 'missing' }
    }
    if (-not [string]::IsNullOrWhiteSpace($ApplicantFullName) -and
        $raw.ToLowerInvariant() -eq $ApplicantFullName.Trim().ToLowerInvariant()) {
        return [pscustomobject]@{ Name = ''; Raw = $raw; Status = 'applicant' }
    }
    $withheld = $false
    if ((Test-PersonNameCompany -Name $raw).IsPerson) { $withheld = $true }
    if ($raw.Contains('@')) { $withheld = $true }
    if (($raw -replace '[\s().+-]', '') -match '\d{7,}') { $withheld = $true }
    if ($raw -match '://|www\.') { $withheld = $true }
    if ($withheld) {
        return [pscustomobject]@{ Name = ''; Raw = $raw; Status = 'withheld' }
    }
    # Printable form: control and markdown/HTML-structural characters removed.
    $clean = ($raw -replace '[\x00-\x1F\x7F|<>`\[\]\\*_#]', ' ' -replace '\s+', ' ').Trim()
    if ($clean.Length -gt 120) { $clean = $clean.Substring(0, 120).TrimEnd() }
    return [pscustomobject]@{ Name = $clean; Raw = $raw; Status = 'ok' }
}

function Get-LaneSocialPageUrl {
    # Resolve the org Facebook / LinkedIn PAGE URL from the application and say
    # whether it is usable. Returns @{ Url; Code } -- Code is $null when the URL
    # is well formed (it still has to answer the HTTP probe), else the gap code.
    [OutputType([pscustomobject])]
    param(
        [Parameter()]$Fields,
        [Parameter(Mandatory = $true)][ValidateSet('facebook', 'linkedin')][string]$Kind
    )
    if ($Kind -eq 'facebook') {
        $value = Get-FieldValue -Fields $Fields -Pattern $script:LaneFieldPatterns.FacebookPage
        if ([string]::IsNullOrWhiteSpace($value)) { $value = Get-FieldValue -Fields $Fields -Pattern $script:LaneFieldPatterns.FacebookFallback }
        if (Test-PlaceholderValue -Value $value) { return [pscustomobject]@{ Url = ''; Code = 'FACEBOOK_URL_MISSING' } }
        if (-not (Test-FacebookPageUrl -Value $value)) { return [pscustomobject]@{ Url = ''; Code = 'FACEBOOK_URL_INVALID' } }
        return [pscustomobject]@{ Url = (Get-HrefOrRaw -Value $value); Code = $null }
    }
    $value = Get-FieldValue -Fields $Fields -Pattern $script:LaneFieldPatterns.LinkedInPage
    if ([string]::IsNullOrWhiteSpace($value)) { $value = Get-FieldValue -Fields $Fields -Pattern $script:LaneFieldPatterns.LinkedInFallback }
    if (Test-PlaceholderValue -Value $value) { return [pscustomobject]@{ Url = ''; Code = 'LINKEDIN_URL_MISSING' } }
    if (-not (Test-LinkedInOrgUrl -Value $value)) { return [pscustomobject]@{ Url = ''; Code = 'LINKEDIN_URL_NOT_ORG_PAGE' } }
    return [pscustomobject]@{ Url = (Get-HrefOrRaw -Value $value); Code = $null }
}

function Test-LaneProbeAnswered {
    # The inventory's "answering" test: an HTTP status in 200-399 with no redirect
    # followed. $null (no probe, transport error) and every other status --
    # including LinkedIn's 999 and the 400/404 that Facebook and LinkedIn return
    # to non-browser clients for real pages -- are NOT answered.
    [OutputType([bool])]
    param($Status)
    if ($null -eq $Status) { return $false }
    $code = 0
    if (-not [int]::TryParse([string]$Status, [ref]$code)) { return $false }
    return ($code -ge 200 -and $code -le 399)
}

function Get-LaneProp {
    # Read a property off a pscustomobject OR a hashtable. The unary comma keeps
    # an EMPTY array intact on return: without it PowerShell unrolls @() to
    # $null, and "no other owner" would read as "lookup failed".
    param($Object, [Parameter(Mandatory = $true)][string]$Name)
    if ($null -eq $Object) { return $null }
    if ($Object -is [System.Collections.IDictionary]) {
        if ($Object.Contains($Name)) { return , $Object[$Name] }
        return $null
    }
    if ($Object.PSObject.Properties[$Name]) { return , $Object.$Name }
    return $null
}

function Resolve-LaneGate1Service {
    # Gate 2: find the pid 16/33 service a pid 40 order depends on. A numeric
    # reference is matched against onboarding service ids and order ids; any
    # other (or no) reference falls back to the client's own onboarding
    # services, preferring an Active one. Returns @{ Service; Code } where Code
    # is $null only when an Active service on this client was found.
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)][string]$ClientId,
        [Parameter()][string]$Reference,
        [Parameter()]$OnboardingServices
    )
    $all = @($OnboardingServices | Where-Object { $_ })
    $statusCode = {
        param($svc)
        $s = [string]$svc.status
        if ($s -eq 'Active') { return $null }
        if ($s -eq 'Pending') { return 'GATE1_SERVICE_PENDING' }
        return 'GATE1_SERVICE_INACTIVE'
    }
    $ref = ([string]$Reference).Trim()
    if ($ref -match '^#?\s*\d+$') {
        $digits = $ref -replace '\D', ''
        $hits = @($all | Where-Object { [string]$_.id -eq $digits -or [string]$_.orderid -eq $digits })
        if ($hits.Count -eq 0) { return [pscustomobject]@{ Service = $null; Code = 'GATE2_REFERENCE_NOT_FOUND' } }
        $own = @($hits | Where-Object { [string]$_.clientid -eq $ClientId })
        if ($own.Count -eq 0) { return [pscustomobject]@{ Service = $null; Code = 'GATE2_REFERENCE_OTHER_CLIENT' } }
        $pick = @($own | Where-Object { [string]$_.status -eq 'Active' } | Select-Object -First 1)
        if ($pick.Count -eq 0) { $pick = @($own | Select-Object -First 1) }
        return [pscustomobject]@{ Service = $pick[0]; Code = (& $statusCode $pick[0]) }
    }
    $mine = @($all | Where-Object { [string]$_.clientid -eq $ClientId })
    if ($mine.Count -eq 0) { return [pscustomobject]@{ Service = $null; Code = 'GATE1_SERVICE_NOT_FOUND' } }
    $active = @($mine | Where-Object { [string]$_.status -eq 'Active' } | Select-Object -First 1)
    if ($active.Count -gt 0) { return [pscustomobject]@{ Service = $active[0]; Code = $null } }
    $pending = @($mine | Where-Object { [string]$_.status -eq 'Pending' } | Select-Object -First 1)
    if ($pending.Count -gt 0) { return [pscustomobject]@{ Service = $pending[0]; Code = 'GATE1_SERVICE_PENDING' } }
    return [pscustomobject]@{ Service = $mine[0]; Code = 'GATE1_SERVICE_INACTIVE' }
}

function Get-LaneDuplicateCodes {
    # Duplicate signals for one pending application. $GateServices = every
    # service (any status) on the same gate's pids; $DomainServices = every
    # onboarding + website service (for the desired-domain comparison).
    # OtherClientsSameOrg / DomainOwnerClientIds come from live lookups on the
    # application; $null there means the lookup did not run or failed.
    [OutputType([string[]])]
    param(
        [Parameter(Mandatory = $true)]$Application,
        [Parameter()]$GateServices,
        [Parameter()]$DomainServices,
        [Parameter()][string]$OrgRaw,
        [Parameter()][string]$Domain
    )
    $codes = [System.Collections.Generic.List[string]]::new()
    $clientId = [string]$Application.clientid
    $orderId = [string]$Application.orderid
    $gate = Get-LaneGate -ProductPid ([int]$Application.pid)

    $sameClient = @($GateServices | Where-Object { $_ -and [string]$_.clientid -eq $clientId -and [string]$_.orderid -ne $orderId })
    if (@($sameClient | Where-Object { [string]$_.status -eq 'Pending' }).Count -gt 0) { $codes.Add('DUPLICATE_ORDER_SAME_CLIENT') }
    if (@($sameClient | Where-Object { [string]$_.status -eq 'Active' }).Count -gt 0) { $codes.Add('CLIENT_ALREADY_ACTIVE') }

    if ($gate -ne 1) { return $codes.ToArray() }

    if (-not [string]::IsNullOrWhiteSpace($OrgRaw)) {
        $sameOrg = Get-LaneProp -Object $Application -Name 'otherClientsSameOrg'
        if ($null -eq $sameOrg) { $codes.Add('DUPLICATE_CHECK_FAILED') }
        elseif ([int]$sameOrg -gt 0) { $codes.Add('DUPLICATE_ORG_OTHER_CLIENT') }
    }

    if (-not [string]::IsNullOrWhiteSpace($Domain)) {
        $live = @($DomainServices | Where-Object {
                $_ -and [string]$_.clientid -ne $clientId -and
                ($script:InactiveServiceStatuses -notcontains [string]$_.status) -and
                ([string]$_.desiredDomain -eq $Domain -or [string]$_.serviceDomain -eq $Domain)
            })
        $owners = Get-LaneProp -Object $Application -Name 'domainOwnerClientIds'
        $ownerHit = $false
        if ($null -eq $owners) { $codes.Add('DUPLICATE_CHECK_FAILED') }
        else { $ownerHit = @(@($owners) | Where-Object { $_ -and [string]$_ -ne $clientId }).Count -gt 0 }
        if ($live.Count -gt 0 -or $ownerHit) { $codes.Add('DUPLICATE_DOMAIN_OTHER_CLIENT') }
    }
    return @($codes | Select-Object -Unique)
}

function Get-CharityLaneVerdict {
    <#
    .SYNOPSIS
        Decide ACCEPT / NEED_INFO / ESCALATE for ONE pending charity-lane order.
    .DESCRIPTION
        Pure: every live fact is precomputed on $Application by the runner (or
        by a test fixture).

        $Application:
          orderid, clientid, pid, fields (@{id;name;value} nodes),
          companyName, applicantFullName   (in memory only, never emitted),
          serviceDomain                    (the service's own domain, if any),
          orderLaneItemCount               (lane items on the same order),
          verification                     (Get-ApplicationVerification output,
                                            or $null when not run),
          urlProbes                        (hashtable URL -> HTTP status or $null),
          otherClientsSameOrg              (int, or $null = lookup failed),
          domainOwnerClientIds             (client ids from GetClientsDomains,
                                            or $null = lookup failed)
        $Context:
          OnboardingServices / WebsiteServices -- every pid 16/33 and pid 40
          service (any status) as @{ id; orderid; clientid; pid; status;
          desiredDomain; serviceDomain }.

        Returns the PII-free report row (and nothing else):
          order_id, client_id, pid, gate, org_name, desired_domain, verdict,
          reason_codes, readiness_score, fraud_check, fraud_score.

        The verdict here is the CHECKS verdict, before FraudLabs screening:
        fraud_check is 'pending' and no fraud code is set. Such a row is never
        published. Merge-LaneFraudOutcome applies the FraudLabs result (or
        FRAUD_CHECK_NOT_RUN), and Get-LaneVerdictReport refuses a 'pending'
        row and any ACCEPT whose fraud_check is not 'pass'.
    #>
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)]$Application,
        [Parameter()]$Context
    )
    $codes = [System.Collections.Generic.List[string]]::new()
    $productPid = [int]$Application.pid
    $gate = Get-LaneGate -ProductPid $productPid
    $fields = @($Application.fields | Where-Object { $_ })
    # Assigned bare, not wrapped in @(): Get-LaneProp returns an array as ONE
    # pipeline item, which @() would nest one level deeper.
    $onboarding = Get-LaneProp -Object $Context -Name 'OnboardingServices'
    if ($null -eq $onboarding) { $onboarding = @() }
    $website = Get-LaneProp -Object $Context -Name 'WebsiteServices'
    if ($null -eq $website) { $website = @() }
    $verification = Get-LaneProp -Object $Application -Name 'verification'
    $probes = Get-LaneProp -Object $Application -Name 'urlProbes'
    if ($null -eq $probes) { $probes = @{} }

    $org = Get-LaneOrgName -CompanyName ([string](Get-LaneProp -Object $Application -Name 'companyName')) `
        -ApplicantFullName ([string](Get-LaneProp -Object $Application -Name 'applicantFullName')) -Fields $fields

    $laneItems = Get-LaneProp -Object $Application -Name 'orderLaneItemCount'
    if ($null -ne $laneItems -and [int]$laneItems -gt 1) { $codes.Add('ORDER_HAS_MULTIPLE_LANE_ITEMS') }

    $domain = ''
    $readiness = $null

    if ($gate -eq 0) {
        $codes.Add('UNSUPPORTED_PID')
    }
    elseif ($gate -eq 1) {
        if ($fields.Count -eq 0) { $codes.Add('NO_APPLICATION_FIELDS') }

        if ($org.Status -eq 'missing') { $codes.Add('ORG_NAME_MISSING') }
        elseif ($org.Status -eq 'applicant') { $codes.Add('ORG_NAME_IS_APPLICANT') }
        elseif ($org.Status -eq 'withheld') { $codes.Add('ORG_NAME_WITHHELD') }

        $domainRaw = Get-FieldValue -Fields $fields -Pattern $script:LaneFieldPatterns.DesiredDomain
        if (Test-PlaceholderValue -Value $domainRaw) { $domainRaw = '' }
        if ([string]::IsNullOrWhiteSpace($domainRaw)) { $domainRaw = [string](Get-LaneProp -Object $Application -Name 'serviceDomain') }
        $domain = ConvertTo-LaneDomain -Value $domainRaw
        if ([string]::IsNullOrWhiteSpace($domainRaw)) { $codes.Add('DESIRED_DOMAIN_MISSING') }
        elseif ([string]::IsNullOrWhiteSpace($domain)) { $codes.Add('DESIRED_DOMAIN_INVALID') }

        foreach ($kind in @('facebook', 'linkedin')) {
            $page = Get-LaneSocialPageUrl -Fields $fields -Kind $kind
            if ($page.Code) { $codes.Add($page.Code); continue }
            $status = if ($probes.ContainsKey($page.Url)) { $probes[$page.Url] } else { $null }
            if (-not (Test-LaneProbeAnswered -Status $status)) { $codes.Add("$($kind.ToUpperInvariant())_URL_UNVERIFIED") }
        }

        if (-not (Test-TruthyTick -Value (Get-FieldValue -Fields $fields -Pattern $script:FieldPatterns.Tos))) { $codes.Add('TOS_NOT_ATTESTED') }
        if (-not (Test-TruthyTick -Value (Get-FieldValue -Fields $fields -Pattern $script:LaneFieldPatterns.UsAttest))) { $codes.Add('US_NOT_ATTESTED') }

        $einIs501c3 = Get-LaneProp -Object $verification -Name 'einIs501c3'
        $legalRaw = Get-FieldValue -Fields $fields -Pattern $script:FieldPatterns.LegalStatus
        $legal = Get-NormalizedLegalStatus -Value $legalRaw
        if ([string]::IsNullOrWhiteSpace($legalRaw)) { $codes.Add('LEGAL_STATUS_MISSING') }
        elseif ($legal -eq 'unknown') { $codes.Add('LEGAL_STATUS_UNRECOGNIZED') }
        if ((Get-CategoryMismatch -ProductPid $productPid -LegalStatus $legal -EinIs501c3 $einIs501c3).IsMismatch) { $codes.Add('TRACK_MISMATCH') }

        $einInfo = Get-ApplicationEin -Fields $fields -ProductPid $productPid
        if (-not (Test-ValidEin -Value $einInfo.Raw)) {
            $codes.Add('EIN_MISSING')
        }
        else {
            $einChecked = [bool](Get-LaneProp -Object $verification -Name 'einChecked')
            $einVerified = [bool](Get-LaneProp -Object $verification -Name 'einVerified')
            if ($productPid -eq 33) {
                if (-not $einChecked -or -not $einVerified -or $null -eq $einIs501c3) { $codes.Add('EIN_NOT_VERIFIED') }
                elseif ($einIs501c3 -ne $true) { $codes.Add('EIN_NOT_501C3') }
            }
            if ([bool](Get-LaneProp -Object $verification -Name 'nameMismatch')) { $codes.Add('EIN_NAME_MISMATCH') }
        }
        if ([bool](Get-LaneProp -Object $verification -Name 'guidestarEinMismatch')) { $codes.Add('CANDID_EIN_MISMATCH') }

        foreach ($c in (Get-LaneDuplicateCodes -Application $Application -GateServices $onboarding `
                    -DomainServices (@($onboarding) + @($website)) -OrgRaw $org.Raw -Domain $domain)) { $codes.Add($c) }

        # Readiness: 226's mission-weighted score, unchanged (Get-ApplicationScore).
        $scoreInput = [pscustomobject]@{
            orderid     = [string]$Application.orderid
            ordernum    = ''
            clientid    = [string]$Application.clientid
            pid         = $productPid
            productName = ''
            charityName = $org.Raw
            fields      = $fields
        }
        if ($verification) {
            foreach ($k in @('einChecked', 'einVerified', 'einIs501c3', 'einSource', 'subsectionCode', 'rulingDate', 'guidestarChecked', 'guidestarLive', 'nameMismatch', 'guidestarEinMismatch')) {
                $scoreInput | Add-Member -NotePropertyName $k -NotePropertyValue (Get-LaneProp -Object $verification -Name $k) -Force
            }
        }
        $readiness = [int](Get-ApplicationScore -Application $scoreInput).ffcReadinessScore
    }
    else {
        $attestPresent = @($fields | Where-Object { [string]$_.name -match $script:LaneFieldPatterns.Gate2Attest }).Count -gt 0
        $refPresent = @($fields | Where-Object { [string]$_.name -match $script:LaneFieldPatterns.Gate2Reference }).Count -gt 0
        $reference = Get-FieldValue -Fields $fields -Pattern $script:LaneFieldPatterns.Gate2Reference
        if (-not $attestPresent -and -not $refPresent) {
            $codes.Add('GATE2_FIELDS_ABSENT')
        }
        elseif (-not (Test-TruthyTick -Value (Get-FieldValue -Fields $fields -Pattern $script:LaneFieldPatterns.Gate2Attest))) {
            $codes.Add('GATE2_ATTESTATION_MISSING')
        }
        $resolved = Resolve-LaneGate1Service -ClientId ([string]$Application.clientid) -Reference $reference -OnboardingServices $onboarding
        if ($resolved.Code) { $codes.Add($resolved.Code) }
        if ($resolved.Service -and $resolved.Service.desiredDomain) { $domain = [string]$resolved.Service.desiredDomain }
        if ([string]::IsNullOrWhiteSpace($domain)) { $domain = ConvertTo-LaneDomain -Value ([string](Get-LaneProp -Object $Application -Name 'serviceDomain')) }
        foreach ($c in (Get-LaneDuplicateCodes -Application $Application -GateServices $website -DomainServices @() -OrgRaw '' -Domain '')) { $codes.Add($c) }
    }

    $sorted = Get-SortedLaneCodes -Codes $codes.ToArray()
    $toInt = { param($v) $n = 0; if ([int]::TryParse([string]$v, [ref]$n)) { $n } else { [string]$v } }
    return [pscustomobject][ordered]@{
        order_id        = (& $toInt $Application.orderid)
        client_id       = (& $toInt $Application.clientid)
        pid             = $productPid
        gate            = $gate
        org_name        = [string]$org.Name
        desired_domain  = [string]$domain
        verdict         = (Get-LaneVerdictFromCodes -Codes $sorted)
        reason_codes    = @($sorted)
        readiness_score = $readiness
        fraud_check     = 'pending'
        fraud_score     = $null
    }
}

function Get-LaneFraudOutcome {
    <#
    .SYNOPSIS
        Map ONE FraudLabs Pro lookup result to a fraud outcome. Pure.
    .DESCRIPTION
        $Result is one row of the screening job's results
        (scripts/whmcs-charity-lane-fraud-screen.ps1):
          order_id, lookup_ok, fraudlabs_status, fraudlabs_score, error

        Fail closed: only a successful lookup whose status is APPROVE and whose
        score parses below the flag threshold is a pass. In order:
          -NotRun                         -> not_run  FRAUD_CHECK_NOT_RUN
          no result / lookup_ok not true  -> failed   FRAUD_CHECK_FAILED
          REJECT                          -> flagged  FRAUD_FLAGGED
          score >= FlagScoreAt            -> flagged  FRAUD_FLAGGED
          REVIEW                          -> review   FRAUD_REVIEW
          APPROVE with a score            -> pass     (no code)
          anything else                   -> failed   FRAUD_CHECK_FAILED
        Returns @{ Check; Code; Score } (Code $null on pass, Score $null when
        the score did not parse).
    #>
    [OutputType([pscustomobject])]
    [CmdletBinding(DefaultParameterSetName = 'Result')]
    param(
        [Parameter(ParameterSetName = 'Result')]$Result,
        [Parameter(ParameterSetName = 'NotRun', Mandatory = $true)][switch]$NotRun,
        [Parameter(ParameterSetName = 'Result')][int]$FlagScoreAt = $script:LaneFraudScoreFlagAt
    )
    if ($NotRun) { return [pscustomobject]@{ Check = 'not_run'; Code = 'FRAUD_CHECK_NOT_RUN'; Score = $null } }
    $failed = [pscustomobject]@{ Check = 'failed'; Code = 'FRAUD_CHECK_FAILED'; Score = $null }
    if ($null -eq $Result) { return $failed }
    # Boolean true, or the string 'true' a serialization round trip may leave.
    # A missing or odd lookup_ok is a failed lookup.
    $ok = Get-LaneProp -Object $Result -Name 'lookup_ok'
    if (-not (($ok -is [bool] -and $ok) -or ([string]$ok -eq 'true'))) { return $failed }

    $status = ([string](Get-LaneProp -Object $Result -Name 'fraudlabs_status')).Trim().ToUpperInvariant()
    $score = $null
    $parsed = 0.0
    $rawScore = ([string](Get-LaneProp -Object $Result -Name 'fraudlabs_score')).Trim()
    if ([double]::TryParse($rawScore, [System.Globalization.NumberStyles]::Float, [System.Globalization.CultureInfo]::InvariantCulture, [ref]$parsed) -and
        $parsed -ge 0 -and $parsed -le 100) {
        $score = [int][math]::Round($parsed)
    }

    if ($status -eq 'REJECT') { return [pscustomobject]@{ Check = 'flagged'; Code = 'FRAUD_FLAGGED'; Score = $score } }
    if ($null -ne $score -and $score -ge $FlagScoreAt) { return [pscustomobject]@{ Check = 'flagged'; Code = 'FRAUD_FLAGGED'; Score = $score } }
    if ($status -eq 'REVIEW') { return [pscustomobject]@{ Check = 'review'; Code = 'FRAUD_REVIEW'; Score = $score } }
    if ($status -eq 'APPROVE' -and $null -ne $score) { return [pscustomobject]@{ Check = 'pass'; Code = $null; Score = $score } }
    return [pscustomobject]@{ Check = 'failed'; Code = 'FRAUD_CHECK_FAILED'; Score = $score }
}

function Merge-LaneFraudOutcome {
    <#
    .SYNOPSIS
        Apply a fraud outcome to ONE report row and re-derive its verdict. Pure.
    .DESCRIPTION
        Any fraud code the row already carries is dropped, the outcome's code is
        added, and the verdict is recomputed from the codes by the same
        Get-LaneVerdictFromCodes every other check uses. So the outcome can only
        ever make a verdict stricter than the checks verdict, never looser, and
        applying an outcome twice gives the same row. The returned row carries
        exactly the report-row fields; anything else on the input is dropped.
    #>
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)]$Row,
        [Parameter(Mandatory = $true)]$Outcome
    )
    # Assigned bare, then iterated: Get-LaneProp returns an array as ONE
    # pipeline item, so @(Get-LaneProp ...) would nest it and join the codes.
    $codes = Get-LaneProp -Object $Row -Name 'reason_codes'
    $kept = @(foreach ($c in @($codes)) { if ($c -and $script:LaneFraudCodes -notcontains [string]$c) { [string]$c } })
    if ($Outcome.Code) { $kept += [string]$Outcome.Code }
    $sorted = Get-SortedLaneCodes -Codes $kept
    return [pscustomobject][ordered]@{
        order_id        = (Get-LaneProp -Object $Row -Name 'order_id')
        client_id       = (Get-LaneProp -Object $Row -Name 'client_id')
        pid             = (Get-LaneProp -Object $Row -Name 'pid')
        gate            = (Get-LaneProp -Object $Row -Name 'gate')
        org_name        = [string](Get-LaneProp -Object $Row -Name 'org_name')
        desired_domain  = [string](Get-LaneProp -Object $Row -Name 'desired_domain')
        verdict         = (Get-LaneVerdictFromCodes -Codes $sorted)
        reason_codes    = @($sorted)
        readiness_score = (Get-LaneProp -Object $Row -Name 'readiness_score')
        fraud_check     = [string]$Outcome.Check
        fraud_score     = $Outcome.Score
    }
}

function Merge-LaneFraudResultSet {
    <#
    .SYNOPSIS
        Apply the screening job's results to every row of an unscreened report. Pure.
    .DESCRIPTION
        $Rows: the unscreened report's verdict rows. $Results: the screening
        job's result rows, or $null when there are none (the job failed, was
        skipped, or its artifact is missing): then EVERY row is
        FRAUD_CHECK_FAILED. A row with no result, or with more than one result
        for its order id, is FRAUD_CHECK_FAILED too. Returns the screened rows.
    #>
    [OutputType([pscustomobject[]])]
    param(
        [Parameter()]$Rows,
        [Parameter()]$Results,
        [Parameter()][int]$FlagScoreAt = $script:LaneFraudScoreFlagAt
    )
    $byOrder = @{}
    $dupes = [System.Collections.Generic.HashSet[string]]::new()
    if ($null -ne $Results) {
        foreach ($r in @($Results | Where-Object { $_ })) {
            $id = [string](Get-LaneProp -Object $r -Name 'order_id')
            if ([string]::IsNullOrWhiteSpace($id)) { continue }
            if ($byOrder.ContainsKey($id)) { [void]$dupes.Add($id) }
            $byOrder[$id] = $r
        }
    }
    $out = foreach ($row in @($Rows | Where-Object { $_ })) {
        $id = [string](Get-LaneProp -Object $row -Name 'order_id')
        $result = $null
        if ($byOrder.ContainsKey($id) -and -not $dupes.Contains($id)) { $result = $byOrder[$id] }
        Merge-LaneFraudOutcome -Row $row -Outcome (Get-LaneFraudOutcome -Result $result -FlagScoreAt $FlagScoreAt)
    }
    return @($out)
}

function ConvertTo-LaneFraudLookupJson {
    <#
    .SYNOPSIS
        The FraudLabs lookup-key file for the screening job, as JSON. Pure.
    .DESCRIPTION
        One entry per pending lane ORDER (an order with two lane items is looked
        up once): [{order_id, lookup_id}], where lookup_id is the WHMCS field 228
        sends to FraudLabs (-OrderIdField: 'ordernum', its default, or 'id'). A
        missing order number stays '' and the screening job records a failed
        lookup (FRAUD_CHECK_FAILED). No applicant data is ever in it.
    #>
    [OutputType([string])]
    param(
        [Parameter()][string[]]$OrderIds,
        [Parameter()][hashtable]$OrderNumbers = @{},
        [Parameter()][ValidateSet('ordernum', 'id')][string]$OrderIdField = 'ordernum'
    )
    $entries = @(@($OrderIds | Where-Object { $_ } | Select-Object -Unique) | ForEach-Object {
            $lookupId = if ($OrderIdField -eq 'id') { $_ } else { [string]$OrderNumbers[$_] }
            [pscustomobject][ordered]@{ order_id = [string]$_; lookup_id = $lookupId }
        })
    # -InputObject keeps an array an array (even empty or of one); -AsArray here
    # would wrap it a second time.
    return (ConvertTo-Json -InputObject @($entries) -Compress)
}

function Get-LaneVerdictReport {
    # Assemble the JSON document (rows + counts + the code catalog) from rows.
    # -FraudCheck says whether the rows are the unscreened set ('not_run') or
    # have had the FraudLabs results applied ('screened').
    # Fail closed at the single point every report is written through: a row
    # that never went through Merge-LaneFraudOutcome ('pending'), or an ACCEPT
    # whose fraud_check is not 'pass', is a programming error and throws.
    [OutputType([pscustomobject])]
    param(
        [Parameter()]$Rows,
        [Parameter()][string]$RunUrl = '',
        [Parameter()][string]$GeneratedAt = '',
        [Parameter()][ValidateSet('not_run', 'screened')][string]$FraudCheck = 'not_run'
    )
    $all = @($Rows | Where-Object { $_ })
    foreach ($r in $all) {
        $fc = [string]$r.fraud_check
        if (@('pass', 'flagged', 'review', 'failed', 'not_run') -notcontains $fc) {
            throw "Row for order $($r.order_id) has fraud_check '$fc'; apply Merge-LaneFraudOutcome before reporting."
        }
        if ([string]$r.verdict -eq 'ACCEPT' -and $fc -ne 'pass') {
            throw "Row for order $($r.order_id) is ACCEPT with fraud_check '$fc'; only a FraudLabs pass can ACCEPT."
        }
    }
    $order = @{ ESCALATE = 0; NEED_INFO = 1; ACCEPT = 2 }
    $sorted = @($all | Sort-Object -Property @{ Expression = { $order[[string]$_.verdict] } }, @{ Expression = { [int]$_.gate } }, @{ Expression = { if ($null -eq $_.readiness_score) { 0 } else { - [int]$_.readiness_score } } }, @{ Expression = { [string]$_.order_id } })
    $counts = [ordered]@{
        ACCEPT    = @($sorted | Where-Object { $_.verdict -eq 'ACCEPT' }).Count
        NEED_INFO = @($sorted | Where-Object { $_.verdict -eq 'NEED_INFO' }).Count
        ESCALATE  = @($sorted | Where-Object { $_.verdict -eq 'ESCALATE' }).Count
    }
    $fraudCounts = [ordered]@{}
    foreach ($k in @('pass', 'flagged', 'review', 'failed', 'not_run')) {
        $fraudCounts[$k] = @($sorted | Where-Object { [string]$_.fraud_check -eq $k }).Count
    }
    $catalog = [ordered]@{}
    foreach ($k in $script:LaneReasonCodes.Keys) {
        $catalog[$k] = [ordered]@{ severity = $script:LaneReasonCodes[$k].severity; description = $script:LaneReasonCodes[$k].description }
    }
    $note = if ($FraudCheck -eq 'screened') {
        "FraudLabs Pro results applied by workflow 232's fraud_screen job (fraudlabspro-prod-read), looked up as 228 does. Only fraud_check 'pass' can ACCEPT; a failed or missing lookup is FRAUD_CHECK_FAILED (ESCALATE)."
    }
    else {
        'UNSCREENED: FraudLabs Pro has not been applied, so every row carries FRAUD_CHECK_NOT_RUN (ESCALATE). This is the intermediate report, not a decision input.'
    }
    return [pscustomobject][ordered]@{
        schema              = 'charity-lane-verdicts/v2'
        generated_at        = $GeneratedAt
        run_url             = $RunUrl
        read_only           = $true
        fraud_check         = $FraudCheck
        fraud_check_note    = $note
        fraud_score_flag_at = $script:LaneFraudScoreFlagAt
        counts              = $counts
        fraud_counts        = $fraudCounts
        verdicts            = @($sorted)
        reason_code_catalog = $catalog
    }
}

function Format-LaneVerdictMarkdown {
    # The job-summary / issue-comment table. Only report-row fields appear.
    [OutputType([string])]
    param(
        [Parameter(Mandatory = $true)]$Report
    )
    $lines = [System.Collections.Generic.List[string]]::new()
    $lines.Add('## Charity-lane verdicts (Gate 1/2, read-only)')
    $lines.Add('')
    if ($Report.run_url) { $lines.Add("Run: $($Report.run_url)"); $lines.Add('') }
    $c = $Report.counts
    $f = $Report.fraud_counts
    $fraudLine = if ([string]$Report.fraud_check -eq 'screened') {
        "FraudLabs: pass $($f.pass) | flagged $($f.flagged) | review $($f.review) | failed $($f.failed)"
    }
    else {
        'FraudLabs: `not_run` (UNSCREENED, every row ESCALATE)'
    }
    $lines.Add("**ACCEPT** $($c.ACCEPT) | **NEED_INFO** $($c.NEED_INFO) | **ESCALATE** $($c.ESCALATE) | $fraudLine")
    $lines.Add('')
    $rows = @($Report.verdicts)
    if ($rows.Count -eq 0) {
        $lines.Add('No Pending pid 16 / 33 / 40 orders.')
    }
    else {
        $lines.Add('| Order | Client | pid | Gate | Org | Desired domain | Verdict | Readiness | Fraud | Reason codes |')
        $lines.Add('| ----- | ------ | --- | ---- | --- | -------------- | ------- | --------- | ----- | ------------ |')
        foreach ($r in $rows) {
            $orgCell = if ([string]::IsNullOrWhiteSpace([string]$r.org_name)) { '-' } else { [string]$r.org_name }
            $domCell = if ([string]::IsNullOrWhiteSpace([string]$r.desired_domain)) { '-' } else { '`' + [string]$r.desired_domain + '`' }
            $readCell = if ($null -eq $r.readiness_score) { '-' } else { [string]$r.readiness_score }
            $fraudCell = [string]$r.fraud_check
            if ($null -ne $r.fraud_score) { $fraudCell = "$fraudCell ($($r.fraud_score))" }
            $codeCell = (@($r.reason_codes) | ForEach-Object { '`' + $_ + '`' }) -join ' '
            $lines.Add("| $($r.order_id) | $($r.client_id) | $($r.pid) | $($r.gate) | $orgCell | $domCell | **$($r.verdict)** | $readCell | $fraudCell | $codeCell |")
        }
    }
    $lines.Add('')
    $lines.Add('Reason codes and severities: `reason_code_catalog` in the `charity-lane-verdicts.json` artifact. INFO codes never block; only fraud `pass` can ACCEPT.')
    return ($lines -join "`n")
}

# When dot-sourced (unit tests), stop here: only the functions are needed.
if ($MyInvocation.InvocationName -eq '.') { return }

# ==========================================================================
# Runner (live, READ-ONLY) -- WHMCS reads + public GETs. No WHMCS write action
# is called anywhere below.
# ==========================================================================

function Get-LaneAllPages {
    # Every item of a WHMCS list action, paged through Invoke-WhmcsPagedFetch so
    # one record WHMCS cannot JSON-encode costs that record, not the page.
    param(
        [Parameter(Mandatory = $true)][string]$Api,
        [Parameter(Mandatory = $true)][hashtable]$Body,
        [Parameter(Mandatory = $true)][string]$ListProperty,
        [Parameter(Mandatory = $true)][string]$ItemProperty,
        [int]$Size = 250
    )
    $items = [System.Collections.Generic.List[object]]::new()
    $start = 0
    while ($true) {
        $page = Invoke-WhmcsPagedFetch -ApiUrl $Api -Body $Body -Start $start -Num $Size -ListProperty $ListProperty -ItemProperty $ItemProperty
        foreach ($i in @($page.Items)) { $items.Add($i) }
        $got = @($page.Items).Count + @($page.SkippedIndexes).Count
        if ($got -le 0) { break }
        $start += $got
        if ($page.Total -gt 0 -and $start -ge $page.Total) { break }
    }
    return $items.ToArray()
}

function Invoke-LaneUrlProbe {
    # One GET, no redirect followed, descriptive User-Agent. Returns the HTTP
    # status code, or $null on any transport error. Only URLs that already
    # passed the facebook.com / linkedin.com host checks reach this.
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][hashtable]$Cache,
        [int]$TimeoutSec = 10
    )
    if ($Cache.ContainsKey($Url)) { return $Cache[$Url] }
    Start-Sleep -Milliseconds 250
    $status = $null
    $handler = [System.Net.Http.HttpClientHandler]::new()
    $handler.AllowAutoRedirect = $false
    $client = [System.Net.Http.HttpClient]::new($handler)
    try {
        $client.Timeout = [TimeSpan]::FromSeconds($TimeoutSec)
        $req = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::Get, $Url)
        [void]$req.Headers.TryAddWithoutValidation('User-Agent', 'FFC-Charity-Lane-Verdicts/1.0 (+https://github.com/FreeForCharity/FFC-Cloudflare-Automation)')
        $resp = $client.SendAsync($req).GetAwaiter().GetResult()
        $status = [int]$resp.StatusCode
        $resp.Dispose()
    }
    catch {
        $status = $null
    }
    finally {
        $client.Dispose()
    }
    $Cache[$Url] = $status
    return $status
}

function ConvertTo-LaneServiceSummary {
    param([Parameter(Mandatory = $true)]$Service, [Parameter(Mandatory = $true)][int]$ProductPid)
    $fields = Get-CustomFieldNodes -Node $Service
    $desired = Get-FieldValue -Fields $fields -Pattern $script:LaneFieldPatterns.DesiredDomain
    $svcDomain = if ($Service.PSObject.Properties['domain']) { [string]$Service.domain } else { '' }
    return [pscustomobject]@{
        id            = [string]$Service.id
        orderid       = [string]$Service.orderid
        clientid      = [string]$Service.clientid
        pid           = $ProductPid
        status        = [string]$Service.status
        desiredDomain = (ConvertTo-LaneDomain -Value $desired)
        serviceDomain = (ConvertTo-LaneDomain -Value $svcDomain)
        rawDomain     = $svcDomain
        fields        = $fields
    }
}

function Write-LaneSummary {
    param([string]$Text)
    if (-not [string]::IsNullOrWhiteSpace($env:GITHUB_STEP_SUMMARY)) {
        $Text | Out-File -FilePath $env:GITHUB_STEP_SUMMARY -Append -Encoding utf8
    }
    Write-Host $Text
}

try {
    $cfg = $script:LaneRunConfig
    $api = Resolve-WhmcsApiUrl -ApiUrlParam $cfg.ApiUrl
    $creds = Resolve-WhmcsCredentials -IdentifierParam $cfg.Identifier -SecretParam $cfg.Secret -CredentialsJsonParam $cfg.CredentialsJson
    $auth = New-WhmcsAuthBody -Creds $creds -AccessKey (Resolve-WhmcsAccessKey -AccessKeyParam $cfg.AccessKey)
    $skipNet = [bool]$cfg.SkipNetworkChecks

    # 1) Pending orders.
    $ordersBody = $auth.Clone(); $ordersBody.action = 'GetOrders'; $ordersBody.status = 'Pending'
    $pendingOrderIds = [System.Collections.Generic.HashSet[string]]::new()
    # order id -> WHMCS order number, for the FraudLabs lookup keys only (never
    # written to the report).
    $orderNumbers = @{}
    foreach ($o in (Get-LaneAllPages -Api $api -Body $ordersBody -ListProperty 'orders' -ItemProperty 'order' -Size $cfg.PageSize)) {
        if ($o -and -not [string]::IsNullOrWhiteSpace([string]$o.id)) {
            [void]$pendingOrderIds.Add([string]$o.id)
            if ($o.PSObject.Properties['ordernum']) { $orderNumbers[[string]$o.id] = [string]$o.ordernum }
        }
    }
    Write-Host "Pending orders (all products): $($pendingOrderIds.Count)"

    # 2) Every pid 16 / 33 / 40 service, any status (duplicates + Gate 2 need the history).
    $byPid = @{}
    foreach ($productPid in @(16, 33, 40)) {
        $body = $auth.Clone(); $body.action = 'GetClientsProducts'; $body.pid = $productPid
        $byPid[$productPid] = @(Get-LaneAllPages -Api $api -Body $body -ListProperty 'products' -ItemProperty 'product' -Size $cfg.PageSize |
                Where-Object { $_ } | ForEach-Object { ConvertTo-LaneServiceSummary -Service $_ -ProductPid $productPid })
        Write-Host "pid ${productPid}: $(@($byPid[$productPid]).Count) service(s)"
    }
    $context = [pscustomobject]@{
        OnboardingServices = @($byPid[16]) + @($byPid[33])
        WebsiteServices    = @($byPid[40])
    }

    $pendingLane = @((@($byPid[16]) + @($byPid[33]) + @($byPid[40])) | Where-Object { $pendingOrderIds.Contains([string]$_.orderid) })
    $laneItemsPerOrder = @{}
    foreach ($s in $pendingLane) { $laneItemsPerOrder[[string]$s.orderid] = 1 + [int]$laneItemsPerOrder[[string]$s.orderid] }
    Write-Host "Pending charity-lane items: $($pendingLane.Count)"

    $clientCache = @{}; $einCache = @{}; $urlCache = @{}; $probeCache = @{}; $einFieldIdCache = @{}
    $orgLookupCache = @{}; $domainLookupCache = @{}
    $rows = foreach ($svc in $pendingLane) {
        $clientId = [string]$svc.clientid
        $details = Get-WhmcsClientDetails -Api $api -Auth $auth -ClientId $clientId -Cache $clientCache
        $names = Get-ClientNameParts -Details $details
        $app = [pscustomobject]@{
            orderid              = [string]$svc.orderid
            clientid             = $clientId
            pid                  = [int]$svc.pid
            fields               = $svc.fields
            companyName          = $names.CompanyName
            applicantFullName    = $names.FullName
            serviceDomain        = $svc.rawDomain
            orderLaneItemCount   = [int]$laneItemsPerOrder[[string]$svc.orderid]
            verification         = $null
            urlProbes            = @{}
            otherClientsSameOrg  = $null
            domainOwnerClientIds = $null
        }

        if ((Get-LaneGate -ProductPid $app.pid) -eq 1) {
            $org = Get-LaneOrgName -CompanyName $app.companyName -ApplicantFullName $app.applicantFullName -Fields $app.fields

            if (-not $skipNet) {
                # EIN (ProPublica) + Candid liveness, exactly as 226 does it.
                $verifyInput = [pscustomobject]@{ pid = $app.pid; fields = $app.fields; charityName = $org.Raw }
                $app.verification = Get-ApplicationVerification -Application $verifyInput -EinBaseUrl $cfg.EinApiBaseUrl `
                    -EinCache $einCache -UrlCache $urlCache -FieldIdCache $einFieldIdCache
                foreach ($kind in @('facebook', 'linkedin')) {
                    $page = Get-LaneSocialPageUrl -Fields $app.fields -Kind $kind
                    if (-not $page.Code -and $page.Url) { $app.urlProbes[$page.Url] = Invoke-LaneUrlProbe -Url $page.Url -Cache $probeCache }
                }
            }

            # Other clients with the same org name (GetClients search; prefix match, compared exactly after normalization).
            if (-not [string]::IsNullOrWhiteSpace($org.Raw)) {
                $key = ConvertTo-LaneOrgKey -Name $org.Raw
                if (-not $orgLookupCache.ContainsKey($org.Raw)) {
                    $orgLookupCache[$org.Raw] = $null
                    try {
                        $b = $auth.Clone(); $b.action = 'GetClients'; $b.search = $org.Raw; $b.limitnum = 250
                        $resp = Invoke-WhmcsApi -ApiUrl $api -Body $b
                        $orgLookupCache[$org.Raw] = @(Get-WhmcsNodeList -Node $resp.clients -ChildName 'client')
                    }
                    catch { Write-Warning "Org-name duplicate lookup failed for order $($app.orderid)." }
                }
                $clients = $orgLookupCache[$org.Raw]
                if ($null -ne $clients) {
                    $app.otherClientsSameOrg = @($clients | Where-Object {
                            $_ -and [string]$_.id -ne $clientId -and $_.PSObject.Properties['companyname'] -and
                            (ConvertTo-LaneOrgKey -Name ([string]$_.companyname)) -eq $key -and $key
                        }).Count
                }
            }

            # Other clients holding the desired domain (GetClientsDomains).
            $domain = ConvertTo-LaneDomain -Value (Get-FieldValue -Fields $app.fields -Pattern $script:LaneFieldPatterns.DesiredDomain)
            if (-not $domain) { $domain = ConvertTo-LaneDomain -Value $app.serviceDomain }
            if ($domain) {
                if (-not $domainLookupCache.ContainsKey($domain)) {
                    $domainLookupCache[$domain] = $null
                    try {
                        $b = $auth.Clone(); $b.action = 'GetClientsDomains'; $b.domain = $domain; $b.limitnum = 250
                        $resp = Invoke-WhmcsApi -ApiUrl $api -Body $b
                        $domainLookupCache[$domain] = @(Get-WhmcsNodeList -Node $resp.domains -ChildName 'domain' |
                                Where-Object { $_ -and $_.PSObject.Properties['userid'] } | ForEach-Object { [string]$_.userid })
                    }
                    catch { Write-Warning "Domain duplicate lookup failed for order $($app.orderid)." }
                }
                $app.domainOwnerClientIds = $domainLookupCache[$domain]
            }
        }

        # UNSCREENED: FraudLabs is not reachable from this environment, so every
        # row leaves here as FRAUD_CHECK_NOT_RUN (ESCALATE). The finalize job
        # applies the screening job's results.
        Merge-LaneFraudOutcome -Row (Get-CharityLaneVerdict -Application $app -Context $context) -Outcome (Get-LaneFraudOutcome -NotRun)
    }

    $lookupJson = ConvertTo-LaneFraudLookupJson -OrderIds @($pendingLane | ForEach-Object { [string]$_.orderid }) `
        -OrderNumbers $orderNumbers -OrderIdField $cfg.OrderIdField

    $runUrl = ''
    if ($env:GITHUB_RUN_ID) { $runUrl = "$($env:GITHUB_SERVER_URL)/$($env:GITHUB_REPOSITORY)/actions/runs/$($env:GITHUB_RUN_ID)" }
    $report = Get-LaneVerdictReport -Rows @($rows) -RunUrl $runUrl -GeneratedAt ((Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')) -FraudCheck 'not_run'
    $markdown = Format-LaneVerdictMarkdown -Report $report

    foreach ($path in @($cfg.OutputFile, $cfg.MarkdownFile, $cfg.FraudLookupFile)) {
        $dir = Split-Path -Parent $path
        if (-not [string]::IsNullOrWhiteSpace($dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    }
    $report | ConvertTo-Json -Depth 8 | Out-File -FilePath $cfg.OutputFile -Encoding utf8
    $markdown | Out-File -FilePath $cfg.MarkdownFile -Encoding utf8
    $lookupJson | Out-File -FilePath $cfg.FraudLookupFile -Encoding utf8
    # The step summary gets a pointer, not the unscreened table: every row in it
    # is ESCALATE, and the decision table is the finalize job's.
    Write-LaneSummary -Text "Unscreened verdicts for $(@($report.verdicts).Count) order item(s); FraudLabs Pro screening and the final table follow in the ``finalize`` job."
    Write-Host $markdown
    Write-Host "Wrote $($cfg.OutputFile) ($(@($report.verdicts).Count) unscreened verdict(s)) and the FraudLabs lookup keys."
    exit 0
}
catch {
    # ::error:: annotation + explicit exit (not Write-Error, which under
    # $ErrorActionPreference = 'Stop' would throw past the exit code).
    Write-Output "::error::Charity-lane verdicts failed: $($_.Exception.Message)"
    exit 1
}
