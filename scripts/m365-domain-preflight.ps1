[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Domain,

    [Parameter()]
    [string]$AccessToken,

    [Parameter()]
    [string]$CloudflareToken,

    [Parameter()]
    [switch]$SkipGraph,

    [Parameter()]
    [switch]$SkipCloudflare,

    [Parameter()]
    [switch]$FailOnGaps
)

$ErrorActionPreference = 'Stop'

function Write-Kv {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Key,
        [Parameter()]
        [AllowNull()]
        [object]$Value
    )

    $v = if ($null -eq $Value) { '' } else { [string]$Value }
    Write-Host ("{0}: {1}" -f $Key, $v)
}

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

function Get-GraphAccessToken {
    param(
        [Parameter()]
        [AllowNull()]
        [string]$Token
    )

    $tokenToUse = if ($Token) { $Token } else { $env:GRAPH_ACCESS_TOKEN }
    if (-not [string]::IsNullOrWhiteSpace($tokenToUse)) {
        return $tokenToUse
    }

    $az = Get-Command az -ErrorAction SilentlyContinue
    if (-not $az) {
        throw 'No AccessToken provided and GRAPH_ACCESS_TOKEN is not set. Install Azure CLI (az) or pass -AccessToken.'
    }

    $tokenToUse = (az account get-access-token --resource-type ms-graph --query accessToken -o tsv)
    if ([string]::IsNullOrWhiteSpace($tokenToUse)) {
        throw 'Failed to acquire a Microsoft Graph access token via Azure CLI.'
    }

    return $tokenToUse
}

function Get-TenantSummary {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Token
    )

    $org = Invoke-GraphGet -Token $Token -Uri 'https://graph.microsoft.com/v1.0/organization?$select=id,displayName'
    $org0 = if ($org.value -and $org.value.Count -gt 0) { $org.value[0] } else { $null }

    $dom = Invoke-GraphGet -Token $Token -Uri 'https://graph.microsoft.com/v1.0/domains?$select=id,isDefault,isVerified'
    $domains = @($dom.value)

    $defaultDomain = $domains | Where-Object { $_.isDefault } | Select-Object -First 1
    $onMicrosoft = $domains | Where-Object { $_.id -like '*.onmicrosoft.com' } | Select-Object -First 1

    [pscustomobject]@{
        TenantId          = if ($org0) { $org0.id } else { $null }
        Organization      = if ($org0) { $org0.displayName } else { $null }
        DefaultDomain     = if ($defaultDomain) { $defaultDomain.id } else { $null }
        OnMicrosoftDomain = if ($onMicrosoft) { $onMicrosoft.id } else { $null }
    }
}

function Try-GetDomainFromTenant {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Domain,
        [Parameter(Mandatory = $true)]
        [string]$Token
    )

    $encoded = [Uri]::EscapeDataString($Domain)
    $uri = "https://graph.microsoft.com/v1.0/domains/$encoded`?$select=id,isDefault,isVerified,supportedServices"

    try {
        return (Invoke-GraphGet -Token $Token -Uri $uri)
    }
    catch {
        $resp = $_.Exception.Response
        if ($resp -and $resp.StatusCode -and ([int]$resp.StatusCode -eq 404)) {
            return $null
        }
        throw
    }
}

function Try-GetMicrosoftDnsGuidance {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Domain,
        [Parameter(Mandatory = $true)]
        [string]$Token
    )

    $encoded = [Uri]::EscapeDataString($Domain)

    $verification = $null
    $service = $null

    try {
        $verification = (Invoke-GraphGet -Token $Token -Uri "https://graph.microsoft.com/v1.0/domains/$encoded/verificationDnsRecords").value
    }
    catch {
        $verification = @()
    }

    try {
        $service = (Invoke-GraphGet -Token $Token -Uri "https://graph.microsoft.com/v1.0/domains/$encoded/serviceConfigurationRecords").value
    }
    catch {
        $service = @()
    }

    [pscustomobject]@{
        Verification = @($verification)
        Service      = @($service)
    }
}

function Get-CfTokens {
    param([AllowNull()][string]$Provided)

    if ($Provided) { return @($Provided) }

    $tokens = @()
    if ($env:CLOUDFLARE_API_TOKEN_FFC) { $tokens += @($env:CLOUDFLARE_API_TOKEN_FFC) }
    if ($env:CLOUDFLARE_API_TOKEN_CM) { $tokens += @($env:CLOUDFLARE_API_TOKEN_CM) }

    # Preserve order, de-dupe
    $seen = @{}
    return @(
        foreach ($t in $tokens) {
            if ([string]::IsNullOrWhiteSpace($t)) { continue }
            $key = $t.Trim()
            if (-not $seen.ContainsKey($key)) {
                $seen[$key] = $true
                $key
            }
        }
    )
}

function Resolve-CfTokenForZone {
    param(
        [Parameter(Mandatory = $true)][string]$Zone,
        [AllowNull()][string]$ProvidedToken
    )

    $tokens = @(Get-CfTokens -Provided $ProvidedToken)
    foreach ($t in $tokens) {
        try {
            $headers = @{ Authorization = "Bearer $t"; 'Content-Type' = 'application/json' }
            $zones = (Invoke-CfApi -Method 'GET' -Uri '/zones' -Headers $headers -Params @{ name = $Zone }).result
            if ($zones -and $zones.Count -gt 0) { return $t }
        }
        catch {
            continue
        }
    }

    return $null
}

function Invoke-CfApi {
    param(
        [Parameter(Mandatory = $true)][string]$Method,
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][hashtable]$Headers,
        [Parameter()][hashtable]$Params
    )

    $base = 'https://api.cloudflare.com/client/v4'
    $u = "$base$Uri"

    if ($Params) {
        $qs = ($Params.Keys | ForEach-Object { "$_=$([uri]::EscapeDataString([string]$Params[$_]))" }) -join '&'
        if ($qs) { $u = "$u`?$qs" }
    }

    $resp = Invoke-RestMethod -Method $Method -Uri $u -Headers $Headers -ErrorAction Stop
    if (-not $resp.success) {
        $msg = ($resp.errors | Select-Object -First 1 -ExpandProperty message -ErrorAction SilentlyContinue)
        throw "Cloudflare API error: $msg"
    }
    return $resp
}

function Try-GetCloudflareDkimSelectors {
    param(
        [Parameter(Mandatory = $true)][string]$Zone,
        [Parameter(Mandatory = $true)][string]$Token
    )

    $headers = @{ Authorization = "Bearer $Token"; 'Content-Type' = 'application/json' }

    $zones = (Invoke-CfApi -Method 'GET' -Uri '/zones' -Headers $headers -Params @{ name = $Zone }).result
    if (-not $zones -or $zones.Count -lt 1) {
        return [pscustomobject]@{ Found = $false; Reason = "Zone '$Zone' not found in Cloudflare"; Selector1 = $null; Selector2 = $null }
    }

    $zoneId = $zones[0].id
    $sel1Name = "selector1._domainkey.$Zone"
    $sel2Name = "selector2._domainkey.$Zone"

    $sel1 = (Invoke-CfApi -Method 'GET' -Uri "/zones/$zoneId/dns_records" -Headers $headers -Params @{ type = 'CNAME'; name = $sel1Name }).result
    $sel2 = (Invoke-CfApi -Method 'GET' -Uri "/zones/$zoneId/dns_records" -Headers $headers -Params @{ type = 'CNAME'; name = $sel2Name }).result

    [pscustomobject]@{
        Found     = $true
        Reason    = $null
        Selector1 = ($sel1 | Select-Object -First 1)
        Selector2 = ($sel2 | Select-Object -First 1)
    }
}

function Run-CloudflareAudit {
    param(
        [Parameter(Mandatory = $true)][string]$Zone,
        [Parameter(Mandatory = $true)][string]$Token
    )

    $cfScript = Join-Path (Join-Path $PSScriptRoot '..') 'Update-CloudflareDns.ps1'
    if (-not (Test-Path $cfScript)) {
        throw "Expected Cloudflare script not found: $cfScript"
    }

    # Merge all streams so we can parse output for [MISSING]/[DIFFERS] indicators.
    $output = & $cfScript -Zone $Zone -Audit -Token $Token *>&1
    $lines = @($output | ForEach-Object { [string]$_ })

    $issues = $lines | Where-Object { $_ -match '^\[(MISSING|MISSING/PARTIAL|DIFFERS)\]' }
    $optional = $lines | Where-Object { $_ -match '^\[OPTIONAL\]' }

    [pscustomobject]@{
        OutputLines = $lines
        Issues      = @($issues)
        Optional    = @($optional)
    }
}

function Find-ExpectedDkimTargetsFromGraph {
    param(
        [Parameter(Mandatory = $true)]
        [object[]]$ServiceRecords
    )

    $cname = $ServiceRecords | Where-Object {
        ($_.recordType -as [string]) -match '(?i)^cname$|^cname$|^cname$|^cname$' -or ($_.recordType -as [string]) -match '(?i)cname'
    }

    $sel1 = $cname | Where-Object { ($_.label -as [string]) -match '(?i)^selector1\._domainkey' } | Select-Object -First 1
    $sel2 = $cname | Where-Object { ($_.label -as [string]) -match '(?i)^selector2\._domainkey' } | Select-Object -First 1

    [pscustomobject]@{
        Selector1 = $sel1
        Selector2 = $sel2
    }
}

function Get-GraphDnsRecordTargetText {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Record
    )

    if ($Record.PSObject.Properties.Name -contains 'text' -and $Record.text) { return [string]$Record.text }
    if ($Record.PSObject.Properties.Name -contains 'Text' -and $Record.Text) { return [string]$Record.Text }
    if ($Record.PSObject.Properties.Name -contains 'canonicalName' -and $Record.canonicalName) { return [string]$Record.canonicalName }
    if ($Record.PSObject.Properties.Name -contains 'mailExchange' -and $Record.mailExchange) { return [string]$Record.mailExchange }
    return ($Record | ConvertTo-Json -Compress -Depth 6)
}

try {
    Write-Host 'M365 domain preflight (read-only)' -ForegroundColor Cyan
    Write-Kv -Key 'Domain' -Value $Domain

    $graphToken = $null
    $tenant = $null
    $tenantDomain = $null

    if (-not $SkipGraph) {
        $graphToken = Get-GraphAccessToken -Token $AccessToken
        $tenant = Get-TenantSummary -Token $graphToken

        Write-Host ''
        Write-Host 'Step 1: Active M365 tenant (from current auth context)' -ForegroundColor Cyan
        Write-Kv -Key 'TenantId' -Value $tenant.TenantId
        Write-Kv -Key 'Organization' -Value $tenant.Organization
        Write-Kv -Key 'DefaultDomain' -Value $tenant.DefaultDomain
        Write-Kv -Key 'OnMicrosoftDomain' -Value $tenant.OnMicrosoftDomain

        Write-Host ''
        Write-Host 'Step 2: Check if domain is already added to this tenant' -ForegroundColor Cyan

        $tenantDomain = Try-GetDomainFromTenant -Domain $Domain -Token $graphToken
        if ($tenantDomain) {
            Write-Kv -Key 'DomainExistsInTenant' -Value 'YES'
            Write-Kv -Key 'IsVerified' -Value $tenantDomain.isVerified
            Write-Kv -Key 'IsDefault' -Value $tenantDomain.isDefault
            $supportsEmail = @($tenantDomain.supportedServices) -contains 'Email'
            Write-Kv -Key 'SupportsEmail' -Value $supportsEmail
        }
        else {
            Write-Kv -Key 'DomainExistsInTenant' -Value 'NO'
        }
    }
    else {
        Write-Host ''
        Write-Host 'Step 1-2: Graph checks skipped (-SkipGraph)' -ForegroundColor DarkGray
    }

    Write-Host ''
    Write-Host 'Step 3: Check Cloudflare DNS health (repo standard audit)' -ForegroundColor Cyan

    $audit = $null

    if ($SkipCloudflare) {
        Write-Host 'Cloudflare checks skipped (-SkipCloudflare)' -ForegroundColor DarkGray
    }
    else {
        $cfToken = Resolve-CfTokenForZone -Zone $Domain -ProvidedToken $CloudflareToken
        if (-not $cfToken) {
            Write-Host 'Cloudflare token not set; skipping Cloudflare audit.' -ForegroundColor Yellow
            Write-Host 'Set CLOUDFLARE_API_TOKEN_FFC and CLOUDFLARE_API_TOKEN_CM, or pass -CloudflareToken.' -ForegroundColor DarkGray
        }
        else {
            $audit = Run-CloudflareAudit -Zone $Domain -Token $cfToken

            if ($audit.Issues.Count -eq 0) {
                Write-Host 'Cloudflare audit: no [MISSING]/[DIFFERS] issues found.' -ForegroundColor Green
            }
            else {
                Write-Host 'Cloudflare audit issues:' -ForegroundColor Yellow
                $audit.Issues | ForEach-Object { Write-Host "- $_" }
            }

            if ($audit.Optional.Count -gt 0) {
                Write-Host 'Cloudflare audit optional notices:' -ForegroundColor DarkGray
                $audit.Optional | ForEach-Object { Write-Host "- $_" }
            }

            Write-Host ''
            Write-Host 'Step 4: DKIM quick check (Cloudflare selectors)' -ForegroundColor Cyan
            $dkim = Try-GetCloudflareDkimSelectors -Zone $Domain -Token $cfToken
            if (-not $dkim.Found) {
                Write-Host $dkim.Reason -ForegroundColor Yellow
            }
            else {
                $hasSel1 = $null -ne $dkim.Selector1
                $hasSel2 = $null -ne $dkim.Selector2

                Write-Kv -Key 'selector1 CNAME present' -Value $hasSel1
                Write-Kv -Key 'selector2 CNAME present' -Value $hasSel2

                if ($tenantDomain -and $graphToken) {
                    try {
                        $guidance = Try-GetMicrosoftDnsGuidance -Domain $Domain -Token $graphToken
                        $expected = Find-ExpectedDkimTargetsFromGraph -ServiceRecords $guidance.Service

                        if ($expected.Selector1 -and $hasSel1) {
                            Write-Kv -Key 'selector1 expected target' -Value (Get-GraphDnsRecordTargetText -Record $expected.Selector1)
                            Write-Kv -Key 'selector1 current target' -Value $dkim.Selector1.content
                        }
                        else {
                            Write-Host 'selector1 target cannot be validated via Graph service records.' -ForegroundColor DarkGray
                        }

                        if ($expected.Selector2 -and $hasSel2) {
                            Write-Kv -Key 'selector2 expected target' -Value (Get-GraphDnsRecordTargetText -Record $expected.Selector2)
                            Write-Kv -Key 'selector2 current target' -Value $dkim.Selector2.content
                        }
                        else {
                            Write-Host 'selector2 target cannot be validated via Graph service records.' -ForegroundColor DarkGray
                        }
                    }
                    catch {
                        Write-Host 'DKIM expected targets could not be fetched from Graph (permissions or API response).' -ForegroundColor DarkGray
                    }
                }
                elseif ($SkipGraph) {
                    Write-Host 'Graph checks skipped, so DKIM target cannot be validated (only selector presence checked).' -ForegroundColor DarkGray
                }
                elseif (-not $tenantDomain) {
                    Write-Host 'Domain not yet in tenant, so DKIM target cannot be validated (only selector presence checked).' -ForegroundColor DarkGray
                }
            }
        }
    }

    if ($FailOnGaps -and $audit -and $audit.Issues.Count -gt 0) {
        exit 2
    }

    exit 0
}
catch {
    Write-Error $_
    exit 1
}
