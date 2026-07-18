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

$index = @()
$accts = (Invoke-RestMethod -Uri "$base/accounts" -Headers $auth).account
foreach ($a in $accts) {
    $containers = (Invoke-RestMethod -Uri "$base/$($a.path)/containers" -Headers $auth).container
    foreach ($c in @($containers)) {
        try {
            $live = Invoke-RestMethod -Uri "$base/$($c.path)/versions:live" -Headers $auth
            $file = Join-Path $OutDir "$($c.publicId).json"
            ($live | ConvertTo-Json -Depth 30) | Set-Content -Path $file -Encoding utf8
            $index += [ordered]@{
                publicId = $c.publicId; name = $c.name; account = $a.name
                versionId = $live.containerVersionId; file = "$($c.publicId).json"
            }
            Write-Host "BACKED_UP $($c.publicId) ($($c.name)) v$($live.containerVersionId)"
        }
        catch {
            Write-Warning "No live version for $($c.publicId) ($($c.name)): $($_.Exception.Message)"
            $index += [ordered]@{ publicId = $c.publicId; name = $c.name; account = $a.name; versionId = $null; file = $null }
        }
    }
}
@{ generatedAt = [DateTimeOffset]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ'); containers = $index } |
    ConvertTo-Json -Depth 5 | Set-Content (Join-Path $OutDir 'index.json') -Encoding utf8
Write-Host "RESULT containers=$($index.Count) outDir=$OutDir"
