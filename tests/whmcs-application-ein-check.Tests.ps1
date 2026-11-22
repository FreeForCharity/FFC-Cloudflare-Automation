#requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0.0' }
<#
.SYNOPSIS
    Unit tests for the pure functions of scripts/whmcs-application-ein-check.ps1
    (workflow 237). No network: the script is dot-sourced, which defines
    functions only.
#>

BeforeAll {
    . (Join-Path $PSScriptRoot '..' 'scripts' 'whmcs-application-ein-check.ps1')
}

Describe 'Get-EinDigitDifference' {
    It 'counts differing positions so a one-digit typo shows as 1' {
        Get-EinDigitDifference -A '42-4096124' -B '424096123' | Should -Be 1
        Get-EinDigitDifference -A '12-3456789' -B '12-3456789' | Should -Be 0
        Get-EinDigitDifference -A '12-3456789' -B '21-3456789' | Should -Be 2
    }
    It 'returns -1 when either value is not a 9-digit EIN' {
        Get-EinDigitDifference -A '12-345' -B '123456789' | Should -Be -1
    }
}

Describe 'Get-EinCheckIrsLabel' {
    It 'labels each IRS outcome' {
        Get-EinCheckIrsLabel -Status $null | Should -Be 'not checked'
        Get-EinCheckIrsLabel -Status ([pscustomobject]@{ Checked = $true; Revoked = $true; InPub78 = $false }) | Should -Be 'revoked'
        Get-EinCheckIrsLabel -Status ([pscustomobject]@{ Checked = $true; Revoked = $false; InPub78 = $true }) | Should -Be 'pub78'
        Get-EinCheckIrsLabel -Status ([pscustomobject]@{ Checked = $true; Revoked = $false; InPub78 = $false }) | Should -Be 'not listed'
    }
}

Describe 'Get-EinCheckProPublicaLabel' {
    It 'tells a 501(c)(3), another subsection, not found and a failed lookup apart' {
        Get-EinCheckProPublicaLabel -Result ([pscustomobject]@{ Verified = $true; Is501c3 = $true; SubsectionCode = 3; NotFound = $false }) | Should -Be '501c3'
        Get-EinCheckProPublicaLabel -Result ([pscustomobject]@{ Verified = $true; Is501c3 = $false; SubsectionCode = 4; NotFound = $false }) | Should -Be 'subsection 4'
        Get-EinCheckProPublicaLabel -Result ([pscustomobject]@{ Verified = $false; Is501c3 = $null; SubsectionCode = $null; NotFound = $true }) | Should -Be 'not found'
        Get-EinCheckProPublicaLabel -Result ([pscustomobject]@{ Verified = $false; Is501c3 = $null; SubsectionCode = $null; NotFound = $false }) | Should -Be 'lookup failed'
    }
}

Describe 'Get-EinCheckVerdict' {
    It 'puts a revocation ahead of everything else' {
        Get-EinCheckVerdict -HasEin $true -Irs 'revoked' -ProPublica '501c3' -SiteCompare 'differs (1 of 9 digits)' -NameMismatch $true | Should -Be 'revoked'
    }
    It 'reports a site/application mismatch next' {
        Get-EinCheckVerdict -HasEin $true -Irs 'pub78' -ProPublica '501c3' -SiteCompare 'differs (1 of 9 digits)' -NameMismatch $false | Should -Be 'site EIN differs from application'
    }
    It 'tells an EIN found nowhere from one missing only from Pub 78' {
        Get-EinCheckVerdict -HasEin $true -Irs 'not listed' -ProPublica 'not found' -SiteCompare 'same' -NameMismatch $false | Should -Be 'in no IRS list or ProPublica'
        Get-EinCheckVerdict -HasEin $true -Irs 'not listed' -ProPublica '501c3' -SiteCompare 'same' -NameMismatch $false | Should -Be 'not in Pub 78'
    }
    It 'is ok only for a listed EIN whose IRS name matches' {
        Get-EinCheckVerdict -HasEin $true -Irs 'pub78' -ProPublica '501c3' -SiteCompare 'same' -NameMismatch $false | Should -Be 'ok'
        Get-EinCheckVerdict -HasEin $true -Irs 'pub78' -ProPublica '501c3' -SiteCompare 'same' -NameMismatch $true | Should -Be 'IRS name differs from application'
        Get-EinCheckVerdict -HasEin $false -Irs 'not checked' -ProPublica 'not checked' -SiteCompare 'no FFC site' -NameMismatch $false | Should -Be 'no EIN on the application'
    }
}
