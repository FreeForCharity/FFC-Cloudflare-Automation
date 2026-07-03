# Repo notes for Claude

> Agent-generic onboarding (catalog, numbering, safety model, add-a-workflow checklist) lives in
> **AGENTS.md** — read that first. This file covers Claude-specific environment notes.

## Merging: queue etiquette (validated 2026-07-01, PRs #534–#538)

- `main` requires **Validate Repository** + **Phantom Revert Guard** (strict) and merges via the
  **merge queue**, which builds a merge group and re-runs those checks (722/723 have `merge_group:`
  triggers; 727 skips on merge groups = passing).
- **Resolve review threads before queueing.** Copilot auto-reviews every PR; fix the real findings
  first, then
  `gh api graphql -f query='mutation{resolveReviewThread(input:{threadId:"<id>"}){thread{isResolved}}}'`.
  List threads: query `pullRequest(number:N){reviewThreads(first:20){nodes{id isResolved …}}}`.
- **`gh pr merge --auto` can mask the real blocker** behind a GraphQL "rate limit" error. Use the
  direct mutation to see the truth (unresolved conversation / CodeQL pending):
  `gh api graphql -f query='mutation{enqueuePullRequest(input:{pullRequestId:"<node_id>"}){mergeQueueEntry{position state}}}'`
- GraphQL and REST have **separate rate pools** (5,000/hr each, shared account-wide). When GraphQL
  is exhausted, reads still work via REST; check with `gh api rate_limit`.
- Never `--admin`-merge; never push to `main` directly.

## Running & authorizing GitHub Actions workflows (IMPORTANT)

In a self-hosted/local remote environment the `gh` CLI is typically pre-authenticated — run
`gh auth status` to confirm (and `gh auth login` if not). When available it acts as a real user
(e.g. `clarkemoyer`) with `workflow` + `repo` scopes. **Prefer `gh` for anything Actions-related.**

Do NOT rely on the MCP GitHub tools to run workflows: they run through a GitHub **App installation**
whose granted scopes do not include `actions: write`, so `actions_run_trigger`/`run_workflow`
returns `403 Resource not accessible by integration`. (MCP is fine for PRs, issues, comments,
reviews — those are within the App installation's granted permissions.)

### Claude Code on the web (sandbox) — `gh` web-flow auth does NOT work here (IMPORTANT)

When running as **Claude Code on the web**, do not waste time trying to `gh auth login` (web/device
flow) to get "full org" access — it cannot work in this sandbox, and here is the proof so a future
session doesn't rediscover it the hard way:

- All outbound HTTPS goes through the agent egress proxy. The proxy **intercepts `api.github.com`
  and injects its own auth**, ignoring whatever token `gh`/`curl` sends. A request to
  `https://api.github.com/user` with a **bogus** `Authorization` header — or **no** header at all —
  still returns `200` as `clarkemoyer`. So no token a web/device flow obtains is ever used.
- Direct repo/Actions calls via that proxy auth return
  `403 "GitHub access is not enabled for this session…"` for this org, so `gh`/`curl` cannot
  dispatch workflows or approve deployments from the sandbox either.
- The **MCP** GitHub tools are the working channel in the sandbox (scoped to this repo) — but, as
  above, MCP lacks `actions: write`, so it still **cannot dispatch workflows or approve environment
  deployments**. It _can_ create/assign issues, open PRs, push files, comment, and read Actions
  runs/logs.

Net effect in the web sandbox: you cannot _dispatch_ a `workflow_dispatch` workflow, and you cannot
_approve_ an environment gate. You **can** trigger any `issues`-event workflow (e.g. Website
Provision) by creating + assigning an issue via MCP, and a human reviewer (`clarkemoyer`) approves
any environment gates. See the next section.

### Provision a website repo + add a maintainer (primary workflow)

This is the canonical way to "establish the repo for `<domain>` and add a GitHub user as
maintainer". It runs **`701. Website - Provision`** (`.github/workflows/701-website-provision.yml`),
which on `issues: [assigned]` creates `FFC-EX-<domain>` from the FFC template, enables GitHub Pages,
adds the Technical POC as a `maintain` collaborator, and (only if the zone is controlled in FFC
Cloudflare) enforces apex + `www` GitHub Pages DNS. All privileged steps run inside Actions with
`secrets.CBM_TOKEN`, so this path needs **no** `actions: write` from the caller.

From the web sandbox (works today), using the admin-minimal template
(`.github/ISSUE_TEMPLATE/07-adminonly-provision-website.yml`) — create the issue **with an
assignee** via MCP so the `assigned` event fires:

- Title: `[WEBSITE REQUEST] <domain>` (apex, no `https://`, no `www`)
- Labels: `website-request`, `admin-provision`, `github-pages`, `cloudflare`
- Body sections (issue-form headings are parsed verbatim):
  - `### Website Domain (no http://)` → `<domain>`
  - `### Technical POC GitHub Username` → the maintainer's GitHub login (omit/blank to skip)
- Assignee: any user (e.g. `clarkemoyer`) — assignment is what triggers the run.

> **Gotcha — keep all prose ABOVE the `###` sections.** `extractSection` captures everything from a
> heading to the next `###` _or end of body_, so any explanatory text placed **after** the last
> section (e.g. a `---` note after `### Technical POC GitHub Username`) is slurped into that field's
> value. A maintainer login then fails validation and is silently skipped
> (`Skipping invalid GitHub username for maintainer`), and the repo is created without the
> maintainer. Put any narrative at the top of the body, before `### Website Domain`.

Then watch the run via MCP (`actions_list` / `get_job_logs`). **If the zone is controlled in FFC
Cloudflare**, the `dns` (`cloudflare-prod-write`) and `repo` (`github-prod`) jobs sit at
`status: waiting` on environment approval, and `repo` is chained behind `dns` — i.e. the repo is
**only** created once the DNS repoint is approved. The sandbox cannot approve; ask `clarkemoyer` to
approve both gates (UI → _Review deployments_, or the `gh api … pending_deployments` flow below).

From a `gh`-authed environment you can instead dispatch directly:
`gh workflow run 701-website-provision.yml --ref main -f domain=<domain> -f technical_poc_github_username=<login>`.

### Dispatch a workflow

```bash
gh workflow run <workflow-file>.yml --ref <branch>
# e.g.
gh workflow run 202-whmcs-export-products.yml --ref main
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

## WHMCS API (Key Vault + APIM architecture)

WHMCS automation is **fully Key-Vault-backed and IP-stable**. The end-to-end path is:

> **Validation status (2026-06-28):** the hardened path is proven in production. A keyless call to
> the APIM gateway returns `401` (the `whmcs` API is `subscriptionRequired: true`), and a real
> `windows-latest` runner dispatch of **`202. WHMCS - Export Products`**
> (`202-whmcs-export-products.yml`) on `main` completed `success` — the `whmcs-secrets-from-kv`
> action loaded `WHMCS_APIM_SUBSCRIPTION_KEY` (masked) and the export returned live data (30
> products, 535 client products) through OIDC → KV → APIM → Cloudflare → WHMCS.

```
GitHub runner ──OIDC──► Azure (ffc-admin-kv-writer) ──► Key Vault (creds + APIM key)
runner ──POST + Ocp-Apim-Subscription-Key──► APIM apim-ffc-gateway-prod (egress 20.231.116.111)
        ──► Cloudflare ──► WHMCS origin (freeforcharity.org/hub/includes/api.php)
```

### Credentials come from Key Vault via OIDC (KV is master — never a GH secret copy)

- Composite action **`.github/actions/whmcs-secrets-from-kv`**: `azure/login@v3` (OIDC, no Azure
  password in GitHub) → `az keyvault secret show` from `kv-ffc-admin-prod-cbm` → masks → exports
  `WHMCS_API_IDENTIFIER`, `WHMCS_API_SECRET`, and `WHMCS_APIM_SUBSCRIPTION_KEY` to `GITHUB_ENV`
  (heredoc-delimited). Mirrors `cloudflare-tokens-from-kv`.
- **Scoped KV secret names** (like the Cloudflare tokens):
  `{wr-all,read-all}-ffc-whmcs-api-identifier`, `…-ffc-whmcs-api-secret`,
  `…-ffc-apim-whmcs-subscription-key` (+ a `…-ffc-whmcs-api-url`). WHMCS is a single credential, so
  `read-all-*` and `wr-all-*` hold identical values; `scope` (default `write`) only selects which
  identity/copy is used. The action defaults to `write`.
- **OIDC identifiers are repository Variables** (not env secrets — they are non-secret GUIDs):
  `vars.WR_ALL_FFC_AZURE_KV_CLIENT_ID` / `vars.WR_ALL_FFC_AZURE_TENANT_ID`. So `whmcs-prod` holds
  **no** secrets; the per-environment **federated credential**
  (`repo:FreeForCharity/FFC-Cloudflare-Automation:environment:whmcs-prod` on `ffc-admin-kv-writer`)
  is what authorizes the OIDC exchange. Each WHMCS job sets `permissions: id-token: write`.
- Scripts resolve creds from those env vars via `Resolve-WhmcsCredentials` in
  `whmcs-api-common.ps1`, so the action is a drop-in — no per-script credential wiring.

### Calls route through APIM for a static egress IP

- `WHMCS_API_URL` in every WHMCS workflow points at the APIM gateway
  `https://apim-ffc-gateway-prod.azure-api.net/whmcs/api.php` (not the origin). The `whmcs` API
  proxies to `freeforcharity.org/hub/includes` and **requires the `Ocp-Apim-Subscription-Key`**
  (subscription `whmcs-ops`). `Invoke-WhmcsApi` and the self-contained export scripts add that
  header from `WHMCS_APIM_SUBSCRIPTION_KEY` when set (unset ⇒ they call WHMCS directly).
- **WHMCS-side config (one-time):** in System Settings → General Settings → Security, allowlist
  `20.231.116.111` under **API IP Access Restriction** and set **Proxy IP Header** to
  `CF-Connecting-IP`. The latter is essential: WHMCS is behind Cloudflare, and APIM appends the
  dynamic runner IP to `X-Forwarded-For`; reading `CF-Connecting-IP` makes WHMCS use APIM's stable
  IP instead. See `docs/whmcs-apim-routing.md`.
- Sandbox testing: you CAN hit the live WHMCS API from this sandbox via the APIM gateway (fetch the
  identifier/secret/`whmcs-ops` key from KV with `az`, POST with the `Ocp-Apim-Subscription-Key`
  header). `whmcs-prod` no longer holds the credential.

### Scripts

- Onboarding: `whmcs-client-add.ps1` (AddClient), `whmcs-contact-add.ps1` (AddContact),
  `whmcs-order-add.ps1` (AddOrder); shared helpers in `whmcs-api-common.ps1`. Product/custom-field
  discovery via `whmcs-products-export.ps1` (prints a catalog to the job log).

### Architectural memory

- **Key Vault is the single source of truth** for the WHMCS credential AND the APIM subscription
  key; GitHub consumes them at runtime via OIDC. Never reintroduce a GH-environment copy of the
  secret (that drift is exactly what broke the Cloudflare token for 4 months). The legacy GH secret
  `ZBBEPFQ5W7RCSIME0NOQOYRQIDGTKBPU` / `WHMCS_API_ACCESS_KEY` is **deprecated** (nothing reads it)
  and can be deleted from `whmcs-prod`. The `whmcs-secrets-from-kv` action no longer fetches or
  exports a WHMCS access key at all (the WHMCS API does not use one); the per-script `-AccessKey`
  parameter remains as a generic, inert WHMCS API option.
- **Rotate** the WHMCS secret or the APIM key by adding a new version of the relevant
  `*-ffc-whmcs-*` / `*-ffc-apim-whmcs-subscription-key` KV secret — no GitHub change needed.
