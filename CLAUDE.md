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
- **Format with the CI-pinned prettier.** `722-ci.yml` checks with `npx --yes prettier@3.8.1`; plain
  `npx prettier` fetches the latest version, whose Markdown reflow differs — producing
  local-pass/CI-fail loops. Always run `npx --yes prettier@3.8.1 --write <files>`.

## Verifying tests: CI is authoritative, local runs may be false-red

- **`tests/workflow-logic/` cannot be run reliably from every local sandbox.** In the Windows
  git-bash environment the Conductor runs in, `python3 tests/workflow-logic/run_all.py` reports most
  modules failing with `harness crashed:` and a node abort —
  `Assertion failed: ncrypto::CSPRNG(nullptr, 0)`. A plain `node -e "…"` works fine; only the
  harness-spawned node dies, so this is an environment/entropy problem, **not** a test defect.
  Pure-python assertions in the same modules still pass.
- **Treat a local red here as no signal at all.** The authority is CI's **Validate Repository**
  check, which runs the same suite on `ubuntu-latest`. Verified 2026-07-24: the identical tree that
  failed ~17 modules locally passed `Validate Repository` in CI (PR #828).
- Never "fix" a test, or hold a PR, on the strength of a local harness crash — confirm against CI
  first.

## Reading `gh --format json` on the Windows Conductor box (validated 2026-07-25)

**Open the file as UTF-8 explicitly, or Python decodes it as cp1252 and dies.** Piping
`gh project item-list … --format json` (or any `gh` JSON output) to a file and reading it back with
plain `open(path)` fails on this box:

```
UnicodeDecodeError: 'charmap' codec can't decode byte 0x9d in position 20770
```

Nothing is wrong with the data — Python's default encoding on Windows is cp1252, and FFC issue
titles, board item titles and PR bodies routinely contain em-dashes, arrows and smart quotes.
Always:

```python
json.load(open(path, encoding="utf-8"))
```

Same for `pathlib.Path(p).read_text(encoding="utf-8")` and any `write_text`. This costs one failed
call every time it is rediscovered, which has now happened more than once.

## `git cat-file` / `git show` with a `.github/…` path needs `MSYS_NO_PATHCONV=1` (validated 2026-07-25)

**git-bash rewrites a `rev:path` argument when the path starts with a dot.** Reading a file out of a
branch without checking it out:

```
$ git cat-file -p "origin/claude/some-branch:.github/workflows/742-x.yml"
fatal: Not a valid object name origin\claude\some-branch;.github\workflows\742-x.yml
```

MSYS path conversion turned the `:` into `;` and every `/` into `\`. The tell is the mangled path in
the error — the ref is fine. It is **the leading dot in the path** that trips the heuristic, not the
slashes in the branch name: `origin/main:docs/foo.md` works on the same command line and
`origin/branch:.github/…` does not, so this looks like it works right up until you touch a workflow
file. Prefix the command:

```bash
MSYS_NO_PATHCONV=1 git cat-file -p "origin/<branch>:.github/workflows/<file>.yml"
```

## `gh api` list endpoints silently truncate at `per_page` — paginate before concluding "absent"

`per_page=100` returns 100 items and **no warning** that more exist. On 2026-07-25 this produced a
false alarm mid-run: a just-merged workflow appeared to be unregistered because the repo had
`total_count: 105` workflows and the 105th was on page 2. The near-miss is the shape to remember —
the conclusion "the alerter did not register" was about to be reported as a defect.

Two habits, both cheap:

- Add `--paginate` to any `gh api` list call whose result feeds a **negative** conclusion ("X is
  missing", "nothing is pending", "zero failures").
- When an endpoint returns `total_count`, compare it against the length of what you actually got
  before drawing a conclusion from the absence of something.

This is the same class as the `#719` log tail needing `--paginate` to find the true run number.

## On a merge-queue repo, `autoMergeRequest` is always `null` — `mergeQueueEntry` is the proof

`main` here is governed by a merge queue, and that changes which field records an enqueue.
`gh pr merge --auto` succeeds, prints only the advisory

```
! The merge strategy for main is set by the merge queue
```

and then **`autoMergeRequest` stays `null` forever** — the PR is in the queue, not in auto-merge.
Reading that `null` as "the enqueue failed" is wrong, and on 2026-07-29 it nearly caused a duplicate
merge attempt; the second `gh pr merge` answered `Pull request … is already queued to merge`, which
is what revealed the mistake.

Confirm with `mergeQueueEntry` instead:

```bash
gh api graphql -f query='{repository(owner:"FreeForCharity",name:"FFC-Cloudflare-Automation"){
  pullRequest(number:905){ mergeQueueEntry{ position state enqueuedAt } }}}'
# → {"position":1,"state":"AWAITING_CHECKS","enqueuedAt":"2026-07-29T13:10:06Z"}
```

Note the advisory goes to **stderr and the command still exits 0**, so a `>/dev/null` wrapper hides
the one hint that the queue — not auto-merge — took the request.

This is the third instance of the same underlying rule already in this file: **confirm a GitHub
write by re-reading the state it should have changed, and make sure you re-read the _right_ field.**
The gate-approval note above says don't trust the POST body; this says don't trust the field that
would have been correct on a non-queue repo. Both fail the same way — a truthful-looking negative.

## Narrowing a workflow to a read lane surfaces the writes that were riding on the old credential

**Before moving a workflow to a `*-prod-read` environment, enumerate what it writes.** Validated the
hard way on 2026-07-29 (#834): `726` was moved off the gated `github-prod` onto `github-prod-read`,
and its Key Vault step exports the **read-scoped** PAT to `GITHUB_ENV` as `GH_TOKEN` — which then
applies to every later step, including the one that **updates the rolling tracking issue**. The
org-wide audit passed and the run died on the last step:

```
Updating issue #667
failed to update .../issues/667: GraphQL: Resource not accessible by
personal access token (updateIssue)
```

Searching an issue and editing one are different permissions, so the failure only appears at the
write. That write had worked for months purely because the gated lane happened to hand it a
**writer** PAT — the narrowing did not break it so much as reveal it.

The fix is the pattern `737` already uses: an own-repo issue write belongs on the **ambient**
`GITHUB_TOKEN`, not on a Key Vault credential. A step-level `env:` beats `GITHUB_ENV`, so the read
step keeps the PAT and only the write falls back:

```yaml
- name: Open / update tracking issue
  env:
    GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
  run: |
    gh issue edit "$existing" --repo "$repo" --body "$body"
```

The job must declare `issues: write` (726 already did). Corollary worth keeping: **a workflow's own
error message states what its author assumed, not what the repo can do.** 726's preflight asserted
"These OIDC identifiers are ENVIRONMENT secrets — a repo Variable does not satisfy a `secrets.*`
reference." True about `secrets.*`, and a false diagnosis: the identifiers were already repo
Variables and the correct fix was to reference them as `vars.*`. That message was escalated to a
human as a provisioning request before anyone checked `gh api repos/<r>/actions/variables`.

## The Conductor cannot `--request-changes` on a cloud-worker PR

Every cloud-worker PR in this org is authored by `clarkemoyer`, and the Conductor is authenticated
as the same account, so GitHub refuses the review:

```
failed to create review: GraphQL: Review Can not request changes on your own
pull request (addPullRequestReview)
```

This is not a scope problem and adding permissions will not fix it. Post blocking feedback with
`gh pr comment <n> --body-file <f>` instead, and simply do not promote the PR out of draft until the
finding is addressed — leaving it draft is what actually holds the merge, not the review state.

## Board & PR-creation env facts (validated 2026-07-24)

- **The public "Agentic OS" board (org project #9) has NO automation.** Its only enabled built-in
  workflow is `Auto-add sub-issues to project`; label/item auto-add is absent, and `Item closed` and
  `Pull request merged` are **disabled**. So **neither issues nor PRs auto-add, and statuses never
  self-update** — every item and every status is placed by hand. Do not assume a new `agentic-os`
  issue reached the board. Check:
  ```bash
  gh api graphql -f query='query{organization(login:"FreeForCharity"){projectV2(number:9){workflows(first:20){nodes{name enabled}}}}}'
  ```
- **Push the branch before opening the PR.** On 2026-07-24, `POST /repos/…/pulls` returned **HTTP
  500 with an empty body** for >20 minutes, across **multiple FFC repos**
  (`FFC-IN-freeforcharity.org` and `FFC-Cloudflare-Automation`) and all three clients (`gh`, REST,
  GitHub MCP), with full and minimal bodies alike. Not auth, not rate limit, and githubstatus.com
  reported all-operational throughout — so treat "GitHub is green" as no guarantee that PR creation
  works. Because the work was committed and pushed first, nothing was lost: the branches simply wait
  for their PRs. **Adopt commit-and-push-first as the default order.**

## Running & authorizing GitHub Actions workflows (IMPORTANT)

In a self-hosted/local remote environment the `gh` CLI is typically pre-authenticated — run
`gh auth status` to confirm (and `gh auth login` if not). When available it acts as a real user
(e.g. `clarkemoyer`) with `workflow` + `repo` scopes. **Prefer `gh` for anything Actions-related.**

**Update (validated 2026-07-06):** the MCP GitHub App installation **now has `actions: write`** —
`actions_run_trigger` with `method: run_workflow` successfully dispatched 101/113/209/210 from the
web sandbox (`204` queued). Two gotchas: (a) **all dispatch inputs must be strings** — a numeric
value (e.g. `issue_number: 609`) fails with `422 Invalid value for input`; pass `"609"`. (b) MCP
still **cannot approve environment deployment gates** (no `pending_deployments` tool, and direct
REST stays 403 in the sandbox), so gated jobs sit at `status: waiting` until a human reviewer
(`clarkemoyer`) approves. The paragraph below is retained as history in case scopes regress:

> Previously (pre-2026-07): the App installation lacked `actions: write`, so
> `actions_run_trigger`/`run_workflow` returned `403 Resource not accessible by integration`, and
> the only sandbox trigger path was `issues`-event workflows. (MCP has always been fine for PRs,
> issues, comments, reviews.)

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
- The **MCP** GitHub tools are the working channel in the sandbox (scoped to this repo). As of
  2026-07-06 they **can dispatch `workflow_dispatch` workflows** (see the update above) and
  create/assign issues, open PRs, push files, comment, and read Actions runs/logs.

Net effect in the web sandbox: you **can dispatch workflows via MCP** (string inputs only) and
trigger any `issues`-event workflow (e.g. Website Provision) by creating + assigning an issue via
MCP — but you still **cannot approve an environment gate**; a human reviewer (`clarkemoyer`)
approves those. See the next section.

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

**Do not try to confirm the approval from the POST's own output — confirm by re-reading the run.**
The response shape does not match the `pending_deployments` GET, so even an array-aware filter
errors. On 2026-07-25 this exact command:

```bash
gh api -X POST …/pending_deployments -F "environment_ids[]=$ENV_ID" -f state=approved \
  --jq '.[0] | "\(.status) \(.environment.name)"'
```

printed `expected an object but got: string ("github-prod")` — while the approval had **succeeded**
and the run moved `waiting → in_progress`. Same trap as the read-after-write note above: the failure
was in the confirmation, not the action, and reacting to it would mean re-approving an
already-approved gate. Drop the `--jq` on the POST and verify with:

```bash
gh api repos/FreeForCharity/FFC-Cloudflare-Automation/actions/runs/$RUN_ID --jq '.status'
```

**"Drop the `--jq`" does not mean "discard the output" — read it for errors, just never for
confirmation.** A conductor run on 2026-07-25 took the rule above one step too far and sent the
approval with `> /dev/null 2>&1`, then reported it approved on the strength of the rule. It had not
been: the command used `-f "environment_ids[]=$ENV_ID"` instead of `-F`, and `-f` sends `["<id>"]` —
an array of **strings** — where the API requires integers. The POST rejected it, the run stayed at
`status: waiting`, and the one place that said so had been routed to `/dev/null`.

So the two halves are not interchangeable:

- the **POST output** is the only place a _rejected_ approval reports itself;
- the **run's `status`** is the only trustworthy sign an _accepted_ one took effect.

Use `-F` for `environment_ids[]` (typed — `-f` is string-only and fails this endpoint silently from
the caller's point of view), keep the POST's stderr, and still confirm from the run.

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

**WHMCS admin UI paths** (for direct links in issue comments/replies — the admin directory is
renamed): `https://freeforcharity.org/hub/globaladmin/` — e.g. `clientssummary.php?userid=<id>`,
`clientsprofile.php?userid=<id>`, `clientsservices.php?userid=<id>`,
`orders.php?action=view&id=<orderid>`.

**Where application answers live (validated 2026-07-07):** the charity-onboarding application's
answers (org name, requested domain, mission, contacts) are **product custom fields** on the
onboarding service — NOT client-level fields. Client `companyname` stays empty and
`GetClientsDetails` returns client custom fields without names; use `GetClientsProducts` (workflow
219 exports it) to read the application with field names.

**Identify an application by DOMAIN, not by the triage name (validated 2026-07-07).** The masked
triage tables (209/210) show the **applicant's personal first name**, not the org — the org name is
only inside the mission text. Matching on a name-initial guessed from the org name will find the
wrong charity. To find "the application for `<domain>`" use **workflow 221 (WHMCS Application
Search)** — it sweeps `GetClientsProducts` and returns the matching client id + readable fields.
Fastest confirm from the sandbox once `az` is authed (see below): read `GetClientsProducts` for a
`clientid` directly via APIM. See `docs/restored-radiance-first-fullchain-retro.md`.

### Azure CLI from the sandbox (device-auth) — direct WHMCS reads + Azure inspection

`az` is **not preinstalled**, but you can install it into a venv and device-auth as the admin
(`clarkemoyer@freeforcharity.org`), which unblocks direct WHMCS queries (KV creds → APIM) and Azure
AD reads:

```bash
python3 -m venv azvenv && ./azvenv/bin/pip install -q azure-cli
export AZURE_CONFIG_DIR="$PWD/azconfig"
./azvenv/bin/az login --use-device-code --allow-no-subscriptions   # give the code to the user
```

- **Reads work:** `az ad app federated-credential list`, `az keyvault secret show`, and querying
  live WHMCS by fetching the `read-all-ffc-whmcs-*` secrets and POSTing to the APIM gateway with the
  `Ocp-Apim-Subscription-Key` header (no gate needed — this is how client 419 was confirmed).
- **Azure AD IAM writes are BLOCKED by the harness auto-mode classifier** (creating/updating a
  federated credential is high-severity). Provide the exact `az` command for a human to run, or ask
  for a Bash allow-rule. Full identity inventory + repair commands:
  **`docs/azure-oidc-federated-credentials.md`** — including the **`m365-prod` credential-subject
  typo** (`FFC-Cloudflare-Automation-`, trailing hyphen) that breaks every M365 job with
  `AADSTS700213`, and the `whmcs-prod-read` setup.

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

## Candid (GuideStar) — MCP + API workflows

- **Interactive:** the repo `.mcp.json` registers Candid's official remote MCP server
  (`https://mcp.candid.org/mcp`, OAuth with a Candid account — run `/mcp` to connect). Tools: org
  search (name/EIN/seal level), org identification, knowledge search, PCS taxonomy matching. Note:
  Claude Code on the web only sees org-level connectors, so this entry helps local/desktop sessions.
- **Workflows:** `801-candid-charity-check.yml` (validate 501(c)(3)/Pub78/BMF/OFAC by EIN) and
  `802-candid-essentials-search.yml` (find profile + transparency-seal level). Both read-only,
  environment `candid-prod-read` (no approval gate), keys from KV via
  `.github/actions/candid-keys-from-kv` (`Subscription-Key` header, host allowlist
  `api.candid.org`).
- **Provisioning status:** scaffolding is inert until the one-time setup in
  `docs/candid-api-and-mcp.md` is done (Candid developer keys → KV secrets
  `read-all-ffc-candid-{charity-check,essentials}-key`, environment `candid-prod-read` with the
  `READ_ALL_FFC_AZURE_*` secrets, federated credential for `ffc-admin-kv-reader`).
- **No write API:** the annual Candid Platinum profile update stays a manual web form — the
  paste-sheet automation is issue #493.
