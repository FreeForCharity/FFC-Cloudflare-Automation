<#
.SYNOPSIS
    Searches Candid's nonprofit database via the Essentials v4 API (read-only).

.DESCRIPTION
    Calls POST https://api.candid.org/essentials/v4 with the given search terms (EIN, organization
    name, keywords) and prints a compact match table (name, EIN, location, Candid seal level,
    profile URL). The full API response is written to -OutputFile (JSON) for the workflow
    artifact.

    Typical FFC uses: find a charity's Candid profile / seal level during onboarding, and pull the
    profile links used on FFC-EX template sites (GuideStar profile URL fields).

.PARAMETER SearchTerms
    Free-text search: EIN, organization name, or keywords.

.PARAMETER Size
    Max results to return (1-25, Candid's per-request cap). Default 10.

.PARAMETER ApiKey
    Essentials subscription key. Falls back to CANDID_ESSENTIALS_KEY (exported by the
    candid-keys-from-kv composite action).

.PARAMETER OutputFile
    Where to write the raw JSON response. Directory is created if needed.

.EXAMPLE
    ./candid-essentials-search.ps1 -SearchTerms 'Free For Charity' -OutputFile artifacts/candid/essentials.json
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SearchTerms,

    [ValidateRange(1, 25)]
    [int]$Size = 10,

    [string]$ApiKey,

    [string]$OutputFile
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'candid-api-common.ps1')

$key = Resolve-CandidApiKey -KeyParam $ApiKey -Api 'essentials'

Write-Host "Querying Candid Essentials v4 for '$SearchTerms' (size $Size) ..."
$body = @{
    search_terms = $SearchTerms
    size         = $Size
    from         = 0
}
$resp = Invoke-CandidApi -Uri 'https://api.candid.org/essentials/v4' -ApiKey $key -Method Post -Body $body

if ($OutputFile) {
    $dir = Split-Path -Parent $OutputFile
    if (-not [string]::IsNullOrWhiteSpace($dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    $resp | ConvertTo-Json -Depth 20 | Out-File -FilePath $OutputFile -Encoding utf8
    Write-Host "Raw response written to $OutputFile"
}

# Essentials v4 returns matches under 'hits'; each hit carries an 'organization' block plus
# 'properties' / 'geography' details. Read defensively so schema drift degrades gracefully.
$hits = @()
if ($resp.PSObject.Properties['hits'] -and $resp.hits) { $hits = @($resp.hits) }
elseif ($resp.PSObject.Properties['data'] -and $resp.data -and $resp.data.PSObject.Properties['hits']) { $hits = @($resp.data.hits) }

function Get-Field {
    param($Object, [string[]]$Names)
    foreach ($n in $Names) {
        if ($null -ne $Object -and $Object.PSObject.Properties[$n] -and $null -ne $Object.$n -and "$($Object.$n)" -ne '') {
            return "$($Object.$n)"
        }
    }
    return ''
}

Write-Host ''
Write-Host "=== Essentials matches: $($hits.Count) ==="
$rows = foreach ($hit in $hits) {
    $org = if ($hit.PSObject.Properties['organization'] -and $hit.organization) { $hit.organization } else { $hit }
    $geo = if ($hit.PSObject.Properties['geography'] -and $hit.geography) { $hit.geography } else { $org }
    $props = if ($hit.PSObject.Properties['properties'] -and $hit.properties) { $hit.properties } else { $org }
    [pscustomobject]@{
        Name       = Get-Field $org @('organization_name', 'org_name', 'name')
        EIN        = Get-Field $org @('ein')
        City       = Get-Field $geo @('city')
        State      = Get-Field $geo @('state', 'state_name')
        Seal       = Get-Field $props @('transparency_seal', 'seal_level', 'profile_level', 'seal')
        ProfileUrl = Get-Field $org @('candid_profile_link', 'profile_link', 'guidestar_url', 'url')
    }
}
if ($rows) {
    $rows | Format-Table -AutoSize | Out-String -Width 300 | Write-Host
}
else {
    Write-Host '(no matches)'
}

if ($env:GITHUB_OUTPUT) {
    Add-Content -Path $env:GITHUB_OUTPUT -Value "match_count=$($hits.Count)"
}
