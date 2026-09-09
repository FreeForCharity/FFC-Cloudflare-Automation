# AGENTS.md — FFC-Cloudflare-Automation

Canonical onboarding for any AI agent (Claude, Copilot, Jules, …) or new admin working in this
repository. Tool-specific notes live in `CLAUDE.md`; org-wide mission/security rules follow the FFC
global policy (never expose secrets; Conventional Commits; PRs, never direct pushes to `main`).

## What this repo is

The automation hub for Free For Charity infrastructure: **105** GitHub Actions workflows that drive
Cloudflare (DNS/registrar), WHMCS (billing/support), Microsoft 365, Zeffy, Google (Analytics/GTM),
WPMUDEV, and the FFC GitHub org itself. PowerShell-first scripts in `scripts/`, credentials from
Azure Key Vault via OIDC (never GitHub secrets).

**Before fleet-wide, credential or monitoring work, read `docs/lessons-ledger.md`.** It is the
durable record of findings that cost previous sessions hours — dead triggers, swallowed 403s,
presence mistaken for validity — each with its evidence link and the guard (if any) now holding it.
Add a row there in the same PR as the fix whenever something surprises you.

**This repo is not all of FFC — read `docs/ffc-repo-map.md` before writing anything about _process_,
_standards_, or _how a charity works on their site_.** This repo is authoritative for how the
automation runs; **`FreeForCharity/FFC-IN-ffcadmin.org`** is authoritative for how FFC develops (the
agent issue→PR workflow, code quality standards, the four-gate intake journey, agentic-OS
governance), and each `FFC-EX-<domain>` repo ships its own charity-facing onboarding docs plus a
preconfigured `.claude/` agent set. The map names what to read for which task and where the two
repos are coupled. It exists because a session once wrote a from-scratch builder guide out of this
repo alone and got the package manager and the merge policy wrong, with the correct source one
public clone away.

## First: check whether this repo's hooks are loaded for YOU (#1237)

Several rules below are written as "`guard_bash.py` blocks / warns about X". That is true only for a
session whose **project root is this repository**. Claude Code loads hooks from the project root and
nowhere else, so `cd`-ing into a clone does not load its `.claude/hooks/`. Two scheduled sessions
fail that condition today — the Conductor (its own workspace) and the **multi-repo cloud worker**,
which clones five FFC repos side by side and is rooted at their _parent_, where there is no
`.claude` at all. For those sessions every "the hook has this covered" sentence in this file is
prose, and prose you have to actually follow.

Establish which you are, in one command. **Run it bare — do not pass `--workspace "$PWD"`:**

```bash
python3 scripts/verify-conductor-hooks.py
# HOOKS: wired        -> the rules below are enforced
# HOOKS: NOT WIRED    -> they are advice; docs/runbooks/conductor-hook-wiring.md has the remedy
# HOOKS: UNVERIFIED   -> it had to guess; the message names the root -- re-run with --workspace <that>
```

Bare is not the lazy form here, it is the **safe** one: with no flag the script uses
`$CLAUDE_PROJECT_DIR` when the session exports it (a statement of the real root) and otherwise
refuses to certify a workspace it had to guess. `--workspace "$PWD"` looks more rigorous and is
strictly worse — it launders the guess into a statement, and from inside a clone that ships
`.claude/hooks/` it reports **`HOOKS: wired`, exit 0** for a session that loads nothing.

That is not hypothetical: this section shipped with `--workspace "${CLAUDE_PROJECT_DIR:-$PWD}"` for
one commit, and in a five-repo worker session — where `$CLAUDE_PROJECT_DIR` is unset, so the
fallback takes over — the command it told every agent to run returned exactly that false green. Pass
`--workspace` only when you **know** the session's project root; never as a stand-in for knowing.

**A worker that comes back NOT WIRED can wire itself, and the fix takes effect immediately — this is
measured, not assumed.** Rendering the tracked template into the session root:

```bash
python3 scripts/verify-conductor-hooks.py --render \
  --workspace /home/user --hub-clone /home/user/FFC-Cloudflare-Automation
```

On 2026-09-08 the very next Bash call in that same session — an `ls … | tail; echo "RC=$?"` — was
**refused by `PreToolUse:Bash`**, naming L50, and a `git status --porcelain` in the call after it
ran normally. So settings are picked up mid-session rather than only at startup, and a run does not
have to finish unguarded just because it started that way. Do this at run start, before any real
work. See `docs/runbooks/conductor-hook-wiring.md`. Ledger **L218**, corrected by **L261**.

## Onboarding a charity (start here for the full chain)

If the task is to **onboard / provision / "set up the repo for" a charity or domain** — or you just
need to know which workflow does which onboarding step — use the **`charity-onboarding` skill**
(`.claude/skills/charity-onboarding/SKILL.md`). It is the ordered map (Phase 0 find-the-application
→ domain → DNS/M365 → website repo → rebrand → analytics → WHMCS → support), names the exact
workflows and gates, and lists the gotchas that have burned prior sessions (identify by domain not
masked name; string-only dispatch inputs; merge-to-`main` before dispatch). The narrative runbook it
indexes is `docs/charity-onboarding-lifecycle.md`.

If the task is to **migrate an existing WordPress/legacy site to GitHub Pages** ("migrate <site>",
"capture <site>", "static conversion", "move off HostPapa/Hostinger") — use the
**`wordpress-to-pages-migration` skill** (`.claude/skills/wordpress-to-pages-migration/SKILL.md`):
capture + asset localization, the `FFC-EX-<domain>` scaffold, footer standard, Pages on the default
URL, and the workflow-121 DNS-ready verdict (epic #702).

## Picking a workflow

1. **Read the catalog first**: `docs/workflow-catalog.json` (machine-readable) or the generated
   section of `.github/workflows/README.md`. Public rendering: <https://ffcadmin.org/automation/>.
2. **The number tells you the target system** — 3-digit, category-first: `1xx` Cloudflare/DNS/Domain
   · `2xx` WHMCS · `3xx` Microsoft (FFC tenant — internal) · `4xx` Zeffy · `5xx` Google · `6xx`
   WPMUDEV · `7xx` GitHub (website + repo) · `8xx` Candid (GuideStar) · `9xx` reserved.
3. **Names**: `NNN. Target - Description [TAG]`; the `[TAG]` lists every API the workflow **calls**
   (`+`-joined). "Calls" means the API actually invoked — not the service the records are _for_
   (M365 DNS written via Cloudflare = `[CF]`) and never plumbing (KV auth, posting an issue
   comment).
4. **Prefer Reads before Writes.** Check the safety level in the catalog /
   `docs/workflow-safety-and-approvals.md` before dispatching anything.

## Safety model (summary — full doc: `docs/workflow-safety-and-approvals.md`)

1. Read vs write credential scopes (`read-all-*` vs `wr-all-*` Key Vault secrets).
2. Environment approval gates — write envs (and some read envs like `m365-prod`, `wpmudev-prod`)
   pause at `waiting` for a human reviewer. Read-only WHMCS workflows use the ungated
   `whmcs-prod-read`.
3. `dry_run` defaults to **true** on write workflows; live requires `dry_run=false`.
4. Typed confirmation for the highest-stakes actions (e.g. domain registration).
5. Key Vault is the **single source of truth** for credentials; rotation = new KV version, no GitHub
   change. Never reintroduce a GitHub-secret copy.

## Merging (validated flow — do not bypass)

- `main` requires status checks **Validate Repository** + **Phantom Revert Guard** (strict), and
  merges go through the **merge queue**, which builds a merge group and re-runs those checks.
- **Review threads must be resolved before the queue accepts a PR.** Fix real findings first, then
  resolve via GraphQL: `resolveReviewThread(input:{threadId:…})`.
- **Copilot re-reviews every push and can file fresh threads.** After pushing fixes, re-poll
  `reviewThreads` before promoting or queueing — one resolution pass is not enough (a 2026-07-20 PR
  needed three rounds).
  - **A fetched ref is a SNAPSHOT and the thread API is LIVE — comparing them dates your conclusion
    to the older of the two, and it surfaces as a finding against the AUTHOR.** Reviewing #1141
    (run 133) the Conductor fetched `pull/1141/head` at `aa73211`, worked in a worktree, then
    queried `reviewThreads`. Both Copilot threads read `isResolved=true, isOutdated=true` while the
    worktree still showed the dead parameter and the duplicated `secrets.*` — a clean,
    well-evidenced reading of _threads resolved without being addressed_. It was wrong: `cdc27aa`
    had landed in the interval and fixed both. The author is an agent actively pushing, so heads
    move in minutes. What makes this worth a bullet rather than a footnote is the **direction of the
    error** — every other stale-read rule here costs a retry, and this one costs an accusation,
    which a reviewer is unlikely to double-check because it already feels uncharitable to make.
    Re-resolve the head (`git fetch origin <branch>`, compare `rev-parse` against what you fetched)
    before drawing any conclusion from a thread, and read `isOutdated=true` as **"the hunk moved, go
    re-read the current file"** — never as evidence in either direction. Ledger **L216**.
- **Reviewing a guard: reintroduce the defect it claims to catch.** Reading the workflow proves a
  new check is _wired_ (present in the `validate` job, no `continue-on-error`); it proves nothing
  about whether it _detects_. Put the original defect back and watch the guard fail. Do it in a
  throwaway worktree so the author's branch is never mutated:
  `git worktree add "$(mktemp -d)" --detach origin/<branch>`, break the thing, run the checker,
  expect a non-zero exit naming the real call site. Let `mktemp -d` pick the path rather than
  hard-coding one: a fixed `/tmp/wt` collides when the directory already exists or two reviewers run
  concurrently, and in the Windows git-bash environment `/tmp` resolves to a _different_ directory
  for bash than for a Windows `python3`, so a worktree bash created there is `FileNotFoundError` to
  the checker you then run against it. On #933 (the #930 command-resolution guard) deleting
  `Remove-Html` from `scripts/whmcs-api-common.ps1` reproduced the exact #929 finding at
  `scripts/whmcs-application-search.ps1:128`. Also probe the fail-closed claims the same way — a
  corrupt input file and a missing tool should each exit 1, not skip. A guard that cannot be shown
  to fail is decoration.
  - **Prove the plant landed before you believe the result.** A reintroduction that silently does
    not apply produces a green run, which reads as "the guard has a hole" — the technique's own
    false negative, and it points the wrong way. On #965 (run 62) a `str.replace(..., 1)` renamed a
    path in the file's **prose** instead of the ledger row intended; the guard stayed green and the
    first reading was that its path-existence check did not work. It did — replacing all 5
    occurrences fired it on all 4 real rows. Assert the mutation: count the occurrences you meant to
    change and fail loudly if the count is not what you expected, or diff the file, before drawing
    any conclusion from a green run. Same for neutering a rule to mutation-test it.
  - **And a landed plant can still be the wrong experiment: parse the mutant before you believe its
    exit code.** The bullet above covers the mutation that does not apply. The opposite failure is a
    mutation that applies and leaves behind something that is no longer a program — and it fails in
    the _flattering_ direction, because a guard that cannot start exits non-zero exactly like a
    guard that caught you. Reviewing #1132 (run 129), re-adding a workflow to
    `check-workflow-input-interpolation.py`'s `KNOWN_UNGUARDED` put a set element into a dict
    literal; the guard exited 1 with a `SyntaxError` and scored as a clean detection, and every
    later mutation of that file would have scored the same way. Compile or parse the mutated
    artifact first — `py_compile.compile(path, doraise=True)`, `node --check`, `yaml.safe_load` —
    and refuse to count the result unless it parses. Two corollaries: **match on the guard's own
    finding text**, never on "any output line mentioning the thing I broke" (a traceback mentions it
    too, which is exactly how the false positive read as real); and **check the sibling mutations
    disagree** — a set of mutations that all fire is weaker evidence than a set where each fires a
    different, correctly-named subset. Ledger **L203**.
  - **A verified mutation is not a verified experiment: assert the CONTROL immediately before each
    mutation, and restore with an explicit source.** The two bullets above check the plant and check
    the mutant. Both can pass while the experiment measures the wrong tree, and it fails toward "the
    guard is weak". Reviewing #1137 (run 131), mutation M2 was applied with
    `git checkout origin/main -- <workflow>` — which writes the **index** as well as the worktree —
    and the restore before M1 was the reflexive `git checkout -- <path>`, whose source is that
    index. M1 therefore ran against the original consistent state, the guard exited **0** correctly,
    and the first reading was that the stale-entry half did not fire. The mutation script had
    asserted its anchor, asserted its insertion count and `py_compile`d the result: the plant was
    perfect and the control was wrong. So run the guard on the untouched tree first and require the
    exit code you expect, then mutate:

    ```bash
    git restore --source=HEAD --staged --worktree -- <paths>   # explicit source, not the index
    python3 <the-guard>; echo "baseline=$?"                    # MUST be 0 before you believe a 1
    ```

    Note what this is not: **L182** is the opposite direction, a restore that is too aggressive and
    reverts to `HEAD` over uncommitted work under test. Here the restore went somewhere else
    entirely because an earlier command silently redefined where it reads from — and `git status` is
    clean afterwards in both cases, so neither is visible by inspection. Ledger **L209**.

  - **A relational defect needs a post-condition on the mutated text, not just a landed
    substitution.** The three bullets above check the plant, the mutant and the control. All three
    can pass while the mutated tree holds **no defect at all**, whenever the thing under test is a
    _relation_ between two pieces of code — ordering, precedence, scope, nesting — rather than the
    presence of one. Reviewing #1148 (run 136), `801`'s emptiness guard was moved from above its
    call to immediately before the `& pwsh …` line to test an ordering assertion; anchor asserted,
    substitution verified, control green, `rc=0`, nothing failed. `801` spells the entire call on
    **one** line (`… -Ein $env:INPUT_EIN -OutputFile $out`), so "before the call line" is still
    _above_ the parameter use. Moving the guard **below** that line, the ordering test fires alone,
    as its author documented. Assert the relation you meant to break —
    `guard_index > first_use_index` — and refuse to read the exit code until it holds. Note the
    direction this fails in: an uncaught mutation reads as "the tests are weaker than claimed",
    which is a finding against the author. Ledger **L221**.
  - **And the control has to have FINISHED. A partial run compared against a complete one blames the
    change under test.** The bullet above is a control measuring the wrong tree; this is the same
    failure one level out. Reviewing #1139 (run 132) the full suite was started on the composed tree
    and on clean `origin/main` as two background tasks, and both output files were read while both
    were still being appended to: **924 PASS / 83 FAIL** against **107 PASS / 0 FAIL**, and the set
    difference attributed **~80 failures to the PR**. Complete, they read **1086 / 83** and **1076 /
    83** with a byte-identical list of 16 failing modules — the PR added its own ten tests and broke
    nothing. Nothing about the early read looks partial: both files are well-formed, every line is a
    real result, and `grep -c` answers instantly and truthfully about the bytes written so far. The
    tell is in the **pair**, not in either file — a control whose PASS count is an order of
    magnitude below the treatment has not finished. `run_all.py` ends by printing a terminal summary
    line naming the failing modules, so require that line in **both** files before comparing:

    ```bash
    # run_all.py:215,217 — exactly one of these is the last line of a finished run.
    for f in pr.txt base.txt; do
      grep -qE '^(::error::workflow-logic tests failed:|All [0-9]+ workflow-logic test modules passed\.)' "$f" \
        || { echo "$f INCOMPLETE — do not compare"; exit 1; }
    done
    ```

    Quote the strings from `run_all.py` rather than from memory: the first draft of this recipe
    guessed `^workflow-logic tests passed`, which the script never prints, so the green half of the
    check could only ever have failed closed. It also used `\|` — a GNU basic-regex extension —
    where `grep -E` is what makes an alternation portable. Copilot caught the second on #1140; the
    first was found by reading the source it claims to match, which is the habit that would have
    caught both.

    Do not settle it by watching the line counts stop growing — a slow module is indistinguishable
    from a finished run for as long as you are willing to wait. Ledger **L211**.

- **If the thing under review is read-only, also run it live.** Mutation-proving establishes that
  the tests discriminate; it cannot establish that the code behaves against real data, because every
  test injects its own fixtures and its own clock. For a script that only reads — the board audit,
  the catalog generator, the status-feed generator — a live run against production is free (no gate,
  no write) and routinely produces the strongest evidence in the review. On #1012 every mutation the
  reviewer applied was caught, and the finding that actually settled the review was the live run:
  the first item the new grace window deferred in production was **#1012 itself**, uncarded and 43
  minutes old, in the same invocation that still exited 1 on a genuine finding. That demonstrates
  the tolerate-latency and still-catch-drift halves simultaneously, on real timestamps, which no
  unit test in the PR could do. Check the script's auth contract first (most take `GH_TOKEN` and
  nothing else) and confirm it takes no write path before running it.
- **Supersession check before ready+queue.** Before promoting a PR, grep `main` for the
  function/capability names the PR adds — a same-purpose implementation may have landed on `main`
  after the PR branched (on 2026-07-20, #772's basePath probe duplicated `basePathMismatch` merged
  40 minutes earlier in #773; only the merge conflict stopped a double-ship). The claim-sync
  workflow (737) labels linked issues from `Refs #N` as well as `Closes #N`, but the `claimed` label
  only tells you a PR exists — it says nothing about what has already landed on `main` — so the grep
  is still the check.
- **A green check has a timestamp, and so does the tree it was green about — compose against the
  base you will actually merge into.** `pull_request` checks describe the merge of the head into the
  base **as the base then stood**, and nothing re-fires them when `main` advances (this repo does
  not require branches to be up to date; the merge queue is the mechanism instead). So a PR can read
  `CLEAN` with every required check `SUCCESS`, honestly, and still be red in the merge group. On
  #1117 the required checks ran at 13:26:25Z; #1121 landed the `reserved-ids` block ten minutes
  later at 13:36:41Z; #1117 carries the row that block reserves, and the ledger guard fails on
  exactly that pair. Three hours on, nothing on the PR showed it. Compose and run the guards:

  ```bash
  WT=$(mktemp -d); git worktree add -q "$WT" origin/main && cd "$WT"
  git merge --no-edit <pr-head-sha>          # a CONFLICT is not this check's finding — that is DIRTY
  python3 tests/workflow-logic/test_lessons_ledger.py; echo "exit=$?"
  ```

  And note the half that is easy to miss: **doing this correctly does not keep it correct.** Run 123
  composed #1117 properly — against #1120 — and then invalidated its own result by merging #1121 ten
  minutes later. Re-compose after anything lands, including your own PR. Ledger **L191**; the
  mechanical form is filed as #1123.

  **And composing against `main` is not enough when the other PR has not landed yet.** The recipe
  above finds the collision once the sibling is in `main`; while both are open it reports green for
  both, because mergeability and every required check are computed against the base alone. Measured
  2026-08-23: #1214 declares `L248 #1209` / `L249 #1209` / `L250 #1212` in the `reserved-ids` block
  and #1212 lands those three as rows; each composes green against `main`, git merges the pair
  cleanly (different lines), and `test_lessons_ledger.py` fails on the union in **either** order.
  Whichever reaches the queue second is the one that fails, with a message about a diff it does not
  contain. When your diff touches a `reserved-ids` entry, compose against the head of the PR the
  entry names — it is written down for you — and prune the matching lines if you are second. Ledger
  **L191**; the reservation-specific form is in the block's own preamble in
  `docs/lessons-ledger.md`.

- **Re-check the PR is still open before pushing to its branch.** Merging main into an agent branch
  whose PR merged moments ago silently **re-creates the auto-deleted branch** — the tell is
  `[new branch]` in push output for a push you meant as an update. If you see it, delete the
  resurrected branch and stop.
- **Fetch refs individually.** `git fetch origin main <agent-branch>` aborts the **entire** fetch
  with "couldn't find remote ref" if the second ref was never pushed — leaving `origin/main` stale,
  so a clean branch falsely appears N commits ahead of main (seen 2026-07-20 on the #748 worker
  run). Fetch `main` on its own before comparing against it.
- Enter the queue with `gh pr merge <n> --auto` — no strategy flag: the queue sets it, and passing
  `--merge` or `--squash` prints "The merge strategy for main is set by the merge queue" (confirmed
  on hub + ffcadmin, 2026-07-20).
  - **That message is not a rejection, and this line used to say it was.** The flag is ignored; the
    enqueue happens anyway. On #1128 (run 127) `gh pr merge 1128 --squash --auto` printed that one
    line and nothing else, and the very next `gh pr merge 1128 --auto` answered
    `Pull request … is already queued to merge` with no command between them. `CLAUDE.md`'s queue
    section has always described the same string as an advisory on a **successful** `--auto`, so the
    two documents contradicted each other on identical bytes. Read the message as neither verdict —
    it says only which flag was discarded. Reading it as a refusal leaves a verified PR sitting
    un-enqueued, or sends a second merge command at a PR already in the queue. Ledger **L198**.

  `.auto_merge != null` confirms the enqueue took, but null does NOT prove a dequeue (it can read
  null while queued — see below); the authoritative probe is the `enqueuePullRequest` mutation
  ("already in the queue"). Or enqueue directly:
  `gh api graphql -f query='mutation{enqueuePullRequest(input:{pullRequestId:"<node_id>"}){mergeQueueEntry{position state}}}'`
  - **Read the probe's answers apart, because some of them are failures wearing the wrong clothes —
    and do not treat the list below as closed.**
    `UNPROCESSABLE: "Pull request is already in the queue"` means queued — the answer you are
    usually after. A populated `mergeQueueEntry` means you just enqueued it. But
    `NOT_FOUND: "Could not resolve to a node with the global id"` means **your node id is wrong**,
    and it is indistinguishable at a glance from the legitimate reading "this PR no longer exists" —
    the shape that would make a run conclude a queued PR had vanished. Derive the id in the same
    command rather than pasting a literal: `PRID=$(gh pr view <n> --json id --jq .id)`. Hit on run
    75; cost was small only because the PR was known-good at the time.
    - And `UNPROCESSABLE: "Pull request is closed"` means it **merged** — the best outcome the probe
      can report, delivered as a bare `gh` error on stderr with a non-zero exit. Seen on ffcadmin
      #870 (run 128), where `gh pr merge --auto` on an already-green PR merged it outright and
      printed nothing, so the probe was the first thing that said so. Settle it on
      `gh pr view <n> --json state,mergedAt`, not on the error text.
    - And `UNPROCESSABLE: "Pull request Required status check \"<name>\" is in progress."` is the
      probe doing its documented job — it names the specific blocker, which is the whole reason the
      Debugging tip below prefers it over `gh pr merge --auto`. Wait for that check; do not re-fire
      the mutation in a loop.
    - **Anything not in this list is unclassified, not a fault.** This enumeration is a claim about
      the API at the time it was written and it decays like any other measurement; a reading you
      cannot place here means go and look at `state`/`mergedAt`, not that something broke. The list
      grew by two while the PR that added this paragraph was open — the closed-PR answer came from
      ffcadmin #870 and the in-progress-check answer from that PR's own enqueue attempt minutes
      later. Ledger **L201**.

- **Debugging tip:** `gh pr merge --auto` can mask the real blocker behind a GraphQL "rate limit"
  error. The `enqueuePullRequest` mutation returns the true reason (unresolved conversation, CodeQL
  still running, …).
- **Once a PR is in the queue, a branch-level check failure does NOT dequeue it — leave the branch
  alone.** Promoting a draft re-runs branch CI, and Phantom Revert Guard can fail there (branch
  behind `main`) while `.auto_merge` reads null — which looks like a bounce but is not: the merge
  group re-runs the required checks against `main` and merges anyway (seen on #797, 2026-07-21).
  Queued branches also reject pushes ("protected branch hook declined") and the update-branch API
  returns 422 "dequeue the associated pull request". Before syncing any "behind" branch, probe queue
  membership first (that 422, or `enqueuePullRequest` saying "already in the queue"); only merge
  `main` into a branch that is genuinely out of the queue.
- Never merge with `--admin`; never push to `main`.
- **Safety-table conflicts are normal, not a red flag.** Prettier reflows every row of
  `docs/workflow-safety-and-approvals.md` when a new cell widens a column, so two PRs that each "add
  one row" conflict across the whole table. Resolve by taking `main`'s table, re-inserting your row
  after its numeric neighbor, then `npx prettier --write` the file and re-run
  `python3 scripts/check-workflow-doc-consistency.py` + the catalog generator to confirm no drift.

## Adding or changing a workflow

1. Pick the next free number in the right category; file name `NNN-<slug>.yml`; display name
   `NNN. Target - Description [TAG]`.
2. Add a row to `docs/workflow-safety-and-approvals.md` (CI enforces coverage).
3. Regenerate the catalog: `python3 scripts/generate-workflow-catalog.py` (CI fails on drift).
4. Credentials via a `*-secrets-from-kv` composite action; jobs set `permissions: id-token: write`
   and an `environment:`.
5. Write workflows: `dry_run` input defaulting to `true`, a `concurrency` group
   (`cancel-in-progress: false`), and an approval-gated environment.
6. **Embedded logic gets a unit test.** If the workflow contains decision logic (a `github-script`
   block, non-trivial bash, pwsh parsing), add a scenario under `tests/workflow-logic/` — the
   harness extracts the real script from the YAML and runs it against fixtures (fake `gh`, mocked
   `core`/`context`). CI runs `tests/workflow-logic/run_all.py` on every PR; see that dir's README.
7. **Editing an already-tested step? Update its fixture in the same PR.** The workflow-logic harness
   extracts the _live_ script from the YAML, so changing a step's bash (new file copied, new env
   var, new `gh` subcommand) breaks that module's fixtures — and it surfaces only in the merge
   group, after review. Before editing a workflow, grep `tests/workflow-logic/` for its file name;
   if a module extracts the step you're touching, extend its fixture seeding/shim in the same PR
   (e.g. #732 added a `cp ../agentic-os-status.json …` to the 502 deliver step that
   `test_502_deliver.py`'s work-dir fixture didn't seed).

## Work claiming (avoid stepping on other agents)

Multiple actors (scheduled conductor runs, live sessions, Copilot agents, humans) share this backlog
and all authenticate as the same user. Before starting ANY issue:

1. **Available = `is:open -label:claimed`.** The pickup query is
   `org:FreeForCharity label:agentic-os is:open -label:claimed`. If an issue has the `claimed` label
   or an open linked PR, it is TAKEN — pick something else.
   - **Prefer `agent-ready`.**
     `org:FreeForCharity label:agent-ready is:open -label:claimed -label:blocked` is the same query
     narrowed to issues that are _actually pickable_: unclaimed, unblocked, one-PR-scoped and
     carrying acceptance criteria. `agentic-os` is the programme-wide **topic** label and stays on
     everything, so it also counts epics, machine-managed rolling issues (740/738 open and close
     those themselves), items blocked on a human with credentials, and durable findings kept as
     records — none of which an agent can execute. Counting the topic label is why the Conductor's
     "keep 5–15 open" band read 46 and drifted upward for eight consecutive runs of trimming that
     could never converge (#922). Add `agent-ready` when you file an issue that meets the bar.
     - **`-label:blocked` is load-bearing, and it is the half this line used to omit.** The sentence
       that ended _"remove it when the issue becomes blocked"_ made `agent-ready` and `blocked`
       mutually exclusive by convention only, and convention is not a filter. Groomers have
       deliberately kept both labels on an issue precisely so it **returns to the pool by itself**
       the moment `blocked` comes off — #1028 says so in as many words ("Both labels stay on, so it
       returns to the ready pool the moment the premise is settled"), which is the better design and
       was silently unsupported: the query as written had no `blocked` term, so #1028 sat in the
       pickup results for three days as a first-class candidate while being explicitly blocked
       pending a controlled experiment. Measured on run 130 against `main` `8774a1a`, over REST
       rather than the lagging search index: the documented query returned **42** issues and #1028
       was one of them; adding `-label:blocked` returns 41 and removes exactly that one. Picking it
       up would have been worse than wasted effort — its AC1 tells the implementer to write a
       diagnosis into `guard_bash.py` that #1028's own later comments show mis-explains the cases,
       and hook PRs are the class Clarke reviews by hand. **Two labels that contradict each other
       are a bug in the query, not in the labelling** — the grooming convention was right and the
       query was wrong, so fix the query and let the labels mean what groomers already use them to
       mean.
   - **An issue that states why it is blocked is making a claim about a tree that has since moved —
     re-run its probe before inheriting it.** A blocker is prose: it does not re-execute, no check
     fails when it stops being true, and nothing links it to the PRs that quietly fix it. #1042
     parked itself behind a `guard_bash.py` false positive it named verbatim; #1062, #1018 and #1115
     then landed on that file for unrelated reasons and none referenced #1042. Six days later,
     feeding seven real routine commands to the current guard returned **7 of 7 allow** — the
     blocker had been gone for at least a day and the issue still read as blocked. Nothing about a
     stale blocker looks stale, and a precisely-written one reads as more authoritative, not less.
     When grooming, spend the one command it takes to re-measure. Ledger **L197**.
   - **`-label:claimed` currently under-reports: check for a cross-repo PR before you start.** The
     backlog lives in the hub while much of the code lives in a template or site repo, so the normal
     shape is a hub issue implemented by a PR in another repository — and 737 neither runs in those
     repositories nor matches the qualified reference form they use. On 2026-07-30 three
     `priority: high` hub issues (#934, #893, #880) sat in the pickup query with finished PRs
     against them. Until #939 lands, search open PRs **org-wide** for the issue number before
     claiming: `gh api -X GET search/issues -f q='org:FreeForCharity is:pr is:open <N>'`.
   - **A grep for `refs #N` is not a check for "does any PR reference this issue".** The qualified
     cross-repo form — `Refs FreeForCharity/FFC-Cloudflare-Automation#934` — has the `owner/repo`
     between the keyword and the `#`, so a pattern anchored on `keyword` + `#` matches nothing and
     reports a _clean_ result. Match `(closes|fixes|refs)[: ]+(owner/repo)?#N`. This is the same
     blind spot as `claim-sync-lib.js`'s `LINK_RE`, and it fooled a conductor run before it was
     found in the code (#939).
2. **Claim before working**: add the `claimed` label AND post one comment
   `CLAIM: <actor> <planned-branch> <UTC timestamp>` where `<actor>` identifies you
   (`conductor-run-N`, `live-session`, `copilot-agent`, or a human name — the shared login does not
   identify you). Opening a PR that says `Closes #N` is also a claim (automation will sync the label
   from linked PRs once the claim-sync workflow lands).
   - **`Refs`/`Closes` CLAIM. To merely cite an issue, use a full link.** The two readings of
     `Refs #N` — "this PR does part of that issue" and "that issue is where the related work lives"
     — are indistinguishable to 737, which claims on both. A citation written as `Refs #N` therefore
     removes a live pickup from the query for as long as the PR stays open, silently and invisibly
     to its author: a docs draft citing #945 hid the run's designated top pickup for six hours
     (#948). So **cite with `https://github.com/FreeForCharity/<repo>/issues/N`** (or a bare
     `<repo>#N` with no keyword) and keep `Refs`/`Closes` for work you are actually taking. 737
     comments on the PR naming every issue it claimed, so a mistake is visible immediately — rewrite
     the reference as a link and remove the `claimed` label.
   - **From another repo, qualify the reference.** The backlog is here; the code usually is not. A
     bare `#N` in a template or `FFC-EX-*` PR means _that_ repo's issue #N, so write the hub issue
     out in full — `Refs FreeForCharity/FFC-Cloudflare-Automation#N`. That qualified form is what
     737's daily sweep reads to claim the hub issue on your behalf; a bare number claims nothing
     here and leaves the issue in the pickup query for someone else to duplicate (#939).
3. **Release on stop**: if you abandon the work, remove the label and comment. Claims with no open
   linked PR and no activity for 48h are considered expired and may be swept. **A draft PR holds a
   claim as hard as a ready one** — 737's daily sweep reports (never releases) every issue whose
   only claimant is a draft older than 48h, so if a draft of yours shows up there, promote it or
   close it rather than leaving the pickup suppressed.
   - **Multi-repo / multi-part issues: claim your portion, not the issue.** Post the
     `CLAIM: <actor> …` comment scoped to the part you are taking (name the repo/portion) and do
     **not** add the exclusive `claimed` label — the remainder must stay visible in the pickup
     query. When you finish, comment what you shipped and what remains (pattern validated on #748,
     2026-07-20). Caveat: the claim-sync workflow (737) will still add the exclusive label while
     your `Refs #N` PR is open (it parses `Refs` too, seen on #806 → epic #752, 2026-07-22) and
     auto-releases it only once the **last** open linked PR merges or closes (it checks all open PR
     bodies before removing) — during an open scoped PR, treat the label as advisory and the
     scoped-claim comment as the source of truth for what portion is taken.
4. **Fleet-wide file changes** (any file synced across the FFC-EX fleet, e.g.
   `post-deploy-smoke.yml`): claim the hub tracking issue FIRST — every fleet sync must have one —
   and before editing, check the target file's last commit in 2-3 fleet repos; a commit within the
   last hour means a rollout may be in flight (two sessions racing the same fleet fix produced
   conflicting variants on 2026-07-19).

## GitHub API rate budget (shared — be frugal)

Every agent session, scheduled task, and PAT-based workflow authenticates as the same user and
shares **one REST core budget (5,000 requests/hr) and one GraphQL points budget (typically 5,000
points/hr — cost varies per query, so heavy queries drain it faster than a request count
suggests)**, with separate reset anchors. Concurrent sessions polling with GraphQL-backed commands
have exhausted the points budget for hours.

- **Poll with REST only**: `gh api repos/OWNER/REPO/pulls/N`, `.../commits/SHA/check-runs`,
  `.../actions/runs/ID`. The `gh pr ...` / `gh issue ...` verbs are **GraphQL** — never put them in
  a loop.
- One consolidated watcher per concern, interval ≥ 60s, bounded iterations.
- GraphQL is for the few mutations that need it (`enqueuePullRequest`, `resolveReviewThread`) —
  single-shot; on `RATE_LIMIT`, read `gh api rate_limit` and wait for the reset instead of retrying.
- Create/close issues and comments via REST (`gh api .../issues --method POST`).
- **Reading the _newest_ comments on a long issue needs `--paginate`.** The comments endpoint
  returns at most 100 per page **oldest-first**, so on a 100+-comment issue (e.g. the Conductor Log
  #719) an unpaginated `gh api .../comments --jq '.[-3:]'` slices the tail of page **one** — the
  _earliest_ comments — not the latest. Use `gh api --paginate .../comments --jq '.[] | ...' | tail`
  (or request the last page explicitly). This silently breaks "the newest `START` comment is the
  source of truth for the run number" — a conductor run misread its own run number this way
  (2026-07-24).
  - **The `--jq` above must be a STREAMING filter, and that is load-bearing, not stylistic.**
    `--paginate` runs the expression **once per page** and concatenates the outputs. A streaming
    filter (`.[] | …`) emits one line per item, so the pages concatenate cleanly — which is why the
    form taught here is correct. An **array-building** filter (`--jq '[.[] | …]'`) emits one array
    **per page**: `[…][…][…]`, which is not valid JSON. `gh` exits 0 and says nothing, and the
    failure lands much later as a parse error at a byte offset in the middle of page 2, nowhere near
    the command that caused it (run 70, ~10 minutes and two wrong hypotheses). The fix is not the
    obvious one — `--slurp` is the right flag but **cannot be combined with `--jq`** — so fetch raw
    and flatten afterwards:

    ```bash
    gh api --paginate --slurp repos/OWNER/REPO/issues/719/comments > pages.json
    # pages.json is an array OF PAGES — flatten it before use
    ```

    `guard_bash.py` warns about the array-building shape (#989). It warns rather than blocks because
    reducing each page to a scalar and recombining downstream is legitimate — see
    `726-repo-rulesets-drift-audit.yml:205`, which does exactly that on purpose.

  - **Once the pagination is right, the FILTER is the next thing that silently dates the answer —
    and a long log has more than one format in it.** Run 133 derived its own run number from #719
    with `--jq 'select(.body|test("RUN [0-9]+ START"))'` and got **Run 86** against an actual 132.
    Every run since ~87 writes `## Run N — START` with an **em dash**, so the pattern matched only
    the pre-87 era it was written for. Nothing errored, and this is the failure mode to internalise:
    `--paginate` had correctly returned all 451 comments and the filter honestly reported the newest
    thing it could see, so the two bugs compose into one well-formed, in-range, 46-runs-stale
    number. **A wrong answer here is worse than an empty one** — an empty result gets investigated,
    and this one only surfaced because `state/CONDUCTOR.md` disagreed. The value cannot tell you it
    is stale, so assert something it cannot fake: that the newest match is **recent** (within a run
    interval of now), or that the match **count** is a sane fraction of the total the endpoint
    returned. Applies to any extractor over an append-only log — #719, a changelog, a CI history.
    Ledger **L215**.

- **An unpaginated list read cannot support an ABSENCE claim.** `per_page=100` is the maximum, not a
  guarantee, and the **default is 30**. A truncated list is indistinguishable from a complete one,
  so "X is missing" / "nothing is pending" / "zero failures" drawn from one may simply be false —
  run 64 nearly reported two incident-tracked workflows as deleted, and run 73 hit the same thing at
  the 30 default. Add `--paginate`, or compare `.total_count` against what you actually received.
  `guard_bash.py` warns (#971); a bounded single-page read is fine when you want the newest N and
  are claiming nothing about the rest.

- **`gh api graphql --paginate` only advances a variable named exactly `$endCursor`.** `gh`
  substitutes the next page cursor into that name and no other, so a query declaring `$cursor` (or
  anything else) keeps `after:` null and **re-fetches page 1 until the budget stops it**. It fails
  silently and looks like success: a large file of well-formed, entirely duplicate rows. **The rate
  limiter is the only thing that ends it.** On 2026-07-30 this ran for ~6.5 hours in a backgrounded
  task, wrote **2,454,201 rows / 98 MB** — 24,542 re-fetches of page 1 — that `sort -u` collapsed to
  **107**, and **drained the shared 5,000-point GraphQL budget to zero**, starving every other agent
  session on the account until the hourly reset. (The 107-rather-than-100 is itself the proof: page
  1's _contents_ drifted as board items were added during those hours, while the page index never
  moved.) Two tells, and **the second one matters more**: a line count that is a clean multiple of
  100 with a `sort -u` that collapses it — and, if you background the command, a `gh api rate_limit`
  that keeps falling after you believe it finished. Do not conclude a backgrounded `gh` loop has
  stopped from a process listing; the 2026-07-30 run checked `ps` and saw no `gh`, then sampled the
  file at 18,000 rows and reported that figure — understating the real damage **136-fold**. The fix
  is
  `query($endCursor:String){ … items(first:100, after:$endCursor){ pageInfo{hasNextPage endCursor} … } }`.
  `guard_bash.py` now blocks the wrong-name form outright, since such a command can never return
  page 2. Plain **REST** `--paginate` takes no variables and is unaffected.

## Dispatch / watch / approve recipes

```bash
gh workflow run <file>.yml --ref main -f key=value          # dispatch
gh run view <id> --log                                       # read results
gh api repos/FreeForCharity/FFC-Cloudflare-Automation/actions/runs/<id>/pending_deployments \
  --jq '.[] | {env: .environment.name, id: .environment.id, can: .current_user_can_approve}'
gh api -X POST repos/FreeForCharity/FFC-Cloudflare-Automation/actions/runs/<id>/pending_deployments \
  -F "environment_ids[]=<env_id>" -f state=approved -f comment="approved"   # approve a gate
```

Poll runs in a background task with an `until` loop — never foreground-sleep.

**Reviewing a pending gate: resolve the run's own `head_sha` first (L155).** A run executes the code
at the SHA it was created from, _not_ the `main` you just fetched, and the gap is widest for exactly
the runs that sit at a gate — a scheduled run parked for days is reviewing a tree from days ago. Any
statement of the form "this run will/won't do X because the code does/doesn't contain Y" is a claim
about a specific tree, so name that tree before making it:

```bash
sha=$(gh api repos/FreeForCharity/FFC-Cloudflare-Automation/actions/runs/<id> --jq .head_sha)
git fetch origin "$sha" && git show "$sha":<path>          # read the code the run will run
git diff "$sha" origin/main -- <path>                      # empty => your working copy was safe
```

Read the file at that SHA, or diff just the paths your argument depends on — a large `rev-list`
distance does not by itself invalidate an analysis, and a small one does not make it safe. Run 106
reported that #1064's functions "do not exist" from a `main` fetched 36 seconds before they landed;
run 107 reviewed the 703 gate whose run is **123 commits** behind `main` and confirmed with one
`git diff` that `703-sites-list-generate.yml` is byte-identical across all 123 — same check,
opposite answer, and only the check tells you which case you are in.

**A held gate also stops the schedule behind it, and `status=waiting` will not show you that
(L212).** A run parked at a gate holds its `concurrency` slot for as long as it waits, so the next
scheduled run is admitted to the group but gets **no job at all** until the older one is reaped.
Measured on 703 (`group: sites-list-generate`, `cancel-in-progress: false`), whose gate has now been
held for 68 consecutive runs: the 2026-08-03 run object was created `09:12:25Z`, and its `generate`
job carries `created_at == started_at == 2026-08-04T07:25:50Z` — the same second the 2026-07-27 run
was cancelled by 734. It neither missed its schedule nor reached the gate; it sat jobless for 22
hours. The list of waiting runs shows exactly one entry throughout and looks orderly, so ask the
**job**, not the run:

```bash
run=<id>
gh api "repos/FreeForCharity/FFC-Cloudflare-Automation/actions/runs/$run" --jq '"run  created \(.created_at)"'
gh api "repos/FreeForCharity/FFC-Cloudflare-Automation/actions/runs/$run/jobs" \
  --jq '.jobs[] | "job  created \(.created_at)  started \(.started_at)  \(.name) [\(.status)]"'
# run  created 2026-08-03T09:12:25Z
# job  created 2026-08-04T07:25:50Z  started 2026-08-04T07:25:50Z  generate [waiting]
```

A job `created_at` later than its run's is the signature. Report the **outcome** a long hold is
costing, not the hold: 703 has not regenerated the sites list since 2026-07-25, because each week's
run is queued behind the held one and then reaped.

**Triaging a CI anomaly: ask upstream first, then ask _which step_ failed (L159).** The check that
separates "our defect" from "their outage" is cheaper than every check that presupposes ours, so run
it before inspecting concurrency groups, `uses:` refs or the diff:

```bash
# Unauthenticated, no token, costs nothing against the GitHub rate budget.
curl -s https://www.githubstatus.com/api/v2/summary.json |
  grep -oE '"(name|status|description)": *"[^"]*"' | head -40
```

Read it in two parts, because they disagree and the disagreement is the point: `components[]` can
say `operational` while an `incident` is still `investigating`/`monitoring`. Neither is proof on its
own — settle it by finding a **real run that took a runner and finished** in the window you care
about. Then:

- **If `Set up job` is the failing step, no repository code ran**, so the break cannot be yours no
  matter how incriminating the diff looks (`Failed to resolve action download info` is the usual
  text).
- **A stalled run is not self-healing.** Runs cancelled with zero steps executed are recorded as
  `failure`, so a required check can go from a self-resolving `queued` to a terminal `cancelled` and
  auto-merge alone will no longer land the PR.
- **Before blaming an identity** (app vs user, signed vs unsigned) for a suppressed event, check
  whether that same identity **succeeded in this repo a few hours earlier**. One call, and it
  usually kills the attribution hypothesis outright.

**"No green checks" is two different states — get the jobs count before choosing a remedy (L160).**

```bash
gh api repos/FreeForCharity/FFC-Cloudflare-Automation/actions/runs/<id>/jobs --jq '.total_count'
```

| signature                                          | mode                  | do this                       |
| -------------------------------------------------- | --------------------- | ----------------------------- |
| **no run object** for the head sha                 | A — event never fired | act: close+reopen, or re-push |
| run exists, `queued`, **`total_count = 0`**        | B — no runner took it | wait; acting costs a head     |
| run exists, `queued`, 0 jobs, **platform healthy** | B, parked             | now close+reopen              |

Close+reopen re-fires `pull_request` checks on the **same sha** with no new commit (`reopened` is in
722's default types and 727's explicit list), which on this behind-count-gated queue is strictly
cheaper than the empty commit (L156).

**Signing off on a PR: quote the sha you checked, and re-read it before acting on someone's sign-off
— including your own (L161).** A review is bound to `headRefOid` and does not travel; a commit
landing between the review and the merge silently voids it while every surface still reads green.

```bash
gh pr view <n> --json headRefOid,mergeStateStatus --jq '"\(.headRefOid[0:7]) \(.mergeStateStatus)"'
```

**Verifying a publish: read the data artifact, never the rendered page (L162).** `mergedAt` says
nothing about what the CDN serves, and a status page that republishes its own log will hand back a
_previous_ run's claim about itself when you grep the HTML.

```bash
curl -s https://ffcadmin.org/data/agentic-os-status.json | grep -oE '"generated_at": *"[^"]*"'
```

**Asking what protects a branch: use the rules endpoint, never `branches/*/protection` (L184).**
Every FFC repo is governed by **rulesets**, and the legacy branch-protection endpoint does not see
them — it returns `404 {"message": "Branch not protected"}` for a branch that in fact requires a
pull request, status checks, code scanning, Copilot review and a merge queue. That failure fails
**open**: the literal reading of the 404 is permission, in the permissive direction, with no error
to notice.

```bash
gh api repos/FreeForCharity/<repo>/rules/branches/main --jq '[.[].type] | unique | join(", ")'
gh api repos/FreeForCharity/<repo>/rulesets --jq '.[] | "\(.name) [\(.enforcement)]"'
```

The first call gives the **effective** rules for that branch in one request; the second names the
rulesets producing them. A concrete tell that you asked the wrong one: `gh pr merge --delete-branch`
refusing with `Cannot use -d or --delete-branch when merge queue enabled` on a branch you had just
described as unprotected.

**Counting orphan branches: subtract the merge queue's own heads first (L209's sibling, L208).** The
usual figure is `remote heads` minus `open PR heads`, and while any PR is in the merge queue that
overstates it: the queue creates a `gh-readonly-queue/main/pr-<n>-<base-sha>` head which has no open
PR **by construction**, so the subtraction counts live queue machinery as an abandoned branch.

```bash
gh api --paginate "repos/FreeForCharity/<repo>/branches?per_page=100" \
  --jq '.[].name' | grep -vE '^main$|^gh-readonly-queue/'
```

The trap is self-inflicted and order-dependent: a run that enqueues a PR and _then_ counts gets one
more than a run that counts first, and neither number looks wrong. Run 131 measured 8 this way
against #986's inventory of 7, and the discrepancy was a queue head for a PR that same run had
enqueued twenty minutes earlier. **Name the difference rather than counting it** — a one-branch gap
against a known inventory is a question about the denominator before it is evidence of drift.

**Find a workflow's runs by FILE NAME, never by matching the run's `.name` (L33).** A run object's
`.name` is the rendered `run-name:`, not the workflow's `name:`. Workflow 228 titles its runs
`WHMCS Fraud Review (FraudLabs Pro)`, so filtering `actions/runs` for a name starting with `228`
returns **zero results while 228 is actively failing on a schedule** — a silent wrong answer, not an
error. Resolve the workflow first, then list its runs:

```bash
id=$(gh api repos/FreeForCharity/FFC-Cloudflare-Automation/actions/workflows/228-whmcs-fraud-review.yml --jq .id)
gh api "repos/FreeForCharity/FFC-Cloudflare-Automation/actions/workflows/$id/runs?branch=main&per_page=10" \
  --jq '.workflow_runs[] | "\(.created_at) \(.event) \(.conclusion)"'
```

Workflow 740 already gets this right (`740-scheduled-workflow-failure-alert.yml:169-170`) — the trap
is in ad-hoc queries, which nothing guards.

## Key docs

| Doc                                                            | What                                                                                                |
| -------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| `docs/workflow-safety-and-approvals.md`                        | per-workflow safety levels, gates, guards                                                           |
| `docs/workflow-catalog.json`                                   | generated machine-readable catalog                                                                  |
| `docs/google-api.md`                                           | Google architecture (KV-backed), GA/GTM models, provisioning record                                 |
| `docs/whmcs-apim-routing.md` / `docs/whmcs-product-catalog.md` | WHMCS credential path + products                                                                    |
| `docs/charity-onboarding-lifecycle.md`                         | end-to-end charity onboarding order                                                                 |
| `docs/lessons-ledger.md`                                       | what previous sessions learned the expensive way — read before fleet, credential or monitoring work |
| `CLAUDE.md`                                                    | Claude-specific environment notes (sandbox constraints, auth quirks)                                |
