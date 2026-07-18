<#
.SYNOPSIS
    Export the WHMCS support departments (GetSupportDepartments). Read-only.

.DESCRIPTION
    Prints a readable catalog of departments (id, name, ticket counts) to the
    job log for discovery, and writes a CSV. The department ids are needed to
    open tickets (whmcs-ticket-open.ps1) in the right queue.
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
    [string]$OutputFile = 'whmcs_support_departments.csv',

    # Include per-status ticket counts (ignore_dept_assignments=true).
    [Parameter()]
    [switch]$IgnoreDeptAssignments
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

function Get-DepartmentsFromResponse {
    param([Parameter(Mandatory = $true)]$Response)
    if ($Response.departments -and $Response.departments.department) {
        return @($Response.departments.department)
    }
    if ($Response.departments -is [System.Array]) {
        return @($Response.departments)
    }
    return @()
}

try {
    $api = Resolve-WhmcsApiUrl -ApiUrlParam $ApiUrl
    $creds = Resolve-WhmcsCredentials -IdentifierParam $Identifier -SecretParam $Secret -CredentialsJsonParam $CredentialsJson
    $accessKey = Resolve-WhmcsAccessKey -AccessKeyParam $AccessKey

    New-DirectoryForFile -Path $OutputFile

    $body = @{
        identifier   = $creds.Identifier
        secret       = $creds.Secret
        action       = 'GetSupportDepartments'
        responsetype = 'json'
    }
    if (-not [string]::IsNullOrWhiteSpace($accessKey)) { $body.accesskey = $accessKey }
    if ($IgnoreDeptAssignments) { $body.ignore_dept_assignments = $true }

    $resp = Invoke-WhmcsApi -ApiUrl $api -Body $body
    $depts = Get-DepartmentsFromResponse -Response $resp

    $rows = foreach ($d in $depts) {
        [pscustomobject]@{
            id            = $d.id
            name          = $d.name
            awaitingreply = $d.awaitingreply
            opentickets   = $d.opentickets
        }
    }
    $rows | Export-Csv -Path $OutputFile -NoTypeInformation -Encoding UTF8

    Write-Host ''
    Write-Host '============ WHMCS SUPPORT DEPARTMENTS ============'
    foreach ($r in $rows) {
        Write-Host ("deptid={0,-4} awaiting={1,-4} open={2,-5} {3}" -f $r.id, $r.awaitingreply, $r.opentickets, $r.name)
    }
    Write-Host '==================================================='
    Write-Host ''
    Write-Host "Exported departments: $(@($rows).Count) -> $OutputFile"
}
catch {
    Write-Error $_
    exit 1
}
