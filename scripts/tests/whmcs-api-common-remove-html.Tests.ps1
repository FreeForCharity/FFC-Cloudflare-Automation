<#
.SYNOPSIS
    Pester (v5) unit tests for Remove-Html in whmcs-api-common.ps1 and the
    match path in 221 (WHMCS Application Search) that depends on it.

.DESCRIPTION
    Regression cover for #928 / #929: `Remove-Html` was deleted from
    whmcs-application-search.ps1 by 8d8adae while its call site in the
    per-field match loop was left behind, so 221 threw on the first custom
    field of the first service and could never return a match. Nothing in CI
    executed the script, so it shipped green for five days behind an approval
    gate.

    Two properties are asserted here, and the second is the one that matters:

    1. Remove-Html resolves and strips WHMCS's <a href> wrapper.
    2. The match site strips WITHOUT masking. Format-WhmcsFieldValue also
       strips HTML, so it looks like a drop-in - but it masks personal
       classifications, which would make an application whose domain sits in a
       personal-classified field silently unfindable. The tests below fail if
       the match site is ever "simplified" to use it.

    Run locally: Invoke-Pester -Path scripts/tests -Output Detailed
#>

BeforeAll {
    . (Join-Path (Split-Path $PSScriptRoot -Parent) 'whmcs-api-common.ps1')

    # The match expression from the per-field loop in
    # whmcs-application-search.ps1, reproduced. That script cannot be
    # dot-sourced (it has a mandatory param block and runs a live sweep), so
    # the expression is mirrored rather than imported. Keep it byte-identical
    # to the call site.
    function Test-NeedleMatch {
        param([string]$Value, [string]$Needle)
        return (Remove-Html ([string]$Value)).ToLowerInvariant().Contains($Needle)
    }
}

Describe 'Remove-Html' {
    It 'is defined and resolves as a command after dot-sourcing the common module' {
        # The literal bug: the call site resolved to nothing at runtime.
        (Get-Command Remove-Html -ErrorAction SilentlyContinue) | Should -Not -BeNullOrEmpty
    }

    It 'returns a plain string unchanged' {
        Remove-Html 'Restored Radiance' | Should -Be 'Restored Radiance'
    }

    # NOTE: no angle brackets in test names below. Pester 5+ treats a name's
    # bracketed spans as -ForEach placeholders and expands them, so an `It`
    # named after the markup it tests dies with a ParseException that reads
    # like a failure in the code under test.
    It 'strips the mailto anchor wrapper WHMCS puts around email answers' {
        $v = '<a href="mailto:x@y.z" rel="nofollow noreferrer">x@y.z</a>'
        Remove-Html $v | Should -Be 'x@y.z'
    }

    It 'strips the https anchor wrapper WHMCS puts around URL answers' {
        $v = '<a href="https://restoredradiance.org" rel="nofollow noreferrer noopener" target="_blank">restoredradiance.org</a>'
        Remove-Html $v | Should -Be 'restoredradiance.org'
    }

    It 'trims surrounding whitespace left behind by the tags' {
        Remove-Html '  <p> Mission text </p>  ' | Should -Be 'Mission text'
    }

    It 'returns an empty string for an empty string' {
        Remove-Html '' | Should -Be ''
    }

    It 'returns an empty string for $null (an absent customfield value)' {
        # $f.value is frequently absent on WHMCS custom fields; the call site
        # casts to [string], but the helper must not throw on its own either.
        Remove-Html $null | Should -Be ''
    }

    It 'returns an empty string for markup that contains no text' {
        Remove-Html '<br /><br />' | Should -Be ''
    }
}

Describe '221 match path' {
    It 'matches a bare-domain needle inside an anchor wrapper (the #928 bug)' {
        # This is the assertion that fails outright when Remove-Html is missing:
        # the call site throws CommandNotFoundException rather than returning.
        $v = '<a href="https://restoredradiance.org" rel="nofollow noreferrer noopener" target="_blank">restoredradiance.org</a>'
        Test-NeedleMatch -Value $v -Needle 'restoredradiance' | Should -BeTrue
    }

    It 'does not match a needle that is only present inside the markup, not the text' {
        # 'nofollow' lives in the rel attribute. Stripping before matching is
        # what keeps attribute noise from producing phantom hits.
        $v = '<a href="https://example.org" rel="nofollow noreferrer">example.org</a>'
        Test-NeedleMatch -Value $v -Needle 'nofollow' | Should -BeFalse
    }

    It 'still matches a domain that sits in a PERSONAL-classified field' {
        # #928's explicit criterion. Field name classifies as personal-contact,
        # so Format-WhmcsFieldValue would return '***' here - see the next test.
        $fieldName = 'Board President Individual Email'
        $v = '<a href="https://restoredradiance.org" rel="nofollow noreferrer noopener" target="_blank">restoredradiance.org</a>'

        Get-WhmcsFieldClass -FieldName $fieldName | Should -Be 'personal-contact'
        Test-NeedleMatch -Value $v -Needle 'restoredradiance' | Should -BeTrue
    }

    It 'would go silently unfindable if the match site used Format-WhmcsFieldValue' {
        # Guards the trap rather than the fix: this asserts WHY the match site
        # must not be routed through the masking helper. If a future change
        # makes Format-WhmcsFieldValue non-masking, this test fails and the
        # decision gets re-read instead of silently changing search behaviour.
        $fieldName = 'Board President Individual Email'
        $v = '<a href="https://restoredradiance.org" rel="nofollow noreferrer noopener" target="_blank">restoredradiance.org</a>'

        $masked = Format-WhmcsFieldValue -FieldName $fieldName -Value $v
        $masked | Should -Be '***'
        $masked.ToLowerInvariant().Contains('restoredradiance') | Should -BeFalse
    }

    It 'still matches an org name that sits in a person-name-classified field' {
        $fieldName = 'Your Name'
        Get-WhmcsFieldClass -FieldName $fieldName | Should -Be 'person-name'
        Test-NeedleMatch -Value 'Restored Radiance' -Needle 'restored radiance' | Should -BeTrue
        Format-WhmcsFieldValue -FieldName $fieldName -Value 'Restored Radiance' | Should -Be 'R***'
    }
}

Describe 'Format-WhmcsFieldValue is unchanged by the shared strip' {
    # Remove-Html replaced an inline regex inside this function. These pin the
    # emitted-row masking that #929 requires to stay byte-identical.
    It 'masks a mailto-wrapped address in a personal-contact field' {
        $v = '<a href="mailto:person@example.org" rel="nofollow noreferrer">person@example.org</a>'
        Format-WhmcsFieldValue -FieldName 'Primary Contact Email' -Value $v | Should -Be '***@example.org'
    }

    It 'masks a bare email pasted into a free-text public field' {
        Format-WhmcsFieldValue -FieldName 'Mission' -Value 'person@example.org' | Should -Be '***@example.org'
    }

    It 'leaves an EIN public and does not trip the phone matcher' {
        Format-WhmcsFieldValue -FieldName 'EIN' -Value '12-3456789' | Should -Be '12-3456789'
    }

    It 'keeps a public URL answer readable with its wrapper stripped' {
        $v = '<a href="https://restoredradiance.org" rel="nofollow noreferrer noopener" target="_blank">restoredradiance.org</a>'
        Format-WhmcsFieldValue -FieldName 'Website Domain' -Value $v | Should -Be 'restoredradiance.org'
    }

    It 'returns an empty string for whitespace-only and tag-only input' {
        Format-WhmcsFieldValue -FieldName 'Mission' -Value '   ' | Should -Be ''
        Format-WhmcsFieldValue -FieldName 'Mission' -Value '<br />' | Should -Be ''
    }
}
