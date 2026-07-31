"""Run every workflow-logic test module and fail on any failure.

Usage: python3 tests/workflow-logic/run_all.py
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent


def _reported_any_test(output: str) -> bool:
    """True if a module reported anything at all about what it ran.

    Deliberately weaker than "printed `  PASS <name>` per test": the modules do
    not share one reporting convention (`test_502_agentic_os_status.py` runs a
    single `main()` and prints one summary line), and this guard is not the
    place to force one. Total silence is the invariant that matters -- it is
    what a module with no runner produces.
    """
    return bool(output.strip())


def main() -> int:
    modules = sorted(HERE.glob("test_*.py"))
    if not modules:
        print("::error::no workflow-logic test modules found")
        return 1
    failed = []
    for mod in modules:
        print(f"== {mod.name} ==")
        proc = subprocess.run(
            [sys.executable, str(mod)],
            cwd=HERE.parents[1],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        sys.stdout.write(proc.stdout)
        sys.stdout.flush()
        if proc.returncode != 0:
            failed.append(mod.name)
            continue
        # A module that exits 0 in silence has run nothing -- the shape a module
        # gets when it defines tests but has no `if __name__ == "__main__":`
        # runner. It is indistinguishable from a passing module here, so its
        # coverage reads as green while being zero (seen on
        # test_722_large_blob_guard.py, whose ten tests never once executed).
        if not _reported_any_test(proc.stdout):
            print(
                f"::error::{mod.name} exited 0 without printing anything, so it "
                "ran no tests. Add an `if __name__ == \"__main__\":` runner that "
                "executes the module's tests and reports each one -- see any "
                "existing module."
            )
            failed.append(mod.name)
    if failed:
        print(f"::error::workflow-logic tests failed: {', '.join(failed)}")
        return 1
    print(f"All {len(modules)} workflow-logic test modules passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
