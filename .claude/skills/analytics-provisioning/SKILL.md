---
name: analytics-provisioning
description: >-
  Provision Google Analytics 4 + Google Tag Manager for one FFC-supported charity site, standalone
  (no full onboarding needed). Use this when asked to "add GA", "add analytics", "set up GA4/GTM",
  "wire tracking", or "provision a GA property / GTM container" for a domain or an FFC-EX-* website
  repo. Runs workflow 505 (GA4 property) then 503 (GTM container) and wires the ids into the repo's
  analytics.config.ts. Names the exact workflows, inputs, the gate, and the gotchas.
---

# Analytics provisioning (GA4 + GTM for one charity site)

Standalone runbook for the analytics slice of onboarding. Use it to add tracking to an existing
`FFC-EX-<domain>` site without running the whole chain. For the broader flow see the
[`charity-onboarding` skill](../charity-onboarding/SKILL.md); the architecture reference is
[`docs/google-api.md`](../../../docs/google-api.md).

## What you're building (one charity site)

- **One GA4 property** named `<domain> - GA4` under the **`FFC Supported Sites`** account (id
  `400204327`), with **one web data stream** for the domain → a measurement id `G-XXXXXXXXXX`. _One
  property per charity_ (not stream-per-charity).
- **One GTM container** under the FFC GTM account (id `4702611686`), seeding a GA4 tag (→ that
  measurement id) on **All Pages**, published → `GTM-XXXXXXX`. _One container per charity_ so the
  POC can self-administer.
- Both ids wired into `FFC-EX-<domain>/src/lib/analytics.config.ts` (`gaMeasurementId`, `gtmId`).

## Order of operations (GA4 first — GTM needs the measurement id)

### 1. `505. Google - GA4 Property Provision` → get `G-XXXXXXXXXX`

- Inputs (**all dispatch inputs are strings**): `domain` (required, the stream `defaultUri`, e.g.
  `newcharity.org`); `account_name` defaults to `FFC Supported Sites` (leave it); `display_name`
  defaults to `<domain> - GA4`; `time_zone` defaults to `America/New_York`; `issue_number` optional
  (comments the result back); `dry_run` **defaults to `true`**.
- **Idempotent** — an existing stream for the domain is reported, not duplicated (matched by stream
  `defaultUri`, never by property name — GA property names are historically unreliable).
- Run `dry_run=true` first, read the preview, then `dry_run=false` and **approve the
  `google-prod-write` gate** (reviewer `clarkemoyer`). The run reports the measurement id.

### 2. `503. Google - GTM Provision` → get `GTM-XXXXXXX`

- Inputs: `domain` (required, container name); `measurement_id` (**required — the `G-XXXX` from step
  1**); `account_id` defaults to `4702611686`; optional `clarity_id`, `meta_pixel_id`,
  `grantee_email` (delegates the POC container Edit/Publish); `issue_number` optional; `dry_run`
  **defaults to `true`**.
- Creates the container, seeds the GA4 tag on All Pages (trigger `2147479553`), `:create_version` →
  `:publish`. Run dry first, then `dry_run=false` + approve the `google-prod-write` gate.

### 3. Wire the ids into the site (PR)

Edit `FFC-EX-<domain>/src/lib/analytics.config.ts`:

```ts
export const analyticsConfig = {
  gtmId: 'GTM-XXXXXXX', // from 503
  gaMeasurementId: 'G-XXXXXXXXXX', // from 505
  metaPixelId: 'XXXXXXXXXXXXXXX', // leave placeholder unless provisioned
  clarityProjectId: 'XXXXXXXXXX', // leave placeholder unless provisioned
} as const;
```

Open a **PR** on the FFC-EX repo (never push its default branch); leave unused integrations as their
`XXXX` placeholders so they stay inert. That placeholder-vs-real distinction is exactly how you tell
whether a repo already has analytics (see the sweep below).

## How it authenticates (context, not steps)

Both workflows run on `windows-latest` under the gated **`google-prod-write`** environment.
Provisioning uses **domain-wide delegation**: the `ffc-workspace-admin` service account impersonates
`clarkemoyer@freeforcharity.org`; its key comes from Key Vault
(`wr-all-cbm-google-workspace-service-account-key`) via OIDC — never a GitHub secret. The DWD scope
set must include `tagmanager.edit.containerversions` (already granted) or 503 publish fails
`ACCESS_TOKEN_SCOPE_INSUFFICIENT`.

## Gotchas (these have burned prior runs)

- **All `workflow_dispatch` inputs must be strings** — `issue_number: "614"`, never `614` (numeric →
  `422 Invalid value for input`).
- **The GA account is `FFC Supported Sites`** — not "Charities" / "FFC Supported Charities". The 505
  default is correct; don't override it with a guessed name.
- **Verify GA by stream `defaultUri` / `measurementId`, never the property display name** — names
  drift from what they actually track.
- **`google-prod-write` gates** — the run parks at `status: waiting` until `clarkemoyer` approves;
  that's the gate, not a failure. From the web sandbox you can dispatch via MCP but **cannot**
  approve.
- **New/edited workflows must be on `main`** before `workflow_dispatch`-by-filename works (505/503
  are already merged).
- **503 needs the `G-XXXX` from 505** — run them in order; don't dispatch 503 with a guessed id.

## Find repos that still need analytics (the sweep)

To list FFC-EX website repos missing a real GA id, read each repo's `src/lib/analytics.config.ts`
and treat `gaMeasurementId` **starting with `G-` but not `G-XXXX…`** as provisioned; a placeholder
(`G-XXXXXXXXXX` / `XXXX…`) means it needs GA. That is the repo-side signal; the authoritative
cross-check is the GA account's stream list (by `defaultUri`). Provision each candidate with step 1
(and step 2 for GTM), then wire the ids back per step 3.
