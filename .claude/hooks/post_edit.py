#!/usr/bin/env python3
"""PostToolUse hook for Edit / Write / MultiEdit.

Two jobs after a file is written:
  1. Security backstop: re-scan the file on disk for secrets. If something
     slipped through, tell the agent (exit 2) so it removes it immediately.
  2. Quality nudge (non-blocking): remind the agent which CI check governs the
     file it just touched, so it runs the matching local formatter/linter
     before pushing -- matching .github/workflows/ci.yml.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

# Maps file extension -> the local command that mirrors the CI gate.
QUALITY_HINTS = {
    ".ps1": "PSScriptAnalyzer + Invoke-Formatter (pwsh scripts/format-powershell.ps1; scripts/analyze-powershell.ps1)",
    ".psm1": "PSScriptAnalyzer + Invoke-Formatter (scripts/analyze-powershell.ps1)",
    ".js": "Prettier (npx prettier@3.3.3 --write <file>)",
    ".mjs": "Prettier (npx prettier@3.3.3 --write <file>)",
    ".cjs": "Prettier (npx prettier@3.3.3 --write <file>)",
    ".json": "Prettier (npx prettier@3.3.3 --write <file>)",
    ".md": "Prettier (npx prettier@3.3.3 --write <file>)",
    ".css": "Prettier (npx prettier@3.3.3 --write <file>)",
    ".html": "Prettier (npx prettier@3.3.3 --write <file>)",
}


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

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
