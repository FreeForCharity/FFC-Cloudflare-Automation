# Azure OIDC federated credentials (identity reference)

Every workflow authenticates to Azure with **GitHub OIDC → Azure AD federated credentials** — no
client secret is ever stored. A GitHub Actions job presents a token whose `subject` is
`repo:<owner>/<repo>:environment:<env>` (or `:ref:refs/heads/<branch>`), and Azure AD matches it
against a **federated identity credential** registered on the target app. The match is
**exact-string**: one wrong character in the subject and the exchange fails with
`AADSTS700213: No matching federated identity record found…`.

This doc is the inventory of the three FFC app registrations, which environment maps to which app,
and the setup/repair recipes. All IDs below are **non-secret GUIDs** (app/object/tenant ids are not
credentials — the same convention as `vars.*_AZURE_KV_CLIENT_ID`).

- **Tenant:** `80c64bf2-fa5b-425c-9a5a-1fcf282d3274` (`freeforcharity.org`)

## App registrations

| App (display name)        | appId (client id)                      | object id                              | Role                                                             |
| ------------------------- | -------------------------------------- | -------------------------------------- | ---------------------------------------------------------------- |
| `ffc-admin-kv-reader`     | `db736be6-6776-4cbd-9f16-10f76de3a3c1` | `79a123d8-2f45-4925-a550-bbd849399daf` | Read identity — `read-all-*` KV secrets (`READ_ALL_*` OIDC vars) |
| `ffc-admin-kv-writer`     | `d42c3d6a-8fe9-4ac7-a776-74bac8a19642` | `be39a762-1727-49aa-998f-7af1a5379894` | Write identity — `wr-all-*` KV secrets (`WR_ALL_*` OIDC vars)    |
| `FFC Microsoft Graph CLI` | `8fc12b52-4f88-43be-ba7c-d2ee9759c212` | `8e54bc08-bd4a-4fc9-ab7b-e6f0d420b6d7` | M365 Graph identity (`FFC_AZURE_CLIENT_ID` env secret)           |

## Environment → app mapping (this repo)

| Environment                                 | App                                  | Notes                                                                               |
| ------------------------------------------- | ------------------------------------ | ----------------------------------------------------------------------------------- |
| `cloudflare-prod-read`                      | kv-reader                            | ungated                                                                             |
| `cloudflare-prod-write` / `cloudflare-prod` | kv-writer                            | gated                                                                               |
| `google-prod-read`                          | kv-reader                            | ungated                                                                             |
| `google-prod-write`                         | kv-writer                            | gated (Google provisioning: 503, 505)                                               |
| `zeffy-prod`                                | kv-writer                            | ungated                                                                             |
| `whmcs-prod`                                | kv-writer                            | gated (WHMCS writes: 102, 116, 118, 204–207, 211, 212, 221)                         |
| `whmcs-prod-read`                           | kv-reader                            | ungated (WHMCS reads: 104, 115, 201–203, 208–210, 213–220) — **applied 2026-07-07** |
| `m365-prod`                                 | Graph CLI (+ kv-reader for KV steps) | gated — **typo fixed 2026-07-07**                                                   |
| `github-prod-read`                          | kv-reader                            | ungated (GitHub reads: 502 `deliver`, 726, 735) — **applied 2026-07-29**            |
| `github-prod`                               | kv-writer                            | gated (GitHub writes)                                                               |
| `candid-prod-read`                          | kv-reader                            | ungated (801, 802) — **federated credential NOT created** (see below)               |
| `fraudlabspro-prod-read`                    | kv-reader                            | ungated (228 `fraud_review`) — **federated credential NOT created** (see below)     |

## ✅ Resolved — `m365-prod` credential subject typo (found & fixed 2026-07-07)

> **Status: APPLIED & VERIFIED 2026-07-07** (issue #625). The Graph CLI credential subject was
> corrected via `az ad app federated-credential update`. Verified green: **101** (`m365` job),
> **301** (Graph login under `m365-prod` + kv-reader login under `cloudflare-prod-read`), and
> **302** — all with the Azure OIDC login succeeding, no `AADSTS700213`. The optional kv-reader
> `…:environment:m365-prod` fallback credential below was **not required** (301's second login runs
> under `cloudflare-prod-read`, which is already credentialed).

Every M365 job (101, 103, 104, 301–305) failed OIDC login with `AADSTS700213`. Root cause: the
`github-oidc-m365-prod` federated credential on the **Graph CLI** app had a **typo in its subject**
— a trailing hyphen on the repo name:

- present: `repo:FreeForCharity/FFC-Cloudflare-Automation-:environment:m365-prod`
- correct: `repo:FreeForCharity/FFC-Cloudflare-Automation:environment:m365-prod`

**Repair** (Graph CLI app — this is the login every m365 job hits first):

```bash
az ad app federated-credential update \
  --id 8e54bc08-bd4a-4fc9-ab7b-e6f0d420b6d7 \
  --federated-credential-id github-oidc-m365-prod \
  --parameters '{"name":"github-oidc-m365-prod","issuer":"https://token.actions.githubusercontent.com","subject":"repo:FreeForCharity/FFC-Cloudflare-Automation:environment:m365-prod","audiences":["api://AzureADTokenExchange"]}'
```

Some m365-prod jobs (e.g. 301) also do a **second** login with the kv-reader (`READ_ALL_*`) under
the same environment. The kv-reader currently has **no** `…:environment:m365-prod` credential, so if
a job still fails after the Graph fix, add it:

```bash
az ad app federated-credential create \
  --id 79a123d8-2f45-4925-a550-bbd849399daf \
  --parameters '{"name":"github-oidc-m365-prod","issuer":"https://token.actions.githubusercontent.com","subject":"repo:FreeForCharity/FFC-Cloudflare-Automation:environment:m365-prod","audiences":["api://AzureADTokenExchange"]}'
```

Verify by re-running **101. Domain - Status** and confirming the m365 job's Azure login succeeds.

## Setup — `whmcs-prod-read` (added 2026-07-07)

> **Status: APPLIED & VERIFIED 2026-07-07** (issue #625). All four steps below are done: the
> kv-reader federated credential exists, both repo Variables are set, the kv-reader holds **Key
> Vault Secrets User** (RBAC) on `kv-ffc-admin-prod-cbm` covering every `read-all-*` secret, and the
> ungated `whmcs-prod-read` environment exists (`protection_rules: []`). Verified green and ungated
> (no approval gate, no `AADSTS700213`): **202** (Export Products), **201** (Export Domains),
> **209** (Tickets Triage). Gate audit **730** re-run green.

The ungated read environment for WHMCS reads needs, one-time:

1. **Federated credential on kv-reader:**
   ```bash
   az ad app federated-credential create \
     --id 79a123d8-2f45-4925-a550-bbd849399daf \
     --parameters '{"name":"github-oidc-whmcs-prod-read","issuer":"https://token.actions.githubusercontent.com","subject":"repo:FreeForCharity/FFC-Cloudflare-Automation:environment:whmcs-prod-read","audiences":["api://AzureADTokenExchange"]}'
   ```
2. **Repo Variables** (Settings → Secrets and variables → Actions → _Variables_):
   `READ_ALL_FFC_AZURE_KV_CLIENT_ID` = `db736be6-6776-4cbd-9f16-10f76de3a3c1`,
   `READ_ALL_FFC_AZURE_TENANT_ID` = `80c64bf2-fa5b-425c-9a5a-1fcf282d3274`.
3. Confirm the kv-reader identity has `Get` on the `read-all-ffc-whmcs-*` KV secrets.
4. Create the `whmcs-prod-read` GitHub environment with **no** required reviewers, then re-run
   **730** to refresh the gate audit.

## `github-prod-read` — the ungated GitHub read lane (#834)

> **Status: PROVISIONED.** The federated credential
> (`gha-FFC-Cloudflare-Automation-github-prod-read`) exists on kv-reader with the correct subject,
> and the environment exists and is ungated. Workflows 502 (`deliver`), 726 and 735 read the OIDC
> identifiers from the **repo Variables** via `vars.*`, so there is nothing per-environment left to
> provision (see step 3). Note the two failure modes are different and easy to confuse: a missing
> **federated credential** gives `AADSTS700213`, while missing **OIDC identifiers** gives an empty
> `client-id` and never reaches Entra at all.

Deliberately **no credential of its own**: the PAT stays in Key Vault and the lane authenticates as
the _reader_ identity over OIDC. That is the point of the lane — the three workflows previously ran
on the gated `github-prod` as the **writer** identity (`wr-all-cbm-github-pat`, the 100+-repo
create/archive/collaborator credential) for work classified `Reads`.

> **Two reference forms exist in this repo, and the choice is the workflow's, not the
> environment's.** A repo Variable does not satisfy a `secrets.*` reference and vice versa — so
> whichever form a workflow uses dictates what must be provisioned.
>
> | Lane                                             | Reference                      | What that requires                                          |
> | ------------------------------------------------ | ------------------------------ | ----------------------------------------------------------- |
> | `whmcs-prod-read` (201, 202, …)                  | `vars.READ_ALL_FFC_AZURE_*`    | **repo Variables** — set once, resolve in every environment |
> | **`github-prod-read`** (502 `deliver`, 726, 735) | `vars.READ_ALL_FFC_AZURE_*`    | **repo Variables** — nothing per-environment                |
> | `google-prod-read`, `cloudflare-prod-*`          | `secrets.READ_ALL_FFC_AZURE_*` | **environment secrets** — added to _each_ environment       |
>
> **Prefer the `vars.*` form for new read lanes.** These identifiers are non-secret GUIDs — the repo
> already publishes them as Variables, `CLAUDE.md` documents them as such, and the `secrets.*` form
> buys nothing but a provisioning step that can be forgotten. It was forgotten here:
> `github-prod-read` shipped in #837 referencing `secrets.*` against an environment with no secrets,
> and 726 failed three times on 2026-07-29 before the references were switched to `vars.*`.
>
> **This is now a CI check, not just advice.** `scripts/check-env-secret-references.py` (#912) fails
> any `secrets.<OIDC identifier>` reference outside the exact set of environments recorded as
> carrying secret copies — so a new lane written the `secrets.*` way fails **Validate Repository**
> instead of failing its first scheduled run. The environments still on the `secrets.*` form are
> listed in that script with the evidence for each, and the list is exact in both directions: when
> the last reference to one goes away, its entry has to go with it.

1. **Federated credential on kv-reader:**
   ```bash
   az ad app federated-credential create \
     --id 79a123d8-2f45-4925-a550-bbd849399daf \
     --parameters '{"name":"github-oidc-github-prod-read","issuer":"https://token.actions.githubusercontent.com","subject":"repo:FreeForCharity/FFC-Cloudflare-Automation:environment:github-prod-read","audiences":["api://AzureADTokenExchange"]}'
   ```
2. Confirm the kv-reader identity can `Get` **`read-all-cbm-github-pat`** (502, 735) and
   **`read-all-cbm-ffc-copilot-mcp-github-pat`** (726 — this is the one carrying Organization
   Administration read, which GitHub requires even to _read_ org rulesets with a fine-grained PAT).
3. Create the `github-prod-read` GitHub environment with **no** required reviewers. **Nothing else
   is needed here** — the workflows read `vars.READ_ALL_FFC_AZURE_KV_CLIENT_ID` /
   `vars.READ_ALL_FFC_AZURE_TENANT_ID`, which are repo Variables that resolve in every environment
   (the `whmcs-prod-read` pattern). Do **not** add environment secrets by those names; a second copy
   is exactly the drift this repo removed in #844.
4. Re-run **730** to refresh the gate audit, and confirm the environment reports no protection rules
   — an ungated lane that quietly acquired a reviewer is the failure #834 exists to prevent.

**Blocked on a remint, not just on provisioning.** 321's liveness monitor reports
`read-all-cbm-github-pat` returning **401** (#877, confirmed real in #878 after two false positives
were fixed) — so 502's delivery and 735 cannot work until that secret is reminted under #848. 726
uses the copilot-mcp PAT, which probes healthy.

**Do not reintroduce a GitHub-secret copy.** An earlier revision of #834 proposed a
`GH_REPORT_TOKEN` environment secret. That predates the Key Vault migration (#844) and would put a
second copy of a credential where it can drift — the failure that silently broke the Cloudflare
token for four months. The scope narrowing #834 wants is real, but it belongs in the _identity_
(reader vs writer), not in a new pasted PAT.

## `fraudlabspro-prod-read` and `candid-prod-read` — one Azure command each (#912)

> **Status: NOT PROVISIONED.** Both environments exist and are ungated; neither has a federated
> credential on kv-reader. Verified live under #912: the reader identity's credentials cover
> `github-prod-read`, `whmcs-prod-read`, `google-prod-read` and `cloudflare-prod-read`, plus two on
> other repos — none for these two.

**What #912 already removed:** both lanes referenced `secrets.READ_ALL_FFC_AZURE_*` against
environments holding no secrets, so they failed at their own "Validate required Azure secrets"
preflight and never reached Entra. 228's `fraud_review` had failed **every** scheduled run since it
shipped (2026-07-27, -28, -29) for exactly this reason, while `fetch_fraud_orders` — ten lines above
it in the same file, on `vars.*` — succeeded. All three jobs now read the repo Variables, so **no
environment secret has to be created for either lane.**

**What still needs a human.** Azure AD IAM writes are blocked by the agent harness classifier
(`CLAUDE.md`), so these two commands are for an Azure admin:

```bash
# FraudLabs Pro read lane (228 fraud_review)
az ad app federated-credential create \
  --id 79a123d8-2f45-4925-a550-bbd849399daf \
  --parameters '{"name":"github-oidc-fraudlabspro-prod-read","issuer":"https://token.actions.githubusercontent.com","subject":"repo:FreeForCharity/FFC-Cloudflare-Automation:environment:fraudlabspro-prod-read","audiences":["api://AzureADTokenExchange"]}'

# Candid read lane (801, 802)
az ad app federated-credential create \
  --id 79a123d8-2f45-4925-a550-bbd849399daf \
  --parameters '{"name":"github-oidc-candid-prod-read","issuer":"https://token.actions.githubusercontent.com","subject":"repo:FreeForCharity/FFC-Cloudflare-Automation:environment:candid-prod-read","audiences":["api://AzureADTokenExchange"]}'
```

Then confirm kv-reader can `Get` the lane's KV secret (`read-all-ffc-fraudlabspro-api-key`;
`read-all-ffc-candid-charity-check-key` / `read-all-ffc-candid-essentials-key`) and add both
environments to `config/federated-credentials.json` so the subject audit covers them.

**Until then the failure mode changes but does not disappear** — 228 will fail at `AADSTS700213`
(Entra reached, no matching credential) rather than at the preflight. That is a strictly better
failure: it names the missing credential instead of an empty string, and `AADSTS700213` is the error
this document exists to explain.

> **`config/federated-credentials.json` is incomplete, and this is not the only gap.** Its `apps[]`
> lists 3 reader environments and 5 writer ones, while the tree runs OIDC jobs against **12**
> environments — `github-prod`, `github-prod-read`, `candid-prod-read` and `fraudlabspro-prod-read`
> are all absent. Three of those four demonstrably work in production, so the map understates
> reality, and the subject audit (`--live`) therefore cannot catch a typo in a credential it does
> not know to expect. Reconciling it needs a live `az ad app federated-credential list` per
> identity, which is a read an operator can do from an authenticated shell; tracked on #912.

## Auditing federated-credential subjects

The `m365-prod` typo above was an **expected** credential with a **malformed subject** — a
presence/enumeration check (the shape of #589) passes it, yet every job fails `AADSTS700213`. To
catch that class, the expected credentials this repo's workflows rely on are declared in
[`config/federated-credentials.json`](../config/federated-credentials.json) (per app: object id +
the environments it must carry). Subjects are **generated** from `repo` + `environment`, so the map
itself can't carry a subject typo.

`scripts/check-federated-credential-subjects.py` uses it three ways:

```bash
python3 scripts/check-federated-credential-subjects.py            # self-check the map (runs in CI)
python3 scripts/check-federated-credential-subjects.py --live     # audit live Azure (operator; needs az)
python3 scripts/check-federated-credential-subjects.py --actual-dir ./dumps   # audit saved az dumps
```

The `--live` / `--actual-dir` audit asserts each expected credential exists with an **exact**
canonical `subject`, `issuer`, and `audiences`, and flags any this-repo credential that isn't
expected. CI runs the self-check on every PR; the live audit is operator-run (and folds naturally
into the #589 drift-audit workflow when that lands). Cross-repo credentials on these shared
identities (`FFC-IN-*`) are intentionally out of scope.

## Inspecting / repairing from the Claude sandbox

`az` is not preinstalled, but you can install it into a venv and device-auth as the admin:

```bash
python3 -m venv azvenv && ./azvenv/bin/pip install -q azure-cli
export AZURE_CONFIG_DIR="$PWD/azconfig"
./azvenv/bin/az login --use-device-code --allow-no-subscriptions   # user completes the device code
./azvenv/bin/az ad app federated-credential list --id <object-id> -o table
```

Read operations (list apps, list federated creds, `az keyvault secret show`, direct WHMCS queries
via the APIM gateway) work once authed. **Writes to Azure AD IAM** (creating/updating a federated
credential) are a high-severity change and are **blocked by the agent harness auto-mode classifier**
— they must be run by a human (or with an explicit Bash allow-rule). The commands above are provided
so a human can apply them directly.
