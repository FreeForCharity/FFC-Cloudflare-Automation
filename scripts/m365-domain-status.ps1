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

    if ($Token) {
        $secure = ConvertTo-SecureString -String $Token -AsPlainText -Force
        Connect-MgGraph -AccessToken $secure | Out-Null
        return
    }

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

try {
    $tokenToUse = if ($AccessToken) { $AccessToken } else { $env:GRAPH_ACCESS_TOKEN }
    Connect-Graph -Mode $Auth -Tenant $TenantId -Token $tokenToUse

    $d = Get-MgDomain -DomainId $Domain -ErrorAction Stop

    Write-Host "Microsoft 365 domain status" -ForegroundColor Cyan
    Write-Kv -Key 'Domain' -Value $d.Id
    Write-Kv -Key 'IsVerified' -Value $d.IsVerified
    Write-Kv -Key 'IsDefault' -Value $d.IsDefault
    Write-Kv -Key 'IsAdminManaged' -Value $d.IsAdminManaged
    Write-Kv -Key 'SupportsEmail' -Value $d.SupportsEmail

    if ($ShowDnsRecords) {
        Write-Host "" 
        Write-Host "DNS records Microsoft provides for verification/service configuration" -ForegroundColor Cyan

        try {
            $verification = Get-MgDomainVerificationDnsRecord -DomainId $Domain -ErrorAction Stop
        } catch {
            $verification = @()
        }

        try {
            $service = Get-MgDomainServiceConfigurationRecord -DomainId $Domain -ErrorAction Stop
        } catch {
            $service = @()
        }

        if (($verification.Count -eq 0) -and ($service.Count -eq 0)) {
            Write-Host "No DNS record details returned by Graph for this domain (or missing permissions)." -ForegroundColor Yellow
        } else {
            if ($verification.Count -gt 0) {
                Write-Host "Verification records:" -ForegroundColor Gray
                $verification | Sort-Object RecordType, Label | ForEach-Object {
                    Write-Host ("- {0} {1} -> {2}" -f $_.RecordType, $_.Label, $_.Text)
                }
            }

            if ($service.Count -gt 0) {
                Write-Host "Service configuration records:" -ForegroundColor Gray
                $service | Sort-Object RecordType, Label | ForEach-Object {
                    Write-Host ("- {0} {1} -> {2}" -f $_.RecordType, $_.Label, $_.Text)
                }
            }
        }
    }

    Disconnect-MgGraph | Out-Null
    exit 0
} catch {
    Write-Error $_
    try { Disconnect-MgGraph | Out-Null } catch { }
    exit 1
}
