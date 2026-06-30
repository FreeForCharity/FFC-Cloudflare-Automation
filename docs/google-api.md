# Google API automation (Azure KV-backed)

Tracks the Google Cloud production environment for FFC. Epic: **#508**. This doc is both the
architecture reference and the setup/rotation runbook.

Google joins WHMCS, Zeffy, M365, and Cloudflare as an OIDC -> Azure Key Vault integration. **Key
Vault is the single source of truth** for the Google service-account credential; it is never copied
into a GitHub secret (that drift broke the Cloudflare token for 4 months — see `CLAUDE.md`).

## Architecture

```
GCP project (ffc-prod) ──► Service Account (read-only to start: GA Viewer)
   SA JSON key ──► Azure Key Vault kv-ffc-admin-prod-cbm
                     read-all-ffc-google-sa-key   (read path)
                     wr-all-ffc-google-sa-key     (write path, Wave 3+)
GitHub runner ──OIDC──► Azure (federated cred on google-prod-read / -write env)
   ──► .github/actions/google-secrets-from-kv  (masks key, writes ADC file,
                                                exports GOOGLE_APPLICATION_CREDENTIALS)
   ──► scripts/google-api-common.ps1  (mints OAuth token in-process via JWT-bearer)
   ──► GA4 Data API / Search Console / ...  ──► aggregate JSON
   ──► PR into FreeForCharity/FFC-IN-ffcadmin.org ──► status pages
```

## Components in this repo

| Path | Purpose |
| --- | --- |
| `.github/actions/google-secrets-from-kv/action.yml` | OIDC -> KV; writes the SA key to a runner-temp ADC file, exports `GOOGLE_APPLICATION_CREDENTIALS`. Twin of `cloudflare-tokens-from-kv`. |
| `scripts/google-api-common.ps1` | `Get-GoogleAccessToken` (signed-JWT bearer flow, pure .NET RSA — no gcloud/Python needed), `Invoke-GoogleApi`. |
| `.github/workflows/google-api-smoke.yml` | Read-only GA4 connectivity gate; reusable via `workflow_call`. |
| `scripts/google-analytics-report.ps1` | Wave 1 GA4 report -> aggregate JSON. `-DryRun` emits a stub without contacting Google. |

## Environments & OIDC identifiers

| Environment | Scope | KV secret prefix | Azure OIDC identity |
| --- | --- | --- | --- |
| `google-prod-read` | read (reporting) | `read-all-` | KV-reader; federated cred `repo:FreeForCharity/FFC-Cloudflare-Automation:environment:google-prod-read` |
| `google-prod-write` | write (provisioning, Wave 3+) | `wr-all-` | KV-writer; **requires human approval** |

OIDC identifiers are **non-secret GUIDs** passed from the caller:
`secrets.READ_ALL_FFC_AZURE_KV_CLIENT_ID` / `READ_ALL_FFC_AZURE_TENANT_ID` (read path) — same names the
Cloudflare read workflows use. Non-secret GA property ids live as repo Variables
(`vars.GA_PROPERTY_ID_PRIMARY`, etc.), never as secrets.

## APIs enabled per wave (least privilege)

| Wave | API | Scope granted |
| --- | --- | --- |
| 0/1 | Analytics Data API | `analytics.readonly` |
| 2 | Search Console API | `webmasters.readonly` |
| 2 | Site Verification API | `siteverification` (gated write) |
| 3 | Analytics Admin API | property admin, scoped to the FFC GA4 account (gated write) |

Enable an API only when its wave starts; grant the SA the minimum role for that API only.

## One-time setup (#509 / #510) — human steps

Run as `clarkemoyer@freeforcharity.org`. Replace IDs as noted.

```bash
# 1. Project + API (Wave 0/1)
gcloud projects create ffc-prod --name="FFC Production"
gcloud billing projects link ffc-prod --billing-account=<BILLING_ACCOUNT_ID>
gcloud services enable analyticsdata.googleapis.com --project ffc-prod

# 2. Read-only service account (NO project roles; access granted per GA4 property)
gcloud iam service-accounts create ffc-ga-reader \
  --project ffc-prod --display-name="FFC GA reader (read-only)"

# 3. Generate a key, store it in Key Vault, then DELETE the local copy
gcloud iam service-accounts keys create ffc-ga-reader.json \
  --iam-account=ffc-ga-reader@ffc-prod.iam.gserviceaccount.com
az keyvault secret set --vault-name kv-ffc-admin-prod-cbm \
  --name read-all-ffc-google-sa-key --file ffc-ga-reader.json
az keyvault secret set --vault-name kv-ffc-admin-prod-cbm \
  --name wr-all-ffc-google-sa-key --file ffc-ga-reader.json   # identical until a writer SA exists
rm -f ffc-ga-reader.json

# 4. In GA4: add ffc-ga-reader@ffc-prod.iam.gserviceaccount.com as a Viewer on the
#    freeforcharity.org and ffcadmin.org properties; record the numeric property ids.
```

Then in GitHub: create environments `google-prod-read` (no approval) and `google-prod-write`
(required reviewer `clarkemoyer`); add the matching Azure federated credentials; set repo Variables
`GA_PROPERTY_ID_PRIMARY` (+ per-site as needed).

## Validate

```bash
gh workflow run google-api-smoke.yml --ref main -f property_id=<GA4_PROPERTY_ID>
```
Green => the OIDC -> KV -> Google chain works with no Google secret in GitHub.

## Safety conventions

- **Read before write.** Reporting (read) is proven before any write API is enabled.
- **Gated writes.** Write workflows use `google-prod-write` (human approval) and default to
  `dry_run=true` with typed confirmation, like `cloudflare-prod-write` / the bulk-reject workflow.
- **No leakage.** The composite action masks every line of the key; the helper masks minted tokens;
  the ADC file lives only in `RUNNER_TEMP` for the job. Never commit a key or print a token.
- **PII-safe.** Only aggregate metrics are published (no user-level dimensions). JSON reaches FFC
  Admin via PR, not direct push.

## Rotation

Add a new version of `read-all-ffc-google-sa-key` (and `wr-all-…`) in Key Vault — no GitHub change.
To revoke, delete the old SA key in GCP:
`gcloud iam service-accounts keys delete <KEY_ID> --iam-account=ffc-ga-reader@ffc-prod.iam.gserviceaccount.com`.
