#!/usr/bin/env python3
"""PostToolUse hook for Edit / Write / MultiEdit.

Three jobs after a file is written:
  1. Security backstop: re-scan the file on disk for secrets. If something
     slipped through, tell the agent (exit 2) so it removes it immediately.
  2. Quality nudge (non-blocking): remind the agent which CI check governs the
     file it just touched, so it runs the matching local formatter/linter
     before pushing -- matching .github/workflows/ci.yml.
  3. Ledger-citation nudge (non-blocking): name the `docs/lessons-ledger.md`
     rows that cite the file just edited by `path:LINE`, because inserting
     lines into it moves those anchors and `test_lessons_ledger.py` goes red.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

# Maps file extension -> the local command that mirrors the CI gate
# (.github/workflows/ci.yml). Prettier is pinned + --ignore-unknown like CI; the
# PowerShell helpers run under pwsh and operate on *.ps1.
_PRETTIER = "Prettier (npx --yes prettier@3.8.1 --write <file> --ignore-unknown)"
_PS1 = ("PSScriptAnalyzer + Invoke-Formatter "
        "(pwsh scripts/format-powershell.ps1; pwsh scripts/analyze-powershell.ps1)")
QUALITY_HINTS = {
    ".ps1": _PS1,
    # The repo's helper scripts only target *.ps1, so scan a module directly.
    ".psm1": "PSScriptAnalyzer (pwsh -c 'Invoke-ScriptAnalyzer -Path <file>')",
    ".js": _PRETTIER,
    ".mjs": _PRETTIER,
    ".cjs": _PRETTIER,
    ".json": _PRETTIER,
    ".md": _PRETTIER,
    ".css": _PRETTIER,
    ".html": _PRETTIER,
}


# --- Ledger-citation nudge -------------------------------------------------
#
# `docs/lessons-ledger.md` cites source locations as `path:LINE`, and
# `tests/workflow-logic/test_lessons_ledger.py` fails a PR whose quoted anchor
# no longer sits on the cited line. Inserting lines anywhere above an anchor
# moves it, so a change to a cited file can break a row it never mentions.
#
# CLAUDE.md has an append-only marker for exactly this reason and a guard that
# pins it. Every OTHER cited file has nothing: the author learns at CI, after a
# push, from a module they had no reason to run. Run 250 shipped #1611 with the
# 740 workflow (+44) and its test module (+113) green on their own tests
# (117/117) and red on L222/L318, whose anchors those insertions had moved.
#
# So this is a nudge, not a rule: it cannot know whether an anchor moved
# without reparsing the ledger against the new file, and the guard already does
# that correctly at PR time. What it can do is name the rows at risk at the
# moment of the edit, while the author is still in a position to run the guard.
LEDGER_REL = "docs/lessons-ledger.md"
LEDGER_TEST = "tests/workflow-logic/test_lessons_ledger.py"

# A row opens with its id in the first cell: `| L222 | …`.
_LEDGER_ROW = re.compile(r"^\|\s*(L\d+)\s*\|")


def ledger_citing_rows(path, ledger_text):
    """Ledger row ids whose text cites `path` by `:LINE`.

    Pure over (path, ledger_text) so the rule is testable against planted text
    rather than only against the ledger's current contents -- the same reason
    test_lessons_ledger.py keeps its own checks pure over (text, files).

    Matches on the BASENAME because the ledger cites both repo-relative paths
    and bare filenames, and the guard itself resolves a bare basename against
    the tracked tree. Over-matching here is cheap (one extra advisory line);
    under-matching restores the silence this exists to break.
    """
    base = os.path.basename(path.replace("\\", "/"))
    if not base:
        return []
    cited = re.compile(r"`[^`]*" + re.escape(base) + r":\d")
    rows = []
    for line in ledger_text.splitlines():
        row = _LEDGER_ROW.match(line)
        if row and cited.search(line) and row.group(1) not in rows:
            rows.append(row.group(1))
    return rows


def ledger_hint(path, repo_root):
    """The advisory line for `path`, or None. Never raises: a missing or
    unreadable ledger means no hint, never a blocked edit."""
    norm = os.path.abspath(path).replace("\\", "/")
    root = os.path.abspath(repo_root).replace("\\", "/").rstrip("/")
    # Only speak about files inside the repo whose ledger this is. The
    # Conductor edits five clones with the hub's hooks loaded, and a basename
    # match across repos would be a confident hint about the wrong file.
    if not norm.startswith(root + "/"):
        return None
    rel = norm[len(root) + 1:]
    try:
        with open(os.path.join(root, LEDGER_REL), encoding="utf-8") as fh:
            ledger = fh.read()
    except Exception:
        return None
    if rel == LEDGER_REL:
        return (f"[ledger] You edited the ledger itself -> run: "
                f"python {LEDGER_TEST}")
    rows = ledger_citing_rows(rel, ledger)
    if not rows:
        return None
    return (f"[ledger] {LEDGER_REL} cites this file by line "
            f"({', '.join(rows)}). If you INSERTED lines, those anchors moved "
            f"-> run: python {LEDGER_TEST}")


def main():
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        sys.exit(0)

    tool_input = data.get("tool_input") or {}
    path = tool_input.get("file_path") or tool_input.get("path") or ""
    if not path:
        sys.exit(0)

    # 1. Security backstop: re-scan the written file.
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            findings = common.find_secrets(fh.read())
    except Exception:
        findings = []
    if findings:
        sys.stderr.write(
            "SECURITY: the file just written appears to contain a secret "
            f"({', '.join(findings)}). Remove it now and rotate the credential "
            "if it was real. See .github/agents/AI_AGENT_INSTRUCTIONS.md.\n"
        )
        sys.exit(2)

    # 2. Quality nudge (non-blocking, stdout shows as transcript info).
    _, ext = os.path.splitext(path.lower())
    norm = path.replace("\\", "/")
    if "/.github/workflows/" in norm and ext in (".yml", ".yaml"):
        print("[quality] Workflow edited -> CI runs actionlint + Prettier. "
              "Keep the two-digit name prefix unique.")
    elif ext in QUALITY_HINTS:
        print(f"[quality] Before pushing, run: {QUALITY_HINTS[ext]}")

    # 3. Ledger-citation nudge. Additive to the above: a cited file is usually
    #    also governed by a CI formatter, and both lines are worth having.
    hint = ledger_hint(path, os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    if hint:
        print(hint)

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
