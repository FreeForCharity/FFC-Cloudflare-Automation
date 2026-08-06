# AGENTS.md — FFC-Cloudflare-Automation

Canonical onboarding for any AI agent (Claude, Copilot, Jules, …) or new admin working in this
repository. Tool-specific notes live in `CLAUDE.md`; org-wide mission/security rules follow the FFC
global policy (never expose secrets; Conventional Commits; PRs, never direct pushes to `main`).

## What this repo is

The automation hub for Free For Charity infrastructure: ~60 GitHub Actions workflows that drive
Cloudflare (DNS/registrar), WHMCS (billing/support), Microsoft 365, Zeffy, Google (Analytics/GTM),
WPMUDEV, and the FFC GitHub org itself. PowerShell-first scripts in `scripts/`, credentials from
Azure Key Vault via OIDC (never GitHub secrets).

**Before fleet-wide, credential or monitoring work, read `docs/lessons-ledger.md`.** It is the
durable record of findings that cost previous sessions hours — dead triggers, swallowed 403s,
presence mistaken for validity — each with its evidence link and the guard (if any) now holding it.
Add a row there in the same PR as the fix whenever something surprises you.

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
- **Re-check the PR is still open before pushing to its branch.** Merging main into an agent branch
  whose PR merged moments ago silently **re-creates the auto-deleted branch** — the tell is
  `[new branch]` in push output for a push you meant as an update. If you see it, delete the
  resurrected branch and stop.
- **Fetch refs individually.** `git fetch origin main <agent-branch>` aborts the **entire** fetch
  with "couldn't find remote ref" if the second ref was never pushed — leaving `origin/main` stale,
  so a clean branch falsely appears N commits ahead of main (seen 2026-07-20 on the #748 worker
  run). Fetch `main` on its own before comparing against it.
- Enter the queue with `gh pr merge <n> --auto` — no strategy flag: the queue sets it, and passing
  `--merge` is rejected with "The merge strategy for main is set by the merge queue" (confirmed on
  hub + ffcadmin, 2026-07-20). `.auto_merge != null` confirms the enqueue took, but null does NOT
  prove a dequeue (it can read null while queued — see below); the authoritative probe is the
  `enqueuePullRequest` mutation ("already in the queue"). Or enqueue directly:
  `gh api graphql -f query='mutation{enqueuePullRequest(input:{pullRequestId:"<node_id>"}){mergeQueueEntry{position state}}}'`
  - **Read that probe's three answers apart, because one of them is a typo wearing a real answer's
    clothes.** `UNPROCESSABLE: "Pull request is already in the queue"` means queued — the answer you
    are usually after. A populated `mergeQueueEntry` means you just enqueued it. But
    `NOT_FOUND: "Could not resolve to a node with the global id"` means **your node id is wrong**,
    and it is indistinguishable at a glance from the legitimate reading "this PR no longer exists" —
    the shape that would make a run conclude a queued PR had vanished. Derive the id in the same
    command rather than pasting a literal: `PRID=$(gh pr view <n> --json id --jq .id)`. Hit on run
    75; cost was small only because the PR was known-good at the time.
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
   - **Prefer `agent-ready`.** `org:FreeForCharity label:agent-ready is:open -label:claimed` is the
     same query narrowed to issues that are _actually pickable_: unclaimed, unblocked, one-PR-scoped
     and carrying acceptance criteria. `agentic-os` is the programme-wide **topic** label and stays
     on everything, so it also counts epics, machine-managed rolling issues (740/738 open and close
     those themselves), items blocked on a human with credentials, and durable findings kept as
     records — none of which an agent can execute. Counting the topic label is why the Conductor's
     "keep 5–15 open" band read 46 and drifted upward for eight consecutive runs of trimming that
     could never converge (#922). Add `agent-ready` when you file an issue that meets the bar;
     remove it when the issue becomes blocked.
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
