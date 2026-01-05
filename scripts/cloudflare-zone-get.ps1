[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Domain,

    [Parameter(Mandatory = $true)]
    [ValidateSet('FFC', 'CM')]
    [string]$Account
)

$ErrorActionPreference = 'Stop'

function Get-TokenForAccount {
    param(
        [Parameter(Mandatory = $true)][string]$Account
    )

    switch ($Account) {
        'FFC' {
            if (-not $env:CLOUDFLARE_API_TOKEN_FFC) { throw 'CLOUDFLARE_API_TOKEN_FFC is not set.' }
            return [string]$env:CLOUDFLARE_API_TOKEN_FFC
        }
        'CM' {
            if (-not $env:CLOUDFLARE_API_TOKEN_CM) { throw 'CLOUDFLARE_API_TOKEN_CM is not set.' }
            return [string]$env:CLOUDFLARE_API_TOKEN_CM
        }
        default {
            throw "Unsupported Account value: $Account"
        }
    }
}

function Invoke-CfApi {
    param(
        [Parameter(Mandatory = $true)][string]$Method,
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][string]$Token,
        [Parameter()][hashtable]$Query
    )

    $base = 'https://api.cloudflare.com/client/v4'
    $url = "$base$Uri"

    if ($Query -and $Query.Count -gt 0) {
        $qs = ($Query.Keys | ForEach-Object { "$($_)=$([uri]::EscapeDataString([string]$Query[$_]))" }) -join '&'
        if ($qs) { $url = "$url`?$qs" }
    }

    $headers = @{ Authorization = "Bearer $Token" }

    $resp = Invoke-RestMethod -Method $Method -Uri $url -Headers $headers -ErrorAction Stop
    if (-not $resp.success) {
        $msg = ($resp.errors | Select-Object -First 1 -ExpandProperty message -ErrorAction SilentlyContinue)
        if (-not $msg) { $msg = ($resp | ConvertTo-Json -Depth 6) }
        throw "Cloudflare API error: $msg"
    }

    return $resp
}

try {
    $d = $Domain.Trim().ToLowerInvariant().Trim('.')
    if ([string]::IsNullOrWhiteSpace($d)) { throw 'Domain is required.' }

    $token = Get-TokenForAccount -Account $Account
    $resp = Invoke-CfApi -Method 'GET' -Uri '/zones' -Token $token -Query @{ name = $d }
    $zones = @($resp.result)

    if ($zones.Count -lt 1) {
        [pscustomobject]@{
            domain      = $d
            account     = $Account
            exists      = $false
            zoneId      = $null
            nameServers = @()
        } | ConvertTo-Json -Depth 5
        exit 0
    }

    $z = $zones[0]
    [pscustomobject]@{
        domain      = $d
        account     = $Account
        exists      = $true
        zoneId      = $z.id
        nameServers = @($z.name_servers)
    } | ConvertTo-Json -Depth 5
    exit 0
}
catch {
    Write-Error $_
    exit 1
}
