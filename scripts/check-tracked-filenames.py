#!/usr/bin/env python3
"""Guard: a tracked path must not carry a non-printable or non-ASCII character (#1165).

On 2026-08-10 a zero-byte file named U+F022 U+F022 (`ef 80 a2 ef 80 a2`) was
committed to `main` via #1162 and had to be removed by #1164. It is #1023's
artifact -- `test_729_add_collaborator.py` runs the step under test with
`cwd=REPO_ROOT`, so a full local suite drops it in the working tree -- and it
became *tracked* because a follow-up commit staged with `git add -A` while the
artifact sat untracked beside the edited files.

WHY NOTHING CAUGHT IT
    Every existing check passed, honestly:

      * the file is zero bytes, so no behaviour changes and no test breaks;
      * the name is unprintable, so it renders as `""`, as
        `\\357\\200\\242\\357\\200\\242`, or as nothing at all, depending on the
        tool doing the rendering;
      * `git status --porcelain --untracked-files=no` -- the tracked-only
        cleanliness check `CLAUDE.md` prescribes for exactly this artifact --
        reports **clean**, correctly, because by then the file is tracked. That
        check is aimed at the untracked case and goes quiet once the mistake has
        actually been made.

    It was found only because a `git checkout main && git pull` happened to print
    `create mode 100644 "\\357\\200\\242\\357\\200\\242"` in a diffstat.

WHY CI IS THE RIGHT TIER, NOT A HOOK
    Per ledger **L218** the Conductor's session loads none of this repository's
    hooks, and this was a Conductor-side mistake -- a `guard_bash.py` rule
    against `git add -A` could not have fired. CI is the tier that catches it,
    because the defect is visible in the committed tree from any host.

WHAT COUNTS AS A VIOLATION
    A tracked path holding any character outside printable ASCII
    (`U+0020`-`U+007E`): a control character, `DEL`, a private-use character
    such as the `U+F022` above, or an ordinary non-ASCII character. Findings are
    reported per character with the codepoint spelled out, because the whole
    difficulty of the original incident was that the name could not be read off
    a terminal.

    THE RULE IS THE BROAD ONE, DELIBERATELY. #1165 offers a choice: narrow the
    rule to the private-use area `U+E000`-`U+F8FF`, or take every non-ASCII
    character and allowlist what already exists. The tree has **857 tracked
    paths and zero** outside printable ASCII, so the broad rule costs nothing
    today and is strictly stronger -- the private-use range is a subset of it.
    A legitimate non-ASCII path added later is a one-line, reasoned entry in
    `ALLOWED_NON_ASCII_PATHS`, which is the same freeze shape every sibling
    guard in `scripts/check-*.py` uses, and stale entries there are an error in
    their own right.

THE ONE MECHANIC THAT DEFEATS THIS CHECK
    **`git` quotes non-ASCII names by default.** Piping
    `git ls-files` into a codepoint filter reads the literal ASCII text
    `"\\357\\200\\242\\357\\200\\242"` -- backslashes, digits and quotes, every
    one of them printable ASCII -- and finds nothing. The guard then passes on a
    tree containing the very file it is looking for. This happened once already,
    while diagnosing the incident.

    So the tree is read with **`-z`**, which is documented to terminate each path
    with NUL *and to not quote filenames*. That is the load-bearing line of this
    file. It is pinned by `test_the_guard_reads_an_unprintable_name_undecoded`
    against a real scratch repository, and the trap itself is pinned by
    `test_default_git_output_quotes_the_name_into_printable_ascii`, so the reason
    for the choice is an executable measurement rather than a comment.

    `git ls-files` (the index) is the enumeration rather than
    `git ls-tree HEAD` (the commit): "tracked" is what the issue is about, and
    the index additionally covers a path that is staged but not yet committed --
    the state the original mistake passed through.

    Output is read as **bytes** and decoded with `errors="surrogateescape"`
    rather than in text mode. A filename is not required to be valid UTF-8 at
    all, and a decode that raises would turn the worst case -- a name so broken
    it cannot be decoded -- into a crash instead of the finding it should be.
    (This is also why nothing here trips `check-subprocess-encoding.py`: the call
    is binary, and bytes have no codec to get wrong.)

FAIL CLOSED
    A `git` invocation that fails, or output that cannot be read, is REPORTED
    and exits non-zero. A guard that goes quiet on what it cannot enumerate
    reads as a pass, which is the failure this repository keeps rediscovering.

Usage:
    python3 scripts/check-tracked-filenames.py
    python3 scripts/check-tracked-filenames.py --repo /path/to/clone
"""

from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys
import unicodedata

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

# Paths deliberately allowed to hold a character outside printable ASCII, each
# with a stated reason beside it. Empty: the population is zero (#1165), so this
# guard fails on ANY instance anywhere in the tree. An entry here is an explicit,
# reasoned exception -- not the normal state.
ALLOWED_NON_ASCII_PATHS: tuple[str, ...] = ()

# The printable ASCII band. Everything outside it is a finding.
PRINTABLE_LO = 0x20
PRINTABLE_HI = 0x7E

# The Unicode private-use area named by #1165. Not the rule -- the rule is the
# broad one above -- but a character in this range gets a sharper label, because
# a private-use codepoint in a filename is never a deliberate choice.
PRIVATE_USE_RANGES = ((0xE000, 0xF8FF), (0xF0000, 0xFFFFD), (0x100000, 0x10FFFD))


class GitUnavailable(RuntimeError):
    """`git` could not enumerate the tracked set. Fail closed, never skip."""


def _classify(char: str) -> str:
    """A human-readable reason for one offending character.

    The label matters more here than in most guards: the incident's whole
    difficulty was that the name is invisible in a terminal, so the report has
    to carry what the rendering cannot.
    """
    point = ord(char)

    if 0xDC80 <= point <= 0xDCFF:
        # A surrogate produced by `errors="surrogateescape"`: the raw byte was
        # not decodable at all, which is worse than an odd codepoint.
        return f"undecodable byte 0x{point - 0xDC00:02X}"

    if any(lo <= point <= hi for lo, hi in PRIVATE_USE_RANGES):
        return f"private-use character U+{point:04X}"

    if point < PRINTABLE_LO or point == 0x7F:
        return f"control character U+{point:04X}"

    try:
        name = unicodedata.name(char)
    except ValueError:
        name = "unnamed"
    return f"non-ASCII character U+{point:04X} ({name})"


def offending_characters(path: str) -> list[tuple[int, str]]:
    """Every (index, reason) in `path` outside printable ASCII, in order."""
    return [
        (index, _classify(char))
        for index, char in enumerate(path)
        if not (PRINTABLE_LO <= ord(char) <= PRINTABLE_HI)
    ]


def tracked_paths(repo_root: pathlib.Path | str = REPO_ROOT) -> list[str]:
    """Every tracked path, NUL-delimited and unquoted.

    `-z` is load-bearing: it is what stops `git` rendering a non-ASCII name as
    the printable ASCII escape `"\\357\\200\\242"`, which a codepoint scan reads
    as clean. Removing it does not break this function -- it makes it lie.
    """
    try:
        proc = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=str(repo_root),
            capture_output=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover - env
        raise GitUnavailable(f"could not run `git ls-files -z`: {exc}") from exc

    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip() or "no stderr"
        raise GitUnavailable(
            f"`git ls-files -z` exited {proc.returncode} in {repo_root}: {detail}"
        )

    # Bytes, not text mode: a filename need not be valid UTF-8, and a raising
    # decode would turn the worst case into a crash instead of a finding.
    raw = proc.stdout.decode("utf-8", "surrogateescape")
    return [entry for entry in raw.split("\0") if entry]


def scan(paths: list[str], allowed: tuple[str, ...] | None = None) -> list[str]:
    """Findings, one line per offending path, naming every offending character."""
    allowed_set = set(ALLOWED_NON_ASCII_PATHS if allowed is None else allowed)
    findings: list[str] = []

    for path in sorted(paths):
        if path in allowed_set:
            continue
        bad = offending_characters(path)
        if not bad:
            continue
        # `ascii()` so the report survives a terminal that cannot render the
        # name -- the failure that hid the original incident for a week.
        detail = "; ".join(f"index {index}: {reason}" for index, reason in bad)
        findings.append(f"{ascii(path)} -- {detail}")

    return findings


def stale_allowances(paths: list[str], allowed: tuple[str, ...] | None = None) -> list[str]:
    """Allowlist entries that no longer describe the tree.

    Same rule the sibling guards apply to their freezes: an exception nobody
    prunes stops being a list of known exceptions.
    """
    allowed_tuple = ALLOWED_NON_ASCII_PATHS if allowed is None else allowed
    tracked = set(paths)
    return [
        f"{ascii(entry)}: listed in ALLOWED_NON_ASCII_PATHS but is not tracked. "
        f"Delete the stale entry."
        for entry in allowed_tuple
        if entry not in tracked
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--repo",
        default=str(REPO_ROOT),
        help="repository to scan (default: this checkout)",
    )
    args = parser.parse_args(argv)

    try:
        paths = tracked_paths(args.repo)
    except GitUnavailable as exc:
        # Fail closed. An enumeration that did not complete is not a clean tree.
        print(f"::error::could not enumerate tracked paths\n\n  {exc}")
        return 1

    errors = scan(paths) + stale_allowances(paths)

    if errors:
        print("::error::tracked path(s) with a non-printable or non-ASCII name\n")
        for error in errors:
            print(f"  {error}")
        # ASCII ONLY below this line, and the reason is narrower than it first
        # looks. `print()` on a Windows console encodes with the console
        # codepage, and a character that codepage cannot represent raises
        # UnicodeEncodeError -- a correct finding becomes a traceback on the host
        # most likely to have produced the file (ledger L35, #945). cp1252 does
        # NOT fail on an em dash or an accented letter (0x97 and 0xe9); it fails
        # on an arrow, an emoji, a box-drawing rule. So an ASCII rule here is a
        # policy, not a bug fix: it is the only band safe on every console, and
        # it costs nothing in a report whose job is to be legible. The docstring
        # above is never printed and is not bound by it.
        print(
            "\nA name like this is invisible in a diff, renders differently in every "
            "tool, and survives every other check in this repo: the file is usually "
            "zero bytes, so nothing breaks, and `git status --untracked-files=no` "
            "reports clean once it is tracked (#1165). The index above is the "
            "character offset in the path, and the codepoint is what your terminal "
            "will not show you.\n"
            "\n"
            "The quoted name in each finding is a Python literal, so it round-trips: "
            "copy it verbatim (quotes included) as the single argument below and the "
            "path is reconstructed exactly, without your shell or your terminal ever "
            "having to represent the character.\n"
            "\n"
            "    python3 -c 'import ast,subprocess,sys; "
            'subprocess.run(["git","rm","--cached","--",ast.literal_eval(sys.argv[1])])\''
            " \"<paste the quoted name here>\"\n"
            "\n"
            "If the path is legitimate, add it to ALLOWED_NON_ASCII_PATHS in this "
            "file with a reason beside it."
        )
        return 1

    print(
        f"tracked filenames OK: {len(paths)} tracked path(s) scanned; every name is "
        f"printable ASCII. The allowlist (ALLOWED_NON_ASCII_PATHS) holds "
        f"{len(ALLOWED_NON_ASCII_PATHS)} entr"
        f"{'y' if len(ALLOWED_NON_ASCII_PATHS) == 1 else 'ies'}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
