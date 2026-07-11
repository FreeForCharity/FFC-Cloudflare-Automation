<#
.SYNOPSIS
    Verify the "Live GitHub Pages URL" custom field on pending WHMCS domain
    orders. Read-only.

.DESCRIPTION
    Domain orders (pid 39 = register, pid 41 = transfer) carry a required
    custom field "Live GitHub Pages URL of your validated website" (field ids
    171 and 173). Acceptance of those orders is gated on that site being live,
    and the check has been manual. This script automates it:

      1. GetOrders (status Pending by default) -> the set of orders awaiting
         acceptance.
      2. GetClientsProducts for each configured pid -> services with their
         custom fields; each service carries the orderid that links it back to
         a pending order.
      3. For every pending domain order, extract the GitHub Pages URL custom
         field and verify it: HTTP GET must return 200 AND the body must
         contain the FFC footer marker (default 'Free For Charity',
         configurable via -FooterMarker or WHMCS_VERIFY_FOOTER_MARKER).

    Emits a per-order verdict table (order id, domain, URL, PASS/FAIL +
    reason) on stdout and writes the same rows to -OutputFile as CSV.

    REPORT-ONLY: no WHMCS writes are performed. This script never accepts,
    cancels, or otherwise mutates an order.

    FUTURE WORK (annotation): pushing a failure note back onto the WHMCS order
    would need an order-annotation API action; the WHMCS API exposes none, and
    the only order-write precedent in this repo is the explicit single-order
    accept/cancel/fraud script (whmcs-order-update.ps1). Until a safe
    annotation path exists, failures surface via this report (job summary /
    artifact) for a human to act on.
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

    # WHMCS product ids that represent domain orders.
    [Parameter()]
    [int[]]$ProductIds = @(39, 41),

    # Custom-field ids that hold the live GitHub Pages URL (171 = pid 39,
    # 173 = pid 41). Matched by id first, then by -FieldNamePattern.
    [Parameter()]
    [int[]]$FieldIds = @(171, 173),

    # Fallback regex matched against the custom-field name.
    [Parameter()]
    [string]$FieldNamePattern = 'Live GitHub Pages URL',

    # Marker string the page body must contain (v1: the FFC footer text).
    [Parameter()]
    [string]$FooterMarker,

    # WHMCS order status to gate on.
    [Parameter()]
    [string]$OrderStatus = 'Pending',

    [Parameter()]
    [string]$OutputFile = 'whmcs_domain_order_url_verify.csv',

    [Parameter()]
    [ValidateRange(1, 300)]
    [int]$TimeoutSec = 30,

    [Parameter()]
    [ValidateRange(1, 250)]
    [int]$PageSize = 250
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'whmcs-api-common.ps1')

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

function Get-ProductsFromResponse {
    param([Parameter(Mandatory = $true)]$Response)
    if ($Response.products -and $Response.products.product) {
        return @($Response.products.product)
    }
    if ($Response.products -is [System.Array]) {
        return @($Response.products)
    }
    return @()
}

function Get-CustomFieldNodes {
    # Returns an array of @{ id; name; value } from a WHMCS node that may carry
    # customfields.customfield entries (present on GetClientsProducts).
    param($Node)

    if (-not $Node) { return @() }

    $cf = $null
    try { $cf = $Node.customfields } catch {}
    if (-not $cf) { return @() }

    $entries = @()
    try {
        if ($cf.customfield) { $entries = @($cf.customfield) }
        elseif ($cf -is [System.Array]) { $entries = @($cf) }
    }
    catch {}

    $out = @()
    foreach ($e in $entries) {
        if (-not $e) { continue }
        $id = $null; $name = $null; $value = $null
        try { $id = [string]$e.id } catch {}
        try { $name = [string]$e.name } catch {}
        if ([string]::IsNullOrWhiteSpace($name)) { try { $name = [string]$e.translated_name } catch {} }
        try { $value = [string]$e.value } catch {}
        if ([string]::IsNullOrWhiteSpace($name) -and [string]::IsNullOrWhiteSpace($id)) { continue }
        $out += [pscustomobject]@{ id = $id; name = $name; value = $value }
    }
    return $out
}

function Get-GhPagesUrlFromCustomFields {
    # Extracts the live GitHub Pages URL from a service's custom fields:
    # match by field id first (171/173), then fall back to a name-pattern
    # match. Returns the raw value, or $null when the field is absent/blank.
    [OutputType([string])]
    param(
        [Parameter()]$Fields,
        [Parameter()]
        [int[]]$FieldIds = @(171, 173),
        [Parameter()]
        [string]$FieldNamePattern = 'Live GitHub Pages URL'
    )

    $wanted = @($FieldIds | ForEach-Object { [string]$_ })
    foreach ($f in @($Fields)) {
        if (-not $f) { continue }
        if ($wanted -contains [string]$f.id -and -not [string]::IsNullOrWhiteSpace([string]$f.value)) {
            return ([string]$f.value).Trim()
        }
    }
    foreach ($f in @($Fields)) {
        if (-not $f) { continue }
        if ([string]$f.name -match $FieldNamePattern -and -not [string]::IsNullOrWhiteSpace([string]$f.value)) {
            return ([string]$f.value).Trim()
        }
    }
    return $null
}

function Test-LiveFfcUrl {
    # Verifies a charity-supplied URL: HTTP GET must return 200 and the body
    # must contain $Marker (case-insensitive). Returns
    # @{ Pass; Reason; StatusCode; Url } and never throws.
    param(
        [Parameter()]
        [string]$Url,

        [Parameter(Mandatory = $true)]
        [string]$Marker,

        [Parameter()]
        [int]$TimeoutSec = 30
    )

    $result = [pscustomobject]@{ Pass = $false; Reason = ''; StatusCode = $null; Url = $Url }

    if ([string]::IsNullOrWhiteSpace($Url)) {
        $result.Reason = 'missing URL (custom field empty)'
        return $result
    }

    $candidate = $Url.Trim()
    if ($candidate -notmatch '^[A-Za-z][A-Za-z0-9+.-]*://') {
        $candidate = "https://$candidate"
    }
    $result.Url = $candidate

    $parsed = $null
    if (-not [Uri]::TryCreate($candidate, [UriKind]::Absolute, [ref]$parsed) -or
        @('http', 'https') -notcontains $parsed.Scheme) {
        $result.Reason = "invalid URL (expected http/https): $Url"
        return $result
    }

    try {
        $resp = Invoke-WebRequest -Uri $candidate -Method Get -TimeoutSec $TimeoutSec `
            -MaximumRedirection 5 -SkipHttpErrorCheck -ErrorAction Stop
    }
    catch {
        $result.Reason = "request failed: $($_.Exception.Message)"
        return $result
    }

    $status = 0
    try { $status = [int]$resp.StatusCode } catch {}
    $result.StatusCode = $status
    if ($status -ne 200) {
        $result.Reason = "HTTP $status (expected 200)"
        return $result
    }

    $bodyText = ''
    try { $bodyText = [string]$resp.Content } catch {}
    if ($bodyText -notmatch [regex]::Escape($Marker)) {
        $result.Reason = "footer marker '$Marker' not found in page body"
        return $result
    }

    $result.Pass = $true
    $result.Reason = "HTTP 200 + marker '$Marker' present"
    return $result
}

# When dot-sourced (unit tests), stop here: only the functions are needed.
if ($MyInvocation.InvocationName -eq '.') { return }

function New-DirectoryForFile {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return }
    $dir = Split-Path -Parent $Path
    if (-not [string]::IsNullOrWhiteSpace($dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
}

try {
    $api = Resolve-WhmcsApiUrl -ApiUrlParam $ApiUrl
    $creds = Resolve-WhmcsCredentials -IdentifierParam $Identifier -SecretParam $Secret -CredentialsJsonParam $CredentialsJson
    $resolvedAccessKey = Resolve-WhmcsAccessKey -AccessKeyParam $AccessKey
    $auth = New-WhmcsAuthBody -Creds $creds -AccessKey $resolvedAccessKey

    $marker = $FooterMarker
    if ([string]::IsNullOrWhiteSpace($marker)) { $marker = $env:WHMCS_VERIFY_FOOTER_MARKER }
    if ([string]::IsNullOrWhiteSpace($marker)) { $marker = 'Free For Charity' }

    New-DirectoryForFile -Path $OutputFile

    # 1) Pending orders (paged) -> orderid -> order metadata.
    $pendingOrders = @{}
    $start = 0
    while ($true) {
        $body = $auth.Clone()
        $body.action = 'GetOrders'
        $body.status = $OrderStatus
        $body.limitstart = $start
        $body.limitnum = $PageSize
        $resp = Invoke-WhmcsApi -ApiUrl $api -Body $body
        $page = Get-OrdersFromResponse -Response $resp
        if ($page.Count -le 0) { break }
        foreach ($o in $page) {
            $oid = $null
            try { $oid = [string]$o.id } catch {}
            if (-not [string]::IsNullOrWhiteSpace($oid)) { $pendingOrders[$oid] = $o }
        }
        $start += $page.Count
        $total = 0
        if ($resp.totalresults) { [void][int]::TryParse($resp.totalresults.ToString(), [ref]$total) }
        if ($total -gt 0 -and $start -ge $total) { break }
    }
    Write-Host "$OrderStatus orders found: $($pendingOrders.Count)"

    # 2) Services for the domain products (paged per pid), joined to pending
    #    orders via the service's orderid.
    $rows = [System.Collections.Generic.List[object]]::new()
    foreach ($pid in $ProductIds) {
        $start = 0
        while ($true) {
            $body = $auth.Clone()
            $body.action = 'GetClientsProducts'
            $body.pid = $pid
            $body.limitstart = $start
            $body.limitnum = $PageSize
            $resp = Invoke-WhmcsApi -ApiUrl $api -Body $body
            $page = Get-ProductsFromResponse -Response $resp
            if ($page.Count -le 0) { break }

            foreach ($svc in $page) {
                $orderId = $null
                try { $orderId = [string]$svc.orderid } catch {}
                if ([string]::IsNullOrWhiteSpace($orderId) -or -not $pendingOrders.ContainsKey($orderId)) { continue }

                $domain = $null
                try { $domain = [string]$svc.domain } catch {}
                if ([string]::IsNullOrWhiteSpace($domain)) { try { $domain = [string]$svc.name } catch {} }

                $fields = Get-CustomFieldNodes -Node $svc
                $url = Get-GhPagesUrlFromCustomFields -Fields $fields -FieldIds $FieldIds -FieldNamePattern $FieldNamePattern

                # 3) Verify the URL (read-only HTTP GET).
                $verdict = Test-LiveFfcUrl -Url $url -Marker $marker -TimeoutSec $TimeoutSec

                $order = $pendingOrders[$orderId]
                $rows.Add([pscustomobject]@{
                        orderid    = $orderId
                        ordernum   = [string]$order.ordernum
                        clientid   = [string]$order.userid
                        pid        = $pid
                        domain     = $domain
                        url        = $verdict.Url
                        statuscode = $verdict.StatusCode
                        verdict    = $(if ($verdict.Pass) { 'PASS' } else { 'FAIL' })
                        reason     = $verdict.Reason
                    })
            }

            $start += $page.Count
            $total = 0
            if ($resp.totalresults) { [void][int]::TryParse($resp.totalresults.ToString(), [ref]$total) }
            if ($total -gt 0 -and $start -ge $total) { break }
        }
    }

    $rows | Export-Csv -Path $OutputFile -NoTypeInformation -Encoding UTF8

    $passCount = @($rows | Where-Object { $_.verdict -eq 'PASS' }).Count
    $failCount = @($rows | Where-Object { $_.verdict -eq 'FAIL' }).Count
    Write-Host ''
    Write-Host "Domain-order URL verification ($OrderStatus, pids: $($ProductIds -join ', ')): $($rows.Count) order(s) - PASS $passCount / FAIL $failCount"
    if ($rows.Count -gt 0) {
        $rows | Format-Table orderid, domain, url, verdict, reason -AutoSize | Out-String | Write-Host
    }
    else {
        Write-Host "No $OrderStatus domain orders to verify."
    }
    Write-Host "Report written: $OutputFile"
}
catch {
    Write-Error $_
    exit 1
}
