# AGENTS.md — FFC-Cloudflare-Automation

Canonical onboarding for any AI agent (Claude, Copilot, Jules, …) or new admin working in this
repository. Tool-specific notes live in `CLAUDE.md`; org-wide mission/security rules follow the FFC
global policy (never expose secrets; Conventional Commits; PRs, never direct pushes to `main`).

## What this repo is

The automation hub for Free For Charity infrastructure: ~60 GitHub Actions workflows that drive
Cloudflare (DNS/registrar), WHMCS (billing/support), Microsoft 365, Zeffy, Google (Analytics/GTM),
WPMUDEV, and the FFC GitHub org itself. PowerShell-first scripts in `scripts/`, credentials from
Azure Key Vault via OIDC (never GitHub secrets).

## Picking a workflow

1. **Read the catalog first**: `docs/workflow-catalog.json` (machine-readable) or the generated
   section of `.github/workflows/README.md`. Public rendering: <https://ffcadmin.org/automation/>.
2. **The number tells you the target system** — 3-digit, category-first: `1xx` Cloudflare/DNS/Domain
   · `2xx` WHMCS · `3xx` Microsoft · `4xx` Zeffy · `5xx` Google · `6xx` WPMUDEV · `7xx` GitHub
   (website + repo) · `8xx/9xx` reserved.
3. **Names**: `NNN. Target - Description [TAG]`; the `[TAG]` lists every API the workflow **calls**
   (`+`-joined). "Calls" means the API actually invoked — not the service the records are _for_
   (M365 DNS written via Cloudflare = `[CF]`) and never plumbing (KV auth, posting an issue
   comment).
4. **Prefer Reads before Writes.** Check the safety level in the catalog /
   `docs/workflow-safety-and-approvals.md` before dispatching anything.

## Safety model (summary — full doc: `docs/workflow-safety-and-approvals.md`)

1. Read vs write credential scopes (`read-all-*` vs `wr-all-*` Key Vault secrets).
2. Environment approval gates — write envs (and some read envs like `whmcs-prod`, `m365-prod`,
   `wpmudev-prod`) pause at `waiting` for a human reviewer.
3. `dry_run` defaults to **true** on write workflows; live requires `dry_run=false`.
4. Typed confirmation for the highest-stakes actions (e.g. domain registration).
5. Key Vault is the **single source of truth** for credentials; rotation = new KV version, no GitHub
   change. Never reintroduce a GitHub-secret copy.

## Merging (validated flow — do not bypass)

- `main` requires status checks **Validate Repository** + **Phantom Revert Guard** (strict), and
  merges go through the **merge queue**, which builds a merge group and re-runs those checks.
- **Review threads must be resolved before the queue accepts a PR.** Fix real findings first, then
  resolve via GraphQL: `resolveReviewThread(input:{threadId:…})`.
- Enter the queue with `gh pr merge <n> --merge --auto`, or directly:
  `gh api graphql -f query='mutation{enqueuePullRequest(input:{pullRequestId:"<node_id>"}){mergeQueueEntry{position state}}}'`
- **Debugging tip:** `gh pr merge --auto` can mask the real blocker behind a GraphQL "rate limit"
  error. The `enqueuePullRequest` mutation returns the true reason (unresolved conversation, CodeQL
  still running, …).
- Never merge with `--admin`; never push to `main`.

## Adding or changing a workflow

1. Pick the next free number in the right category; file name `NNN-<slug>.yml`; display name
   `NNN. Target - Description [TAG]`.
2. Add a row to `docs/workflow-safety-and-approvals.md` (CI enforces coverage).
3. Regenerate the catalog: `python3 scripts/generate-workflow-catalog.py` (CI fails on drift).
4. Credentials via a `*-secrets-from-kv` composite action; jobs set `permissions: id-token: write`
   and an `environment:`.
5. Write workflows: `dry_run` input defaulting to `true`, a `concurrency` group
   (`cancel-in-progress: false`), and an approval-gated environment.

## GitHub API rate budget (shared — be frugal)

Every agent session, scheduled task, and PAT-based workflow authenticates as the same user and
shares **one REST core budget (5,000 requests/hr) and one GraphQL points budget (typically
5,000 points/hr — cost varies per query, so heavy queries drain it faster than a request count
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
