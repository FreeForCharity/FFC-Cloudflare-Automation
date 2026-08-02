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
    """The test names a module reported an outcome for, in order."""
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
        return (
            f"{name} defines {len(declared)} tests and reported an outcome for none of "
            f"them, so nothing in it ran. Its output above is the diagnosis."
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


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    directory = pathlib.Path(args[0]).resolve() if args else HERE
    modules = sorted(directory.glob("test_*.py"))
    if not modules:
        print("::error::no workflow-logic test modules found")
        return 1
    failed = []
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
            failed.append(mod.name)
            continue
        finding = roster_finding(
            mod.name,
            declared_tests(mod),
            reported_outcomes(proc.stdout),
            roster_exemption(mod),
        )
        if finding:
            print(f"::error::{finding}")
            failed.append(mod.name)
            continue
        if proc.returncode != 0:
            failed.append(mod.name)
    if failed:
        print(f"::error::workflow-logic tests failed: {', '.join(failed)}")
        return 1
    print(f"All {len(modules)} workflow-logic test modules passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
