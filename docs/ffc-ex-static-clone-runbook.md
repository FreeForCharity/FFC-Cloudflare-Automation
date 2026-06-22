# FFC-EX static-clone runbook (WordPress → Next.js static export)

The FFC-EX sites must be **faithful static clones of the live WordPress sites** (exact visuals, all
assets localized) served by each repo's **Next.js `output: 'export'`** build after WordPress is
decommissioned. The current staging builds are unfinished template scaffolds (see
[`ffc-ex-clone-fidelity-audit.md`](./ffc-ex-clone-fidelity-audit.md)). This runbook regenerates them
from the live sites.

## Pipeline

Two scripts in `scripts/`, plus the repo's own `next build`:

1. **`clone-site-static.mjs`** — mirrors the live site with `httrack` (strict containment: only the
   target domain's HTML, but pulls page assets/images even when CDN/S3-hosted, and Google Fonts),
   localizes links, and writes `clone-report.json` (page count, localized image count, remaining
   external hosts). Read-only against the live site.

2. **`integrate-clone-into-nextjs.mjs`** — drops the clone into the repo's `public/` and moves the
   template's `src/app/**/page.*` routes aside (to a `_disabled_template_routes/` backup) so they
   don't collide with the clone's pages. With `output: 'export'`, `next build` copies `public/`
   verbatim into `out/`, so the export ships the exact clone. Writes `public/CNAME` (apex).

3. **`next build`** in the FFC-EX repo → `out/` is the deployable static clone.

## Steps (per domain)

```bash
# 1. Clone the live site (run from this automation repo; httrack required)
node scripts/clone-site-static.mjs \
  --domain browncanyonranch.org --out /tmp/clone/bcr --depth 8 --exclude /beta

# 2. Integrate into a checkout of the matching FFC-EX repo
node scripts/integrate-clone-into-nextjs.mjs \
  --clone /tmp/clone/bcr/browncanyonranch.org \
  --repo  ../FFC-EX-browncanyonranch.org \
  --domain browncanyonranch.org

# 3. Build + preview in the FFC-EX repo
cd ../FFC-EX-browncanyonranch.org
npm ci && npm run build      # produces out/ (the static clone)
npx serve out                # spot-check vs the live site, then commit + PR
```

## Verification gate (before any cutover)

- `clone-report.json` `localizedImages` ≈ live image count (not 0).
- Visual diff of the built `out/` homepage + key inner pages vs the live site.
- `remainingExternalHosts` reviewed: outbound `<a>` links are fine; asset hosts (fonts/CDN images)
  should be localized or consciously accepted.
- Only then run the cutover (workflow 19). **Do not bulk-cut-over** until each domain passes this
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
