[CmdletBinding()]
param(
    [Parameter()]
    [string]$InputFile = 'ffcadmin_sites_list_domains.csv',

    [Parameter()]
    [string]$DomainColumn = 'domain',

    [Parameter()]
    [ValidateRange(1, 120)]
    [int]$TimeoutSeconds = 15,

    [Parameter()]
    [int]$MaxRedirects = 0,

    [Parameter()]
    [string]$OutputFile = 'domain_http_probe.csv'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not (Test-Path $InputFile)) {
    throw "InputFile not found: $InputFile"
}

$rowsIn = Import-Csv -Path $InputFile
$domains = @($rowsIn | ForEach-Object { $_.$DomainColumn } | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | ForEach-Object { $_.Trim().ToLowerInvariant().TrimEnd('.') } | Sort-Object -Unique)

# HttpClient with redirect disabled (we want the raw 3xx classification).
$handler = New-Object System.Net.Http.HttpClientHandler
$handler.AllowAutoRedirect = $false
$client = New-Object System.Net.Http.HttpClient($handler)
$client.Timeout = [TimeSpan]::FromSeconds($TimeoutSeconds)

function Invoke-Probe {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Url
    )

    $req = New-Object System.Net.Http.HttpRequestMessage([System.Net.Http.HttpMethod]::Head, $Url)

    try {
        $resp = $client.SendAsync($req, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
        return @{ ok = $true; status = [int]$resp.StatusCode; location = [string]$resp.Headers.Location; errorType = ''; errorMessage = '' }
    }
    catch {
        # Some origins reject HEAD; fall back to GET.
        try {
            $req2 = New-Object System.Net.Http.HttpRequestMessage([System.Net.Http.HttpMethod]::Get, $Url)
            $resp2 = $client.SendAsync($req2, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
            return @{ ok = $true; status = [int]$resp2.StatusCode; location = [string]$resp2.Headers.Location; errorType = ''; errorMessage = '' }
        }
        catch {
            $t = $_.Exception.GetType().FullName
            $m = $_.Exception.Message
            return @{ ok = $false; status = 0; location = ''; errorType = $t; errorMessage = $m }
        }
    }
}

$out = New-Object System.Collections.Generic.List[object]

$idx = 0
foreach ($d in $domains) {
    $idx++
    if (($idx % 25) -eq 0) {
        Write-Host ("Probing {0}/{1}: {2}" -f $idx, $domains.Count, $d) -ForegroundColor Gray
    }

    $https = "https://$d/"
    $r = Invoke-Probe -Url $https
    $scheme = 'https'

    if (-not $r.ok) {
        $http = "http://$d/"
        $r = Invoke-Probe -Url $http
        $scheme = 'http'
    }

    $health = if (-not $r.ok) {
        'unreachable'
    }
    elseif ($r.status -ge 200 -and $r.status -lt 300) {
        'live'
    }
    elseif ($r.status -ge 300 -and $r.status -lt 400) {
        'redirect'
    }
    else {
        'error'
    }

    $out.Add([PSCustomObject]@{
            domain           = $d
            scheme           = $scheme
            statusCode       = $r.status
            health           = $health
            isCritical       = ($health -in @('live', 'redirect'))
            redirectLocation = $r.location
            errorType        = $r.errorType
            errorMessage     = $r.errorMessage
            probedAtUtc      = (Get-Date).ToUniversalTime().ToString('o')
        }) | Out-Null
}

$out | Export-Csv -Path $OutputFile -NoTypeInformation -Encoding utf8
Write-Host "Wrote $($out.Count) rows to $OutputFile" -ForegroundColor Green
