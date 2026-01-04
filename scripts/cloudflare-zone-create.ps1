[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Domain,

    [Parameter(Mandatory = $true)]
    [ValidateSet('FFC', 'CM')]
    [string]$Account,

    [Parameter()]
    [ValidateSet('full', 'partial')]
    [string]$ZoneType = 'full',

    [Parameter()]
    [switch]$JumpStart
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
        [Parameter()][hashtable]$Query,
        [Parameter()][object]$Body
    )

    $base = 'https://api.cloudflare.com/client/v4'
    $url = "$base$Uri"

    if ($Query -and $Query.Count -gt 0) {
        $qs = ($Query.Keys | ForEach-Object { "$($_)=$([uri]::EscapeDataString([string]$Query[$_]))" }) -join '&'
        if ($qs) { $url = "$url`?$qs" }
    }

    $headers = @{ Authorization = "Bearer $Token" }

    $payload = $null
    if ($null -ne $Body) {
        $payload = ($Body | ConvertTo-Json -Depth 10)
    }

    $irmParams = @{
        Method      = $Method
        Uri         = $url
        Headers     = $headers
        ErrorAction = 'Stop'
    }

    if ($null -ne $Body) {
        $irmParams.Body = $payload
        $irmParams.ContentType = 'application/json'
    }

    $resp = Invoke-RestMethod @irmParams
    if (-not $resp.success) {
        $msg = ($resp.errors | Select-Object -First 1 -ExpandProperty message -ErrorAction SilentlyContinue)
        if (-not $msg) { $msg = ($resp | ConvertTo-Json -Depth 6) }
        throw "Cloudflare API error: $msg"
    }

    return $resp
}

function Get-ZoneIfExists {
    param(
        [Parameter(Mandatory = $true)][string]$Domain,
        [Parameter(Mandatory = $true)][string]$Token
    )

    $resp = Invoke-CfApi -Method 'GET' -Uri '/zones' -Token $Token -Query @{ name = $Domain } -Body $null
    $zones = @($resp.result)
    if ($zones.Count -gt 0) {
        return $zones[0]
    }
    return $null
}

try {
    $Domain = $Domain.Trim().ToLowerInvariant()
    if ([string]::IsNullOrWhiteSpace($Domain)) { throw 'Domain is required.' }

    $selectedToken = Get-TokenForAccount -Account $Account

    # Safety: prevent accidental duplicate zones by checking the other token too (when available).
    $otherToken = $null
    if ($Account -eq 'FFC' -and $env:CLOUDFLARE_API_TOKEN_CM) { $otherToken = [string]$env:CLOUDFLARE_API_TOKEN_CM }
    if ($Account -eq 'CM' -and $env:CLOUDFLARE_API_TOKEN_FFC) { $otherToken = [string]$env:CLOUDFLARE_API_TOKEN_FFC }

    $zoneInSelected = Get-ZoneIfExists -Domain $Domain -Token $selectedToken
    if (-not $zoneInSelected -and $otherToken) {
        $zoneInOther = Get-ZoneIfExists -Domain $Domain -Token $otherToken
        if ($zoneInOther) {
            throw "Zone '$Domain' already exists in the other Cloudflare account. Refusing to create a duplicate zone."
        }
    }

    if ($zoneInSelected) {
        Write-Host 'Zone already exists.' -ForegroundColor Yellow
        Write-Host ("ZoneId: {0}" -f $zoneInSelected.id)
        if ($zoneInSelected.name_servers) {
            Write-Host 'NameServers:'
            $zoneInSelected.name_servers | ForEach-Object { Write-Host ("- {0}" -f $_) }
        }
        exit 0
    }

    $accounts = @( (Invoke-CfApi -Method 'GET' -Uri '/accounts' -Token $selectedToken -Body $null).result )
    if ($accounts.Count -lt 1) {
        throw 'Could not determine Cloudflare account from token (GET /accounts returned no results).'
    }
    if ($accounts.Count -gt 1) {
        $names = ($accounts | Select-Object -ExpandProperty name -ErrorAction SilentlyContinue)
        throw ("Token has access to multiple Cloudflare accounts; refusing to proceed. Accounts: {0}" -f ($names -join ', '))
    }

    $accountId = $accounts[0].id
    $accountName = $accounts[0].name

    Write-Host ("Creating zone '{0}' in account '{1}' ({2})..." -f $Domain, $accountName, $Account) -ForegroundColor Cyan

    $body = @{
        name       = $Domain
        type       = $ZoneType
        account    = @{ id = $accountId }
        jump_start = [bool]$JumpStart
    }

    $created = Invoke-CfApi -Method 'POST' -Uri '/zones' -Token $selectedToken -Body $body
    $zone = $created.result

    Write-Host 'Zone created.' -ForegroundColor Green
    Write-Host ("ZoneId: {0}" -f $zone.id)
    if ($zone.name_servers) {
        Write-Host 'NameServers:'
        $zone.name_servers | ForEach-Object { Write-Host ("- {0}" -f $_) }
    }

    exit 0
}
catch {
    Write-Error $_
    exit 1
}
