<#
.SYNOPSIS
    Create a WHMCS domain record for a Cloudflare-registered domain and populate
    it with the real registrar facts (expiry, nameservers, lock) from Cloudflare.

.DESCRIPTION
    Cloudflare has no official WHMCS registrar module, so these domains never
    appear in a client's Domains list. This adds the domain the "proper" way:
      1. AddOrder creates the tbldomains record on the client ($0, no invoice).
      2. UpdateClientDomain sets registrar=<Registrar>, status=Active, and the
         registration/expiry dates + nameservers pulled live from the Cloudflare
         Registrar API.
    The domain then shows in the WHMCS UI with real fields (expiry, nameservers,
    status, renewal tracking) alongside the pid-39 "Domain Registered in
    Cloudflare" product. There is no live registrar automation (no module), but
    every field is accurate and staff-manageable.

    Idempotent: if the client already has a domain record for this name, it is
    left untouched. dry-run is the default; pass -Execute to write.

    WHMCS calls route through the APIM gateway (allow-listed IP).

.PARAMETER Domain
    The Cloudflare-registered domain to import.

.PARAMETER ClientId
    The WHMCS client id that owns the domain.

.PARAMETER Registrar
    Registrar label to store on the domain record. Default 'cloudflare'.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Domain,

    [Parameter(Mandatory = $true)]
    [string]$ClientId,

    # WHMCS requires the registrar to be an INSTALLED, ACTIVE module. There is no
    # Cloudflare registrar module here, so leave this blank (registrar = None) —
    # the record still shows with real fields. Only set a value if you install a
    # matching module (e.g. a third-party Cloudflare Registrar for WHMCS).
    [Parameter()]
    [string]$Registrar = '',

    [Parameter()]
    [string]$CloudflareAccountId = '0fa33828a8a294ba7c3e945cec827f12',

    [Parameter()]
    [string]$CloudflareToken,

    [Parameter()]
    [switch]$Execute,

    # Also accept the client's Pending onboarding order (targeted), so a charity
    # that now has a domain isn't left with an un-accepted application.
    [Parameter()]
    [switch]$AcceptOnboarding,

    [Parameter()]
    [string]$ApiUrl,

    [Parameter()]
    [string]$Identifier,

    [Parameter()]
    [string]$Secret,

    [Parameter()]
    [string]$OutputFile
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'whmcs-api-common.ps1')

$domainLc = $Domain.Trim().ToLowerInvariant()
$creds = Resolve-WhmcsCredentials -IdentifierParam $Identifier -SecretParam $Secret
$api = Resolve-WhmcsApiUrl -ApiUrlParam $ApiUrl
$auth = New-WhmcsAuthBody -Creds $creds
function New-Body([string]$action) { $b = $auth.Clone(); $b.action = $action; $b.responsetype = 'json'; return $b }

# --- 1. Cloudflare registrar facts ---
$token = if ($CloudflareToken) { $CloudflareToken } else { $env:CLOUDFLARE_API_TOKEN_FFC }
if ([string]::IsNullOrWhiteSpace($token)) { throw 'Missing Cloudflare token: pass -CloudflareToken or set CLOUDFLARE_API_TOKEN_FFC.' }
$uri = "https://api.cloudflare.com/client/v4/accounts/$CloudflareAccountId/registrar/domains/$domainLc"
$cf = Invoke-RestMethod -Method Get -Uri $uri -Headers @{ Authorization = "Bearer $token" } -TimeoutSec 30
if (-not $cf.success) { throw "Cloudflare registrar lookup failed for $domainLc : $($cf.errors | ConvertTo-Json -Compress)" }
$r = $cf.result
function To-Date($v) { if ($v) { ([datetime]$v).ToUniversalTime().ToString('yyyy-MM-dd') } else { '' } }
$regDate = To-Date $r.registered_at; if (-not $regDate) { $regDate = To-Date $r.created_at }
$expDate = To-Date $r.expires_at
$ns = @($r.name_servers)
$autoRenew = [bool]$r.auto_renew

# --- 2. Idempotency: does the client already have this domain record, and is it populated? ---
$existingId = $null; $existingStatus = ''; $existingExpiry = ''
$start = 0
do {
    $b = New-Body 'GetClientsDomains'; $b.clientid = $ClientId; $b.limitstart = $start; $b.limitnum = 100
    $resp = Invoke-WhmcsApi -ApiUrl $api -Body $b
    $items = @($resp.domains.domain)
    foreach ($d in $items) {
        $name = if ($d.domainname) { [string]$d.domainname } else { [string]$d.domain }
        if ($name.ToLowerInvariant() -eq $domainLc) { $existingId = "$($d.id)"; $existingStatus = [string]$d.status; $existingExpiry = [string]$d.expirydate }
    }
    $start += 100
} while ($items.Count -eq 100 -and -not $existingId)
$isPopulated = ($existingStatus -eq 'Active') -and ($existingExpiry) -and ($existingExpiry -ne '0000-00-00')

function Set-DomainFacts([string]$DomainId) {
    $u = New-Body 'UpdateClientDomain'
    $u.domainid = $DomainId
    if ($Registrar) { $u.registrar = $Registrar }
    $u.status = 'Active'
    if ($regDate) { $u.regdate = $regDate }
    if ($expDate) { $u.expirydate = $expDate; $u.nextduedate = $expDate }
    $u.recurringamount = '0.00'
    $u.donotrenew = if ($autoRenew) { '0' } else { '1' }
    if ($ns.Count -ge 1) { $u.ns1 = $ns[0] }
    if ($ns.Count -ge 2) { $u.ns2 = $ns[1] }
    if ($ns.Count -ge 3) { $u.ns3 = $ns[2] }
    if ($ns.Count -ge 4) { $u.ns4 = $ns[3] }
    [void](Invoke-WhmcsApi -ApiUrl $api -Body $u)
}

$plan = [ordered]@{
    domain = $domainLc; clientId = $ClientId; registrar = ($Registrar ? $Registrar : 'None'); status = 'Active'
    regDate = $regDate; expiryDate = $expDate; nameservers = $ns; autoRenew = $autoRenew
    existingDomainId = $existingId
}

if ($existingId -and $isPopulated) {
    $plan.action = 'skipped-existing'
    Write-Host "Domain '$domainLc' already exists and is populated on client $ClientId (domainid $existingId). Skipping."
}
elseif (-not $Execute) {
    $plan.action = if ($existingId) { 'would-complete' } else { 'would-create' }
    Write-Host "DRY-RUN: would $($plan.action) domain '$domainLc' on client $ClientId"
    Write-Host "  registrar=$($plan.registrar) status=Active regDate=$regDate expiry=$expDate autoRenew=$autoRenew"
    Write-Host "  nameservers: $($ns -join ', ')"
}
else {
    $domainId = $existingId
    if (-not $domainId) {
        # Create the domain record.
        $b = New-Body 'AddOrder'
        $b.clientid = $ClientId
        $b.domain = $domainLc
        $b.domaintype = 'register'
        $b.regperiod = '1'
        $b.domainpriceoverride = '0'
        $b.domainrenewoverride = '0'
        $b.paymentmethod = 'mailin'
        $b.noinvoice = '1'
        $b.noemail = '1'
        $order = Invoke-WhmcsApi -ApiUrl $api -Body $b
        $domainId = ("$($order.domainids)").Split(',')[0].Trim()
        if ([string]::IsNullOrWhiteSpace($domainId)) { throw "AddOrder did not return a domainid for $domainLc (response: $($order | ConvertTo-Json -Compress))" }
        # Accept exactly the order AddOrder created (targeted; no side effects).
        $orderId = "$($order.orderid)"
        if ($orderId) {
            $a = New-Body 'AcceptOrder'; $a.orderid = $orderId; $a.autosetup = $false; $a.sendemail = $false; $a.sendregistrar = $false
            try { [void](Invoke-WhmcsApi -ApiUrl $api -Body $a) } catch {}
        }
        $plan.action = 'created'
    }
    else {
        $plan.action = 'completed-existing'
    }
    Set-DomainFacts $domainId
    $plan.domainId = $domainId
    Write-Host "$($plan.action.ToUpper()) domain '$domainLc' on client $ClientId as domainid $domainId (registrar=$($plan.registrar), expiry=$expDate)."
}

# Optionally accept the client's Pending onboarding order (targeted by name).
if ($Execute -and $AcceptOnboarding) {
    $b = New-Body 'GetOrders'; $b.userid = $ClientId; $b.status = 'Pending'; $b.limitnum = 25
    $r2 = Invoke-WhmcsApi -ApiUrl $api -Body $b
    foreach ($o in @($r2.orders.order)) {
        # Only accept orders that look like the onboarding application (avoid touching unrelated orders).
        $bp = New-Body 'GetOrders'; $bp.id = $o.id
        $rp = Invoke-WhmcsApi -ApiUrl $api -Body $bp
        $isOnboard = $false
        foreach ($p in @(@($rp.orders.order)[0].lineitems.lineitem)) { if ("$($p.producttype)$($p.product)" -match '(?i)onboard') { $isOnboard = $true } }
        if ($isOnboard) {
            $a = New-Body 'AcceptOrder'; $a.orderid = $o.id; $a.autosetup = $false; $a.sendemail = $false; $a.sendregistrar = $false
            try { [void](Invoke-WhmcsApi -ApiUrl $api -Body $a); $plan.acceptedOnboardingOrder = "$($o.id)"; Write-Host "Accepted pending onboarding order $($o.id) for client $ClientId." } catch {}
        }
    }
}

if ($OutputFile) {
    $dir = Split-Path -Parent $OutputFile
    if (-not [string]::IsNullOrWhiteSpace($dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $plan | ConvertTo-Json -Depth 5 | Out-File -FilePath $OutputFile -Encoding utf8
}
$plan | ConvertTo-Json -Depth 5
