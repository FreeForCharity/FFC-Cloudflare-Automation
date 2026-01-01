[CmdletBinding()]
param(
    [Parameter()]
    [ValidateSet('Interactive', 'DeviceCode')]
    [string]$Auth = 'Interactive',

    [Parameter()]
    [string]$UserPrincipalName = 'clarkemoyer@freeforcharity.org',

    [Parameter()]
    [ValidateSet('Auto', 'AzureCli', 'GraphModule')]
    [string]$LoginProvider = 'Auto',

    [Parameter()]
    [switch]$AlsoCheckExchangeOnline
)

$ErrorActionPreference = 'Stop'

function Ensure-Module {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    try {
        if (-not (Get-PackageProvider -Name NuGet -ErrorAction SilentlyContinue)) {
            Write-Host "Installing NuGet package provider..." -ForegroundColor Yellow
            Install-PackageProvider -Name NuGet -MinimumVersion 2.8.5.201 -Force | Out-Null
        }
    } catch {
        # Best-effort; module install may still succeed.
    }

    try {
        Set-PSRepository -Name PSGallery -InstallationPolicy Trusted -ErrorAction SilentlyContinue | Out-Null
    } catch {
        # Best-effort; if this fails, Install-Module may prompt.
    }

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
        Write-Host "Device Code login selected." -ForegroundColor Yellow
        Write-Host "If you do not see a device login URL/code, the session is likely still installing modules." -ForegroundColor DarkGray
        Connect-MgGraph -Scopes $scopes -UseDeviceCode | Out-Null
        return
    }

    Write-Host "Interactive login selected." -ForegroundColor Green
    Write-Host "A browser window/tab should open for Microsoft sign-in." -ForegroundColor DarkGray
    Write-Host "If nothing opens, re-run with: -Auth DeviceCode" -ForegroundColor DarkGray
    Connect-MgGraph -Scopes $scopes | Out-Null
}

function Connect-GraphViaAzureCli {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('Interactive', 'DeviceCode')]
        [string]$Mode
    )

    $az = Get-Command az -ErrorAction SilentlyContinue
    if (-not $az) {
        throw "Azure CLI (az) not found. Install Azure CLI or use -LoginProvider GraphModule."
    }

    Write-Host "Using Azure CLI for login + Graph token." -ForegroundColor Green

    if ($Mode -eq 'DeviceCode') {
        Write-Host "Starting: az login --use-device-code" -ForegroundColor DarkGray
        az login --use-device-code --allow-no-subscriptions | Out-Null
    } else {
        Write-Host "Starting: az login (browser popup)" -ForegroundColor DarkGray
        az login --allow-no-subscriptions | Out-Null
    }

    $token = (az account get-access-token --resource-type ms-graph --query accessToken -o tsv)
    if (-not $token) {
        throw 'Failed to acquire Graph access token via Azure CLI.'
    }

    return $token
}

function Invoke-GraphGet {
    param(
        [Parameter(Mandatory = $true)]
        [string]$AccessToken,
        [Parameter(Mandatory = $true)]
        [string]$Url
    )

    $headers = @{ Authorization = "Bearer $AccessToken" }
    return Invoke-RestMethod -Method GET -Uri $Url -Headers $headers -ErrorAction Stop
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
    Write-Host "Tenant discovery" -ForegroundColor Cyan

    $isWindowsPowerShell = ($PSVersionTable.PSEdition -eq 'Desktop')
    $provider = $LoginProvider
    if ($provider -eq 'Auto') {
        $provider = if ($isWindowsPowerShell) { 'AzureCli' } else { 'GraphModule' }
    }

    $tenantId = $null
    $account = $null
    $orgName = $null
    $orgId = $null
    $domains = @()

    if ($provider -eq 'AzureCli') {
        Write-Host "Logging into Microsoft 365 using Azure CLI ($Auth)..." -ForegroundColor Cyan
        $token = Connect-GraphViaAzureCli -Mode $Auth

        $acct = Invoke-RestMethod -Method GET -Uri 'https://management.azure.com/tenants?api-version=2020-01-01' -Headers @{ Authorization = "Bearer $(az account get-access-token --resource https://management.azure.com/ --query accessToken -o tsv)" } -ErrorAction SilentlyContinue
        $tenantId = (az account show --query tenantId -o tsv)
        $account = (az account show --query user.name -o tsv)

        $org = Invoke-GraphGet -AccessToken $token -Url 'https://graph.microsoft.com/v1.0/organization?$select=id,displayName'
        if ($org.value -and $org.value.Count -gt 0) {
            $orgName = $org.value[0].displayName
            $orgId = $org.value[0].id
        }

        $dom = Invoke-GraphGet -AccessToken $token -Url 'https://graph.microsoft.com/v1.0/domains?$select=id,isDefault,isVerified'
        $domains = @($dom.value)
    } else {
        Write-Host "Logging into Microsoft Graph module ($Auth)..." -ForegroundColor Cyan
        Connect-GraphInteractive -Mode $Auth

        $ctx = Get-MgContext
        $tenantId = $ctx.TenantId
        $account = $ctx.Account

        $org = Get-MgOrganization -ErrorAction Stop | Select-Object -First 1
        if ($org) {
            $orgName = $org.DisplayName
            $orgId = $org.Id
        }

        $domains = Get-MgDomain -All -ErrorAction Stop
        Disconnect-MgGraph | Out-Null
    }

    Write-Kv -Key 'TenantId' -Value $tenantId
    Write-Kv -Key 'Account' -Value $account
    Write-Kv -Key 'Organization' -Value $orgName
    Write-Kv -Key 'OrganizationId' -Value $orgId

    $defaultDomain = $domains | Where-Object { $_.IsDefault } | Select-Object -First 1
    $onMicrosoft = $domains | Where-Object { $_.Id -like '*.onmicrosoft.com' } | Select-Object -First 1

    Write-Host "" 
    Write-Host "Domains" -ForegroundColor Cyan
    Write-Kv -Key 'DefaultDomain' -Value $defaultDomain.Id
    Write-Kv -Key 'OnMicrosoftDomain' -Value $onMicrosoft.Id

    Write-Host "" 
    Write-Host "Recommended GitHub Actions secrets (m365-prod environment)" -ForegroundColor Cyan
    Write-Host ("AZURE_TENANT_ID={0}" -f $tenantId)
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

    exit 0
} catch {
    Write-Error $_
    try { Disconnect-MgGraph | Out-Null } catch { }
    try { Disconnect-ExchangeOnline -Confirm:$false | Out-Null } catch { }
    exit 1
}
