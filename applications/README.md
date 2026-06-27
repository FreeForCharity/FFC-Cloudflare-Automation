# Applications feed (`applications.json`)

A **PII-safe** feed of charities that have applied to Free For Charity, published
by this repo and consumed by the FFCadmin public roadmap intake
([FreeForCharity/FFC-IN-ffcadmin.org](https://github.com/FreeForCharity/FFC-IN-ffcadmin.org)).

This repo owns the WHMCS / Zeffy flows; FFCadmin holds no WHMCS or Zeffy
credentials. The trust boundary lives here: this repo sees the raw client data,
strips PII, and publishes only the minimal non-PII feed below.

## Source & product gate

A charity "application" is a **WHMCS client holding an FFC onboarding product**:

| Product                                   | pid |
| ----------------------------------------- | --- |
| FFC Pre-501c3 Nonprofit / Charity Onboarding | 16  |
| FFC 501c3 Nonprofit / Charity Onboarding     | 33  |

See [`config/whmcs-onboarding-products.json`](../config/whmcs-onboarding-products.json).
Donors are payments/transactions, **not** clients holding these products, so
gating on these product ids excludes donors by construction.

## Contract

```json
{
  "version": 1,
  "generatedAt": "2026-06-27T08:00:00Z",
  "applications": [
    {
      "id": "ffc-1234",
      "charityName": "Helping Hands Shelter",
      "serviceTier": "Tier 1 — Application & verification (501(c)(3))",
      "missionExcerpt": "Emergency shelter and meals for families experiencing homelessness.",
      "submittedAt": "2026-06-25T14:12:00Z"
    }
  ]
}
```

| Field            | Required | Notes |
| ---------------- | -------- | ----- |
| `id`             | yes      | Stable, opaque, non-PII per-charity surrogate (`ffc-<whmcs client id>`). The dedup key FFCadmin stores. |
| `charityName`    | yes      | Public organization name (WHMCS `companyname`). Never a person's name. |
| `serviceTier`    | yes      | Derived from the onboarding product; encodes the 501(c)(3) stage. |
| `missionExcerpt` | no       | Truncated (≤180 chars) mission text, when the product collected it. |
| `submittedAt`    | no       | ISO-8601 onboarding/registration date. |

### Never published

Applicant email, phone, mailing/physical address, board-member contacts, EIN,
GuideStar links, payment/bank info, amounts — and any other WHMCS custom field.
The feed is built from an explicit **allowlist** (see
[`scripts/whmcs-applications-feed.ps1`](../scripts/whmcs-applications-feed.ps1)),
not by removing fields from a fuller object.

## How it is produced

[`.github/workflows/38-publish-applications-feed.yml`](../.github/workflows/38-publish-applications-feed.yml)
runs daily (and on demand):

1. **generate** (`whmcs-prod`) — `scripts/whmcs-applications-feed.ps1` calls the
   read-only WHMCS API (`GetClientsProducts` gated to the onboarding pids, then
   `GetClientsDetails` for the public org name) and writes this file.
2. **publish** (`github-prod`) — opens a PR with the regenerated file via
   `CBM_TOKEN` (`main` is protected; changes land via PR + merge queue).

Run the workflow with **`dry_run: true`** to preview the feed in the logs
without opening a PR. Always dry-run after any WHMCS product/custom-field change.

## Consumer

FFCadmin's `sync-applications.yml` fetches this file daily from
`https://raw.githubusercontent.com/FreeForCharity/FFC-Cloudflare-Automation/main/applications/applications.json`,
dedupes on `id`, and opens `kind:intake` issues. The committed empty feed
(`applications: []`) is a valid no-op until the first real run publishes data.
