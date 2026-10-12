#requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0.0' }
<#
    Unit tests for workflow 232's FraudLabs Pro legs (epic #1618, G1.3b):

      scripts/whmcs-charity-lane-fraud-screen.ps1  the fraud_screen job: one
          stored-verdict lookup per order through 228's Invoke-FraudLabsProApi
          (MOCKED here: no FraudLabs call is ever made);
      scripts/whmcs-charity-lane-fraud-merge.ps1   the finalize job, run end to
          end against fixture files in a child pwsh (it makes no API call at all).

    The verdict logic itself (Get-LaneFraudOutcome, Merge-LaneFraudOutcome,
    Merge-LaneFraudResultSet) is covered in whmcs-charity-lane-verdicts.Tests.ps1.

    Run: Invoke-Pester tests/whmcs-charity-lane-fraud.Tests.ps1
#>

BeforeAll {
    $script:ScriptsDir = Join-Path $PSScriptRoot '..' 'scripts'
    . (Join-Path $script:ScriptsDir 'whmcs-charity-lane-fraud-screen.ps1')
}

Describe 'ConvertTo-LaneFraudLookupList' {
    It 'keeps digit order ids once each, and blanks an unusable lookup id' {
        $in = @(
            [pscustomobject]@{ order_id = '101'; lookup_id = '4455667788' }
            [pscustomobject]@{ order_id = '101'; lookup_id = '9999999999' }
            [pscustomobject]@{ order_id = 102; lookup_id = '' }
            [pscustomobject]@{ order_id = '103'; lookup_id = '12 34&x=1' }
            [pscustomobject]@{ order_id = 'abc'; lookup_id = '1' }
            [pscustomobject]@{ order_id = ''; lookup_id = '1' }
        )
        $out = ConvertTo-LaneFraudLookupList -Entries $in
        @($out.order_id) | Should -Be @('101', '102', '103')
        $out[0].lookup_id | Should -Be '4455667788'
        $out[1].lookup_id | Should -Be ''
        $out[2].lookup_id | Should -Be ''
    }

    It 'an empty list stays empty' {
        @(ConvertTo-LaneFraudLookupList -Entries @()).Count | Should -Be 0
    }
}

Describe 'Lookup-key hand-over: verdicts job -> fraud_screen job' {
    BeforeAll {
        . (Join-Path $script:ScriptsDir 'whmcs-charity-lane-verdicts.ps1')
    }
    It 'the file the verdicts job writes parses back to the same orders' {
        $json = ConvertTo-LaneFraudLookupJson -OrderIds @('9001', '9002') -OrderNumbers @{ '9001' = '4455667788'; '9002' = '1122334455' }
        $out = ConvertTo-LaneFraudLookupList -Entries @($json | ConvertFrom-Json)
        @($out.order_id) | Should -Be @('9001', '9002')
        @($out.lookup_id) | Should -Be @('4455667788', '1122334455')
    }
    It 'a single order survives the round trip' {
        $out = ConvertTo-LaneFraudLookupList -Entries @((ConvertTo-LaneFraudLookupJson -OrderIds @('9001') -OrderIdField 'id') | ConvertFrom-Json)
        @($out).Count | Should -Be 1
        $out[0].lookup_id | Should -Be '9001'
    }
}

Describe 'Get-LaneFraudScreenResult (FraudLabs mocked)' {
    It 'sends the lookup id, exactly as 228 does, and keeps status + score' {
        Mock Invoke-FraudLabsProApi { [pscustomobject]@{ fraudlabspro_status = 'APPROVE'; fraudlabspro_score = '17'; ip_address = '203.0.113.9'; email_domain = 'example.com' } }
        $r = Get-LaneFraudScreenResult -OrderId '101' -LookupId '4455667788' -Key 'k'
        Should -Invoke Invoke-FraudLabsProApi -Times 1 -Exactly -ParameterFilter { $OrderId -eq '4455667788' -and $ApiKey -eq 'k' }
        $r.lookup_ok | Should -BeTrue
        $r.fraudlabs_status | Should -Be 'APPROVE'
        $r.fraudlabs_score | Should -Be '17'
        $r.error | Should -Be ''
    }

    It 'writes only the PII-free result fields, never the rest of the response or the lookup id' {
        Mock Invoke-FraudLabsProApi { [pscustomobject]@{ fraudlabspro_status = 'REVIEW'; fraudlabspro_score = '61'; ip_address = '203.0.113.9'; email_domain = 'example.com' } }
        $r = Get-LaneFraudScreenResult -OrderId '101' -LookupId '4455667788' -Key 'k'
        @($r.PSObject.Properties.Name) | Should -Be @('order_id', 'lookup_ok', 'fraudlabs_status', 'fraudlabs_score', 'error')
        $json = $r | ConvertTo-Json
        $json | Should -Not -Match '203\.0\.113\.9'
        $json | Should -Not -Match 'example\.com'
        $json | Should -Not -Match '4455667788'
    }

    It 'records a FraudLabs error code as a failed lookup' {
        Mock Invoke-FraudLabsProApi { [pscustomobject]@{ fraudlabspro_error_code = '302'; fraudlabspro_message = 'INVALID ORDER ID' } }
        $r = Get-LaneFraudScreenResult -OrderId '101' -LookupId '1' -Key 'k'
        $r.lookup_ok | Should -BeFalse
        $r.error | Should -Be 'FLP_ERROR_302'
        $r.fraudlabs_status | Should -Be ''
    }

    It 'records a thrown request as a failed lookup, with the HTTP status only' {
        Mock Invoke-FraudLabsProApi { throw 'FraudLabs Pro request to https://api.fraudlabspro.com/v2/order/result?key=***&id=1&format=json failed (status: 503): Service Unavailable' }
        $r = Get-LaneFraudScreenResult -OrderId '101' -LookupId '1' -Key 'k'
        $r.lookup_ok | Should -BeFalse
        $r.error | Should -Be 'REQUEST_FAILED_HTTP_503'
    }

    It 'records a thrown request without a status as REQUEST_FAILED' {
        Mock Invoke-FraudLabsProApi { throw 'No such host is known.' }
        (Get-LaneFraudScreenResult -OrderId '101' -LookupId '1' -Key 'k').error | Should -Be 'REQUEST_FAILED'
    }

    It 'never calls FraudLabs without a lookup id' {
        Mock Invoke-FraudLabsProApi { throw 'must not be called' }
        $r = Get-LaneFraudScreenResult -OrderId '101' -LookupId '' -Key 'k'
        Should -Invoke Invoke-FraudLabsProApi -Times 0 -Exactly
        $r.lookup_ok | Should -BeFalse
        $r.error | Should -Be 'NO_LOOKUP_ID'
    }

    It 'drops a free-form status or score instead of copying it' {
        Mock Invoke-FraudLabsProApi { [pscustomobject]@{ fraudlabspro_status = 'APPROVE <b>jane@example.com</b>'; fraudlabspro_score = 'high' } }
        $r = Get-LaneFraudScreenResult -OrderId '101' -LookupId '1' -Key 'k'
        $r.lookup_ok | Should -BeTrue
        $r.fraudlabs_status | Should -Be ''
        $r.fraudlabs_score | Should -Be ''
    }
}

Describe 'whmcs-charity-lane-fraud-screen.ps1 runner (no network)' {
    BeforeAll {
        $script:Screen = Join-Path $script:ScriptsDir 'whmcs-charity-lane-fraud-screen.ps1'
        function Invoke-Screen {
            param([string]$LookupJson)
            $in = Join-Path $TestDrive 'lookups.json'
            $out = Join-Path $TestDrive 'results.json'
            if (Test-Path $out) { Remove-Item $out }
            Set-Content -LiteralPath $in -Value $LookupJson -Encoding utf8
            $saved = $env:FRAUDLABSPRO_API_KEY
            try {
                $env:FRAUDLABSPRO_API_KEY = $null
                $text = & pwsh -NoProfile -File $script:Screen -LookupFile $in -OutputFile $out 2>&1 | Out-String
                return [pscustomobject]@{ Exit = $LASTEXITCODE; Text = $text; Out = $out }
            }
            finally { $env:FRAUDLABSPRO_API_KEY = $saved }
        }
    }

    It 'zero orders writes [] and needs no key' {
        $r = Invoke-Screen -LookupJson '[]'
        $r.Exit | Should -Be 0
        @((Get-Content -LiteralPath $r.Out -Raw) | ConvertFrom-Json).Count | Should -Be 0
    }

    It 'orders without a key fail the job (finalize then fails every row closed)' {
        $r = Invoke-Screen -LookupJson '[{"order_id":"101","lookup_id":"4455667788"}]'
        $r.Exit | Should -Be 1
        $r.Text | Should -Match 'Missing FraudLabs Pro API key'
        Test-Path -LiteralPath $r.Out | Should -BeFalse
    }
}

Describe 'whmcs-charity-lane-fraud-merge.ps1 end to end' {
    BeforeAll {
        $script:Merge = Join-Path $script:ScriptsDir 'whmcs-charity-lane-fraud-merge.ps1'

        # An unscreened report, as the WHMCS job writes it: rows are already
        # PII-free report rows, every one ESCALATE FRAUD_CHECK_NOT_RUN.
        $row = {
            param($Id, $Codes)
            [ordered]@{
                order_id = $Id; client_id = 700 + ($Id % 100); pid = 16; gate = 1; org_name = "Org $Id"; desired_domain = "org$Id.org"
                verdict = 'ESCALATE'; reason_codes = @($Codes) + @('FRAUD_CHECK_NOT_RUN'); readiness_score = 70; fraud_check = 'not_run'; fraud_score = $null
            }
        }
        $script:UnscreenedJson = [ordered]@{
            schema = 'charity-lane-verdicts/v2'; generated_at = '2026-10-11T00:00:00Z'; run_url = 'https://example.invalid/run/1'
            read_only = $true; fraud_check = 'not_run'; fraud_check_note = ''; fraud_score_flag_at = 80
            counts   = [ordered]@{ ACCEPT = 0; NEED_INFO = 0; ESCALATE = 4 }
            verdicts = @(
                (& $row 9001 @()),
                (& $row 9002 @()),
                (& $row 9003 @('TOS_NOT_ATTESTED')),
                (& $row 9004 @())
            )
        } | ConvertTo-Json -Depth 6

        function Invoke-Merge {
            param([string]$ResultsJson, [switch]$NoResults)
            $verdicts = Join-Path $TestDrive 'unscreened.json'
            $results = Join-Path $TestDrive 'results.json'
            $out = Join-Path $TestDrive 'final.json'
            $md = Join-Path $TestDrive 'final.md'
            $summary = Join-Path $TestDrive 'summary.md'
            foreach ($p in @($results, $out, $md, $summary)) { if (Test-Path $p) { Remove-Item $p } }
            Set-Content -LiteralPath $verdicts -Value $script:UnscreenedJson -Encoding utf8
            $resultsArg = ''
            if (-not $NoResults) { Set-Content -LiteralPath $results -Value $ResultsJson -Encoding utf8; $resultsArg = $results }
            $saved = $env:GITHUB_STEP_SUMMARY
            try {
                $env:GITHUB_STEP_SUMMARY = $summary
                $text = & pwsh -NoProfile -File $script:Merge -VerdictsFile $verdicts -FraudResultsFile $resultsArg -OutputFile $out -MarkdownFile $md 2>&1 | Out-String
                $exit = $LASTEXITCODE
            }
            finally { $env:GITHUB_STEP_SUMMARY = $saved }
            $report = if (Test-Path $out) { Get-Content -LiteralPath $out -Raw | ConvertFrom-Json } else { $null }
            $by = @{}
            if ($report) { foreach ($v in $report.verdicts) { $by[[string]$v.order_id] = $v } }
            return [pscustomobject]@{ Exit = $exit; Text = $text; Report = $report; By = $by; Summary = $summary; Md = $md }
        }
    }

    It 'applies APPROVE / REJECT / failed lookup / missing result' {
        $r = Invoke-Merge -ResultsJson '[
            {"order_id":"9001","lookup_ok":true,"fraudlabs_status":"APPROVE","fraudlabs_score":"9","error":""},
            {"order_id":"9002","lookup_ok":true,"fraudlabs_status":"REJECT","fraudlabs_score":"88","error":""},
            {"order_id":"9003","lookup_ok":true,"fraudlabs_status":"APPROVE","fraudlabs_score":"9","error":""}
        ]'
        $r.Exit | Should -Be 0 -Because $r.Text
        $r.Report.schema | Should -Be 'charity-lane-verdicts/v2'
        $r.Report.fraud_check | Should -Be 'screened'
        $r.By['9001'].verdict | Should -Be 'ACCEPT'
        $r.By['9001'].fraud_check | Should -Be 'pass'
        $r.By['9002'].verdict | Should -Be 'ESCALATE'
        @($r.By['9002'].reason_codes) | Should -Be @('FRAUD_FLAGGED')
        $r.By['9003'].verdict | Should -Be 'NEED_INFO'
        $r.By['9004'].verdict | Should -Be 'ESCALATE'
        @($r.By['9004'].reason_codes) | Should -Be @('FRAUD_CHECK_FAILED')
        $r.Report.counts.ACCEPT | Should -Be 1
        $r.Report.fraud_counts.failed | Should -Be 1
        (Get-Content -LiteralPath $r.Summary -Raw) | Should -Match 'FraudLabs: pass 2 \| flagged 1 \| review 0 \| failed 1'
        (Get-Content -LiteralPath $r.Md -Raw) | Should -Match '\| 9001 \|'
    }

    It 'no results handed over (fraud_screen failed) -> every row ESCALATE FRAUD_CHECK_FAILED' {
        $r = Invoke-Merge -NoResults
        $r.Exit | Should -Be 0 -Because $r.Text
        $r.Report.counts.ACCEPT | Should -Be 0
        $r.Report.counts.NEED_INFO | Should -Be 0
        $r.Report.fraud_counts.failed | Should -Be 4
        foreach ($v in $r.Report.verdicts) { @($v.reason_codes) | Should -Contain 'FRAUD_CHECK_FAILED' }
        $r.Text | Should -Match 'every row is ESCALATE FRAUD_CHECK_FAILED'
    }

    It 'an unreadable results file -> every row ESCALATE FRAUD_CHECK_FAILED' {
        $r = Invoke-Merge -ResultsJson '{ not json'
        $r.Exit | Should -Be 0 -Because $r.Text
        $r.Report.counts.ACCEPT | Should -Be 0
        $r.Report.fraud_counts.failed | Should -Be 4
    }

    It 'refuses an input that is not the unscreened v2 report' {
        $saved = $script:UnscreenedJson
        try {
            $script:UnscreenedJson = '{"schema":"charity-lane-verdicts/v1","fraud_check":"not_run","verdicts":[]}'
            $r = Invoke-Merge -ResultsJson '[]'
            $r.Exit | Should -Be 1
            $r.Text | Should -Match 'Unexpected input'
        }
        finally { $script:UnscreenedJson = $saved }
    }
}
