<#
.SYNOPSIS
    Wire provisioned GA4 / GTM ids into a checked-out FFC-EX website repo.

.DESCRIPTION
    Pure file-manipulation half of workflow 704 (Website - Analytics Wire).
    Detects the site type of the target repo working tree and injects the
    analytics ids:

      - template  : Next.js FFC template site (package.json + src/). Ensures
                    src/lib/analytics.config.ts carries the ids and that
                    src/components/google-tag-manager/index.tsx reads the GTM
                    id from that config instead of a hardcoded literal.
      - static    : WordPress static export (*.html, no package.json). Injects
                    the standard GTM head <script> + body <noscript> on every
                    page and replaces any existing (non-FFC) GTM id.

    Idempotent: a tree already carrying the ids is left unchanged. No git or
    network operations are performed here - the workflow clones the repo and
    opens the PR. Emits a JSON summary on stdout.

.NOTES
    GTM/GA ids are public client-side identifiers, not secrets.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RepoDir,

    [Parameter(Mandatory = $true)]
    [string]$Domain,

    [Parameter(Mandatory = $true)]
    [string]$GtmId,

    [Parameter()]
    [string]$MeasurementId = ''
)

$ErrorActionPreference = 'Stop'

if ($GtmId -notmatch '^GTM-[A-Z0-9]{5,9}$') {
    throw "Invalid GtmId '$GtmId' (expected GTM-XXXXXXX)."
}
if ($MeasurementId -and $MeasurementId -notmatch '^G-[A-Z0-9]{6,12}$') {
    throw "Invalid MeasurementId '$MeasurementId' (expected G-XXXXXXXXXX)."
}
if (-not (Test-Path -LiteralPath $RepoDir)) {
    throw "RepoDir '$RepoDir' does not exist."
}

$changed = [System.Collections.Generic.List[string]]::new()

# Record a repo-relative changed path once (single source of the bookkeeping).
function Add-ChangedFile {
    param([string]$Path)
    $rel = $Path.Substring($RepoDir.Length).TrimStart('/', '\')
    if (-not $changed.Contains($rel)) { $changed.Add($rel) }
}

# Write $Content to $Path only if it differs; record the repo-relative path.
function Set-FileIfChanged {
    param([string]$Path, [string]$Content)
    # Guarantee exactly one trailing newline (prettier requires it).
    $Content = ($Content -replace "`r`n", "`n").TrimEnd("`n") + "`n"
    $existing = if (Test-Path -LiteralPath $Path) { Get-Content -LiteralPath $Path -Raw } else { $null }
    # Normalize to LF so a CRLF-only delta is not treated as a change.
    $norm = { param($s) if ($null -eq $s) { $null } else { $s -replace "`r`n", "`n" } }
    if ((& $norm $existing) -eq (& $norm $Content)) { return $false }
    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    # Write LF, UTF-8 no BOM (matches the template repos + prettier).
    [System.IO.File]::WriteAllText($Path, ($Content -replace "`r`n", "`n"), (New-Object System.Text.UTF8Encoding($false)))
    Add-ChangedFile $Path
    return $true
}

$pkg = Join-Path $RepoDir 'package.json'
$srcDir = Join-Path $RepoDir 'src'
$siteType = 'unknown'

if ((Test-Path -LiteralPath $pkg) -and (Test-Path -LiteralPath $srcDir)) {
    $siteType = 'template'
}
elseif (Get-ChildItem -LiteralPath $RepoDir -Filter '*.html' -File -ErrorAction SilentlyContinue | Select-Object -First 1) {
    $siteType = 'static'
}

switch ($siteType) {
    'template' {
        $meas = if ($MeasurementId) { $MeasurementId } else { 'G-XXXXXXXXXX' }
        $configPath = Join-Path $srcDir 'lib/analytics.config.ts'

        if (Test-Path -LiteralPath $configPath) {
            # Existing config (newer template): update the ids in place.
            $c = Get-Content -LiteralPath $configPath -Raw
            $c = [regex]::Replace($c, "gtmId:\s*'[^']*'", "gtmId: '$GtmId'")
            if ($MeasurementId) {
                $c = [regex]::Replace($c, "gaMeasurementId:\s*'[^']*'", "gaMeasurementId: '$MeasurementId'")
            }
            Set-FileIfChanged -Path $configPath -Content $c | Out-Null
        }
        else {
            # Older template: create the config file (mirrors the current template shape).
            $cfg = @"
// Analytics & tracking IDs - the single place to change them.
//
// These are NOT secrets. They are public, client-side identifiers baked into
// the static export and visible in page source anyway. They live here so a
// forking charity - or an automated assistant - can point the site at its own
// accounts by editing this one file. Provisioned by FFC workflow 704.
export const analyticsConfig = {
  // Google Tag Manager container ID, e.g. 'GTM-ABC1234'.
  gtmId: '$GtmId',

  // Google Analytics 4 measurement ID, e.g. 'G-ABC1234567'. The GA4 tag itself
  // fires inside the GTM container; this is kept for reference/components.
  gaMeasurementId: '$meas',

  // Meta (Facebook) Pixel ID.
  metaPixelId: 'XXXXXXXXXXXXXXX',

  // Microsoft Clarity project ID.
  clarityProjectId: 'XXXXXXXX',
} as const
"@
            Set-FileIfChanged -Path $configPath -Content $cfg | Out-Null
        }

        # Rewire the GTM component to read the id from config (no-op if already wired).
        $gtmComp = Join-Path $srcDir 'components/google-tag-manager/index.tsx'
        if (Test-Path -LiteralPath $gtmComp) {
            $t = Get-Content -LiteralPath $gtmComp -Raw
            if ($t -notmatch [regex]::Escape("from '@/lib/analytics.config'")) {
                $t = [regex]::Replace(
                    $t,
                    "(import Script from 'next/script'\r?\n)",
                    "`$1import { analyticsConfig } from '@/lib/analytics.config'`n"
                )
            }
            $t = [regex]::Replace($t, "const GTM_ID = '[^']*'", "const GTM_ID = analyticsConfig.gtmId")
            Set-FileIfChanged -Path $gtmComp -Content $t | Out-Null
        }
    }

    'static' {
        $head = @"
    <!-- Google Tag Manager -->
    <script>
      ;(function (w, d, s, l, i) {
        w[l] = w[l] || []
        w[l].push({ 'gtm.start': new Date().getTime(), event: 'gtm.js' })
        var f = d.getElementsByTagName(s)[0],
          j = d.createElement(s),
          dl = l != 'dataLayer' ? '&l=' + l : ''
        j.async = true
        j.src = 'https://www.googletagmanager.com/gtm.js?id=' + i + dl
        f.parentNode.insertBefore(j, f)
      })(window, document, 'script', 'dataLayer', '$GtmId')
    </script>
    <!-- End Google Tag Manager -->
"@
        $body = @"
    <!-- Google Tag Manager (noscript) --><noscript><iframe src="https://www.googletagmanager.com/ns.html?id=$GtmId" height="0" width="0" style="display:none;visibility:hidden"></iframe></noscript><!-- End Google Tag Manager (noscript) -->
"@
        # Snippets authored with LF; matched to each file's own EOL at inject time.
        $headLf = ($head -replace "`r`n", "`n")
        $bodyLf = ($body -replace "`r`n", "`n")
        $pages = Get-ChildItem -LiteralPath $RepoDir -Recurse -Filter '*.html' -File -ErrorAction SilentlyContinue
        foreach ($p in $pages) {
            $h = Get-Content -LiteralPath $p.FullName -Raw
            $orig = $h
            # Preserve this file's existing newline convention so we never leave mixed CRLF/LF
            # (WordPress exports vary; rewriting all endings would blow up the diff).
            $eol = if ($h -match "`r`n") { "`r`n" } else { "`n" }
            $headBlock = $eol + ($headLf -replace "`n", $eol)
            $bodyBlock = $eol + ($bodyLf -replace "`n", $eol)
            # Point any existing GTM id (e.g. a leftover non-FFC container) at ours.
            $h = [regex]::Replace($h, 'GTM-[A-Z0-9]{5,9}', $GtmId)
            # Inject the head snippet once (skip if a GTM loader is already present).
            # Script-block replacement avoids '$' in the snippet being read as a backreference.
            if ($h -notmatch [regex]::Escape('googletagmanager.com/gtm.js')) {
                $h = [regex]::Replace($h, '(<head[^>]*>)', { param($m) $m.Groups[1].Value + $headBlock }, 1)
            }
            # Inject the noscript once.
            if ($h -notmatch [regex]::Escape('googletagmanager.com/ns.html')) {
                $h = [regex]::Replace($h, '(<body[^>]*>)', { param($m) $m.Groups[1].Value + $bodyBlock }, 1)
            }
            if ($h -ne $orig) {
                # Write bytes as-is (no LF normalization) to keep the file's EOL convention;
                # bookkeeping via the shared Add-ChangedFile helper.
                [System.IO.File]::WriteAllText($p.FullName, $h, (New-Object System.Text.UTF8Encoding($false)))
                Add-ChangedFile $p.FullName
            }
        }
    }

    default {
        throw "Could not classify site type for '$Domain' (no package.json+src/ and no *.html)."
    }
}

$summary = [ordered]@{
    domain        = $Domain
    siteType      = $siteType
    gtmId         = $GtmId
    measurementId = if ($MeasurementId) { $MeasurementId } else { '' }
    changed       = ($changed.Count -gt 0)
    changedCount  = $changed.Count
    changedFiles  = @($changed | Select-Object -First 25)
}
$summary | ConvertTo-Json -Depth 5
