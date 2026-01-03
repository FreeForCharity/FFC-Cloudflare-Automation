[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Domain
)

$ErrorActionPreference = 'Stop'

$domainName = $Domain.Trim().ToLowerInvariant()
if ([string]::IsNullOrWhiteSpace($domainName)) { throw 'Domain cannot be empty.' }

$token = $env:GRAPH_ACCESS_TOKEN
if ([string]::IsNullOrWhiteSpace($token)) {
    throw 'GRAPH_ACCESS_TOKEN is not set. Acquire a Graph token first (see workflows that use azure/login + az account get-access-token).'
}

$headers = @{
    Authorization  = "Bearer $token"
    'Content-Type' = 'application/json'
}

$baseUri = 'https://graph.microsoft.com/v1.0'

function Invoke-Graph {
    param(
        [Parameter(Mandatory = $true)][ValidateSet('GET', 'POST')][string]$Method,
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter()][object]$Body
    )

    $uri = "$baseUri$Path"
    if ($null -ne $Body) {
        $json = $Body | ConvertTo-Json -Depth 6
        return Invoke-RestMethod -Method $Method -Uri $uri -Headers $headers -Body $json
    }
    return Invoke-RestMethod -Method $Method -Uri $uri -Headers $headers
}

Write-Host "Adding domain to tenant: $domainName"

$created = $false
try {
    $createBody = @{ id = $domainName }
    $null = Invoke-Graph -Method POST -Path '/domains' -Body $createBody
    $created = $true
    Write-Host 'Domain created.'
}
catch {
    Write-Host 'Create domain failed (may already exist). Attempting to fetch domain...' -ForegroundColor Yellow
}

$domain = Invoke-Graph -Method GET -Path ("/domains/$domainName")

Write-Host ''
Write-Host 'Domain status:' -ForegroundColor Cyan
Write-Host ("- id: {0}" -f $domain.id)
Write-Host ("- isDefault: {0}" -f $domain.isDefault)
Write-Host ("- isVerified: {0}" -f $domain.isVerified)
Write-Host ("- authenticationType: {0}" -f $domain.authenticationType)

Write-Host ''
Write-Host 'Verification DNS records (required to verify ownership):' -ForegroundColor Cyan

$verification = Invoke-Graph -Method GET -Path ("/domains/$domainName/verificationDnsRecords")
if ($verification.value -and $verification.value.Count -gt 0) {
    foreach ($rec in $verification.value) {
        $label = $rec.label
        $rtype = $rec.recordType
        $ttl = $rec.ttl

        $supportedService = $rec.supportedService
        $isOptional = $rec.isOptional

        $text = $null
        if ($rec.PSObject.Properties.Name -contains 'text') { $text = $rec.text }

        Write-Host ("- {0} {1} TTL={2} Optional={3} Service={4}" -f $label, $rtype, $ttl, $isOptional, $supportedService)
        if ($text) {
            Write-Host ("  Value: {0}" -f $text)
        }
    }
}
else {
    Write-Host '(No verification records returned.)' -ForegroundColor Yellow
}

Write-Host ''
Write-Host 'Service configuration DNS records (recommended for email/services):' -ForegroundColor Cyan
try {
    $svc = Invoke-Graph -Method GET -Path ("/domains/$domainName/serviceConfigurationRecords")
    if ($svc.value -and $svc.value.Count -gt 0) {
        foreach ($rec in $svc.value) {
            $label = $rec.label
            $rtype = $rec.recordType
            $ttl = $rec.ttl
            $supportedService = $rec.supportedService

            $text = $null
            if ($rec.PSObject.Properties.Name -contains 'text') { $text = $rec.text }
            $mailExchange = $null
            if ($rec.PSObject.Properties.Name -contains 'mailExchange') { $mailExchange = $rec.mailExchange }
            $preference = $null
            if ($rec.PSObject.Properties.Name -contains 'preference') { $preference = $rec.preference }

            Write-Host ("- {0} {1} TTL={2} Service={3}" -f $label, $rtype, $ttl, $supportedService)
            if ($text) { Write-Host ("  Value: {0}" -f $text) }
            if ($mailExchange) {
                if ($null -ne $preference) {
                    Write-Host ("  MX: {0} (preference {1})" -f $mailExchange, $preference)
                }
                else {
                    Write-Host ("  MX: {0}" -f $mailExchange)
                }
            }
        }
    }
    else {
        Write-Host '(No service configuration records returned.)' -ForegroundColor Yellow
    }
}
catch {
    Write-Host 'Failed to fetch service configuration records.' -ForegroundColor Yellow
}

Write-Host ''
Write-Host 'Next steps:' -ForegroundColor Cyan
Write-Host '- Add the verification TXT record(s) to DNS (Cloudflare), then re-run status/preflight workflows to confirm verification.'
Write-Host "- After verification: run 02. Domain - Enforce Standard (Fix) with dry_run disabled (LIVE) to enable DKIM when appropriate."

if ($env:GITHUB_OUTPUT) {
    "created={0}" -f $created | Out-File -FilePath $env:GITHUB_OUTPUT -Append -Encoding utf8
}
