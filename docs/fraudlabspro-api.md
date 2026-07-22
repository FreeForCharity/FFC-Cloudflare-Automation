# FraudLabs Pro — fraud-screening review API

FraudLabs Pro ([fraudlabspro.com](https://www.fraudlabspro.com)) is the fraud-screening module
configured in WHMCS. It scores every order at checkout and can drop an order into **Fraud** status.
FFC onboards known, vetted 501(c)(3) / pre-501(c)(3) charities, so a high FraudLabs score on a **$0
onboarding order** is almost always a false positive (free-email address + residential IP +
brand-new domain), not real fraud — and a mis-flagged applicant sits blocked until a human notices
(see [#813](https://github.com/FreeForCharity/FFC-Cloudflare-Automation/issues/813), order 793).

This doc covers the **read-only review API** used by workflow
[`228. WHMCS - Fraud Review`](../.github/workflows/228-whmcs-fraud-review.yml) to read each Fraud
order's stored verdict programmatically and recommend clearing the obvious false positives. Like the
Candid scaffolding, **228 is inert until the one-time setup below is done** — until then the review
job fails fast with a clear message.

## What 228 does (and does not do)

| Does                                                                              | Does not                                                         |
| --------------------------------------------------------------------------------- | ---------------------------------------------------------------- |
| Read each WHMCS **Fraud**-status order (via workflow 210's `whmcs-orders-export`) | Write to any order                                               |
| Look up each order's stored FraudLabs Pro verdict (`APPROVE`/`REJECT`/`REVIEW`)   | Clear an order (that is workflow **211**, gated on `whmcs-prod`) |
| Emit a masked triage row with a **recommended** action                            | Contact the applicant or change WHMCS state                      |

The recommendation policy is the pure function `Get-FraudReviewRecommendation`
(`scripts/fraudlabspro-api-common.ps1`), unit-tested at
`tests/workflow-logic/test_228_fraud_review.py`:

| WHMCS status | FraudLabs verdict | Order amount | Recommendation      |
| ------------ | ----------------- | ------------ | ------------------- |
| Fraud        | APPROVE           | $0           | `clear-recommended` |
| Fraud        | APPROVE           | > $0         | `review-manually`   |
| Fraud        | REJECT            | any          | `hold-for-human`    |
| Fraud        | REVIEW / (none)   | any          | `review-manually`   |
| not Fraud    | any               | any          | `no-action`         |

A `clear-recommended` order is one a reviewer can clear with one tap via workflow **211**
(`action=accept`) — 228 never does it automatically.

## REST API surface (read-only)

- **Endpoint:** `GET https://api.fraudlabspro.com/v2/order/result`
- **Query params:** `key` (the account API key), `id` (the order id submitted at screening time),
  `format=json`.
- **Response fields read:** `fraudlabspro_status` (`APPROVE` / `REJECT` / `REVIEW`),
  `fraudlabspro_score`, plus `fraudlabspro_error_code` / `fraudlabspro_message` on error.
- **Host allowlist:** `Invoke-FraudLabsProApi` refuses to send the key anywhere except
  `api.fraudlabspro.com`, and never logs the key (the redacted URL is logged instead) — mirroring
  `Invoke-CandidApi` / `Invoke-WhmcsApi`.

> **Order-id keying (validate at live-test time).** 228 sends the WHMCS field named by its
> `order_id_field` input (default `ordernum`) as the FraudLabs `id`. This assumes the WHMCS
> FraudLabs Pro module submitted that same id at screening. If a live dry run shows empty verdicts,
> try `order_id_field=id`, or confirm how the module keys its stored results, and update the
> default.

## One-time provisioning (KV secret + environment + federated credential)

Nothing here lives in a GitHub secret — Key Vault is the single source of truth, consumed at runtime
via OIDC (identical pattern to `docs/candid-api-and-mcp.md`). Steps, all done once by an admin:

1. **Get a FraudLabs Pro API key** from the FraudLabs Pro account that owns the WHMCS module
   (Account → API Key). One account key covers the read API.
2. **Store it in Key Vault** `kv-ffc-admin-prod-cbm` as secret
   **`read-all-ffc-fraudlabspro-api-key`** (a `wr-all-ffc-fraudlabspro-api-key` copy is optional;
   the review is read-only). Until set, seed a placeholder value `PLACEHOLDER-SET-VIA-AZURE-PORTAL`
   — the composite action detects it and fails with a setup hint, so the scaffold stays dormant.
3. **Create the `fraudlabspro-prod-read` environment** (Settings → Environments), **no required
   reviewers** (this is a read-only env, like `whmcs-prod-read` / `candid-prod-read`). Add the two
   environment secrets the KV-reader identity uses:
   - `READ_ALL_FFC_AZURE_KV_CLIENT_ID`
   - `READ_ALL_FFC_AZURE_TENANT_ID`
4. **Add a federated credential** for the reader app (`ffc-admin-kv-reader`) with subject
   `repo:FreeForCharity/FFC-Cloudflare-Automation:environment:fraudlabspro-prod-read` — follow
   `docs/azure-oidc-federated-credentials.md` (mind the trailing-hyphen subject typo documented
   there). Then extend that doc's expected-credential list so
   `scripts/check-federated-credential-subjects.py` accounts for it.

Once steps 1–4 are done, dispatch 228 (read-only): it prints every current Fraud order with its
FraudLabs status/score and a recommended action, writing no changes.

## Related

- Issue [#813](https://github.com/FreeForCharity/FFC-Cloudflare-Automation/issues/813) — the
  tracking epic (item 2: daily schedule + rolling issue; item 3: clear-order wiring via 211 — both
  follow-ups).
- `docs/whmcs-apim-routing.md` — the WHMCS credential/APIM path 228's order read reuses.
- `docs/candid-api-and-mcp.md` — the read-only-KV-key pattern this mirrors.
