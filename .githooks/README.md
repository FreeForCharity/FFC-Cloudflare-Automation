# Shared git hooks

A `pre-commit` hook that scans staged changes for secrets and sensitive files **for every
contributor** — not just Claude Code sessions. It reuses the exact detection logic in
[`../.claude/hooks/common.py`](../.claude/hooks/common.py), so Git and Claude Code enforce identical
rules.

## Enable (opt-in, once per clone)

```sh
git config core.hooksPath .githooks
# or:
scripts/install-git-hooks.sh        # macOS/Linux
pwsh scripts/install-git-hooks.ps1  # Windows
```

It is opt-in by design (`core.hooksPath` is a local setting that is never set automatically), so it
can't surprise anyone or interfere with unrelated work.

## Behavior

On `git commit`, `pre-commit` runs `scan_staged.py`, which blocks the commit if any **staged**
change:

- adds a secret-looking value (private key, GitHub/AWS/Slack/Google/Stripe/JWT token,
  Cloudflare-style token, or a hardcoded `api_key`/`secret`/`password`/…), scanning only the
  **added** lines so pre-existing content never trips it; or
- adds a sensitive file (`.env`, `*.pem`, `*.key`, `secrets/`, …).

Placeholders (`your-token-here`, `${{ secrets.* }}`) are ignored. Detection fails open, so a scanner
error never blocks a commit.

## Bypass a confirmed false positive

```sh
git commit --no-verify
```

## Manual scan / tests

```sh
python3 .githooks/scan_staged.py <file>...   # scan specific working-tree files
python3 .claude/hooks/test_hooks.py          # full hook + pre-commit test suite
```

CI (`.github/workflows/97-ai-agent-hooks-validate.yml`) runs the test suite on changes under
`.claude/**` or `.githooks/**`. Standard-library Python 3 only.
