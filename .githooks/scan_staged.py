#!/usr/bin/env python3
"""Secret / sensitive-file scanner for the shared pre-commit hook.

Reuses the detection logic in .claude/hooks/common.py so Git and Claude Code
enforce the SAME rules for every contributor (not just Claude Code sessions).

Two modes:
  * no args      -> scan the staged index (used by .githooks/pre-commit)
  * <paths...>   -> scan those files' working-tree contents (manual / tests)

Exit 1 if a secret or sensitive file is found, else 0. Fails open (exit 0) if
anything unexpected happens, so a scanner bug never wedges commits -- but it
SAYS SO on stderr, because a crash that prints nothing is indistinguishable
from a clean scan (#996).
"""

import os
import subprocess
import sys
import traceback

# Printed whenever the scan aborts. Grep-able, and worded so nobody reads a
# successful commit as a passing scan.
DID_NOT_RUN = "pre-commit: SECRET SCAN DID NOT RUN"


def _git(args, check=True):
    # Decode git's output as UTF-8 explicitly. Git emits UTF-8 regardless of
    # the console codepage, so this is correct everywhere -- whereas bare
    # text=True decodes with the LOCALE codec, which is cp1252 on the Windows
    # Conductor host. cp1252 leaves five byte values undefined
    # (0x81/0x8D/0x8F/0x90/0x9D), so a staged line carrying a character whose
    # UTF-8 encoding contains one of them -- U+201D, the RIGHT curly double
    # quote, and U+274C, the cross mark, both via 0x9D -- aborted the read
    # before find_secrets() ever ran. The fail-open below then turned that
    # total failure into a successful commit (#996; same root cause as ledger
    # L35 / #945).
    #
    # NOT the em dash, despite what #996 and L07 both said: e2 80 94 is fully
    # defined in cp1252, so it decoded to mojibake and the scan completed.
    # tests/workflow-logic/test_githooks_scan_staged.py pins the real set.
    # errors="replace" degrades a genuinely undecodable byte to one character
    # instead of losing the whole file.
    proc = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    # A git command that FAILS must not look like a git command that found
    # nothing. Returning "" here let `git diff --cached` exit 128 ("index file
    # corrupt") and still produce exit 0 with an empty stderr -- the same silent
    # fail-open #996 is about, reached through the non-exception path, so the
    # handler at the bottom of this file never saw it. Raising routes it there,
    # which is where the DID_NOT_RUN notice already lives.
    #
    # stdout is None when the reader thread died mid-decode; that is a failed
    # read too, even though the return code is 0. UNTESTED AND CURRENTLY
    # UNREACHABLE: with the codec pinned above, the decode cannot raise, so
    # nothing can kill that thread. It is kept as defence in depth for whoever
    # removes `encoding=` again -- do not read it as a covered path.
    if check and (proc.returncode != 0 or proc.stdout is None):
        raise RuntimeError(
            "git " + " ".join(args) + f" exited {proc.returncode}"
            + (" and produced no readable output" if proc.stdout is None else "")
            + ": " + ((proc.stderr or "").strip()[:500] or "<no stderr>")
        )
    return proc.stdout or ""


def repo_root():
    # check=False deliberately: this one call has a legitimate failure mode
    # (invoked outside a work tree, e.g. manual path mode) and its own fallback.
    # Every other caller wants a failed git command to be loud.
    root = _git(["rev-parse", "--show-toplevel"], check=False).strip()
    return root or os.getcwd()


ROOT = repo_root()
sys.path.insert(0, os.path.join(ROOT, ".claude", "hooks"))
try:
    import common  # shared detection logic
except Exception:
    # Shared module unavailable -> don't block commits, but don't let the
    # commit look scanned either. Wrapped for the same reason as the handler at
    # the bottom of this file: anything raised while reporting a fail-open
    # would escape it and wedge the commit.
    try:
        sys.stderr.write(
            "\n" + DID_NOT_RUN + ": .claude/hooks/common.py could not be imported,\n"
            "so this commit was NOT checked for secrets. This is not a passing scan.\n\n"
            + traceback.format_exc()
            + "\n"
        )
    except Exception:
        pass
    sys.exit(0)


def staged_files():
    out = _git(["diff", "--cached", "--name-only", "--diff-filter=ACM"])
    return [f for f in out.splitlines() if f.strip()]


def staged_added_text(path):
    """Only the lines this commit ADDS, so pre-existing content never trips us."""
    out = _git(["diff", "--cached", "--unified=0", "--diff-filter=ACM", "--", path])
    added = [ln[1:] for ln in out.splitlines()
             if ln.startswith("+") and not ln.startswith("+++")]
    return "\n".join(added)


def worktree_text(path):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            return fh.read()
    except Exception:
        return ""


def main():
    args = sys.argv[1:]
    if args:
        files = args
        read_text = worktree_text
    else:
        files = staged_files()
        read_text = staged_added_text

    problems = []
    for f in files:
        if common.is_sensitive_path(f):
            problems.append(f"  {f}: sensitive file type must not be committed")
            continue
        findings = common.find_secrets(read_text(f))
        if findings:
            problems.append(f"  {f}: {', '.join(findings)}")

    if problems:
        sys.stderr.write(
            "\npre-commit BLOCKED: possible secret(s) / sensitive file(s):\n"
            + "\n".join(problems)
            + "\n\nUse GitHub Actions secrets or a gitignored .env instead.\n"
            "See .github/agents/AI_AGENT_INSTRUCTIONS.md.\n"
            "If this is genuinely a false positive: git commit --no-verify\n\n"
        )
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        # Fail open -- the docstring's reasoning stands: a scanner bug must not
        # wedge commits. But never silently. Before #996 this bare exit 0 made
        # a total scan failure look exactly like a clean scan, and that is how
        # a cp1252 decode crash went unnoticed for as long as the file existed.
        try:
            sys.stderr.write(
                "\n" + DID_NOT_RUN + ": the scanner crashed, so this commit was\n"
                "NOT checked for secrets. This is not a passing scan -- please\n"
                "report it.\n\n" + traceback.format_exc() + "\n"
            )
        except Exception:
            # Anything raised in here would escape the fail-open and wedge the
            # commit, which is the one outcome this handler exists to prevent.
            pass
        sys.exit(0)
