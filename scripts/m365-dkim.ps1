[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [Parameter(Mandatory = $true)]
    [string]$Domain,

    [Parameter()]
    [switch]$Enable,

    [Parameter()]
    [switch]$CreateIfMissing,

    [Parameter()]
    [string]$Organization,

    [Parameter()]
    [switch]$DeviceCode
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

function Connect-Exchange {
    param(
        [Parameter()]
        [string]$Org,

        [Parameter()]
        [switch]$UseDeviceCode
    )

    Ensure-Module -Name 'ExchangeOnlineManagement'
    Import-Module ExchangeOnlineManagement -ErrorAction Stop

    $connectParams = @{}
    if ($Org) {
        $connectParams.Organization = $Org
    }
    if ($UseDeviceCode) {
        $connectParams.Device = $true
    }

    Connect-ExchangeOnline @connectParams -ShowBanner:$false | Out-Null
}

function Write-Section {
    param([string]$Title)
    Write-Host "" 
    Write-Host $Title -ForegroundColor Cyan
}

function Print-DkimCnameHints {
    param(
        [Parameter(Mandatory = $true)]
        [string]$D
    )

    Write-Host "Expected DKIM CNAME records (Cloudflare should have these):" -ForegroundColor Gray
    Write-Host "- selector1._domainkey.$D -> <provided-by-M365>"
    Write-Host "- selector2._domainkey.$D -> <provided-by-M365>"
    Write-Host "(Targets depend on tenant; script prints actual values when available.)" -ForegroundColor DarkGray
}

try {
    Connect-Exchange -Org $Organization -UseDeviceCode:$DeviceCode

    Write-Section "Microsoft 365 DKIM status"

    $config = $null
    try {
        $config = Get-DkimSigningConfig -Identity $Domain -ErrorAction Stop
    } catch {
        $config = $null
    }

    if (-not $config) {
        Write-Host "No DKIM signing config found for $Domain." -ForegroundColor Yellow

        if ($CreateIfMissing) {
            if ($PSCmdlet.ShouldProcess($Domain, 'Create DKIM signing config')) {
                New-DkimSigningConfig -DomainName $Domain -Enabled:$false | Out-Null
                Write-Host "Created DKIM signing config (disabled by default)." -ForegroundColor Green
                $config = Get-DkimSigningConfig -Identity $Domain -ErrorAction Stop
            }
        }
    }

    if ($config) {
        Write-Host ("Domain: {0}" -f $config.Domain)
        Write-Host ("Enabled: {0}" -f $config.Enabled)

        $s1 = $null
        $s2 = $null
        if ($config.PSObject.Properties.Name -contains 'Selector1CNAME') { $s1 = $config.Selector1CNAME }
        if ($config.PSObject.Properties.Name -contains 'Selector2CNAME') { $s2 = $config.Selector2CNAME }

        Write-Host "" 
        Write-Host "DKIM DNS records (from Exchange Online):" -ForegroundColor Gray
        if ($s1) {
            Write-Host ("- selector1._domainkey.{0} CNAME {1}" -f $Domain, $s1)
        } else {
            Write-Host "- selector1._domainkey.$Domain CNAME (not returned)" -ForegroundColor Yellow
        }

        if ($s2) {
            Write-Host ("- selector2._domainkey.{0} CNAME {1}" -f $Domain, $s2)
        } else {
            Write-Host "- selector2._domainkey.$Domain CNAME (not returned)" -ForegroundColor Yellow
        }

        if ($Enable -and (-not $config.Enabled)) {
            if ($PSCmdlet.ShouldProcess($Domain, 'Enable DKIM signing')) {
                Set-DkimSigningConfig -Identity $Domain -Enabled:$true | Out-Null
                Write-Host "Enabled DKIM signing." -ForegroundColor Green
            }
        }
    } else {
        Print-DkimCnameHints -D $Domain
        if (-not $CreateIfMissing) {
            Write-Host "Run again with -CreateIfMissing to create DKIM config." -ForegroundColor DarkGray
        }
    }

    Disconnect-ExchangeOnline -Confirm:$false | Out-Null
    exit 0
} catch {
    Write-Error $_
    try { Disconnect-ExchangeOnline -Confirm:$false | Out-Null } catch { }
    exit 1
}
