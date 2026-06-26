#!/usr/bin/env python3
"""SessionStart hook.

Surfaces the repo's AI-agent guardrails at the top of every session so the
rules are in context from the first turn (stdout is added to the context).
Purely informational -- never blocks.
"""

import sys


BANNER = """\
[FFC AI-agent guardrails active]
Security hooks in .claude/hooks/ enforce .github/agents/AI_AGENT_INSTRUCTIONS.md:
  * Edits/Writes to .env, *.pem, *.key, secrets/ are blocked.
  * Edits or Bash commands containing a real-looking secret are blocked.
  * Bash that disables TLS/proxy, force-pushes main, or echoes secrets is blocked.
Quality (mirrors .github/workflows/ci.yml): Prettier for web files; PSScriptAnalyzer
+ Invoke-Formatter for *.ps1; actionlint for workflows. Run these before pushing.
Secrets live only in the per-service GitHub Environments (cloudflare-prod-read,
cloudflare-prod-write, whmcs-prod, m365-prod, github-prod, wpmudev-prod) -- never
in the repo. Reference them as ${{ secrets.* }}, never as literals.
"""


def main():
    sys.stdout.write(BANNER)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
