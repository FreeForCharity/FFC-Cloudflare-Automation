"""Run every workflow-logic test module and fail on any failure.

Usage: python3 tests/workflow-logic/run_all.py
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent


def main() -> int:
    modules = sorted(HERE.glob("test_*.py"))
    if not modules:
        print("::error::no workflow-logic test modules found")
        return 1
    failed = []
    for mod in modules:
        print(f"== {mod.name} ==")
        proc = subprocess.run([sys.executable, str(mod)], cwd=HERE.parents[1])
        if proc.returncode != 0:
            failed.append(mod.name)
    if failed:
        print(f"::error::workflow-logic tests failed: {', '.join(failed)}")
        return 1
    print(f"All {len(modules)} workflow-logic test modules passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
