<#
.SYNOPSIS
    Probe whether the FFC WHMCS API credential can create catalog products
    (AddProduct). Read-only / non-mutating.

.DESCRIPTION
    1. Confirms GetProducts works (read access to the catalog).
    2. Sends an intentionally INVALID AddProduct call (no name). Because the
       required 'name' field is missing, WHMCS rejects it BEFORE creating
       anything, so nothing is ever written. The error it returns tells us
       whether the credential is *allowed* to call AddProduct at all:
         - a validation error mentioning the missing name  -> 'allowed'
         - a permission/not-permitted/access error          -> 'denied'
         - anything else                                    -> 'unknown'

    Emits a single JSON object on stdout:
      { getProducts: 'success'|'error', productCount, addProductPermission, detail }

    Use this before relying on scripts/whmcs-product-add.ps1's live path; if the
    result is 'denied', create the products via the WHMCS admin UI instead (see
    docs/whmcs-product-catalog.md).
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
    [string]$AccessKey
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'whmcs-api-common.ps1')

try {
    $api = Resolve-WhmcsApiUrl -ApiUrlParam $ApiUrl
    $creds = Resolve-WhmcsCredentials -IdentifierParam $Identifier -SecretParam $Secret -CredentialsJsonParam $CredentialsJson
    $accessKey = Resolve-WhmcsAccessKey -AccessKeyParam $AccessKey
    $auth = New-WhmcsAuthBody -Creds $creds -AccessKey $accessKey

    # 1. GetProducts (read)
    $getResult = 'success'
    $productCount = 0
    try {
        $gp = $auth.Clone()
        $gp.action = 'GetProducts'
        $resp = Invoke-WhmcsApi -ApiUrl $api -Body $gp
        if ($resp.totalresults) { [void][int]::TryParse($resp.totalresults.ToString(), [ref]$productCount) }
    }
    catch {
        $getResult = 'error'
    }

    # 2. Non-mutating AddProduct permission probe (missing required 'name').
    $permission = 'unknown'
    $detail = $null
    try {
        $ap = $auth.Clone()
        $ap.action = 'AddProduct'
        [void](Invoke-WhmcsApi -ApiUrl $api -Body $ap)
        # Unexpected success on a nameless AddProduct; treat as allowed.
        $permission = 'allowed'
        $detail = 'AddProduct returned success unexpectedly (no product should have been created without a name).'
    }
    catch {
        $detail = "$($_.Exception.Message)"
        if ($detail -match '(?i)name') {
            $permission = 'allowed'
        }
        elseif ($detail -match '(?i)permission|not permitted|not allowed|access') {
            $permission = 'denied'
        }
        else {
            $permission = 'unknown'
        }
    }

    [pscustomobject]@{
        getProducts          = $getResult
        productCount         = $productCount
        addProductPermission = $permission
        detail               = $detail
    } | ConvertTo-Json -Depth 6
    exit 0
}
catch {
    Write-Error $_
    exit 1
}
