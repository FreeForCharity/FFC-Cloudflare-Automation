[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RepoPath,

    [Parameter(Mandatory = $true)]
    [string]$Domain,

    [Parameter(Mandatory = $true)]
    [string]$CharityName,

    [Parameter(Mandatory = $true)]
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

    # Optional. When set, a JSON summary { blankFields: [...] } is written here
    # naming every identity field the charity did not provide and that was
    # therefore left blank (never filled with the template's FFC value). 701's
    # finalize comment lists them for a human to fill in.
    [string]$SummaryPath
)

$ErrorActionPreference = 'Stop'

# Identity fields left blank because the charity did not provide them. A blank
# is the honest state: the template's values are Free For Charity's own (EIN,
# phone, addresses, GuideStar profile, social links, staff), and leaving any of
# them on a charity's site presents FFC's identity as the charity's.
$script:BlankFields = New-Object System.Collections.Generic.List[string]
function Add-BlankField {
    param([Parameter(Mandatory = $true)][string]$Name)
    if (-not $script:BlankFields.Contains($Name)) { $script:BlankFields.Add($Name) }
}

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

    # Social links (best-effort: parse "platform: url")
    if ($Social -and $Social.Count -gt 0) {
        $map = @{}
        foreach ($line in $Social) {
            if ([string]::IsNullOrWhiteSpace($line)) { continue }
            $clean = ($line.Trim() -replace '^[\*-]\s+', '')
            $m = [regex]::Match($clean, '^(?<k>[A-Za-z ]+)\s*:\s*(?<v>https://\S+)$')
            if (-not $m.Success) { continue }
            $key = $m.Groups['k'].Value.Trim().ToLowerInvariant()
            $val = $m.Groups['v'].Value.Trim()
            $map[$key] = $val
        }

        foreach ($k in @('facebook', 'x', 'twitter', 'linkedin', 'github')) {
            if (-not $map.ContainsKey($k)) { continue }
            $url = $map[$k]

            switch ($k) {
                'facebook' { $text = $text -replace "href: 'https://www\.facebook\.com/[^']+'", "href: '$url'" }
                'x' { $text = $text -replace "href: 'https://x\.com/[^']+'", "href: '$url'" }
                'twitter' { $text = $text -replace "href: 'https://x\.com/[^']+'", "href: '$url'" }
                'linkedin' { $text = $text -replace "href: 'https://www\.linkedin\.com/[^']+'", "href: '$url'" }
                'github' { $text = $text -replace "href: 'https://github\.com/[^']+'", "href: '$url'" }
            }
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

function Update-SiteConfig {
    param(
        [Parameter(Mandatory = $true)][string]$ConfigFile,
        [Parameter(Mandatory = $true)][string]$CharityName,
        [Parameter(Mandatory = $true)][string]$Email,
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

    $missionText = if ([string]::IsNullOrWhiteSpace($Mission)) {
        Add-BlankField 'mission'
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
    $text = Set-SiteConfigValue -Source $text -Key 'contactEmail' -ValueTs (ConvertTo-TsString $Email)

    foreach ($pair in @(@('donationUrl', $DonationUrl), @('volunteerUrl', $VolunteerUrl))) {
        $url = [string]$pair[1]
        # Only https URLs; anything else keeps the template's mailto fallback.
        $url = if ($url -match '^https://\S+$') { $url.Trim() } else { '' }
        $text = Set-SiteConfigValue -Source $text -Key $pair[0] -ValueTs (ConvertTo-TsString $url) -Optional
    }

    # No EIN: blank, never the template's. Keeping it would publish FFC's tax
    # ID (46-2471893) as the charity's -- a false legal claim. The shared
    # schema and the templates' own tests still want a non-empty EIN, so the
    # site's CI flags the blank until a human fills it in; that is the right
    # failure, and the content that IS known still lands.
    $einValue = if ([string]::IsNullOrWhiteSpace($Ein)) {
        Write-Warning 'No EIN supplied; siteConfig.ein is left blank (never the template EIN).'
        Add-BlankField 'EIN'
        ''
    }
    else { $Ein.Trim() }
    $text = Set-SiteConfigValue -Source $text -Key 'ein' -ValueTs (ConvertTo-TsString $einValue)

    # An empty phone is the template's documented "no phone" state (no block).
    $telDigits = Get-TelDigits -Phone $Phone
    $phoneTs = if ($telDigits) {
        '{ display: ' + (ConvertTo-TsString $Phone) + ', tel: ' + (ConvertTo-TsString $telDigits) + ' }'
    }
    else {
        Add-BlankField 'phone'
        "{ display: '', tel: '' }"
    }
    $text = Set-SiteConfigValue -Source $text -Key 'phone' -ValueTs $phoneTs

    $addrLines = @()
    if (-not [string]::IsNullOrWhiteSpace($Address)) {
        # Also split a literal backslash-n: 701's issue-form path emitted one
        # until it was fixed, and a re-run on its recorded data must not
        # render "\n" in the footer.
        $addrLines = @($Address -split "`r`n|`n|\\n" | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    }
    $addressesTs = if ($addrLines.Count -gt 0) {
        $mapUrl = 'https://www.google.com/maps/search/?api=1&query=' + (Convert-AddressToMapsQuery -Address ($addrLines -join ' '))
        $linesTs = ($addrLines | ForEach-Object { ConvertTo-TsString $_ }) -join ', '
        "[`n    {`n      label: 'Main Address',`n      lines: [$linesTs],`n      mapUrl: $(ConvertTo-TsString $mapUrl),`n    },`n  ]"
    }
    else {
        # Never the template's Raleigh / State College offices.
        Add-BlankField 'address'
        '[]'
    }
    $text = Set-SiteConfigValue -Source $text -Key 'addresses' -ValueTs $addressesTs

    # Candid / GuideStar. The footer's seal and "Direct GuideStar Profile Link"
    # are a transparency claim, so they only ever point at the charity's own
    # profile -- never FFC's (46-2471893 / bbbe173a-...), which is what the
    # template ships:
    #  - URLs given (https)           -> used as given.
    #  - none given, recognized
    #    501(c)(3) with an EIN        -> Candid's profile-by-EIN URL. Candid
    #                                    carries every IRS-recognized exempt
    #                                    org, so this is the charity's own
    #                                    profile, and it keeps the site's CI
    #                                    (which wants https URLs) green.
    #  - otherwise                    -> both blank. A pre-501(c)(3) org has no
    #                                    Candid profile to link to. The shared
    #                                    schema (minLength 1) and the Footer-Only
    #                                    footer (no empty guard) do not accept a
    #                                    blank yet -- a template gap tracked on
    #                                    both template repos -- so until that
    #                                    lands the site's CI flags it rather than
    #                                    this script inventing a link.
    $candidByEin = if ($recognized -and -not [string]::IsNullOrWhiteSpace($Ein)) { "https://www.guidestar.org/profile/$($Ein.Trim())" } else { '' }
    $profileUrl = if ($GuideStarProfileUrl -match '^https://\S+$') { $GuideStarProfileUrl.Trim() } else { $candidByEin }
    $directUrl = if ($GuideStarDirectProfileUrl -match '^https://\S+$') { $GuideStarDirectProfileUrl.Trim() } else { $profileUrl }
    if (-not $profileUrl) {
        # A direct link alone still names the charity's own profile.
        $profileUrl = $directUrl
    }
    if (-not $profileUrl) {
        Write-Warning 'No Candid / GuideStar profile for this charity; siteConfig.guidestar is left blank (never the template profile).'
        Add-BlankField 'Candid/GuideStar profile'
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
        Add-BlankField 'social links'
        '[]'
    }
    $text = Set-SiteConfigValue -Source $text -Key 'social' -ValueTs $socialTs

    $xLink = $socialEntries | Where-Object { $_.Label -eq 'X (Twitter)' } | Select-Object -First 1
    $handle = if ($xLink) { [regex]::Match($xLink.Href, '^https://(?:www\.)?(?:x|twitter)\.com/@?(?<h>[A-Za-z0-9_]{1,15})(?:[/?#]|$)').Groups['h'].Value } else { '' }
    $text = Set-SiteConfigValue -Source $text -Key 'twitterHandle' -ValueTs (ConvertTo-TsString ($(if ($handle) { "@$handle" } else { '' })))

    Write-LfFile -Path $ConfigFile -Text $text
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
    # No usable lines: an EMPTY team, never the template's sample members --
    # those are FFC's own people, and publishing them as the charity's
    # leadership is a false claim. Both templates' team sections render nothing
    # for an empty roster; the Footer-Only template's own tests still want at
    # least one member, so the site's CI flags the gap until a human adds them.
    if ($members.Count -eq 0) {
        Write-Warning 'No usable leadership lines (each needs a name); the team is left empty (never the template team).'
        Add-BlankField 'leadership'
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
    if (-not (Get-Command npx -ErrorAction SilentlyContinue) -or -not (Test-Path -LiteralPath $pkgFile)) {
        Write-Warning 'npx or package.json not available; generated files were not run through prettier.'
        return
    }
    $pkg = Get-Content -LiteralPath $pkgFile -Raw -Encoding utf8 | ConvertFrom-Json
    $version = [string]$pkg.devDependencies.prettier
    $version = ($version -replace '^[\^~>=\s]+', '')
    $spec = if ($version -match '^\d+\.\d+\.\d+$') { "prettier@$version" } else { 'prettier@3' }

    Push-Location $RepoRoot
    try {
        & npx --yes $spec --write @Paths 2>&1 | Out-Host
        if ($LASTEXITCODE -ne 0) { Write-Warning "prettier exited $LASTEXITCODE; generated files may need formatting." }
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

    Update-SecurityTxtContact -RepoRoot $repoRoot -Email $FooterEmail

    Update-TeamData -RepoRoot $repoRoot -LeadershipLines $LeadershipLines

    Invoke-RepoPrettier -RepoRoot $repoRoot -Paths @('src/lib/site.config.ts', 'src/data/team.ts', 'src/data/team')

    if ($script:BlankFields.Count -gt 0) {
        Write-Warning ("Not provided, left blank for a human to fill in: {0}." -f ($script:BlankFields -join ', '))
    }
    if ($SummaryPath) {
        $summary = [ordered]@{ blankFields = @($script:BlankFields) }
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
if (-not (Get-TelDigits -Phone $FooterPhone)) { $legacyMissing += 'phone' }
if ([string]::IsNullOrWhiteSpace($FooterAddress)) { $legacyMissing += 'address' }
if ([string]::IsNullOrWhiteSpace($FooterEin)) { $legacyMissing += 'EIN' }
if (@($LeadershipLines | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }).Count -eq 0) { $legacyMissing += 'leadership' }
$legacySocial = @{}
foreach ($line in $FooterSocial) {
    if ([string]::IsNullOrWhiteSpace($line)) { continue }
    $m = [regex]::Match(($line.Trim() -replace '^[\*-]\s+', ''), '^(?<k>[A-Za-z ]+)\s*:\s*https://\S+$')
    if ($m.Success) { $legacySocial[$m.Groups['k'].Value.Trim().ToLowerInvariant()] = $true }
}
foreach ($k in @('facebook', 'linkedin', 'github')) {
    if (-not $legacySocial.ContainsKey($k)) { $legacyMissing += "$k link" }
}
if (-not ($legacySocial.ContainsKey('x') -or $legacySocial.ContainsKey('twitter'))) { $legacyMissing += 'x link' }
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
