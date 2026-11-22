<#
.SYNOPSIS
    Read-only EIN check of every charity application in WHMCS against the IRS
    lists and ProPublica, and against the EIN each charity's website shows.

.DESCRIPTION
    Clarke, 2026-11-21: verify EINs against IRS Publication 78 and the IRS
    automatic revocation list, with ProPublica as a second opinion; run the same
    check on applications whose site shows no EIN and on pending applicants; and
    compare each site's EIN with the application's so a typo can be told from a
    real problem.

    For every onboarding application (pid 16 pre-501(c)(3), pid 33 501(c)(3)),
    in any status, it reads the EIN the application carries (the same
    Get-ApplicationEin resolver 226 and 232 use), looks it up in the IRS lists
    (Import-IrsEoList / Get-IrsEinStatus) and on ProPublica, and, when
    -SiteEinFile names the EINs FFC's websites show (scripts/collect_site_eins.py),
    compares the two.

    READ-ONLY: WHMCS GetClientsProducts / GetClientsDetails only, plus public
    GETs to apps.irs.gov and ProPublica. It writes nothing anywhere but its own
    output files.

    PII: the hub repo is public, so the output never carries an EIN, a person's
    name or contact data. Per application it carries the order id, client id,
    pid, service status, the domain (desired or service domain), the org name
    WHMCS holds (withheld when it looks like a person), the IRS result, the IRS
    name the EIN belongs to (a public organization name), the ProPublica result,
    and how the application's EIN compares with the site's: same, differs (with
    the number of differing digits, so a one-digit typo shows), or no site EIN.
#>
[CmdletBinding()]
param(
    [Parameter()][string]$ApiUrl,
    [Parameter()][string]$Identifier,
    [Parameter()][string]$Secret,
    [Parameter()][string]$CredentialsJson,
    [Parameter()][string]$AccessKey,
    # JSON from scripts/collect_site_eins.py: [{repo, domain, eins: [...]}].
    [Parameter()][string]$SiteEinFile = '',
    [Parameter()][string]$OutputFile = 'artifacts/whmcs/application-ein-check.json',
    [Parameter()][string]$MarkdownFile = 'artifacts/whmcs/application-ein-check.md',
    [Parameter()][ValidateRange(1, 250)][int]$PageSize = 250
)

$ErrorActionPreference = 'Stop'

# Capture this script's parameters BEFORE dot-sourcing: a dot-sourced script
# binds its own param() block in this scope (see whmcs-charity-lane-verdicts.ps1).
$script:EinCheckConfig = @{
    ApiUrl          = $ApiUrl
    Identifier      = $Identifier
    Secret          = $Secret
    CredentialsJson = $CredentialsJson
    AccessKey       = $AccessKey
    SiteEinFile     = $SiteEinFile
    OutputFile      = $OutputFile
    MarkdownFile    = $MarkdownFile
    PageSize        = $PageSize
}

# Field readers, EIN resolver, IRS lists, ProPublica and the lane helpers
# (org-name withholding, domain parsing). Both runners are guarded, so this
# defines functions only.
. (Join-Path $PSScriptRoot 'whmcs-charity-lane-verdicts.ps1')

function Get-EinDigitDifference {
    # Number of positions at which two 9-digit EINs differ; -1 when either is
    # not a 9-digit EIN. A 1 is almost always a typo; a transposition is 2.
    [OutputType([int])]
    param([string]$A, [string]$B)
    $a1 = ([string]$A -replace '\D', ''); $b1 = ([string]$B -replace '\D', '')
    if ($a1.Length -ne 9 -or $b1.Length -ne 9) { return -1 }
    $n = 0
    for ($i = 0; $i -lt 9; $i++) { if ($a1[$i] -ne $b1[$i]) { $n++ } }
    return $n
}

function Get-EinCheckIrsLabel {
    # One word for the IRS result. Pure.
    [OutputType([string])]
    param($Status)
    if (-not $Status -or -not $Status.Checked) { return 'not checked' }
    if ($Status.Revoked) { return 'revoked' }
    if ($Status.InPub78) { return 'pub78' }
    return 'not listed'
}

function Get-EinCheckProPublicaLabel {
    [OutputType([string])]
    param($Result)
    if (-not $Result) { return 'not checked' }
    if ($Result.Verified) {
        if ($Result.Is501c3 -eq $true) { return '501c3' }
        if ($null -eq $Result.SubsectionCode) { return 'listed, no subsection' }
        return "subsection $($Result.SubsectionCode)"
    }
    if ($Result.PSObject.Properties['NotFound'] -and $Result.NotFound) { return 'not found' }
    return 'lookup failed'
}

function Get-EinCheckVerdict {
    # The one-line finding for a row. Pure. Order: what blocks first.
    [OutputType([string])]
    param(
        [bool]$HasEin,
        [string]$Irs,
        [string]$ProPublica,
        [string]$SiteCompare,
        [bool]$NameMismatch
    )
    if (-not $HasEin) { return 'no EIN on the application' }
    if ($Irs -eq 'revoked') { return 'revoked' }
    if ($SiteCompare -like 'differs*') { return 'site EIN differs from application' }
    if ($Irs -eq 'not listed' -and $ProPublica -eq 'not found') { return 'in no IRS list or ProPublica' }
    if ($Irs -eq 'not listed') { return 'not in Pub 78' }
    if ($Irs -eq 'not checked') { return 'IRS lists not checked' }
    if ($NameMismatch) { return 'IRS name differs from application' }
    return 'ok'
}

if ($MyInvocation.InvocationName -eq '.') { return }

try {
    $cfg = $script:EinCheckConfig
    $api = Resolve-WhmcsApiUrl -ApiUrlParam $cfg.ApiUrl
    $creds = Resolve-WhmcsCredentials -IdentifierParam $cfg.Identifier -SecretParam $cfg.Secret -CredentialsJsonParam $cfg.CredentialsJson
    $auth = New-WhmcsAuthBody -Creds $creds -AccessKey (Resolve-WhmcsAccessKey -AccessKeyParam $cfg.AccessKey)

    # Site EINs by domain (public: each site already shows them).
    $siteByDomain = @{}
    if ($cfg.SiteEinFile -and (Test-Path -LiteralPath $cfg.SiteEinFile)) {
        foreach ($s in @(Get-Content -LiteralPath $cfg.SiteEinFile -Raw -Encoding utf8 | ConvertFrom-Json)) {
            $d = ConvertTo-LaneDomain -Value ([string]$s.domain)
            if ($d) { $siteByDomain[$d] = [pscustomobject]@{ Repo = [string]$s.repo; Eins = @($s.eins | ForEach-Object { ([string]$_ -replace '\D', '') }) } }
        }
        Write-Host "Site EIN file: $($siteByDomain.Count) site(s)."
    }

    $irsLists = Import-IrsEoList
    if ($irsLists.Loaded) {
        Write-Host "IRS lists: Pub 78 $($irsLists.Pub78.Count) EINs (Last-Modified $($irsLists.Pub78Modified)); revocation list $($irsLists.Revocation.Count) EINs (Last-Modified $($irsLists.RevocationModified))."
    }
    else { Write-Warning "IRS lists unavailable: $($irsLists.Error)" }

    $services = [System.Collections.Generic.List[object]]::new()
    foreach ($productPid in @(16, 33)) {
        $body = $auth.Clone(); $body.action = 'GetClientsProducts'; $body.pid = $productPid
        $start = 0
        while ($true) {
            $page = Invoke-WhmcsPagedFetch -ApiUrl $api -Body $body -Start $start -Num $cfg.PageSize -ListProperty 'products' -ItemProperty 'product'
            foreach ($i in @($page.Items)) { if ($i) { $services.Add([pscustomobject]@{ Pid = $productPid; Service = $i }) } }
            $got = @($page.Items).Count + @($page.SkippedIndexes).Count
            if ($got -le 0) { break }
            $start += $got
            if ($page.Total -gt 0 -and $start -ge $page.Total) { break }
        }
    }
    Write-Host "Applications read: $($services.Count)"

    $clientCache = @{}; $einCache = @{}; $fieldIdCache = @{}
    $seenDomains = [System.Collections.Generic.HashSet[string]]::new()
    $rows = foreach ($entry in $services) {
        $svc = $entry.Service
        $fields = Get-CustomFieldNodes -Node $svc
        $desired = ConvertTo-LaneDomain -Value (Get-FieldValue -Fields $fields -Pattern $script:LaneFieldPatterns.DesiredDomain)
        $svcDomain = if ($svc.PSObject.Properties['domain']) { ConvertTo-LaneDomain -Value ([string]$svc.domain) } else { '' }
        $domain = if ($desired) { $desired } else { $svcDomain }
        if ($domain) { [void]$seenDomains.Add($domain) }
        if ($svcDomain) { [void]$seenDomains.Add($svcDomain) }

        $clientId = [string]$svc.clientid
        $details = Get-WhmcsClientDetails -Api $api -Auth $auth -ClientId $clientId -Cache $clientCache
        $names = Get-ClientNameParts -Details $details
        $org = Get-LaneOrgName -CompanyName $names.CompanyName -ApplicantFullName $names.FullName -Fields $fields

        $einInfo = Get-ApplicationEin -Fields $fields -ProductPid $entry.Pid -FieldIdCache $fieldIdCache
        $hasEin = Test-ValidEin -Value $einInfo.Raw
        $irs = $null; $pp = $null; $irsName = ''; $nameMismatch = $false
        if ($hasEin) {
            $irs = Get-IrsEinStatus -Ein $einInfo.Raw -Lists $irsLists
            $pp = Invoke-ProPublicaEin -Ein $einInfo.Raw -BaseUrl 'https://projects.propublica.org/nonprofits/api/v2/organizations' -Cache $einCache
            $irsName = if ($irs.Pub78Name) { $irs.Pub78Name } elseif ($pp.Verified -and $pp.Name) { $pp.Name } elseif ($irs.RevocationName) { $irs.RevocationName } else { '' }
            if ($irsName -and $org.Raw) { $nameMismatch = Test-NameMismatch -NameA $irsName -NameB $org.Raw }
        }

        # Site comparison: the site for either the desired or the service domain.
        $site = $null
        foreach ($d in @($domain, $svcDomain)) { if ($d -and $siteByDomain.ContainsKey($d)) { $site = $siteByDomain[$d]; break } }
        $siteCompare = if (-not $site) { 'no FFC site' }
        elseif (@($site.Eins).Count -eq 0) { 'site shows no EIN' }
        elseif (-not $hasEin) { 'application has no EIN' }
        else {
            $appDigits = ([string]$einInfo.Raw -replace '\D', '')
            if (@($site.Eins) -contains $appDigits) { 'same' }
            else {
                $diff = (@($site.Eins) | ForEach-Object { Get-EinDigitDifference -A $_ -B $appDigits } | Measure-Object -Minimum).Minimum
                "differs ($diff of 9 digits)"
            }
        }

        $irsLabel = Get-EinCheckIrsLabel -Status $irs
        $ppLabel = Get-EinCheckProPublicaLabel -Result $pp
        [pscustomobject][ordered]@{
            order_id       = [string]$svc.orderid
            client_id      = $clientId
            pid            = $entry.Pid
            status         = [string]$svc.status
            domain         = $domain
            site_repo      = if ($site) { $site.Repo } else { '' }
            org_name       = [string]$org.Name
            has_ein        = [bool]$hasEin
            irs            = $irsLabel
            irs_revoked_on = if ($irs -and $irs.Revoked) { [string]$irs.RevocationDate } else { '' }
            irs_name       = $irsName
            name_mismatch  = [bool]$nameMismatch
            propublica     = $ppLabel
            site_compare   = $siteCompare
            finding        = (Get-EinCheckVerdict -HasEin $hasEin -Irs $irsLabel -ProPublica $ppLabel -SiteCompare $siteCompare -NameMismatch $nameMismatch)
        }
    }
    $rows = @($rows)

    # Sites with no application on either domain.
    $sitesWithoutApplication = @($siteByDomain.Keys | Where-Object { -not $seenDomains.Contains($_) } | Sort-Object | ForEach-Object { $siteByDomain[$_].Repo })

    $report = [ordered]@{
        generated_at              = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        irs_lists_loaded          = [bool]$irsLists.Loaded
        pub78_last_modified       = [string]$irsLists.Pub78Modified
        revocation_last_modified  = [string]$irsLists.RevocationModified
        applications              = $rows
        sites_without_application = $sitesWithoutApplication
    }

    $order = @{ 'revoked' = 0; 'site EIN differs from application' = 1; 'in no IRS list or ProPublica' = 2; 'not in Pub 78' = 3; 'IRS name differs from application' = 4; 'IRS lists not checked' = 5; 'no EIN on the application' = 6; 'ok' = 7 }
    $md = [System.Collections.Generic.List[string]]::new()
    $md.Add('## Application EIN check (read-only)')
    $md.Add('')
    $md.Add("IRS lists loaded: $([bool]$irsLists.Loaded). Pub 78 Last-Modified: $($irsLists.Pub78Modified). Revocation list Last-Modified: $($irsLists.RevocationModified).")
    $md.Add("Applications: $($rows.Count). Findings other than ok: $(@($rows | Where-Object { $_.finding -ne 'ok' }).Count).")
    $md.Add('')
    $md.Add('| Order | Client | pid | Status | Domain | Site repo | Org (WHMCS) | IRS | IRS name | ProPublica | Site vs application | Finding |')
    $md.Add('| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |')
    foreach ($r in ($rows | Sort-Object { $order[$_.finding] }, domain)) {
        $irsCell = if ($r.irs_revoked_on) { "revoked $($r.irs_revoked_on)" } else { $r.irs }
        $md.Add("| $($r.order_id) | $($r.client_id) | $($r.pid) | $($r.status) | $($r.domain) | $($r.site_repo) | $($r.org_name) | $irsCell | $($r.irs_name) | $($r.propublica) | $($r.site_compare) | $($r.finding) |")
    }
    $md.Add('')
    $md.Add("### FFC sites with no application on their domain ($($sitesWithoutApplication.Count))")
    $md.Add('')
    $md.Add(($sitesWithoutApplication -join ', '))
    $markdown = $md -join "`n"

    foreach ($path in @($cfg.OutputFile, $cfg.MarkdownFile)) {
        $dir = Split-Path -Parent $path
        if (-not [string]::IsNullOrWhiteSpace($dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    }
    $report | ConvertTo-Json -Depth 6 | Out-File -FilePath $cfg.OutputFile -Encoding utf8
    $markdown | Out-File -FilePath $cfg.MarkdownFile -Encoding utf8
    if ($env:GITHUB_STEP_SUMMARY) { $markdown | Out-File -FilePath $env:GITHUB_STEP_SUMMARY -Append -Encoding utf8 }
    Write-Host $markdown
    exit 0
}
catch {
    Write-Output "::error::Application EIN check failed: $($_.Exception.Message)"
    exit 1
}
