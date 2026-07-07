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

function Format-MaskedName {
    param([string]$Name)
    if ([string]::IsNullOrWhiteSpace($Name)) { return '' }
    $t = $Name.Trim()
    if ($t.Length -le 1) { return '***' }
    return $t.Substring(0, 1) + '***'
}
function Format-MaskedEmail {
    param([string]$Email)
    if ([string]::IsNullOrWhiteSpace($Email)) { return '' }
    $at = $Email.IndexOf('@')
    if ($at -lt 1) { return '***' }
    return '***' + $Email.Substring($at)
}
# Strip simple HTML (WHMCS wraps URL answers in <a href>...</a>) for matching + display.
function Remove-Html {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return '' }
    return ([regex]::Replace($Value, '<[^>]+>', '')).Trim()
}
function Format-MaskedField {
    param([string]$FieldName, [string]$Value)
    $v = Remove-Html $Value
    if ([string]::IsNullOrWhiteSpace($v)) { return '' }
    if ($FieldName -match '(?i)\b(first|last|your|contact|poc)[ _-]?name\b' -and
        $FieldName -notmatch '(?i)org|charity|company|nonprofit|foundation|business') {
        return Format-MaskedName $v
    }
    if ($FieldName -match '(?i)\bein\b|tax[ _-]?id') { return '***' }
    if ($FieldName -match '(?i)phone|email|e-mail') {
        if ($v -match '@') { return Format-MaskedEmail $v }
        return '***'
    }
    return $v
}

function Get-WhmcsList {
    param($Node, [Parameter(Mandatory = $true)][string]$ChildName)
    if ($null -eq $Node -or $Node -is [string]) { return @() }
    if ($Node -is [System.Array]) { return @($Node | Where-Object { $null -ne $_ }) }
    if ($Node.PSObject.Properties[$ChildName]) { return @($Node.$ChildName | Where-Object { $null -ne $_ }) }
    return @()
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
$scanned = 0
$start = 0
while ($true) {
    $b = New-Body 'GetClientsProducts'
    $b.limitstart = $start
    $b.limitnum = $PageSize
    $r = Invoke-WhmcsApi -ApiUrl $api -Body $b
    $services = Get-WhmcsList $r.products 'product'
    if ($services.Count -le 0) { break }
    foreach ($s in $services) {
        $scanned++
        $fields = Get-WhmcsList $s.customfields 'customfield'
        $hit = $false
        if (("$($s.name)").ToLowerInvariant().Contains($needle)) { $hit = $true }
        foreach ($f in $fields) {
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
    $start += $services.Count
    $total = 0
    if ($r.totalresults) { [void][int]::TryParse($r.totalresults.ToString(), [ref]$total) }
    if ($total -gt 0 -and $start -ge $total) { break }
}

$result = [ordered]@{
    query      = $Query
    scanned    = $scanned
    matchCount = $hits.Count
    truncated  = ($hits.Count -ge $MaxMatches)
    matches    = $hits
}

$json = $result | ConvertTo-Json -Depth 7
if (-not [string]::IsNullOrWhiteSpace($OutputFile)) {
    $dir = Split-Path -Parent $OutputFile
    if (-not [string]::IsNullOrWhiteSpace($dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $json | Out-File -FilePath $OutputFile -Encoding utf8
}
$json
