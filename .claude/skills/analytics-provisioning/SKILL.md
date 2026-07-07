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

### 3b. Static HTML / WordPress-export sites (no `analytics.config.ts`)

Some FFC-EX repos are **static WordPress exports** (`index.html` + many `*.html` pages, no
`package.json`, no `src/lib/`). They have no config file — GTM is embedded as **raw snippets in the
HTML**. GA4/GTM **provisioning is identical** (505 then 503 still create the property + FFC
container); only the wiring differs:

1. Inject the standard GTM snippet on **every** `*.html` page (static exports are one file per page,
   so a single index edit is not enough). The head snippet goes immediately after `<head>`, the
   `<noscript>` immediately after `<body>`:

   ```html
   <!-- Google Tag Manager -->
   <script>
     (function (w, d, s, l, i) {
       w[l] = w[l] || [];
       w[l].push({ 'gtm.start': new Date().getTime(), event: 'gtm.js' });
       var f = d.getElementsByTagName(s)[0],
         j = d.createElement(s),
         dl = l != 'dataLayer' ? '&l=' + l : '';
       j.async = true;
       j.src = 'https://www.googletagmanager.com/gtm.js?id=' + i + dl;
       f.parentNode.insertBefore(j, f);
     })(window, document, 'script', 'dataLayer', 'GTM-XXXXXXX');
   </script>
   <!-- End Google Tag Manager -->
   ```

   ```html
   <!-- Google Tag Manager (noscript) -->
   <noscript
     ><iframe
       src="https://www.googletagmanager.com/ns.html?id=GTM-XXXXXXX"
       height="0"
       width="0"
       style="display: none; visibility: hidden"
     ></iframe
   ></noscript>
   <!-- End Google Tag Manager (noscript) -->
   ```

2. **If a non-FFC GTM id is already baked in** (e.g. a leftover `GTM-5JV8JHCH` from the site's
   original WordPress install), **replace** it with the FFC container id from 503 — don't leave the
   old one, or the charity's tags fire through a container FFC doesn't own.
3. The GA4 tag itself lives **inside** the FFC GTM container (seeded by 503), so the pages only
   carry the `GTM-XXXX` snippet — no `gtag`/`G-XXXX` in the HTML. Open a **PR** on the static repo.

> These repos are outside this repo's MCP scope, so pushing the wiring PR needs the repo added to
> the session (`add_repo`) or a workflow that edits the FFC-EX repo (the 505/503 provisioning half
> needs neither — it only talks to Google). For a fleet-wide rollout, script the snippet injection
> rather than hand-editing N pages per site.

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
