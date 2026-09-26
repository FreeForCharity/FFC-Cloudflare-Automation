"""Apply one sample charity to a real template checkout (used by workflow 747).

    python3 tests/workflow-logic/apply_sample_charity.py --charity riverbend-pantry --repo site

Runs the charity from tests/fixtures/sample-charities.json through 701's real
`resolve` parse script, then scripts/Apply-WebsiteReactTemplate.ps1 against
--repo, exactly as test_sample_charities.py does against a fixture. Prints the
script's output and exits with its exit code.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_701_apply_website_template import run_apply  # noqa: E402
from test_sample_charities import load, script_args  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--charity", required=True)
    ap.add_argument("--repo", required=True)
    a = ap.parse_args()
    matches = [c for c in load() if c["id"] == a.charity]
    if not matches:
        print(f"::error::no sample charity with id {a.charity!r}")
        return 2
    repo = pathlib.Path(a.repo).resolve()
    proc = run_apply(repo, script_args(matches[0]))
    print(proc.stdout, end="")
    print(proc.stderr, end="", file=sys.stderr)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
