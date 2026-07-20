# AGENTS.md — FFC-Cloudflare-Automation

Canonical onboarding for any AI agent (Claude, Copilot, Jules, …) or new admin working in this
repository. Tool-specific notes live in `CLAUDE.md`; org-wide mission/security rules follow the FFC
global policy (never expose secrets; Conventional Commits; PRs, never direct pushes to `main`).

## What this repo is

The automation hub for Free For Charity infrastructure: ~60 GitHub Actions workflows that drive
Cloudflare (DNS/registrar), WHMCS (billing/support), Microsoft 365, Zeffy, Google (Analytics/GTM),
WPMUDEV, and the FFC GitHub org itself. PowerShell-first scripts in `scripts/`, credentials from
Azure Key Vault via OIDC (never GitHub secrets).

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
- **Supersession check before ready+queue.** Before promoting a PR, grep `main` for the
  function/capability names the PR adds — a same-purpose implementation may have landed on `main`
  after the PR branched (on 2026-07-20, #772's basePath probe duplicated `basePathMismatch` merged
  40 minutes earlier in #773; only the merge conflict stopped a double-ship). PRs that say `Refs #N`
  instead of `Closes #N` never sync the `claimed` label, so the claim protocol will not warn you —
  the grep is the check.
- **Re-check the PR is still open before pushing to its branch.** Merging main into an agent branch
  whose PR merged moments ago silently **re-creates the auto-deleted branch** — the tell is
  `[new branch]` in push output for a push you meant as an update. If you see it, delete the
  resurrected branch and stop.
- **Fetch refs individually.** `git fetch origin main <agent-branch>` aborts the **entire** fetch
  with "couldn't find remote ref" if the second ref was never pushed — leaving `origin/main` stale,
  so a clean branch falsely appears N commits ahead of main (seen 2026-07-20 on the #748 worker
  run). Fetch `main` on its own before comparing against it.
- Enter the queue with `gh pr merge <n> --merge --auto`, or directly:
  `gh api graphql -f query='mutation{enqueuePullRequest(input:{pullRequestId:"<node_id>"}){mergeQueueEntry{position state}}}'`
- **Debugging tip:** `gh pr merge --auto` can mask the real blocker behind a GraphQL "rate limit"
  error. The `enqueuePullRequest` mutation returns the true reason (unresolved conversation, CodeQL
  still running, …).
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
2. **Claim before working**: add the `claimed` label AND post one comment
   `CLAIM: <actor> <planned-branch> <UTC timestamp>` where `<actor>` identifies you
   (`conductor-run-N`, `live-session`, `copilot-agent`, or a human name — the shared login does not
   identify you). Opening a PR that says `Closes #N` is also a claim (automation will sync the label
   from linked PRs once the claim-sync workflow lands).
3. **Release on stop**: if you abandon the work, remove the label and comment. Claims with no open
   linked PR and no activity for 48h are considered expired and may be swept.
   - **Multi-repo / multi-part issues: claim your portion, not the issue.** Post the
     `CLAIM: <actor> …` comment scoped to the part you are taking (name the repo/portion) and do
     **not** add the exclusive `claimed` label — the remainder must stay visible in the pickup
     query. When you finish, comment what you shipped and what remains (pattern validated on #748,
     2026-07-20).
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

## Key docs

| Doc                                                            | What                                                                 |
| -------------------------------------------------------------- | -------------------------------------------------------------------- |
| `docs/workflow-safety-and-approvals.md`                        | per-workflow safety levels, gates, guards                            |
| `docs/workflow-catalog.json`                                   | generated machine-readable catalog                                   |
| `docs/google-api.md`                                           | Google architecture (KV-backed), GA/GTM models, provisioning record  |
| `docs/whmcs-apim-routing.md` / `docs/whmcs-product-catalog.md` | WHMCS credential path + products                                     |
| `docs/charity-onboarding-lifecycle.md`                         | end-to-end charity onboarding order                                  |
| `CLAUDE.md`                                                    | Claude-specific environment notes (sandbox constraints, auth quirks) |
