"""Run every workflow-logic test module and fail on any failure.

Usage: python3 tests/workflow-logic/run_all.py [directory]

`directory` defaults to this file's own directory -- what CI runs. It exists so
the roster guards below can be exercised end to end against fixture modules in a
temp dir (`test_run_all_roster.py`); nothing in CI passes it.
"""

from __future__ import annotations

import ast
import os
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]

# One line per test, the convention 48 of the 50 modules follow:
#     "  PASS test_x" / "  FAIL test_x: ..." / "  SKIP test_x: ..."
# The name stops at the `:` a FAIL/SKIP line puts before its reason -- `\S+`
# would carry the colon into the reported name, and that name is printed back to
# the reader as "the last test to report".
OUTCOME_RE = re.compile(r"^  (?:PASS|FAIL|SKIP) ([^\s:]+)", re.M)

# `  SKIP all (pwsh not installed ...)` -- 228 and 720 print this and run none of
# their tests when the environment lacks a PowerShell host. It is a whole-module
# skip the module ANNOUNCES, not a count we infer, so the roster check stands
# down for that run only.
WHOLE_MODULE_SKIP = "all"

# Module-level `RUN_ALL_ROSTER_EXEMPT = "<why>"`: the permanent opt-out, for a
# module that legitimately does not report a per-test roster
# (`test_502_agentic_os_status.py` runs a single `main()` and prints one summary
# line). Deliberately an explicit marker rather than a count heuristic: a module
# that quietly STOPS following the convention must be caught, not excused.
EXEMPT_NAME = "RUN_ALL_ROSTER_EXEMPT"

# --------------------------------------------------------------------------
# Failure classification (#1290)
#
# A failing sweep used to end in one undifferentiated list of module names. On
# a Windows host most of those modules never failed an assertion -- they died
# on the bash/PowerShell harness shim (#1119) -- but they rendered identically
# to a real regression, so the only economical response was to ignore the whole
# list. A signal that is always noisy in the same way stops being read.
#
# This is a RENDERING fix and nothing else. Every failure still fails the run;
# see `main`'s return below. A sweep that passed on "environmental only" would
# be strictly worse than the undifferentiated list it replaces.
# --------------------------------------------------------------------------

# Where a reader goes to understand the environmental bucket. Named once so the
# summary and the tests cannot drift from each other.
ENVIRONMENTAL_ISSUE = "#1119"

ASSERTION = "assertion"
ENVIRONMENTAL = "environmental"

# One line per signature. Each is a string that only the ENVIRONMENT can emit:
# it is produced by the shell, the OS or a runtime's own startup, before or
# outside any code this repository owns. Adding one is a one-line change.
#
# The bar for membership is deliberately high -- a signature here can only ever
# move a failure OUT of the actionable bucket, so a loose one (a bare
# `FileNotFoundError`, say, which a genuine path bug produces just as readily)
# would hide exactly what this list exists to surface. When in doubt, leave it
# out: an unrecognised failure is classified as an assertion by
# `classify_failure`, which is the safe direction.
ENVIRONMENTAL_SIGNATURES = (
    # A PowerShell host refusing to run the harness's bash `gh`/`git` shim: the
    # shim is a shell document, and PowerShell cannot place one in a pipeline.
    # Emitted by the host before a single line of module code runs.
    "Cannot run a document in the middle of a pipeline",
    # git-bash failing to PARSE a command substitution. A shell parse error is
    # raised before the command is executed, so no repo logic reached the point
    # of producing it -- it is the shim quoting difference #1119 tracks.
    "command substitution: line 1: syntax error near unexpected token",
    # ERROR_SHARING_VIOLATION. Windows will not unlink a file another process
    # still holds, which is how a `tempfile.TemporaryDirectory` teardown dies
    # here long after the test body itself has passed.
    "[WinError 32]",
    # ERROR_ACCESS_DENIED, reached by the same teardown path as WinError 32.
    "[WinError 5]",
    # ERROR_FILE_NOT_FOUND from `subprocess`, i.e. a POSIX tool the module
    # shells out to is absent from PATH on this host.
    "[WinError 2]",
    # ERROR_PRIVILEGE_NOT_HELD. This account cannot create symlinks at all, so
    # `os.symlink` is unavailable by construction rather than by defect.
    "[WinError 1314]",
    # node aborting inside its own C++ startup when spawned with a scrubbed
    # environment (#943's shape). Never reachable from repo code.
    "Assertion failed: ncrypto::CSPRNG",
)

# `  FAIL <name>` -- the outcome line a module prints when a test it RAN failed
# its assertion. Distinct from OUTCOME_RE, which counts every outcome for the
# roster check; here only the failures are evidence.
FAIL_LINE_RE = re.compile(r"^  FAIL ([^\s:]+)", re.M)


def environmental_signature(output: str) -> str | None:
    """The first `ENVIRONMENTAL_SIGNATURES` entry present in `output`, or None."""
    for signature in ENVIRONMENTAL_SIGNATURES:
        if signature in output:
            return signature
    return None


def classify_failure(output: str) -> tuple[str, str]:
    """Classify one failing module's captured output as assertion or environmental.

    Returns `(kind, evidence)`, where `evidence` is the thing actually matched
    rather than a boolean -- the classification has to be checkable by the
    person reading the summary, not merely trusted.

    **Assertion evidence is tested FIRST, and that order is the point of this
    function.** A module on a Windows host routinely contains both: a real
    failing assertion AND a `[WinError 32]` from some unrelated teardown. If
    the environmental signature were allowed to win, one incidental OS error
    would silently reclassify a genuine regression as "not our problem" -- the
    exact defect #1290 exists to prevent, rather than a fix for it. So any sign
    that a test ran and failed makes the module an assertion failure no matter
    what else its output contains.

    The cost of that order is the one worth accepting: a `  FAIL` line whose
    reason happens to be environmental is reported as an assertion failure.
    That is over-reporting into the actionable bucket, which someone reads and
    corrects, rather than under-reporting into the bucket nobody reads.
    """
    failed_tests = FAIL_LINE_RE.findall(output)
    if failed_tests:
        return ASSERTION, f"reported FAIL for {failed_tests[0]}"
    if "AssertionError" in output:
        return ASSERTION, "raised AssertionError"
    signature = environmental_signature(output)
    if signature is not None:
        return ENVIRONMENTAL, signature
    # Unrecognised: fail toward "this is a real bug". An environment this list
    # does not yet describe is a signature to add, and until someone adds it
    # the failure stays visible.
    return ASSERTION, "no environmental signature recognised"


def summary_lines(failures: list[tuple[str, str, str]]) -> list[str]:
    """Render the classified breakdown that precedes the canonical summary line.

    Ordering is load-bearing: this goes ABOVE
    `::error::workflow-logic tests failed: ...`, which stays the LAST line of a
    finished run. AGENTS.md's base-vs-PR comparison recipe requires that line
    to be present before two runs may be compared, and describes it as the
    terminal line; appending anything after it would quietly falsify that.
    """
    environmental = [f for f in failures if f[1] == ENVIRONMENTAL]
    assertions = [f for f in failures if f[1] == ASSERTION]
    lines = [
        f"::error::failure breakdown: {len(assertions)} assertion failure(s); "
        f"{len(environmental)} environmental (see {ENVIRONMENTAL_ISSUE})"
    ]
    # With an empty environmental bucket -- ubuntu, i.e. what CI runs -- the
    # canonical line below is already the assertion list, so itemising it again
    # would be noise. CI's reading is unchanged in substance.
    if not environmental:
        return lines
    lines.append(
        f"::error::  environmental ({ENVIRONMENTAL_ISSUE}) -- not a code defect, "
        "and the run still fails on them:"
    )
    for name, _, evidence in environmental:
        lines.append(f"::error::    {name} -- matched {evidence!r}")
    lines.append("::error::  assertion failures -- these are the ones to act on:")
    for name, _, evidence in assertions:
        lines.append(f"::error::    {name} -- {evidence}")
    return lines


def _reported_any_test(output: str) -> bool:
    """True if a module reported anything at all about what it ran.

    Deliberately weaker than "printed `  PASS <name>` per test": the modules do
    not share one reporting convention (`test_502_agentic_os_status.py` runs a
    single `main()` and prints one summary line), and this guard is not the
    place to force one. Total silence is the invariant that matters -- it is
    what a module with no runner produces.
    """
    return bool(output.strip())


def declared_tests(path: pathlib.Path) -> list[str]:
    """Names of the module's top-level `def test_*` functions, read with `ast`.

    Parsed, never grepped: a `def test_` inside a docstring or a string literal
    is not a test, and a source scan that tries to strip literals first is
    exactly how L48's guard deleted the thing it was looking for. A module that
    will not parse declares nothing -- its own traceback is the diagnosis.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, ValueError, OSError):
        return []
    return [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    ]


def roster_exemption(path: pathlib.Path) -> str | None:
    """The module's `RUN_ALL_ROSTER_EXEMPT = "<why>"` reason, or None.

    The reason string is required and must be non-empty: an exemption that
    cannot say why it exists is the one nobody re-examines.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, ValueError, OSError):
        return None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not (isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
            continue
        if not node.value.value.strip():
            continue
        if any(isinstance(t, ast.Name) and t.id == EXEMPT_NAME for t in node.targets):
            return node.value.value
    return None


def reported_outcomes(output: str) -> list[str]:
    """The test names a module reported an outcome for, in order.

    A module that ECHOES captured child output (an assertion message quoting a
    fixture run) inflates this count. The error is one-directional and the safe
    direction: an inflated count can mask a truncation in a module that is
    already red and printing FAILs, but it can never invent one in a green run.
    """
    return OUTCOME_RE.findall(output)


def roster_finding(
    name: str,
    declared: list[str],
    reported: list[str],
    exemption: str | None,
) -> str | None:
    """Describe how a module's reported roster falls short of what it defines.

    The gap this closes: a module runner catches `AssertionError` only, so any
    other exception -- an `IndexError` subscripting an empty result, a `KeyError`
    on a renamed section -- ends the MODULE. The tests after it never run, and
    what survives on stdout is a clean-looking list of PASSes with no marker that
    anything was skipped. The return code cannot tell that apart from an ordinary
    failure: Python exits 1 for an uncaught exception and the runner exits 1 for
    a failed assertion (L82).

    CI is red either way, so this is not a hole in the merge gate. It is a hole
    in what a human or an agent READING the output concludes, which is what L82
    records costing real review time.
    """
    if exemption is not None:
        return None
    if WHOLE_MODULE_SKIP in reported:
        return None
    if not declared:
        return (
            f"{name} defines no top-level `def test_*` functions, so a full run and a "
            f"truncated one look identical here. Either follow the roster convention "
            f'(one `  PASS <name>` line per test) or declare `{EXEMPT_NAME} = "<why>"` '
            f"at module level."
        )
    if not reported:
        # Say only what the outcome lines can support. "Nothing ran" is a
        # stronger claim than their absence licenses: a module that raises in
        # its first test, or at import, executed code and reported nothing. The
        # honest statement is that no test reached the point of reporting.
        return (
            f"{name} defines {len(declared)} tests and reported an outcome for none of "
            f"them, so not one of them ran to completion -- the module stopped before, "
            f"during, or at the end of its first test. Its output above is the diagnosis."
        )
    if len(reported) < len(declared):
        return (
            f"{name}: truncated roster -- defines {len(declared)} tests but reported "
            f"{len(reported)}. The last test to report was {reported[-1]}, so the module "
            f"died in the test after it: the runner catches AssertionError only, and any "
            f"other exception ends the module while the PASSes already printed still read "
            f"as a green roster (L82)."
        )
    return None


def _survive_unencodable_output() -> None:
    """Stop a child's non-ASCII byte from killing the harness mid-suite.

    #962 pinned two encoding settings -- the child's encode and the parent's
    decode. There is a THIRD, and it is the one that bites: after decoding a
    module's output cleanly, this process RE-ENCODES it on `sys.stdout.write`,
    using whatever encoding the parent's stdout happens to have. Redirect a run
    on Windows and that is cp1252, so a module printing an arrow as U+2192
    (RIGHTWARDS ARROW, not the ASCII `->`) raises UnicodeEncodeError inside
    the loop. The harness dies at whichever module got there first, prints no
    summary, and exits non-zero.

    That failure is dangerous rather than merely annoying, because it is
    SILENT in the direction that matters: the run stops early, so every module
    after it never runs, and the "workflow-logic tests failed:" line never
    prints. Anything reading the output for that line -- a reviewer, a script,
    a later step -- finds no failures and concludes there were none. A crashed
    suite and a clean suite are indistinguishable to a reader who greps for
    failures instead of for the summary's PRESENCE.

    `errors="replace"` and not `encoding="utf-8"`: re-encoding is only ever a
    display concern here (every comparison this file makes is on the decoded
    `str`), and forcing utf-8 bytes at a genuinely cp1252 console trades a
    crash for mojibake on every box that has one. Replacement degrades one
    character and keeps the roster and the summary readable.
    """
    for stream in (sys.stdout, sys.stderr):
        # Absent if stdout has been swapped for a plain object; nothing to pin.
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")


def main(argv: list[str] | None = None) -> int:
    _survive_unencodable_output()
    args = list(sys.argv[1:] if argv is None else argv)
    directory = pathlib.Path(args[0]).resolve() if args else HERE
    modules = sorted(directory.glob("test_*.py"))
    if not modules:
        print("::error::no workflow-logic test modules found")
        return 1
    # (module name, kind, evidence) per failing module, classified from the
    # output that module actually produced -- never from a list of module names
    # known to be flaky, which would go stale silently and cannot notice a real
    # regression appearing inside a module already on it.
    #
    # ONE list, and the canonical name list is derived from it below. The
    # obvious shape -- a `failed` list of names beside a `failures` list of
    # records -- requires every future `append` site to remember both, and the
    # cost of forgetting is a breakdown whose counts silently disagree with the
    # summary line right under it. Deriving makes that unrepresentable.
    failures: list[tuple[str, str, str]] = []
    for mod in modules:
        print(f"== {mod.name} ==")
        proc = subprocess.run(
            [sys.executable, str(mod)],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            # The parent's decode and the child's encode are two settings. Pinning
            # only this side turns a mojibake'd line into a UnicodeDecodeError on
            # subprocess's reader thread, which leaves proc.stdout None and blames
            # the caller's arithmetic instead. See #962.
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        sys.stdout.write(proc.stdout)
        sys.stdout.flush()
        # A module that exits 0 in silence has run nothing -- the shape a module
        # gets when it defines tests but has no `if __name__ == "__main__":`
        # runner. It is indistinguishable from a passing module here, so its
        # coverage reads as green while being zero (seen on
        # test_722_large_blob_guard.py, whose ten tests never once executed).
        # Kept as its own guard, checked first: "ran nothing" is a different
        # diagnosis from "ran some and died", and folding them loses it.
        if proc.returncode == 0 and not _reported_any_test(proc.stdout):
            print(
                f"::error::{mod.name} exited 0 without printing anything, so it "
                "ran no tests. Add an `if __name__ == \"__main__\":` runner that "
                "executes the module's tests and reports each one -- see any "
                "existing module."
            )
            failures.append((mod.name, *classify_failure(proc.stdout)))
            continue
        finding = roster_finding(
            mod.name,
            declared_tests(mod),
            reported_outcomes(proc.stdout),
            roster_exemption(mod),
        )
        if finding:
            print(f"::error::{finding}")
            failures.append((mod.name, *classify_failure(proc.stdout)))
            continue
        if proc.returncode != 0:
            failures.append((mod.name, *classify_failure(proc.stdout)))
    if failures:
        failed = [name for name, _, _ in failures]
        for line in summary_lines(failures):
            print(line)
        # Unchanged, and last. Both properties are relied on elsewhere: the
        # prefix is what AGENTS.md greps for, and `test_run_all_roster.py`
        # asserts the module list follows it directly.
        print(f"::error::workflow-logic tests failed: {', '.join(failed)}")
        return 1
    print(f"All {len(modules)} workflow-logic test modules passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
