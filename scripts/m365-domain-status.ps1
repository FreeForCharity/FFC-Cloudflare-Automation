[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Domain,

    [Parameter()]
    [ValidateSet('Interactive', 'DeviceCode')]
    [string]$Auth = 'Interactive',

    [Parameter()]
    [string]$AccessToken,

    [Parameter()]
    [string]$TenantId,

    [Parameter()]
    [switch]$ShowDnsRecords
)

$ErrorActionPreference = 'Stop'

$useRest = $false

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
    }
    catch {
        # Best-effort; module install may still succeed.
    }

    try {
        Set-PSRepository -Name PSGallery -InstallationPolicy Trusted -ErrorAction SilentlyContinue | Out-Null
    }
    catch {
        # Best-effort; if this fails, Install-Module may prompt.
    }

    if (-not (Get-Module -ListAvailable -Name $Name)) {
        Write-Host "Installing PowerShell module: $Name" -ForegroundColor Yellow
        Install-Module -Name $Name -Scope CurrentUser -Force -AllowClobber
    }
}

function Invoke-GraphRequest {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Uri,

        [Parameter(Mandatory = $true)]
        [string]$AccessToken
    )

    $headers = @{ Authorization = "Bearer $AccessToken" }
    Invoke-RestMethod -Method Get -Uri $Uri -Headers $headers -ErrorAction Stop
}

function Connect-Graph {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('Interactive', 'DeviceCode')]
        [string]$Mode,

        [Parameter()]
        [string]$Tenant,

        [Parameter()]
        [string]$Token
    )

    Ensure-Module -Name 'Microsoft.Graph'

    Import-Module Microsoft.Graph -ErrorAction Stop

    $scopes = @('Domain.Read.All')
    $connectParams = @{ Scopes = $scopes }
    if ($Tenant) { $connectParams.TenantId = $Tenant }

    if ($Mode -eq 'DeviceCode') { Connect-MgGraph @connectParams -UseDeviceCode | Out-Null }
    else { Connect-MgGraph @connectParams | Out-Null }
}

function Write-Kv {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Key,
        [Parameter(Mandatory = $true)]
        [AllowNull()]
        [object]$Value
    )

    $v = if ($null -eq $Value) { '' } else { [string]$Value }
    Write-Host ("{0}: {1}" -f $Key, $v)
}

function Get-DnsRecordText {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Record
    )

    if ($Record.PSObject.Properties.Name -contains 'Text' -and $Record.Text) { return [string]$Record.Text }
    if ($Record.PSObject.Properties.Name -contains 'text' -and $Record.text) { return [string]$Record.text }

    if ($Record.PSObject.Properties.Name -contains 'mailExchange' -and $Record.mailExchange) {
        $pref = if ($Record.PSObject.Properties.Name -contains 'preference') { $Record.preference } else { $null }
        return if ($null -ne $pref) { "{0} (pref {1})" -f $Record.mailExchange, $pref } else { [string]$Record.mailExchange }
    }

    if ($Record.PSObject.Properties.Name -contains 'canonicalName' -and $Record.canonicalName) { return [string]$Record.canonicalName }
    if ($Record.PSObject.Properties.Name -contains 'ipAddress' -and $Record.ipAddress) { return [string]$Record.ipAddress }
    if ($Record.PSObject.Properties.Name -contains 'ipv6Address' -and $Record.ipv6Address) { return [string]$Record.ipv6Address }

    return ($Record | ConvertTo-Json -Compress -Depth 6)
}

try {
    $tokenToUse = if ($AccessToken) { $AccessToken } else { $env:GRAPH_ACCESS_TOKEN }
    $useRest = -not [string]::IsNullOrWhiteSpace($tokenToUse)

    if (-not $useRest) {
        Connect-Graph -Mode $Auth -Tenant $TenantId
    }

    if ($useRest) {
        $encodedDomain = [Uri]::EscapeDataString($Domain)
        $d = Invoke-GraphRequest -Uri ("https://graph.microsoft.com/v1.0/domains/{0}?$select=id,isVerified,isDefault,isAdminManaged,supportedServices" -f $encodedDomain) -AccessToken $tokenToUse
    }
    else {
        $d = Get-MgDomain -DomainId $Domain -ErrorAction Stop
    }

    Write-Host "Microsoft 365 domain status" -ForegroundColor Cyan
    Write-Kv -Key 'Domain' -Value $d.Id
    Write-Kv -Key 'IsVerified' -Value $d.IsVerified
    Write-Kv -Key 'IsDefault' -Value $d.IsDefault
    Write-Kv -Key 'IsAdminManaged' -Value $d.IsAdminManaged
    $supportsEmail = if ($useRest) { @($d.supportedServices) -contains 'Email' } else { $d.SupportsEmail }
    Write-Kv -Key 'SupportsEmail' -Value $supportsEmail

    if ($ShowDnsRecords) {
        Write-Host "" 
        Write-Host "DNS records Microsoft provides for verification/service configuration" -ForegroundColor Cyan

        if ($useRest) {
            try {
                $verification = (Invoke-GraphRequest -Uri ("https://graph.microsoft.com/v1.0/domains/{0}/verificationDnsRecords" -f $encodedDomain) -AccessToken $tokenToUse).value
            }
            catch {
                $verification = @()
            }

            try {
                $service = (Invoke-GraphRequest -Uri ("https://graph.microsoft.com/v1.0/domains/{0}/serviceConfigurationRecords" -f $encodedDomain) -AccessToken $tokenToUse).value
            }
            catch {
                $service = @()
            }
        }
        else {
            try {
                $verification = Get-MgDomainVerificationDnsRecord -DomainId $Domain -ErrorAction Stop
            }
            catch {
                $verification = @()
            }

            try {
                $service = Get-MgDomainServiceConfigurationRecord -DomainId $Domain -ErrorAction Stop
            }
            catch {
                $service = @()
            }
        }

        if (($verification.Count -eq 0) -and ($service.Count -eq 0)) {
            Write-Host "No DNS record details returned by Graph for this domain (or missing permissions)." -ForegroundColor Yellow
        }
        else {
            if ($verification.Count -gt 0) {
                Write-Host "Verification records:" -ForegroundColor Gray
                if ($useRest) {
                    $verification | Sort-Object recordType, label | ForEach-Object {
                        $target = Get-DnsRecordText -Record $_
                        Write-Host ("- {0} {1} -> {2}" -f $_.recordType, $_.label, $target)
                    }
                }
                else {
                    $verification | Sort-Object RecordType, Label | ForEach-Object {
                        Write-Host ("- {0} {1} -> {2}" -f $_.RecordType, $_.Label, $_.Text)
                    }
                }
            }

            if ($service.Count -gt 0) {
                Write-Host "Service configuration records:" -ForegroundColor Gray
                if ($useRest) {
                    $service | Sort-Object recordType, label | ForEach-Object {
                        $target = Get-DnsRecordText -Record $_
                        Write-Host ("- {0} {1} -> {2}" -f $_.recordType, $_.label, $target)
                    }
                }
                else {
                    $service | Sort-Object RecordType, Label | ForEach-Object {
                        Write-Host ("- {0} {1} -> {2}" -f $_.RecordType, $_.Label, $_.Text)
                    }
                }
            }
        }
    }

    if (-not $useRest) { Disconnect-MgGraph | Out-Null }
    exit 0
}
catch {
    Write-Error $_
    try { if (-not $useRest) { Disconnect-MgGraph | Out-Null } } catch { }
    exit 1
}
