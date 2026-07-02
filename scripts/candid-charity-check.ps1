<#
.SYNOPSIS
    Validates a nonprofit's IRS status via the Candid Charity Check v1 API (read-only).

.DESCRIPTION
    Calls GET https://api.candid.org/charitycheck/v1/{ein} and reports the organization's
    501(c)(3) / Pub78 / BMF / OFAC standing. Used during charity onboarding to verify an
    applicant's EIN before provisioning, and for periodic re-verification of partner charities.

    The full API response is written to -OutputFile (JSON) for the workflow artifact; the console
    output and exit code carry the verdict:
      exit 0  - organization found; see printed verdict fields
      exit 1  - request failed (auth, network, invalid EIN format)

.PARAMETER Ein
    The organization's EIN. Any punctuation is accepted (462471893, 46-2471893).

.PARAMETER ApiKey
    Charity Check subscription key. Falls back to CANDID_CHARITY_CHECK_KEY (exported by the
    candid-keys-from-kv composite action).

.PARAMETER OutputFile
    Where to write the raw JSON response. Directory is created if needed.

.EXAMPLE
    ./candid-charity-check.ps1 -Ein 46-2471893 -OutputFile artifacts/candid/charity_check.json
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Ein,

    [string]$ApiKey,

    [string]$OutputFile
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'candid-api-common.ps1')

$key = Resolve-CandidApiKey -KeyParam $ApiKey -Api 'charity-check'
$einNorm = Format-CandidEin -Ein $Ein

Write-Host "Querying Candid Charity Check v1 for EIN $einNorm ..."
$resp = Invoke-CandidApi -Uri "https://api.candid.org/charitycheck/v1/$einNorm" -ApiKey $key

if ($OutputFile) {
    $dir = Split-Path -Parent $OutputFile
    if (-not [string]::IsNullOrWhiteSpace($dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    $resp | ConvertTo-Json -Depth 20 | Out-File -FilePath $OutputFile -Encoding utf8
    Write-Host "Raw response written to $OutputFile"
}

# The response schema nests the verdict under 'data' (see Charity Check example responses at
# developer.candid.org). Read fields defensively so a schema tweak degrades to blanks, not a crash.
$data = if ($resp.PSObject.Properties['data']) { $resp.data } else { $resp }

function Get-Field {
    param($Object, [string[]]$Names)
    foreach ($n in $Names) {
        if ($null -ne $Object -and $Object.PSObject.Properties[$n] -and $null -ne $Object.$n -and "$($Object.$n)" -ne '') {
            return "$($Object.$n)"
        }
    }
    return ''
}

# Explicit array + filter so a missing city or state never leaves stray punctuation.
$cityStateParts = @(
    (Get-Field $data @('city'))
    (Get-Field $data @('state', 'state_name'))
) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }

$verdict = [ordered]@{
    'Organization'      = Get-Field $data @('organization_name', 'org_name', 'name')
    'EIN'               = if ((Get-Field $data @('ein'))) { Get-Field $data @('ein') } else { $einNorm }
    'Pub78 verified'    = Get-Field $data @('pub78_verified')
    'Pub78 org type'    = Get-Field $data @('pub78_organization_type', 'organization_type')
    'Subsection (BMF)'  = Get-Field $data @('subsection_code', 'subsection_description')
    'Foundation status' = Get-Field $data @('foundation_code_description', 'foundation_code', 'foundation_type_description')
    'BMF status'        = Get-Field $data @('bmf_status')
    'Most recent Pub78' = Get-Field $data @('most_recent_pub_78', 'most_recent_pub78')
    'Most recent BMF'   = Get-Field $data @('most_recent_bmf', 'most_recent_irb')
    'OFAC status'       = Get-Field $data @('ofac_status')
    'Revocation code'   = Get-Field $data @('revocation_code')
    'Revocation date'   = Get-Field $data @('revocation_date')
    'City / State'      = $cityStateParts -join ', '
}

Write-Host ''
Write-Host '=== Charity Check verdict ==='
foreach ($k in $verdict.Keys) {
    $v = if ([string]::IsNullOrWhiteSpace([string]$verdict[$k])) { '(not present in response)' } else { $verdict[$k] }
    Write-Host ('{0,-20}: {1}' -f $k, $v)
}

# Expose the verdict to callers (workflow step summary) as JSON on a well-known variable.
$script:CandidCharityCheckVerdict = $verdict
if ($env:GITHUB_OUTPUT) {
    $flat = ($verdict.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }) -join '; '
    Add-Content -Path $env:GITHUB_OUTPUT -Value "verdict_line=$flat"
}
