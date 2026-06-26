#!/usr/bin/env python3
"""UserPromptSubmit hook.

Non-blocking. If the user pastes something that looks like a real secret into
the chat, inject a reminder into the agent's context so it does NOT write the
value into any file and instead routes it to GitHub secrets / .env. This mirrors
the "If User Provides a Secret" rule in AI_AGENT_INSTRUCTIONS.md.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402


def main():
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        sys.exit(0)

    prompt = data.get("prompt", "") or ""
    findings = common.find_secrets(prompt)
    if findings:
        # stdout from a UserPromptSubmit hook is added to the model's context.
        print(
            "[FFC security reminder] The message may contain a secret "
            f"({', '.join(findings)}). Do NOT write it into any file, commit, "
            "or log. Tell the user to store it in GitHub Actions secrets or a "
            "gitignored .env, and (if it was real and exposed) to rotate it."
        )
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
