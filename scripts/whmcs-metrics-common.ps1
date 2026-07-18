<#
.SYNOPSIS
    Shared helpers for the WHMCS metrics workflows (214-219 family).

.DESCRIPTION
    Dot-source AFTER whmcs-api-common.ps1:
        . (Join-Path $PSScriptRoot 'whmcs-api-common.ps1')
        . (Join-Path $PSScriptRoot 'whmcs-metrics-common.ps1')

    Provides:
      - Get-WhmcsListFromResponse: extracts item lists from nested, flat, or
        XML WHMCS response shapes.
      - Invoke-WhmcsGet: JSON-first request with automatic XML fallback when
        WHMCS's JSON encoder rejects a record containing malformed UTF-8
        ("Error generating JSON encoded response"). PowerShell property access
        works the same on the parsed XML root, so callers are format-agnostic.
      - Get-YearFromDate: safe YYYY extraction from WHMCS date strings.
#>

function Get-WhmcsListFromResponse {
    param(
        [Parameter(Mandatory = $true)] $Response,
        [Parameter(Mandatory = $true)] [string]$Container,
        [Parameter(Mandatory = $true)] [string]$Item
    )

    $node = $Response.$Container
    if ($node) {
        if ($node.$Item) { return @($node.$Item) }
        if ($node -is [System.Array]) { return @($node) }
    }

    $rx = '^' + [regex]::Escape($Container) + '\[' + [regex]::Escape($Item) + '\]\[(\d+)\]\[([^\]]+)\]$'
    $byIndex = @{}
    foreach ($prop in $Response.PSObject.Properties) {
        $m = [regex]::Match($prop.Name, $rx)
        if (-not $m.Success) { continue }
        $idx = [int]$m.Groups[1].Value
        if (-not $byIndex.ContainsKey($idx)) { $byIndex[$idx] = @{} }
        $byIndex[$idx][$m.Groups[2].Value] = $prop.Value
    }
    if ($byIndex.Count -le 0) { return @() }

    $items = @()
    foreach ($idx in ($byIndex.Keys | Sort-Object)) { $items += [PSCustomObject]$byIndex[$idx] }
    return $items
}

function Get-YearFromDate {
    param([string]$Date)
    if ($Date -match '^(\d{4})-\d{2}-\d{2}') { return [int]$Matches[1] }
    return $null
}

function ConvertFrom-WhmcsXml {
    param([Parameter(Mandatory = $true)][string]$RawXml)

    $clean = $RawXml -replace "[\x00-\x08\x0B\x0C\x0E-\x1F]", ''
    try {
        return [xml]$clean
    }
    catch {
        $snippet = if ($clean.Length -gt 400) { $clean.Substring(0, 400) + '...' } else { $clean }
        throw "Failed to parse WHMCS XML response. Snippet: $snippet"
    }
}

function Invoke-WhmcsApiXml {
    param(
        [Parameter(Mandatory = $true)][string]$ApiUrl,
        [Parameter(Mandatory = $true)][hashtable]$Body
    )

    $allowedHosts = @('apim-ffc-gateway-prod.azure-api.net', 'freeforcharity.org')
    $parsedUri = $null
    if (-not [Uri]::TryCreate($ApiUrl, [UriKind]::Absolute, [ref]$parsedUri) -or $parsedUri.Scheme -ne 'https' -or $allowedHosts -notcontains $parsedUri.Host) {
        throw "Refusing to send WHMCS credentials to '$ApiUrl': host is not in the allowlist ($($allowedHosts -join ', '))."
    }

    $headers = @{
        'Accept'     = '*/*'
        'User-Agent' = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
    }
    if (-not [string]::IsNullOrWhiteSpace($env:WHMCS_APIM_SUBSCRIPTION_KEY)) {
        $headers['Ocp-Apim-Subscription-Key'] = $env:WHMCS_APIM_SUBSCRIPTION_KEY
    }

    $resp = Invoke-RestMethod -Method Post -Uri $ApiUrl -Headers $headers -Body $Body -ContentType 'application/x-www-form-urlencoded' -ErrorAction Stop

    if ($resp -is [xml]) {
        $xml = $resp
    }
    elseif ($resp -is [string]) {
        $xml = ConvertFrom-WhmcsXml -RawXml $resp
    }
    else {
        $raw = $resp | Out-String
        $xml = ConvertFrom-WhmcsXml -RawXml $raw
    }

    $root = $xml.whmcsapi
    if (-not $root) {
        throw 'WHMCS API returned XML but did not contain <whmcsapi> root.'
    }
    if ($root.result -ne 'success') {
        $msg = $null
        if ($root.message) { $msg = [string]$root.message }
        elseif ($root.errormessage) { $msg = [string]$root.errormessage }
        if ([string]::IsNullOrWhiteSpace($msg)) { $msg = 'Unknown WHMCS API error.' }
        throw "WHMCS API error: $msg"
    }
    return $root
}

function Invoke-WhmcsGet {
    param(
        [Parameter(Mandatory = $true)][string]$ApiUrl,
        [Parameter(Mandatory = $true)][hashtable]$Body
    )
    try {
        return Invoke-WhmcsApi -ApiUrl $ApiUrl -Body $Body
    }
    catch {
        if ("$($_.Exception.Message)" -notmatch 'Malformed UTF-8|JSON encoded response') { throw }
        Write-Warning "WHMCS JSON encoding failed for action '$($Body.action)'; retrying as XML."
    }
    $xmlBody = @{}
    foreach ($k in $Body.Keys) { $xmlBody[$k] = $Body[$k] }
    $xmlBody.responsetype = 'xml'
    return Invoke-WhmcsApiXml -ApiUrl $ApiUrl -Body $xmlBody
}
