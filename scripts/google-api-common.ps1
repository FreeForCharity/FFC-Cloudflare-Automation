<#
.SYNOPSIS
  Shared helpers for FFC Google API automation. Mints an OAuth2 access token from a Google
  service-account key using the JWT-bearer flow, entirely in-process (.NET RSA) so no gcloud / Python
  / extra GitHub Action is required on the runner.

.DESCRIPTION
  Companion to whmcs-api-common.ps1 / zeffy-api-common.ps1. The service-account key is supplied as
  Application Default Credentials: GOOGLE_APPLICATION_CREDENTIALS points at the JSON key file written
  by the .github/actions/google-secrets-from-kv composite action (Key Vault is the source of truth).

  Dot-source this file, then call Get-GoogleAccessToken / Invoke-GoogleApi.

  SAFETY: this file never writes the credential or token to disk or logs. Callers must mask any token
  they surface. Read-only by default (the analytics.readonly scope).
#>

Set-StrictMode -Version Latest

function ConvertTo-Base64Url {
  param([Parameter(Mandatory)][byte[]]$Bytes)
  [Convert]::ToBase64String($Bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

function Resolve-GoogleCredentials {
  <#
    Returns the parsed SA key object from GOOGLE_APPLICATION_CREDENTIALS (or an explicit path).
    Throws a clear error if the credential is missing or malformed.
  #>
  param([string]$CredentialsPath)

  if ([string]::IsNullOrWhiteSpace($CredentialsPath)) {
    $CredentialsPath = $env:GOOGLE_APPLICATION_CREDENTIALS
  }
  if ([string]::IsNullOrWhiteSpace($CredentialsPath)) {
    throw 'GOOGLE_APPLICATION_CREDENTIALS is not set. Run the google-secrets-from-kv action first (or pass -CredentialsPath).'
  }
  if (-not (Test-Path -Path $CredentialsPath)) {
    throw "Google credentials file not found at '$CredentialsPath'."
  }

  $raw = Get-Content -Path $CredentialsPath -Raw
  try {
    $key = $raw | ConvertFrom-Json -ErrorAction Stop
  } catch {
    throw "Google credentials file '$CredentialsPath' is not valid JSON."
  }
  foreach ($f in @('client_email', 'private_key', 'token_uri')) {
    if ([string]::IsNullOrWhiteSpace($key.$f)) {
      throw "Google credentials file is missing required field '$f'."
    }
  }
  return $key
}

function Get-GoogleAccessToken {
  <#
    Mints a short-lived OAuth2 access token for the given scope via the signed-JWT bearer grant.
    .PARAMETER Scope  One or more OAuth scopes (space-separated). Defaults to read-only Analytics.
  #>
  param(
    [string]$Scope = 'https://www.googleapis.com/auth/analytics.readonly',
    [string]$CredentialsPath
  )

  $key = Resolve-GoogleCredentials -CredentialsPath $CredentialsPath

  $now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
  $header = @{ alg = 'RS256'; typ = 'JWT'; kid = $key.private_key_id }
  $claims = @{
    iss   = $key.client_email
    scope = $Scope
    aud   = $key.token_uri
    iat   = $now
    exp   = $now + 3600
  }

  $headerB64 = ConvertTo-Base64Url ([Text.Encoding]::UTF8.GetBytes(($header | ConvertTo-Json -Compress)))
  $claimsB64 = ConvertTo-Base64Url ([Text.Encoding]::UTF8.GetBytes(($claims | ConvertTo-Json -Compress)))
  $signingInput = "$headerB64.$claimsB64"

  $rsa = [System.Security.Cryptography.RSA]::Create()
  try {
    # private_key is PEM (BEGIN PRIVATE KEY) with real newlines after JSON parsing.
    $rsa.ImportFromPem($key.private_key)
    $sigBytes = $rsa.SignData(
      [Text.Encoding]::ASCII.GetBytes($signingInput),
      [System.Security.Cryptography.HashAlgorithmName]::SHA256,
      [System.Security.Cryptography.RSASignaturePadding]::Pkcs1
    )
  } finally {
    $rsa.Dispose()
  }
  $jwt = "$signingInput.$(ConvertTo-Base64Url $sigBytes)"

  $body = @{
    grant_type = 'urn:ietf:params:oauth:grant-type:jwt-bearer'
    assertion  = $jwt
  }

  try {
    $resp = Invoke-RestMethod -Method Post -Uri $key.token_uri -Body $body -ContentType 'application/x-www-form-urlencoded' -ErrorAction Stop
  } catch {
    throw "Google token exchange failed: $($_.Exception.Message). Check that the SA has access to the target resource and the requested scope is enabled."
  }
  if ([string]::IsNullOrWhiteSpace($resp.access_token)) {
    throw 'Google token exchange returned no access_token.'
  }
  # Mask in CI so the bearer token never lands in logs.
  if ($env:GITHUB_ACTIONS -eq 'true') { Write-Host "::add-mask::$($resp.access_token)" }
  return $resp.access_token
}

function Invoke-GoogleApi {
  <# Thin REST wrapper that attaches the bearer token and returns parsed JSON. #>
  param(
    [Parameter(Mandatory)][string]$Uri,
    [string]$Method = 'GET',
    [object]$Body,
    [Parameter(Mandatory)][string]$AccessToken
  )
  $headers = @{ Authorization = "Bearer $AccessToken" }
  $params = @{ Method = $Method; Uri = $Uri; Headers = $headers; ErrorAction = 'Stop' }
  if ($null -ne $Body) {
    $params.Body = ($Body | ConvertTo-Json -Depth 20)
    $params.ContentType = 'application/json'
  }
  return Invoke-RestMethod @params
}
