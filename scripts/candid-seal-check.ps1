<#
.SYNOPSIS
    Fetch a charity's Candid seal widget and its shareable profile link the
    way a signed-out visitor would, and say whether each one really works.

.DESCRIPTION
    The site footer shows a charity's Candid Seal of Transparency as
      <a href="<shareable profile link>"><img src="<seal widget>"></a>
    where the seal widget is
      https://widgets.guidestar.org/prod/v1/pdp/transparency-seal/<profile id>/svg
    Building that address from the profile id proves nothing about whether it
    loads, so this script fetches both halves and classifies the answers.

    Cloud agent containers cannot reach Candid (their egress proxy refuses the
    hosts), so this runs on a GitHub Actions runner: workflow 803.

    Dot-source it for the pure functions (tests do), or run it with
    -ProfileUrl to fetch and report.
#>
[CmdletBinding()]
param(
    [string[]]$ProfileUrl = @(),
    [string[]]$SealId = @(),
    [string]$JsonOut = '',
    [switch]$Raw,
    [switch]$FailOnBad
)

$ErrorActionPreference = 'Stop'

function Get-CandidProfileId {
    # The numeric Candid profile id in an app.candid.org profile link, else ''. Pure.
    param([AllowEmptyString()][AllowNull()][string]$ProfileUrl)
    $m = [regex]::Match([string]$ProfileUrl, '(?i)^https://app\.candid\.org/profile/(\d{3,12})(?:[/?#]|$)')
    if ($m.Success) { return $m.Groups[1].Value }
    return ''
}

function Get-CandidSealWidgetUrl {
    # The seal widget address for a profile id. Pure.
    param([Parameter(Mandatory = $true)][string]$ProfileId)
    return "https://widgets.guidestar.org/prod/v1/pdp/transparency-seal/$ProfileId/svg"
}

function Invoke-CandidFetch {
    # One signed-out GET with a browser User-Agent, following redirects.
    # Returns status ($null on transport error), contentType, finalUrl, body.
    param([Parameter(Mandatory = $true)][string]$Url, [int]$TimeoutSec = 20)
    $out = [ordered]@{ url = $Url; status = $null; contentType = ''; finalUrl = ''; body = ''; error = '' }
    $handler = [System.Net.Http.HttpClientHandler]::new()
    $handler.AllowAutoRedirect = $true
    $handler.MaxAutomaticRedirections = 8
    $handler.UseCookies = $false
    $client = [System.Net.Http.HttpClient]::new($handler)
    try {
        $client.Timeout = [TimeSpan]::FromSeconds($TimeoutSec)
        $req = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::Get, $Url)
        [void]$req.Headers.TryAddWithoutValidation('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36')
        [void]$req.Headers.TryAddWithoutValidation('Accept', 'text/html,application/xhtml+xml,image/svg+xml,*/*;q=0.8')
        $resp = $client.SendAsync($req).GetAwaiter().GetResult()
        $out.status = [int]$resp.StatusCode
        if ($resp.Content.Headers.ContentType) { $out.contentType = [string]$resp.Content.Headers.ContentType }
        $out.finalUrl = [string]$resp.RequestMessage.RequestUri
        $body = $resp.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        if ($body.Length -gt 400000) { $body = $body.Substring(0, 400000) }
        $out.body = $body
        $resp.Dispose()
    }
    catch { $out.error = $_.Exception.Message }
    finally { $client.Dispose() }
    return [pscustomobject]$out
}

function Get-CandidBodyDigest {
    # sha256 of the body text, lowercase hex. Pure.
    param([AllowEmptyString()][string]$Body)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes([string]$Body)
        return (($sha.ComputeHash($bytes) | ForEach-Object { $_.ToString('x2') }) -join '')
    }
    finally { $sha.Dispose() }
}

function Get-CandidSealVerdict {
    <#
    .SYNOPSIS
        Is this seal widget answer a real, visible seal? Pure.
    .DESCRIPTION
        Measured on a GitHub runner, 2026-11-21 (workflow 803):
          9326392 (Free For Charity)  200 image/svg+xml, 12.7 KB, 108x108, outer #a6c0ce (Platinum)
          15935725, 16766185          200 image/svg+xml, 12.0 KB, 108x108, outer #d3aa41 (Gold)
          1 (an org with no seal)     200 image/svg+xml, 173 bytes: an empty white outline,
                                      invisible on a page. A 200 SVG is therefore NOT proof.
          999999999 (no such org)     404 application/json {"Message":"Organization ... not found"}
        So a seal counts only when it is an SVG whose 108x108 outer square is
        filled with a colour. Returns @{ verdict; level; color } where verdict
        is ok | no_seal | no_org | not_svg | unverified.
    #>
    param([Parameter(Mandatory = $true)]$Fetch)
    $r = [ordered]@{ verdict = 'unverified'; level = ''; color = '' }
    if ($null -eq $Fetch.status) { return [pscustomobject]$r }
    if ($Fetch.status -eq 404) { $r.verdict = 'no_org'; return [pscustomobject]$r }
    if ($Fetch.status -ne 200) { return [pscustomobject]$r }
    $body = [string]$Fetch.body
    if ([string]$Fetch.contentType -notmatch '(?i)image/svg\+xml' -or $body -notmatch '(?i)<svg\b') { $r.verdict = 'not_svg'; return [pscustomobject]$r }
    if ($body -notmatch '(?i)viewBox\s*=\s*"0 0 108 108"') { $r.verdict = 'no_seal'; return [pscustomobject]$r }
    # The outer square: a rect at the origin, 108 wide and tall, filled by a class.
    $outer = [regex]::Match($body, '(?i)<rect\s+class="(cls-\d+)"(?=[^>]*\bwidth="108")(?=[^>]*\bheight="108")[^>]*/?>')
    if (-not $outer.Success) { $r.verdict = 'no_seal'; return [pscustomobject]$r }
    $cls = [regex]::Escape($outer.Groups[1].Value)
    $fill = [regex]::Match($body, "(?is)\.$cls\s*\{[^}]*?fill:\s*(#[0-9a-f]{3,8})")
    if (-not $fill.Success) { $r.verdict = 'no_seal'; return [pscustomobject]$r }
    $color = $fill.Groups[1].Value.ToLowerInvariant()
    $r.color = $color
    if ($color -in @('#fff', '#ffffff', '#ffffffff')) { $r.verdict = 'no_seal'; return [pscustomobject]$r }
    $levels = @{ '#a6c0ce' = 'Platinum'; '#d3aa41' = 'Gold' }
    $r.level = if ($levels.ContainsKey($color)) { $levels[$color] } else { "unrecognised colour $color" }
    $r.verdict = 'ok'
    return [pscustomobject]$r
}

function Get-CandidReport {
    # Fetch every profile link and seal widget asked for. Returns a list of
    # raw fetch records tagged with kind (profile | seal) and profileId.
    param([string[]]$ProfileUrl = @(), [string[]]$SealId = @())
    $records = [System.Collections.Generic.List[object]]::new()
    $ids = [System.Collections.Generic.List[string]]::new()
    foreach ($u in $ProfileUrl) {
        if ([string]::IsNullOrWhiteSpace($u)) { continue }
        $id = Get-CandidProfileId -ProfileUrl $u
        $f = Invoke-CandidFetch -Url $u
        $f | Add-Member -NotePropertyName kind -NotePropertyValue 'profile'
        $f | Add-Member -NotePropertyName profileId -NotePropertyValue $id
        $records.Add($f)
        if ($id -and -not $ids.Contains($id)) { $ids.Add($id) }
    }
    foreach ($s in $SealId) { if ($s -and -not $ids.Contains($s)) { $ids.Add($s) } }
    foreach ($id in $ids) {
        $f = Invoke-CandidFetch -Url (Get-CandidSealWidgetUrl -ProfileId $id)
        $f | Add-Member -NotePropertyName kind -NotePropertyValue 'seal'
        $f | Add-Member -NotePropertyName profileId -NotePropertyValue $id
        $records.Add($f)
    }
    return $records
}

if ($MyInvocation.InvocationName -ne '.') {
    $records = Get-CandidReport -ProfileUrl $ProfileUrl -SealId $SealId
    foreach ($r in $records) {
        $title = ([regex]::Match([string]$r.body, '(?is)<title[^>]*>(.*?)</title>')).Groups[1].Value.Trim()
        $texts = @([regex]::Matches([string]$r.body, '(?is)<text[^>]*>(.*?)</text>') | ForEach-Object { ($_.Groups[1].Value -replace '<[^>]+>', '').Trim() } | Where-Object { $_ } | Select-Object -First 12)
        Write-Host "=== $($r.kind) id=$($r.profileId)"
        Write-Host "url:          $($r.url)"
        Write-Host "status:       $($r.status) $($r.error)"
        Write-Host "content-type: $($r.contentType)"
        Write-Host "final url:    $($r.finalUrl)"
        Write-Host "bytes:        $(([string]$r.body).Length)   sha256: $(Get-CandidBodyDigest -Body $r.body)"
        Write-Host "title:        $title"
        if ($r.kind -eq 'seal') {
            $v = Get-CandidSealVerdict -Fetch $r
            Write-Host "SEAL VERDICT: $($v.verdict) $($v.level)"
        }
        foreach ($w in 'sign in', 'log in', 'login', 'Platinum', 'Gold', 'Silver', 'Bronze', 'seal', 'Transparency', 'not found', 'pkId') {
            Write-Host ("  contains {0,-14} {1}" -f "'$w':", (([string]$r.body).IndexOf($w, [StringComparison]::OrdinalIgnoreCase) -ge 0))
        }
        if ($Raw -and $r.kind -eq 'seal') {
            $head = [string]$r.body
            if ($head.Length -gt 1500) { $head = $head.Substring(0, 1500) }
            Write-Host '--- first 1500 chars ---'
            Write-Host $head
            Write-Host '--- end ---'
        }
    }
    if ($JsonOut) {
        $records | Select-Object kind, profileId, url, status, contentType, finalUrl, error, @{ n = 'bytes'; e = { ([string]$_.body).Length } }, @{ n = 'sha256'; e = { Get-CandidBodyDigest -Body $_.body } } |
            ConvertTo-Json -Depth 4 | Set-Content -Path $JsonOut -Encoding utf8
    }
}
