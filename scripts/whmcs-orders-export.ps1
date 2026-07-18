<#
.SYNOPSIS
    Export WHMCS orders (GetOrders). Read-only.

.DESCRIPTION
    Pages through GetOrders and writes a CSV. Optional filters: -Status
    (Pending, Active, Fraud, Cancelled, ...), -ClientId. Emits a one-line
    summary on stdout. No writes are performed.

    CSV columns follow the live GetOrders response shape:
    id, ordernum, userid, contactid, name, status, paymentstatus, amount,
    paymentmethod, paymentmethodname, date, fraudmodule, invoiceid, ipaddress,
    lineitemcount, products.
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

    # WHMCS order status filter (e.g. Pending, Active, Fraud, Cancelled).
    [Parameter()]
    [string]$Status,

    # Filter to a single client's orders (WHMCS GetOrders 'userid').
    [Parameter()]
    [int]$ClientId,

    [Parameter()]
    [string]$OutputFile = 'whmcs_orders.csv',

    [Parameter()]
    [ValidateRange(1, 250)]
    [int]$PageSize = 250
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'whmcs-api-common.ps1')

function New-DirectoryForFile {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return }
    $dir = Split-Path -Parent $Path
    if (-not [string]::IsNullOrWhiteSpace($dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
}

function Get-OrdersFromResponse {
    param([Parameter(Mandatory = $true)]$Response)
    if ($Response.orders -and $Response.orders.order) {
        return @($Response.orders.order)
    }
    if ($Response.orders -is [System.Array]) {
        return @($Response.orders)
    }
    return @()
}

function Get-OrderLineItems {
    # Returns @{ Count = <int>; Products = '<semicolon-joined product names>' }
    param($Order)
    $items = @()
    if ($Order.lineitems -and $Order.lineitems.lineitem) {
        $items = @($Order.lineitems.lineitem)
    }
    elseif ($Order.lineitems -is [System.Array]) {
        $items = @($Order.lineitems)
    }
    $names = foreach ($li in $items) {
        $n = $null
        try { $n = [string]$li.product } catch {}
        if ([string]::IsNullOrWhiteSpace($n)) { try { $n = [string]$li.type } catch {} }
        if (-not [string]::IsNullOrWhiteSpace($n)) { $n }
    }
    return @{ Count = @($items).Count; Products = (@($names) -join '; ') }
}

try {
    $api = Resolve-WhmcsApiUrl -ApiUrlParam $ApiUrl
    $creds = Resolve-WhmcsCredentials -IdentifierParam $Identifier -SecretParam $Secret -CredentialsJsonParam $CredentialsJson
    $accessKey = Resolve-WhmcsAccessKey -AccessKeyParam $AccessKey

    New-DirectoryForFile -Path $OutputFile

    $all = [System.Collections.Generic.List[object]]::new()
    $start = 0
    while ($true) {
        $body = @{
            identifier   = $creds.Identifier
            secret       = $creds.Secret
            action       = 'GetOrders'
            responsetype = 'json'
            limitstart   = $start
            limitnum     = $PageSize
        }
        if (-not [string]::IsNullOrWhiteSpace($accessKey)) { $body.accesskey = $accessKey }
        if ($Status) { $body.status = $Status }
        if ($PSBoundParameters.ContainsKey('ClientId')) { $body.userid = $ClientId }

        $resp = Invoke-WhmcsApi -ApiUrl $api -Body $body
        $page = Get-OrdersFromResponse -Response $resp
        if ($page.Count -le 0) { break }

        $all.AddRange([object[]]$page)
        $start += $page.Count

        $total = 0
        if ($resp.totalresults) { [void][int]::TryParse($resp.totalresults.ToString(), [ref]$total) }
        if ($total -gt 0 -and $start -ge $total) { break }
    }

    $rows = foreach ($o in $all) {
        $li = Get-OrderLineItems -Order $o
        [pscustomobject]@{
            id                = $o.id
            ordernum          = $o.ordernum
            userid            = $o.userid
            contactid         = $o.contactid
            name              = $o.name
            status            = $o.status
            paymentstatus     = $o.paymentstatus
            amount            = $o.amount
            paymentmethod     = $o.paymentmethod
            paymentmethodname = $o.paymentmethodname
            date              = $o.date
            fraudmodule       = $o.fraudmodule
            invoiceid         = $o.invoiceid
            ipaddress         = $o.ipaddress
            lineitemcount     = $li.Count
            products          = $li.Products
        }
    }
    $rows | Export-Csv -Path $OutputFile -NoTypeInformation -Encoding UTF8
    Write-Host "Exported orders: $(@($rows).Count) -> $OutputFile"
}
catch {
    Write-Error $_
    exit 1
}
