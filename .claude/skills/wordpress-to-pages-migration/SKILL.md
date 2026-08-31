---
name: wordpress-to-pages-migration
description: >-
  Operational runbook for migrating a live WordPress (or other legacy-hosted) charity site to a
  static GitHub Pages deployment in an FFC-EX-<domain> repo, to the "DNS-ready" bar of epic #702.
  Use this when asked to "migrate <site>", "wordpress to pages", "capture <site>", do a "static
  conversion", "move <site> off HostPapa" or "off Hostinger", or bring a site to Pages-capable /
  DNS-ready. Covers capture + asset localization, repo scaffold from the proven pattern, footer
  standard injection, Pages on the default URL, the workflow-121 preflight verdict, and tracking.
---

# WordPress → GitHub Pages migration (per site)

This skill migrates **one site** from live WordPress/legacy hosting to a static build in
`FFC-EX-<domain>`, serving the **default Pages URL** (`freeforcharity.github.io/FFC-EX-<domain>/`),
with **fully localized assets**, to **DNS-ready** — cutover itself stays separately gated. It is the
per-site unit of work for epic
[#702](https://github.com/FreeForCharity/FFC-Cloudflare-Automation/issues/702) (Wave 1 = the
in-Cloudflare sites, easiest first).

Read alongside:

- [`docs/ffc-ex-static-clone-runbook.md`](../../../docs/ffc-ex-static-clone-runbook.md) — the
  httrack-based clone pipeline validated end-to-end on browncanyonranch.org.
- [`docs/ffc-ex-clone-fidelity-audit.md`](../../../docs/ffc-ex-clone-fidelity-audit.md) — why
  "template scaffold" ≠ "clone"; the fidelity bar.
- The 2026-05 wave summary (`C:\vscode\FFC-MIGRATION-WAVE-SUMMARY.md` locally) — 13 sites migrated
  in parallel, easy-first; the fix-up patterns below come from it.
- For an already-migrated site's cutover: workflow **120** (bulk cutover) and **121** (preflight),
  and the `ffc-cutover` skill where available.

## 1. Preconditions — is this site in scope?

1. **In the sites list.** The canonical inventory is
   [`sites-list/sites_list.csv`](../../../sites-list/sites_list.csv) /
   [`sites_list.json`](../../../sites-list/sites_list.json) in this repo. If the domain is not
   there, stop and reconcile the inventory first.
2. **Wave 1 = in-Cloudflare only.** The zone must be under FFC (or CM) Cloudflare
   (`In Cloudflare = Yes`). The ~59 non-Cloudflare sites are the next wave — do not start them
   early.
3. **Not excluded.** Check the exclusion list on epic #702 before starting. Known exclusions:
   - **ptuganda.org** — non-US wind-down (term-eligible 2026-09-27). Do not migrate.
   - **Tier-5 needs-triage and tier-6 inactive sites** — triage before any migration work.
   - For-profit / personal sites are included **only** as host-exit necessities; flag scope
     questions to the operator rather than silently migrating them.
4. **Not already done.** 12 sites from the 2026-05 wave are already Pages-live; 5 repos exist but
   are unverified. Check for an existing `FFC-EX-<domain>` repo and its deploy state before
   capturing anything.
5. Pick **easiest first** — the epic body carries a scored, ordered list. Do not start a HARD-tier
   site (password walls, 30+ pages, WAF) while easier ones remain.

## 2. Capture the live site (fully localized)

Three capture paths; the verification gate is the same for all of them. **Start by running the Path
C inspect** — it is read-only, takes seconds, and tells you which of the three the site can actually
support before you spend a mirror on it.

> **Not cPanel.** A cPanel/FTP backup is not a starting point for this work, and on a
> WordPress.com-hosted site there is no cPanel to take one from. A backup yields PHP, a database
> dump and a plugin tree — the inputs to a rendering engine that will not exist after the migration.
> What is being migrated is the rendered artifact, and the live site is the only place that exists.

**Path C — WordPress REST API + rendered scrape** (workflow
**`705. Website - Capture WordPress Site (REST API + Scrape)`**,
`scripts/capture-wordpress-api.mjs`). Read-only, ungated:

```bash
# Probe first — read-only, seconds, no capture.
node scripts/capture-wordpress-api.mjs --domain <domain> --inspect
# Or dispatch 705 with mode=inspect, then mode=capture.
```

The other two paths discover pages by **following links**, so their page count is a property of the
theme's navigation. Path C takes the inventory from the CMS itself (`wp/v2/pages`, with an
`X-WP-Total` to check it against) and then fetches the rendered markup for each of those URLs — so
completeness becomes something the run can fail on. It reports:

- **Which REST flavor answers** — `pretty` (`/wp-json/`), `query` (`/?rest_route=/`), or `dotcom`
  (`public-api.wordpress.com/wp/v2/sites/<domain>/`). **A WordPress.com-hosted site serves 404 on
  its own `/wp-json/`**, so a probe that only tries the site's own domain reports "not WordPress"
  for a site whose full inventory is one URL away. Measured 2026-08-30 on vpmin.org, which is a
  WordPress.com stub.
- **Which collections are restricted.** The `.com` public API answers per collection — vpmin.org
  returns 200 for `pages` and **401 for `media`**. A restricted collection is reported, never
  silently treated as empty, because a collection that returns nothing and a collection that is not
  readable look identical in the output otherwise.
- **The plugin/builder fingerprint**, from the REST namespace list. This decides whether the
  rendered scrape is optional or mandatory (see the Divi note below).
- **Sitemap vs REST disagreement.** The run cross-checks `sitemap.xml` and captures the **union**,
  because comparing the REST total against pages fetched from that same REST list is a tautology,
  not a check. The gap is routinely large: on viewpointministriesinternational.org the sitemap
  advertises **587** page URLs against `wp/v2/pages`' **19**.

> **Read the two domains apart, and do not treat the new one as part of the site.** A charity often
> has a long legacy domain and a short new one, and they can be different systems. For Viewpoint
> Ministries the **capture source** is `viewpointministriesinternational.org` (self-hosted WordPress
> on WPMU DEV, Divi, 19 pages / 229 posts / 280 media). `vpmin.org` is a **new, empty short domain**
> parked on a WordPress.com stub, to be pointed at the migrated site later — and the repo is named
> for that destination, `FFC-EX-vpmin.org`. Capturing the repo's namesake rather than the live site
> produces a clean, green, 2-page artifact of the wrong website. Confirm which domain actually
> serves the content before capturing, and do not infer it from the repo name.
>
> **But do not then conclude the other domain is irrelevant to the capture — check what the CMS
> thinks its own address is.** This is the trap that cost the most on this migration, and it looks
> exactly like the one above. Measured 2026-08-31: `viewpointministriesinternational.org`'s
> WordPress has `siteurl` **and** `home` set to `https://vpmin.org`, so its REST `link` fields, its
> sitemap entries and its `/wp-content/uploads/` references all name the empty domain. All 19
> `wp/v2/pages` links are on vpmin.org; every one 404s there; every one returns 200 on the serving
> domain (11/11 probed, pages and uploads alike). It is one site with a stale self-reference, not
> two sites. Correcting it, measured on the same runner:
>
> |                        | untreated | normalized    |
> | ---------------------- | --------- | ------------- |
> | entries captured       | 343 / 590 | **589 / 590** |
> | page-fetch failures    | 247       | **0**         |
> | asset failures         | 823       | **15**        |
> | assets downloaded      | 177       | **1838**      |
> | `wp/v2/pages` captured | 1 / 19    | **19 / 19**   |
>
> So the two situations are opposites and are told apart by ONE reading, not by reasoning about
> which domain is "real":
>
> ```bash
> curl -s https://<serving-domain>/wp-json/ | jq '{url, home}'
> ```
>
> If that names a host other than the one serving the site, its self-references are stale and must
> be **normalized onto the serving host** — 705/706 now derive this automatically
> (`declaredSelfHost`) and report it, so no operator has to spot it. If it agrees, then a foreign
> host in the markup really is foreign, and `ignore_hosts` drops it without inventing content.
>
> Two corollaries worth carrying:
>
> - **A stale self-reference is invisible in the inventory count.** `localPathForLink` maps by
>   pathname, so a wrong-host link still yields a correct local path: the entry count stays right
>   while the fetch 404s. The number that looks healthiest is the one that cannot see this.
> - **It is a live defect, not just a migration obstacle.** Those URLs are 404ing for real visitors
>   right now. Report it to whoever owns the WordPress; the fix is a search-replace on
>   `siteurl`/`home`, and it is worth doing whether or not the site is being migrated.
> - **What survives the correction is what `ignore_hosts` is actually for.** The 15 remaining asset
>   failures are the genuine article: 11×404 for WordPress.com scaffolding the site still references
>   but does not serve (`remote-login.php?wpcom_remote_login=validate`,
>   `wp-content/js/rlt-proxy.js`, `mu-plugins/actionbar/actionbar.js`, a `themes/a8c/…` watermark —
>   leftovers of an earlier move off WordPress.com), and 4×**410 Gone** on
>   `myviewletstalkaboutjesus.files.wordpress.com`. A 410 is the origin stating the resource is
>   deliberately gone. Neither can be recovered by rewriting a hostname, and both 404 for live
>   visitors too. Drop them with `ignore_hosts`; do not normalize them.
>
> Beware near-miss matches when reconciling the inventory: `sites-list/` contains
> `viewpointnorth.org`, an unrelated organization.

**Divi / page builders make the rendered scrape mandatory, not optional.** When the namespace list
carries `divi/v1` (or the markup shows Elementor, WPBakery, Fusion/Avada), the real page markup
lives in postmeta and `content.rendered` is a shortcode husk — a REST-only export of such a site
reproduces almost nothing. Path C scrapes the rendered URL for exactly this reason.

**Forminator / contact forms** (namespace `forminator/v1`) have no backend after migration. Apply
the Forms gotcha below: replace with `mailto:` or a preserved external service; never ship a form
that silently posts to a dead endpoint.

Use Path A or B when the site is not WordPress at all, or when its REST API is blocked (the inspect
verdict says `API_BLOCKED`).

**Path A — `capture_site.py`** (repo `FFC-Static-Site-Capture-Tools`, local
`C:\vscode\FFC-Static-Site-Capture-Tools`):

```powershell
pip install -r requirements.txt
python .\scripts\capture_site.py https://<domain>/ --out .\static --max 500 --delay 0.2
```

- Crawls same-domain pages (plus `/sitemap.xml` when present), saves each page as
  `<path>/index.html`, downloads CSS/JS/images/fonts/media, and rewrites `href`/`src` (and basic
  `srcset`) to local relative paths. Query-string assets are saved as `…__q=<query><ext>`.
- **Etiquette:** keep `--delay` at 0.2s or higher and `--max` sane (default 500) — these are small
  shared-hosting origins; do not hammer them.
- External links stay external by design; external **assets** are the thing you must verify (next
  step).

**Path B — httrack pipeline** (`scripts/clone-site-static.mjs` +
`scripts/integrate-clone-into-nextjs.mjs` in this repo) — see the
[static-clone runbook](../../../docs/ffc-ex-static-clone-runbook.md). It additionally localizes
CDN/S3-hosted page assets and Google Fonts, and writes a `clone-report.json`
(pages/localizedImages/remainingExternalHosts) you can gate on.

**Localization verification (hard gate — "zero external asset hosts"):** grep the captured output
for anything still loading off-site, e.g.:

```bash
grep -rEoh 'https?://[^"'\'' )]+' <capture-dir> --include='*.html' --include='*.css' \
  | grep -vE '(<domain>|^https?://(www\.)?(twitter|facebook|instagram|youtube)\.com)' | sort -u
```

Every hit that is an **asset** (images, CSS, JS, fonts — `fonts.googleapis.com`,
`fonts.gstatic.com`, `i0.wp.com`, `secure.gravatar.com`, CDN/S3 hosts, srcset variants) must be
downloaded and the reference rewritten. Outbound `<a>` links to other organizations are fine and
stay.

**Gotchas from the 2026-05 wave:**

- **Forms** — there is no backend after migration. Replace WP contact forms with `mailto:` links or
  a preserved external service; **keep** working external donation/embed providers (Givebutter,
  Donorbox, Team Shop links) as-is. Never ship a form that silently posts to a dead WP endpoint.
- **Embeds** — replace heavy YouTube iframes with a lite-embed pattern; keep the video external
  (that is an allowed external host, it is not a page asset).
- **`srcset`** — the crawler's srcset handling is basic; grep specifically for `srcset=` with
  remaining `http` URLs and localize every size variant.
- **Fonts** — Google Fonts CSS must be downloaded and its inner `gstatic` URLs localized too
  (two-level fetch); Path B does this automatically.
- **JS/dynamic content** — the capture is rendered-HTML only; API-driven widgets (calendars, search)
  need a static replacement or removal. Blogs that are dormant can be dropped (decision precedent:
  falloutshelterecovillage).
- **WAF-blocked origins** — instituteofforgiveness was solved by driving a real browser (Playwright
  MCP) instead of `requests`.
- **Password-walled sites** — capture the public pages only; open a blocked follow-up issue for the
  gated content (bucktownbullsbaseball precedent).
- **Suspended/parked sites** — don't clone a suspension page; ship a minimal "Under Development"
  placeholder instead (amargraves precedent).
- **CDN-injected instrumentation is not the site's content, and no capture can mirror it.**
  Cloudflare's beacon fabricates a request to the same-origin `/cdn-cgi/rum` at runtime — it appears
  in no attribute, so the asset inventory never sees it, and `/cdn-cgi/*` is answered by the edge,
  so it exists on no origin. Measured on viewpointministriesinternational.org: **every one of 120
  pages** failed the self-containment gate on it. 705 now strips the beacon and any `/cdn-cgi/`
  script — and decodes Cloudflare's `email-protection` obfuscation first, because removing the
  decoder without that leaves every obfuscated address rendered as hex.
- **A reference that is already dead on the source is not a clone defect.** The same run failed on
  `/dist/widgets.css?v=2110`, fabricated at runtime by a leftover WordPress.com script and 404 on
  the live site too. `verify-no-legacy` now asks the source whether it serves a missing asset:
  404/410 (or a soft 404 — HTML served for a `.css`) is reported, anything the source still serves
  stays fatal, and a probe that could not run stays fatal. `--no-source-probe` restores the old
  always-fatal behaviour.
- **A URL that lives only inside an inline `<script>` config blob is invisible to every attribute
  scan.** WordPress emits

  ```html
  <script>
    window._wpemojiSettings = {
      source: {
        concatemoji: 'https:\/\/example.org\/wp-includes\/js\/wp-emoji-release.min.js?ver=6.7',
      },
    };
  </script>
  ```

  — no `src`, no `href`, JSON-escaped slashes. On viewpointministriesinternational.org it was the
  ONE asset the source still served (HTTP 200) that the clone had lost, and the self-containment
  gate caught it on 2 of 120 pages. 705 now scans inline script bodies for asset-extension strings.

- **A non-ASCII upload filename breaks unless the local name is percent-DECODED.**
  `new URL().pathname` is encoded, so writing it verbatim produces a file literally named `…%C3%97…`
  while every request for it is decoded before the filesystem lookup — the asset 404s in the export
  and on GitHub Pages alike. Any upload a human named (`Digest-2160-×-1080-px.jpg`, `Crèche.png`)
  hits this. Decode, but ONLY where it cannot change the path's shape: `..%2f` decodes into real
  traversal, so a decoded form containing a `..` segment falls back to the encoded one.
- **`&amp;` in an attribute URL must be decoded before fetching.** It is how a multi-parameter URL
  is spelled in HTML; requesting the literal entity asks the origin for a query string it does not
  have.
- **A site that moved off WordPress.com carries its old host's scripts.** This one still requests
  `remote-login.php`, `rlt-proxy.js`, `bilmur.min.js`, `actionbar.js` and `_static/??-eJx…`
  concatenated CSS — all 404 on the live site. Expect a handful of permanent asset failures and read
  them as the source's history, not the capture's failure.

## 3. Repo: `FFC-EX-<domain>`, proven scaffold, CI green

1. **Repo name is exactly `FFC-EX-<domain>`** (apex domain, no `www`). If it doesn't exist, create
   it via **`701. Website - Provision`** (issue template 07 + assign; see the `charity-onboarding`
   skill Phase 3) — gated on `github-prod` (+ `cloudflare-prod-write` when the zone is in FFC CF).
2. **Scaffold from the proven repo pattern** — reference repos: `FFC-EX-southamptonfriends.org`
   (baseline) and `FFC-EX-catnipandcattitude.org` (current best practice). Workflow files to carry:
   `ci.yml` (Test and Build), `deploy.yml`, `lighthouse.yml`; Catnip adds `drift-check.yml` and
   `post-deploy-smoke.yml`.
3. **basePath/assetPrefix rule** (`next.config`): `output: 'export'` with
   `basePath: process.env.NEXT_PUBLIC_BASE_PATH || ''` and the **same** value for `assetPrefix`.
   `deploy.yml` derives the value at build time: **`public/CNAME` present → empty basePath (custom
   domain, root)**; **no CNAME → `/<repo-name>` (github.io subpath)**. For this phase there is **no
   `public/CNAME`**, so the build serves the default subpath URL.
4. **The lighthouse subpath fix:** `lighthouse.yml` must derive `NEXT_PUBLIC_BASE_PATH` with the
   **identical CNAME-based logic** as `deploy.yml` (Catnip pattern). The stale template hardcodes
   `NEXT_PUBLIC_BASE_PATH: /FFC_Single_Page_Template`, which audits 404 pages — fix it when
   scaffolding.
5. **Localized clone → real routes.** Dropping the raw capture into `public/` deploys (export copies
   `public/` verbatim into `out/`) but leaves CI hollow. The CI-green pattern
   (Catnip/browncanyonranch) is to convert the dump into real `src/app` routes; the integration
   script parks colliding template routes in `_disabled_template_routes/`. Strip or update legacy
   template E2E specs that reference removed components (GTM, mission-video, animated-numbers,
   social-links) — that was the #1 wave fix-up.
6. **CI green definition:** `CI - Build and Test` passes, plus Lighthouse where scaffolded. Known
   pre-existing failures that are **not** yours to chase per-PR: the CodeQL
   Default-Setup-vs-Advanced conflict (fix by disabling one), and Lighthouse 404s before the first
   deploy exists.

## 4. Footer standard — at the correct level, never fabricated

Inject the FFC footer standard per the ffcadmin checklist:
[`docs/footer-standard-adoption-checklist.md`](https://github.com/FreeForCharity/FFC-IN-ffcadmin.org/blob/main/docs/footer-standard-adoption-checklist.md)
(worked example: FFC-EX-catnipandcattitude.org PR #29).

- **Two passing levels.** **Level 1 (pre-501c3):** full footer **minus** the 501(c)(3) status line
  and the Candid/GuideStar link — a pre-501c3 site claiming 501c3 status is a false legal claim and
  a fleet-audit violation. **Level 2 (full 501c3):** Level 1 plus both items.
- **Never fabricate data.** EIN, legal name, and nonprofit status come from the charity's validated
  application (generate with ffcadmin's `scripts/generate-footer-config.mjs`); if the data is
  missing, that is an onboarding gap to send back — **do not guess or invent an EIN/status**.
- Keep the site's own design; the standard fixes content, not look. Watch WCAG contrast when
  restyling footer links in brand colors (Catnip lesson).

## 5. Enable Pages on the DEFAULT URL

Target for this phase is `https://freeforcharity.github.io/FFC-EX-<domain>/` — **not** a custom
domain. The `staging.<domain>` / apex custom-domain stage comes later, with cutover.

- Repos created by workflow 701 already have Pages enabled (Actions build). Otherwise:
  `gh api -X POST repos/FreeForCharity/FFC-EX-<domain>/pages -f build_type=workflow`.
- Ensure **no `public/CNAME`** is committed and no `custom_domain` is set on the Pages config
  (`gh api repos/FreeForCharity/FFC-EX-<domain>/pages` to inspect) — a stale custom domain flips the
  basePath and 404s every asset.
- Merge the migration PR → `deploy.yml` runs → verify the deployment landed on the default URL.

## 6. DNS-ready definition (cutover staged, NOT executed)

A site is **DNS-ready** when both of these hold:

1. **Workflow `121. DNS + GH Pages - Fleet Cutover Preflight`** returns a **READY verdict** for the
   domain (dispatch with `domains=<domain>`, optionally `marker=<charity name>`). It is read-only
   and ungated: confirms the Pages origin is healthy, reads live apex/www DNS over DoH, checks CAA
   permits Let's Encrypt, and probes HTTPS.
2. **Cutover artifacts are staged, not merged** (Catnip pattern, its PR #25): a PR adding
   `public/CNAME` (+ any basePath-integrity guard) is opened and **held open** — merging it is the
   cutover trigger and happens only under the separately-gated cutover step (workflow 120 /
   `ffc-cutover` skill). Do not merge the CNAME PR as part of this skill.

## 7. Verification

- **Live default-URL check:** `curl -sSIL https://freeforcharity.github.io/FFC-EX-<domain>/` returns
  200, and the HTML contains a charity-specific content marker (not template text).
- **Gate3-validate-style checks** (ffcadmin `scripts/gate3-validate.mjs` pattern): footer brand
  text, freeforcharity.org link, EIN (Level 2 only), HTTP 200, basePath sanity — no `/_next` or
  image reference missing the `/FFC-EX-<domain>` prefix.
- **Fleet-audit row flip:** re-run ffcadmin's `scripts/fleet-audit.mjs` (or the fleet audit
  workflow) and confirm the site's row moves to the migrated/compliant state.

## 8. Tracking

- **Per-site issue in the `FFC-EX-<domain>` repo** describing the migration (tier, capture notes,
  deferred content, follow-up sub-issues for phased/gated content — americanlegionpost64 precedent).
- **Progress comment on epic #702** in this repo when the site reaches DNS-ready: link the repo, the
  migration PR, the 121 run, and the held CNAME PR.
