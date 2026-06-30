<#
.SYNOPSIS
    Export Zeffy contacts/donors (GET /api/v1/contacts). Read-only.

.DESCRIPTION
    Pages the Zeffy contacts endpoint and writes a flattened CSV (donor profile + lifetime giving
    stats + structured address). Optional filters: -Email (exact match), -CreatedAfter /
    -CreatedBefore, -UpdatedAfter / -UpdatedBefore. No writes are performed against Zeffy.
#>
[CmdletBinding()]
param(
    [Parameter()]
    [string]$ApiKey,

    [Parameter()]
    [string]$BaseUrl,

    [Parameter()]
    [string]$Email,

    [Parameter()]
    [datetime]$CreatedAfter,

    [Parameter()]
    [datetime]$CreatedBefore,

    [Parameter()]
    [datetime]$UpdatedAfter,

    [Parameter()]
    [datetime]$UpdatedBefore,

    [Parameter()]
    [string]$OutputFile = 'zeffy_contacts.csv',

    [Parameter()]
    [ValidateRange(1, 100)]
    [int]$PageSize = 100,

    [Parameter()]
    [ValidateRange(1, 1000000)]
    [int]$MaxRows = 100000
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'zeffy-api-common.ps1')

function New-DirectoryForFile {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return }
    $dir = Split-Path -Parent $Path
    if (-not [string]::IsNullOrWhiteSpace($dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
}

try {
    $key = Resolve-ZeffyApiKey -ApiKeyParam $ApiKey
    $base = Resolve-ZeffyBaseUrl -BaseUrlParam $BaseUrl
    New-DirectoryForFile -Path $OutputFile

    $query = @{}
    if ($Email) { $query['email'] = $Email }
    if ($PSBoundParameters.ContainsKey('CreatedAfter')) { $query['created[gte]'] = [DateTimeOffset]::new($CreatedAfter.ToUniversalTime()).ToUnixTimeSeconds() }
    if ($PSBoundParameters.ContainsKey('CreatedBefore')) { $query['created[lte]'] = [DateTimeOffset]::new($CreatedBefore.ToUniversalTime()).ToUnixTimeSeconds() }
    if ($PSBoundParameters.ContainsKey('UpdatedAfter')) { $query['updated[gte]'] = [DateTimeOffset]::new($UpdatedAfter.ToUniversalTime()).ToUnixTimeSeconds() }
    if ($PSBoundParameters.ContainsKey('UpdatedBefore')) { $query['updated[lte]'] = [DateTimeOffset]::new($UpdatedBefore.ToUniversalTime()).ToUnixTimeSeconds() }

    $items = Get-ZeffyList -BaseUrl $base -ApiKey $key -Path '/api/v1/contacts' -Query $query -PageSize $PageSize -MaxItems $MaxRows

    $rows = foreach ($c in $items) {
        $addr = $c.address
        [pscustomobject]@{
            id                  = $c.id
            created             = ConvertFrom-UnixSeconds $c.created
            updated             = ConvertFrom-UnixSeconds $c.updated
            email               = Get-ZeffyText $c.email
            first_name          = Get-ZeffyText $c.first_name
            last_name           = Get-ZeffyText $c.last_name
            phone_number        = Get-ZeffyText $c.phone_number
            donor_type          = Get-ZeffyText $c.donor_type
            total_contrib_cents = $c.total_contribution
            total_contrib       = if ($null -ne $c.total_contribution) { [math]::Round(([double]$c.total_contribution) / 100, 2) } else { $null }
            currency            = $c.currency
            donation_count      = $c.donation_count
            first_donation      = ConvertFrom-UnixSeconds $c.first_donation_date
            last_donation       = ConvertFrom-UnixSeconds $c.last_donation_date
            city                = if ($addr) { Get-ZeffyText $addr.city } else { $null }
            state               = if ($addr) { Get-ZeffyText $addr.state } else { $null }
            postal_code         = if ($addr) { Get-ZeffyText $addr.postal_code } else { $null }
            country             = if ($addr) { Get-ZeffyText $addr.country } else { $null }
        }
    }
    $rows | Export-Csv -Path $OutputFile -NoTypeInformation -Encoding UTF8
    Write-Host "Exported Zeffy contacts: $(@($rows).Count) -> $OutputFile"
}
catch {
    Write-Error $_
    exit 1
}
