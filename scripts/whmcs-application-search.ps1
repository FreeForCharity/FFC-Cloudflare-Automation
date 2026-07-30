<#
.SYNOPSIS
    Find the WHMCS onboarding application(s) matching a domain or organization
    name. Read-only.

.DESCRIPTION
    The onboarding application's answers (organization name, desired domain,
    mission) live as PRODUCT custom fields on the charity-onboarding service,
    and the org name is only embedded in the mission text - there is no
    client-level field to search. So neither a client search nor the masked
    triage tables can locate "the application for <domain>".

    This sweeps GetClientsProducts (all clients, paginated), scans each
    product's name + custom-field values for the query substring
    (case-insensitive), and returns the matching client id, order/service,
    and the readable application fields (mission, desired domain, legal
    status) with personal contact fields masked.

    No writes are performed.
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

    # Substring to search for in product custom fields (e.g. a domain or org
    # name). Case-insensitive; HTML in stored values is stripped before match.
    [Parameter(Mandatory = $true)]
    [string]$Query,

    [Parameter()]
    [ValidateRange(1, 250)]
    [int]$PageSize = 250,

    [Parameter()]
    [ValidateRange(1, 100)]
    [int]$MaxMatches = 25,

    [Parameter()]
    [string]$OutputFile = 'artifacts/whmcs/whmcs_application_search.json'
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'whmcs-api-common.ps1')

# Masking policy lives in whmcs-api-common.ps1 (Format-WhmcsFieldValue) and is
# documented in docs/pii-classification.md. It strips WHMCS's <a href> wrapper
# itself, so callers pass the raw value.
function Format-MaskedField {
    param([string]$FieldName, [string]$Value)
    return Format-WhmcsFieldValue -FieldName $FieldName -Value $Value
}

$creds = Resolve-WhmcsCredentials -IdentifierParam $Identifier -SecretParam $Secret -CredentialsJsonParam $CredentialsJson
$api = Resolve-WhmcsApiUrl -ApiUrlParam $ApiUrl
$key = Resolve-WhmcsAccessKey -AccessKeyParam $AccessKey
$needle = $Query.Trim().ToLowerInvariant()

function New-Body {
    param([Parameter(Mandatory = $true)][string]$Action)
    $b = @{ action = $Action; username = $creds.Identifier; password = $creds.Secret; responsetype = 'json' }
    if ($key) { $b.accesskey = $key }
    return $b
}

$hits = [System.Collections.Generic.List[object]]::new()
$unreadable = [System.Collections.Generic.List[int]]::new()
$scanned = 0
$start = 0
$total = 0
# A window with nothing readable in it also carries no `totalresults`, so the
# loop learns nothing about where the data ends and cannot bound itself - while
# each such window costs the bisect ~2*PageSize calls. Stop after this many
# consecutive blind windows rather than crawling the table one record at a time.
$maxBlindWindows = 3
$blindWindows = 0
$authBody = New-Body 'GetClientsProducts'
while ($true) {
    # Paged via Invoke-WhmcsPagedFetch so one record WHMCS cannot JSON-encode
    # costs us that record, not the whole page (and not the whole search).
    $page = Invoke-WhmcsPagedFetch -ApiUrl $api -Body $authBody -Start $start -Num $PageSize `
        -ListProperty 'products' -ItemProperty 'product'
    foreach ($idx in $page.SkippedIndexes) { $unreadable.Add($idx) }
    if ($page.Total -gt $total) { $total = $page.Total }
    $services = @($page.Items)
    if ($services.Count -le 0 -and $page.SkippedIndexes.Count -le 0) { break }

    if ($services.Count -le 0) {
        $blindWindows++
        if ($total -le 0 -and $blindWindows -ge $maxBlindWindows) {
            # Name the actual index range: whoever reads this has to go find the
            # offending rows in WHMCS, and $start alone is the current window's
            # first index, not the span that failed.
            $blindFrom = $start - (($blindWindows - 1) * $PageSize)
            $blindTo = $start + $PageSize - 1
            throw ("Aborting: $blindWindows consecutive windows covering indexes $blindFrom-$blindTo returned no " +
                'readable records and WHMCS never reported totalresults, so the sweep cannot tell where the data ' +
                'ends. Repair the malformed records in WHMCS before searching.')
        }
    }
    else { $blindWindows = 0 }

    foreach ($s in $services) {
        $scanned++
        $fields = Get-WhmcsNodeList -Node $s.customfields -ChildName 'customfield'
        $hit = $false
        if (("$($s.name)").ToLowerInvariant().Contains($needle)) { $hit = $true }
        foreach ($f in $fields) {
            # Remove-Html, NOT Format-MaskedField: matching happens against the
            # unmasked text. Masking here would compare the needle against
            # redacted output for personal-classified fields - '***', a masked
            # email like '***@example.org', or an initial like 'R***', depending
            # on the classification - so an application whose domain sits in one
            # would be unfindable while the search reported no matches.
            if ((Remove-Html ([string]$f.value)).ToLowerInvariant().Contains($needle)) { $hit = $true; break }
        }
        if (-not $hit) { continue }

        $cfOut = @()
        foreach ($f in $fields) {
            $fn = if ($f.PSObject.Properties['name'] -and $f.name) { [string]$f.name }
            elseif ($f.PSObject.Properties['fieldname'] -and $f.fieldname) { [string]$f.fieldname }
            else { "field-$($f.id)" }
            $cfOut += [ordered]@{ name = $fn; value = Format-MaskedField -FieldName $fn -Value ([string]$f.value) }
        }
        $hits.Add([ordered]@{
                clientId     = [string]$s.clientid
                serviceId    = [string]$s.id
                product      = [string]$s.name
                status       = [string]$s.status
                regDate      = [string]$s.regdate
                domain       = [string]$s.domain
                customFields = $cfOut
            })
        if ($hits.Count -ge $MaxMatches) { break }
    }
    if ($hits.Count -ge $MaxMatches) { break }
    # Advance by the window, not by the returned count: limitstart is an absolute
    # index, and a skipped record makes the two differ.
    $start += $PageSize
    if ($total -gt 0 -and $start -ge $total) { break }
}

# Read nothing but skipped something: a zero-match answer here would be
# indistinguishable from "not found", which is the failure this whole script
# exists to prevent. Fail instead of reporting an empty result.
if ($scanned -le 0 -and $unreadable.Count -gt 0) {
    throw ("Aborting: 0 records readable, $($unreadable.Count) unencodable. A 'no match' result would be " +
        'indistinguishable from a genuine absence. Repair the malformed records in WHMCS before searching.')
}

$result = [ordered]@{
    query             = $Query
    scanned           = $scanned
    matchCount        = $hits.Count
    truncated         = ($hits.Count -ge $MaxMatches)
    # Records WHMCS could not serialize; they were NOT searched, so a zero-match
    # result with a non-empty list here is "not found in what we could read".
    unreadableCount   = $unreadable.Count
    unreadableIndexes = @($unreadable)
    matches           = $hits
}

$json = $result | ConvertTo-Json -Depth 7
if (-not [string]::IsNullOrWhiteSpace($OutputFile)) {
    $dir = Split-Path -Parent $OutputFile
    if (-not [string]::IsNullOrWhiteSpace($dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $json | Out-File -FilePath $OutputFile -Encoding utf8
}
$json
