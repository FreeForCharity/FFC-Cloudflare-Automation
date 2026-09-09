# Wiring an unrooted session to this repo's hooks

**Issues:** [#1042](https://github.com/FreeForCharity/FFC-Cloudflare-Automation/issues/1042) (the
Conductor) · [#1237](https://github.com/FreeForCharity/FFC-Cloudflare-Automation/issues/1237) (the
multi-repo cloud worker) · **Ledger:** L218, corrected by L261 · **Gates:** none

## The problem in one paragraph

Claude Code loads hooks from the **session's project root**. A session that clones this repo and
opens _it_ as its project root gets `.claude/settings.json` applied and `.claude/hooks/` run. Two
scheduled sessions do not, and they are the ones with authority. The hub's settings spell every hook
path as `$CLAUDE_PROJECT_DIR/.claude/hooks/…`, and `$CLAUDE_PROJECT_DIR` for either session is not
the clone, so the block never resolves.

| session                  | project root                                              | state                                                               |
| ------------------------ | --------------------------------------------------------- | ------------------------------------------------------------------- |
| **Conductor** (#1042)    | `C:\ClaudeCodeDesktop\Claude_AI_OS_Routine`               | `settings.local.json` with a `permissions` block and no `hooks` key |
| **cloud worker** (#1237) | `/home/user` — the parent of five side-by-side FFC clones | **no `.claude` directory at all**                                   |

The Conductor is the actor with `gh` as an org admin, Azure CLI, M365 and write access to every FFC
repo. The cloud worker is the population that does the issue→PR work — the population L218
originally called the _protected_ class. Neither is guarded, and the condition is the project root,
not the job title.

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

For a **multi-repo cloud worker** the same command applies, with the session root — the _parent_ of
the clones — as the workspace. Render at clone time, before the run does any work:

```bash
python3 /home/user/FFC-Cloudflare-Automation/scripts/verify-conductor-hooks.py \
  --render --workspace /home/user \
  --hub-clone /home/user/FFC-Cloudflare-Automation
```

**A render takes effect on the session that performs it — measured 2026-09-08, not assumed.** In the
worker session that filed #1237, the Bash call immediately after the render
(`ls … | tail; echo "RC=$?"`) was refused by `PreToolUse:Bash` naming L50, and a
`git status --porcelain` after that ran normally. Settings are read mid-session, not only at
startup, so a worker that finds itself unguarded does **not** have to spend the run that way —
render at step 0 and continue guarded. That also makes the environment-definition fix an
optimisation rather than a prerequisite.

The settings land on the **session root**, never inside a clone: a clone is a checkout a PR would
carry, and `/home/user/.claude/settings.json` is per-session scaffolding. Setting the session's
project root to the hub clone in the environment definition is the other valid fix and needs no
rendered file at all — but it only guards the run while the unit of work is in _this_ repo, and a
worker holding five clones routinely edits the others.

The rendered file is a **build product**. Do not hand-edit it — change
`.claude/conductor/settings.template.json` and re-render, or the next drift check is comparing
against a source nobody updated.

## Verifying it every run (AC2, AC3)

Bootstrap runs the verifier and puts its one line in the run's START comment:

```bash
python3 repos/FFC-Cloudflare-Automation/scripts/verify-conductor-hooks.py \
  --workspace "$PWD"
```

⚠️ **Do not copy that `$PWD` form into any other session.** It is correct _here and only here_,
because the Conductor's bootstrap runs from its workspace, so its cwd genuinely **is** its project
root. Anywhere else — a worker whose cwd is a clone, most of all — `$PWD` promotes a guess to a
statement and reports `HOOKS: wired` for a tree that is not the session. That is exactly how the
foot-gun reached `AGENTS.md` (see the section below); a reader who scrolls to the nearest snippet
and copies it is the case this note exists for. Elsewhere, run the verifier **bare**.

`$PWD` is git-bash's MSYS spelling (`/c/...`), and that is accepted: the script maps a `/<drive>/`
prefix to `C:/` on Windows before building any `Path`. It has to — native Windows Python treats a
leading `/c/` as drive-relative and resolves it to `C:\c\...`, which would report a correctly-wired
workspace as NOT WIRED and, under `--render`, write the settings somewhere the session never looks.

```
HOOKS: wired -- …/.claude/hooks/guard_bash.py blocks the L50 shape and allows `git status --porcelain` (verify-conductor-hooks.py, exit 0)
HOOKS: NOT WIRED -- this run is unguarded (#1042). <what is wrong>
HOOKS: UNVERIFIED -- workspace was inferred, not stated, and it ships the hooks under test … (#1237)
```

Exit 0 wired, 1 not-or-unknown, 2 usage. The line prints in **all** directions on purpose: a
verifier that only speaks up on failure leaves "guarded" and "the check never ran" identical in the
record, which is L218 one level up.

### Pass `--workspace` only when you know the root — otherwise run bare (#1237)

There are three provenances, and **only two of them are statements**: an explicit `--workspace`, and
`$CLAUDE_PROJECT_DIR`, which the session itself sets. The third — the cwd fallback, used when
neither is present — is a guess.

One particular guess always passes: a clone that ships the `.claude/hooks/` being probed satisfies
every check above by construction. Run with no arguments from inside the hub clone, this script
printed `HOOKS: wired … exit 0` inside the five-repo worker session, which loads nothing whatsoever.
The guard it found was real and the probe honestly passed — against a tree that was not the session.

**So the safe default is the bare invocation, not a flag.** Bare already prefers
`$CLAUDE_PROJECT_DIR` when the session exports it, and refuses rather than certifying when it must
guess. Supplying `--workspace "$PWD"` looks more careful and is strictly worse — it promotes the
guess to a statement and restores the false green. The Conductor's bootstrap line above is the one
place `$PWD` is correct, and only because that session's cwd _is_ its project root.

This heading previously read _"Always pass `--workspace`"_, which is how the `$PWD` foot-gun got
into `AGENTS.md` in the first place. Ledger **L261**: the operation to watch for is anything that
promotes a guess to a statement — a default, a shell fallback, an empty value, or a line of
documentation recommending a flag.

`--workspace ""` is refused as a **usage error** (exit 2) rather than honoured, because
`Path("").resolve()` is the cwd — an empty value is the inferred fallback wearing the explicit
flag's clothes, and it reproduced the false green through this very fix's own escape hatch. Caught
in review on #1253.

That case is now refused: an **inferred** workspace that ships hooks reports `HOOKS: UNVERIFIED`,
exit 1, and names the sibling-clone parent it believes the real session root to be, so the remedy is
one paste away. Stating the workspace — or having the session export `CLAUDE_PROJECT_DIR` — restores
a normal verdict, because the refusal is about **provenance**, not about the config.

Worth keeping in view when reading the rest of this page: the script exists specifically to make
L218's false green impossible, shipped mutation-tested, and still carried the same fault one level
down, reachable by omitting a flag. Ledger **L261**.

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
