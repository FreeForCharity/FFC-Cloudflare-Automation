#!/usr/bin/env python3
"""Secret / sensitive-file scanner for the shared pre-commit hook.

Reuses the detection logic in .claude/hooks/common.py so Git and Claude Code
enforce the SAME rules for every contributor (not just Claude Code sessions).

Two modes:
  * no args      -> scan the staged index (used by .githooks/pre-commit)
  * <paths...>   -> scan those files' working-tree contents (manual / tests)

Exit 1 if a secret or sensitive file is found, else 0. Fails open (exit 0) if
anything unexpected happens, so a scanner bug never wedges commits.
"""

import os
import subprocess
import sys


def _git(args):
    return subprocess.run(["git", *args], capture_output=True, text=True).stdout


def repo_root():
    root = _git(["rev-parse", "--show-toplevel"]).strip()
    return root or os.getcwd()


ROOT = repo_root()
sys.path.insert(0, os.path.join(ROOT, ".claude", "hooks"))
try:
    import common  # shared detection logic
except Exception:
    sys.exit(0)  # shared module unavailable -> don't block commits


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
        sys.exit(0)
