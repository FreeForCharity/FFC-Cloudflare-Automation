<#
.SYNOPSIS
    Export Zeffy campaigns / donation forms / events (GET /api/v1/campaigns). Read-only.

.DESCRIPTION
    Pages the Zeffy campaigns endpoint and writes a flattened CSV (title, type, status, fundraising
    target vs. current volume, public URL, event occurrences count). Optional filters:
    -CreatedAfter / -CreatedBefore. No writes are performed against Zeffy.
#>
[CmdletBinding()]
param(
    [Parameter()]
    [string]$ApiKey,

    [Parameter()]
    [string]$BaseUrl,

    [Parameter()]
    [datetime]$CreatedAfter,

    [Parameter()]
    [datetime]$CreatedBefore,

    [Parameter()]
    [string]$OutputFile = 'zeffy_campaigns.csv',

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
    if ($PSBoundParameters.ContainsKey('CreatedAfter')) { $query['created[gte]'] = [DateTimeOffset]::new($CreatedAfter.ToUniversalTime()).ToUnixTimeSeconds() }
    if ($PSBoundParameters.ContainsKey('CreatedBefore')) { $query['created[lte]'] = [DateTimeOffset]::new($CreatedBefore.ToUniversalTime()).ToUnixTimeSeconds() }

    $items = Get-ZeffyList -BaseUrl $base -ApiKey $key -Path '/api/v1/campaigns' -Query $query -PageSize $PageSize -MaxItems $MaxRows

    $rows = foreach ($c in $items) {
        [pscustomobject]@{
            id               = $c.id
            type             = $c.type
            category         = $c.category
            status           = $c.status
            title            = Get-ZeffyText $c.title
            created          = ConvertFrom-UnixSeconds $c.created
            updated          = ConvertFrom-UnixSeconds $c.updated
            url              = Get-ZeffyText $c.url
            currency         = $c.currency
            target_cents     = $c.target
            goal_cents       = $c.goal_amount
            volume_cents     = $c.volume
            volume           = if ($null -ne $c.volume) { [math]::Round(([double]$c.volume) / 100, 2) } else { $null }
            is_archived      = $c.is_archived
            start_date       = ConvertFrom-UnixSeconds $c.start_date
            end_date         = ConvertFrom-UnixSeconds $c.end_date
            occurrence_count = if ($c.occurrences) { @($c.occurrences).Count } else { 0 }
        }
    }
    $rows | Export-Csv -Path $OutputFile -NoTypeInformation -Encoding UTF8
    Write-Host "Exported Zeffy campaigns: $(@($rows).Count) -> $OutputFile"
}
catch {
    Write-Error $_
    exit 1
}
