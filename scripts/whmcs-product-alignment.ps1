<#
.SYNOPSIS
    Align a WHMCS status-marker product (default pid 39 "Domain Registered in
    Cloudflare") onto the clients who own a given set of domains.

.DESCRIPTION
    Takes an authoritative domain list (e.g. the Cloudflare Registrar domains from
    scripts/cloudflare-registrar-domains.ps1), resolves each domain to its WHMCS
    client via GetClientsDomains, and -- for a domain whose client does NOT already
    hold the product -- places a $0 AddOrder for it. Idempotent: a client that
    already holds the product is left untouched. Domains with no WHMCS domain
    record cannot be safely attributed to a client and are reported (never guessed)
    for the client-resolution / onboarding path.

    dry_run is the default: it previews the exact orders without writing. Pass
    -Execute to place orders live (the workflow gates that behind whmcs-prod).

    Read + write both route through the APIM gateway, whose IP is allow-listed at
    WHMCS; calling WHMCS directly is rejected with "Invalid IP".

.PARAMETER DomainsJson
    Path to a JSON array of domains (the work-list).

.PARAMETER ProductId
    WHMCS product id to align. Default 39 (Domain Registered in Cloudflare).

.PARAMETER Execute
    Place orders live. Omit for a dry-run preview (default).

.OUTPUTS
    Writes -OutputFile (JSON report) and a markdown summary to stdout.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$DomainsJson,

    [Parameter()]
    [string]$ProductId = '39',

    [Parameter()]
    [string]$BillingCycle = 'free',

    # 'mailin' is the offline/manual gateway (no card processing). This install
    # does NOT have 'banktransfer'; valid gateways are authorize, authorizeecheck,
    # mailin, paypalcheckout, paypal_acdc, paypal_ppcpv. mailin fits a $0 marker.
    [Parameter()]
    [string]$PaymentMethod = 'mailin',

    [Parameter()]
    [switch]$Execute,

    [Parameter()]
    [string]$ApiUrl,

    [Parameter()]
    [string]$Identifier,

    [Parameter()]
    [string]$Secret,

    [Parameter()]
    [string]$OutputFile = 'artifacts/whmcs/whmcs_product_alignment.json'
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'whmcs-api-common.ps1')

$creds = Resolve-WhmcsCredentials -IdentifierParam $Identifier -SecretParam $Secret
$api = Resolve-WhmcsApiUrl -ApiUrlParam $ApiUrl
$auth = New-WhmcsAuthBody -Creds $creds

if (-not (Test-Path $DomainsJson)) { throw "Domains file not found: $DomainsJson" }
$domains = @(Get-Content $DomainsJson -Raw | ConvertFrom-Json) |
    ForEach-Object { [string]$_ } |
    Where-Object { $_ } |
    ForEach-Object { $_.ToLowerInvariant() } |
    Sort-Object -Unique

function New-Body([string]$action) {
    $b = $auth.Clone()
    $b.action = $action
    $b.responsetype = 'json'
    return $b
}

# client id -> set of domains that already hold the product (cached per client).
# Idempotency is per (client, domain): a client that owns several registrar
# domains gets one product service line per domain, each labelled with it.
# NB: the cache is populated as a side effect and READ back by index (below);
# returning the HashSet from a function would enumerate it in the pipeline.
$productDomainsByClient = @{}
function Set-ClientProductDomainCache {
    param([string]$ClientId)
    $b = New-Body 'GetClientsProducts'
    $b.clientid = $ClientId
    $resp = Invoke-WhmcsApi -ApiUrl $api -Body $b
    $set = New-Object 'System.Collections.Generic.HashSet[string]'
    foreach ($p in @($resp.products.product)) {
        if ("$($p.pid)" -eq $ProductId -and $p.domain) {
            [void]$set.Add(([string]$p.domain).ToLowerInvariant())
        }
    }
    $productDomainsByClient[$ClientId] = $set
}

# domain -> client id (WHMCS domain records only)
$domainClient = @{}
$start = 0
do {
    $b = New-Body 'GetClientsDomains'
    $b.limitstart = $start
    $b.limitnum = 200
    $resp = Invoke-WhmcsApi -ApiUrl $api -Body $b
    $items = @($resp.domains.domain)
    foreach ($d in $items) {
        $name = if ($d.domainname) { [string]$d.domainname } else { [string]$d.domain }
        if ($name) { $domainClient[$name.ToLowerInvariant()] = "$($d.userid)" }
    }
    $start += 200
} while ($items.Count -eq 200)

$aligned = @()
$ordered = @()
$needsClient = @()
$errors = @()

foreach ($domain in $domains) {
    $cid = $domainClient[$domain]
    if (-not $cid) {
        $needsClient += $domain
        continue
    }
    if (-not $productDomainsByClient.ContainsKey($cid)) { Set-ClientProductDomainCache -ClientId $cid }
    $held = $productDomainsByClient[$cid]
    if ($held.Contains($domain)) {
        $aligned += [pscustomobject]@{ domain = $domain; clientId = $cid; status = 'already-held' }
        continue
    }
    if (-not $Execute) {
        $ordered += [pscustomobject]@{ domain = $domain; clientId = $cid; status = 'would-order' }
        continue
    }
    try {
        $b = New-Body 'AddOrder'
        $b.clientid = $cid
        $b.pid = $ProductId
        $b.billingcycle = $BillingCycle
        $b.paymentmethod = $PaymentMethod
        $b.domain = $domain
        # Form-encoded, so use '1' (a boolean $true serializes to "True", which
        # WHMCS mishandles and returns a malformed response for).
        $b.noinvoice = '1'
        $b.noemail = '1'
        $resp = Invoke-WhmcsApi -ApiUrl $api -Body $b
        $ordered += [pscustomobject]@{ domain = $domain; clientId = $cid; status = 'ordered'; orderid = "$($resp.orderid)" }
    }
    catch {
        $errors += [pscustomobject]@{ domain = $domain; clientId = $cid; error = "$($_.Exception.Message)" }
    }
}

$result = [ordered]@{
    generatedAt = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    productId   = $ProductId
    execute     = [bool]$Execute
    counts      = [ordered]@{
        totalDomains = $domains.Count
        alreadyHeld  = $aligned.Count
        ordered      = @($ordered | Where-Object { $_.status -eq 'ordered' }).Count
        wouldOrder   = @($ordered | Where-Object { $_.status -eq 'would-order' }).Count
        needsClient  = $needsClient.Count
        errors       = $errors.Count
    }
    aligned     = $aligned
    ordered     = $ordered
    needsClient = @($needsClient | Sort-Object)
    errors      = $errors
}

$dir = Split-Path -Parent $OutputFile
if (-not [string]::IsNullOrWhiteSpace($dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
$result | ConvertTo-Json -Depth 6 | Out-File -FilePath $OutputFile -Encoding utf8

$mode = if ($Execute) { 'LIVE' } else { 'dry-run' }
Write-Host "## WHMCS product alignment (pid $ProductId, $mode)"
Write-Host ""
Write-Host "- Domains checked: $($domains.Count)"
Write-Host "- Already held (skipped): $($aligned.Count)"
if ($Execute) {
    Write-Host "- Ordered: $(@($ordered | Where-Object { $_.status -eq 'ordered' }).Count)"
}
else {
    Write-Host "- Would order: $($ordered.Count)"
}
Write-Host "- No WHMCS domain record (needs client resolution): $($needsClient.Count)"
Write-Host "- Errors: $($errors.Count)"
if ($errors.Count -gt 0) { throw "Product alignment completed with $($errors.Count) error(s)." }
