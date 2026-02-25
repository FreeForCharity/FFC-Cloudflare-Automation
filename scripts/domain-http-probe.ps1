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

$utilsPath = Join-Path -Path $PSScriptRoot -ChildPath 'ffc-utils.psm1'
Import-Module $utilsPath -Force

if (-not (Test-Path $InputFile)) {
    throw "InputFile not found: $InputFile"
}

$rowsIn = Import-Csv -Path $InputFile

$domains = @(
    $rowsIn |
        ForEach-Object { Normalize-Domain -Domain ([string]$_.($DomainColumn)) } |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
        Sort-Object -Unique
)

# HttpClient with redirect disabled (we want the raw 3xx classification).
$handler = [System.Net.Http.HttpClientHandler]::new()
$handler.AllowAutoRedirect = $false
$client = [System.Net.Http.HttpClient]::new($handler)
$client.Timeout = [TimeSpan]::FromSeconds($TimeoutSeconds)

function Resolve-RedirectUrl {
    param(
        [Parameter(Mandatory = $true)]
        [string]$CurrentUrl,

        [Parameter(Mandatory = $true)]
        [string]$Location
    )

    try {
        $baseUri = [uri]$CurrentUrl
        $nextUri = [uri]::new($baseUri, $Location)
        return $nextUri.AbsoluteUri
    }
    catch {
        return $Location
    }
}

function Invoke-Probe {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Url
    )

    function Invoke-Once {
        param(
            [Parameter(Mandatory = $true)]
            [System.Net.Http.HttpMethod]$Method,

            [Parameter(Mandatory = $true)]
            [string]$RequestUrl
        )

        $req = $null
        $resp = $null
        try {
            $req = [System.Net.Http.HttpRequestMessage]::new($Method, $RequestUrl)
            $resp = $client.SendAsync($req, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()

            $location = ''
            if ($resp.Headers.Location) {
                $location = [string]$resp.Headers.Location
            }

            return @{ ok = $true; status = [int]$resp.StatusCode; location = $location; errorType = ''; errorMessage = '' }
        }
        finally {
            if ($null -ne $resp) { $resp.Dispose() }
            if ($null -ne $req) { $req.Dispose() }
        }
    }

    $currentUrl = $Url
    $lastLocation = ''

    for ($i = 0; $i -le $MaxRedirects; $i++) {
        try {
            $r = Invoke-Once -Method ([System.Net.Http.HttpMethod]::Head) -RequestUrl $currentUrl
        }
        catch {
            # Some origins reject HEAD; fall back to GET.
            try {
                $r = Invoke-Once -Method ([System.Net.Http.HttpMethod]::Get) -RequestUrl $currentUrl
            }
            catch {
                $t = $_.Exception.GetType().FullName
                $m = $_.Exception.Message
                return @{ ok = $false; status = 0; location = ''; errorType = $t; errorMessage = $m }
            }
        }

        if (-not $r.ok) {
            return $r
        }

        if ($r.status -ge 300 -and $r.status -lt 400 -and -not [string]::IsNullOrWhiteSpace($r.location)) {
            $lastLocation = $r.location
            if ($MaxRedirects -gt 0 -and $i -lt $MaxRedirects) {
                $currentUrl = Resolve-RedirectUrl -CurrentUrl $currentUrl -Location $r.location
                continue
            }
        }

        if ([string]::IsNullOrWhiteSpace($r.location) -and -not [string]::IsNullOrWhiteSpace($lastLocation)) {
            $r.location = $lastLocation
        }

        return $r
    }

    return @{ ok = $true; status = 0; location = $lastLocation; errorType = ''; errorMessage = '' }
}

$out = New-Object System.Collections.Generic.List[object]

$idx = 0
try {
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
                domain           = (Protect-CsvCell -Value $d)
                scheme           = $scheme
                statusCode       = $r.status
                health           = $health
                isCritical       = ($health -in @('live', 'redirect'))
                redirectLocation = (Protect-CsvCell -Value $r.location)
                errorType        = (Protect-CsvCell -Value $r.errorType)
                errorMessage     = (Protect-CsvCell -Value $r.errorMessage)
                probedAtUtc      = (Get-Date).ToUniversalTime().ToString('o')
            }) | Out-Null
    }
}
finally {
    if ($null -ne $client) { $client.Dispose() }
    if ($null -ne $handler) { $handler.Dispose() }
}

$outDir = Split-Path -Path $OutputFile -Parent
if ($outDir -and -not (Test-Path -LiteralPath $outDir)) {
    New-Item -ItemType Directory -Path $outDir -Force | Out-Null
}

$out | Export-Csv -Path $OutputFile -NoTypeInformation -Encoding utf8
Write-Host "Wrote $($out.Count) rows to $OutputFile" -ForegroundColor Green
