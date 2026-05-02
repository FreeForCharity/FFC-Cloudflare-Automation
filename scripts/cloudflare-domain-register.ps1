[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Domain,

    [Parameter()]
    [switch]$CheckOnly,

    [Parameter()]
    [switch]$DryRun,

    [Parameter()]
    [string]$RegistrantName,

    [Parameter()]
    [string]$RegistrantOrg,

    [Parameter()]
    [string]$RegistrantEmail,

    [Parameter()]
    [string]$RegistrantPhone,

    [Parameter()]
    [string]$RegistrantAddress,

    [Parameter()]
    [string]$RegistrantCity,

    [Parameter()]
    [string]$RegistrantState,

    [Parameter()]
    [string]$RegistrantPostalCode,

    [Parameter()]
    [string]$RegistrantCountry = 'US'
)

$ErrorActionPreference = 'Stop'

function Invoke-CfRegistrarApi {
    param(
        [Parameter(Mandatory = $true)][string]$Method,
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter()][object]$Body
    )

    if (-not $env:CLOUDFLARE_REGISTRAR_API_TOKEN) {
        throw 'CLOUDFLARE_REGISTRAR_API_TOKEN is not set.'
    }
    if (-not $env:CLOUDFLARE_ACCOUNT_ID) {
        throw 'CLOUDFLARE_ACCOUNT_ID is not set.'
    }

    $base = 'https://api.cloudflare.com/client/v4'
    $accountId = $env:CLOUDFLARE_ACCOUNT_ID
    $url = "$base/accounts/$accountId$Uri"

    $headers = @{
        Authorization  = "Bearer $env:CLOUDFLARE_REGISTRAR_API_TOKEN"
        'Content-Type' = 'application/json'
    }

    $irmParams = @{
        Method      = $Method
        Uri         = $url
        Headers     = $headers
        ErrorAction = 'Stop'
    }

    if ($null -ne $Body) {
        $irmParams.Body = ($Body | ConvertTo-Json -Depth 10)
    }

    $resp = Invoke-RestMethod @irmParams
    if (-not $resp.success) {
        $msg = ($resp.errors | Select-Object -First 1 -ExpandProperty message -ErrorAction SilentlyContinue)
        if (-not $msg) { $msg = ($resp | ConvertTo-Json -Depth 6) }
        throw "Cloudflare Registrar API error: $msg"
    }

    return $resp
}

function Search-Domain {
    param([string]$Query)

    Write-Host ("Searching for domains matching '{0}'..." -f $Query) -ForegroundColor Cyan

    if (-not $env:CLOUDFLARE_REGISTRAR_API_TOKEN) { throw 'CLOUDFLARE_REGISTRAR_API_TOKEN is not set.' }
    if (-not $env:CLOUDFLARE_ACCOUNT_ID) { throw 'CLOUDFLARE_ACCOUNT_ID is not set.' }

    $base = 'https://api.cloudflare.com/client/v4'
    $accountId = $env:CLOUDFLARE_ACCOUNT_ID
    $encodedQ = [uri]::EscapeDataString($Query)
    $url = "$base/accounts/$accountId/registrar/domain-search?q=$encodedQ&limit=5"

    $resp = Invoke-RestMethod -Method GET -Uri $url `
        -Headers @{ Authorization = "Bearer $env:CLOUDFLARE_REGISTRAR_API_TOKEN" } `
        -ErrorAction Stop

    if (-not $resp.success) {
        $msg = ($resp.errors | Select-Object -First 1 -ExpandProperty message -ErrorAction SilentlyContinue)
        if (-not $msg) { $msg = ($resp | ConvertTo-Json -Depth 6) }
        throw "Cloudflare domain search error: $msg"
    }

    return $resp.result.domains
}

function Confirm-DomainAvailability {
    param([string]$DomainName)

    Write-Host ("Checking availability of '{0}'..." -f $DomainName) -ForegroundColor Cyan

    $resp = Invoke-CfRegistrarApi -Method 'POST' -Uri '/registrar/domain-check' -Body @{
        domains = @($DomainName)
    }

    $domainResult = @($resp.result.domains)[0]

    if (-not $domainResult) {
        throw "Domain check returned no results for '$DomainName'."
    }

    return $domainResult
}

function Register-Domain {
    param(
        [string]$DomainName,
        [hashtable]$Contact
    )

    $body = @{ domain_name = $DomainName }

    if ($Contact -and $Contact.Count -gt 0) {
        $body.contacts = @{
            registrant = $Contact
        }
    }

    Write-Host ("Registering '{0}'..." -f $DomainName) -ForegroundColor Cyan

    $resp = Invoke-CfRegistrarApi -Method 'POST' -Uri '/registrar/registrations' -Body $body
    return $resp.result
}

try {
    $Domain = $Domain.Trim().ToLowerInvariant().Trim('.')
    if ([string]::IsNullOrWhiteSpace($Domain)) { throw 'Domain is required.' }

    # Step 1: Availability check (always run).
    $availability = Confirm-DomainAvailability -DomainName $Domain

    $result = [pscustomobject]@{
        domain       = $availability.name
        registrable  = $availability.registrable
        reason       = if ($availability.registrable) { $null } else { $availability.reason }
        tier         = $availability.tier
        pricing      = $availability.pricing
        registered   = $false
        registration = $null
        dry_run      = [bool]$DryRun
    }

    if (-not $availability.registrable) {
        Write-Host ("Domain '{0}' is NOT available: {1}" -f $Domain, $availability.reason) -ForegroundColor Red
        $result | ConvertTo-Json -Depth 10
        exit 0
    }

    $price = $null
    if ($availability.pricing -and $availability.pricing.registration_cost) {
        $price = $availability.pricing.registration_cost
        $currency = $availability.pricing.currency
        Write-Host ("Domain '{0}' is AVAILABLE at {1} {2}/year." -f $Domain, $price, $currency) -ForegroundColor Green
    }
    else {
        Write-Host ("Domain '{0}' is AVAILABLE (price information unavailable)." -f $Domain) -ForegroundColor Green
    }

    if ($CheckOnly) {
        Write-Host 'CheckOnly mode: stopping after availability check.' -ForegroundColor Yellow
        $result | ConvertTo-Json -Depth 10
        exit 0
    }

    if ($DryRun) {
        Write-Host 'DryRun mode: registration would proceed but is skipped.' -ForegroundColor Yellow
        $result | ConvertTo-Json -Depth 10
        exit 0
    }

    # Step 2: Build optional inline contact if any contact fields are provided.
    $contact = @{}

    if ($RegistrantEmail) { $contact.email = $RegistrantEmail }
    if ($RegistrantPhone) { $contact.phone = $RegistrantPhone }

    $postalInfo = @{}
    if ($RegistrantName) { $postalInfo.name = $RegistrantName }
    if ($RegistrantOrg) { $postalInfo.organization = $RegistrantOrg }

    $address = @{}
    if ($RegistrantAddress) { $address.street = $RegistrantAddress }
    if ($RegistrantCity) { $address.city = $RegistrantCity }
    if ($RegistrantState) { $address.state = $RegistrantState }
    if ($RegistrantPostalCode) { $address.postal_code = $RegistrantPostalCode }
    if ($RegistrantCountry) { $address.country_code = $RegistrantCountry }

    if ($address.Count -gt 0) { $postalInfo.address = $address }
    if ($postalInfo.Count -gt 0) { $contact.postal_info = $postalInfo }

    # Step 3: Register.
    $registration = Register-Domain -DomainName $Domain -Contact $contact

    $result.registered = $true
    $result.registration = $registration

    Write-Host 'Domain registered successfully.' -ForegroundColor Green
    if ($registration.context -and $registration.context.registration) {
        $reg = $registration.context.registration
        Write-Host ("  Status:     {0}" -f $reg.status)
        Write-Host ("  Expires:    {0}" -f $reg.expires_at)
        Write-Host ("  Auto-renew: {0}" -f $reg.auto_renew)
    }

    $result | ConvertTo-Json -Depth 10
    exit 0
}
catch {
    Write-Error $_
    exit 1
}
