<#
.SYNOPSIS
    Export WHMCS support tickets (GetTickets). Read-only.

.DESCRIPTION
    Pages through GetTickets and writes a CSV (id, tid, dept, status, priority,
    subject, requester, lastreply). Optional filters: -Status, -DeptId,
    -ClientId, -Email, -Subject.
#>
[CmdletBinding()]
param(
    [Parameter()]
    [string]$ApiUrl,

    [Parameter()]
    [string]$Identifier,

    [Parameter()]
    [string]$Secret,

    [Parameter()]
    [string]$CredentialsJson,

    [Parameter()]
    [string]$AccessKey,

    [Parameter()]
    [string]$Status,

    [Parameter()]
    [int]$DeptId,

    [Parameter()]
    [int]$ClientId,

    [Parameter()]
    [string]$Email,

    [Parameter()]
    [string]$Subject,

    [Parameter()]
    [string]$OutputFile = 'whmcs_tickets.csv',

    [Parameter()]
    [ValidateRange(1, 250)]
    [int]$PageSize = 250
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'whmcs-api-common.ps1')

function New-DirectoryForFile {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return }
    $dir = Split-Path -Parent $Path
    if (-not [string]::IsNullOrWhiteSpace($dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
}

function Get-TicketsFromResponse {
    param([Parameter(Mandatory = $true)]$Response)
    if ($Response.tickets -and $Response.tickets.ticket) {
        return @($Response.tickets.ticket)
    }
    if ($Response.tickets -is [System.Array]) {
        return @($Response.tickets)
    }
    return @()
}

try {
    $api = Resolve-WhmcsApiUrl -ApiUrlParam $ApiUrl
    $creds = Resolve-WhmcsCredentials -IdentifierParam $Identifier -SecretParam $Secret -CredentialsJsonParam $CredentialsJson
    $accessKey = Resolve-WhmcsAccessKey -AccessKeyParam $AccessKey

    New-DirectoryForFile -Path $OutputFile

    $all = @()
    $start = 0
    while ($true) {
        $body = @{
            identifier   = $creds.Identifier
            secret       = $creds.Secret
            action       = 'GetTickets'
            responsetype = 'json'
            limitstart   = $start
            limitnum     = $PageSize
        }
        if (-not [string]::IsNullOrWhiteSpace($accessKey)) { $body.accesskey = $accessKey }
        if ($Status) { $body.status = $Status }
        if ($PSBoundParameters.ContainsKey('DeptId')) { $body.deptid = $DeptId }
        if ($PSBoundParameters.ContainsKey('ClientId')) { $body.clientid = $ClientId }
        if ($Email) { $body.email = $Email }
        if ($Subject) { $body.subject = $Subject }

        $resp = Invoke-WhmcsApi -ApiUrl $api -Body $body
        $page = Get-TicketsFromResponse -Response $resp
        if ($page.Count -le 0) { break }

        $all += $page
        $start += $page.Count

        $total = 0
        if ($resp.totalresults) { [void][int]::TryParse($resp.totalresults.ToString(), [ref]$total) }
        if ($total -gt 0 -and $start -ge $total) { break }
    }

    $rows = foreach ($t in $all) {
        [pscustomobject]@{
            id        = $t.id
            tid       = $t.tid
            deptid    = $t.deptid
            deptname  = $t.deptname
            status    = $t.status
            priority  = $t.priority
            subject   = $t.subject
            name      = $t.name
            email     = $t.email
            lastreply = $t.lastreply
        }
    }
    $rows | Export-Csv -Path $OutputFile -NoTypeInformation -Encoding UTF8
    Write-Host "Exported tickets: $(@($rows).Count) -> $OutputFile"
}
catch {
    Write-Error $_
    exit 1
}
