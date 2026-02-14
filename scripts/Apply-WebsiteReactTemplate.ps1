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

  [string[]]$FooterSocial = @(),

  [string[]]$LeadershipLines = @()
)

$ErrorActionPreference = 'Stop'

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
  if ($digits.Length -lt 7) { return $null }
  return $digits
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

  # Social links (best-effort: parse "platform: url")
  if ($Social -and $Social.Count -gt 0) {
    $map = @{}
    foreach ($line in $Social) {
      if ([string]::IsNullOrWhiteSpace($line)) { continue }
      $clean = ($line.Trim() -replace '^[\*-]\s+', '')
      $m = [regex]::Match($clean, '^(?<k>[A-Za-z ]+)\s*:\s*(?<v>https?://\S+)$')
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
    } elseif ($parts.Count -ge 3) {
      $linkedin = $parts[2]
    }
  } else {
    # Supported dash format (matches issue template guidance):
    # Role - Name (optional: notes)
    $m = [regex]::Match($clean, '^(?<t>[^-]+?)\s*-\s*(?<n>.+)$')
    if ($m.Success) {
      $title = $m.Groups['t'].Value.Trim()
      $name = $m.Groups['n'].Value.Trim()
    } else {
      $name = $clean
    }
  }

  if ([string]::IsNullOrWhiteSpace($name)) { return $null }
  if ([string]::IsNullOrWhiteSpace($title)) { $title = 'Board Member' }

  if ([string]::IsNullOrWhiteSpace($linkedin) -or ($linkedin -notmatch '^https?://')) {
    $linkedin = 'https://www.linkedin.com'
  }

  return [pscustomobject]@{
    Name = $name
    Title = $title
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
      name = $members[$i].Name
      title = $members[$i].Title
      imageUrl = $img
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

# ---- Main ----
$repoRoot = (Resolve-Path -LiteralPath $RepoPath).Path

$footerFile = Join-Path $repoRoot 'src/components/footer/index.tsx'

Update-FooterComponent `
  -FooterFile $footerFile `
  -Email $FooterEmail `
  -Phone $FooterPhone `
  -Address $FooterAddress `
  -Ein $FooterEin `
  -Social $FooterSocial `
  -Domain $Domain `
  -CharityName $CharityName

Update-LeadershipSection `
  -RepoRoot $repoRoot `
  -CharityName $CharityName `
  -LeadershipLines $LeadershipLines

Write-Host 'React template content updated successfully.' -ForegroundColor Green
