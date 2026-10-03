<#
.SYNOPSIS
    Writes a charity's details into a newly provisioned FFC-EX-<domain> site
    (701's content step). A field the charity did not provide is emptied --
    never left at the template's Free For Charity value -- and listed in
    siteConfig.pending where the template supports it.

.PARAMETER FooterPhone
    A US number in any common form ("(555) 010-0101", "555-010-0101",
    "+1 555 010 0303"). Shown as given; the tel: link uses its digits, with a
    leading + only when the input has one. Anything else is treated as missing.

.PARAMETER FooterAddress
    One address, one visual line per line. Separate lines with real line
    breaks (PowerShell: "12 Main St`nSpringfield, IL 62701") or with a literal
    backslash-n, which is what a bash caller passes without $'...' quoting:
        -FooterAddress '12 Main St\nSpringfield, IL 62701'
    A literal "\r\n" works too. Commas do NOT split lines: they belong to an
    ordinary line such as "Springfield, IL 62701". A one-line address
    ("St. Petersburg, FL") is a single footer line.

.PARAMETER GuideStarProfileUrl
    The charity's own Candid / GuideStar profile URL (https). Never derived
    from the EIN: the footer's seal claims a Candid transparency level, so only
    a URL the charity publishes is used. Blank -> empty and pending.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RepoPath,

    [Parameter(Mandatory = $true)]
    [string]$Domain,

    [Parameter(Mandatory = $true)]
    [string]$CharityName,

    # Blank = pending (see siteConfig.pending below), never the template's.
    [string]$FooterEmail,

    [string]$FooterPhone,

    [string]$FooterAddress,

    [string]$FooterEin,

    [string]$GuideStarProfileUrl,

    [string]$GuideStarDirectProfileUrl,

    [string[]]$FooterSocial = @(),

    [string[]]$LeadershipLines = @(),

    # One-sentence mission, shown under the charity name in the footer of the
    # config-driven templates. Blank falls back to a generic sentence naming
    # the charity, never to the template's own (FFC) mission.
    [string]$Mission,

    # https URLs for the footer Donate / Volunteer links. Blank (or not https)
    # leaves the template's fallback in place: a mailto: to the contact email.
    [string]$DonationUrl,

    [string]$VolunteerUrl,

    # 701's IRS status value. Drives siteConfig.taxStatusLabel: the footer's
    # "a US 501c3 Non Profit" clause and the donation policy's deductibility
    # sentence are legal claims, made only for a recognized 501(c)(3).
    [string]$IrsStatus,

    # Optional. When set, a JSON summary is written here:
    #   { pendingFields: [...], pendingRendered: true|false }
    # pendingFields names every footer-standard field the charity did not
    # provide (siteConfig `PendingField` vocabulary); pendingRendered says
    # whether the template renders them as visible placeholders. 701 records
    # both in ffc-content.json, its completion comment and a call-to-action
    # issue on the new repo.
    [string]$SummaryPath
)

$ErrorActionPreference = 'Stop'

# Footer-standard fields the charity did not provide ("pending"). Each one is
# EMPTIED -- never left at the template's value, which is Free For Charity's
# own (EIN, phone, offices, GuideStar profile, social links, staff) -- AND
# listed in siteConfig.pending, which the templates render as a visible
# "Awaiting information from the charity" placeholder: a call to action, not a
# silent gap in the footer standard. An empty value NOT listed in `pending`
# means "the charity has none", which 701 can never know, so every field it
# could not fill is listed. Names are the templates' `PendingField` union.
$script:PendingFields = New-Object System.Collections.Generic.List[string]
function Add-PendingField {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('email', 'phone', 'address', 'ein', 'guidestar', 'social', 'team', 'donationUrl', 'volunteerUrl')]
        [string]$Name
    )
    if (-not $script:PendingFields.Contains($Name)) { $script:PendingFields.Add($Name) }
}
$script:PendingRendered = $false

function Assert-FileExists {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Required file not found: $Path"
    }
}

function Get-TelDigits {
    param([string]$Phone)
    if ([string]::IsNullOrWhiteSpace($Phone)) { return $null }
    $digits = ($Phone -replace '[^0-9]', '')
    if ($digits.Length -eq 10) { return ('1' + $digits) }
    if ($digits.Length -eq 11 -and $digits.StartsWith('1')) { return $digits }
    return $null
}

function Get-PublishedTel {
    # The tel: value for a published number: its digits, with a leading + only
    # if the input began with one (after whitespace).
    param([string]$Phone)
    if ([string]::IsNullOrWhiteSpace($Phone)) { return '' }
    $digits = ($Phone -replace '[^0-9]', '')
    if ($Phone.TrimStart().StartsWith('+')) { return "+$digits" }
    return $digits
}

function Split-AddressLine {
    # One entry per visual line. Accepts real line breaks (CRLF / LF / CR) and
    # their literal escaped forms ("\r\n", "\n"), which is what a bash caller or
    # a JSON-ish issue-form value hands over. A comma is NOT a line break: it is
    # part of an ordinary line ("Springfield, IL 62701"), so splitting on it
    # would break every US city/state line.
    param([string]$Address)
    if ([string]::IsNullOrWhiteSpace($Address)) { return @() }
    return @($Address -split '\r\n|\n|\r|\\r\\n|\\n' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}

function Convert-AddressToHtml {
    param([string]$Address)
    if ([string]::IsNullOrWhiteSpace($Address)) { return $null }

    $lines = @()
    foreach ($line in ($Address -split "`r`n|`n")) {
        $t = if ($null -eq $line) { '' } else { $line.Trim() }
        if (-not [string]::IsNullOrWhiteSpace($t)) {
            $lines += $t
        }
    }

    if ($lines.Count -eq 0) { return $null }
    return ($lines -join "`n                  <br />`n                  ")
}

function Convert-AddressToMapsQuery {
    param([string]$Address)
    if ([string]::IsNullOrWhiteSpace($Address)) { return $null }
    return [Uri]::EscapeDataString(($Address -replace "`r`n|`n", ' ').Trim())
}

function Update-FooterComponent {
    param(
        [Parameter(Mandatory = $true)][string]$FooterFile,
        [Parameter(Mandatory = $true)][string]$Email,
        [string]$Phone,
        [string]$Address,
        [string]$Ein,
        [string]$GuideStarProfileUrl,
        [string]$GuideStarDirectProfileUrl,
        [string[]]$Social,
        [Parameter(Mandatory = $true)][string]$Domain,
        [Parameter(Mandatory = $true)][string]$CharityName
    )

    Assert-FileExists -Path $FooterFile

    $text = Get-Content -LiteralPath $FooterFile -Raw -Encoding utf8

    # Email (mailto + visible)
    $text = $text -replace 'href="mailto:[^"]+"', ('href="mailto:{0}"' -f $Email)
    $text = $text -replace '(?<![\w.-])([\w.+-]+@[\w.-]+\.[A-Za-z]{2,})(?![\w.-])', $Email

    # Phone (tel + visible)
    $telDigits = Get-TelDigits -Phone $Phone
    if ($telDigits) {
        $text = $text -replace 'href="tel:[0-9]+"', ('href="tel:{0}"' -f $telDigits)

        # Replace the first visible phone number near the Call Us Today block.
        # (Conservative: only update numbers with 7+ digits)
        $text = [regex]::Replace(
            $text,
            '(?s)(<p[^>]*>\s*Call Us Today\s*</p>\s*<a[^>]*>)(.*?)(</a>)',
            {
                param($m)
                $display = if ([string]::IsNullOrWhiteSpace($Phone)) { $m.Groups[2].Value } else { $Phone }
                return $m.Groups[1].Value + $display + $m.Groups[3].Value
            },
            1
        )
    }

    # Main address (map link + visible lines)
    if (-not [string]::IsNullOrWhiteSpace($Address)) {
        $addrHtml = Convert-AddressToHtml -Address $Address
        $addrQuery = Convert-AddressToMapsQuery -Address $Address

        if ($addrQuery) {
            $text = [regex]::Replace(
                $text,
                '(href="https://www\.google\.com/maps/search/\?api=1&query=)([^"]+)(")',
                ('$1' + $addrQuery + '$3'),
                1
            )
        }

        if ($addrHtml) {
            # Replace only the Main Address block's <p id="aria-font"> inner text.
            $text = [regex]::Replace(
                $text,
                '(?s)(<p className="font-\[500\] text-\[22px\]">Main Address</p>\s*<p className="font-\[500\] text-\[16px\]" id="aria-font">)(.*?)(</p>)',
                ('$1' + $addrHtml + '$3'),
                1
            )
        }
    }

    # EIN display
    if (-not [string]::IsNullOrWhiteSpace($Ein)) {
        $einText = "$CharityName EIN: $Ein"
        $text = [regex]::Replace(
            $text,
            '(?s)(<span className="font-\[500\] text-\[22px\]">)(.*?EIN:.*?)(</span>)',
            {
                param($m)
                return $m.Groups[1].Value + $einText + $m.Groups[3].Value
            },
            1
        )
    }

    # GuideStar / Candid endorsements
    # Template contains Free For Charity's links. For partner sites:
    # - If URLs provided, replace them.
    # - If URLs blank, remove the endorsement link/button to avoid wrong links.
    if ([string]::IsNullOrWhiteSpace($GuideStarProfileUrl)) {
        $text = [regex]::Replace(
            $text,
            '(?s)<a\s+[^>]*href="https://www\.guidestar\.org/profile/[^\"]+"[^>]*>.*?</a>\s*',
            '',
            1
        )
    }
    else {
        $text = [regex]::Replace(
            $text,
            'href="https://www\.guidestar\.org/profile/[^\"]+"',
            ('href="{0}"' -f $GuideStarProfileUrl),
            1
        )
        $text = [regex]::Replace(
            $text,
            'aria-label="View [^\"]* GuideStar Profile"',
            ('aria-label="View {0} GuideStar Profile"' -f $CharityName),
            1
        )
    }

    if ([string]::IsNullOrWhiteSpace($GuideStarDirectProfileUrl)) {
        $text = [regex]::Replace(
            $text,
            '(?s)<Link\s+[^>]*href="https://www\.guidestar\.org/profile/shared/[^\"]+"[^>]*>.*?</Link>\s*',
            '',
            1
        )
    }
    else {
        $text = [regex]::Replace(
            $text,
            'href="https://www\.guidestar\.org/profile/shared/[^\"]+"',
            ('href="{0}"' -f $GuideStarDirectProfileUrl),
            1
        )
    }

    # Make the seal alt text generic (avoid hard-coding a seal level).
    $text = $text -replace 'alt="GuideStar Platinum Seal of Transparency"', 'alt="Candid / GuideStar Seal of Transparency"'

    # Social links ("platform: url"), parsed exactly as the config-driven path
    # and the legacy guard below parse them (Get-SocialEntries), so "X",
    # "Twitter", "X (Twitter)" and "X / Twitter" all reach the X link.
    $map = Get-LegacySocialMap -Social $Social
    foreach ($k in @('facebook', 'x', 'linkedin', 'github')) {
        if (-not $map.ContainsKey($k)) { continue }
        $url = $map[$k]

        switch ($k) {
            'facebook' { $text = $text -replace "href: 'https://www\.facebook\.com/[^']+'", "href: '$url'" }
            'x' { $text = $text -replace "href: 'https://x\.com/[^']+'", "href: '$url'" }
            'linkedin' { $text = $text -replace "href: 'https://www\.linkedin\.com/[^']+'", "href: '$url'" }
            'github' { $text = $text -replace "href: 'https://github\.com/[^']+'", "href: '$url'" }
        }
    }

    # Copyright line: swap out Free For Charity + link target for the new domain.
    $text = $text -replace 'All Rights Are Reserved by Free For Charity', ("All Rights Are Reserved by $CharityName")
    $text = $text -replace 'href="https://freeforcharity\.org"', ('href="https://{0}"' -f $Domain)
    $text = $text -replace '>https://freeforcharity\.org<', ('>https://{0}<' -f $Domain)

    Set-Content -LiteralPath $FooterFile -Value $text -Encoding utf8
}

function Parse-LeadershipLine {
    param([Parameter(Mandatory = $true)][string]$Line)

    $clean = ($Line.Trim() -replace '^[\*-]\s+', '')

    $name = ''
    $title = ''
    $linkedin = ''

    if ($clean -match '\|') {
        # Supported pipe-delimited formats:
        # - Name | Title
        # - Name | Title | LinkedIn
        # - Name | Title | Email | Phone | LinkedIn   (legacy)
        $parts = $clean.Split('|') | ForEach-Object { $_.Trim() }
        $name = if ($parts.Count -ge 1) { $parts[0] } else { '' }
        $title = if ($parts.Count -ge 2) { $parts[1] } else { '' }
        if ($parts.Count -ge 5) {
            $linkedin = $parts[4]
        }
        elseif ($parts.Count -ge 3) {
            $linkedin = $parts[2]
        }
    }
    else {
        # Supported dash format (matches issue template guidance):
        # Role - Name (optional: notes)
        $m = [regex]::Match($clean, '^(?<t>[^-]+?)\s*-\s*(?<n>.+)$')
        if ($m.Success) {
            $title = $m.Groups['t'].Value.Trim()
            $name = $m.Groups['n'].Value.Trim()
        }
        else {
            $name = $clean
        }
    }

    if ([string]::IsNullOrWhiteSpace($name)) { return $null }
    if ([string]::IsNullOrWhiteSpace($title)) { $title = 'Board Member' }

    if ([string]::IsNullOrWhiteSpace($linkedin)) {
        $linkedin = ''
    }
    elseif ($linkedin -notmatch '^https://') {
        $linkedin = ''
    }

    return [pscustomobject]@{
        Name     = $name
        Title    = $title
        LinkedIn = $linkedin
    }
}

function Escape-TsxString {
    param([Parameter(Mandatory = $true)][string]$Value)
    return $Value.Replace('\\', '\\\\').Replace('"', '\\"')
}

function Convert-ToKebabCase {
    param([Parameter(Mandatory = $true)][string]$Value)
    $v = $Value.ToLowerInvariant()
    # Replace non-alphanumerics with hyphen
    $v = [regex]::Replace($v, '[^a-z0-9]+', '-')
    # Collapse and trim
    $v = [regex]::Replace($v, '-{2,}', '-')
    $v = $v.Trim('-')
    if ([string]::IsNullOrWhiteSpace($v)) { return 'member' }
    return $v
}

function New-TeamTs {
    param(
        [Parameter(Mandatory = $true)][pscustomobject[]]$Members
    )

    $imports = New-Object System.Collections.Generic.List[string]
    $vars = New-Object System.Collections.Generic.List[string]

    for ($i = 0; $i -lt $Members.Count; $i++) {
        $var = 'teamMember{0}' -f ($i + 1)
        $file = $Members[$i].File
        $imports.Add("import $var from './team/$file'")
        $vars.Add($var)
    }

    $importsText = ($imports -join "`n")
    $varsText = ($vars -join ', ')

    return @"
// Team member data
// This file imports team member data from JSON files in ./team/ directory
// To edit team members, edit the JSON files directly in src/data/team/

$importsText

export const team = [$varsText]
"@
}

function Update-LeadershipSection {
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][string]$CharityName,
        [string[]]$LeadershipLines
    )

    $teamSectionFile = Join-Path $RepoRoot 'src/components/home-page/TheFreeForCharityTeam/index.tsx'
    $teamDataDir = Join-Path $RepoRoot 'src/data/team'
    $teamIndexFile = Join-Path $RepoRoot 'src/data/team.ts'

    Assert-FileExists -Path $teamSectionFile
    Assert-FileExists -Path $teamIndexFile
    if (-not (Test-Path -LiteralPath $teamDataDir)) {
        throw "Required folder not found: $teamDataDir"
    }

    $members = @()
    foreach ($line in ($LeadershipLines | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })) {
        $m = Parse-LeadershipLine -Line $line
        if ($null -ne $m) { $members += $m }
    }

    if ($members.Count -eq 0) {
        Write-Host 'No leadership lines provided; skipping team/leadership update.' -ForegroundColor Yellow
        return
    }

    # Generate JSON data files + regenerate src/data/team.ts
    $images = @('/Images/member1.webp', '/Images/member2.webp', '/Images/member3.webp', '/Images/member4.webp', '/Images/member5.webp')
    $usedSlugs = @{}

    $generated = @()
    for ($i = 0; $i -lt $members.Count; $i++) {
        $baseSlug = Convert-ToKebabCase -Value $members[$i].Name
        $slug = $baseSlug
        $n = 2
        while ($usedSlugs.ContainsKey($slug)) {
            $slug = "{0}-{1}" -f $baseSlug, $n
            $n++
        }
        $usedSlugs[$slug] = $true

        $fileName = "$slug.json"
        $img = $images[$i % $images.Count]

        $obj = [ordered]@{
            name        = $members[$i].Name
            title       = $members[$i].Title
            imageUrl    = $img
            linkedinUrl = $members[$i].LinkedIn
        }

        $jsonPath = Join-Path $teamDataDir $fileName
        $json = ($obj | ConvertTo-Json -Depth 5)
        Set-Content -LiteralPath $jsonPath -Value $json -Encoding utf8

        $generated += [pscustomobject]@{ File = $fileName }
    }

    $teamTs = New-TeamTs -Members $generated
    Set-Content -LiteralPath $teamIndexFile -Value $teamTs -Encoding utf8

    # Update team section component to render from JSON-driven data
    $newHeading = (Escape-TsxString -Value ("$CharityName Leadership"))
    $teamComponent = @"
import React from 'react'
import TeamMemberCard from '@/components/ui/TeamMemberCard'
import { team } from '@/data/team'

const index = () => {
  const topRow = team.slice(0, 3)
  const bottomRow = team.slice(3)

  return (
    <div id="team" className="py-[50px]">
      <h1
        className="font-[400] text-[40px] lg:text-[48px]  tracking-[0] text-center mx-auto mb-[50px]"
        id="faustina-font"
      >
        $newHeading
      </h1>

      <div className="w-[90%] mx-auto py-[40px]">
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3  items-stretch justify-center mb-[50px] gap-[30px]">
          {topRow.map((m) => (
            <TeamMemberCard
              key={m.name}
              imageUrl={m.imageUrl}
              name={m.name}
              title={m.title}
              linkedinUrl={m.linkedinUrl}
            />
          ))}
        </div>

        {bottomRow.length > 0 && (
          <div className="grid grid-cols-1 md:grid-cols-2 items-center justify-center mt-[40px] gap-[30px]">
            {bottomRow.map((m) => (
              <TeamMemberCard
                key={m.name}
                imageUrl={m.imageUrl}
                name={m.name}
                title={m.title}
                linkedinUrl={m.linkedinUrl}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

export default index
"@

    Set-Content -LiteralPath $teamSectionFile -Value $teamComponent -Encoding utf8
}

# ---- Config-driven templates (src/lib/site.config.ts) ----
# Both current FFC templates (FFC-IN-Footer_Only_Template, the provisioning
# default, and FFC-IN-FFC_Single_Page_Template) render the footer from the
# `siteConfig` object and type team members as { name, role, linkedinUrl }.
# The regex footer patch above targets the older hard-coded footer and matches
# nothing there, and its { title, imageUrl } team JSON fails the TypeScript
# build. For these templates the values are written into siteConfig instead.

function Write-LfFile {
    param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Text)
    # The templates enforce LF (.prettierrc endOfLine) and this runs on
    # windows-latest, where Set-Content would write CRLF.
    $normalized = ($Text -replace "`r`n", "`n")
    if (-not $normalized.EndsWith("`n")) { $normalized += "`n" }
    [System.IO.File]::WriteAllText($Path, $normalized, [System.Text.UTF8Encoding]::new($false))
}

function ConvertTo-TsString {
    param([AllowEmptyString()][string]$Value)
    if ($null -eq $Value) { $Value = '' }
    $v = ($Value -replace "`r`n|`r|`n", ' ').Trim()
    return "'" + $v.Replace('\', '\\').Replace("'", "\'") + "'"
}

function Get-TsScanEnd {
    # Returns the index of the first character at bracket depth 0 that is one
    # of $StopChars, scanning from $Start and skipping strings and comments.
    param([string]$Source, [int]$Start, [char[]]$StopChars)
    $depth = 0
    $i = $Start
    while ($i -lt $Source.Length) {
        $ch = $Source[$i]
        $next = if ($i + 1 -lt $Source.Length) { $Source[$i + 1] } else { [char]0 }
        if ($ch -eq '/' -and $next -eq '/') {
            $i = $Source.IndexOf("`n", $i)
            if ($i -lt 0) { break }
            continue
        }
        if ($ch -eq '/' -and $next -eq '*') {
            $i = $Source.IndexOf('*/', $i + 2)
            if ($i -lt 0) { break }
            $i += 2
            continue
        }
        if ($ch -eq "'" -or $ch -eq '"' -or $ch -eq '`') {
            $i++
            while ($i -lt $Source.Length -and $Source[$i] -ne $ch) {
                if ($Source[$i] -eq '\') { $i++ }
                $i++
            }
            $i++
            continue
        }
        if ($depth -eq 0 -and $StopChars -contains $ch) { return $i }
        if ('([{'.Contains($ch)) { $depth++ }
        elseif (')]}'.Contains($ch)) { $depth-- }
        $i++
    }
    throw 'Unbalanced brackets while scanning src/lib/site.config.ts.'
}

function Get-SiteConfigProperties {
    # Maps each top-level siteConfig key to the [start, end) span of its value.
    param([Parameter(Mandatory = $true)][string]$Source)

    $m = [regex]::Match($Source, 'export const siteConfig\s*(?::\s*SiteConfig)?\s*=\s*\{')
    if (-not $m.Success) {
        throw 'Could not find "export const siteConfig ... = {" in src/lib/site.config.ts.'
    }
    $bodyStart = $m.Index + $m.Length
    $bodyEnd = Get-TsScanEnd -Source $Source -Start $bodyStart -StopChars @('}')

    $props = @{}
    $pos = $bodyStart
    while ($pos -lt $bodyEnd) {
        # Skip whitespace and comments between properties.
        $gap = [regex]::Match($Source.Substring($pos, $bodyEnd - $pos), '^(?:\s+|//[^\n]*|/\*.*?\*/)*', 'Singleline')
        $pos += $gap.Length
        if ($pos -ge $bodyEnd) { break }

        $key = [regex]::Match($Source.Substring($pos, $bodyEnd - $pos), '^([A-Za-z_$][\w$]*)\??\s*:')
        if (-not $key.Success) {
            throw "Unexpected syntax in siteConfig near: $($Source.Substring($pos, [Math]::Min(40, $bodyEnd - $pos)))"
        }
        $valueStart = $pos + $key.Length
        $valueEnd = Get-TsScanEnd -Source $Source -Start $valueStart -StopChars @(',', '}')
        $props[$key.Groups[1].Value] = [pscustomobject]@{ KeyStart = $pos; Start = $valueStart; End = $valueEnd }
        $pos = $valueEnd + 1
    }
    return $props
}

function Set-SiteConfigValue {
    # Replaces one top-level siteConfig value. A key the template does not
    # declare throws, unless -Optional (keys added in newer template versions).
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Key,
        [Parameter(Mandatory = $true)][string]$ValueTs,
        [switch]$Optional
    )
    $props = Get-SiteConfigProperties -Source $Source
    if (-not $props.ContainsKey($Key)) {
        if ($Optional) {
            Write-Warning "siteConfig has no '$Key' key (older template); leaving it out."
            return $Source
        }
        throw "siteConfig has no '$Key' key; the template changed shape. Update Apply-WebsiteReactTemplate.ps1."
    }
    $span = $props[$Key]
    return $Source.Substring(0, $span.Start) + ' ' + $ValueTs + $Source.Substring($span.End)
}

function Remove-SiteConfigValue {
    # Drops an optional top-level key (and its trailing comma) if present.
    param([Parameter(Mandatory = $true)][string]$Source, [Parameter(Mandatory = $true)][string]$Key)
    $props = Get-SiteConfigProperties -Source $Source
    if (-not $props.ContainsKey($Key)) { return $Source }
    $span = $props[$Key]
    $end = $span.End
    if ($Source[$end] -eq ',') { $end++ }
    $lineStart = $Source.LastIndexOf("`n", $span.KeyStart) + 1
    $lineEnd = $Source.IndexOf("`n", $end)
    if ($lineEnd -lt 0) { $lineEnd = $end } else { $lineEnd++ }
    return $Source.Substring(0, $lineStart) + $Source.Substring($lineEnd)
}

function Get-SocialEntries {
    # "platform: https://..." lines -> ordered { label, href } for siteConfig.social.
    param([string[]]$Social)
    $labels = [ordered]@{
        facebook  = 'Facebook'
        x         = 'X (Twitter)'
        twitter   = 'X (Twitter)'
        linkedin  = 'LinkedIn'
        github    = 'GitHub'
        instagram = 'Instagram'
        youtube   = 'YouTube'
    }
    $entries = New-Object System.Collections.Generic.List[object]
    $seen = @{}
    foreach ($line in $Social) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        $clean = ($line.Trim() -replace '^[\*-]\s+', '')
        $m = [regex]::Match($clean, '^(?<k>[A-Za-z /()]+?)\s*:\s*(?<v>https://\S+)$')
        if (-not $m.Success) { continue }
        $k = $m.Groups['k'].Value.Trim().ToLowerInvariant() -replace '\s*/\s*twitter$|\s*\(twitter\)$', ''
        $label = if ($labels.Contains($k)) { $labels[$k] } else { (Get-Culture).TextInfo.ToTitleCase($k) }
        if ($seen.ContainsKey($label)) { continue }
        $seen[$label] = $true
        $entries.Add([pscustomobject]@{ Label = $label; Href = $m.Groups['v'].Value.Trim() })
    }
    return , $entries
}

function Clear-UnguardedIntegration {
    # Single Page template: `siteConfig.integrations` holds FREE FOR CHARITY's
    # own third-party endpoints (its Zeffy endowment form, Idealist page,
    # events Facebook page, application Microsoft Form). Current versions only
    # use them on FFC's own site (`isSupportingOrgSite()`), so a charity's site
    # never renders them and the values are left for the template's own tests.
    # Older versions (e.g. FFC-EX-vcof.org) have no such guard and embed them
    # on every page -- a charity's Donate / Volunteer buttons going to FFC's
    # pages -- so there every integration URL is emptied, and the FFC
    # endowment section ("Support Free For Charity", whose only content is
    # that Zeffy form) is switched off.
    param([Parameter(Mandatory = $true)][string]$Source)
    $props = Get-SiteConfigProperties -Source $Source
    if (-not $props.ContainsKey('integrations')) { return $Source }
    if ($Source -match 'function\s+isSupportingOrgSite\s*\(') { return $Source }

    $span = $props['integrations']
    $value = $Source.Substring($span.Start, $span.End - $span.Start)
    # Every property's string value -> '' (keys and non-string values kept).
    $emptied = [regex]::Replace($value, "(:\s*)'(?:[^'\\]|\\.)*'", { param($m) $m.Groups[1].Value + "''" })
    $Source = $Source.Substring(0, $span.Start) + $emptied + $Source.Substring($span.End)
    Write-Warning "This template embeds siteConfig.integrations on every site (no isSupportingOrgSite guard); Free For Charity's integration URLs were emptied and the FFC endowment section switched off."

    $props = Get-SiteConfigProperties -Source $Source
    if ($props.ContainsKey('sections')) {
        $span = $props['sections']
        $value = $Source.Substring($span.Start, $span.End - $span.Start)
        $value = [regex]::Replace($value, '(\bshowEndowment\s*:\s*)true\b', '${1}false')
        $Source = $Source.Substring(0, $span.Start) + $value + $Source.Substring($span.End)
    }
    return $Source
}

function Get-LegacySocialMap {
    # The legacy footer's four networks, keyed facebook / x / linkedin / github,
    # from the same parse the config-driven path uses (Get-SocialEntries).
    param([string[]]$Social)
    $keyByLabel = @{ 'Facebook' = 'facebook'; 'X (Twitter)' = 'x'; 'LinkedIn' = 'linkedin'; 'GitHub' = 'github' }
    $map = @{}
    foreach ($e in (Get-SocialEntries -Social $Social)) {
        if ($keyByLabel.ContainsKey($e.Label)) { $map[$keyByLabel[$e.Label]] = $e.Href }
    }
    return $map
}

function Update-SiteConfig {
    param(
        [Parameter(Mandatory = $true)][string]$ConfigFile,
        [Parameter(Mandatory = $true)][string]$CharityName,
        [string]$Email,
        [string]$Phone,
        [string]$Address,
        [string]$Ein,
        [string]$GuideStarProfileUrl,
        [string]$GuideStarDirectProfileUrl,
        [string[]]$Social,
        [string]$Mission,
        [string]$DonationUrl,
        [string]$VolunteerUrl,
        [string]$IrsStatus
    )

    $text = Get-Content -LiteralPath $ConfigFile -Raw -Encoding utf8

    # Not a footer-standard pending field: a generic sentence naming the
    # charity is honest, and every page needs a mission line.
    $missionText = if ([string]::IsNullOrWhiteSpace($Mission)) {
        "$CharityName is a nonprofit organization."
    }
    else { ($Mission -replace "`r`n|`r|`n", ' ').Trim() }
    $missionTs = ConvertTo-TsString $missionText

    $text = Set-SiteConfigValue -Source $text -Key 'name' -ValueTs (ConvertTo-TsString $CharityName)
    $text = Set-SiteConfigValue -Source $text -Key 'mission' -ValueTs $missionTs -Optional
    # The template's description is FFC's own; the charity's mission is the
    # honest replacement for the meta and social-card descriptions. The
    # templates' own tests require a <meta description> over 50 characters,
    # and a one-sentence mission is often shorter, so a short one is extended
    # with a sentence naming the charity rather than failing the new repo's CI.
    $description = $missionText
    if ($description.Length -le 50) {
        $description = "$missionText Learn about $CharityName, our team, and how to support our work."
    }
    $text = Set-SiteConfigValue -Source $text -Key 'description' -ValueTs (ConvertTo-TsString $description)
    $text = Set-SiteConfigValue -Source $text -Key 'shortDescription' -ValueTs $missionTs
    # FFC's own tagline ("Reduce Costs, Increase Impact") and keywords ("free
    # hosting", "Microsoft 365") would otherwise title and describe every
    # charity's site.
    $text = Set-SiteConfigValue -Source $text -Key 'tagline' -ValueTs (ConvertTo-TsString 'Nonprofit Organization')
    $keywordsTs = '[' + ((@('nonprofit', 'charity', 'donate', 'volunteer', $CharityName) | ForEach-Object { ConvertTo-TsString $_ }) -join ', ') + ']'
    $text = Set-SiteConfigValue -Source $text -Key 'keywords' -ValueTs $keywordsTs

    # Legal claims follow the IRS status 701 recorded (same anchored test as
    # 701): recognized -> the standard clause; anything else -> none.
    $recognized = $IrsStatus -match '^\s*501\s*\(c\)\s*\(?3\)?'
    $taxLabel = if ($recognized) { 'a US 501c3 Non Profit' } else { '' }
    $text = Set-SiteConfigValue -Source $text -Key 'taxStatusLabel' -ValueTs (ConvertTo-TsString $taxLabel) -Optional
    # Single Page template extras that describe FFC, not the charity: its 2014
    # founding date, and the schema.org 501(c)(3) claim for an org without one.
    $text = Remove-SiteConfigValue -Source $text -Key 'foundingDate'
    if (-not $recognized) { $text = Remove-SiteConfigValue -Source $text -Key 'nonprofitStatus' }
    # A provisioned charity is standalone: the template's "a project of Free
    # For Charity" parentOrg (Single Page template) is FFC's own relationship.
    # FFC attribution stays via the permanent supportedBy key.
    $text = Remove-SiteConfigValue -Source $text -Key 'parentOrg'
    # Every field below that the charity did not provide is EMPTIED (never the
    # template's FFC value) and listed as pending; see Add-PendingField.
    $emailValue = if ([string]::IsNullOrWhiteSpace($Email)) {
        Write-Warning 'No contact email supplied; siteConfig.contactEmail is left empty and pending (never the template address).'
        Add-PendingField 'email'
        ''
    }
    else { $Email.Trim() }
    $text = Set-SiteConfigValue -Source $text -Key 'contactEmail' -ValueTs (ConvertTo-TsString $emailValue)

    foreach ($pair in @(@('donationUrl', $DonationUrl), @('volunteerUrl', $VolunteerUrl))) {
        $url = [string]$pair[1]
        # Only https URLs. Anything else is pending; until it is filled the
        # template's fallback (a mailto: to the contact email) stays usable.
        $url = if ($url -match '^https://\S+$') { $url.Trim() } else { '' }
        if (-not $url) { Add-PendingField $pair[0] }
        $text = Set-SiteConfigValue -Source $text -Key $pair[0] -ValueTs (ConvertTo-TsString $url) -Optional
    }

    # No EIN: empty and pending. Keeping the template's would publish FFC's tax
    # ID (46-2471893) as the charity's -- a false legal claim.
    $einValue = if ([string]::IsNullOrWhiteSpace($Ein)) {
        Write-Warning 'No EIN supplied; siteConfig.ein is left empty and pending (never the template EIN).'
        Add-PendingField 'ein'
        ''
    }
    else { $Ein.Trim() }
    $text = Set-SiteConfigValue -Source $text -Key 'ein' -ValueTs (ConvertTo-TsString $einValue)

    # Get-TelDigits only VALIDATES (a US number, 10 digits or 1 + 10). The
    # tel: value is the number as published, digits only: no country code is
    # invented, and a leading + is kept only when the charity wrote one
    # ("(555) 010-0101" -> 5550100101, "+1 555 010 0303" -> +15550100303).
    $phoneTs = if (Get-TelDigits -Phone $Phone) {
        '{ display: ' + (ConvertTo-TsString $Phone.Trim()) + ', tel: ' + (ConvertTo-TsString (Get-PublishedTel -Phone $Phone)) + ' }'
    }
    else {
        Add-PendingField 'phone'
        "{ display: '', tel: '' }"
    }
    $text = Set-SiteConfigValue -Source $text -Key 'phone' -ValueTs $phoneTs

    # Real or literal ("\n") line breaks; see Split-AddressLine and the
    # -FooterAddress help.
    $addrLines = @(Split-AddressLine -Address $Address)
    $addressesTs = if ($addrLines.Count -gt 0) {
        $mapUrl = 'https://www.google.com/maps/search/?api=1&query=' + (Convert-AddressToMapsQuery -Address ($addrLines -join ' '))
        $linesTs = ($addrLines | ForEach-Object { ConvertTo-TsString $_ }) -join ', '
        "[`n    {`n      label: 'Main Address',`n      lines: [$linesTs],`n      mapUrl: $(ConvertTo-TsString $mapUrl),`n    },`n  ]"
    }
    else {
        # Never the template's Raleigh / State College offices.
        Add-PendingField 'address'
        '[]'
    }
    $text = Set-SiteConfigValue -Source $text -Key 'addresses' -ValueTs $addressesTs

    # Candid / GuideStar. The footer shows a seal whose alt text claims a
    # Candid "Platinum" transparency level, so it only ever links a profile
    # URL the charity itself provided -- never one derived from its EIN (a
    # profile existing is not the seal level the footer claims, and a
    # pre-501(c)(3) org has no profile at all), and never FFC's (46-2471893 /
    # bbbe173a-...), which is what the template ships. No URL given -> both
    # empty, and pending.
    $profileUrl = if ($GuideStarProfileUrl -match '^https://\S+$') { $GuideStarProfileUrl.Trim() } else { '' }
    $directUrl = if ($GuideStarDirectProfileUrl -match '^https://\S+$') { $GuideStarDirectProfileUrl.Trim() } else { $profileUrl }
    if (-not $profileUrl) {
        # A direct link alone still names the charity's own profile.
        $profileUrl = $directUrl
    }
    if (-not $profileUrl) {
        Write-Warning 'No Candid / GuideStar profile for this charity; siteConfig.guidestar is left empty and pending (never the template profile).'
        Add-PendingField 'guidestar'
    }
    $guidestarTs = "{`n    profileUrl: $(ConvertTo-TsString $profileUrl),`n    directProfileUrl: $(ConvertTo-TsString $directUrl),`n  }"
    $text = Set-SiteConfigValue -Source $text -Key 'guidestar' -ValueTs $guidestarTs

    # Only the links the charity gave; none of FFC's (facebook.com/freeforcharity,
    # x.com/freeforcharity1, linkedin.com/company/freeforcharity, the template repo).
    $socialEntries = Get-SocialEntries -Social $Social
    $socialTs = if ($socialEntries.Count -gt 0) {
        "[`n" + (($socialEntries | ForEach-Object {
                    "    { label: $(ConvertTo-TsString $_.Label), href: $(ConvertTo-TsString $_.Href) },"
                }) -join "`n") + "`n  ]"
    }
    else {
        Add-PendingField 'social'
        '[]'
    }
    $text = Set-SiteConfigValue -Source $text -Key 'social' -ValueTs $socialTs

    $text = Clear-UnguardedIntegration -Source $text

    $xLink = $socialEntries | Where-Object { $_.Label -eq 'X (Twitter)' } | Select-Object -First 1
    $handle = if ($xLink) { [regex]::Match($xLink.Href, '^https://(?:www\.)?(?:x|twitter)\.com/@?(?<h>[A-Za-z0-9_]{1,15})(?:[/?#]|$)').Groups['h'].Value } else { '' }
    $text = Set-SiteConfigValue -Source $text -Key 'twitterHandle' -ValueTs (ConvertTo-TsString ($(if ($handle) { "@$handle" } else { '' })))

    Write-LfFile -Path $ConfigFile -Text $text
}

function Test-SiteConfigDeclaresPending {
    # True when the repo's `SiteConfig` type declares the optional `pending`
    # key (templates from FFC-IN-Footer_Only_Template#169 /
    # FFC-IN-FFC_Single_Page_Template#482 on). Writing the key into a template
    # whose type lacks it would fail the TypeScript build.
    param([Parameter(Mandatory = $true)][string]$Source)
    $m = [regex]::Match($Source, 'export type SiteConfig\s*=\s*\{')
    if (-not $m.Success) { return $false }
    $start = $m.Index + $m.Length
    $end = Get-TsScanEnd -Source $Source -Start $start -StopChars @('}')
    $typeBody = $Source.Substring($start, $end - $start)
    # Only top-level members: strip nested { ... } blocks and comments first.
    $flat = [regex]::Replace($typeBody, '(?s)/\*.*?\*/|//[^\n]*', '')
    while ($flat -match '\{[^{}]*\}') { $flat = [regex]::Replace($flat, '\{[^{}]*\}', '') }
    return [regex]::IsMatch($flat, '(?m)^\s*pending\??\s*:')
}

function Update-SiteConfigPending {
    # Writes siteConfig.pending (or removes it when nothing is pending, e.g. a
    # re-run after the charity supplied everything). Returns whether the
    # template renders the placeholders.
    param([Parameter(Mandatory = $true)][string]$ConfigFile, [string[]]$Pending = @())

    $text = Get-Content -LiteralPath $ConfigFile -Raw -Encoding utf8
    if (-not (Test-SiteConfigDeclaresPending -Source $text)) {
        if ($Pending.Count -gt 0) {
            Write-Warning ("This template's SiteConfig has no 'pending' key yet (FFC-IN-Footer_Only_Template#169 / FFC-IN-FFC_Single_Page_Template#482), so the footer cannot show an 'awaiting information' placeholder. The fields are still emptied (never FFC's values) and recorded for 701: {0}." -f ($Pending -join ', '))
        }
        return $false
    }

    $props = Get-SiteConfigProperties -Source $text
    $pendingComment = "  // Footer-standard fields still awaiting the charity; each renders a visible`n  // 'awaiting information' placeholder until it is filled in.`n"
    if ($Pending.Count -eq 0) {
        $text = Remove-SiteConfigValue -Source $text -Key 'pending'
        $text = $text.Replace($pendingComment, '')
    }
    else {
        $valueTs = '[' + (($Pending | ForEach-Object { ConvertTo-TsString $_ }) -join ', ') + ']'
        if ($props.ContainsKey('pending')) {
            $text = Set-SiteConfigValue -Source $text -Key 'pending' -ValueTs $valueTs
        }
        else {
            # Append as the last property of the literal.
            $last = $props.Values | Sort-Object End -Descending | Select-Object -First 1
            if ($last -and $text[$last.End] -ne ',') {
                $text = $text.Substring(0, $last.End) + ',' + $text.Substring($last.End)
            }
            $m = [regex]::Match($text, 'export const siteConfig\s*(?::\s*SiteConfig)?\s*=\s*\{')
            if (-not $m.Success) {
                throw 'Could not find "export const siteConfig ... = {" in src/lib/site.config.ts while writing pending.'
            }
            $bodyEnd = Get-TsScanEnd -Source $text -Start ($m.Index + $m.Length) -StopChars @('}')
            $lineStart = $text.LastIndexOf("`n", $bodyEnd - 1) + 1
            $at = if ([string]::IsNullOrWhiteSpace($text.Substring($lineStart, $bodyEnd - $lineStart))) { $lineStart } else { $bodyEnd }
            $line = $pendingComment + "  pending: $valueTs,`n"
            if ($at -eq $bodyEnd) { $line = "`n" + $line }
            $text = $text.Substring(0, $at) + $line + $text.Substring($at)
        }
    }
    Write-LfFile -Path $ConfigFile -Text $text
    return $true
}

function Update-SecurityTxtContact {
    # The templates' drift check requires security.txt's Contact line to match
    # siteConfig.contactEmail. Other fields are left as the template ships them.
    param([Parameter(Mandatory = $true)][string]$RepoRoot, [Parameter(Mandatory = $true)][string]$Email)
    foreach ($rel in @('public/security.txt', 'public/.well-known/security.txt')) {
        $path = Join-Path $RepoRoot $rel
        if (-not (Test-Path -LiteralPath $path)) { continue }
        $body = Get-Content -LiteralPath $path -Raw -Encoding utf8
        # A MatchEvaluator, not a replacement string: "$&", "$1" or "$0" in an
        # address would otherwise be read as substitutions.
        $contactLine = "Contact: mailto:$Email"
        $updated = [regex]::Replace($body, '(?m)^Contact:\s*mailto:\S+', { param($m) $contactLine })
        Write-LfFile -Path $path -Text $updated
    }
}

function Update-TeamData {
    # Config-driven templates: team members are { name, role, linkedinUrl }
    # JSON files aggregated by src/data/team.ts; the component reads them, so
    # it is left untouched.
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [string[]]$LeadershipLines
    )

    $teamDataDir = Join-Path $RepoRoot 'src/data/team'
    $teamIndexFile = Join-Path $RepoRoot 'src/data/team.ts'
    Assert-FileExists -Path $teamIndexFile
    if (-not (Test-Path -LiteralPath $teamDataDir)) { throw "Required folder not found: $teamDataDir" }

    $members = @()
    foreach ($line in ($LeadershipLines | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })) {
        $m = Parse-LeadershipLine -Line $line
        if ($null -ne $m) { $members += $m }
    }
    # No usable lines: an EMPTY team, pending -- never the template's sample
    # members, who are FFC's own people; publishing them as the charity's
    # leadership is a false claim.
    if ($members.Count -eq 0) {
        Write-Warning 'No usable leadership lines (each needs a name); the team is left empty and pending (never the template team).'
        Add-PendingField 'team'
    }

    # One card per person. Small boards often give one person two offices
    # ("President" and "Secretary"); two cards with the same name fail the
    # templates' duplicate-name test and React's key uniqueness, so the roles
    # are merged instead, in the order given.
    $byName = [ordered]@{}
    foreach ($m in $members) {
        $key = $m.Name.Trim().ToLowerInvariant()
        if ($byName.Contains($key)) {
            $prev = $byName[$key]
            if ($prev.Title -notmatch ('(^|&\s)' + [regex]::Escape($m.Title) + '(\s&|$)')) {
                $prev.Title = "$($prev.Title) & $($m.Title)"
            }
            if (-not $prev.LinkedIn -and $m.LinkedIn) { $prev.LinkedIn = $m.LinkedIn }
        }
        else { $byName[$key] = $m }
    }
    $members = @($byName.Values)

    # Only the JSON imports and the `team` array are replaced; everything else
    # in team.ts (the TeamMember type, derived exports such as the Single Page
    # template's `configuredTeam`) is the template's and is kept as-is.
    $indexText = Get-Content -LiteralPath $teamIndexFile -Raw -Encoding utf8
    $importRe = "(?m)^import\s+\w+\s+from\s+'\./team/[^']+\.json'\r?\n"
    # The closing bracket may sit on its own line (as the templates ship it) or
    # on the same line (as prettier leaves a short array after an earlier run),
    # so a second run over the same repo matches too.
    $arrayRe = '(?s)(export const team\s*(?::\s*TeamMember\[\])?\s*=\s*\[).*?(\s*\])'
    # The imports may legitimately be absent: a repo whose team was left empty
    # by an earlier run has none. The array must always be there.
    if (-not [regex]::IsMatch($indexText, $arrayRe)) {
        throw 'src/data/team.ts no longer has an "export const team = [ ... ]" array; the template changed shape.'
    }

    # Replace the template's sample members (FFC's own team).
    Get-ChildItem -LiteralPath $teamDataDir -Filter '*.json' | Remove-Item -Force

    $usedSlugs = @{}
    $imports = New-Object System.Collections.Generic.List[string]
    $vars = New-Object System.Collections.Generic.List[string]
    for ($i = 0; $i -lt $members.Count; $i++) {
        $baseSlug = Convert-ToKebabCase -Value $members[$i].Name
        $slug = $baseSlug
        $n = 2
        while ($usedSlugs.ContainsKey($slug)) { $slug = "{0}-{1}" -f $baseSlug, $n; $n++ }
        $usedSlugs[$slug] = $true

        $obj = [ordered]@{ name = $members[$i].Name; role = $members[$i].Title }
        if ($members[$i].LinkedIn) { $obj.linkedinUrl = $members[$i].LinkedIn }
        Write-LfFile -Path (Join-Path $teamDataDir "$slug.json") -Text ($obj | ConvertTo-Json -Depth 5)

        $var = 'member{0}' -f ($i + 1)
        $imports.Add("import $var from './team/$slug.json'")
        $vars.Add("  $var,")
    }

    # Drop the old imports, then put the new ones where the first one was (or,
    # with no old imports, just before the first export).
    $firstImportMatch = [regex]::Match($indexText, $importRe)
    $withoutImports = [regex]::Replace($indexText, $importRe, '')
    $insertAt = if ($firstImportMatch.Success) { $firstImportMatch.Index } else {
        $firstExport = [regex]::Match($withoutImports, '(?m)^export\s')
        if ($firstExport.Success) { $firstExport.Index } else { 0 }
    }
    $importBlock = if ($imports.Count -gt 0) {
        ($imports -join "`n") + "`n" + $(if ($firstImportMatch.Success) { '' } else { "`n" })
    }
    else { '' }
    $teamTs = $withoutImports.Substring(0, $insertAt) + $importBlock + $withoutImports.Substring($insertAt)
    $teamTs = if ($vars.Count -gt 0) {
        $arrayBody = "`n" + ($vars -join "`n")
        [regex]::Replace($teamTs, $arrayRe, { param($m) $m.Groups[1].Value + $arrayBody + "`n]" }, 'None')
    }
    else {
        [regex]::Replace($teamTs, $arrayRe, { param($m) $m.Groups[1].Value + ']' }, 'None')
    }
    # Removing every import can leave a run of blank lines; collapse to one.
    $teamTs = [regex]::Replace($teamTs, "\n{3,}", "`n`n")
    Write-LfFile -Path $teamIndexFile -Text $teamTs
}

function Invoke-RepoPrettier {
    # Generated files must pass the new repo's own `format:check`. Uses the
    # prettier version the repo pins; best-effort, since formatting is not
    # worth failing a provision over (CI will name any remaining file).
    param([Parameter(Mandatory = $true)][string]$RepoRoot, [Parameter(Mandatory = $true)][string[]]$Paths)

    $pkgFile = Join-Path $RepoRoot 'package.json'
    if (-not (Test-Path -LiteralPath $pkgFile)) {
        Write-Warning 'No package.json; generated files were not run through prettier.'
        return
    }
    $pkg = Get-Content -LiteralPath $pkgFile -Raw -Encoding utf8 | ConvertFrom-Json
    $version = [string]$pkg.devDependencies.prettier
    $version = ($version -replace '^[\^~>=\s]+', '')
    $spec = if ($version -match '^\d+\.\d+\.\d+$') { "prettier@$version" } else { 'prettier@3' }

    # 1. The repo's own installed prettier, when node_modules exists (a local
    #    re-run over an installed checkout): exactly the pinned version.
    # 2. Otherwise npx. On Windows call npx.cmd, not the npx.ps1 shim that
    #    pwsh resolves `npx` to: with `--yes <spec>` the shim fails with "npm
    #    error could not determine executable to run" (measured locally, npm
    #    10 / node 22), while npx.cmd runs the same command fine. Linux is
    #    unaffected (plain `npx`).
    $localBin = @('node_modules/.bin/prettier.cmd', 'node_modules/.bin/prettier') |
        ForEach-Object { Join-Path $RepoRoot $_ } |
        Where-Object { Test-Path -LiteralPath $_ } |
        Select-Object -First 1
    $isWin = $IsWindows -or $env:OS -eq 'Windows_NT'
    $npx = if ($isWin -and (Get-Command npx.cmd -ErrorAction SilentlyContinue)) { 'npx.cmd' }
    elseif (Get-Command npx -ErrorAction SilentlyContinue) { 'npx' }
    else { $null }
    if (-not $localBin -and -not $npx) {
        Write-Warning ("Neither the repo's prettier nor npx is available; generated files were not formatted. Run: npx --yes {0} --write {1}" -f $spec, ($Paths -join ' '))
        return
    }

    Push-Location $RepoRoot
    try {
        if ($localBin) { & $localBin --write @Paths 2>&1 | Out-Host }
        else { & $npx --yes $spec --write @Paths 2>&1 | Out-Host }
        if ($LASTEXITCODE -ne 0) {
            Write-Warning ("prettier exited {0}; generated files may need formatting. Run in the repo: npx --yes {1} --write {2}" -f $LASTEXITCODE, $spec, ($Paths -join ' '))
            # Best-effort by design: do not let npx's code become the script's
            # own exit status (a caller reading it would see a failed apply).
            $global:LASTEXITCODE = 0
        }
    }
    finally { Pop-Location }
}

# ---- Main ----
$repoRoot = (Resolve-Path -LiteralPath $RepoPath).Path

$siteConfigFile = Join-Path $repoRoot 'src/lib/site.config.ts'
if (Test-Path -LiteralPath $siteConfigFile) {
    Write-Host 'Config-driven template detected (src/lib/site.config.ts); writing siteConfig + team data.'
    Update-SiteConfig `
        -ConfigFile $siteConfigFile `
        -CharityName $CharityName `
        -Email $FooterEmail `
        -Phone $FooterPhone `
        -Address $FooterAddress `
        -Ein $FooterEin `
        -GuideStarProfileUrl $GuideStarProfileUrl `
        -GuideStarDirectProfileUrl $GuideStarDirectProfileUrl `
        -Social $FooterSocial `
        -Mission $Mission `
        -DonationUrl $DonationUrl `
        -VolunteerUrl $VolunteerUrl `
        -IrsStatus $IrsStatus

    if (-not [string]::IsNullOrWhiteSpace($FooterEmail)) {
        Update-SecurityTxtContact -RepoRoot $repoRoot -Email $FooterEmail.Trim()
    }
    else {
        # security.txt must name a reachable address. The template's is FFC's
        # (which hosts and maintains the site), so it is left until the
        # charity's arrives; the templates' drift check flags the mismatch.
        Write-Warning 'No contact email; security.txt Contact is left as the template ships it until the email is filled in.'
    }

    Update-TeamData -RepoRoot $repoRoot -LeadershipLines $LeadershipLines

    $script:PendingRendered = Update-SiteConfigPending -ConfigFile $siteConfigFile -Pending @($script:PendingFields)

    Invoke-RepoPrettier -RepoRoot $repoRoot -Paths @('src/lib/site.config.ts', 'src/data/team.ts', 'src/data/team')

    if ($script:PendingFields.Count -gt 0) {
        Write-Warning ("Awaiting information from the charity (emptied, never FFC's values): {0}." -f ($script:PendingFields -join ', '))
    }
    if ($SummaryPath) {
        $summary = [ordered]@{
            pendingFields   = @($script:PendingFields)
            pendingRendered = [bool]$script:PendingRendered
        }
        Write-LfFile -Path $SummaryPath -Text ($summary | ConvertTo-Json -Depth 3)
    }

    Write-Host 'Config-driven template content updated successfully.' -ForegroundColor Green
    return
}

# Legacy hard-coded footer (pre-site.config.ts repos).
# Its regex patch can only REPLACE the template's hard-coded values, not blank
# them, so a missing field would leave Free For Charity's own phone, address,
# EIN, social link or staff on the charity's site. Refuse instead (701 reports
# content_status=failed) rather than publish FFC's identity as the charity's.
$legacyMissing = @()
if ([string]::IsNullOrWhiteSpace($FooterEmail)) { $legacyMissing += 'email' }
if (-not (Get-TelDigits -Phone $FooterPhone)) { $legacyMissing += 'phone' }
if ([string]::IsNullOrWhiteSpace($FooterAddress)) { $legacyMissing += 'address' }
if ([string]::IsNullOrWhiteSpace($FooterEin)) { $legacyMissing += 'EIN' }
if (@($LeadershipLines | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }).Count -eq 0) { $legacyMissing += 'leadership' }
# Same parse Update-FooterComponent applies, so the guard accepts exactly the
# links the patch can replace ("X (Twitter): https://..." included).
$legacySocial = Get-LegacySocialMap -Social $FooterSocial
foreach ($k in @('facebook', 'x', 'linkedin', 'github')) {
    if (-not $legacySocial.ContainsKey($k)) { $legacyMissing += "$k link" }
}
if ($legacyMissing.Count -gt 0) {
    throw ("Legacy hard-coded footer (no src/lib/site.config.ts) cannot blank missing fields, so it would keep Free For Charity's own values for: {0}. Edit src/components/footer/index.tsx by hand." -f ($legacyMissing -join ', '))
}

$footerFile = Join-Path $repoRoot 'src/components/footer/index.tsx'

Update-FooterComponent `
    -FooterFile $footerFile `
    -Email $FooterEmail `
    -Phone $FooterPhone `
    -Address $FooterAddress `
    -Ein $FooterEin `
    -GuideStarProfileUrl $GuideStarProfileUrl `
    -GuideStarDirectProfileUrl $GuideStarDirectProfileUrl `
    -Social $FooterSocial `
    -Domain $Domain `
    -CharityName $CharityName

Update-LeadershipSection `
    -RepoRoot $repoRoot `
    -CharityName $CharityName `
    -LeadershipLines $LeadershipLines

Write-Host 'React template content updated successfully.' -ForegroundColor Green
