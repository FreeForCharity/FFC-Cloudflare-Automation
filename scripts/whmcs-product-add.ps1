<#
.SYNOPSIS
    Create a WHMCS catalog product (AddProduct). Used to add the FFC status
    products such as "Domain Registered in Cloudflare (Registrar)" and
    "Hosted by GitHub Pages".

.DESCRIPTION
    Wraps the WHMCS 'AddProduct' API action with the same credential / error
    conventions as the other write scripts in this repo. Emits a single JSON
    object on stdout: { action, dryRun, pid, existing?, skipped? }.

    -DryRun previews the request without writing (secrets redacted) and skips the
    idempotency lookup. On a live run the script first lists products in the
    target group (GetProducts) and, if one already exists with the same name
    (case-insensitive), makes no change and returns
    { ..., existing = true, pid = <id>, skipped = 'existing-product' }.

    NOTE: product groups (gid) are created in the WHMCS admin UI. Supply the gid
    of the target group. After a live create, copy the returned pid into
    config/whmcs-catalog-products.json. If the API credential lacks AddProduct
    permission (see scripts/whmcs-product-capability-check.ps1), create the
    product in the admin UI instead (docs/whmcs-product-catalog.md).
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Name,

    # WHMCS product group id (gid). Groups are created in the admin UI.
    [Parameter(Mandatory = $true)]
    [int]$GroupId,

    [Parameter()]
    [ValidateSet('hostingaccount', 'reselleraccount', 'server', 'other')]
    [string]$Type = 'other',

    [Parameter()]
    [string]$Description,

    # WHMCS payment type for the product.
    [Parameter()]
    [ValidateSet('free', 'onetime', 'recurring')]
    [string]$PaymentType = 'free',

    # Hide the product from the public order form (catalog/status marker only).
    [Parameter()]
    [switch]$Hidden,

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

    # Create even if a product with the same name already exists in the group.
    [Parameter()]
    [switch]$AllowDuplicate,

    [Parameter()]
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'whmcs-api-common.ps1')

function Find-WhmcsProductByName {
    # Returns the pid of an existing product (optionally within a group) whose
    # name matches exactly (case-insensitive), or $null.
    [OutputType([string])]
    param(
        [Parameter(Mandatory = $true)][string]$ApiUrl,
        [Parameter(Mandatory = $true)][hashtable]$Auth,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][int]$GroupId
    )
    $target = $Name.Trim().ToLowerInvariant()
    $body = $Auth.Clone()
    $body.action = 'GetProducts'
    $body.gid = $GroupId
    $resp = Invoke-WhmcsApi -ApiUrl $ApiUrl -Body $body

    $products = @()
    if ($resp.products -and $resp.products.product) { $products = @($resp.products.product) }
    elseif ($resp.products -is [System.Array]) { $products = @($resp.products) }

    foreach ($p in $products) {
        $n = $null
        try { $n = [string]$p.name } catch {}
        if ($n -and $n.Trim().ToLowerInvariant() -eq $target) {
            return [string]$p.pid
        }
    }
    return $null
}

try {
    $api = Resolve-WhmcsApiUrl -ApiUrlParam $ApiUrl
    $creds = Resolve-WhmcsCredentials -IdentifierParam $Identifier -SecretParam $Secret -CredentialsJsonParam $CredentialsJson
    $accessKey = Resolve-WhmcsAccessKey -AccessKeyParam $AccessKey

    $body = @{
        identifier   = $creds.Identifier
        secret       = $creds.Secret
        action       = 'AddProduct'
        responsetype = 'json'
        name         = $Name
        gid          = $GroupId
        type         = $Type
        paytype      = $PaymentType
    }
    if (-not [string]::IsNullOrWhiteSpace($accessKey)) { $body.accesskey = $accessKey }
    if ($Description) { $body.description = $Description }
    if ($Hidden) { $body.hidden = $true }

    if ($DryRun) {
        $preview = $body.Clone()
        foreach ($k in @('secret', 'accesskey')) { if ($preview.ContainsKey($k)) { $preview[$k] = '***' } }
        [pscustomobject]@{ action = 'AddProduct'; dryRun = $true; pid = $null; request = $preview } | ConvertTo-Json -Depth 8
        exit 0
    }

    # Idempotency: skip if a product with this name already exists in the group.
    if (-not $AllowDuplicate) {
        $auth = New-WhmcsAuthBody -Creds $creds -AccessKey $accessKey
        $existingPid = Find-WhmcsProductByName -ApiUrl $api -Auth $auth -Name $Name -GroupId $GroupId
        if ($existingPid) {
            [pscustomobject]@{ action = 'AddProduct'; dryRun = $false; pid = $existingPid; existing = $true; skipped = 'existing-product' } | ConvertTo-Json -Depth 6
            exit 0
        }
    }

    $resp = Invoke-WhmcsApi -ApiUrl $api -Body $body
    $createdPid = $null
    try { $createdPid = [string]$resp.pid } catch {}

    [pscustomobject]@{ action = 'AddProduct'; dryRun = $false; pid = $createdPid; existing = $false } | ConvertTo-Json -Depth 6
    exit 0
}
catch {
    Write-Error $_
    exit 1
}
