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


class GitFailed(Exception):
    """A git command exited non-zero, so its output was never read.

    Raised rather than returning "" because an empty string is
    indistinguishable from "nothing is staged": that read as a clean scan and
    exited 0 in silence -- the same defect as the decode crash below, reached
    through the non-exception path instead. The handler at the bottom of this
    file turns this into a loud fail-open.
    """


def _git(args, required=True):
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
    if required and proc.returncode != 0:
        raise GitFailed(
            "git %s exited %d: %s"
            % (" ".join(args), proc.returncode, (proc.stderr or "").strip()[:500])
        )
    return proc.stdout or ""


def repo_root():
    # required=False: outside a work tree this legitimately fails, and the
    # cwd fallback is the answer. Everything that actually READS content keeps
    # required=True, so a failure there can never pass for "nothing staged".
    root = _git(["rev-parse", "--show-toplevel"], required=False).strip()
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
