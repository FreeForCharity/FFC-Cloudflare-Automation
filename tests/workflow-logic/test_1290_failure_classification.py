"""Unit tests for run_all.py's failure classifier (#1290).

`run_all.py` used to end a failing sweep with one undifferentiated list of
module names. On a Windows host most of those modules never failed an
assertion -- they died on the bash/PowerShell harness shim (#1119) -- but they
rendered identically to a real regression. The only economical response was to
ignore the whole list and re-run single modules by hand, which is how a signal
that is always noisy in the same way stops being read at all.

The classifier splits a failing sweep into `assertion` and `environmental`. The
tests below pin the four ways that split could rot:

  * an environmental signature must never MASK a real failure in the same
    module -- that is the defect this exists to prevent, not a fix for it;
  * an unrecognised failure must land in the ASSERTION bucket, so an
    environment nobody has described yet stays visible;
  * the exit code must not move -- this is a rendering fix, and a run that
    passed on "environmental only" would be strictly worse than today;
  * the canonical `::error::workflow-logic tests failed:` line must stay
    intact and LAST, because AGENTS.md greps for it and `test_run_all_roster.py`
    asserts the module list follows it directly.

Run: python3 tests/workflow-logic/test_1290_failure_classification.py
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
RUN_ALL = HERE / "run_all.py"


def _load_run_all():
    spec = importlib.util.spec_from_file_location("wf_run_all_1290", RUN_ALL)
    assert spec is not None and spec.loader is not None, (
        f"run_all.py is not importable as a module from {RUN_ALL} (exists="
        f"{RUN_ALL.exists()}) -- the classifier cannot be tested without it"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run_all = _load_run_all()


# --------------------------------------------------------------------------
# Fixture OUTPUT. Real captured-output shapes, not module sources: the
# classifier's whole contract is that it reads the text a module produced.
# --------------------------------------------------------------------------

# A module that ran everything it defines and failed one assertion.
ASSERTION_OUTPUT = "  PASS test_a\n  FAIL test_b: expected 3, got 4\n  PASS test_c\n"

# The #1119 shape from run 159: PowerShell refusing the harness's bash shim.
# Note there is no `  FAIL` line anywhere -- nothing ran far enough to report.
ENVIRONMENTAL_OUTPUT = (
    "  PASS test_a\n"
    "Traceback (most recent call last):\n"
    '  File "test_720_owner_parse.py", line 41, in <module>\n'
    "Cannot run a document in the middle of a pipeline: "
    "C:/repo/tests/workflow-logic/harness/gh\n"
)

# Both at once, which is the ordinary state of a Windows run: a genuine
# assertion failure sitting beside an incidental OS error from some unrelated
# teardown. This is the case the classifier exists to get right.
MIXED_OUTPUT = (
    "  PASS test_a\n"
    "  FAIL test_b: the workflow lost its `permissions:` block\n"
    "Exception ignored while cleaning up TemporaryDirectory:\n"
    "PermissionError: [WinError 32] The process cannot access the file\n"
)

# A failure in no signature's shape -- a plain uncaught exception.
UNRECOGNISED_OUTPUT = (
    "Traceback (most recent call last):\n"
    '  File "test_x.py", line 12, in test_a\n'
    "KeyError: 'jobs'\n"
)


def test_an_environmental_death_is_classified_environmental():
    """The #1119 shape, with no assertion evidence present, goes to the quiet bucket."""
    kind, evidence = run_all.classify_failure(ENVIRONMENTAL_OUTPUT)
    assert kind == run_all.ENVIRONMENTAL, f"got {kind} for a harness death: {evidence}"
    assert evidence == "Cannot run a document in the middle of a pipeline", (
        f"the evidence must name the signature that matched, not a boolean; got {evidence!r}"
    )


def test_a_failed_assertion_is_classified_assertion():
    """A `  FAIL` line means a test RAN and its assertion failed."""
    kind, evidence = run_all.classify_failure(ASSERTION_OUTPUT)
    assert kind == run_all.ASSERTION, f"got {kind} for a real assertion failure"
    assert "test_b" in evidence, (
        f"the evidence must name the test that failed so the reader can go "
        f"straight to it; got {evidence!r}"
    )


def test_an_environmental_signature_cannot_mask_a_real_failure():
    """The load-bearing one -- AC's falsifiability check.

    A module carrying BOTH a genuine `  FAIL` and an incidental `[WinError 32]`
    from a temp-dir teardown must be reported as an ASSERTION failure. A
    classifier that let the OS error win would file a real regression under
    "not our problem", which is the defect #1290 exists to prevent rather than
    a fix for it.
    """
    assert run_all.environmental_signature(MIXED_OUTPUT) is not None, (
        "fixture is not exercising the masking case -- it must contain a real "
        "environmental signature, or this test passes vacuously"
    )
    kind, evidence = run_all.classify_failure(MIXED_OUTPUT)
    assert kind == run_all.ASSERTION, (
        f"an environmental signature masked a real failure -- got {kind} ({evidence})"
    )
    assert "test_b" in evidence, evidence


def test_an_unrecognised_failure_lands_in_the_assertion_bucket():
    """Fail toward "this is a real bug", never toward "probably the environment"."""
    assert run_all.environmental_signature(UNRECOGNISED_OUTPUT) is None, (
        "fixture must not match any signature, or this test proves nothing"
    )
    kind, _ = run_all.classify_failure(UNRECOGNISED_OUTPUT)
    assert kind == run_all.ASSERTION, f"an unrecognised failure was excused as {kind}"


def test_every_signature_is_non_empty_and_actually_classifies():
    """Non-vacuity: a blank or unreachable entry would silently widen the quiet bucket.

    An empty string is `in` every output, so one blank entry would classify the
    entire suite as environmental -- the worst available failure, and invisible
    by inspection.
    """
    assert run_all.ENVIRONMENTAL_SIGNATURES, "the signature list must not be empty"
    for signature in run_all.ENVIRONMENTAL_SIGNATURES:
        assert signature.strip(), f"blank signature in the list: {signature!r}"
        kind, evidence = run_all.classify_failure(f"some preamble\n{signature}\nmore\n")
        assert kind == run_all.ENVIRONMENTAL, (
            f"{signature!r} is in the list but does not classify its own text"
        )
        assert evidence == signature, f"expected {signature!r}, got {evidence!r}"


def test_the_breakdown_reports_a_count_for_each_bucket():
    """AC3 -- the summary has to carry both numbers, not just a module list."""
    lines = run_all.summary_lines(
        [
            ("test_real.py", run_all.ASSERTION, "reported FAIL for test_b"),
            ("test_env_a.py", run_all.ENVIRONMENTAL, "[WinError 32]"),
            ("test_env_b.py", run_all.ENVIRONMENTAL, "[WinError 2]"),
        ]
    )
    joined = "\n".join(lines)
    assert "1 assertion failure(s)" in joined, joined
    assert "2 environmental" in joined, joined
    assert run_all.ENVIRONMENTAL_ISSUE in joined, (
        "the environmental bucket must cite where the reader goes next\n" + joined
    )
    # Both groups itemised, so the classification can be checked rather than trusted.
    assert "test_real.py" in joined and "test_env_a.py" in joined, joined


def test_a_clean_environmental_bucket_leaves_the_output_unchanged_in_substance():
    """On ubuntu -- what CI runs -- there is nothing to itemise, so nothing is.

    The canonical summary line already IS the assertion list when the
    environmental bucket is empty. Repeating it would be noise on the one host
    whose reading of this file must not change.
    """
    lines = run_all.summary_lines(
        [("test_real.py", run_all.ASSERTION, "reported FAIL for test_b")]
    )
    assert len(lines) == 1, f"expected a single breakdown line, got {lines}"
    assert "0 environmental" in lines[0], lines[0]
    assert "test_real.py" not in lines[0], (
        "the module list belongs to the canonical line, not the breakdown\n" + lines[0]
    )


# --------------------------------------------------------------------------
# End to end: the classifier has to be WIRED IN, not merely correct. AC1 is
# about run_all.py's summary, so a unit-tested function nothing calls would
# satisfy every test above and fix nothing.
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

# Reports every test it defines (so the roster guard stands down) and then
# exits non-zero having printed an environmental signature -- the shape of a
# module whose teardown died on Windows after the bodies themselves passed.
ENVIRONMENTAL_MODULE = (
    "import sys\n\n"
    "def test_a():\n    assert True\n\n"
    "def test_b():\n    assert True\n\n"
    'if __name__ == "__main__":\n'
    '    print("  PASS test_a")\n'
    '    print("  PASS test_b")\n'
    "    print('PermissionError: [WinError 32] The process cannot access the file')\n"
    "    sys.exit(1)\n"
)

ASSERTION_MODULE = (
    "import sys\n\n"
    "def test_a():\n    assert True\n\n"
    "def test_b():\n    assert False, 'a genuine assertion failure'\n" + RUNNER
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
        # Both halves pinned -- the parent's decode and the child's encode are
        # two settings, and pinning one alone returns stdout=None on Windows
        # (#962).
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        timeout=120,
    )
    return proc.returncode, proc.stdout


def test_a_mixed_sweep_splits_both_ways_and_still_fails():
    """AC1, AC3 and AC4 together, through the real entry point.

    Asserting on the exit code alone could not tell this apart from a harness
    that never started, so the bucket contents are asserted too.
    """
    with tempfile.TemporaryDirectory(prefix="classify-") as tmp:
        d = pathlib.Path(tmp)
        _write(d, "test_env_only.py", ENVIRONMENTAL_MODULE)
        _write(d, "test_real_failure.py", ASSERTION_MODULE)
        code, out = _run_all_on(d)
    assert code == 1, f"a failing sweep must still exit non-zero\n{out}"
    assert "1 assertion failure(s); 1 environmental" in out, (
        f"the breakdown must count both buckets\n{out}"
    )
    assert "test_env_only.py -- matched" in out, (
        f"the environmental module must be itemised with its signature\n{out}"
    )
    assert "test_real_failure.py -- reported FAIL for test_b" in out, (
        f"the real failure must be itemised as actionable\n{out}"
    )


def test_an_environmental_only_sweep_still_exits_non_zero():
    """AC4, at its sharpest.

    This is the one regression that would make the change actively harmful: a
    sweep whose every failure is environmental must STILL fail. Rendering the
    bucket quietly is the point; letting it pass would convert a noisy signal
    into a silent one, which is strictly worse than the list it replaces.
    """
    with tempfile.TemporaryDirectory(prefix="classify-") as tmp:
        d = pathlib.Path(tmp)
        _write(d, "test_env_only.py", ENVIRONMENTAL_MODULE)
        code, out = _run_all_on(d)
    assert code == 1, f"an environmental-only sweep must still exit non-zero\n{out}"
    assert "0 assertion failure(s); 1 environmental" in out, out
    assert "::error::workflow-logic tests failed: test_env_only.py" in out, (
        f"the module must still be named in the canonical list\n{out}"
    )


def test_the_canonical_summary_line_survives_and_stays_last():
    """The contract two other readers depend on.

    AGENTS.md's base-vs-PR recipe requires this line before two runs may be
    compared and describes it as the last line of a finished run;
    `test_run_all_roster.py` asserts the module list follows the prefix
    directly. Printing the breakdown after it would falsify both quietly.
    """
    with tempfile.TemporaryDirectory(prefix="classify-") as tmp:
        d = pathlib.Path(tmp)
        _write(d, "test_real_failure.py", ASSERTION_MODULE)
        code, out = _run_all_on(d)
    assert code == 1, out
    assert "::error::workflow-logic tests failed: test_real_failure.py" in out, out
    last = [line for line in out.splitlines() if line.strip()][-1]
    assert last.startswith("::error::workflow-logic tests failed:"), (
        f"the canonical summary must be the last line of a finished run; got {last!r}\n{out}"
    )


def test_a_green_sweep_prints_no_breakdown():
    """Nothing failed, so there is nothing to classify."""
    with tempfile.TemporaryDirectory(prefix="classify-") as tmp:
        d = pathlib.Path(tmp)
        _write(
            d,
            "test_green.py",
            "import sys\n\ndef test_a():\n    assert True\n" + RUNNER,
        )
        code, out = _run_all_on(d)
    assert code == 0, out
    assert "failure breakdown" not in out, f"a green sweep must stay quiet\n{out}"
    assert "All 1 workflow-logic test modules passed." in out, out


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
