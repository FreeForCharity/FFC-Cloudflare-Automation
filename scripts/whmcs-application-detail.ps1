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
    # An EIN is public (IRS BMF / GuideStar publish it), so it must NOT be
    # masked — but its NN-NNNNNNN shape (9 digits, one hyphen) otherwise trips
    # the generic phone matcher below. Pass EIN-shaped values through first.
    if ($t -match '^\d{2}-?\d{7}$') { return $t }
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
# drop null elements. Anything else (empty-string container, wrapper without
# the child) is an empty list - mirroring the sibling export scripts.
function Get-WhmcsList {
    param($Node, [Parameter(Mandatory = $true)][string]$ChildName)
    if ($null -eq $Node -or $Node -is [string]) { return @() }
    if ($Node -is [System.Array]) { return @($Node | Where-Object { $null -ne $_ }) }
    if ($Node.PSObject.Properties[$ChildName]) { return @($Node.$ChildName | Where-Object { $null -ne $_ }) }
    return @()
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

# --- 3. Products/services + their custom fields ----------------------------
# The onboarding application's answers are captured as PRODUCT custom fields
# on the charity-onboarding service (GetClientsProducts returns field NAMES,
# unlike client-level GetClientsDetails). Mask a value when it looks like
# contact data OR the field name says it holds a person's name/contact -
# organization/charity-name and mission fields stay readable.
function Format-MaskedProductField {
    param([string]$FieldName, [string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return '' }
    if ($FieldName -match '(?i)\b(first|last|your|contact|poc)[ _-]?name\b' -and
        $FieldName -notmatch '(?i)org|charity|company|nonprofit|foundation|business') {
        return Format-MaskedName $Value
    }
    if ($FieldName -match '(?i)phone|email|e-mail') {
        if ($Value -match '@') { return Format-MaskedEmail $Value.Trim() }
        return '***'
    }
    return Format-MaskedCustomValue $Value
}

$body = New-Body 'GetClientsProducts'
$body.clientid = $ClientId
$body.limitstart = 0
$body.limitnum = 250
$prodResp = Invoke-WhmcsApi -ApiUrl $api -Body $body

$productsOut = @()
foreach ($p in (Get-WhmcsList $prodResp.products 'product')) {
    $cfOut = @()
    foreach ($f in (Get-WhmcsList $p.customfields 'customfield')) {
        $fieldName = if ($f.PSObject.Properties['name'] -and $f.name) { [string]$f.name }
        elseif ($f.PSObject.Properties['fieldname'] -and $f.fieldname) { [string]$f.fieldname }
        else { "field-$($f.id)" }
        $cfOut += [ordered]@{
            id    = [string]$f.id
            name  = $fieldName
            value = Format-MaskedProductField -FieldName $fieldName -Value ([string]$f.value)
        }
    }
    $productsOut += [ordered]@{
        id           = [string]$p.id
        product      = [string]$p.name
        status       = [string]$p.status
        regDate      = [string]$p.regdate
        billingCycle = [string]$p.billingcycle
        amount       = [string]$p.recurringamount
        customFields = $cfOut
    }
}

# --- 4. Publishable projection ---------------------------------------------
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
    products     = $productsOut
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
