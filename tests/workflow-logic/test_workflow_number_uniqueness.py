"""Guard: no two workflows may declare the same display number.

The numbering scheme in AGENTS.md is category-first (`1xx` Cloudflare, `2xx`
WHMCS, …) and every operator-facing check keys on the number parsed out of the
`name:` field — the safety-table coverage check, the phantom-row check, the
catalog generator, and the Conductor's own auto-approval lookup. A number that
addresses two files makes all of them ambiguous.

**Why this needs a test rather than care.** Two 2026-07 PRs both claimed display
number 229 — #904 (domain order add) and #825 (client field populate), each in a
differently-named file. Because the filenames differ:

* git merges both branches **without a conflict** — nothing forces a human to
  look;
* `check-workflow-doc-consistency.py` built its number→file map with a plain
  `wf_nums[num] = f`, so the alphabetically-later file overwrote the earlier one
  and the loser was invisible to every downstream assertion;
* the reported count stayed **correct-looking** (98 numbers for 99 files),
  because a dict with a duplicated key simply has fewer entries.

That last point is the trap: the failure presents as a number that still adds
up. Verified by hand on 2026-07-29 — dropping a second file declaring `221.`
into `.github/workflows/` left the checker printing "OK: 98 workflows" and
exiting 0.

Both halves are covered here: the live tree must have unique numbers, and the
checker script must actually detect a duplicate (a passing invariant proves
nothing about a guard that cannot fail).
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import WORKFLOWS

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CHECKER = REPO_ROOT / "scripts" / "check-workflow-doc-consistency.py"

NAME_NUMBER = re.compile(r"^name:\s*['\"]?(\d{2,3})\.", re.M)


def _declared_numbers() -> dict[str, list[str]]:
    """display number -> every workflow filename declaring it."""
    out: dict[str, list[str]] = {}
    for path in sorted(WORKFLOWS.glob("*.yml")):
        m = NAME_NUMBER.search(path.read_text(encoding="utf-8-sig"))
        if m:
            out.setdefault(str(int(m.group(1))).zfill(3), []).append(path.name)
    return out


def test_display_numbers_are_unique():
    """The live invariant: one number, one workflow."""
    numbers = _declared_numbers()
    assert numbers, "parsed no display numbers — the name: convention changed"

    dupes = {num: files for num, files in numbers.items() if len(files) > 1}
    assert not dupes, "display numbers claimed by more than one workflow: " + "; ".join(
        f"{num} -> {files}" for num, files in sorted(dupes.items())
    )


def test_checker_detects_a_duplicate_number():
    """The guard must fail on a duplicate — and fail with a useful message.

    Asserting only on the exit code would let a broken invocation (bad cwd,
    missing interpreter) masquerade as a caught duplicate: both are exit 1.
    So this pins the number in the output too.
    """
    victim = next(
        (p for p in sorted(WORKFLOWS.glob("*.yml")) if NAME_NUMBER.search(
            p.read_text(encoding="utf-8-sig")
        )),
        None,
    )
    assert victim is not None, "no numbered workflow to duplicate"

    text = victim.read_text(encoding="utf-8-sig")
    number = NAME_NUMBER.search(text).group(1)
    twin = WORKFLOWS / "999-duplicate-number-probe-DELETE-ME.yml"
    assert not twin.exists(), f"{twin.name} already exists; refusing to clobber it"

    twin.write_text(text, encoding="utf-8", newline="\n")
    try:
        proc = subprocess.run(
            [sys.executable, str(CHECKER)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
    finally:
        twin.unlink()

    output = proc.stdout + proc.stderr
    assert proc.returncode != 0, (
        "the checker passed a tree with two workflows declaring "
        f"{number}. — the duplicate guard is not working.\n{output}"
    )
    assert twin.name in output and number in output, (
        "the checker failed, but not with a duplicate-number message naming the "
        f"offending file — so this test cannot tell a caught duplicate from a "
        f"broken invocation.\nexit={proc.returncode}\n{output}"
    )


def test_checker_passes_on_the_real_tree():
    """Baseline: the guard is not simply always-red."""
    proc = subprocess.run(
        [sys.executable, str(CHECKER)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert proc.returncode == 0, (
        "check-workflow-doc-consistency.py fails on the committed tree:\n"
        f"{proc.stdout}{proc.stderr}"
    )


def test_every_workflow_file_prefix_matches_its_declared_number():
    """`NNN-*.yml` must agree with the `NNN.` in its display name.

    A file named `229-foo.yml` whose name: says `230.` is the same ambiguity
    seen from the other side, and it defeats the "grep the number to find the
    file" habit every runbook relies on.
    """
    mismatches = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        fm = re.match(r"^(\d{3})-", path.name)
        m = NAME_NUMBER.search(path.read_text(encoding="utf-8-sig"))
        if not fm or not m:
            continue
        declared = str(int(m.group(1))).zfill(3)
        if fm.group(1) != declared:
            mismatches.append(f"{path.name} declares name: '{declared}.'")
    assert not mismatches, "filename / display-number disagreement: " + "; ".join(mismatches)


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
