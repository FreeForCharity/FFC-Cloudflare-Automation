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
         field and verify liveness: the URL host must be a GitHub Pages host
         (github.io or *.github.io, matching the WHMCS field regex) and an
         HTTP GET must return 200. That liveness result is the PASS/FAIL
         verdict.
      4. Separately, the page body is matched against a footer-marker regex
         (default matches both 'FreeForCharity' and 'Free For Charity',
         case-insensitive; configurable via -FooterMarker or
         WHMCS_VERIFY_FOOTER_MARKER). A marker miss is reported as WARN in its
         own column - it never fails the verdict, because cut-over FFC-EX
         sites do not carry the literal 'Free For Charity' footer text.

    Emits a per-order verdict table (order id, domain, URL, PASS/FAIL +
    reason, footer OK/WARN) on stdout and writes the same rows to
    -OutputFile as CSV.

    REPORT-ONLY: no WHMCS writes are performed. This script never accepts,
    cancels, or otherwise mutates an order.

    FUTURE WORK (annotation): pushing a failure note back onto the WHMCS order
    would need an order-annotation API action; the WHMCS API exposes none, and
    the only order-write precedent in this repo is the explicit single-order
    accept/cancel/fraud script (whmcs-order-update.ps1). Until a safe
    annotation path exists, failures surface via this report (job summary /
    artifact) for a human to act on. The Gate-3 checklist and the WHMCS
    attestation field (id 172) are not machine-read yet either.
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

    # Regex the page body should match (footer heuristic; WARN-only). The
    # default matches 'FreeForCharity' and 'Free For Charity' alike.
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

# Same Chrome UA spoof Invoke-WhmcsApi uses (whmcs-api-common.ps1): charity
# hosts behind Imunify360/bot protection 403 the default PowerShell UA.
$script:FfcVerifyUserAgent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'

# WHMCS JSON responses use two shapes for lists: a plain array (JSON mode) or
# a wrapper object with a named child ({ orders: { order: [...] } }). Member
# enumeration across a plain array yields an array OF NULLS for a missing
# child property (truthy!), so the array shape MUST be detected before any
# child-property truthiness test, and null elements always dropped - same
# hardened pattern as Get-WhmcsList in whmcs-application-detail.ps1.
function Get-WhmcsListNode {
    param($Node, [Parameter(Mandatory = $true)][string]$ChildName)
    if ($null -eq $Node -or $Node -is [string]) { return @() }
    if ($Node -is [System.Array]) { return @($Node | Where-Object { $null -ne $_ }) }
    if ($Node.PSObject.Properties[$ChildName]) { return @($Node.$ChildName | Where-Object { $null -ne $_ }) }
    return @()
}

function Get-OrdersFromResponse {
    param([Parameter(Mandatory = $true)]$Response)
    return Get-WhmcsListNode -Node $Response.orders -ChildName 'order'
}

function Get-ProductsFromResponse {
    param([Parameter(Mandatory = $true)]$Response)
    return Get-WhmcsListNode -Node $Response.products -ChildName 'product'
}

function Get-CustomFieldNodes {
    # Returns an array of @{ id; name; value } from a WHMCS node that may carry
    # customfields.customfield entries (present on GetClientsProducts).
    param($Node)

    if (-not $Node) { return @() }

    $cf = $null
    if ($Node.PSObject.Properties['customfields']) { $cf = $Node.customfields }
    $entries = Get-WhmcsListNode -Node $cf -ChildName 'customfield'

    $out = @()
    foreach ($e in $entries) {
        if (-not $e) { continue }
        $id = if ($e.PSObject.Properties['id']) { [string]$e.id } else { $null }
        $name = if ($e.PSObject.Properties['name'] -and $e.name) { [string]$e.name }
        elseif ($e.PSObject.Properties['translated_name'] -and $e.translated_name) { [string]$e.translated_name }
        else { $null }
        $value = if ($e.PSObject.Properties['value']) { [string]$e.value } else { $null }
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
    # Verifies a charity-supplied URL.
    #   Verdict (Pass): the URL host must be a GitHub Pages host (github.io or
    #   *.github.io - the canonical gate is the free github.io address, per
    #   the WHMCS field regex) AND an HTTP GET must return 200.
    #   Footer heuristic (FooterCheck): $Marker is matched as a
    #   case-insensitive regex against the body; a miss is WARN, never FAIL.
    # Returns @{ Pass; Reason; StatusCode; Url; FooterCheck; FooterNote } and
    # never throws.
    param(
        [Parameter()]
        [string]$Url,

        [Parameter(Mandatory = $true)]
        [string]$Marker,

        [Parameter()]
        [int]$TimeoutSec = 30
    )

    $result = [pscustomobject]@{
        Pass        = $false
        Reason      = ''
        StatusCode  = $null
        Url         = $Url
        FooterCheck = ''
        FooterNote  = ''
    }

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

    # The canonical gate is validation on the free *.github.io address (the
    # WHMCS field regex enforces it). Any other host - a Wix page, an apex
    # domain, anything - is not the artifact this order is gated on.
    $uriHost = $parsed.Host
    if ($uriHost -ne 'github.io' -and $uriHost -notlike '*.github.io') {
        $result.Reason = "not a GitHub Pages URL (host '$uriHost'; expected *.github.io)"
        return $result
    }

    try {
        $resp = Invoke-WebRequest -Uri $candidate -Method Get -TimeoutSec $TimeoutSec `
            -MaximumRedirection 5 -SkipHttpErrorCheck -UserAgent $script:FfcVerifyUserAgent `
            -ErrorAction Stop
    }
    catch {
        $result.Reason = "request failed: $($_.Exception.Message)"
        return $result
    }

    $status = 0
    try { $status = [int]$resp.StatusCode }
    catch { Write-Warning "Could not read HTTP status from response for $($result.Url): $($_.Exception.Message)" }
    $result.StatusCode = $status
    if ($status -ne 200) {
        $result.Reason = "HTTP $status (expected 200)"
        return $result
    }

    # Liveness verdict is settled: GitHub Pages host + HTTP 200.
    $result.Pass = $true
    $result.Reason = 'HTTP 200 on GitHub Pages host'

    # Footer-marker heuristic (WARN-only). Invoke-WebRequest can surface
    # Content as byte[] depending on the response headers; decode it instead
    # of [string]-casting (which yields space-joined byte numbers).
    $bodyText = ''
    try {
        $content = $resp.Content
        if ($content -is [byte[]]) {
            $bodyText = [System.Text.Encoding]::UTF8.GetString($content)
        }
        elseif ($null -ne $content) {
            $bodyText = [string]$content
        }
    }
    catch {
        Write-Warning "Could not read response body from $($result.Url): $($_.Exception.Message)"
        $result.FooterCheck = 'WARN'
        $result.FooterNote = "body unreadable: $($_.Exception.Message)"
        return $result
    }

    $markerHit = $false
    try { $markerHit = [bool]($bodyText -match $Marker) }
    catch {
        Write-Warning "Invalid footer-marker pattern '$Marker': $($_.Exception.Message)"
        $result.FooterCheck = 'WARN'
        $result.FooterNote = "invalid marker pattern '$Marker'"
        return $result
    }

    if ($markerHit) {
        $result.FooterCheck = 'OK'
        $result.FooterNote = "footer marker pattern '$Marker' matched"
    }
    else {
        $result.FooterCheck = 'WARN'
        $result.FooterNote = "footer marker pattern '$Marker' not found in page body"
    }
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
    # Default regex matches 'FreeForCharity' (repo-URL form on cut-over sites)
    # and 'Free For Charity' (template placeholder footer) alike.
    if ([string]::IsNullOrWhiteSpace($marker)) { $marker = 'Free\s?For\s?Charity' }

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
            $oid = [string]$o.id
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
    foreach ($productId in $ProductIds) {
        $start = 0
        while ($true) {
            $body = $auth.Clone()
            $body.action = 'GetClientsProducts'
            $body.pid = $productId
            $body.limitstart = $start
            $body.limitnum = $PageSize
            $resp = Invoke-WhmcsApi -ApiUrl $api -Body $body
            $page = Get-ProductsFromResponse -Response $resp
            if ($page.Count -le 0) { break }

            foreach ($svc in $page) {
                $orderId = [string]$svc.orderid
                if ([string]::IsNullOrWhiteSpace($orderId) -or -not $pendingOrders.ContainsKey($orderId)) { continue }

                $domain = [string]$svc.domain
                if ([string]::IsNullOrWhiteSpace($domain)) { $domain = [string]$svc.name }

                $fields = Get-CustomFieldNodes -Node $svc
                $url = Get-GhPagesUrlFromCustomFields -Fields $fields -FieldIds $FieldIds -FieldNamePattern $FieldNamePattern

                # 3) Verify the URL (read-only HTTP GET).
                $verdict = Test-LiveFfcUrl -Url $url -Marker $marker -TimeoutSec $TimeoutSec

                $order = $pendingOrders[$orderId]
                $rows.Add([pscustomobject]@{
                        orderid     = $orderId
                        ordernum    = [string]$order.ordernum
                        clientid    = [string]$order.userid
                        pid         = $productId
                        domain      = $domain
                        url         = $verdict.Url
                        statuscode  = $verdict.StatusCode
                        verdict     = $(if ($verdict.Pass) { 'PASS' } else { 'FAIL' })
                        reason      = $verdict.Reason
                        footercheck = $verdict.FooterCheck
                        footernote  = $verdict.FooterNote
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
    $warnCount = @($rows | Where-Object { $_.footercheck -eq 'WARN' }).Count
    Write-Host ''
    Write-Host "Domain-order URL verification ($OrderStatus, pids: $($ProductIds -join ', ')): $($rows.Count) order(s) - PASS $passCount / FAIL $failCount / footer WARN $warnCount"
    if ($rows.Count -gt 0) {
        $rows | Format-Table orderid, domain, url, verdict, reason, footercheck -AutoSize | Out-String | Write-Host
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
