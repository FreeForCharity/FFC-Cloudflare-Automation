<#
.SYNOPSIS
    Shared transient-failure classifier for the REST API helpers in this directory.

.DESCRIPTION
    Dot-source from an *-api-common.ps1 helper:
      . (Join-Path $PSScriptRoot 'api-transient-label.ps1')

    SECURITY (#1766, the non-WHMCS half of #1753). A retry loop decides whether to
    retry and then LOGS why. This repository is public, so those Actions logs are
    world-readable, and the reason is the thing that leaks: interpolating
    $_.Exception.Message into a Write-Warning prints it once per attempt, and a
    .NET/WebException message can carry a fragment of the response body.

    This function therefore does both halves of the fix at once:

      1. It returns a label drawn from a CLOSED SET OF LITERALS and never any part
         of its input, so no caller can echo an exception string or a response
         fragment into a public log by routing it through here.

      2. Its status-code rules are SHAPE-ANCHORED. The signature these callers used
         ended in \b(429|502|503|504)\b, which fires on a bare status number
         anywhere in prose, because punctuation and spaces satisfy \b. An ordinary
         permanent error ('Order 502 declined by rule set') was then classified
         transient, retried five times with exponential backoff, and its raw message
         printed on every attempt.

    $Status is read first and is the primary signal: it is an integer taken off the
    exception's Response object, so it is structural rather than text, and it cannot
    be spoofed by the contents of a message. The text rules are only a fallback for
    transport-level failures that never produced a response at all (DNS, TLS,
    timeouts). The status check used to be duplicated at each call site; it lives
    here so a third caller cannot get it subtly wrong.

    Returns $null when nothing matches. Callers MUST treat $null as permanent.

    Deliberately NOT a rule here: the Imunify360 bot-protection challenge that
    Get-WhmcsTransientLabel carries. That is a property of the WHMCS host's cPanel
    stack, not of api.candid.org or api.fraudlabspro.com, and a rule that can never
    fire for the callers of this file would be dead code. The two implementations are
    otherwise convergent; #1763 tracks factoring them together.
#>

function Get-ApiTransientLabel {
    <#
    .SYNOPSIS
        Classify an API failure as transient and return a FIXED label, never input text.

    .PARAMETER Text
        The exception message. Used only as a fallback signal; never echoed back.

    .PARAMETER Status
        The HTTP status code off the exception's Response, when there was one.

    .OUTPUTS
        One of 'rate limited', 'gateway error', 'service unavailable',
        'request timed out' - or $null, which means permanent.
    #>
    param(
        [AllowNull()][string]$Text,
        [AllowNull()][object]$Status
    )

    # Structural signal first. An integer off the response object is not text and
    # carries nothing from the body, so it is both safer and more reliable than any
    # pattern over a message.
    $byStatus = switch ([string]$Status) {
        '429' { 'rate limited' }
        '502' { 'gateway error' }
        '503' { 'service unavailable' }
        '504' { 'gateway error' }
        default { $null }
    }
    if ($byStatus) { return $byStatus }

    if ([string]::IsNullOrWhiteSpace($Text)) { return $null }

    # A status code counts only in a status-line shape. These are the three forms
    # .NET and the hosts actually produce:
    #   PS 7    Response status code does not indicate success: 502 (Bad Gateway).
    #   PS 5.1  The remote server returned an error: (502) Bad Gateway.
    #   raw     HTTP/1.1 503 Service Unavailable
    # The PS 5.1 spelling is load-bearing: windows-latest runners and the Conductor
    # host produce it, so a rule anchored only on the PS 7 wording regresses there.
    #
    # %S% is substituted with .Replace, not the -f operator: the pattern contains
    # [^0-9]{0,40}, and -f would read '{0,40}' as 'argument 0, width 40'.
    $statusShape = 'status code[^0-9]{0,40}%S%\b|returned an error:\s*\(?%S%\)?|HTTP(?:/[0-9.]+)?\s+%S%\b'

    # Ordered so the most specific signature wins; every key here is a literal
    # label, which is the whole point.
    $rules = [ordered]@{
        'rate limited'        = 'too many requests|' + $statusShape.Replace('%S%', '429')
        'gateway error'       = 'Bad Gateway|Gateway Time-?out|' + $statusShape.Replace('%S%', '(?:502|504)')
        'service unavailable' = 'Service Unavailable|temporarily unavailable|' + $statusShape.Replace('%S%', '503')
        # Subsumes .NET's 'The operation has timed out'.
        'request timed out'   = 'timed out'
    }

    foreach ($label in $rules.Keys) {
        if ($Text -match $rules[$label]) { return $label }
    }
    return $null
}
