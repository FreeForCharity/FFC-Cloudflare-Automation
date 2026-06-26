#!/usr/bin/env python3
"""PreToolUse hook for Edit / Write / MultiEdit.

Blocks (exit 2) when an agent tries to:
  * author/overwrite a sensitive file (.env, *.pem, *.key, secrets/, ...), or
  * write content that contains a real-looking secret.

Example/template variants (.env.example, etc.) are allowed. Documented fake
tokens are allowed. Any internal error => allow (exit 0).
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402


def block(reason):
    sys.stderr.write(
        "BLOCKED by FFC security hook (.claude/hooks/guard_edit.py):\n"
        f"{reason}\n"
        "Use GitHub Actions secrets or a gitignored .env (see "
        ".github/agents/AI_AGENT_INSTRUCTIONS.md).\n"
    )
    sys.exit(2)


def _content_from(tool_input):
    """Collect every chunk of new text this tool would write."""
    parts = []
    if "content" in tool_input:
        parts.append(str(tool_input.get("content") or ""))
    if "new_string" in tool_input:
        parts.append(str(tool_input.get("new_string") or ""))
    for edit in tool_input.get("edits", []) or []:
        if isinstance(edit, dict):
            parts.append(str(edit.get("new_string") or ""))
    return "\n".join(parts)


def main():
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        sys.exit(0)

    tool_input = data.get("tool_input") or {}
    path = tool_input.get("file_path") or tool_input.get("path") or ""

    if common.is_sensitive_path(path):
        block(f"'{path}' is a sensitive file type that must never be committed.")

    findings = common.find_secrets(_content_from(tool_input))
    if findings:
        block("Content appears to contain a secret: " + ", ".join(findings) + ".")

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
