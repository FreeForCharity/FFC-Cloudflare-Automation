# FFC-EX static-clone runbook (WordPress → Next.js static export)

The FFC-EX sites must be **faithful static clones of the live WordPress sites** (exact visuals, all
assets localized) served by each repo's **Next.js `output: 'export'`** build after WordPress is
decommissioned. The current staging builds are unfinished template scaffolds (see
[`ffc-ex-clone-fidelity-audit.md`](./ffc-ex-clone-fidelity-audit.md)). This runbook regenerates them
from the live sites.

## Pipeline

Four scripts in `scripts/`, plus the repo's own `next build`:

1. **`clone-site-static.mjs`** — mirrors the live site with `httrack` (strict containment: only the
   target domain's HTML, but pulls page assets/images even when CDN/S3-hosted, and Google Fonts),
   localizes links, and writes `clone-report.json` (page count, localized image count, remaining
   external hosts). Read-only against the live site.

2. **`integrate-clone-into-nextjs.mjs`** — drops the clone into the repo's `public/` and moves the
   template's `src/app/**/page.*` routes aside (to a `_disabled_template_routes/` backup) so they
   don't collide with the clone's pages. With `output: 'export'`, `next build` copies `public/`
   verbatim into `out/`, so the export ships the exact clone. Writes `public/CNAME` (apex).

   The end state is a repo with **zero app routes**, and that is correct — `next build` succeeds
   with `src/app` holding only `layout.tsx` (verified on Next 16.2.12; the route table is just the
   framework's `/404`). Any surviving route would risk colliding with a cloned path and would export
   a stray page into the published site. The script used to write a `_clone-host/page.tsx` sentinel
   claiming to guarantee a route; underscore-prefixed folders are private in the App Router, so it
   never was one, and it was removed in #905.

3. **`sync-runtime-assets.mjs`** — mirrors the assets that only appear at runtime. httrack fetches
   what it can see in markup; Elementor / Essential Addons / ElementsKit assemble content-hashed
   webpack chunk URLs in JavaScript, so those are absent from the mirror and every widget depending
   on them dies silently. This drives each page in a browser, fetches whatever 404s, and repeats
   until a round finds nothing new (a fresh chunk routinely requests further chunks). Run it between
   steps 1 and 2.

4. **`next build`** in the FFC-EX repo → `out/` is the deployable static clone.

5. **`verify-no-legacy.mjs`** — the cutover gate. Loads every exported page with all requests to the
   live host aborted, and fails on a surviving legacy dependency **or** a missing local asset.

Steps 3 and 5 need a browser. This repo is deliberately dependency-free, so install Playwright just
for the run:

```bash
npm i --no-save playwright && npx playwright install --with-deps chromium
```

## Steps (per domain)

```bash
# 1. Clone the live site (run from this automation repo; httrack required)
node scripts/clone-site-static.mjs \
  --domain browncanyonranch.org --out /tmp/clone/bcr --depth 8 --exclude /beta

# 2. Mirror the assets only requested at runtime
node scripts/sync-runtime-assets.mjs \
  --domain browncanyonranch.org --dir /tmp/clone/bcr/browncanyonranch.org

# 3. Integrate into a checkout of the matching FFC-EX repo
node scripts/integrate-clone-into-nextjs.mjs \
  --clone /tmp/clone/bcr/browncanyonranch.org \
  --repo  ../FFC-EX-browncanyonranch.org \
  --domain browncanyonranch.org

# 4. Build in the FFC-EX repo
cd ../FFC-EX-browncanyonranch.org
npm ci && npm run build      # produces out/ (the static clone)

# 5. Gate: prove the clone no longer needs the live host at all
node ../FFC-Cloudflare-Automation/scripts/verify-no-legacy.mjs \
  --domain browncanyonranch.org --dir out

npx serve out                # spot-check vs the live site, then commit + PR
```

## Verification gate (before any cutover)

- **`verify-no-legacy.mjs` exits 0.** This is the authoritative gate. `clone-report.json` and a
  visual diff both miss two whole classes of dependency (#902): URLs inside entity-escaped Elementor
  JSON, and URLs JavaScript assembles at runtime. The second is why slopestohope.org shipped with
  its counter frozen at `0` for months — the page _looked_ right, it just showed the wrong number.
- `clone-report.json` `localizedImages` ≈ live image count (not 0).
- Visual diff of the built `out/` homepage + key inner pages vs the live site.
- `remainingExternalHosts` reviewed: outbound `<a>` links are fine; asset hosts (fonts/CDN images)
  should be localized or consciously accepted. Note this field is a static scan of `src`/`href` only
  — treat a clean value as necessary, not sufficient.
- Only then run the cutover (workflow 120). **Do not bulk-cut-over** until each domain passes this
  gate.

## Validation status

Proven end-to-end on **browncanyonranch.org**: clone = 55 pages / 87 images / 12.5 MB; the Next.js
`output: 'export'` build serves the exact live visuals (byte-identical render to the clone). The
integration script correctly disables colliding template routes (`contact`, `donate`, root) and the
build stays green.

## Known blocker (registrar track, separate)

WHMCS API calls from GitHub Actions runners are intermittently blocked by **Imunify360
bot-protection** (`Access denied … IPs should be whitelisted`) on `freeforcharity.org/hub`. EPP/lock
workflows may need retries or the Actions IP ranges whitelisted in Imunify360.
