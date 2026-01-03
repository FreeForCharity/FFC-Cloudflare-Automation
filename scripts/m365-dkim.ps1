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
    [string]$AppId,

    [Parameter()]
    [string]$TenantId,

    [Parameter()]
    [string]$CertificateThumbprint,

    [Parameter()]
    [string]$CertificatePfxBase64,

    [Parameter()]
    [string]$CertificatePassword,

    [Parameter()]
    [switch]$DeviceCode
)

$ErrorActionPreference = 'Stop'

$script:TempPfxPath = $null

function Get-FirstNonEmpty {
    param(
        [Parameter(Mandatory = $true)]
        [AllowNull()]
        [object[]]$Values
    )

    foreach ($v in $Values) {
        $s = if ($null -eq $v) { '' } else { [string]$v }
        if (-not [string]::IsNullOrWhiteSpace($s)) {
            return $s
        }
    }

    return $null
}

function Set-GitHubOutput {
    param(
        [Parameter(Mandatory = $true)][string]$Key,
        [Parameter()][AllowNull()][object]$Value
    )

    $outFile = $env:GITHUB_OUTPUT
    if ([string]::IsNullOrWhiteSpace($outFile)) {
        return
    }

    $val = if ($null -eq $Value) { '' } else { [string]$Value }
    Add-Content -Path $outFile -Value ("{0}={1}" -f $Key, $val)
}

function New-TempPfxFile {
    param(
        [Parameter(Mandatory = $true)][string]$PfxBase64
    )

    $bytes = [Convert]::FromBase64String($PfxBase64)
    $path = [System.IO.Path]::Combine([System.IO.Path]::GetTempPath(), ("exo-auth-{0}.pfx" -f ([guid]::NewGuid().ToString('n'))))
    [System.IO.File]::WriteAllBytes($path, $bytes)
    return $path
}

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

function Connect-Exchange {
    param(
        [Parameter()]
        [string]$Org,

        [Parameter()]
        [string]$App,

        [Parameter()]
        [string]$Thumbprint,

        [Parameter()]
        [string]$PfxBase64,

        [Parameter()]
        [string]$PfxPassword,

        [Parameter()]
        [switch]$UseDeviceCode
    )

    Ensure-Module -Name 'ExchangeOnlineManagement'
    Import-Module ExchangeOnlineManagement -ErrorAction Stop

    $connectParams = @{ ShowBanner = $false }
    if ($Org) { $connectParams.Organization = $Org }

    if ($App -and $Org -and ($Thumbprint -or $PfxBase64)) {
        $connectParams.AppId = $App
        $connectParams.Organization = $Org

        if ($Thumbprint) {
            $connectParams.CertificateThumbprint = $Thumbprint
            Connect-ExchangeOnline @connectParams | Out-Null
            return
        }

        $script:TempPfxPath = New-TempPfxFile -PfxBase64 $PfxBase64
        $connectParams.CertificateFilePath = $script:TempPfxPath
        if ($PfxPassword) {
            $connectParams.CertificatePassword = (ConvertTo-SecureString -String $PfxPassword -AsPlainText -Force)
        }

        Connect-ExchangeOnline @connectParams | Out-Null
        return
    }

    if ($UseDeviceCode) { $connectParams.Device = $true }
    Connect-ExchangeOnline @connectParams | Out-Null
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
    $effectiveOrg = if ($Organization) { $Organization } else { $env:EXO_ORGANIZATION }
    $effectiveAppId = if ($AppId) { $AppId } else { Get-FirstNonEmpty @($env:EXO_APP_ID, $env:AZURE_CLIENT_ID) }
    $effectiveTenant = if ($TenantId) { $TenantId } else { $env:EXO_TENANT }
    $effectiveThumb = if ($CertificateThumbprint) { $CertificateThumbprint } else { $env:EXO_CERT_THUMBPRINT }

    $effectivePfx = if ($CertificatePfxBase64) { $CertificatePfxBase64 } else { Get-FirstNonEmpty @($env:EXO_CERT_PFX_BASE64, $env:FFC_EXO_CERT_PFX_BASE64) }
    $effectivePfxPwd = if ($CertificatePassword) { $CertificatePassword } else { Get-FirstNonEmpty @($env:EXO_CERT_PFX_PASSWORD, $env:FFC_EXO_CERT_PASSWORD) }

    if (-not $effectiveOrg -and $effectiveTenant) {
        $effectiveOrg = $effectiveTenant
    }

    Connect-Exchange -Org $effectiveOrg -App $effectiveAppId -Thumbprint $effectiveThumb -PfxBase64 $effectivePfx -PfxPassword $effectivePfxPwd -UseDeviceCode:$DeviceCode

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

        Set-GitHubOutput -Key 'dkim_enabled' -Value $config.Enabled
        Set-GitHubOutput -Key 'selector1_name' -Value ("selector1._domainkey.{0}" -f $Domain)
        Set-GitHubOutput -Key 'selector2_name' -Value ("selector2._domainkey.{0}" -f $Domain)
        Set-GitHubOutput -Key 'selector1_target' -Value $s1
        Set-GitHubOutput -Key 'selector2_target' -Value $s2

        if ($Enable -and (-not $config.Enabled)) {
            if ($PSCmdlet.ShouldProcess($Domain, 'Enable DKIM signing')) {
                Set-DkimSigningConfig -Identity $Domain -Enabled:$true | Out-Null
                Write-Host "Enabled DKIM signing." -ForegroundColor Green

                $post = Get-DkimSigningConfig -Identity $Domain -ErrorAction Stop
                Set-GitHubOutput -Key 'dkim_enabled_after' -Value $post.Enabled
            }
        }
    } else {
        Print-DkimCnameHints -D $Domain
        if (-not $CreateIfMissing) {
            Write-Host "Run again with -CreateIfMissing to create DKIM config." -ForegroundColor DarkGray
        }
    }

    Disconnect-ExchangeOnline -Confirm:$false | Out-Null
    if ($script:TempPfxPath) {
        Remove-Item -Path $script:TempPfxPath -Force -ErrorAction SilentlyContinue
        $script:TempPfxPath = $null
    }
    exit 0
} catch {
    Write-Error $_
    try { Disconnect-ExchangeOnline -Confirm:$false | Out-Null } catch { }
    if ($script:TempPfxPath) {
        Remove-Item -Path $script:TempPfxPath -Force -ErrorAction SilentlyContinue
        $script:TempPfxPath = $null
    }
    exit 1
}
