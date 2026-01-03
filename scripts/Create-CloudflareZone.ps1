<#
.SYNOPSIS
    Create a new Cloudflare zone (domain)

.DESCRIPTION
    Creates a new zone in Cloudflare using the Cloudflare API.
    Requires CLOUDFLARE_API_KEY_DNS_ONLY and CLOUDFLARE_ACCOUNT_ID environment variables.

.PARAMETER Domain
    The domain name to add as a zone (e.g., "example.org")

.PARAMETER ZoneType
    The zone type. Valid values: full, partial. Default: full

.PARAMETER JumpStart
    Enable jump start (auto-scan existing DNS records). Default: true

.EXAMPLE
    .\Create-CloudflareZone.ps1 -Domain example.org -ZoneType full

.EXAMPLE
    $env:CLOUDFLARE_API_KEY_DNS_ONLY = "your_token"
    $env:CLOUDFLARE_ACCOUNT_ID = "your_account_id"
    .\Create-CloudflareZone.ps1 -Domain example.org
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, HelpMessage = "The domain name to add")]
    [string]$Domain,

    [Parameter(Mandatory = $false)]
    [ValidateSet('full', 'partial')]
    [string]$ZoneType = 'full',

    [Parameter(Mandatory = $false)]
    [bool]$JumpStart = $true,

    [Parameter(Mandatory = $false)]
    [string]$Token = $env:CLOUDFLARE_API_KEY_DNS_ONLY,

    [Parameter(Mandatory = $false)]
    [string]$AccountId = $env:CLOUDFLARE_ACCOUNT_ID
)

# Validate inputs
if ([string]::IsNullOrWhiteSpace($Token)) {
    Write-Error "CLOUDFLARE_API_KEY_DNS_ONLY environment variable or -Token parameter is required."
    exit 1
}

if ([string]::IsNullOrWhiteSpace($AccountId)) {
    Write-Error "CLOUDFLARE_ACCOUNT_ID environment variable or -AccountId parameter is required."
    exit 1
}

# Cloudflare API settings
$ApiBase = 'https://api.cloudflare.com/client/v4'
$Headers = @{
    'Authorization' = "Bearer $Token"
    'Content-Type'  = 'application/json'
}

Write-Host "=== Cloudflare Zone Creation ===" -ForegroundColor Cyan
Write-Host "Domain: $Domain"
Write-Host "Zone Type: $ZoneType"
Write-Host "Account ID: $($AccountId.Substring(0, 8))..." -ForegroundColor DarkGray

# Check if zone already exists
Write-Host "`nChecking if zone already exists..." -ForegroundColor Yellow

try {
    $checkParams = @{
        Method      = 'GET'
        Uri         = "$ApiBase/zones?name=$Domain"
        Headers     = $Headers
        ContentType = 'application/json'
    }
    
    $checkResponse = Invoke-RestMethod @checkParams
    
    if ($checkResponse.success -and $checkResponse.result.Count -gt 0) {
        $existingZone = $checkResponse.result[0]
        Write-Host "`n✓ Zone already exists!" -ForegroundColor Green
        Write-Host "Zone ID: $($existingZone.id)"
        Write-Host "Status: $($existingZone.status)"
        Write-Host "Name Servers:" -ForegroundColor Cyan
        foreach ($ns in $existingZone.name_servers) {
            Write-Host "  - $ns"
        }
        
        # Output for GitHub Actions
        Write-Host "`n::notice title=Zone Already Exists::Zone '$Domain' already exists with ID: $($existingZone.id)"
        exit 0
    }
}
catch {
    Write-Warning "Error checking existing zone: $($_.Exception.Message)"
    # Continue to create
}

# Create the zone
Write-Host "`nCreating new zone..." -ForegroundColor Yellow

$body = @{
    name    = $Domain
    account = @{
        id = $AccountId
    }
    type    = $ZoneType
}

if ($JumpStart) {
    $body['jump_start'] = $true
}

try {
    $createParams = @{
        Method      = 'POST'
        Uri         = "$ApiBase/zones"
        Headers     = $Headers
        Body        = ($body | ConvertTo-Json -Depth 10)
        ContentType = 'application/json'
    }
    
    $response = Invoke-RestMethod @createParams
    
    if ($response.success) {
        $zone = $response.result
        Write-Host "`n✓ Zone created successfully!" -ForegroundColor Green
        Write-Host "Zone ID: $($zone.id)"
        Write-Host "Status: $($zone.status)"
        Write-Host "Name Servers:" -ForegroundColor Cyan
        foreach ($ns in $zone.name_servers) {
            Write-Host "  - $ns"
        }
        
        # Output for GitHub Actions
        Write-Host "`n::notice title=Zone Created::Zone '$Domain' created with ID: $($zone.id)"
        Write-Host "::set-output name=zone_id::$($zone.id)"
        Write-Host "::set-output name=name_servers::$($zone.name_servers -join ',')"
        Write-Host "::set-output name=status::$($zone.status)"
        
        exit 0
    }
    else {
        $errorMsg = ($response.errors | ConvertTo-Json)
        Write-Error "Zone creation failed: $errorMsg"
        exit 1
    }
}
catch {
    Write-Error "Failed to create zone: $($_.Exception.Message)"
    if ($_.Exception.Response) {
        $reader = [System.IO.StreamReader]::new($_.Exception.Response.GetResponseStream())
        $responseBody = $reader.ReadToEnd()
        Write-Error "API Response: $responseBody"
    }
    exit 1
}
