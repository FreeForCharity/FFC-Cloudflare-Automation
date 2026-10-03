#!/usr/bin/env python3
"""Guard: a prettier command a reader can paste must name the CI-pinned version.

Why this exists. `722-ci.yml` checks formatting with `npx --yes prettier@3.8.1`.
`npx prettier` without the pin fetches whatever is current, and prettier's
Markdown reflow is not stable across minor versions — so an unpinned local check
disagrees with CI in both directions. CLAUDE.md has said so since run 80-odd
("Format with the CI-pinned prettier"), and `AGENTS.md` — the file every agent is
told to read FIRST — still handed out the unpinned form in its safety-table
conflict recipe. Documentation that contradicts itself is resolved by whichever
file the reader opened, which is not a property anyone chose.

Measured, 2026-10-03 (Conductor run 187): `npx prettier@3 --check
docs/lessons-ledger.md` reported "Code style issues found" on a branch that was
in fact clean; `npx --yes prettier@3.8.1 --check` on the same bytes reported
"All matched files use Prettier code style". The branch under review was
correct and the check was wrong, which is the expensive direction — a reviewer
who trusts it sends a clean PR back.

WHAT IS AND IS NOT A VIOLATION, because the distinction is the whole design.
A violation is an **invocation**: a backticked span that starts with `npx`,
names `prettier` without a full `@MAJOR.MINOR.PATCH`, and carries an action flag
(`--write`, `--check`, `--list-different`). That is a line someone pastes.
`prettier@3` counts as unpinned -- it is a range, and the range is what moved.

A bare **mention** of `npx prettier` is not a violation, and must not be, because
the clearest way to warn about an anti-pattern is to name it — CLAUDE.md's own
explanation of this exact rule does precisely that:

    `npx prettier` fetches the latest version, whose Markdown reflow differs

A guard that fired there would make the warning unwritable, and the usual
response to an unwritable warning is to delete it. Same negative-polarity
problem the 726 error-swallowing scanner solves by skipping comments.

Exit 0 when clean, 1 with findings on stdout. Takes paths or defaults to every
tracked Markdown file.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

# A backtick-delimited span. Prettier invocations in these docs are always
# written inside code spans or fenced blocks; scanning the raw line instead
# would match prose that merely abuts a backtick.
_SPAN = re.compile(r"`([^`]+)`")

# `npx`, optionally `--yes`/`-y`, then `prettier` not followed by a FULL
# `@MAJOR.MINOR.PATCH`. Requiring all three parts is deliberate: `prettier@3` is
# still a floating range, and `@3` quietly resolving to a newer minor than CI's
# 3.8.1 is the exact failure this guard was written from. `@latest` likewise.
_UNPINNED = re.compile(r"\bnpx\b(?:\s+(?:--yes|-y))?\s+prettier(?!@\d+\.\d+\.\d+\b)")

# An action flag makes the span a command rather than a name.
_ACTION = re.compile(r"--(?:write|check|list-different)\b")


def _tracked_markdown() -> list[pathlib.Path]:
    """Tracked `*.md` only. An untracked file is somebody's scratch copy, and
    failing CI on it would make the guard a nuisance rather than a rule."""
    out = subprocess.run(
        ["git", "ls-files", "-z", "*.md"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout
    return [REPO_ROOT / p for p in out.split("\0") if p]


def scan_text(text: str, name: str = "<text>") -> list[str]:
    """Findings in one document, as `name:lineno: span` strings.

    Split out from the file walk so the test module can feed it the pre-fix
    shape directly. A scanner only exercised against a tree that is clean by
    construction asserts nothing (#943's lesson, applied again).
    """
    findings: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for span in _SPAN.findall(line):
            if _UNPINNED.search(span) and _ACTION.search(span):
                findings.append(f"{name}:{lineno}: `{span}`")
    return findings


def scan_paths(paths: list[pathlib.Path]) -> list[str]:
    findings: list[str] = []
    for path in paths:
        rel = path.relative_to(REPO_ROOT).as_posix()
        findings.extend(scan_text(path.read_text(encoding="utf-8"), rel))
    return findings


def main(argv: list[str]) -> int:
    paths = [pathlib.Path(a).resolve() for a in argv[1:]] or _tracked_markdown()
    findings = scan_paths(paths)
    if findings:
        print("Unpinned prettier invocation(s) — CI pins prettier@3.8.1:")
        for f in findings:
            print(f"  {f}")
        print(
            "\nUse `npx --yes prettier@3.8.1 --write <files>`. An unpinned run uses a "
            "newer Markdown reflow than CI and reports a clean file as dirty."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv))
