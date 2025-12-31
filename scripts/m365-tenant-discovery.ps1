[CmdletBinding()]
param(
    [Parameter()]
    [ValidateSet('Interactive', 'DeviceCode')]
    [string]$Auth = 'Interactive',

    [Parameter()]
    [string]$UserPrincipalName = 'clarkemoyer@freeforcharity.org',

    [Parameter()]
    [switch]$AlsoCheckExchangeOnline
)

$ErrorActionPreference = 'Stop'

function Ensure-Module {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    if (-not (Get-Module -ListAvailable -Name $Name)) {
        Write-Host "Installing PowerShell module: $Name" -ForegroundColor Yellow
        Install-Module -Name $Name -Scope CurrentUser -Force -AllowClobber
    }
}

function Connect-GraphInteractive {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('Interactive', 'DeviceCode')]
        [string]$Mode
    )

    Ensure-Module -Name 'Microsoft.Graph'
    Import-Module Microsoft.Graph -ErrorAction Stop

    $scopes = @(
        'Organization.Read.All',
        'Domain.Read.All'
    )

    if ($Mode -eq 'DeviceCode') {
        Connect-MgGraph -Scopes $scopes -UseDeviceCode | Out-Null
    } else {
        Connect-MgGraph -Scopes $scopes | Out-Null
    }
}

function Write-Kv {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Key,
        [Parameter()]
        [AllowNull()]
        [object]$Value
    )

    $v = if ($null -eq $Value) { '' } else { [string]$Value }
    Write-Host ("{0}: {1}" -f $Key, $v)
}

try {
    Write-Host "Logging into Microsoft Graph ($Auth)..." -ForegroundColor Cyan
    Connect-GraphInteractive -Mode $Auth

    $ctx = Get-MgContext
    Write-Host "" 
    Write-Host "Tenant discovery" -ForegroundColor Cyan
    Write-Kv -Key 'TenantId' -Value $ctx.TenantId
    Write-Kv -Key 'Account' -Value $ctx.Account

    $org = Get-MgOrganization -ErrorAction Stop | Select-Object -First 1
    if ($org) {
        Write-Kv -Key 'Organization' -Value $org.DisplayName
        Write-Kv -Key 'OrganizationId' -Value $org.Id
    }

    $domains = Get-MgDomain -All -ErrorAction Stop
    $defaultDomain = $domains | Where-Object { $_.IsDefault } | Select-Object -First 1
    $onMicrosoft = $domains | Where-Object { $_.Id -like '*.onmicrosoft.com' } | Select-Object -First 1

    Write-Host "" 
    Write-Host "Domains" -ForegroundColor Cyan
    Write-Kv -Key 'DefaultDomain' -Value $defaultDomain.Id
    Write-Kv -Key 'OnMicrosoftDomain' -Value $onMicrosoft.Id

    Write-Host "" 
    Write-Host "Recommended GitHub Actions secrets (m365-prod environment)" -ForegroundColor Cyan
    Write-Host ("AZURE_TENANT_ID={0}" -f $ctx.TenantId)
    Write-Host ("EXO_TENANT={0}" -f $onMicrosoft.Id)
    Write-Host ("EXO_ORGANIZATION={0}" -f $onMicrosoft.Id)
    Write-Host "AZURE_CLIENT_ID=<Entra app (same app for Graph + EXO)>"
    Write-Host "EXO_CERT_PFX_BASE64=<base64 PFX for app-only EXO auth>"

    if ($AlsoCheckExchangeOnline) {
        Write-Host "" 
        Write-Host "Checking Exchange Online DKIM cmdlets (interactive)..." -ForegroundColor Cyan

        Ensure-Module -Name 'ExchangeOnlineManagement'
        Import-Module ExchangeOnlineManagement -ErrorAction Stop

        Connect-ExchangeOnline -UserPrincipalName $UserPrincipalName -ShowBanner:$false | Out-Null

        $cmd = Get-Command Get-DkimSigningConfig -ErrorAction SilentlyContinue
        if (-not $cmd) {
            Write-Host "Get-DkimSigningConfig not found. You may lack permissions or the cmdlet isn’t available." -ForegroundColor Yellow
        } else {
            Write-Host "Exchange Online DKIM cmdlets are available." -ForegroundColor Green
        }

        Disconnect-ExchangeOnline -Confirm:$false | Out-Null
    }

    Disconnect-MgGraph | Out-Null
    exit 0
} catch {
    Write-Error $_
    try { Disconnect-MgGraph | Out-Null } catch { }
    try { Disconnect-ExchangeOnline -Confirm:$false | Out-Null } catch { }
    exit 1
}
