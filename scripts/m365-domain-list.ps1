[CmdletBinding()]
param(
    [Parameter()]
    [string]$AccessToken
)

$ErrorActionPreference = 'Stop'

function Invoke-GraphGet {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Uri,

        [Parameter(Mandatory = $true)]
        [string]$Token
    )

    $headers = @{ Authorization = "Bearer $Token" }
    Invoke-RestMethod -Method Get -Uri $Uri -Headers $headers -ErrorAction Stop
}

try {
    $tokenToUse = if ($AccessToken) { $AccessToken } else { $env:GRAPH_ACCESS_TOKEN }

    if ([string]::IsNullOrWhiteSpace($tokenToUse)) {
        $az = Get-Command az -ErrorAction SilentlyContinue
        if (-not $az) {
            throw 'No AccessToken provided and GRAPH_ACCESS_TOKEN is not set. Install Azure CLI (az) or pass -AccessToken.'
        }

        $tokenToUse = (az account get-access-token --resource-type ms-graph --query accessToken -o tsv)
    }

    if ([string]::IsNullOrWhiteSpace($tokenToUse)) {
        throw 'Failed to acquire a Microsoft Graph access token.'
    }

    $uri = 'https://graph.microsoft.com/v1.0/domains?$select=id,isDefault,isVerified,supportedServices&$top=999'
    $resp = Invoke-GraphGet -Uri $uri -Token $tokenToUse

    $domains = @($resp.value)

    'Domain\tIsDefault\tIsVerified\tSupportsEmail'
    $domains |
        Sort-Object @{ Expression = 'isDefault'; Descending = $true }, @{ Expression = 'id'; Descending = $false } |
        ForEach-Object {
            $supportsEmail = @($_.supportedServices) -contains 'Email'
            "{0}\t{1}\t{2}\t{3}" -f $_.id, $_.isDefault, $_.isVerified, $supportsEmail
        }

    exit 0
} catch {
    Write-Error $_
    exit 1
}
