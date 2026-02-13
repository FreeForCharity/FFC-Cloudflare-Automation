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

  # Expected: Name | Title | Email | Phone | LinkedIn
  $clean = ($Line.Trim() -replace '^[\*-]\s+', '')
  $parts = $clean.Split('|') | ForEach-Object { $_.Trim() }
  $name = if ($parts.Count -ge 1) { $parts[0] } else { '' }
  $title = if ($parts.Count -ge 2) { $parts[1] } else { '' }
  $linkedin = if ($parts.Count -ge 5) { $parts[4] } else { '' }

  if ([string]::IsNullOrWhiteSpace($name)) { return $null }
  if ([string]::IsNullOrWhiteSpace($title)) { $title = 'Board Member' }
  if ([string]::IsNullOrWhiteSpace($linkedin)) { $linkedin = 'https://www.linkedin.com' }

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

function Update-LeadershipSection {
  param(
    [Parameter(Mandatory = $true)][string]$TeamSectionFile,
    [Parameter(Mandatory = $true)][string]$CharityName,
    [string[]]$LeadershipLines
  )

  Assert-FileExists -Path $TeamSectionFile

  $members = @()
  foreach ($line in ($LeadershipLines | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })) {
    $m = Parse-LeadershipLine -Line $line
    if ($null -ne $m) { $members += $m }
  }

  if ($members.Count -eq 0) {
    Write-Host 'No leadership lines provided; skipping team/leadership update.' -ForegroundColor Yellow
    return
  }

  $images = @('/Images/member1.webp', '/Images/member2.webp', '/Images/member3.webp', '/Images/member4.webp', '/Images/member5.webp')

  $cards = New-Object System.Collections.Generic.List[string]
  for ($i = 0; $i -lt $members.Count; $i++) {
    $img = $images[$i % $images.Count]
    $nm = Escape-TsxString -Value $members[$i].Name
    $tt = Escape-TsxString -Value $members[$i].Title
    $li = Escape-TsxString -Value $members[$i].LinkedIn

    $cards.Add((@'
          <TeamMemberCard
            imageUrl="{0}"
            name="{1}"
            title="{2}"
            linkedinUrl="{3}"
          />
'@) -f $img, $nm, $tt, $li)
  }

  $cardsText = ($cards -join "`n")

  $newHeading = (Escape-TsxString -Value ("$CharityName Leadership"))

  $newFile = @"
import React from 'react'
import TeamMemberCard from '@/components/ui/TeamMemberCard'

const index = () => {
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
$cardsText
        </div>
      </div>
    </div>
  )
}

export default index
"@

  Set-Content -LiteralPath $TeamSectionFile -Value $newFile -Encoding utf8
}

# ---- Main ----
$repoRoot = (Resolve-Path -LiteralPath $RepoPath).Path

$footerFile = Join-Path $repoRoot 'src/components/footer/index.tsx'
$teamSectionFile = Join-Path $repoRoot 'src/components/home-page/TheFreeForCharityTeam/index.tsx'

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
  -TeamSectionFile $teamSectionFile `
  -CharityName $CharityName `
  -LeadershipLines $LeadershipLines

Write-Host 'React template content updated successfully.' -ForegroundColor Green
