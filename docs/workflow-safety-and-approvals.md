# Workflow safety & approvals

This is the single reference for **how safe each workflow is to run**, what stops an accidental live
change, and what guarantees the automation gives you. Read this before running anything that might
write to Cloudflare, WHMCS, M365, GitHub, or a donor/charity record.

It complements
[github-actions-environments-and-secrets.md](github-actions-environments-and-secrets.md) (which
covers _how secrets are configured_) — this doc covers _what protects you at run time_.

## The safety model: five independent layers

A live change generally has to get past several of these, not just one:

1. **Read vs. write split.** Read-only workflows load `*-read` / reader-scope credentials and cannot
   mutate anything. Write workflows load `*-write` / writer-scope credentials.
2. **Environment approval gates.** Any job with `environment: cloudflare-prod-write`, `whmcs-prod`,
   `github-prod`, or `m365-prod` pauses at `status: waiting` until a **required reviewer**
   (currently `clarkemoyer`) approves the deployment. Note `whmcs-prod` gates **every** WHMCS job —
   including read-only exports — so even a triage run waits for approval.
3. **`dry_run` defaults to preview.** The granular write workflows take a `dry_run` input that
   **defaults to `true`**. A dry run returns a preview (e.g. redacted JSON of what _would_ be sent)
   and performs **no** mutation. You must explicitly pass `dry_run=false` to go live.
4. **Typed confirmation for the highest-stakes actions.** The riskiest workflows require you to type
   an exact value (e.g. domain registration needs `mode=execute-register` **and** `confirm_domain`
   to exactly match the domain).
5. **Concurrency serialization.** Stateful workflows declare a `concurrency` group with
   `cancel-in-progress: false`, so a second dispatch **queues** behind the first instead of racing
   it (no two cutovers / imports running over each other).

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

Legend — **Reads**: no external mutation. **Writes (dry-run default)**: mutates only when you set
`dry_run=false`. **Writes (gated)**: mutates when run, protected by the environment approval gate
and/or a typed confirmation. ✅ = an environment approval gate applies.

| #     | Workflow                          | Level                     | Approval env                     | Extra guard                                               |
| ----- | --------------------------------- | ------------------------- | -------------------------------- | --------------------------------------------------------- |
| 01    | Domain - Status                   | Reads                     | cloudflare-prod-read / m365-prod | —                                                         |
| 02    | Domain - Add to CF + WHMCS NS     | Writes (gated)            | ✅ cloudflare-prod-write / whmcs | —                                                         |
| 03    | Domain - Enforce Standard         | Writes (dry-run default)  | ✅ cloudflare-prod-write / m365  | `dry_run` (default true)                                  |
| 04    | Domain - Export Inventory         | Reads                     | read envs (all four sources)     | —                                                         |
| 05    | DNS - Manage Record               | Writes (dry-run default)  | ✅ cloudflare-prod-write         | `dry_run` (default true)                                  |
| 06    | DNS - Enforce Standard (DNS-only) | Writes (dry-run default)  | ✅ cloudflare-prod-write         | `dry_run` (default true)                                  |
| 07    | DNS - Audit Compliance            | Reads                     | cloudflare-prod-read             | —                                                         |
| 08    | DNS - Export Zones                | Reads                     | cloudflare-prod-read             | —                                                         |
| 09    | DNS - Create Zone                 | Writes (gated)            | ✅ cloudflare-prod-write         | —                                                         |
| 10/16 | DNS - Create Redirect Rule        | Writes (dry-run default)  | ✅ cloudflare-prod-write         | `dry_run` (default true)                                  |
| 11    | DNS - Create Zone (admin)         | Writes (gated)            | ✅ cloudflare-prod-write         | —                                                         |
| 12    | Domain - Registrar Search/Reg.    | Writes (gated)            | ✅ cloudflare-prod-write         | `mode` (default `check`)                                  |
| 13    | Registrar - API Access Check      | Reads                     | cloudflare-prod-write\*          | never charges                                             |
| 14    | Domain - Transfer Preflight       | Reads                     | whmcs-prod (gated)               | —                                                         |
| 15    | Website - Provision               | Writes (gated)            | ✅ cloudflare-prod-write / gh    | `repo` chained behind `dns` approval                      |
| 16    | Domain - EPP/Auth Probe           | Writes (dry-run default)  | whmcs-prod                       | `dry-run` vs `execute`                                    |
| 17    | DNS - Bulk Replace A-IP           | Writes (gated)            | ✅ cloudflare-prod-write         | high blast radius — see below                             |
| 18    | Bulk Staging CNAME → GH Pages     | Writes (dry-run default)  | ✅ cloudflare-prod-write         | `dry_run` (default true)                                  |
| 19    | Bulk Cutover staging → apex       | Writes (dry-run default)  | ✅ cloudflare-prod-write / gh    | `dry_run` (default true); serialized                      |
| 20    | Domain - Registrar Register       | Writes (gated)            | ✅ cloudflare-prod-write         | `mode=execute-register` + `confirm_domain`                |
| 24    | WHMCS - Domain Lock               | Writes (gated)            | ✅ whmcs-prod                    | —                                                         |
| 25    | Domain - Post-Transfer Verify     | Reads                     | cloudflare-prod-read             | —                                                         |
| 27    | FFC-EX - Clone Deploy             | Writes (gated)            | ✅ github-prod                   | opens a PR (never pushes default); serialized             |
| 30–32 | WHMCS - Exports                   | Reads                     | ✅ whmcs-prod                    | gated only by the env approval                            |
| 33    | WHMCS → Zeffy Import Draft        | Reads (builds a file)     | ✅ whmcs-prod                    | output is a draft; serialized                             |
| 34    | WHMCS - Charity Onboard           | Writes (dry-run default)  | ✅ whmcs-prod                    | `dry_run` (default true); idempotent                      |
| 35/36 | WHMCS - Open / Issue→Ticket       | Writes (gated)            | ✅ whmcs-prod                    | one-way GitHub→WHMCS                                      |
| 37/38 | WHMCS - Tickets Export / Triage   | Reads                     | ✅ whmcs-prod                    | summary masks PII                                         |
| 39    | WHMCS - Ticket Respond            | Writes (dry-run default)  | ✅ whmcs-prod                    | `dry_run` (default true); live reply needs admin username |
| 41    | WHMCS - Orders Triage             | Reads                     | ✅ whmcs-prod                    | summary masks PII                                         |
| 42    | WHMCS - Order Update              | Writes (dry-run default)  | ✅ whmcs-prod                    | `dry_run` (default true); one order at a time             |
| 43    | WHMCS - Product Add               | Writes (dry-run default)  | ✅ whmcs-prod                    | `dry_run` (default true); idempotent                      |
| 44–46 | Zeffy - Exports                   | Reads                     | zeffy-prod                       | PII masked; never `-IncludePii`                           |
| 95    | Repo - Rulesets Drift Audit       | Reads                     | —                                | report only                                               |
| 98    | Repo - Add Collaborator           | Writes (**live default**) | ✅ github-prod                   | ⚠️ `dry_run` defaults to **false**                        |

\* Workflow 13 loads a write-scope token only to _probe_ Registrar permissions; it never registers
or charges.

> **Exception to call out:** **98. Repo - Add Collaborator** is the one write workflow whose
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
5. For **bulk DNS (17/18/19)**: cut over in the staging→apex order, verify a single domain
   end-to-end before running the full list, and remember runs are **serialized** — a second dispatch
   queues, it does not cancel the first.

## What is _not_ protected

- The environment approval gate protects against an _unapproved_ live run, not a _wrong_ approved
  one. The dry-run preview is your check against approving a mistake.
- `dry_run=false` runs do real work immediately after approval — there is no second confirmation for
  most WHMCS/DNS writes (registration #20 is the exception, with its typed `confirm_domain`).
