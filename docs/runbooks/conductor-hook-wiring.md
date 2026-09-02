# Wiring the Conductor's session to this repo's hooks

**Issue:** [#1042](https://github.com/FreeForCharity/FFC-Cloudflare-Automation/issues/1042) ·
**Ledger:** L218 · **Gates:** none

## The problem in one paragraph

Claude Code loads hooks from the **session's project root**. Every sandboxed agent clones this repo
and opens it as its project root, so `.claude/settings.json` applies and `.claude/hooks/` runs. The
Conductor does not: its project root is its own workspace
(`C:\ClaudeCodeDesktop\Claude_AI_OS_Routine`), and it merely `cd`s into a clone to work. The hub's
settings spell every hook path as `$CLAUDE_PROJECT_DIR/.claude/hooks/…`, and `$CLAUDE_PROJECT_DIR`
for that session is the **workspace**. The block never resolves. The one actor with `gh` as an org
admin, Azure CLI, M365 and write access to every FFC repo is the only agent running unguarded.

This is not theoretical. Run 134 piped a board audit into `tail` and read `$?` after the pipe,
reported `rc=0` from `tail` while the audit had refused to run unauthenticated. Replaying that exact
string into `guard_bash.py` **blocks it, exit 2, naming L50 verbatim**. The rule existed, was
correct, had already been promoted out of prose precisely because it kept being violated — and could
not fire.

## The choice, and why (AC1)

Three options were on the table in #1042. The hub now ships **(a) + (b): a tracked template,
rendered into the workspace with absolute paths.**

| Option                                                                        | Verdict                                                                                                                                                                                                                                                                     |
| ----------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **(a) a `settings.json` committed here, copied in**                           | **Chosen.** The config becomes a file a PR can review and a test can assert. Its weakness — the copy drifts from the source — is answered by the verifier below, which is run every bootstrap rather than trusted once.                                                     |
| **(b) hook paths made absolute so a workspace config can point at the clone** | **Chosen, as the mechanism for (a).** A copy that keeps `$CLAUDE_PROJECT_DIR` resolves against the workspace and is silently dead, which is the original bug wearing the fix's clothes.                                                                                     |
| **(c) move the Conductor's project root to the hub clone**                    | **Not taken here.** It is the cleanest end state and it changes how the scheduled routine is launched — @clarkemoyer's call, not an agent's. Nothing in this change forecloses it; when it lands, the verifier still passes and the template becomes dead weight to delete. |

The reason (a) needs (b) is worth stating plainly, because copying the hub's `settings.json`
verbatim is the obvious move and it fails **silently**: it is valid JSON, it has a real `hooks`
block, it passes every check that stops at config presence, and it resolves to nothing.
`test_the_hubs_own_settings_copied_verbatim_is_not_wired` exists to keep that case detected.

## Wiring it

```bash
# from the hub clone, once per workspace
python3 scripts/verify-conductor-hooks.py \
  --render --workspace "C:/ClaudeCodeDesktop/Claude_AI_OS_Routine" \
  --hub-clone "C:/ClaudeCodeDesktop/Claude_AI_OS_Routine/repos/FFC-Cloudflare-Automation"
```

`--render` refuses to clobber an existing `settings.json` without `--force`; the workspace's
`settings.local.json` (its `permissions` allowlist) is untouched and still layers on top.

The rendered file is a **build product**. Do not hand-edit it — change
`.claude/conductor/settings.template.json` and re-render, or the next drift check is comparing
against a source nobody updated.

## Verifying it every run (AC2, AC3)

Bootstrap runs the verifier and puts its one line in the run's START comment:

```bash
python3 repos/FFC-Cloudflare-Automation/scripts/verify-conductor-hooks.py \
  --workspace "$PWD"
```

`$PWD` is git-bash's MSYS spelling (`/c/...`), and that is accepted: the script maps a `/<drive>/`
prefix to `C:/` on Windows before building any `Path`. It has to — native Windows Python treats a
leading `/c/` as drive-relative and resolves it to `C:\c\...`, which would report a correctly-wired
workspace as NOT WIRED and, under `--render`, write the settings somewhere the session never looks.

```
HOOKS: wired -- …/.claude/hooks/guard_bash.py blocks the L50 shape and allows `git status --porcelain` (verify-conductor-hooks.py, exit 0)
HOOKS: NOT WIRED -- this run is unguarded (#1042). <what is wrong>
```

Exit 0 wired, 1 not, 2 usage. The line prints in **both** directions on purpose: a verifier that
only speaks up on failure leaves "guarded" and "the check never ran" identical in the record, which
is L218 one level up.

**It is a live probe, not a config diff (AC3).** The configured `PreToolUse`/`Bash` guard is fed one
command that must be blocked (the L50 pipeline-exit shape from run 134) and one that must be allowed
(`git status --porcelain`), and both verdicts have to land. Two-sided by design (#1027): a guard
stubbed to `exit 0` passes any check that only looks for a block it never sees, and a guard stubbed
to `exit 2` "blocks" everything including the commands step 0 needs. A guard that exits with any
_other_ code is reported as **crashed, never as a detection** — a guard that cannot start exits
non-zero exactly like a guard that caught you, and that failure points the flattering way (L203).

## What this does not cover

The verifier runs the resolved hook **file** with its own interpreter, not the configured command
string verbatim — executing an arbitrary string out of a config file is not a thing a verifier
should do. A command that names a real guard but neutralises it some other way (wrong interpreter,
an added flag) resolves and probes clean. Narrow gap; the alternative is worse.

It also says nothing about hooks other than the Bash guard beyond checking that their paths resolve.
`PreToolUse`/`Bash` is probed because it is the one that has already failed in production.
