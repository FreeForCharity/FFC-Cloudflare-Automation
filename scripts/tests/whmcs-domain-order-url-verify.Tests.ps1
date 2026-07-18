<#
.SYNOPSIS
    Pester (v5) unit tests for whmcs-domain-order-url-verify.ps1.

.DESCRIPTION
    Dot-sources the script (which returns after its function definitions when
    dot-sourced) and tests the pure verification logic: WHMCS list-shape
    parsing, custom-field extraction, and the URL liveness / footer-marker
    check with a mocked Invoke-WebRequest. No network or WHMCS calls are made.

    Run locally: Invoke-Pester -Path scripts/tests -Output Detailed
#>

BeforeAll {
    . (Join-Path (Split-Path $PSScriptRoot -Parent) 'whmcs-domain-order-url-verify.ps1')
}

Describe 'Get-OrdersFromResponse' {
    It 'parses the nested orders.order wrapper shape' {
        $resp = [pscustomobject]@{
            orders = [pscustomobject]@{
                order = @(
                    [pscustomobject]@{ id = '100'; ordernum = '1000' },
                    [pscustomobject]@{ id = '101'; ordernum = '1001' }
                )
            }
        }
        $orders = Get-OrdersFromResponse -Response $resp
        @($orders).Count | Should -Be 2
        $orders[0].id | Should -Be '100'
    }

    It 'parses a multi-element bare-array orders shape (member-enumeration pitfall)' {
        # On pwsh 7, $resp.orders.order over a plain 2+ element array whose
        # elements lack an "order" property yields a truthy array OF NULLS;
        # naive truthiness-first code drops every order silently.
        $resp = [pscustomobject]@{
            orders = @(
                [pscustomobject]@{ id = '200'; ordernum = '2000' },
                [pscustomobject]@{ id = '201'; ordernum = '2001' },
                [pscustomobject]@{ id = '202'; ordernum = '2002' }
            )
        }
        $orders = Get-OrdersFromResponse -Response $resp
        @($orders).Count | Should -Be 3
        @($orders | ForEach-Object { $_.id }) | Should -Be @('200', '201', '202')
    }

    It 'returns an empty array for missing or empty-string containers' {
        @(Get-OrdersFromResponse -Response ([pscustomobject]@{ result = 'success' })).Count | Should -Be 0
        @(Get-OrdersFromResponse -Response ([pscustomobject]@{ orders = '' })).Count | Should -Be 0
    }
}

Describe 'Get-ProductsFromResponse' {
    It 'parses the nested products.product wrapper shape' {
        $resp = [pscustomobject]@{
            products = [pscustomobject]@{
                product = @([pscustomobject]@{ id = '5'; orderid = '100' })
            }
        }
        $products = Get-ProductsFromResponse -Response $resp
        @($products).Count | Should -Be 1
        $products[0].orderid | Should -Be '100'
    }

    It 'parses a multi-element bare-array products shape (member-enumeration pitfall)' {
        $resp = [pscustomobject]@{
            products = @(
                [pscustomobject]@{ id = '5'; orderid = '100'; domain = 'a.org' },
                [pscustomobject]@{ id = '6'; orderid = '101'; domain = 'b.org' }
            )
        }
        $products = Get-ProductsFromResponse -Response $resp
        @($products).Count | Should -Be 2
        $products[1].domain | Should -Be 'b.org'
    }

    It 'returns an empty array for missing or empty-string containers' {
        @(Get-ProductsFromResponse -Response ([pscustomobject]@{ result = 'success' })).Count | Should -Be 0
        @(Get-ProductsFromResponse -Response ([pscustomobject]@{ products = '' })).Count | Should -Be 0
    }
}

Describe 'Get-CustomFieldNodes' {
    It 'parses the nested customfields.customfield JSON shape' {
        $node = [pscustomobject]@{
            customfields = [pscustomobject]@{
                customfield = @(
                    [pscustomobject]@{ id = '171'; name = 'Live GitHub Pages URL of your validated website'; value = 'https://example.github.io' },
                    [pscustomobject]@{ id = '172'; name = 'Website validated'; value = 'on' }
                )
            }
        }
        $fields = Get-CustomFieldNodes -Node $node
        @($fields).Count | Should -Be 2
        $fields[0].id | Should -Be '171'
        $fields[0].value | Should -Be 'https://example.github.io'
    }

    It 'parses a bare-array customfields shape' {
        $node = [pscustomobject]@{
            customfields = @(
                [pscustomobject]@{ id = '173'; name = 'Live GitHub Pages URL of your validated website'; value = 'https://charity.github.io' }
            )
        }
        $fields = Get-CustomFieldNodes -Node $node
        @($fields).Count | Should -Be 1
        $fields[0].id | Should -Be '173'
    }

    It 'parses a multi-element bare-array customfields shape (member-enumeration pitfall)' {
        # $cf.customfield over a plain 2+ element array of field entries
        # yields a truthy array of nulls; truthiness-first code returned an
        # empty field list here, dropping the URL silently.
        $node = [pscustomobject]@{
            customfields = @(
                [pscustomobject]@{ id = '171'; name = 'Live GitHub Pages URL of your validated website'; value = 'https://charity.github.io' },
                [pscustomobject]@{ id = '172'; name = 'Website validated'; value = 'on' }
            )
        }
        $fields = Get-CustomFieldNodes -Node $node
        @($fields).Count | Should -Be 2
        $fields[0].value | Should -Be 'https://charity.github.io'
    }

    It 'returns an empty array when the node has no custom fields' {
        @(Get-CustomFieldNodes -Node ([pscustomobject]@{ domain = 'x.org' })).Count | Should -Be 0
        @(Get-CustomFieldNodes -Node $null).Count | Should -Be 0
        @(Get-CustomFieldNodes -Node ([pscustomobject]@{ customfields = '' })).Count | Should -Be 0
    }
}

Describe 'Get-GhPagesUrlFromCustomFields' {
    It 'matches the register-product field id 171' {
        $fields = @(
            [pscustomobject]@{ id = '171'; name = 'Live GitHub Pages URL of your validated website'; value = ' https://a.github.io ' },
            [pscustomobject]@{ id = '172'; name = 'Website validated'; value = 'on' }
        )
        Get-GhPagesUrlFromCustomFields -Fields $fields | Should -Be 'https://a.github.io'
    }

    It 'matches the transfer-product field id 173' {
        $fields = @([pscustomobject]@{ id = '173'; name = 'Live GitHub Pages URL of your validated website'; value = 'https://b.github.io' })
        Get-GhPagesUrlFromCustomFields -Fields $fields | Should -Be 'https://b.github.io'
    }

    It 'falls back to a name-pattern match when the id is unknown' {
        $fields = @([pscustomobject]@{ id = '999'; name = 'Live GitHub Pages URL of your validated website'; value = 'https://c.github.io' })
        Get-GhPagesUrlFromCustomFields -Fields $fields | Should -Be 'https://c.github.io'
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
                    Content    = '<html><body><a href="https://github.com/FreeForCharity/FFC-EX-x.org">source</a></body></html>'
                }
            }
        }

        It 'passes with footer OK' {
            $r = Test-LiveFfcUrl -Url 'https://charity.github.io' -Marker 'Free\s?For\s?Charity'
            $r.Pass | Should -BeTrue
            $r.StatusCode | Should -Be 200
            $r.FooterCheck | Should -Be 'OK'
        }

        It 'sends a browser User-Agent (Imunify360 blocks the pwsh default)' {
            Test-LiveFfcUrl -Url 'https://charity.github.io' -Marker 'Free\s?For\s?Charity' | Out-Null
            Should -Invoke Invoke-WebRequest -ParameterFilter { $UserAgent -match 'Chrome' }
        }

        It 'matches the marker case-insensitively' {
            (Test-LiveFfcUrl -Url 'https://charity.github.io' -Marker 'FREEFORCHARITY').FooterCheck | Should -Be 'OK'
        }

        It 'matches the spaced placeholder form with the default pattern' {
            Mock Invoke-WebRequest {
                [pscustomobject]@{ StatusCode = 200; Content = '<footer>Free For Charity</footer>' }
            }
            (Test-LiveFfcUrl -Url 'https://charity.github.io' -Marker 'Free\s?For\s?Charity').FooterCheck | Should -Be 'OK'
        }

        It 'normalizes a scheme-less URL to https' {
            $r = Test-LiveFfcUrl -Url 'charity.github.io' -Marker 'Free\s?For\s?Charity'
            $r.Pass | Should -BeTrue
            $r.Url | Should -Be 'https://charity.github.io'
        }

        It 'decodes a byte[] Content body before matching the marker' {
            Mock Invoke-WebRequest {
                [pscustomobject]@{
                    StatusCode = 200
                    Content    = [System.Text.Encoding]::UTF8.GetBytes('<footer>FreeForCharity</footer>')
                }
            }
            $r = Test-LiveFfcUrl -Url 'https://charity.github.io' -Marker 'Free\s?For\s?Charity'
            $r.Pass | Should -BeTrue
            $r.FooterCheck | Should -Be 'OK'
        }
    }

    Context 'HTTP 200 without the marker' {
        BeforeAll {
            Mock Invoke-WebRequest {
                [pscustomobject]@{ StatusCode = 200; Content = '<html><body>Under construction</body></html>' }
            }
        }

        It 'still passes liveness but reports footer WARN' {
            $r = Test-LiveFfcUrl -Url 'https://charity.github.io' -Marker 'Free\s?For\s?Charity'
            $r.Pass | Should -BeTrue
            $r.FooterCheck | Should -Be 'WARN'
            $r.FooterNote | Should -Match 'not found'
        }
    }

    Context 'non-GitHub-Pages hosts' {
        BeforeAll {
            Mock Invoke-WebRequest { throw 'should not be called' }
        }

        It 'fails a non-github.io host without making an HTTP call' {
            $r = Test-LiveFfcUrl -Url 'https://charity.wixsite.com/mysite' -Marker 'Free\s?For\s?Charity'
            $r.Pass | Should -BeFalse
            $r.Reason | Should -Match 'not a GitHub Pages URL'
            Should -Invoke Invoke-WebRequest -Times 0
        }

        It 'fails an apex-domain host even when it embeds github.io in the path' {
            $r = Test-LiveFfcUrl -Url 'https://example.org/charity.github.io' -Marker 'Free\s?For\s?Charity'
            $r.Pass | Should -BeFalse
            $r.Reason | Should -Match 'not a GitHub Pages URL'
        }

        It 'fails a lookalike host that merely ends in github.io without the dot' {
            $r = Test-LiveFfcUrl -Url 'https://evilgithub.io.example.com' -Marker 'Free\s?For\s?Charity'
            $r.Pass | Should -BeFalse
            $r.Reason | Should -Match 'not a GitHub Pages URL'
        }
    }

    Context 'non-200 responses' {
        BeforeAll {
            Mock Invoke-WebRequest {
                [pscustomobject]@{ StatusCode = 404; Content = 'Not Found' }
            }
        }

        It 'fails with the HTTP status in the reason' {
            $r = Test-LiveFfcUrl -Url 'https://charity.github.io/missing' -Marker 'Free\s?For\s?Charity'
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
            $r = Test-LiveFfcUrl -Url 'https://does-not-resolve.github.io' -Marker 'Free\s?For\s?Charity'
            $r.Pass | Should -BeFalse
            $r.Reason | Should -Match 'request failed'
        }
    }

    Context 'invalid input (no HTTP call made)' {
        BeforeAll {
            Mock Invoke-WebRequest { throw 'should not be called' }
        }

        It 'fails on a missing URL' {
            $r = Test-LiveFfcUrl -Url '' -Marker 'Free\s?For\s?Charity'
            $r.Pass | Should -BeFalse
            $r.Reason | Should -Match 'missing URL'
            Should -Invoke Invoke-WebRequest -Times 0
        }

        It 'fails on a non-http scheme' {
            $r = Test-LiveFfcUrl -Url 'ftp://charity.github.io' -Marker 'Free\s?For\s?Charity'
            $r.Pass | Should -BeFalse
            $r.Reason | Should -Match 'invalid URL'
            Should -Invoke Invoke-WebRequest -Times 0
        }
    }
}
