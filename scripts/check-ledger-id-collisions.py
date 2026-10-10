#!/usr/bin/env python3
"""Guard: a new lessons-ledger id must not be claimed by another open PR.

Why this exists, measured on #1584 and #1586 (Conductor run 240). `main`'s
ledger ended at L351. #1584 added L352-L355 and #1586 also added L352 --
different lessons, same id. **Both PRs were green.** `test_lessons_ledger.py`
reads the ledger in the branch it runs on and compares it against nothing else,
so on #1584's tree L352 is unique, on #1586's tree L352 is unique, and the
duplicate exists only in the union. 79 PASS / 0 FAIL on both.

The merge queue does catch it, but it fires on the merge GROUP, so the red names
neither PR's own change and costs a full ~17-minute cycle. Worse, the natural
conflict resolution ships both rows: the pair conflicts across the whole table
(#1584 re-pads every row), and a reviewer resolving "keep the re-padded table,
append the new row" produces two L352 rows and a 77/2 red whose message names
L43 -- a row nobody touched.

The evidence was already in the repository and no check read it. #1588's
`reserved-ids` block declared `L352 #1584` **two days** before anyone looked.
That is the positive signal this guard turns into a failure: an id another open
PR declares reserved is evidence your branch must not take it.

WHAT IS A VIOLATION. Two arms, because the two carry different evidence:

  * `both-rows` -- an id that is a NEW row on this PR and a new row on a sibling
    PR. New is measured against the merge base, so a row both branches merely
    inherit from `main` is not a collision.
  * `declared-elsewhere` -- an id this PR takes as a row while a sibling's
    `reserved-ids` block names a DIFFERENT holder for it. This fires even when
    the sibling has not written its row yet, which is the earlier and cheaper
    catch.

An id reserved by the PR under test itself is not a violation: declaring your
own id is how the block is meant to be used.

FAIL-CLOSED, per #993's rule that "we could not read it" must never render as
"nothing to report". If the sibling PR list cannot be read, or a sibling's
ledger cannot be fetched, this exits non-zero and says so. A guard that goes
quiet when its input is missing is the failure mode it exists to prevent, and
this one depends on a network read, so the quiet path is reachable in ordinary
operation (a token without `pull-requests: read`, a rate-limited API).

Exit 0 when clean, 1 with findings on stdout, 2 when the comparison could not be
made. The decision logic is pure and lives in `collision_problems`;
`tests/workflow-logic/test_ledger_id_collisions.py` drives it with fixtures, so
none of it needs the network to be tested.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys

LEDGER_PATH = "docs/lessons-ledger.md"

# A ledger row opens a markdown table cell holding `L<n>`. Anchored to the line
# start so a mid-prose mention of `L352` in some other row is never a claim.
_ROW = re.compile(r"(?m)^\|\s*L(\d+)\s*\|")

# Kept deliberately identical in shape to `test_lessons_ledger.py`'s pair, which
# is the file that defines what a reservation means. If that syntax ever changes,
# both must move together -- the test module asserts the two agree on a fixture.
_RESERVED_BLOCK = re.compile(r"<!--\s*reserved-ids\b(.*?)-->", re.DOTALL)
_RESERVED_ENTRY = re.compile(r"^(L\d+)(?:\s+(\S.*?))?$")


def row_ids(text: str) -> set[int]:
    """Every id that appears as a ledger ROW in `text`."""
    return {int(m.group(1)) for m in _ROW.finditer(text)}


def reserved_ids(text: str) -> dict[int, str]:
    """`{id: holder}` for each `reserved-ids` entry that names a holder.

    An entry with no holder is ignored here. It is already an error in
    `test_lessons_ledger.py` ("a reservation with no PR behind it"), and
    reporting it twice in different words helps nobody.
    """
    out: dict[int, str] = {}
    for block in _RESERVED_BLOCK.findall(text):
        for raw in block.splitlines():
            entry = raw.strip()
            if not entry:
                continue
            matched = _RESERVED_ENTRY.match(entry)
            if matched and matched.group(2):
                out[int(matched.group(1)[1:])] = matched.group(2).strip()
    return out


def _same_holder(holder: str, me: str) -> bool:
    """Does `holder` name the PR under test?

    Holders are written `#1584` by convention, but a reviewer may well write
    `PR #1584` or paste a URL. Compare on the digits so a cosmetic difference
    cannot turn a correct self-reservation into a failure -- the expensive
    direction, because it blocks a PR that did the right thing.
    """
    mine = re.sub(r"\D", "", me)
    return bool(mine) and mine in re.findall(r"\d+", holder)


def collision_problems(
    base_text: str,
    mine_text: str,
    siblings: dict[str, str],
    me: str,
) -> list[str]:
    """Findings for the ledger in `mine_text` against every sibling PR's ledger.

    `base_text` is the merge base's ledger, which is what makes "new" meaningful.
    `siblings` maps a label (`#1588`) to that PR's ledger text. `me` is the PR
    under test, used only to recognise its own reservations.
    """
    base = row_ids(base_text)
    mine_new = row_ids(mine_text) - base
    problems: list[str] = []

    for label in sorted(siblings, key=lambda s: int(re.sub(r"\D", "", s) or 0)):
        text = siblings[label]

        for lid in sorted(mine_new & (row_ids(text) - base)):
            problems.append(
                f"L{lid} is a NEW row on this PR and on {label}. Neither PR's own "
                f"checks can see this: {LEDGER_PATH} is compared against itself, "
                f"so the duplicate exists only in the union. Renumber to the "
                f"first id no open PR claims, and declare the ids you are "
                f"skipping in the `reserved-ids` block."
            )

        for lid, holder in sorted(reserved_ids(text).items()):
            if lid in mine_new and not _same_holder(holder, me):
                problems.append(
                    f"L{lid} is a row on this PR, but {label} declares it "
                    f"reserved by {holder}. A reservation another PR has already "
                    f"committed is positive evidence the id is taken -- it is the "
                    f"cheapest signal available here, and it predates the "
                    f"colliding row. Renumber."
                )

    return problems


def _run(args: list[str]) -> str:
    """Run a read-only `gh` command. Raises on failure so main() fails closed."""
    proc = subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"`{' '.join(args[:3])} ...` exited {proc.returncode}: "
            f"{(proc.stderr or proc.stdout or '').strip()[:400]}"
        )
    return proc.stdout


def _repo() -> str:
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not repo:
        raise RuntimeError("GITHUB_REPOSITORY is unset — cannot name the repo to query")
    return repo


def _this_pr() -> str:
    """The PR under test, from the event payload or PR_NUMBER."""
    for var in ("PR_NUMBER", "GITHUB_PR_NUMBER"):
        if os.environ.get(var, "").strip():
            return "#" + os.environ[var].strip().lstrip("#")
    event = os.environ.get("GITHUB_EVENT_PATH", "")
    if event and pathlib.Path(event).is_file():
        payload = json.loads(pathlib.Path(event).read_text(encoding="utf-8"))
        number = (payload.get("pull_request") or {}).get("number")
        if number:
            return f"#{number}"
    raise RuntimeError(
        "cannot determine which PR this is — set PR_NUMBER, or run on a "
        "`pull_request` event so GITHUB_EVENT_PATH names one"
    )


def gather_siblings(repo: str, me: str) -> dict[str, str]:
    """Every other open PR's ledger text, keyed by `#<number>`.

    Only PRs that actually touch the ledger have their ledger fetched, so the
    usual cost is the list read plus one `/files` read per open PR.

    BOTH list reads are paginated, and that is load-bearing rather than tidiness.
    `per_page=100` is a maximum and not a guarantee (AGENTS.md: "An unpaginated
    list read cannot support an ABSENCE claim"), and *every* verdict this guard
    reports rests on an absence -- "no other open PR claims this id". So a 101st
    open PR falling off page one does not merely weaken the check, it inverts it:
    a real collision renders as `OK: no ledger id on #N is claimed by another
    open PR`. That is the precise shape #993's fail-closed rule exists to
    prevent -- an unread input reported as a clean one -- reached here through a
    truncation rather than through an error, so nothing is raised and no page is
    missing from the caller's point of view. The same applies one level down: a
    sibling whose `/files` list runs past a page would have its ledger edit read
    as "this PR does not touch the ledger", which silently removes it from the
    comparison set that `checked` then reports as examined.

    The jq filters are deliberately STREAMING -- one line per item, never an
    array. `--paginate` runs the filter once per page and concatenates the
    outputs, so an array-building filter (`[.[] | ...]`) emits `[...][...]`,
    which is not valid JSON; `json.loads` rejects it with a byte offset in the
    middle of page two, nowhere near the command that caused it. See CLAUDE.md's
    `--paginate` section. That trap is the reason this function parses lines
    instead of JSON, and `test_ledger_id_collisions.py` asserts no paginated read
    here ever grows an array-building filter back.

    Raises if any part of the read fails.
    """
    mine = re.sub(r"\D", "", me)
    siblings: dict[str, str] = {}
    listed = _run(
        [
            "gh",
            "api",
            "--paginate",
            f"repos/{repo}/pulls?state=open&per_page=100",
            "--jq",
            r'.[] | "\(.number) \(.head.sha)"',
        ]
    )
    for raw in listed.splitlines():
        entry = raw.strip()
        if not entry:
            continue
        number, _, head = entry.partition(" ")
        head = head.strip()
        if not number.isdigit() or not head:
            raise RuntimeError(
                f"unparseable row in the open-PR list: {entry!r} — expected "
                f"`<number> <head sha>`. A row this function cannot read is a PR "
                f"it cannot compare against, so it fails closed rather than "
                f"skipping the line."
            )
        if number == mine:
            continue
        files = _run(
            [
                "gh",
                "api",
                "--paginate",
                f"repos/{repo}/pulls/{number}/files?per_page=100",
                "--jq",
                ".[].filename",
            ]
        )
        if LEDGER_PATH not in files.splitlines():
            continue
        siblings[f"#{number}"] = _run(
            [
                "gh",
                "api",
                f"repos/{repo}/contents/{LEDGER_PATH}?ref={head}",
                "-H",
                "Accept: application/vnd.github.raw",
            ]
        )
    return siblings


def main(argv: list[str]) -> int:
    local = pathlib.Path(argv[1] if len(argv) > 1 else LEDGER_PATH)
    if not local.is_file():
        print(f"ERROR: {local} not found — run from the repository root")
        return 2

    try:
        repo = _repo()
        me = _this_pr()
        base_ref = os.environ.get("BASE_SHA", "").strip() or "origin/main"
        base_text = _run(["git", "show", f"{base_ref}:{LEDGER_PATH}"])
        siblings = gather_siblings(repo, me)
    except Exception as exc:  # noqa: BLE001 — every failure here must be loud
        print(f"ERROR: could not compare {LEDGER_PATH} against the other open PRs:")
        print(f"  {exc}")
        print(
            "\nFailing closed. This guard needs `pull-requests: read` and a network\n"
            "read; 'we could not look' must never be reported as 'nothing found'\n"
            "(#993). Re-run, or fix the permission, rather than ignoring this."
        )
        return 2

    problems = collision_problems(
        base_text, local.read_text(encoding="utf-8"), siblings, me
    )
    checked = ", ".join(sorted(siblings)) or "none"
    if problems:
        print(f"Ledger id collisions for {me} (compared against {checked}):")
        for problem in problems:
            print(f"  {problem}")
        return 1

    print(f"OK: no ledger id on {me} is claimed by another open PR ({checked}).")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv))
