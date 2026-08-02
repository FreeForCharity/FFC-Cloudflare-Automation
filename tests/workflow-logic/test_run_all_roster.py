"""Unit tests for run_all.py's roster guards.

`run_all.py` could already tell a module that ran NOTHING from one that ran
everything (the silence guard, #722's ten never-executed tests). It could not
tell either from a module that ran SOME of its tests and then died: 49 of the 50
module runners catch `AssertionError` only, so an `IndexError` or a `KeyError`
ends the module, the tests after it never execute, and what survives on stdout is
a clean-looking list of PASSes. The return code cannot discriminate -- Python
exits 1 for an uncaught exception and the runner exits 1 for a failed assertion
(L82, found while mutation-proving #992: two mutants aborted a module at test 16
of 53 and the report read "only 2 tests flipped").

So the guard compares what a module REPORTS against what it DEFINES, and the
tests below pin the four ways that comparison could rot:

  * an ordinary red module must not be called truncated (or the check decays
    into "any failing run is truncated" and stops meaning anything);
  * the silence guard must keep its own message (they are different diagnoses);
  * the roster count must be `ast`-based, because a grep counts `def test_`
    inside a docstring (L48);
  * the opt-out must be an explicit marker, so a module that quietly stops
    following the convention is caught rather than excused by its own counts.

Run: python3 tests/workflow-logic/test_run_all_roster.py
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import re
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
RUN_ALL = HERE / "run_all.py"


def _load_run_all():
    spec = importlib.util.spec_from_file_location("wf_run_all", RUN_ALL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run_all = _load_run_all()


# --------------------------------------------------------------------------
# Fixture modules. Each is a real, runnable workflow-logic module in miniature:
# top-level `def test_*` functions, a TESTS list, and the same runner tail the
# 49 modules share -- `except AssertionError` and nothing else.
# --------------------------------------------------------------------------

RUNNER = '''
TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {e}")
    sys.exit(1 if failures else 0)
'''

FULL_ROSTER = (
    "import sys\n\n"
    "def test_a():\n    assert True\n\n"
    "def test_b():\n    assert True\n\n"
    "def test_c():\n    assert True\n" + RUNNER
)

# test_b raises IndexError -- exactly L82's shape: subscripting an empty result.
# The runner catches AssertionError only, so test_c and test_d never run.
TRUNCATED = (
    "import sys\n\n"
    "def test_a():\n    assert True\n\n"
    "def test_b():\n    empty = []\n    assert empty[0]\n\n"
    "def test_c():\n    assert True\n\n"
    "def test_d():\n    assert True\n" + RUNNER
)

ORDINARY_FAILURE = (
    "import sys\n\n"
    "def test_a():\n    assert True\n\n"
    "def test_b():\n    assert False, 'a genuine assertion failure'\n\n"
    "def test_c():\n    assert True\n" + RUNNER
)

# Defines tests, has no runner: exits 0 having executed none of them.
SILENT = "def test_a():\n    assert True\n\n\ndef test_b():\n    assert True\n"

WHOLE_MODULE_SKIP = (
    "import sys\n\n"
    "def test_a():\n    assert True\n\n"
    "def test_b():\n    assert True\n\n"
    "def test_c():\n    assert True\n\n"
    'if __name__ == "__main__":\n'
    "    print('  SKIP all (no PowerShell host in this environment; runs in CI)')\n"
    "    sys.exit(0)\n"
)

EXEMPT_MARKER = (
    'RUN_ALL_ROSTER_EXEMPT = "runs one main() and prints a single summary line"\n'
)

# A module that reports one outcome out of three -- truncated on the counts
# alone. With the marker it is exempt; without it, it must redden. That
# difference is what makes the marker load-bearing rather than decorative.
PARTIAL_REPORTER = (
    "import sys\n\n"
    "def test_a():\n    assert True\n\n"
    "def test_b():\n    assert True\n\n"
    "def test_c():\n    assert True\n\n"
    'if __name__ == "__main__":\n'
    "    print('  PASS test_a')\n"
    "    sys.exit(0)\n"
)

# `def test_` inside a docstring and inside a string literal. A grep counts two
# tests here; `ast` counts none (L48 -- the guard that stripped string literals
# deleted the very thing it was looking for).
LITERALS_ONLY = '''"""A module whose docstring shows a test:

    def test_documented_example():
        assert True
"""

TEMPLATE = "def test_generated_by_a_template():\\n    assert True\\n"
'''

NESTED_ONLY = (
    "def helper():\n"
    "    def test_inner():\n"
    "        assert True\n\n"
    "    return test_inner\n"
)


def _write(dirpath: pathlib.Path, name: str, source: str) -> pathlib.Path:
    path = dirpath / name
    path.write_text(source, encoding="utf-8")
    return path


def _run_all_on(dirpath: pathlib.Path) -> tuple[int, str]:
    """Run run_all.py against a fixture directory, end to end."""
    proc = subprocess.run(
        [sys.executable, str(RUN_ALL), str(dirpath)],
        cwd=HERE.parents[1],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        # Both halves pinned: the parent's decode and the child's encode are two
        # settings, and pinning only one returns proc.stdout=None on Windows
        # (#962).
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        timeout=120,
    )
    return proc.returncode, proc.stdout


# --------------------------------------------------------------------------
# AC1 / AC2 / AC4 -- the discrimination the whole check exists for.
# --------------------------------------------------------------------------


def test_a_complete_roster_passes_unchanged():
    """N defined, N reported, all green: nothing to say."""
    with tempfile.TemporaryDirectory(prefix="roster-") as tmp:
        _write(pathlib.Path(tmp), "test_full.py", FULL_ROSTER)
        code, out = _run_all_on(pathlib.Path(tmp))
    assert code == 0, out
    assert "truncated roster" not in out, out
    assert "All 1 workflow-logic test modules passed." in out, out


def test_a_truncated_roster_is_reported_with_both_counts_and_the_last_test():
    """The AC2 message has to carry its own diagnosis.

    The module name says where, N vs M says how much was lost, and the last
    reporting test says WHERE to look -- the abort is in the test after it. A
    message without those numbers leaves the reader doing the count by hand,
    which is the work L82 records as expensive.
    """
    with tempfile.TemporaryDirectory(prefix="roster-") as tmp:
        _write(pathlib.Path(tmp), "test_truncated.py", TRUNCATED)
        code, out = _run_all_on(pathlib.Path(tmp))
    assert code == 1, out
    assert "truncated roster" in out, out
    assert "test_truncated.py" in out, out
    assert "defines 4 tests but reported 1" in out, out
    assert "the last test to report was test_a" in out.lower(), out
    assert "IndexError" in out, "the module's own traceback must still be shown\n" + out


def test_an_ordinary_failing_test_is_not_called_truncated():
    """The criterion that stops the check degrading into "red means truncated".

    N defined, N reported, one of them FAIL: the module ran everything and one
    assertion failed. Calling that a truncated roster would train readers to
    ignore the message.
    """
    with tempfile.TemporaryDirectory(prefix="roster-") as tmp:
        _write(pathlib.Path(tmp), "test_ordinary.py", ORDINARY_FAILURE)
        code, out = _run_all_on(pathlib.Path(tmp))
    assert code == 1, out
    assert "FAIL test_b" in out, out
    assert "truncated roster" not in out, "an ordinary failure was reported as truncated\n" + out
    assert "workflow-logic tests failed: test_ordinary.py" in out, out


# --------------------------------------------------------------------------
# AC3 -- the two guards are different diagnoses and must stay separable.
# --------------------------------------------------------------------------


def test_a_module_that_reports_nothing_still_trips_the_silence_guard():
    with tempfile.TemporaryDirectory(prefix="roster-") as tmp:
        _write(pathlib.Path(tmp), "test_silent.py", SILENT)
        code, out = _run_all_on(pathlib.Path(tmp))
    assert code == 1, out
    assert "exited 0 without printing anything" in out, out
    assert "truncated roster" not in out, (
        "'ran nothing' must not be re-labelled as 'ran some and stopped' -- the "
        "remedy is different (add a runner vs. find the crash)\n" + out
    )


def test_the_two_guards_report_distinct_findings():
    """Collapsing them loses the "ran nothing" diagnosis.

    Asserted on the two messages a single run produces for the two shapes, not
    on the source text: a refactor is free to move the code, but a reader must
    still be told which of the two happened.
    """
    with tempfile.TemporaryDirectory(prefix="roster-") as tmp:
        d = pathlib.Path(tmp)
        _write(d, "test_silent.py", SILENT)
        _write(d, "test_truncated.py", TRUNCATED)
        code, out = _run_all_on(d)
    assert code == 1, out
    silence = [ln for ln in out.splitlines() if "exited 0 without printing anything" in ln]
    truncation = [ln for ln in out.splitlines() if "truncated roster" in ln]
    assert len(silence) == 1, f"expected exactly one silence finding, got {silence}\n{out}"
    assert len(truncation) == 1, f"expected exactly one truncation finding, got {truncation}"
    assert "test_silent.py" in silence[0], silence[0]
    assert "test_truncated.py" in truncation[0], truncation[0]


# --------------------------------------------------------------------------
# AC5 -- the opt-out is an explicit marker, never a count heuristic.
# --------------------------------------------------------------------------


def test_the_exemption_marker_is_what_excuses_a_short_roster():
    """The same fixture, with and without the marker, must land differently.

    PARTIAL_REPORTER defines three tests and reports one. On the counts alone it
    is indistinguishable from a module that died at test two -- which is exactly
    why the exemption cannot be inferred from counts.
    """
    with tempfile.TemporaryDirectory(prefix="roster-") as tmp:
        d = pathlib.Path(tmp)
        _write(d, "test_marked.py", EXEMPT_MARKER + PARTIAL_REPORTER)
        marked_code, marked_out = _run_all_on(d)
    with tempfile.TemporaryDirectory(prefix="roster-") as tmp:
        d = pathlib.Path(tmp)
        _write(d, "test_unmarked.py", PARTIAL_REPORTER)
        bare_code, bare_out = _run_all_on(d)
    assert marked_code == 0, "the declared exemption must be honoured\n" + marked_out
    assert "truncated roster" not in marked_out, marked_out
    assert bare_code == 1, "the same module without the marker must redden\n" + bare_out
    assert "truncated roster" in bare_out, bare_out


def test_an_exemption_must_state_a_reason():
    """`RUN_ALL_ROSTER_EXEMPT = True` (or "") is not an exemption.

    An opt-out that cannot say why it exists is the one nobody re-examines.
    """
    with tempfile.TemporaryDirectory(prefix="roster-") as tmp:
        d = pathlib.Path(tmp)
        good = _write(d, "test_reason.py", EXEMPT_MARKER + PARTIAL_REPORTER)
        empty = _write(d, "test_empty.py", 'RUN_ALL_ROSTER_EXEMPT = ""\n' + PARTIAL_REPORTER)
        truthy = _write(d, "test_true.py", "RUN_ALL_ROSTER_EXEMPT = True\n" + PARTIAL_REPORTER)
        assert run_all.roster_exemption(good), "a reason string is an exemption"
        assert run_all.roster_exemption(empty) is None, "an empty reason is not an exemption"
        assert run_all.roster_exemption(truthy) is None, "a bare True is not an exemption"


def test_the_502_module_carries_the_marker_rather_than_being_excused_by_its_counts():
    """The one real module that does not report a per-test roster.

    It defines zero `def test_*` functions, so a counts-only check would sail
    past it in silence. The marker is what makes the exception visible.
    """
    module = HERE / "test_502_agentic_os_status.py"
    assert module.exists(), module
    assert run_all.declared_tests(module) == [], (
        "test_502 is the summary-line module; if it grew a `def test_*` roster, "
        "drop RUN_ALL_ROSTER_EXEMPT and let the roster check cover it"
    )
    reason = run_all.roster_exemption(module)
    assert reason, "test_502_agentic_os_status.py must declare RUN_ALL_ROSTER_EXEMPT"
    assert "summary" in reason.lower(), reason


# --------------------------------------------------------------------------
# AC6 -- the roster is parsed, not grepped.
# --------------------------------------------------------------------------


def test_test_functions_in_docstrings_and_literals_count_zero():
    """L48: a source scan that strips string literals deletes what it seeks."""
    with tempfile.TemporaryDirectory(prefix="roster-") as tmp:
        path = _write(pathlib.Path(tmp), "test_literals.py", LITERALS_ONLY)
        grepped = len(re.findall(r"def test_\w+", path.read_text(encoding="utf-8")))
        assert grepped == 2, f"fixture must be discriminating; grep found {grepped}"
        assert run_all.declared_tests(path) == [], "ast must count none of them"


def test_a_nested_test_function_is_not_a_declared_test():
    """The module runner collects `globals()`, so only top-level defs are tests."""
    with tempfile.TemporaryDirectory(prefix="roster-") as tmp:
        path = _write(pathlib.Path(tmp), "test_nested.py", NESTED_ONLY)
        assert run_all.declared_tests(path) == [], "a nested def is not in globals()"


def test_a_real_roster_module_is_counted_correctly():
    """Anchored on a real module so the parser is exercised against real source."""
    module = HERE / "test_745_board_audit.py"
    assert module.exists(), module
    names = run_all.declared_tests(module)
    assert len(names) > 20, f"expected a large roster, got {len(names)}"
    assert all(n.startswith("test_") for n in names), names


# --------------------------------------------------------------------------
# Reporting conventions the real suite already uses.
# --------------------------------------------------------------------------


def test_skips_count_as_reported_outcomes():
    """Per-test SKIP is a report, not a gap.

    `test_powershell_command_resolution.py` skips 21 of its 26 tests where no
    PowerShell host exists. Counting PASS/FAIL only would call that suite
    truncated on every machine without pwsh -- a false alarm on the one signal
    this check exists to make trustworthy.
    """
    out = "  PASS test_a\n  SKIP test_b: no PowerShell host\n  FAIL test_c: boom\n"
    assert run_all.reported_outcomes(out) == ["test_a", "test_b", "test_c"], out


def test_a_whole_module_skip_stands_the_check_down():
    """228 and 720 print `  SKIP all (...)` and run nothing where pwsh is absent.

    That is a skip the module ANNOUNCES, which is why it is honoured; it is not
    inferred from the fact that the counts came up short.
    """
    with tempfile.TemporaryDirectory(prefix="roster-") as tmp:
        _write(pathlib.Path(tmp), "test_skipall.py", WHOLE_MODULE_SKIP)
        code, out = _run_all_on(pathlib.Path(tmp))
    assert code == 0, out
    assert "truncated roster" not in out, out


def test_every_module_in_the_suite_either_reports_a_roster_or_declares_why_not():
    """The check has to hold for the tree as it stands, or it is not enforceable.

    A module with no `def test_*` functions and no marker cannot be checked at
    all -- this is the static half of the same claim, run against every real
    module so a new one cannot arrive un-checkable.
    """
    offenders = []
    for module in sorted(HERE.glob("test_*.py")):
        if run_all.declared_tests(module):
            continue
        if run_all.roster_exemption(module):
            continue
        offenders.append(module.name)
    assert offenders == [], (
        "these modules declare no tests and no RUN_ALL_ROSTER_EXEMPT reason, so "
        f"run_all.py cannot tell a full run from a truncated one: {offenders}"
    )


def test_the_runner_is_the_one_ci_invokes():
    """A guard nobody runs is the failure mode this whole class is about."""
    ci = (HERE.parents[1] / ".github" / "workflows" / "722-ci.yml").read_text(encoding="utf-8")
    assert "tests/workflow-logic/run_all.py" in ci, (
        "722-ci.yml must invoke run_all.py, or these guards are documentation"
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
            print(f"  FAIL {t.__name__}: {str(e)[:2000]}")
    sys.exit(1 if failures else 0)
