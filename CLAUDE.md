# Repo notes for Claude

## Running & authorizing GitHub Actions workflows (IMPORTANT)

In this remote environment the `gh` CLI is typically pre-authenticated — run `gh auth status` to
confirm (and `gh auth login` if not). When available it acts as a real user (e.g. `clarkemoyer`)
with `workflow` + `repo` scopes. **Prefer `gh` for anything Actions-related.**

Do NOT rely on the MCP GitHub tools to run workflows: they run through a GitHub **App installation**
whose granted scopes do not include `actions: write`, so `actions_run_trigger`/`run_workflow`
returns `403 Resource not accessible by integration`. (MCP is fine for PRs, issues, comments,
reviews — those are within the App installation's granted permissions.)

### Dispatch a workflow

```bash
gh workflow run <workflow-file>.yml --ref <branch>
# e.g.
gh workflow run 8-whmcs-export-products.yml --ref main
```

`git push` also triggers `push`-event workflows, but environment-gated jobs still wait for approval
(see below).

### Environment approval gate (`whmcs-prod`)

Workflows that use `environment: whmcs-prod` (all WHMCS jobs) require a deployment approval; the run
sits at `status: waiting`. Reviewer is `clarkemoyer`, and `gh` is authed as them, so approve it
directly:

```bash
RUN_ID=<run id>
# find the environment id + confirm you can approve
gh api repos/FreeForCharity/FFC-Cloudflare-Automation/actions/runs/$RUN_ID/pending_deployments \
  --jq '.[] | {env: .environment.name, env_id: .environment.id, current_user_can_approve}'
# approve
gh api -X POST repos/FreeForCharity/FFC-Cloudflare-Automation/actions/runs/$RUN_ID/pending_deployments \
  -F "environment_ids[]=<env_id>" -f state=approved -f comment="approved"
```

(Note: the approval API returns an array of deployment objects; don't apply a `--jq` filter that
assumes a single object.)

### Watch a run / read results

```bash
gh run view <run id>                 # summary
gh run view <run id> --log           # full logs (read-only export catalogs print here)
gh api repos/FreeForCharity/FFC-Cloudflare-Automation/actions/runs/<id>/jobs --jq '.jobs[]|{name,status,conclusion}'
```

To wait for completion, poll in a background Bash task with an `until`/loop on
`gh api .../runs/<id> --jq '.status'` (do not foreground-sleep).

## WHMCS API

- Endpoint: `https://freeforcharity.org/hub/includes/api.php`. Identifier is inline in workflows;
  the secret value is stored under the GitHub Actions **secret named**
  `ZBBEPFQ5W7RCSIME0NOQOYRQIDGTKBPU` (this is the secret name/key, not the value), referenced as
  `${{ secrets.ZBBEPFQ5W7RCSIME0NOQOYRQIDGTKBPU }}`, plus the secret named `WHMCS_API_ACCESS_KEY`.
  These secrets live only in the `whmcs-prod` environment, so WHMCS scripts can't run from this
  sandbox directly — run them via Actions.
- Onboarding scripts: `whmcs-client-add.ps1` (AddClient), `whmcs-contact-add.ps1` (AddContact),
  `whmcs-order-add.ps1` (AddOrder), shared helpers in `whmcs-api-common.ps1`. Product/custom-field
  discovery via `whmcs-products-export.ps1` (prints a catalog to the job log).
