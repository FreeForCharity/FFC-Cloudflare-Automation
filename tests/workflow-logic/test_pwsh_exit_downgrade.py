"""Unit tests for the downgraded-`$LASTEXITCODE` guard (#1068).

The load-bearing tests are `test_the_exact_pre_fix_101_body_is_a_finding` and
`test_the_m365_sibling_in_the_same_file_is_clean`. They are the two halves of
#1068's own evidence: the body that shipped and failed, and the body 160 lines
below it in the same workflow that does the same thing correctly. A guard that
cannot tell those two apart is worthless here, because the tree is clean by
construction once the fix lands -- every other test in this module would also
pass against a `scan_body` that returned `[]` unconditionally.

`test_the_live_m365_step_is_flagged_when_its_exit_0_is_removed` is the mutation
#1068's Verification section asks for by name, run against the **real** workflow
text rather than a fixture, so a refactor that moves the idiom cannot leave this
module quietly testing a string nobody ships. Per AGENTS.md the plant is
asserted to have landed -- the occurrence count is checked before the mutant's
verdict is read -- because a mutation that no longer applies is otherwise
indistinguishable from a mutation that was caught.

The propagation tests exist for the opposite polarity. `$code = $LASTEXITCODE`
followed by `if ($code -ne 0) { exit $code }` is correct, common, and must stay
silent; a guard that flags it would fire on most of this repo's pwsh steps and
be switched off within a week.

WHICH OF THE STRING-LITERAL TESTS ACTUALLY DISCRIMINATE (measured, #1347)
    Copilot raised two defects in the first revision's quote handling. They are
    not worth the same, and the tests say so rather than presenting eight
    equally-weighted green rows:

    * **Terminator keywords inside a literal — REAL, and the serious one.**
      `Write-Warning "tolerated exit $code"` matched `TERMINATES_RE`, so a live
      downgrade was filed as propagation and never checked. Replayed against
      the pre-fix revision, `test_the_word_exit_inside_a_warning_string_…`,
      `…_throw_…`, `test_an_error_annotation_without_an_exit_…` and
      `test_a_string_holding_only_the_word_exit_…` all FAIL there and pass
      here. Those four are discriminators.
    * **Doubled quotes and the single-quote backtick — correctness only, NOT
      reachable.** The reading of the code was accurate, but no verdict moves:
      11 candidate bodies built to exploit it (doubled quotes followed by a `{`
      or a `#`, a backtick against the closing quote) returned **identical
      verdicts on both revisions**. The reason is structural — an adjacent
      doubled quote is two toggles that cancel, so parity is preserved, and the
      backtick mis-parse only runs a literal to end-of-line, where this
      line-based scanner resets anyway. Their individual mutations survive.

    So `test_a_doubled_double_quote_…`, `test_a_doubled_single_quote_…` and
    `test_a_backtick_is_literal_…` are **regression pins, not discriminators**,
    and are labelled that way so a later reader does not mistake them for
    evidence. They are kept because they pin the lexer as a whole: deleting
    `_scan_line` outright reddens 8 tests in this module, these three among
    them.

    `test_a_backtick_escaped_quote_…` was in that list until Copilot pointed
    out the fixture did not contain what its name claimed -- a backslash and a
    backtick escaping a SPACE, rather than a backtick-escaped double quote.
    With the real sequence it **discriminates**: removing the backtick branch
    from `_scan_line` now reddens it, where before the mutation survived. A
    fixture that tests something easier than its name is the quietest way for a
    suite to overstate itself, and it was found by review, not by the mutation
    pass -- the mutation pass had already scored that branch as untested and
    was right for the wrong reason.

SECOND REVIEW ROUND (#1347, Copilot HIGH)
    `test_a_closing_brace_on_the_elseif_header_does_not_close_the_new_block`
    and its `…_still_reports_a_genuine_downgrade` sibling cover a real false
    positive: `_if_block` counted the leading `}` of a `} elseif (...) {`
    header, which belongs to the PREVIOUS block, so depth returned to 0 on the
    header line and the block was declared closed before its body was read. An
    `exit` inside it was invisible and a propagating block was reported as a
    downgrade. `test_re` matches that spelling deliberately, so it is a shape
    the guard was written for, not an exotic input.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
GUARD = REPO_ROOT / "scripts" / "check-pwsh-exit-downgrade.py"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "101-domain-status.yml"

_spec = importlib.util.spec_from_file_location("check_pwsh_exit_downgrade", GUARD)
guard = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
# Registered BEFORE exec: `@dataclass` resolves its field annotations through
# `sys.modules[cls.__module__]`, which is None for a module loaded by spec alone
# -- the import raises `AttributeError: 'NoneType' object has no attribute
# '__dict__'` inside dataclasses, nowhere near the guard's own code.
sys.modules[_spec.name] = guard
_spec.loader.exec_module(guard)


def _kinds(body: str) -> list[str]:
    return [f.kind for f in guard.scan_body(body)]


# --- the two halves of #1068's evidence -------------------------------------

# Verbatim from `101-domain-status.yml`'s `cloudflare` job as it shipped before
# the fix (run 30953491678). Shortened only by dropping the Cloudflare audit and
# dry-run calls, which play no part in the defect.
PRE_FIX_101 = """
$ErrorActionPreference = 'Stop'
$domain = $env:IN_DOMAIN
$outDir = Join-Path $env:RUNNER_TEMP 'domain-status-cloudflare'

$dkim = & pwsh -NoProfile -File .\\scripts\\m365-domain-preflight.ps1 -Domain $domain -SkipGraph -CloudflareToken $cfToken *>&1
$dkimExit = $LASTEXITCODE
$dkimPath = Join-Path $outDir 'cloudflare-dkim-check.txt'
$dkim | Out-File -FilePath $dkimPath -Encoding utf8
if ($dkimExit -ne 0) {
  Write-Warning "m365-domain-preflight.ps1 (DKIM Cloudflare-only) exited with code $dkimExit (output saved to $dkimPath)"
}

"out_dir=$outDir" | Out-File -FilePath $env:GITHUB_OUTPUT -Append -Encoding utf8
"""

# The same idiom from the `m365` job of the same file, which ends `exit 0`.
CORRECT_SIBLING = """
$ErrorActionPreference = 'Stop'
$pre = & pwsh -NoProfile -File .\\scripts\\m365-domain-preflight.ps1 -Domain $domain -SkipCloudflare *>&1
$preExit = $LASTEXITCODE
if ($preExit -ne 0) {
  Write-Warning "m365-domain-preflight.ps1 exited with code $preExit"
}

$dns = & pwsh -NoProfile -File .\\scripts\\m365-domain-status.ps1 -Domain $domain *>&1
$dnsExit = $LASTEXITCODE
if ($dnsExit -ne 0) {
  Write-Warning "m365-domain-status.ps1 exited with code $dnsExit"
}

"out_dir=$outDir" | Out-File -FilePath $env:GITHUB_OUTPUT -Append -Encoding utf8

exit 0
"""


def test_the_exact_pre_fix_101_body_is_a_finding():
    """Without this the module asserts only that a clean tree is clean."""
    assert _kinds(PRE_FIX_101) == [guard.NO_EXIT], (
        "the body that actually failed run 30953491678 must be a finding; got "
        f"{_kinds(PRE_FIX_101)!r}"
    )


def test_the_m365_sibling_in_the_same_file_is_clean():
    """Two downgrades and an `exit 0`. The closest possible true negative."""
    assert _kinds(CORRECT_SIBLING) == [], (
        "the correct idiom from the same workflow must not be flagged; got "
        f"{_kinds(CORRECT_SIBLING)!r}"
    )


def test_the_two_bodies_differ_only_in_the_exit():
    """Guards against a fixture pair that disagrees for some unrelated reason."""
    patched = PRE_FIX_101.rstrip() + "\n\nexit 0\n"
    assert _kinds(patched) == [], (
        "adding `exit 0` to the pre-fix body must clear the finding -- otherwise "
        f"the fixtures differ in more than the exit; got {_kinds(patched)!r}"
    )


# --- the opposite polarity: propagation must stay silent --------------------


def test_propagating_the_exit_code_is_not_a_finding():
    body = """
$code = $LASTEXITCODE
if ($code -ne 0) { exit $code }
"done" | Out-File -FilePath $env:GITHUB_OUTPUT -Append
"""
    assert _kinds(body) == [], f"propagation is correct handling; got {_kinds(body)!r}"


def test_throwing_on_the_exit_code_is_not_a_finding():
    body = """
$code = $LASTEXITCODE
if ($code -ne 0) {
  throw "preflight failed with $code"
}
"done" | Out-File -FilePath $env:GITHUB_OUTPUT -Append
"""
    assert _kinds(body) == [], f"`throw` is propagation; got {_kinds(body)!r}"


def test_an_error_annotation_and_exit_is_not_a_finding():
    body = """
$code = $LASTEXITCODE
if ($code -ne 0) {
  Write-Output "::error::preflight failed with $code"
  exit 1
}
"done" | Out-File -FilePath $env:GITHUB_OUTPUT -Append
"""
    assert _kinds(body) == [], f"annotate-and-exit is propagation; got {_kinds(body)!r}"


def test_a_body_that_never_captures_lastexitcode_is_not_scanned():
    body = """
$ErrorActionPreference = 'Stop'
& pwsh -NoProfile -File .\\scripts\\thing.ps1
"done" | Out-File -FilePath $env:GITHUB_OUTPUT -Append
"""
    assert _kinds(body) == [], f"no capture, nothing to say; got {_kinds(body)!r}"


# --- fail-closed shapes -----------------------------------------------------


def test_a_conditional_terminal_exit_is_its_own_finding():
    """`if ($x) { exit 1 }` leaves $LASTEXITCODE untouched on the other branch."""
    body = """
$code = $LASTEXITCODE
if ($code -ne 0) {
  Write-Warning "tolerated: $code"
}
if ($somethingElse) { exit 1 }
"""
    assert _kinds(body) == [guard.CONDITIONAL_EXIT], (
        f"a conditional final exit must be distinguished, not accepted; got {_kinds(body)!r}"
    )


def test_a_closing_brace_on_the_elseif_header_does_not_close_the_new_block():
    """`} elseif (...) {` -- the leading `}` closes the PREVIOUS block.

    Counting it drove depth to -1, the trailing `{` brought it back to 0, and
    the block was declared closed on its own header line. The body was then
    never examined, so the `exit $code` inside it was invisible and a
    PROPAGATING block was reported as a downgrade (Copilot HIGH, #1347).
    `test_re` matches this spelling deliberately, so it is not an exotic input.
    """
    body = """
$code = $LASTEXITCODE
if ($somethingElse -eq 1) {
  Write-Output 'a'
} elseif ($code -ne 0) {
  exit $code
}
"done" | Out-File -FilePath $env:GITHUB_OUTPUT -Append
"""
    assert _kinds(body) == [], (
        "the elseif block propagates via `exit $code`, so there is nothing to "
        f"report; got {_kinds(body)!r}"
    )


def test_the_elseif_form_still_reports_a_genuine_downgrade():
    """The other polarity of the same fix: having stopped closing the block on
    its header, the scanner must still read the body and find the downgrade."""
    body = """
$code = $LASTEXITCODE
if ($somethingElse -eq 1) {
  Write-Output 'a'
} elseif ($code -ne 0) {
  Write-Warning "tolerated: $code"
}
"done" | Out-File -FilePath $env:GITHUB_OUTPUT -Append
"""
    assert _kinds(body) == [guard.NO_EXIT], (
        f"the elseif block tolerates the failure and nothing exits; got {_kinds(body)!r}"
    )


def test_an_unclosable_if_block_is_reported_rather_than_skipped():
    body = """
$code = $LASTEXITCODE
if ($code -ne 0) {
  Write-Warning "tolerated: $code
"""
    assert guard.UNBALANCED in _kinds(body), (
        f"a block the scanner cannot delimit is its blind spot; got {_kinds(body)!r}"
    )


# --- string literals are data, not syntax (Copilot, #1347) ------------------
#
# These are the highest-value tests in the module after the two halves of
# #1068's evidence. Each one was a live defect in the first revision, and every
# one of them failed SILENTLY and in the permissive direction: the guard said
# nothing and the reader had no way to know it had stopped looking.


def test_the_word_exit_inside_a_warning_string_is_not_propagation():
    """The false negative that matters most: a downgrade whose message happens
    to contain the word `exit` was classified as propagation and skipped."""
    body = """
$code = $LASTEXITCODE
if ($code -ne 0) {
  Write-Warning "tolerated exit $code from the preflight"
}
"done" | Out-File -FilePath $env:GITHUB_OUTPUT -Append
"""
    assert _kinds(body) == [guard.NO_EXIT], (
        "a terminator keyword inside a string literal must not count as "
        f"propagation -- that is a silent miss in a guard; got {_kinds(body)!r}"
    )


def test_the_word_throw_inside_a_string_is_not_propagation():
    body = """
$code = $LASTEXITCODE
if ($code -ne 0) {
  Write-Warning "will not throw for $code"
}
"done" | Out-File -FilePath $env:GITHUB_OUTPUT -Append
"""
    assert _kinds(body) == [guard.NO_EXIT], f"got {_kinds(body)!r}"


def test_an_error_annotation_without_an_exit_is_still_a_downgrade():
    """`::error::` annotates; it does not change the step's exit status, so a
    block that annotates and falls through is still relying on the epilogue."""
    body = """
$code = $LASTEXITCODE
if ($code -ne 0) {
  Write-Output "::error::preflight failed with $code"
}
"done" | Out-File -FilePath $env:GITHUB_OUTPUT -Append
"""
    assert _kinds(body) == [guard.NO_EXIT], (
        f"an annotation is not a terminator; got {_kinds(body)!r}"
    )


def test_a_doubled_double_quote_does_not_end_the_string():
    """REGRESSION PIN, not a discriminator -- this passes on the pre-fix
    revision too (see the module docstring). `""` is PowerShell's escape for a
    literal quote; reading it as close-then-reopen is wrong, but it is two
    toggles and they cancel, so no verdict was ever reachable through it."""
    body = '''
$code = $LASTEXITCODE
if ($code -ne 0) {
  Write-Warning "he said ""hi"" # not a comment { not a brace"
}
"done" | Out-File -FilePath $env:GITHUB_OUTPUT -Append
'''
    assert _kinds(body) == [guard.NO_EXIT], (
        f"a doubled quote must not end the literal; got {_kinds(body)!r}"
    )


def test_a_doubled_single_quote_does_not_end_the_string():
    """REGRESSION PIN, not a discriminator -- passes on the pre-fix revision too
    (see the module docstring): unreachable, not merely unexercised."""
    body = """
$code = $LASTEXITCODE
if ($code -ne 0) {
  Write-Warning 'it''s tolerated # not a comment { not a brace'
}
"done" | Out-File -FilePath $env:GITHUB_OUTPUT -Append
"""
    assert _kinds(body) == [guard.NO_EXIT], f"got {_kinds(body)!r}"


def test_a_backtick_is_literal_inside_a_single_quoted_string():
    """REGRESSION PIN, not a discriminator -- passes on the pre-fix revision too
    (see the module docstring): unreachable, not merely unexercised."""
    body = """
$code = $LASTEXITCODE
if ($code -ne 0) {
  Write-Warning 'a backtick ` is literal here'
}
"done" | Out-File -FilePath $env:GITHUB_OUTPUT -Append
"""
    assert _kinds(body) == [guard.NO_EXIT], f"got {_kinds(body)!r}"


def test_a_backtick_escaped_quote_inside_a_double_quoted_string_is_data():
    """A backtick-escaped DOUBLE QUOTE, which is what the name says and what the
    first version of this fixture did not contain -- it held a backtick escaping
    a space, so it exercised nothing the plain cases did not (Copilot, #1347).

    With the real sequence the test discriminates: if the backtick does not
    escape, the `"` ends the literal, the `{` after it counts as syntax, and the
    block scanner reports `unbalanced-if-block` instead of the real finding.
    """
    body = """
$code = $LASTEXITCODE
if ($code -ne 0) {
  Write-Warning "an escaped quote `" is still inside { not a brace"
}
"done" | Out-File -FilePath $env:GITHUB_OUTPUT -Append
"""
    assert _kinds(body) == [guard.NO_EXIT], f"got {_kinds(body)!r}"


def test_a_literal_only_final_line_is_the_last_statement():
    """`_scan_line` keeps string DELIMITERS in the `code` view.

    Blanking them too made a line holding nothing but a literal come out empty,
    so `_last_statement` skipped it and named an earlier line instead. Here that
    turns a correct `no-terminal-exit` into `conditional-terminal-exit`, because
    the line it falls back to is the conditional `exit` above (Copilot, #1347).
    """
    body = """
$code = $LASTEXITCODE
if ($code -ne 0) {
  Write-Warning "tolerated: $code"
}
if ($other) { exit 1 }
"done"
"""
    assert _kinds(body) == [guard.NO_EXIT], (
        "the literal on the final line is the last statement, and it is not an "
        f"exit; got {_kinds(body)!r}"
    )


def test_the_reported_last_statement_is_the_literal_not_the_brace():
    """The same defect seen through the finding's own text. A detail that names
    the wrong line sends the reader to the wrong place, which is the failure
    this guard exists to stop -- one level up."""
    body = """
$code = $LASTEXITCODE
if ($code -ne 0) {
  Write-Warning "tolerated: $code"
}
"exit 0"
"""
    findings = guard.scan_body(body)
    assert len(findings) == 1, f"expected one finding; got {findings!r}"
    assert findings[0].line == 6, (
        f"the finding must point at the literal on line 6, not the closing brace "
        f"on line 5; got line {findings[0].line}"
    )
    assert '"exit 0"' in findings[0].detail, (
        f"the detail must quote the literal it is talking about; got {findings[0].detail!r}"
    )


def test_a_string_holding_only_the_word_exit_is_not_a_terminal_exit():
    """The last statement is a literal, not an `exit`."""
    body = """
$code = $LASTEXITCODE
if ($code -ne 0) {
  Write-Warning "tolerated: $code"
}
"exit 0"
"""
    assert _kinds(body) == [guard.NO_EXIT], (
        f"a quoted 'exit 0' is a string, not a statement; got {_kinds(body)!r}"
    )


def test_a_brace_inside_a_string_does_not_break_the_block_scanner():
    """Without quote-awareness this reads as an extra `{` and reports UNBALANCED."""
    body = """
$code = $LASTEXITCODE
if ($code -ne 0) {
  Write-Warning "tolerated { not a block: $code"
}
"done" | Out-File -FilePath $env:GITHUB_OUTPUT -Append
"""
    assert _kinds(body) == [guard.NO_EXIT], (
        f"a brace inside a string is data, and the real finding must survive it; got {_kinds(body)!r}"
    )


def test_a_github_expression_does_not_read_as_an_unbalanced_block():
    """`${{ … }}` reaches the scanner unmasked, on purpose (see the guard's
    comment). These bodies are the check that leaving it there is safe."""
    bodies = [
        "$domain = '${{ inputs.domain }}'",
        "$sep = '${{ inputs.sep == '{' && 'a' || 'b' }}'",
        "$multi = '${{ inputs.a\n  && inputs.b }}'",
    ]
    for prefix in bodies:
        body = (
            prefix
            + """
$code = $LASTEXITCODE
if ($code -ne 0) {
  Write-Warning "tolerated: $code"
}
"done" | Out-File -FilePath $env:GITHUB_OUTPUT -Append
"""
        )
        assert _kinds(body) == [guard.NO_EXIT], (
            f"expression text must not manufacture an UNBALANCED, and must not hide "
            f"the real finding; {prefix!r} gave {_kinds(body)!r}"
        )


def test_an_expression_inside_the_tolerated_block_does_not_hide_the_finding():
    body = """
$code = $LASTEXITCODE
if ($code -ne 0) {
  Write-Warning "${{ inputs.msg }} $code"
}
"done" | Out-File -FilePath $env:GITHUB_OUTPUT -Append
"""
    assert _kinds(body) == [guard.NO_EXIT], (
        f"an expression inside the block must not change the verdict; got {_kinds(body)!r}"
    )


def test_a_trailing_comment_is_not_the_last_statement():
    body = CORRECT_SIBLING + "\n# trailing note about the exit above\n"
    assert _kinds(body) == [], (
        f"comments must be stripped before reading the last statement; got {_kinds(body)!r}"
    )


def test_exit_with_a_variable_is_accepted():
    body = """
$code = $LASTEXITCODE
if ($code -ne 0) {
  Write-Warning "tolerated: $code"
}
exit $deliberateStatus
"""
    assert _kinds(body) == [], (
        f"an author who computed a status deliberately is not the target; got {_kinds(body)!r}"
    )


# --- against the real tree ---------------------------------------------------


def test_the_repository_is_clean_under_the_guard():
    assert guard.main() == 0, "the tree must be green under its own guard"


def test_the_live_101_cloudflare_step_now_ends_with_an_explicit_exit():
    text = WORKFLOW.read_text(encoding="utf-8")
    findings = guard.scan_workflow(text, WORKFLOW.name)
    assert findings == [], f"101-domain-status.yml must be clean; got {findings!r}"


def test_the_live_101_cloudflare_step_still_tolerates_the_dkim_warning():
    """AC1: the warning survives. A fix that deleted the downgrade would also
    turn the guard green, and would be the wrong fix."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "$dkimExit = $LASTEXITCODE" in text, "the DKIM capture was removed"
    assert "(DKIM Cloudflare-only) exited with code $dkimExit" in text, (
        "the tolerated-warning message was removed; AC1 requires it still be emitted"
    )


def test_the_live_101_cloudflare_step_still_fails_on_a_genuine_error():
    """AC2: the two Update-CloudflareDns.ps1 calls are inspected, not ignored."""
    text = WORKFLOW.read_text(encoding="utf-8")
    for anchor in ("$auditExit = $LASTEXITCODE", "$dryExit = $LASTEXITCODE"):
        assert anchor in text, (
            f"{anchor} missing: a genuine Cloudflare failure would be silent again (AC2)"
        )
    assert "Refusing to run the DKIM preflight unauthenticated" in text, (
        "the empty-token guard was removed (AC2)"
    )


def test_ac3_decision_is_recorded_in_the_workflow():
    """AC3 is 'state the decision either way'. A decision that lives only in a PR
    body is not available to the next reader of the file."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "DELIBERATELY NOT `if: always()`" in text, (
        "AC3's decision must be recorded beside the `needs:` it is about"
    )


def test_the_live_m365_step_is_flagged_when_its_exit_0_is_removed():
    """The mutation #1068's Verification section asks for, by name.

    Run on the real workflow text in memory -- nothing on disk is touched, so
    there is no restore to get wrong (CLAUDE.md's read_text/write_text CRLF
    trap, and the `git checkout --` trap of L182).
    """
    text = WORKFLOW.read_text(encoding="utf-8")

    anchor = "\n\n          exit 0\n"
    landed = text.count(anchor)
    assert landed == 1, (
        f"the mutation anchor {anchor!r} appears {landed} times, not 1 -- the m365 "
        "step's idiom moved, so this mutation would test nothing (assert the plant "
        "landed before reading the mutant's verdict)"
    )

    mutant = text.replace(anchor, "\n")
    findings = guard.scan_workflow(mutant, WORKFLOW.name)
    assert findings, (
        "removing the m365 step's `exit 0` must be caught by the guard; it was not, "
        "so the guard is permissive rather than discriminating"
    )
    assert any("m365" in f for f in findings), (
        f"the finding must name the mutated job; got {findings!r}"
    )

    assert guard.scan_workflow(text, WORKFLOW.name) == [], (
        "the unmutated text must still be clean (restore check)"
    )


# --- wiring ------------------------------------------------------------------


def test_the_guard_is_wired_into_ci():
    """A checker nobody runs is the failure mode this whole class is about."""
    ci = (REPO_ROOT / ".github" / "workflows" / "722-ci.yml").read_text(encoding="utf-8")
    assert "scripts/check-pwsh-exit-downgrade.py" in ci, (
        "check-pwsh-exit-downgrade.py must run in 722-ci.yml, or the rule is documentation"
    )


def test_the_guard_installs_its_own_yaml_dependency_in_ci():
    """The runner image only happens to preinstall PyYAML; without the install an
    image that drops it turns this guard into an ImportError, which is a red build
    that says nothing about exit codes."""
    ci = (REPO_ROOT / ".github" / "workflows" / "722-ci.yml").read_text(encoding="utf-8")

    # Assert each marker BEFORE splitting on it. `split(...)[1]` raises IndexError
    # on a missing marker, and this module's runner catches only AssertionError --
    # so a renamed step would abort the module mid-roster and take the 11 tests
    # that sort after this one with it, reporting a harness death rather than a
    # wiring failure (L194).
    step = "Validate pwsh steps that downgrade an exit code"
    assert step in ci, (
        f"722-ci.yml no longer has a step named {step!r} -- if it was renamed, "
        "update this test; if it was removed, the guard is no longer wired in"
    )
    block = ci.split(step, 1)[1]

    invocation = "scripts/check-pwsh-exit-downgrade.py"
    assert invocation in block, (
        f"the {step!r} step no longer invokes {invocation} -- the step name and "
        "its command have drifted apart"
    )
    block = block.split(invocation, 1)[0]

    assert "pip install --quiet pyyaml" in block, (
        "the exit-downgrade step must install PyYAML defensively, like its siblings"
    )


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {str(e)[:400]}")
    sys.exit(1 if failures else 0)
