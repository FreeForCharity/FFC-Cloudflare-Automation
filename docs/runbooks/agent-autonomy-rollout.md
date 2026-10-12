# Runbook: agent-autonomy rollout (local Desktop session with Clarke)

Written 2026-10-10 by a cloud session for a **local** Claude Desktop session to execute, with Clarke
present. The why is in [`../human-intervention-map.md`](../human-intervention-map.md). This file is
the how, in order. Every phase ends with a check that proves it worked.

## Where to run it

- **Claude Desktop → Code tab → Environment: Local.** Not Cloud. Only a local session has Clarke's
  own `gh` sign-in (GraphQL, the environments API, gate approvals) and `az` sign-in.
- **Project folder: a clone of `FreeForCharity/FFC-Cloudflare-Automation`, opened at the repo
  root.** Opening the repo itself (not a parent folder) is what loads `CLAUDE.md`, `AGENTS.md`,
  `.claude/skills/` and the `.claude/hooks/` guards.
- **Use a separate clone or a worktree, not the Conductor's tree.** The scheduled Conductor works in
  `C:\ClaudeCodeDesktop\Claude_AI_OS_Routine\repos\FFC-Cloudflare-Automation`. Sharing that tree
  with a second agent is the background-tree hazard in `CLAUDE.md`. From that clone:

  ```bash
  git fetch origin claude/brave-curie-yphsu2
  git worktree add C:/ClaudeCodeDesktop/ffc-autonomy origin/claude/brave-curie-yphsu2
  ```

  Then open `C:\ClaudeCodeDesktop\ffc-autonomy` as the session's folder. This file is on that branch
  (PR #1595) until it merges.

- **Opening prompt:** _"Read `docs/runbooks/agent-autonomy-rollout.md` and execute it phase by phase
  with me. Stop at each ⛔ for my decision."_

## Ground rules for the executing session

- ⛔ marks a decision or action that is Clarke's. Ask, then wait. Everything else, do.
- Never print a secret. Where a value must reach a settings page, pipe it to the clipboard (`| clip`
  on Windows) and tell Clarke to paste it. Never into chat, never into a file.
- Confirm every GitHub or Azure write by re-reading the state it should have changed (the
  `CLAUDE.md` read-after-write rules). A command's exit code is not the confirmation.
- Windows host notes apply (`CLAUDE.md`): `PYTHONIOENCODING=utf-8`, `MSYS_NO_PATHCONV=1` for
  `rev:.github/...` paths, `C:/...` paths for Python.

---

## Phase 0: preflight

```bash
gh auth status                     # expect clarkemoyer, scopes incl. repo, workflow, admin:org
gh api graphql -f query='{viewer{login}}'
az account show --query '{user:user.name,tenant:tenantId}'
gh api orgs/FreeForCharity/installations --jq '.installations[]|{app:.app_slug,repos:.repository_selection}'
```

**Done when** all four succeed. In the last one, the `claude` app must show
`repository_selection: "all"`. ⛔ If it shows `selected`, Clarke switches it to **All repositories**
at <https://github.com/organizations/FreeForCharity/settings/installations>. This is the step that
makes newly created charity repos visible to every session.

## Phase 1: Azure identity for cloud sessions (decision ⛔)

⛔ **Clarke approves the exception**: a dedicated, read-only service principal with a client secret,
used only by the `FFC_Ops` cloud environment. Every other identity stays OIDC-only. ⛔ Also choose
the scope: the whole vault, or only the `read-all-*` secrets (safer; the default below).

```bash
APP=$(az ad app create --display-name ffc-claude-cloud-kv-reader --query appId -o tsv)
az ad sp create --id "$APP" >/dev/null
KV=$(az keyvault show -n kv-ffc-admin-prod-cbm --query id -o tsv)
# read-all-* only: one assignment per secret
for s in $(az keyvault secret list --vault-name kv-ffc-admin-prod-cbm --query "[?starts_with(name,'read-all-')].name" -o tsv); do
  az role assignment create --assignee "$APP" --role "Key Vault Secrets User" --scope "$KV/secrets/$s" >/dev/null && echo "granted $s"
done
echo "AZURE_CLIENT_ID=$APP"; echo "AZURE_TENANT_ID=$(az account show --query tenantId -o tsv)"
az ad app credential reset --id "$APP" --display-name claude-cloud --years 1 --query password -o tsv | clip
```

The last line puts the password on the clipboard and nowhere else. Record the app in
`docs/azure-oidc-federated-credentials.md` (identity table), marked as the one secret-based
identity, with its expiry date. Then start a 1-year rotation reminder (a one-off routine).

**Done when** `az role assignment list --assignee "$APP" --all --query 'length(@)'` equals the
number of `read-all-*` secrets.

## Phase 2: the `FFC_Ops` cloud environment (Clarke, in the UI, about 3 minutes ⛔)

Claude Desktop → Code → environment dropdown → **Cloud → Add cloud environment**:

- **Name:** `FFC_Ops`
- **Network access:** Full
- **Environment variables:** `AZURE_TENANT_ID`, `AZURE_CLIENT_ID` (from Phase 1), and
  `AZURE_CLIENT_SECRET`, pasted from the clipboard.
- **Network secrets (optional):** a read-only Cloudflare API token for `api.cloudflare.com`.
- **Setup script:**

  ```bash
  #!/bin/bash
  set -e
  command -v az >/dev/null || curl -sL https://aka.ms/InstallAzureCLIDeb | bash
  ```

Leave `FFC_Trusted` as it is. It stays the narrow lane for coding work.

## Phase 3: the phone-runnable test routine

Create it at <https://claude.ai/code/routines> (or `/schedule` in a CLI session):

- **Name:** `FFC Ops check` · **Environment:** `FFC_Ops` · **Repo:** `FFC-Cloudflare-Automation`
  only · **Connectors:** none · **Trigger:** none (Run now only) · **Model:** Opus 5.5
- **Prompt:**

  > Read-only connectivity check. Never print a secret value. Report PASS/FAIL per step with the
  > error text on failure, then stop. (1)
  > `az login --service-principal -u "$AZURE_CLIENT_ID" -p "$AZURE_CLIENT_SECRET" --tenant "$AZURE_TENANT_ID" --allow-no-subscriptions -o none`.
  > (2) List secret names in `kv-ffc-admin-prod-cbm` that start with `read-all-`, and report the
  > count. (3) Fetch `read-all-ffc-whmcs-api-identifier` and report only its length. (4) Fetch the
  > three `read-all-ffc-whmcs-*` / `read-all-ffc-apim-whmcs-subscription-key` values into shell
  > variables and POST `action=GetProducts&responsetype=json` to
  > `https://apim-ffc-gateway-prod.azure-api.net/whmcs/api.php` with the `Ocp-Apim-Subscription-Key`
  > header; report `totalresults`. (5) If a Cloudflare network secret exists,
  > `GET https://api.cloudflare.com/client/v4/zones?per_page=1` and report
  > `result_info.total_count`. (6)
  > `gh api repos/FreeForCharity/FFC-Cloudflare-Automation --jq .full_name`.

**Done when** Clarke presses Run now from his phone and steps 1–4 and 6 report PASS.

## Phase 4: gate tiers on GitHub (decision ⛔)

⛔ Clarke confirms the Tier 2 list in `human-intervention-map.md` before anything here changes.

**4a. Find what a `main`-only policy would break first.** A job on a write environment that runs on
`pull_request` or `merge_group` has a non-`main` ref and would stop deploying. List them:

```bash
grep -lE "^\s*(pull_request|merge_group):" .github/workflows/*.yml \
  | xargs grep -lE "environment:\s*(cloudflare-prod-write|whmcs-prod|github-prod|google-prod-write|m365-prod|wpmudev-prod)\s*(#.*)?$"
```

Each hit gets read before 4b. Expect none; any hit is a job to move or an exception to record.

**4b. Snapshot, then add the `main`-only policy to every existing write environment.** The
environments `PUT` **replaces** protection rules, so a call that omits `reviewers` deletes the
reviewer. Always re-send it:

```bash
R=repos/FreeForCharity/FFC-Cloudflare-Automation
gh api "$R/environments?per_page=100" > env-before.json         # keep this file: it is the rollback
RID=$(gh api users/clarkemoyer --jq .id)   # not UID: bash makes UID read-only
for E in cloudflare-prod-write whmcs-prod github-prod google-prod-write m365-prod wpmudev-prod; do
  gh api -X PUT "$R/environments/$E" --input - <<JSON
{"reviewers":[{"type":"User","id":$RID}],
 "deployment_branch_policy":{"protected_branches":false,"custom_branch_policies":true}}
JSON
  gh api -X POST "$R/environments/$E/deployment-branch-policies" -f name=main -f type=branch
done
```

**4c. Create the Tier 1 environments** with the same `main` policy and **no** `reviewers`:
`github-prod-provision`, `cloudflare-prod-provision`, `google-prod-provision` (#636) and
`whmcs-prod-provision`. Then add a federated credential on **`ffc-admin-kv-writer`** for each, with
subject `repo:FreeForCharity/FFC-Cloudflare-Automation:environment:<name>`, following
`docs/azure-oidc-federated-credentials.md`. These lanes write, so they use the writer identity.

**Done when** `gh api "$R/environments?per_page=100"` shows: every write environment with
`custom_branch_policies: true` and a `main` policy; Tier 2 environments still carrying a reviewer;
Tier 1 environments with none. Run `730. Repo - Audit Environment Approval Gates` and keep its
output.

**Rollback** is `env-before.json`: re-`PUT` each environment from it.

## Phase 5: route workflows to the tiers (PRs, merge queue)

One PR per workflow family, in this order. Each PR updates the matching rows in
`docs/workflow-safety-and-approvals.md`, the catalog
(`python3 scripts/generate-workflow-catalog.py`) and whatever `tests/workflow-logic/` asserts about
that workflow's environment. Run `python3 scripts/check-environment-protection.py` and the
workflow-logic tests before pushing.

1. **`check-environment-protection.py`**: also assert the `main`-only branch policy on every write
   environment (the guard first, so the routing PRs are checked by it).
2. **505 / 503** → `google-prod-provision`.
3. **701 `repo` job**, **703** and **706 `deliver`** → `github-prod-provision`. Add the input guards
   from the map: repo names must match `FFC-EX-*`, owner `FreeForCharity`, and the job may not
   delete or change visibility.
4. **701 `dns` job, 103 and 105** → choose the tier per run with a `classify` job (does the apex
   serve a live non-GitHub site?), `cloudflare-prod-provision` when it does not,
   `cloudflare-prod-write` when it does. Dry runs always go to the provision tier.
5. **204** and **206** → `whmcs-prod-provision`. **211**, **207** live, **113** execute and **120**
   stay Tier 2.

**Done when** a fresh charity runs from the 701 issue to rebrand PR with no approval prompt, on a
test domain from `docs/testing-provisioning-with-sample-charities.md`.

## Phase 6: routines (Clarke at <https://claude.ai/code/routines>, or `/schedule update`)

| Routine                       | Change                                                                                                                                                                                                                                                                          |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Agentic OS Cloud Worker       | Model → Opus 5.5. Prepend step 0: `python3 /home/user/FFC-Cloudflare-Automation/scripts/verify-conductor-hooks.py --render --workspace /home/user --hub-clone /home/user/FFC-Cloudflare-Automation`. Stays on `FFC_Unrestricted`, or moves to `FFC_Ops` if it needs live reads. |
| Pending site conversion agent | Remove all 7 connectors. Repos → the hub plus the 3 sites in flight. Pin the model. Rewrite the prompt in the Cloud Worker's shape (one unit per run, hard rules, a tracking issue). Merge only PRs that are green and touch only the site's own content.                       |
| Consent sweep                 | Move its state block into a pinned tracking issue. Cut the prompt to the stable rules. Re-create as a new-session-per-run routine. Never `AskUserQuestion`: write a `DECISION NEEDED:` line on the issue instead. Clear the question its session is stuck on today.             |
| tamkeensports recapture       | Delete (disabled since 2026-10-03).                                                                                                                                                                                                                                             |
| 3 personal reminders          | Remove all connectors. They only need to send a message.                                                                                                                                                                                                                        |

Plus one template change, as a PR: add a `permissions.allow` block to
`.claude/conductor/settings.template.json` for `mcp__claude-code-remote__add_repo` and the GitHub
MCP write tools. The step-0 render then carries it into every multi-repo cloud session.

**Done when** `list_triggers` shows the changes, and the next firing of each routine ends with a
tracking-issue entry and no pending question.

## Phase 7: verify `add_repo` without a person (back in a cloud session)

This one has to be tested from the cloud, because the confirmation lives there. In a new cloud
session on `FFC_Trusted` with only the hub attached: run the step-0 render, then `add_repo` a repo
that is not attached. Record whether a confirmation appeared.

- **No prompt** → H1 is solved by fix 1 of the map. Note it in `CLAUDE.md`.
- **A prompt still appears** → it is enforced server-side. Adopt fix 2 (hand the new repo to a child
  session via `create_session` with `source_url`) for workflows A and B, and test that the same way.

## When it is all done

Update `human-intervention-map.md`'s "Today" columns to what was measured, and close the loop on PR
#1595 with the measured click counts per workflow.
