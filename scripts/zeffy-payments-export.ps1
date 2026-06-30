<#
.SYNOPSIS
    Export Zeffy payments (GET /api/v1/payments). Read-only.

.DESCRIPTION
    Pages the Zeffy payments endpoint and writes a flattened CSV. Optional filters mirror the API:
    -Status, -Type, -Currency, -CampaignId, -ContactId, and a -CreatedAfter / -CreatedBefore date
    window. Amounts are reported in cents (as the API returns them) plus a dollars convenience
    column. No writes are performed against Zeffy.
#>
[CmdletBinding()]
param(
    [Parameter()]
    [string]$ApiKey,

    [Parameter()]
    [string]$BaseUrl,

    [Parameter()]
    [ValidateSet('succeeded', 'failed', 'pending')]
    [string]$Status,

    [Parameter()]
    [ValidateSet('online', 'manual', 'imported')]
    [string]$Type,

    [Parameter()]
    [ValidateSet('cad', 'usd')]
    [string]$Currency,

    [Parameter()]
    [string]$CampaignId,

    [Parameter()]
    [string]$ContactId,

    [Parameter()]
    [datetime]$CreatedAfter,

    [Parameter()]
    [datetime]$CreatedBefore,

    [Parameter()]
    [string]$OutputFile = 'zeffy_payments.csv',

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
    if ($Status) { $query['status'] = $Status }
    if ($Type) { $query['type'] = $Type }
    if ($Currency) { $query['currency'] = $Currency }
    if ($CampaignId) { $query['campaign'] = $CampaignId }
    if ($ContactId) { $query['contact'] = $ContactId }
    if ($PSBoundParameters.ContainsKey('CreatedAfter')) { $query['created[gte]'] = [DateTimeOffset]::new($CreatedAfter.ToUniversalTime()).ToUnixTimeSeconds() }
    if ($PSBoundParameters.ContainsKey('CreatedBefore')) { $query['created[lte]'] = [DateTimeOffset]::new($CreatedBefore.ToUniversalTime()).ToUnixTimeSeconds() }

    $items = Get-ZeffyList -BaseUrl $base -ApiKey $key -Path '/api/v1/payments' -Query $query -PageSize $PageSize -MaxItems $MaxRows

    $rows = foreach ($p in $items) {
        $amountCents = 0
        [void][int]::TryParse([string]$p.amount, [ref]$amountCents)
        [pscustomobject]@{
            id               = $p.id
            created          = ConvertFrom-UnixSeconds $p.created
            amount_cents     = $p.amount
            amount           = if ($null -ne $p.amount) { [math]::Round(([double]$p.amount) / 100, 2) } else { $null }
            eligible_cents   = $p.eligible_amount
            currency         = $p.currency
            status           = $p.status
            type             = $p.type
            refund_status    = $p.refund_status
            campaign_id      = $p.campaign_id
            campaign_type    = $p.campaign_type
            campaign_cat     = $p.campaign_category
            description      = Get-ZeffyText $p.description
            contact_id       = Get-ZeffyText $p.contact
            buyer_email      = if ($p.buyer) { Get-ZeffyText $p.buyer.email } else { $null }
            buyer_first      = if ($p.buyer) { Get-ZeffyText $p.buyer.first_name } else { $null }
            buyer_last       = if ($p.buyer) { Get-ZeffyText $p.buyer.last_name } else { $null }
            buyer_company    = if ($p.buyer) { Get-ZeffyText $p.buyer.company_name } else { $null }
            is_recurring     = if ($p.recurring) { $p.recurring.is_recurring } else { $false }
            recur_interval   = if ($p.recurring) { Get-ZeffyText $p.recurring.interval } else { $null }
            payment_method   = if ($p.payment_method) { Get-ZeffyText $p.payment_method.type } else { $null }
            receipt_url      = Get-ZeffyText $p.receipt_url
            item_count       = if ($p.items) { @($p.items).Count } else { 0 }
        }
    }
    $rows | Export-Csv -Path $OutputFile -NoTypeInformation -Encoding UTF8
    Write-Host "Exported Zeffy payments: $(@($rows).Count) -> $OutputFile"
}
catch {
    Write-Error $_
    exit 1
}
