<#
.SYNOPSIS
  Export the LIVE version of every GTM container in the FFC account(s) as JSON backups
  (disaster recovery / restore point for charity self-service containers). Read-only.

.DESCRIPTION
  Uses domain-wide delegation (the ffc-workspace-admin SA impersonating -Subject) with the
  tagmanager.edit.containers scope (read calls only; a *.readonly scope is not in the DWD
  grant list). Writes one <publicId>.json per container plus an index.json into -OutDir.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$KeyPath,
    [Parameter(Mandatory)][string]$Subject,
    [Parameter(Mandatory)][string]$OutDir,
    [string]$Scope = 'https://www.googleapis.com/auth/tagmanager.edit.containers'
)
$ErrorActionPreference = 'Stop'
$base = 'https://www.googleapis.com/tagmanager/v2'

function ConvertTo-B64Url([byte[]]$b) { [Convert]::ToBase64String($b).TrimEnd('=').Replace('+', '-').Replace('/', '_') }
function Get-DwdToken {
    param($Key, $Subject, $Scope)
    $now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    $h = @{ alg = 'RS256'; typ = 'JWT'; kid = $Key.private_key_id }
    $c = @{ iss = $Key.client_email; sub = $Subject; scope = $Scope; aud = $Key.token_uri; iat = $now; exp = $now + 3600 }
    $si = "$(ConvertTo-B64Url ([Text.Encoding]::UTF8.GetBytes(($h|ConvertTo-Json -Compress)))).$(ConvertTo-B64Url ([Text.Encoding]::UTF8.GetBytes(($c|ConvertTo-Json -Compress))))"
    $rsa = [System.Security.Cryptography.RSA]::Create()
    try { $rsa.ImportFromPem($Key.private_key); $sig = $rsa.SignData([Text.Encoding]::ASCII.GetBytes($si), [System.Security.Cryptography.HashAlgorithmName]::SHA256, [System.Security.Cryptography.RSASignaturePadding]::Pkcs1) } finally { $rsa.Dispose() }
    (Invoke-RestMethod -Method Post -Uri $Key.token_uri -ContentType 'application/x-www-form-urlencoded' -Body @{ grant_type = 'urn:ietf:params:oauth:grant-type:jwt-bearer'; assertion = "$si.$(ConvertTo-B64Url $sig)" }).access_token
}

$key = Get-Content $KeyPath -Raw | ConvertFrom-Json
$token = Get-DwdToken -Key $key -Subject $Subject -Scope $Scope
if ($env:GITHUB_ACTIONS -eq 'true') { Write-Host "::add-mask::$token" }
$auth = @{ Authorization = "Bearer $token" }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

function Get-HttpStatus($ErrorRecord) {
    try { return [int]$ErrorRecord.Exception.Response.StatusCode } catch { return 0 }
}

# Tag Manager enforces a 'Queries' quota of 30/min per project+user; the fleet sweep
# outgrew it (2 consecutive weekly 429 failures, #789). Pace every call and retry 429s.
$script:lastCall = [DateTimeOffset]::MinValue
function Invoke-GtmApi {
    param([Parameter(Mandatory)][string]$Uri, [Parameter(Mandatory)][hashtable]$Headers)
    $sinceMs = ([DateTimeOffset]::UtcNow - $script:lastCall).TotalMilliseconds
    if ($sinceMs -lt 2100) { Start-Sleep -Milliseconds (2100 - [int]$sinceMs) }
    $delays = @(30, 60, 120)
    for ($try = 0; ; $try++) {
        try {
            $script:lastCall = [DateTimeOffset]::UtcNow
            return Invoke-RestMethod -Uri $Uri -Headers $Headers
        }
        catch {
            if ((Get-HttpStatus $_) -eq 429 -and $try -lt $delays.Count) {
                Write-Warning "429 from $Uri — retry $($try + 1)/$($delays.Count) in $($delays[$try])s"
                Start-Sleep -Seconds $delays[$try]
                continue
            }
            throw
        }
    }
}

$index = @()
$failed = @()
$accts = (Invoke-GtmApi -Uri "$base/accounts" -Headers $auth).account
foreach ($a in $accts) {
    $containers = (Invoke-GtmApi -Uri "$base/$($a.path)/containers" -Headers $auth).container
    foreach ($c in @($containers)) {
        try {
            $live = Invoke-GtmApi -Uri "$base/$($c.path)/versions:live" -Headers $auth
            $file = Join-Path $OutDir "$($c.publicId).json"
            ($live | ConvertTo-Json -Depth 30) | Set-Content -Path $file -Encoding utf8
            $index += [ordered]@{
                publicId = $c.publicId; name = $c.name; account = $a.name
                versionId = $live.containerVersionId; file = "$($c.publicId).json"
            }
            Write-Host "BACKED_UP $($c.publicId) ($($c.name)) v$($live.containerVersionId)"
        }
        catch {
            if ((Get-HttpStatus $_) -eq 404) {
                # Genuinely unpublished container — expected for not-yet-live charities.
                Write-Warning "No live version for $($c.publicId) ($($c.name)) — skipped"
                $index += [ordered]@{ publicId = $c.publicId; name = $c.name; account = $a.name; versionId = $null; file = $null }
            }
            else {
                Write-Warning "FAILED $($c.publicId) ($($c.name)): $($_.Exception.Message)"
                $failed += "$($c.publicId) ($($c.name))"
                $index += [ordered]@{ publicId = $c.publicId; name = $c.name; account = $a.name; versionId = $null; file = $null; error = $_.Exception.Message }
            }
        }
    }
}
@{ generatedAt = [DateTimeOffset]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ'); containers = $index } |
    ConvertTo-Json -Depth 5 | Set-Content (Join-Path $OutDir 'index.json') -Encoding utf8
Write-Host "RESULT containers=$($index.Count) failed=$($failed.Count) outDir=$OutDir"
if ($failed.Count -gt 0) {
    # A partial backup must not report success — name what was missed and fail at the end.
    Write-Error "Backup incomplete: $($failed.Count) container(s) failed after retries: $($failed -join ', ')"
    exit 1
}
