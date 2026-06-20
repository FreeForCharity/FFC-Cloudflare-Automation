<#
.SYNOPSIS
    Create a WHMCS order/service for an existing client (AddOrder). Used to place
    a charity onto an onboarding product (e.g. pre-501(c)(3) or 501(c)(3)).

.DESCRIPTION
    Wraps the WHMCS 'AddOrder' API action following the same credential / error
    conventions as the other scripts in this repo. Emits a single JSON object on
    stdout: { action, dryRun, orderid, invoiceid, productids }. Use -DryRun to
    preview the request (no write); secrets are stripped from the preview.

    Product custom fields are populated by id via -CustomFieldsJson
    '{"<productCustomFieldId>":"value"}'. Discover the ids by running the WHMCS
    products export report (it prints each product's custom fields).

.NOTES
    -ProductId / -BillingCycle / custom-field ids are install-specific; confirm
    them from the products export before running live (see issue tracking the
    onboarding follow-ups).
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [int]$ClientId,

    [Parameter(Mandatory = $true)]
    [int]$ProductId,

    # WHMCS billing cycle: free, onetime, monthly, quarterly, semiannually,
    # annually, biennially, triennially. Charity onboarding is typically 'free'.
    [Parameter()]
    [ValidateSet('free', 'onetime', 'monthly', 'quarterly', 'semiannually', 'annually', 'biennially', 'triennially')]
    [string]$BillingCycle = 'free',

    [Parameter()]
    [string]$PaymentMethod = 'banktransfer',

    # For domain-type products / products that take a domain.
    [Parameter()]
    [string]$Domain,

    # Override the product price for this order (e.g. 0 for sponsored).
    [Parameter()]
    [decimal]$PriceOverride,

    # JSON object of { "<productCustomFieldId>": "value", ... }
    [Parameter()]
    [string]$CustomFieldsJson,

    # Do not generate an invoice for this order.
    [Parameter()]
    [switch]$NoInvoice,

    # Suppress the order confirmation email.
    [Parameter()]
    [switch]$NoEmail,

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
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'whmcs-api-common.ps1')

try {
    $api = Resolve-WhmcsApiUrl -ApiUrlParam $ApiUrl
    $creds = Resolve-WhmcsCredentials -IdentifierParam $Identifier -SecretParam $Secret -CredentialsJsonParam $CredentialsJson
    $accessKey = Resolve-WhmcsAccessKey -AccessKeyParam $AccessKey

    $body = @{
        identifier    = $creds.Identifier
        secret        = $creds.Secret
        action        = 'AddOrder'
        responsetype  = 'json'
        clientid      = $ClientId
        pid           = $ProductId
        billingcycle  = $BillingCycle
        paymentmethod = $PaymentMethod
    }
    if (-not [string]::IsNullOrWhiteSpace($accessKey)) { $body.accesskey = $accessKey }

    if ($Domain) { $body.domain = $Domain }
    if ($PSBoundParameters.ContainsKey('PriceOverride')) { $body.priceoverride = $PriceOverride }
    if ($NoInvoice) { $body.noinvoice = $true }
    if ($NoEmail) { $body.noemail = $true }

    if (-not [string]::IsNullOrWhiteSpace($CustomFieldsJson)) {
        $body.customfields = ConvertTo-WhmcsCustomFields -Json $CustomFieldsJson
    }

    if ($DryRun) {
        $preview = $body.Clone()
        foreach ($k in @('secret', 'accesskey')) { if ($preview.ContainsKey($k)) { $preview[$k] = '***' } }
        [pscustomobject]@{ action = 'AddOrder'; dryRun = $true; orderid = $null; invoiceid = $null; productids = $null; request = $preview } | ConvertTo-Json -Depth 8
        exit 0
    }

    $resp = Invoke-WhmcsApi -ApiUrl $api -Body $body
    $orderId = $null; $invoiceId = $null; $productIds = $null
    try { $orderId = [string]$resp.orderid } catch {}
    try { $invoiceId = [string]$resp.invoiceid } catch {}
    try { $productIds = [string]$resp.productids } catch {}

    [pscustomobject]@{ action = 'AddOrder'; dryRun = $false; orderid = $orderId; invoiceid = $invoiceId; productids = $productIds } | ConvertTo-Json -Depth 6
    exit 0
}
catch {
    Write-Error $_
    exit 1
}
