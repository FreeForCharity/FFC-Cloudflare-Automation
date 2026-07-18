# Claude Code hooks — AI-agent security & quality guardrails

These hooks turn the rules in
[`.github/agents/AI_AGENT_INSTRUCTIONS.md`](../../.github/agents/AI_AGENT_INSTRUCTIONS.md) from
_documentation the agent is asked to remember_ into _controls the harness enforces automatically_
while any Claude Code session works on this repo.

They run for **Claude Code** (CLI, IDE, and Claude Code on the web). They do **not** affect other AI
tools (Copilot, ChatGPT) — for those, the markdown instructions and the CI checks remain the safety
net.

## What runs when

Wired up in [`../settings.json`](../settings.json):

| Hook event         | Matcher                  | Script             | Effect                                                                                                                                                |
| ------------------ | ------------------------ | ------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `SessionStart`     | —                        | `session_start.py` | Prints the guardrail summary into context. Never blocks.                                                                                              |
| `UserPromptSubmit` | —                        | `scan_prompt.py`   | If your message looks like it contains a real secret, injects a reminder to route it to GitHub secrets / `.env` instead of into a file. Never blocks. |
| `PreToolUse`       | `Bash`                   | `guard_bash.py`    | **Blocks** dangerous commands (see below).                                                                                                            |
| `PreToolUse`       | `Edit\|Write\|MultiEdit` | `guard_edit.py`    | **Blocks** writing sensitive files or secret content.                                                                                                 |
| `PostToolUse`      | `Edit\|Write\|MultiEdit` | `post_edit.py`     | Re-scans the written file for secrets (**blocks/flags** if found) and prints the matching CI quality check to run before pushing.                     |

`common.py` holds the shared secret-detection and sensitive-path logic.

## What gets blocked

**`guard_bash.py`**

- Disabling TLS / tampering with the agent proxy (`curl -k`/`--insecure`,
  `NODE_TLS_REJECT_UNAUTHORIZED=0`, `git config http.sslVerify false`, `unset HTTPS_PROXY`, …) —
  forbidden by the environment.
- Force-pushing to `main`/`master` (`--force` / `--force-with-lease` / `-f`).
- Printing secrets to logs (`echo`/`printenv`/`env` of `*_TOKEN`/`*_SECRET`/
  `*_KEY`/`CLOUDFLARE_API_TOKEN`/`${{ secrets.* }}`).
- A real-looking secret literal pasted into the command.
- Destructive `rm -rf` of `/`, `~`, `$HOME`, or `.git`.

**`guard_edit.py`**

- Authoring/overwriting `.env`, `*.pem`, `*.key`, `*.pfx`, `*.p12`, anything under `secrets/`,
  `id_rsa`, `id_ed25519` (`.example` / `.sample` / `.template` variants are allowed).
- Content containing a private-key block, a vendor token (GitHub / AWS / Slack / Google / Stripe /
  JWT), a Cloudflare-style token literal, or a hardcoded value assigned to an
  `api_key`/`secret`/`password`/…

Detection is tuned to **fail open** on anything ambiguous and to skip placeholders
(`your-token-here`, `${{ secrets.* }}`, …) and the documented fake token in
`AI_AGENT_INSTRUCTIONS.md`, so it does not block legitimate edits.

## When a block is wrong

If a guard blocks something legitimate, the fix is normally to use a placeholder or a secret
reference. If detection is genuinely wrong, adjust the patterns in `common.py`, add a case to
`test_hooks.py`, and confirm the suite still passes.

## Tests

```bash
python3 .claude/hooks/test_hooks.py
```

CI runs this on every change under `.claude/**`
(`.github/workflows/97-ai-agent-hooks-validate.yml`). The scripts are standard-library Python 3 only
— no dependencies.
