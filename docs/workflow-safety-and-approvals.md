# Workflow safety & approvals

This is the single reference for **how safe each workflow is to run**, what stops an accidental live
change, and what guarantees the automation gives you. Read this before running anything that might
write to Cloudflare, WHMCS, M365, GitHub, or a donor/charity record.

It complements
[github-actions-environments-and-secrets.md](github-actions-environments-and-secrets.md) (which
covers _how secrets are configured_) — this doc covers _what protects you at run time_.

> **Maintenance.** The per-workflow table is kept honest by CI
> (`scripts/check-workflow-doc-consistency.py`): a new or renamed workflow that isn't reflected here
> fails the build. Owner: repo maintainers. Last hand-reconciled against the workflow set:
> **2026-06-30**.

## The safety model: five independent layers

A live change generally has to get past several of these, not just one:

1. **Read vs. write split.** Read-only workflows load `*-read` / reader-scope credentials and cannot
   mutate anything. Write workflows load `*-write` / writer-scope credentials. ("Read" means no data
   change — but a read run still calls the live API, so it counts against rate limits and appears in
   provider logs.)
2. **Environment approval gates.** A job whose `environment` is configured with **required
   reviewers** pauses at `status: waiting` until a reviewer (currently `clarkemoyer`) approves the
   deployment. The live config was **audited on 2026-06-30** by the read-only workflow **730. Repo -
   Audit Environment Approval Gates [Repo]** (reads the protection rules with `GITHUB_TOKEN`). The
   environments that require a reviewer are: **`cloudflare-prod-write`**, **`whmcs-prod`**,
   **`github-prod`**, **`m365-prod`**, and **`wpmudev-prod`** (plus a bare **`cloudflare-prod`**
   that no workflow currently uses). The environments with **no** reviewer — runs proceed without
   pausing — are **`cloudflare-prod-read`** and **`zeffy-prod`**. Because `whmcs-prod`, `m365-prod`,
   and `wpmudev-prod` are gated at the environment level, they gate **every** job that uses them —
   including read-only exports and triage (e.g. the cross-source inventory 104, the M365
   list/preflight reads 301–303, and the WPMUDEV export 601) — so even a read run waits for
   approval. Re-run workflow 730 after any change in _Settings → Environments_ to refresh this list.
3. **`dry_run` defaults to preview.** The granular write workflows take a `dry_run` input that
   **defaults to `true`**. A dry run returns a preview (e.g. redacted JSON of what _would_ be sent)
   and performs **no** mutation. You must explicitly pass `dry_run=false` to go live.
4. **Typed confirmation for the highest-stakes actions.** The riskiest workflows require you to type
   an exact value (e.g. domain registration needs `mode=execute-register` **and** `confirm_domain`
   to exactly match the domain).
5. **Concurrency serialization.** The stateful workflows declare a `concurrency` group with
   `cancel-in-progress: false` — **112** / **119** (bulk DNS), **120** (bulk cutover), **702**
   (clone-deploy), and **213** (WHMCS → Zeffy import) — so a second dispatch **queues** behind the
   first instead of racing it.

## Credential & data guarantees (always on)

These hold for every run, independent of the layers above:

- **No stored secrets.** Credentials are fetched from Azure Key Vault at run time via OIDC
  (`*-secrets-from-kv` / `*-tokens-from-kv` composite actions). Nothing sensitive lives in the repo
  or in GitHub environment secrets; rotation happens in Key Vault with no repo change. Secrets are
  masked **line-by-line** before use and written to `$GITHUB_ENV` with a randomized heredoc
  delimiter, so a value can't leak or inject extra variables.
- **WHMCS credential host allowlist.** WHMCS API calls refuse to send the identifier/secret + APIM
  key anywhere except `apim-ffc-gateway-prod.azure-api.net` or `freeforcharity.org`, so a workflow
  input can't redirect the credential to an arbitrary host.
- **No donor PII in public artifacts.** The Zeffy exports (44–46) run on this **public** repo and
  mask donor PII by default; only campaign data (which has none) is exported in full. The
  `-IncludePii` switch exists for local/private use only and **no workflow passes it**.
- **Pinned third-party actions.** Non-first-party actions are pinned to commit SHAs, so a
  compromised upstream tag can't inject code into a run.

## Reference: per-workflow safety level

Numbers are the **display numbers** shown in the Actions UI (the `name:` prefix), which differ from
the workflow file names. Legend — **Reads**: no external mutation. **Writes (dry-run default)**:
mutates only when you set `dry_run=false`. **Writes (gated)**: mutates when run, protected by the
environment approval gate and/or a typed confirmation. ✅ = an approval-gated environment
(`cloudflare-prod-write`, `whmcs-prod`, `github-prod`, `m365-prod`, or `wpmudev-prod`) applies — so
the run pauses for approval, even if the action itself only reads.

| #       | Workflow                                  | Level                     | Approval env                                               | Extra guard                                                                         |
| ------- | ----------------------------------------- | ------------------------- | ---------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| 101     | Domain - Status                           | Reads                     | cloudflare-prod-read / ✅ m365-prod                        | M365 job waits on m365-prod approval                                                |
| 102     | Domain - Add to FFC CF + WHMCS NS         | Writes (gated)            | ✅ cloudflare-prod-write / ✅ whmcs-prod                   | —                                                                                   |
| 103     | Domain - Enforce Standard (Apex + M365)   | Writes (dry-run default)  | ✅ cloudflare-prod-write / ✅ m365-prod                    | `dry_run` (default true)                                                            |
| 104     | Domain - Export Inventory                 | Reads                     | ✅ whmcs-prod / ✅ m365-prod / ✅ wpmudev-prod (+ cf-read) | waits on whmcs-prod, m365-prod, and wpmudev-prod approvals                          |
| 105     | DNS - Manage Record                       | Writes (dry-run default)  | ✅ cloudflare-prod-write                                   | `dry_run` (default true); issue-label trig                                          |
| 106     | DNS - Enforce Standard (DNS-only)         | Writes (dry-run default)  | ✅ cloudflare-prod-write                                   | `dry_run` (default true)                                                            |
| 107     | DNS - Audit Compliance                    | Reads                     | cloudflare-prod-read                                       | —                                                                                   |
| 108     | DNS - Export Cloudflare Zones             | Reads                     | cloudflare-prod-read                                       | —                                                                                   |
| 110     | DNS - Create Zone (Admin)                 | Writes (gated)            | ✅ cloudflare-prod-write                                   | —                                                                                   |
| 111     | DNS - Create Redirect Rule                | Writes (dry-run default)  | ✅ cloudflare-prod-write                                   | `dry_run` (default true)                                                            |
| 109     | DNS - Export All Records                  | Reads                     | cloudflare-prod-read                                       | —                                                                                   |
| 113     | Domain - Registrar Search/Check/Reg.      | Writes (gated)            | ✅ cloudflare-prod-write                                   | `mode` (default `check`); register needs `mode=execute-register` + `confirm_domain` |
| 114     | Domain - Validate Registrar API Access    | Reads                     | ✅ cloudflare-prod-write                                   | never charges                                                                       |
| 115     | Domain - Transfer Readiness Preflight     | Reads                     | ✅ whmcs-prod                                              | —                                                                                   |
| 701     | Website - Provision                       | Writes (gated)            | ✅ cloudflare-prod-write / ✅ github-prod                  | `repo` chained behind `dns` approval                                                |
| 116     | Domain - Transfer EPP/Auth Probe          | Writes (dry-run default)  | ✅ whmcs-prod                                              | `dry-run` vs `execute`                                                              |
| 112     | DNS - Bulk Replace A-record IP            | Writes (gated)            | ✅ cloudflare-prod-write                                   | high blast radius; serialized                                                       |
| 119     | DNS - Bulk Staging CNAME → GH Pages       | Writes (dry-run default)  | ✅ cloudflare-prod-write                                   | `dry_run` (default true); serialized                                                |
| 120     | DNS + GH Pages - Bulk Cutover             | Writes (dry-run default)  | ✅ cloudflare-prod-write / ✅ github-prod                  | `dry_run` (default true); serialized                                                |
| 301     | M365 - Domain Preflight                   | Reads                     | cloudflare-prod-read / ✅ m365-prod                        | M365 job waits on m365-prod approval                                                |
| 302     | M365 - List Tenant Domains                | Reads                     | ✅ m365-prod                                               | waits on m365-prod approval                                                         |
| 303     | M365 - Domain Status + DKIM (Toolbox)     | Reads                     | ✅ m365-prod                                               | read-oriented toolbox; waits on m365-prod approval                                  |
| 304     | M365 - Enable DKIM                        | Writes (gated)            | ✅ cloudflare-prod-write / ✅ m365-prod                    | —                                                                                   |
| 305     | M365 - Add Tenant Domain (Admin)          | Writes (dry-run default)  | ✅ m365-prod                                               | `dry_run` (default true); also gated by m365-prod approval                          |
| 117     | Domain - Post-Transfer Verification       | Reads                     | cloudflare-prod-read                                       | —                                                                                   |
| 118     | Domain - Registrar Lock / Unlock          | Writes (dry-run default)  | ✅ whmcs-prod                                              | `dry_run` (default true)                                                            |
| 702     | Domain - Deploy Static Clone (FFC-EX)     | Writes (gated)            | ✅ github-prod                                             | opens a draft PR (never pushes); serialized                                         |
| 201–203 | WHMCS - Exports (domains/products/pmts)   | Reads                     | ✅ whmcs-prod                                              | gated only by the env approval                                                      |
| 213     | WHMCS → Zeffy Payments Import (Draft)     | Reads (builds a file)     | ✅ whmcs-prod                                              | output is a draft; serialized                                                       |
| 204     | WHMCS - Charity Onboard                   | Writes (dry-run default)  | ✅ whmcs-prod                                              | `dry_run` (default true); idempotent                                                |
| 205     | WHMCS - Open Ticket (manual)              | Writes (gated)            | ✅ whmcs-prod                                              | one-way GitHub→WHMCS                                                                |
| 206     | WHMCS - Issue to Ticket                   | Writes (gated)            | ✅ whmcs-prod                                              | one-way GitHub→WHMCS                                                                |
| 208     | WHMCS - Export Tickets                    | Reads                     | ✅ whmcs-prod                                              | —                                                                                   |
| 209     | WHMCS - Tickets Triage                    | Reads                     | ✅ whmcs-prod                                              | summary masks PII                                                                   |
| 207     | WHMCS - Ticket Respond                    | Writes (dry-run default)  | ✅ whmcs-prod                                              | `dry_run` (default true); live reply needs admin username                           |
| 601     | WPMUDEV - Export Sites/Domains            | Reads                     | ✅ wpmudev-prod                                            | waits on wpmudev-prod approval                                                      |
| 210     | WHMCS - Orders Triage                     | Reads                     | ✅ whmcs-prod                                              | summary masks PII                                                                   |
| 211     | WHMCS - Order Update                      | Writes (dry-run default)  | ✅ whmcs-prod                                              | `dry_run` (default true); one order at a time                                       |
| 212     | WHMCS - Product Add                       | Writes (dry-run default)  | ✅ whmcs-prod                                              | `dry_run` (default true); idempotent                                                |
| 214     | WHMCS - Clients Metrics (aggregate)       | Reads                     | ✅ whmcs-prod                                              | aggregate counts only — no PII in artifact or summary                               |
| 401–403 | Zeffy - Exports (campaigns/pmts/contacts) | Reads                     | zeffy-prod                                                 | PII masked; never `-IncludePii`                                                     |
| 306     | Discover - Uncaptured Comms (M365)        | Reads                     | ✅ m365-prod                                               | PII masked; dispatch-only; org mailboxes only; waits on m365-prod approval          |
| 501     | Google - API Smoke (GA4 connectivity)     | Reads                     | google-prod-read                                           | read-only; fails closed; reusable via `workflow_call`                               |
| 502     | Google - Analytics Report (GA4 -> JSON)   | Reads                     | google-prod-read                                           | delivers JSON to ffcadmin via PR (CBM_TOKEN); PII-safe aggregates                   |
| 503     | Google - GTM Provision (per-charity)      | Writes (dry-run default)  | ✅ google-prod-write                                       | dry_run default true; seeds GA4/Clarity/Meta; delegates POC access                  |
| 726     | Repo - Rulesets + Settings Drift Audit    | Reads                     | —                                                          | report only                                                                         |
| 729     | Repo - Add Collaborator                   | Writes (**live default**) | ✅ github-prod                                             | ⚠️ `dry_run` defaults to **false**                                                  |
| 730     | Repo - Audit Environment Approval Gates   | Reads                     | —                                                          | report only (environment reviewer config)                                           |

> **Exception to call out:** **729. Repo - Add Collaborator** is the one write workflow whose
> `dry_run` defaults to **`false`** (it runs live by default). It's low-risk (adding a repo
> collaborator, gated by `github-prod` approval), but don't assume the "preview-by-default" rule
> applies to it.

## Before you flip `dry_run=false`

1. **Run it dry first** and read the preview. For WHMCS writes the preview is redacted JSON of the
   exact request; for DNS it's the planned record changes.
2. **Confirm the target** — domain, client id, order id, record name — matches what you intend. The
   dry preview echoes these.
3. **Check idempotency** where noted (34, 43 dedupe by name/email; re-running is safe and reports
   `existing`/`skipped`).
4. **Dispatch with `dry_run=false`**, then **approve the environment gate** when the run pauses at
   `status: waiting` (a reviewer must approve `*-write` / `whmcs-prod` / `github-prod`).
5. For **bulk DNS (112/119/120)**: cut over in the staging→apex order, and verify a single domain
   end-to-end before running the full list. All three are now serialized (a second dispatch queues
   behind the first), but the **blast radius is large** — **112** rewrites A records across **all**
   zones, and **119/120** default to the full ~13-domain FFC-EX list. Read the dry-run preview
   first.

## What is _not_ protected

- The environment approval gate protects against an _unapproved_ live run, not a _wrong_ approved
  one. The dry-run preview is your check against approving a mistake.
- `dry_run=false` runs do real work immediately after approval — there is no second confirmation for
  most WHMCS/DNS writes (registration **#12** is the exception, with its typed `confirm_domain`).
