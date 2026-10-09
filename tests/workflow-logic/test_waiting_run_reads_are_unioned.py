"""Every waiting-run read in this repo must union two redundant query shapes.

`?status=waiting` / `gh run list --status waiting` is not reliable: its failure
mode is to **under-report rather than to error**. Measured on the hub
2026-10-07T13:14Z (Conductor run 219), the unqualified shape answered
`total_count: 0` on two consecutive reads while four runs were provably waiting,
each reading back individually as `status: waiting` with a live
`pending_deployments` entry; adding a branch filter to the same call returned all
four. The unqualified shape had been correct six minutes earlier, so the defect is
transient and unpredictable — which is why the remedy is redundancy rather than a
replacement query. Ledger **L341**.

Client-side filtering over an unfiltered run list is **not** the alternative and
was measured too: a held gate is routinely days old and falls off the newest-100
page entirely, which is precisely the population every one of these callers
exists to act on.

**Why this is a repo-wide guard rather than three fixed call sites.** L341 fixed
one consumer (`generate-agentic-os-status.py`) and its own evidence named the
other two as "untouched here". Nothing re-reads a lesson's evidence, so both sat
unfixed for two days and roughly ten Conductor runs while the ledger read as
complete — and the janitor's failure mode is the worst of the three: an empty
list is indistinguishable from a clean queue, so it cancels nothing, posts
nothing, exits green, and the pre-reap warning a human relies on to answer a gate
never arrives. This test exists so the **next** consumer cannot be added bare.
Ledger **L352**.

Refs #752 (verify the Agentic OS's own monitors keep working), L341, L352.
"""

from __future__ import annotations

import pathlib
import re
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# Only the surfaces that actually read the Actions runs API. PowerShell under
# `scripts/` talks to WHMCS, whose `GetOrders status=Pending` is unrelated.
SCAN = [
    *sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml")),
    *sorted((REPO_ROOT / "scripts").glob("*.py")),
]

# An executable waiting-run read: `status` and the bare word `waiting` on one
# line. `\bwaiting\b` deliberately does not match "awaiting".
READ = re.compile(r"status.{0,12}\bwaiting\b", re.IGNORECASE)

# The redundant companion shape. Any of these spellings counts — the point is
# that a second, branch-qualified query exists in the same file.
BRANCH_SHAPE = re.compile(
    r"""branch["']?\s*[:=]\s*["']?\w          # branch: 'main' / "branch": "main"
        | --branch                             # gh run list --branch main
        | \{\*\*base,\s*["']branch["']         # {**base, "branch": ...}
        | default_branch                       # the parameter the shapes are built from
    """,
    re.VERBOSE,
)

COMMENT = re.compile(r"^\s*(#|//|\*|<#)")

# The shared sentence every consumer must carry, so that "all reads failed" is
# never served as "the queue is clean". One spelling across Python and the
# janitor's inline JS on purpose: a grep for it answers "is this defended?" in
# every language this repo reads waiting runs from.
REFUSAL = "every waiting-run query shape failed"


def _reads(path):
    """Non-comment lines in `path` that look like a waiting-run read."""
    hits = []
    for n, line in enumerate(
        path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
    ):
        if COMMENT.match(line):
            continue
        if READ.search(line):
            hits.append((n, line.strip()))
    return hits


def test_the_scan_finds_the_known_consumers():
    """The guard must be shown to have a population before it can pass.

    An opt-in guard whose scan silently matches nothing reports `0 violations`
    and measures only its own regex. This asserts the three callers L341 and
    L352 are about are actually in front of it.
    """
    found = {p.name for p in SCAN if _reads(p)}
    for expected in (
        "734-stale-waiting-run-janitor.yml",
        "approve-waiting-runs.py",
        "generate-agentic-os-status.py",
    ):
        assert expected in found, f"{expected} not matched by the scan; found={sorted(found)}"


def test_every_waiting_run_read_has_a_branch_qualified_companion():
    violations = []
    for path in SCAN:
        hits = _reads(path)
        if not hits:
            continue
        body = path.read_text(encoding="utf-8", errors="replace")
        if not BRANCH_SHAPE.search(body):
            rel = path.relative_to(REPO_ROOT).as_posix()
            violations.append(f"{rel}:{hits[0][0]}  {hits[0][1][:100]}")
    assert not violations, (
        "a waiting-run read with no branch-qualified companion shape "
        "(L341: ?status=waiting under-reports to zero without erroring):\n  "
        + "\n  ".join(violations)
    )


def test_every_waiting_run_read_refuses_when_every_shape_fails():
    """A union is only a floor if a failing shape cannot take the other down.

    The companion guard above checks that a second shape *exists*. That is not
    enough, and the gap is not hypothetical: all three consumers shipped the
    union with both shapes iterated inside **one** exception scope, so a
    rate-limit 403 or a timeout on the first discarded the second's answer and
    handed the caller the empty list L341 is entirely about. Two shapes that
    cannot fail independently are not redundant; they are two chances to fail.

    Keyed on the refusal rather than on the isolation, because the refusal is
    the part that is both greppable and load-bearing. Every one of these callers
    does something destructive-by-omission with an empty list -- cancels nothing
    and suppresses the pre-reap warning, publishes "no approvals outstanding",
    prints "no waiting runs found" -- so a consumer that cannot distinguish "all
    reads failed" from "the queue is clean" has no safe behaviour available to
    it. A caller carrying the sentence necessarily tracks per-shape failure to
    be able to say it.
    """
    violations = []
    for path in SCAN:
        hits = _reads(path)
        if not hits:
            continue
        body = path.read_text(encoding="utf-8", errors="replace")
        if REFUSAL not in body:
            rel = path.relative_to(REPO_ROOT).as_posix()
            violations.append(f"{rel}:{hits[0][0]}  {hits[0][1][:100]}")
    assert not violations, (
        "a waiting-run read that cannot tell a failed read from an empty queue; "
        f'it must isolate each shape and then refuse with "{REFUSAL}" when none '
        "survives (L341/L352: an empty union is indistinguishable from a clean "
        "queue, and every caller acts destructively on that):\n  "
        + "\n  ".join(violations)
    )


def main():
    failures = []
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"  PASS {name}")
        except AssertionError as exc:
            failures.append(name)
            print(f"  FAIL {name}: {exc}")
    print(f"\n{len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
