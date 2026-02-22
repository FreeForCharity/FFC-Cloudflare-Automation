[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$InputCsv,

    [Parameter()]
    [string]$DomainColumn = 'repoDomain',

    [Parameter()]
    [int]$TimeoutSec = 20,

    [Parameter()]
    [int]$MaxRedirect = 8,

    [Parameter()]
    [string]$OutputFile = '_run_artifacts/domain_content_validation.csv'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Normalize-Domain {
    param([string]$Domain)
    if ([string]::IsNullOrWhiteSpace($Domain)) { return $null }
    return $Domain.Trim().ToLowerInvariant().TrimEnd('.')
}

function Try-Fetch {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Url,

        [Parameter(Mandatory = $true)]
        [int]$TimeoutSec,

        [Parameter(Mandatory = $true)]
        [int]$MaxRedirect
    )

    $iwrParams = @{
        Uri                = $Url
        MaximumRedirection = $MaxRedirect
        TimeoutSec         = $TimeoutSec
        Headers            = @{
            'User-Agent' = 'FFC-Automation/1.0 (domain validation)'
            'Accept'     = 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
        }
    }

    $iwrCommand = Get-Command Invoke-WebRequest

    # Prefer non-interactive behavior (suppresses Script Execution Risk prompt in Windows PowerShell)
    if ($null -ne $iwrCommand.Parameters['UseBasicParsing']) {
        $iwrParams['UseBasicParsing'] = $true
    }

    # Avoid throwing on 4xx/5xx in PowerShell 7+; in Windows PowerShell, treat them as non-terminating.
    if ($null -ne $iwrCommand.Parameters['SkipHttpErrorCheck']) {
        $iwrParams['SkipHttpErrorCheck'] = $true
        $iwrParams['ErrorAction'] = 'Stop'
    }
    else {
        $iwrParams['ErrorAction'] = 'Continue'
    }

    try {
        $resp = Invoke-WebRequest @iwrParams
        if ($null -eq $resp) {
            return [PSCustomObject]@{
                ok           = $false
                statusCode   = $null
                finalUrl     = $Url
                contentType  = $null
                rawLength    = 0
                content      = ''
                errorType    = 'InvokeWebRequestReturnedNull'
                errorMessage = 'Invoke-WebRequest returned null response.'
            }
        }

        $finalUrl = $Url
        try {
            if ($resp.BaseResponse -and $resp.BaseResponse.ResponseUri) {
                $finalUrl = [string]$resp.BaseResponse.ResponseUri.AbsoluteUri
            }
        }
        catch {
            # ignore
        }

        return [PSCustomObject]@{
            ok            = $true
            statusCode    = if ($null -ne $resp.StatusCode) { [int]$resp.StatusCode } else { $null }
            finalUrl      = $finalUrl
            contentType   = [string]$resp.Headers.'Content-Type'
            rawLength     = if ($null -ne $resp.RawContentLength) { [int]$resp.RawContentLength } else { 0 }
            content       = [string]$resp.Content
            errorType     = $null
            errorMessage  = $null
        }
    }
    catch {
        $ex = $_.Exception

        $statusCode = $null
        $finalUrl = $Url

        try {
            if ($ex.PSObject.Properties.Match('Response').Count -gt 0 -and $null -ne $ex.Response) {
                if ($ex.Response.PSObject.Properties.Match('ResponseUri').Count -gt 0 -and $null -ne $ex.Response.ResponseUri) {
                    $finalUrl = [string]$ex.Response.ResponseUri.AbsoluteUri
                }
                if ($ex.Response.PSObject.Properties.Match('StatusCode').Count -gt 0 -and $null -ne $ex.Response.StatusCode) {
                    $statusCode = [int]$ex.Response.StatusCode
                }
            }
        }
        catch {
            # ignore
        }

        return [PSCustomObject]@{
            ok            = $false
            statusCode    = $statusCode
            finalUrl      = $finalUrl
            contentType   = $null
            rawLength     = 0
            content       = ''
            errorType     = $ex.GetType().FullName
            errorMessage  = $ex.Message
        }
    }
}

function Extract-Title {
    param([string]$Html)
    if ([string]::IsNullOrWhiteSpace($Html)) { return $null }

    $m = [regex]::Match($Html, '<title[^>]*>(.*?)</title>', 'IgnoreCase,Singleline')
    if (-not $m.Success) { return $null }

    $t = $m.Groups[1].Value
    $t = [regex]::Replace($t, '\s+', ' ').Trim()
    if ($t.Length -gt 200) { $t = $t.Substring(0, 200) }
    return $t
}

function Classify-Content {
    param(
        [string]$Html,
        [string]$Title,
        [string]$ContentType,
        [int]$RawLength
    )

    $reasons = New-Object System.Collections.Generic.List[string]

    $ct = if ($ContentType) { $ContentType.ToLowerInvariant() } else { '' }
    $isHtml = ($ct -match 'text/html' -or $ct -match 'application/xhtml\+xml' -or [string]::IsNullOrWhiteSpace($ct))

    if (-not $isHtml) {
        $reasons.Add('non_html_content_type')
        return [PSCustomObject]@{ isPlaceholder = $false; isBlank = $false; reasons = $reasons }
    }

    $text = if ($Html) { $Html } else { '' }
    $textLower = $text.ToLowerInvariant()

    # crude blank detection
    $bodyNoWs = [regex]::Replace($text, '\s+', '')
    $isBlank = ($RawLength -gt 0 -and $RawLength -lt 400) -or ($bodyNoWs.Length -lt 200)
    if ($isBlank) { $reasons.Add('very_small_or_blank') }

    # Strong placeholder/broken indicators
    $strongPatterns = @(
        'domain\s+parked',
        'this\s+domain\s+is\s+parked',
        'account\s+suspended',
        'website\s+is\s+unavailable',
        'temporarily\s+unavailable',
        'welcome\s+to\s+nginx',
        'apache2\s+debian\s+default\s+page',
        'iis\s+windows\s+server',
        'default\s+web\s+site',
        'index\s+of\s*/',
        'there\s+isn\x27t\s+a\s+github\s+pages\s+site\s+here',
        'project\s+not\s+found',
        'site\s+not\s+found',
        'parking\s+page'
    )

    # Weak signals that can appear on otherwise valid pages (e.g., blog posts or image alt text)
    $weakPatterns = @(
        'coming\s+soon',
        'under\s+construction',
        '404\s+not\s+found',
        'page\s+not\s+found',
        'error\s+404'
    )

    $matchedStrong = @()
    foreach ($p in $strongPatterns) {
        if ($textLower -match $p) { $matchedStrong += $p }
    }

    $matchedWeak = @()
    foreach ($p in $weakPatterns) {
        if ($textLower -match $p) { $matchedWeak += $p }
    }

    foreach ($m in ($matchedStrong | Select-Object -Unique)) {
        $reasons.Add("strong_signal:$m")
    }
    foreach ($m in ($matchedWeak | Select-Object -Unique)) {
        $reasons.Add("weak_signal:$m")
    }

    $titleLower = if ($Title) { $Title.ToLowerInvariant() } else { '' }
    $titleSuggestsNotFound = ($titleLower -match '404' -or $titleLower -match 'not\s+found')

    $weakCountsAsPlaceholder = ($matchedWeak.Count -gt 0) -and ($isBlank -or $RawLength -lt 20000 -or $titleSuggestsNotFound)

    $isPlaceholder = ($matchedStrong.Count -gt 0) -or $weakCountsAsPlaceholder

    if ($weakCountsAsPlaceholder) { $reasons.Add('weak_signals_triggered_placeholder') }

    return [PSCustomObject]@{ isPlaceholder = $isPlaceholder; isBlank = $isBlank; reasons = $reasons }
}

if (-not (Test-Path -Path $InputCsv)) {
    throw "Input CSV not found: $InputCsv"
}

$rows = Import-Csv -Path $InputCsv
if (-not ($rows | Get-Member -Name $DomainColumn -MemberType NoteProperty,Property)) {
    throw "Column '$DomainColumn' not found in $InputCsv"
}

$outDir = Split-Path -Parent $OutputFile
if (-not [string]::IsNullOrWhiteSpace($outDir)) {
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null
}

$results = New-Object System.Collections.Generic.List[object]

$domains = @($rows | ForEach-Object { Normalize-Domain ([string]($_.$DomainColumn)) } | Where-Object { $_ })
$domains = @($domains | Select-Object -Unique)

Write-Host "Validating domains: $($domains.Count)" -ForegroundColor Cyan

$i = 0
foreach ($d in $domains) {
    $i++
    Write-Host ("[{0}/{1}] {2}" -f $i, $domains.Count, $d) -ForegroundColor Gray

    $httpsUrl = "https://$d/"
    $httpUrl  = "http://$d/"

    $fetch = Try-Fetch -Url $httpsUrl -TimeoutSec $TimeoutSec -MaxRedirect $MaxRedirect
    $schemeUsed = 'https'
    if (-not $fetch.ok) {
        $fetch = Try-Fetch -Url $httpUrl -TimeoutSec $TimeoutSec -MaxRedirect $MaxRedirect
        $schemeUsed = 'http'
    }

    $title = Extract-Title -Html $fetch.content
    $classification = Classify-Content -Html $fetch.content -Title $title -ContentType $fetch.contentType -RawLength $fetch.rawLength

    $is200 = ($fetch.ok -and $fetch.statusCode -eq 200)
    $looksGood = ($is200 -and -not $classification.isPlaceholder -and -not $classification.isBlank)

    $results.Add([PSCustomObject]@{
        domain         = $d
        schemeUsed     = $schemeUsed
        ok             = $fetch.ok
        statusCode     = $fetch.statusCode
        finalUrl       = $fetch.finalUrl
        contentType    = $fetch.contentType
        rawLength      = $fetch.rawLength
        title          = $title
        is200          = $is200
        isBlank        = $classification.isBlank
        isPlaceholder  = $classification.isPlaceholder
        looksGood      = $looksGood
        reasons        = [string]::Join(';', @($classification.reasons))
        errorType      = $fetch.errorType
        errorMessage   = $fetch.errorMessage
    })
}

$results | Export-Csv -Path $OutputFile -NoTypeInformation -Encoding utf8

$good = @($results | Where-Object { $_.looksGood })
$bad  = @($results | Where-Object { -not $_.looksGood })
Write-Host "Looks good (200 + non-placeholder): $($good.Count)" -ForegroundColor Green
Write-Host "Not good (non-200 / placeholder / blank / error): $($bad.Count)" -ForegroundColor Yellow
Write-Host "Wrote: $OutputFile" -ForegroundColor Green
