<#
.SYNOPSIS
    Pester (v5) unit tests for whmcs-domain-order-url-verify.ps1.

.DESCRIPTION
    Dot-sources the script (which returns after its function definitions when
    dot-sourced) and tests the pure verification logic: custom-field
    extraction and the URL liveness/marker check with a mocked
    Invoke-WebRequest. No network or WHMCS calls are made.

    Run locally: Invoke-Pester -Path scripts/tests -Output Detailed
#>

BeforeAll {
    . (Join-Path (Split-Path $PSScriptRoot -Parent) 'whmcs-domain-order-url-verify.ps1')
}

Describe 'Get-CustomFieldNodes' {
    It 'parses the nested customfields.customfield JSON shape' {
        $node = [pscustomobject]@{
            customfields = [pscustomobject]@{
                customfield = @(
                    [pscustomobject]@{ id = '171'; name = 'Live GitHub Pages URL of your validated website'; value = 'https://example.org' },
                    [pscustomobject]@{ id = '172'; name = 'Website validated'; value = 'on' }
                )
            }
        }
        $fields = Get-CustomFieldNodes -Node $node
        @($fields).Count | Should -Be 2
        $fields[0].id | Should -Be '171'
        $fields[0].value | Should -Be 'https://example.org'
    }

    It 'parses a bare-array customfields shape' {
        $node = [pscustomobject]@{
            customfields = @(
                [pscustomobject]@{ id = '173'; name = 'Live GitHub Pages URL of your validated website'; value = 'https://charity.example' }
            )
        }
        $fields = Get-CustomFieldNodes -Node $node
        @($fields).Count | Should -Be 1
        $fields[0].id | Should -Be '173'
    }

    It 'returns an empty array when the node has no custom fields' {
        @(Get-CustomFieldNodes -Node ([pscustomobject]@{ domain = 'x.org' })).Count | Should -Be 0
        @(Get-CustomFieldNodes -Node $null).Count | Should -Be 0
    }
}

Describe 'Get-GhPagesUrlFromCustomFields' {
    It 'matches the register-product field id 171' {
        $fields = @(
            [pscustomobject]@{ id = '171'; name = 'Live GitHub Pages URL of your validated website'; value = ' https://a.example ' },
            [pscustomobject]@{ id = '172'; name = 'Website validated'; value = 'on' }
        )
        Get-GhPagesUrlFromCustomFields -Fields $fields | Should -Be 'https://a.example'
    }

    It 'matches the transfer-product field id 173' {
        $fields = @([pscustomobject]@{ id = '173'; name = 'Live GitHub Pages URL of your validated website'; value = 'https://b.example' })
        Get-GhPagesUrlFromCustomFields -Fields $fields | Should -Be 'https://b.example'
    }

    It 'falls back to a name-pattern match when the id is unknown' {
        $fields = @([pscustomobject]@{ id = '999'; name = 'Live GitHub Pages URL of your validated website'; value = 'https://c.example' })
        Get-GhPagesUrlFromCustomFields -Fields $fields | Should -Be 'https://c.example'
    }

    It 'ignores a matching field whose value is blank' {
        $fields = @(
            [pscustomobject]@{ id = '171'; name = 'Live GitHub Pages URL of your validated website'; value = '  ' }
        )
        Get-GhPagesUrlFromCustomFields -Fields $fields | Should -BeNullOrEmpty
    }

    It 'returns null when no URL field exists' {
        $fields = @([pscustomobject]@{ id = '172'; name = 'Website validated'; value = 'on' })
        Get-GhPagesUrlFromCustomFields -Fields $fields | Should -BeNullOrEmpty
        Get-GhPagesUrlFromCustomFields -Fields @() | Should -BeNullOrEmpty
    }
}

Describe 'Test-LiveFfcUrl' {
    Context 'HTTP 200 with the FFC footer marker' {
        BeforeAll {
            Mock Invoke-WebRequest {
                [pscustomobject]@{
                    StatusCode = 200
                    Content    = '<html><body><footer>Built by Free For Charity volunteers</footer></body></html>'
                }
            }
        }

        It 'passes' {
            $r = Test-LiveFfcUrl -Url 'https://charity.example' -Marker 'Free For Charity'
            $r.Pass | Should -BeTrue
            $r.StatusCode | Should -Be 200
        }

        It 'matches the marker case-insensitively' {
            (Test-LiveFfcUrl -Url 'https://charity.example' -Marker 'FREE FOR CHARITY').Pass | Should -BeTrue
        }

        It 'normalizes a scheme-less URL to https' {
            $r = Test-LiveFfcUrl -Url 'charity.example' -Marker 'Free For Charity'
            $r.Pass | Should -BeTrue
            $r.Url | Should -Be 'https://charity.example'
        }
    }

    Context 'HTTP 200 without the marker' {
        BeforeAll {
            Mock Invoke-WebRequest {
                [pscustomobject]@{ StatusCode = 200; Content = '<html><body>Under construction</body></html>' }
            }
        }

        It 'fails with a marker-not-found reason' {
            $r = Test-LiveFfcUrl -Url 'https://charity.example' -Marker 'Free For Charity'
            $r.Pass | Should -BeFalse
            $r.Reason | Should -Match 'marker'
        }
    }

    Context 'non-200 responses' {
        BeforeAll {
            Mock Invoke-WebRequest {
                [pscustomobject]@{ StatusCode = 404; Content = 'Not Found' }
            }
        }

        It 'fails with the HTTP status in the reason' {
            $r = Test-LiveFfcUrl -Url 'https://charity.example/missing' -Marker 'Free For Charity'
            $r.Pass | Should -BeFalse
            $r.StatusCode | Should -Be 404
            $r.Reason | Should -Match 'HTTP 404'
        }
    }

    Context 'network errors' {
        BeforeAll {
            Mock Invoke-WebRequest { throw 'No such host is known.' }
        }

        It 'fails with a request-failed reason instead of throwing' {
            $r = Test-LiveFfcUrl -Url 'https://does-not-resolve.example' -Marker 'Free For Charity'
            $r.Pass | Should -BeFalse
            $r.Reason | Should -Match 'request failed'
        }
    }

    Context 'invalid input (no HTTP call made)' {
        BeforeAll {
            Mock Invoke-WebRequest { throw 'should not be called' }
        }

        It 'fails on a missing URL' {
            $r = Test-LiveFfcUrl -Url '' -Marker 'Free For Charity'
            $r.Pass | Should -BeFalse
            $r.Reason | Should -Match 'missing URL'
            Should -Invoke Invoke-WebRequest -Times 0
        }

        It 'fails on a non-http scheme' {
            $r = Test-LiveFfcUrl -Url 'ftp://charity.example' -Marker 'Free For Charity'
            $r.Pass | Should -BeFalse
            $r.Reason | Should -Match 'invalid URL'
            Should -Invoke Invoke-WebRequest -Times 0
        }
    }
}
