---
name: sites-list-refresh
description: >-
  Refresh the FFC Sites Master List (ffcadmin.org/sites-list) end to end — find the stuck 703 run,
  approve its github-prod gate, merge the data PR, then run the ffcadmin sync and merge that. Use
  when asked to "update the sites list", "refresh the sites list", "the sites list is stale", "pull
  the latest sites data", "approve the sites list gate", or when the ffcadmin Sites List page shows
  its "Site data may be stale" banner. Names the exact workflows, the gate, the order, the approval
  commands, and the gotchas.
---

# Sites list refresh (703 → data PR → ffcadmin sync → deploy)

The public [Sites Master List](https://ffcadmin.org/sites-list/) is built in two repos, and only the
first hop needs a human. This skill is the whole chain, in order, so it can be run without
re-explaining it each time.

```
703 (this repo, gated github-prod) ──► data PR data/sites-list-auto ──► merge queue ──► main
   sites-list/sites_list.{csv,json} on main (public raw URL)
        │
        ▼
update-sites-data.yml (ffcadmin, no secrets, Mon 09:00Z) ──► PR data/sites-list-sync ──► merge ──► Pages deploy
   docs/sites_list.{csv,json} + docs/sites_list.prev.csv
```

## Why it gets stuck

`703. Sites List - Generate` runs weekly (Mon 08:00Z) on `environment: github-prod`, which has a
required reviewer (`clarkemoyer`). **A scheduled run cannot approve itself**, so every Monday it
parks at `status: waiting`. If nobody answers, `734. Stale Waiting-Run Janitor` (daily 06:31Z)
cancels it at the first tick after it is **7 days** old, warning 2 days before. That is not a
failure — the run reads `cancelled` — and the downstream ffcadmin sync keeps going green because it
copies whatever upstream last published. A green sync is **not** evidence of fresh data.

The gate is held on purpose (`docs/workflow-safety-and-approvals.md`, 703 row: _held on credential
scope_ — it loads the `wr-all-cbm-ffc-copilot-mcp-github-pat` writer PAT so its PR can trigger
required checks). Do not "fix" this by moving 703 to an ungated environment.

## Step 1 — Find the waiting 703 run

```bash
gh run list -R FreeForCharity/FFC-Cloudflare-Automation --workflow 703-sites-list-generate.yml \
  --limit 5 --json databaseId,status,conclusion,event,createdAt
```

- One run `waiting` → approve it (step 2). Approve the **newest** waiting run; it checks out `main`
  at job start (`ref: main`), so an old run still generates from current code.
- None waiting and the newest is `cancelled` → dispatch a fresh one, then approve it:
  `gh workflow run 703-sites-list-generate.yml -R FreeForCharity/FFC-Cloudflare-Automation --ref main`
  (MCP: `actions_run_trigger`, `method: run_workflow`, no inputs).
- Only one run can proceed at a time (`concurrency: sites-list-generate`, no cancel-in-progress).

UI:
<https://github.com/FreeForCharity/FFC-Cloudflare-Automation/actions/workflows/703-sites-list-generate.yml>
→ the run marked **Waiting** → **Review deployments** → tick `github-prod` → **Approve and deploy**.

## Step 2 — Approve the `github-prod` gate (human: `clarkemoyer`)

Sandboxed agents (Claude Code on the web, MCP) **cannot approve** — there is no pending-deployments
tool and direct REST is 403. The approver is Clarke, via the UI above or a `gh` authenticated as
Clarke:

```bash
RUN_ID=<run id from step 1>
R=FreeForCharity/FFC-Cloudflare-Automation
gh api repos/$R/actions/runs/$RUN_ID/pending_deployments \
  --jq '.[] | {env: .environment.name, env_id: .environment.id, current_user_can_approve}'
# -F (typed), not -f: -f sends the id as a string and the API rejects it
gh api -X POST repos/$R/actions/runs/$RUN_ID/pending_deployments \
  -F "environment_ids[]=<env_id>" -f state=approved -f comment="sites list refresh"
# confirm from the run, never from the POST's output
gh api repos/$R/actions/runs/$RUN_ID --jq '.status'   # waiting -> in_progress
```

Keep the POST's stderr visible: it is the only place a _rejected_ approval reports itself.

### The second prompt (601) — approve it too, it is next week's data

703 reuses the newest successful `601. WPMUDEV - Export Sites` artifact and, as its **last step**,
dispatches a fresh 601 for next week. 601 is gated on `wpmudev-prod`, so a **second approval prompt
appears ~10 minutes after the first**. Nothing in the current run waits on it — but approve it while
you are on the Actions page, or next week's run reuses an ever-older WPMUDEV export
(`STALE_EXPORT_DAYS` = 14 warns on it).

## Step 3 — Watch 703 and merge its data PR

```bash
gh run watch $RUN_ID -R $R --exit-status
gh pr list -R $R --head data/sites-list-auto --json number,title,state,mergeStateStatus
```

703 opens `chore(sites-list): automated sites list regeneration` from branch `data/sites-list-auto`
(no PR means the data did not change — you are done upstream). `main` merges through the **merge
queue**:

- If it is a draft: `gh pr ready <n>`. The first enqueue after promotion usually fails with
  `Required status check "Phantom Revert Guard" is expected` — retry after ~60s, don't diagnose.
- Enqueue: `gh pr merge <n> --auto -R $R` (no strategy flag, no `--delete-branch`). Confirm with
  `mergeQueueEntry`, or re-run the `enqueuePullRequest` mutation and read "already in the queue" — a
  `null` `autoMergeRequest` does **not** mean the enqueue failed.
- If Phantom Revert Guard fails for staleness (`N commits behind main (threshold: 5)`):
  `gh api -X PUT repos/$R/pulls/<n>/update-branch`, then enqueue again.
- Resolve any Copilot review threads first; an unresolved thread blocks the queue.

## Step 4 — Sync into ffcadmin and merge

The weekly sync runs Mon 09:00Z. If the upstream PR merged after that, dispatch it:

```bash
F=FreeForCharity/FFC-IN-ffcadmin.org
gh workflow run update-sites-data.yml -R $F --ref main
gh pr list -R $F --head data/sites-list-sync --json number,title,state
gh pr merge <n> --auto -R $F
```

It needs no secrets and no approval. Merging to `main` deploys the site. No PR after a green run
means upstream was unchanged (see Gotchas).

## Step 5 — Verify on the published page, not on a green run

```bash
curl -fsSL https://raw.githubusercontent.com/FreeForCharity/FFC-Cloudflare-Automation/main/sites-list/sites_list.json | head -c 400
git -C <ffcadmin clone> log -1 --format='%h %ci %s' -- docs/sites_list.csv
```

Then load <https://ffcadmin.org/sites-list/>: the amber "Site data may be stale" banner should be
gone. That banner is computed from the newest repo activity inside the snapshot, so it is the real
freshness signal. The header's "Site data refreshed …" line is **not** — it reads the CSV's file
mtime at build time, which is the checkout time on CI, so it always looks recent.

## Gotchas

- **Deadline math.** A run created Monday 08:22Z is reaped at the first 06:31Z tick after it is 7
  days old — i.e. the _Tuesday_ after next Monday, not "in 7 days". The 734 warning in the Conductor
  Log names the exact instant.
- **Cancelled ≠ failed.** Five consecutive janitor-reaped runs once froze the page for six weeks
  with nothing red anywhere (#1238). Read `conclusion`, not just "no failures".
- **No sync PR is not a failure.** Since FreeForCharity/FFC-IN-ffcadmin.org#1255 the sync compares
  the fetched files with the committed ones and exits green without opening a PR when upstream is
  unchanged, so the page's changed-row markers (diffed against `sites_list.prev.csv`) survive a
  quiet week. If the sync ran and no `data/sites-list-sync` PR appeared, upstream simply has nothing
  new — check that step 3's data PR actually merged before assuming anything else.
- **Dispatch inputs over MCP must be strings** (703 and the sync take none, so this only matters if
  you extend them).
- **Never** approve a gate you did not mean to, `--admin`-merge, or push to `main` directly.

## One-line version for the operator

> Approve the waiting **Generate Sites List** run (and the WPMUDEV export prompt that follows ~10
> min later) → merge the `data/sites-list-auto` PR → run **Sync Sites List Data** in ffcadmin if
> Monday's already passed → merge `data/sites-list-sync`.
