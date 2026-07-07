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

| Environment                                 | App                                  | Notes                                                                            |
| ------------------------------------------- | ------------------------------------ | -------------------------------------------------------------------------------- |
| `cloudflare-prod-read`                      | kv-reader                            | ungated                                                                          |
| `cloudflare-prod-write` / `cloudflare-prod` | kv-writer                            | gated                                                                            |
| `google-prod-read`                          | kv-reader                            | ungated                                                                          |
| `google-prod-write`                         | kv-writer                            | gated (Google provisioning: 503, 505)                                            |
| `zeffy-prod`                                | kv-writer                            | ungated                                                                          |
| `whmcs-prod`                                | kv-writer                            | gated (WHMCS writes: 102, 116, 118, 204–207, 211, 212, 221)                      |
| `whmcs-prod-read`                           | kv-reader                            | ungated (WHMCS reads: 104, 115, 201–203, 208–210, 213–220) — **applied 2026-07-07** |
| `m365-prod`                                 | Graph CLI (+ kv-reader for KV steps) | gated — **typo fixed 2026-07-07**                                               |

## ✅ Resolved — `m365-prod` credential subject typo (found & fixed 2026-07-07)

> **Status: APPLIED & VERIFIED 2026-07-07** (issue #625). The Graph CLI credential subject was
> corrected via `az ad app federated-credential update`. Verified green: **101** (`m365` job),
> **301** (Graph login under `m365-prod` + kv-reader login under `cloudflare-prod-read`), and **302**
> — all with the Azure OIDC login succeeding, no `AADSTS700213`. The optional kv-reader
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
> kv-reader federated credential exists, both repo Variables are set, the kv-reader holds
> **Key Vault Secrets User** (RBAC) on `kv-ffc-admin-prod-cbm` covering every `read-all-*` secret,
> and the ungated `whmcs-prod-read` environment exists (`protection_rules: []`). Verified green and
> ungated (no approval gate, no `AADSTS700213`): **202** (Export Products), **201** (Export Domains),
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
