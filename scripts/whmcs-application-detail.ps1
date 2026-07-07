<#
.SYNOPSIS
    Export one WHMCS client's charity-application detail (PII-masked). Read-only.

.DESCRIPTION
    Fetches GetClientsDetails + GetOrders for a single client id and emits a
    JSON document containing only website-publishable application fields:
    organization/company name, client status, creation date, custom-field
    values (masked when they look like an email address or phone number), and
    the client's orders (products, dates, statuses).

    Personal contact fields (person name, email, phone, address) are always
    masked to first-initial / domain-only form, mirroring the masking already
    used by the triage workflows whose output lands in public issues.

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

    # WHMCS client id whose application detail to export.
    [Parameter(Mandatory = $true)]
    [int]$ClientId,

    [Parameter()]
    [string]$OutputFile = 'artifacts/whmcs/whmcs_application_detail.json'
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

# Custom-field values are free text; mask any value that looks like personal
# contact data (email/phone). Everything else (legal status, org website,
# mission text, EIN) is application data the charity publishes anyway.
function Format-MaskedCustomValue {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return '' }
    $t = $Value.Trim()
    if ($t -match '^[^@\s]+@[^@\s]+\.[^@\s]+$') { return Format-MaskedEmail $t }
    if ($t -match '^\+?[0-9 ()\-\.]{7,}$') { return '***' }
    return $t
}

$creds = Resolve-WhmcsCredentials -IdentifierParam $Identifier -SecretParam $Secret -CredentialsJsonParam $CredentialsJson
$api = Resolve-WhmcsApiUrl -ApiUrlParam $ApiUrl
$key = Resolve-WhmcsAccessKey -AccessKeyParam $AccessKey

function New-Body {
    param([Parameter(Mandatory = $true)][string]$Action)
    $b = @{
        action       = $Action
        username     = $creds.Identifier
        password     = $creds.Secret
        responsetype = 'json'
    }
    if ($key) { $b.accesskey = $key }
    return $b
}

# WHMCS JSON responses use two shapes for lists: a plain array (JSON mode) or
# a wrapper object with a named child ({ customfields: { customfield: [...] } }).
# Member enumeration across a plain array yields an array OF NULLS for a
# missing child property (truthy!), so detect the shape explicitly and always
# drop null elements.
function Get-WhmcsList {
    param($Node, [Parameter(Mandatory = $true)][string]$ChildName)
    if ($null -eq $Node) { return @() }
    if ($Node -is [System.Array]) { return @($Node | Where-Object { $null -ne $_ }) }
    if ($Node.PSObject.Properties[$ChildName]) { return @($Node.$ChildName | Where-Object { $null -ne $_ }) }
    return @($Node)
}

# --- 1. Client detail ------------------------------------------------------
$body = New-Body 'GetClientsDetails'
$body.clientid = $ClientId
$body.stats = 'false'
$detail = Invoke-WhmcsApi -ApiUrl $api -Body $body

$client = if ($detail.client) { $detail.client } else { $detail }

$fields = Get-WhmcsList $detail.customfields 'customfield'
if (-not $fields) { $fields = Get-WhmcsList $client.customfields 'customfield' }

$customOut = @()
foreach ($f in $fields) {
    $name = if ($f.PSObject.Properties['fieldname'] -and $f.fieldname) { [string]$f.fieldname }
    elseif ($f.PSObject.Properties['name'] -and $f.name) { [string]$f.name }
    else { "field-$($f.id)" }
    $customOut += [ordered]@{
        id    = [string]$f.id
        name  = $name
        value = Format-MaskedCustomValue ([string]$f.value)
    }
}

# --- 2. Orders for the client ---------------------------------------------
$body = New-Body 'GetOrders'
$body.userid = $ClientId
$body.limitstart = 0
$body.limitnum = 250
$ordersResp = Invoke-WhmcsApi -ApiUrl $api -Body $body

$orders = Get-WhmcsList $ordersResp.orders 'order'

$ordersOut = @()
foreach ($o in $orders) {
    $items = Get-WhmcsList $o.lineitems 'lineitem'
    $products = @($items | ForEach-Object { [string]$_.product } | Where-Object { $_ }) -join '; '
    $ordersOut += [ordered]@{
        id       = [string]$o.id
        ordernum = [string]$o.ordernum
        date     = [string]$o.date
        status   = [string]$o.status
        payment  = [string]$o.paymentmethodname
        amount   = [string]$o.amount
        products = $products
    }
}

# --- 3. Publishable projection ---------------------------------------------
$result = [ordered]@{
    clientId     = $ClientId
    status       = [string]$client.status
    companyName  = [string]$client.companyname
    dateCreated  = [string]$client.datecreated
    clientGroup  = [string]$client.groupid
    contact      = [ordered]@{
        name  = Format-MaskedName ("$($client.firstname) $($client.lastname)")
        email = Format-MaskedEmail ([string]$client.email)
    }
    customFields = $customOut
    orders       = $ordersOut
}

$json = $result | ConvertTo-Json -Depth 6
if (-not [string]::IsNullOrWhiteSpace($OutputFile)) {
    $dir = Split-Path -Parent $OutputFile
    if (-not [string]::IsNullOrWhiteSpace($dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    $json | Out-File -FilePath $OutputFile -Encoding utf8
}
$json
