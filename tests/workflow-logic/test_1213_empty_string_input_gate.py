r"""`-ne ''` is not an emptiness test — it passes whitespace and passes $null (#1213).

WHAT THIS MODULE PINS
    Three things, in ascending order of how easy they are to lose:

    1. The TRUTH TABLE, measured against a real PowerShell host rather than
       asserted in a comment. `-ne ''` and `-not IsNullOrWhiteSpace` disagree on
       two of the four blank forms, and both disagreements let a blank value
       through.

    2. The CONSEQUENCE, measured against a stub callee using `229`'s own
       hashtable-splat shape: whitespace binds silently at exit 0.

    3. The GUARD, `scripts/check-workflow-empty-string-input-gate.py` — by
       DISCRIMINATION, not by running it once and reading a pass. Reverting each
       real site exits 1 and names it; the property-presence form stays clean.

THE PROBE TRAP THIS MODULE IS WRITTEN AROUND
    Setting the variable from INSIDE PowerShell (`$env:X = ''`) DELETES it. A
    probe written that way reads `$null` where a runner would supply an empty
    string, and the two disagree on exactly the predicate under review
    (`-ne ''` -> True vs False). So every case below sets the variable from
    outside the interpreter — `PROBE_VAL="" pwsh -File ...` for empty, and
    `env -u PROBE_VAL` for the genuinely-unset case. Recorded on #1213 and hit
    independently by two prior runs before it was written down.

WHY THE GATED CASES ARE NAMED INDIVIDUALLY
    `NEEDS_PWSH` lists the cases that spawn a PowerShell host, rather than the
    module gating itself wholesale on `shutil.which`. A whole-module gate would
    report "everything passed" on a host with no PowerShell while every static
    assertion here — the guard's entire discrimination suite, which needs no
    tool at all — went unmeasured (#1182, and
    `test_tool_gated_skips_are_scoped.py`).
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import REPO_ROOT  # noqa: E402

CHECKER = REPO_ROOT / "scripts" / "check-workflow-empty-string-input-gate.py"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"


def _load():
    spec = importlib.util.spec_from_file_location("check_wf_empty_gate", CHECKER)
    assert spec is not None and spec.loader is not None, (
        f"cannot import the checker at {CHECKER} — the path is not an importable "
        "Python source file. If it was renamed, update CHECKER above."
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


guard = _load()
PWSH = shutil.which("pwsh") or shutil.which("powershell")
HAVE_PWSH = PWSH is not None


# ---------------------------------------------------------------------------
# 1. The truth table, measured.
# ---------------------------------------------------------------------------

_PROBE = """
$v = $env:PROBE_VAL
"present={0} ne={1} inw={2}" -f (Test-Path Env:PROBE_VAL), ($v -ne ''), (-not [string]::IsNullOrWhiteSpace($v))
"""


def _probe(value: str | None) -> dict[str, bool]:
    """Run the probe with PROBE_VAL set EXTERNALLY (or genuinely absent)."""
    with tempfile.TemporaryDirectory() as tmp:
        script = pathlib.Path(tmp) / "probe.ps1"
        script.write_text(_PROBE, encoding="utf-8", newline="\n")
        env = dict(os.environ)
        if value is None:
            env.pop("PROBE_VAL", None)
        else:
            env["PROBE_VAL"] = value
        out = subprocess.run(
            [PWSH, "-NoProfile", "-File", str(script)],
            capture_output=True, text=True, encoding="utf-8", env=env,
        )
        assert out.returncode == 0, f"probe did not run: {out.stderr[:400]}"
        parts = dict(p.split("=", 1) for p in out.stdout.strip().split())
        return {k: v == "True" for k, v in parts.items()}


def test_ne_empty_is_true_for_an_unset_variable():
    row = _probe(None)
    assert row["present"] is False, "the unset case did not actually unset the variable"
    assert row["ne"] is True, (
        "`-ne ''` was False for an unset variable — the whole finding rests on "
        "this being True, so a False here means the probe is not measuring the "
        "predicate under review"
    )
    assert row["inw"] is False


def test_ne_empty_is_true_for_whitespace():
    row = _probe("   ")
    assert row["present"] is True
    assert row["ne"] is True, "`-ne ''` was False for whitespace"
    assert row["inw"] is False, (
        "IsNullOrWhiteSpace accepted whitespace — it is the whole remedy, so "
        "this failing means the remedy does not do what #1213 claims"
    )


def test_the_two_predicates_agree_on_empty_and_on_a_real_value():
    """The rows where nothing is wrong — a control against a probe that always
    reports a disagreement (which would make the two cases above vacuous)."""
    empty = _probe("")
    assert empty["present"] is True, (
        "setting PROBE_VAL='' from outside the interpreter did not produce a "
        "PRESENT, empty variable — this is the exact trap the module docstring "
        "describes, and every row below it is now measuring $null instead"
    )
    assert empty["ne"] is False and empty["inw"] is False

    real = _probe("D1")
    assert real["ne"] is True and real["inw"] is True


# ---------------------------------------------------------------------------
# 2. The consequence, measured against a stub callee (229's hashtable splat).
# ---------------------------------------------------------------------------

_STUB = """
param([string]$ClientId, [string]$Email)
"CALLEE ClientId=[{0}] Email=[{1}] BoundClientId={2}" -f $ClientId, $Email, $PSBoundParameters.ContainsKey('ClientId')
"""

_CALLER = """
$ErrorActionPreference = 'Stop'
$params = @{{ }}
if ({predicate}) {{ $params.ClientId = $env:IN_CLIENT_ID }}
& '{pwsh}' -NoProfile -File '{stub}' @params
"""


def _splat(predicate: str, value: str) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = pathlib.Path(tmp)
        stub = tmpdir / "stub.ps1"
        stub.write_text(_STUB, encoding="utf-8", newline="\n")
        caller = tmpdir / "caller.ps1"
        caller.write_text(
            _CALLER.format(predicate=predicate, pwsh=PWSH, stub=stub.as_posix()),
            encoding="utf-8", newline="\n",
        )
        env = dict(os.environ)
        env["IN_CLIENT_ID"] = value
        out = subprocess.run(
            [PWSH, "-NoProfile", "-File", str(caller)],
            capture_output=True, text=True, encoding="utf-8", env=env,
        )
        return out.stdout + out.stderr


def test_without_the_fix_a_whitespace_client_id_reaches_the_callee():
    """The positive control. It asserts on the OBSERVED value, not on the exit
    code: a harness that cannot start also produces "no real value bound", and
    the two must not be confusable (CLAUDE.md, `assert rc != 0`)."""
    out = _splat("$env:IN_CLIENT_ID -ne ''", "   ")
    assert "BoundClientId=True" in out, (
        f"the pre-fix predicate did NOT bind whitespace — if this is failing, "
        f"the control is no longer reproducing the defect it exists to "
        f"demonstrate, and the test below it proves nothing. Got: {out[:400]}"
    )
    assert "ClientId=[   ]" in out, f"expected the whitespace value to arrive verbatim: {out[:400]}"


def test_the_fix_refuses_a_whitespace_client_id():
    out = _splat("-not [string]::IsNullOrWhiteSpace($env:IN_CLIENT_ID)", "   ")
    assert "BoundClientId=False" in out, (
        f"IsNullOrWhiteSpace let whitespace through to the callee: {out[:400]}"
    )


def test_the_fix_still_passes_a_real_value():
    """Refusing everything would pass the test above and break the workflow."""
    out = _splat("-not [string]::IsNullOrWhiteSpace($env:IN_CLIENT_ID)", "419")
    assert "ClientId=[419]" in out, f"a real value was refused: {out[:400]}"


# ---------------------------------------------------------------------------
# 3. The guard, by discrimination.
# ---------------------------------------------------------------------------

def _sample(body: str) -> str:
    """Substitute the body WITHOUT touching the template's own `${{ }}`."""
    assert "__BODY__" in _SAMPLE, "the sample template lost its body placeholder"
    return _SAMPLE.replace("__BODY__", body)


def _scan_text(text: str) -> list:
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "999-sample.yml"
        path.write_text(text, encoding="utf-8", newline="\n")
        return guard.scan_workflow(path)


# NOTE: substituted with `.replace`, NOT `str.format`. `str.format` collapses
# `{{` -> `{`, which silently rewrites this template's own
# `${{ inputs.client_id }}` env mapping into `${ inputs.client_id }` — no longer
# an expression, so `step_env_map` returns {} and the `$env:` case reports NO
# FINDING. That is the flattering direction: it reads as "the guard misses the
# post-#1080 form" when in fact the sample never contained one. Caught here on
# the first run of this module.
_SAMPLE = """\
name: 999. Sample
on:
  workflow_dispatch:
    inputs:
      client_id:
        type: string
      overwrite:
        type: boolean
jobs:
  go:
    runs-on: ubuntu-latest
    steps:
      - name: Run
        env:
          IN_CLIENT_ID: ${{ inputs.client_id }}
        run: |
          __BODY__
"""


def test_the_interpolated_form_is_detected():
    found = _scan_text(_sample("if ('${{ inputs.client_id }}' -ne '') { $x = 1 }"))
    assert [f.input_name for f in found] == ["client_id"], [str(f) for f in found]


def test_the_env_form_is_detected_after_a_1080_conversion():
    """The systemic half: a burn-down lane converts the interpolated form into
    an `$env:` read, which WIDENS the predicate's domain from string to
    string-or-null. A guard that only matched `${{ }}` would go quiet at exactly
    the moment the hazard got worse."""
    found = _scan_text(_sample("if ($env:IN_CLIENT_ID -ne '') { $x = 1 }"))
    assert [f.input_name for f in found] == ["client_id"], [str(f) for f in found]


def test_the_remedy_is_not_a_finding():
    found = _scan_text(
        _sample("if (-not [string]::IsNullOrWhiteSpace($env:IN_CLIENT_ID)) { $x = 1 }"))
    assert found == [], [str(f) for f in found]


def test_a_property_presence_test_is_not_a_finding():
    """The three `scripts/` hits #1213 explicitly excluded use this operator on
    an API response object. The same shape inside a workflow must stay clean."""
    found = _scan_text(_sample('if ("$($Object.$n)" -ne \'\') { $x = 1 }'))
    assert found == [], [str(f) for f in found]


def test_a_local_variable_is_not_a_finding():
    found = _scan_text(_sample("if ($dir -ne '') { $x = 1 }"))
    assert found == [], [str(f) for f in found]


def test_an_env_var_that_is_not_a_dispatch_input_is_not_a_finding():
    found = _scan_text(_sample("if ($env:GITHUB_REF -ne '') { $x = 1 }"))
    assert found == [], [str(f) for f in found]


def test_a_constrained_input_is_not_a_finding():
    """`boolean` and `choice` are values GitHub generated; neither can arrive
    blank-but-present because the dispatcher chose it."""
    found = _scan_text(_sample("if ('${{ inputs.overwrite }}' -ne '') { $x = 1 }"))
    assert found == [], [str(f) for f in found]


def test_a_non_empty_comparison_is_not_a_finding():
    """`-eq 'true'` is a value test, not an emptiness test. 229 carries two."""
    found = _scan_text(_sample("if ('${{ inputs.client_id }}' -eq 'true') { $x = 1 }"))
    assert found == [], [str(f) for f in found]


def test_the_eq_spelling_is_detected_too():
    found = _scan_text(_sample("if ('${{ inputs.client_id }}' -eq '') { $x = 1 }"))
    assert [f.input_name for f in found] == ["client_id"], [str(f) for f in found]


def test_unparseable_yaml_is_a_finding_not_a_skip():
    found = _scan_text("name: [unclosed\n  on: {{{")
    assert len(found) == 1 and "unparseable" in found[0].text, [str(f) for f in found]


def test_the_scan_sees_real_workflows():
    """The Norway-problem control: `on:` deserialises as the boolean True, so a
    guard reading only the string key returns {} for every workflow and passes
    by inspecting nothing. If this drops to zero the whole suite is vacuous."""
    findings, scanned = guard.scan_all()
    assert scanned >= 100, f"only {scanned} workflow files scanned"
    counted = 0
    for path in guard.workflow_paths():
        import yaml
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(parsed, dict) and guard.free_text_inputs(parsed):
            counted += 1
    assert counted >= 40, (
        f"only {counted} workflows were seen to declare a free-text dispatch "
        f"input — the guard is reading nothing and would pass on any tree"
    )


# --- the real tree, and reverting the real sites ---------------------------

FIXED_SITES = {
    "208-whmcs-tickets-export.yml": [
        ("-not [string]::IsNullOrWhiteSpace('${{ inputs.status }}')",
         "'${{ inputs.status }}' -ne ''"),
    ],
    "229-whmcs-client-field-populate.yml": [
        ("-not [string]::IsNullOrWhiteSpace('${{ inputs.client_id }}')",
         "'${{ inputs.client_id }}' -ne ''"),
        ("-not [string]::IsNullOrWhiteSpace('${{ inputs.email }}')",
         "'${{ inputs.email }}' -ne ''"),
    ],
}


def test_the_tree_is_clean_apart_from_the_freeze():
    findings, _ = guard.scan_all()
    current = guard.current_map(findings)
    errors, _notes = guard.compare(current)
    assert errors == [], errors


def test_reverting_each_fixed_site_is_detected_and_named():
    """AC3: proven by discrimination, not permissiveness.

    Each substitution asserts its anchor is present and unique BEFORE
    substituting, so a refactor that moved the line fails loudly here instead of
    silently testing nothing (a mutation that does not land produces a clean
    run, which reads as "the guard has a hole" — the technique's own false
    negative, pointing the wrong way).
    """
    for workflow, mutations in FIXED_SITES.items():
        original = (WORKFLOWS / workflow).read_text(encoding="utf-8")
        for fixed, weak in mutations:
            assert original.count(fixed) == 1, (
                f"{workflow}: expected exactly 1 occurrence of the fixed "
                f"predicate {fixed!r}, found {original.count(fixed)} — the "
                f"mutation cannot be applied, so this case would otherwise "
                f"test an unmodified file"
            )
            mutant = original.replace(fixed, weak)
            assert mutant != original
            with tempfile.TemporaryDirectory() as tmp:
                path = pathlib.Path(tmp) / workflow
                path.write_text(mutant, encoding="utf-8", newline="\n")
                findings = guard.scan_workflow(path)
            names = {f.input_name for f in findings}
            assert names, (
                f"{workflow}: reverting {fixed!r} to the empty-string "
                f"comparison produced NO finding — the guard does not detect "
                f"the defect it exists to catch"
            )
            reported = " ".join(str(f) for f in findings)
            assert workflow in reported, f"the finding did not name {workflow}: {reported}"


def test_a_new_weak_gate_in_a_frozen_workflow_is_still_a_finding():
    """The freeze pins named inputs, not the whole file. Adding a THIRD weak
    gate to `205` must fail even though `205` is listed."""
    current = {"205-whmcs-ticket-open.yml": ("client_email", "client_id", "subject")}
    errors, _notes = guard.compare(current)
    assert any("subject" in e for e in errors), errors


def test_a_freeze_entry_whose_site_was_fixed_is_a_note_not_an_error():
    """The deliberate asymmetry, documented in `compare()`: the 205 entry is
    fixed by #1207/#1212, which predate this guard and cannot edit it. Making
    that fatal would turn someone else's merge into a red `main`."""
    errors, notes = guard.compare({})
    assert errors == [], errors
    assert any("205-whmcs-ticket-open.yml" in n for n in notes), notes


def test_a_freeze_entry_naming_a_missing_file_is_an_error():
    """The other staleness direction stays fatal: a list that refers to a file
    which is not there has stopped describing the tree entirely."""
    errors, _notes = guard.compare({}, known={"999-does-not-exist.yml": ("x",)})
    assert any("999-does-not-exist.yml" in e for e in errors), errors


def test_the_freeze_names_inputs_that_actually_exist():
    """The freeze is only protective if its input names match the workflow's
    declarations. `205`'s email input is `client_email`, not `email` — writing
    the issue's spelling made the entry cover nothing."""
    import yaml
    for workflow, names in guard.KNOWN_WEAK_GATES.items():
        path = WORKFLOWS / workflow
        assert path.is_file(), f"{workflow} is in the freeze but not in the tree"
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        declared = set(guard.dispatch_inputs(parsed))
        unknown = sorted(set(names) - declared)
        assert not unknown, (
            f"{workflow}: the freeze names {unknown}, which are not dispatch "
            f"inputs of that workflow. Declared: {sorted(declared)}"
        )


def test_the_checker_exits_zero_on_the_shipped_tree():
    # `PYTHONIOENCODING` pins the CHILD's output encoding, not just this
    # process's decode. Without it the child emits cp1252 on Windows, the
    # decode raises on subprocess's reader thread, `proc.stdout` comes back
    # None, and the traceback blames the assertion below (#962).
    out = subprocess.run(
        [sys.executable, str(CHECKER)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(REPO_ROOT),
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert out.returncode == 0, f"{out.stdout}\n{out.stderr}"
    assert "empty-string input gate OK" in out.stdout, out.stdout


def test_no_case_substitutes_the_sample_with_str_format():
    """`_SAMPLE` must only ever be substituted through `_sample()`.

    `str.format` collapses `{{` -> `{`, which rewrites the template's own `env:`
    mapping into a non-expression AND — since the placeholder is `__BODY__`, not
    a format field — silently ignores the body argument entirely. The case then
    scans a sample with no gate in it and passes **vacuously**.

    Not hypothetical: one call site survived the conversion to `_sample()`
    because it wraps across two lines, so a substring rewrite keyed on the
    single-line spelling missed it — and the rewrite then asserted the count it
    had *observed* rather than the count that should exist, which is what made
    the miss invisible. Copilot caught it on #1214; a grep-style assertion is
    the cheap thing that would have.

    The needle is assembled at runtime so this function's own source cannot
    match it. The first version spelled it literally and the guard promptly
    flagged its own docstring — a self-trigger reads as a real finding.
    """
    needle = "_SAMPLE" + ".format"
    source = pathlib.Path(__file__).read_text(encoding="utf-8")
    offenders = [
        n for n, line in enumerate(source.splitlines(), start=1) if needle in line
    ]
    assert not offenders, (
        f"lines {offenders} substitute the sample template with str.format — use "
        f"_sample(), which preserves the expressions and asserts the placeholder "
        f"is present"
    )
    assert "__BODY__" in _SAMPLE, "the sample template lost its body placeholder"
    assert "{body}" not in _SAMPLE, (
        "the template regained a str.format placeholder — the two substitution "
        "styles must not coexist, or the wrong one silently wins"
    )


def test_needs_pwsh_names_tests_that_exist():
    """Keeps the roster honest across a rename: a NEEDS_PWSH entry naming a test
    that no longer exists silently stops gating anything, and the test it was
    meant to gate then crashes the module on a host with no PowerShell."""
    declared = {t.__name__ for t in TESTS}
    unknown = sorted(NEEDS_PWSH - declared)
    assert not unknown, (
        f"NEEDS_PWSH names {unknown}, which are not tests in this module."
    )


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

# Only the cases that spawn a PowerShell host. Everything else — the guard's
# entire discrimination suite — runs with no tool at all and must NOT be gated,
# or a host without PowerShell reports "everything passed" while the assertions
# that matter went unmeasured (#1182).
NEEDS_PWSH = {
    "test_ne_empty_is_true_for_an_unset_variable",
    "test_ne_empty_is_true_for_whitespace",
    "test_the_two_predicates_agree_on_empty_and_on_a_real_value",
    "test_without_the_fix_a_whitespace_client_id_reaches_the_callee",
    "test_the_fix_refuses_a_whitespace_client_id",
    "test_the_fix_still_passes_a_real_value",
}

if __name__ == "__main__":
    failures = 0
    for t in TESTS:
        if t.__name__ in NEEDS_PWSH and not HAVE_PWSH:
            print(f"  SKIP {t.__name__} (pwsh not installed; runs in CI)")
            continue
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {str(e)[:400]}")
    sys.exit(1 if failures else 0)
