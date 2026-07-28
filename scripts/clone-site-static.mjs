#!/usr/bin/env node
/**
 * clone-site-static.mjs — produce a faithful, self-contained static clone of a
 * live WordPress (often Divi) site so it can be served from GitHub Pages after
 * WordPress is decommissioned (FFC-EX cutover; project #157).
 *
 * Wraps httrack with strict containment so the clone:
 *   - mirrors ONLY the target domain's HTML pages (no spidering into linked
 *     external sites), but
 *   - still pulls in page assets (images/CSS/JS/fonts) referenced off-domain
 *     (e.g. CDN/S3-hosted wp-content uploads, Google Fonts) and rewrites links
 *     to local — so the result has the exact visuals with assets localized.
 *
 * It then VERIFIES the clone (page count, localized image count, and any
 * still-external http(s) references it could not localize) and writes a JSON
 * report. It changes nothing on the live site (read-only HTTP GETs).
 *
 * Usage:
 *   node scripts/clone-site-static.mjs --domain <domain> --out <dir> \
 *        [--depth 8] [--timeout 600] [--exclude /beta,/members]
 *
 * The servable site root is <out>/<domain>/ (index.html at its root).
 */
import { spawnSync } from 'node:child_process';
import { readdirSync, statSync, writeFileSync, existsSync, readFileSync } from 'node:fs';
import { join, extname } from 'node:path';

function arg(name, def = undefined) {
  const i = process.argv.indexOf(`--${name}`);
  return i > -1 && process.argv[i + 1] ? process.argv[i + 1] : def;
}

/**
 * Build the pattern that finds legacy absolute URLs for `domain`.
 *
 * The entity boundary must stop the match itself rather than be trimmed after:
 * a global replace resumes scanning after the whole match, so a pattern that
 * runs past `&quot;` swallows the rest of an escaped-JSON blob and every
 * following URL in it silently goes unrewritten.
 */
export function legacyUrlPattern(domain) {
  const ENTITY_BOUNDARY = '&(?:quot|apos|lt|gt|#0?3[49]|#x27);';
  const URL_CHAR = `(?:(?!${ENTITY_BOUNDARY})[^\\s"'<>()\\\\])`;
  const escapedDomain = domain.replace(/\./g, '\\.');
  // Matches the plain form and the JSON-escaped `https:\/\/host\/path` form.
  return new RegExp(
    `https?:(?:\\\\?/){2}(?:www\\.)?${escapedDomain}((?:\\\\?/${URL_CHAR}*)*)`,
    'g',
  );
}

/** Drop scheme+host from every legacy URL, keeping the path as-written. */
export function localizeLegacyUrls(html, domain) {
  return html.replace(legacyUrlPattern(domain), (_m, path) => path || '/');
}

if (process.argv.includes('--self-test')) {
  const cases = [
    {
      name: 'entity-escaped JSON: every URL in the blob is rewritten',
      domain: 'example.org',
      in:
        '{&quot;url&quot;:&quot;https://example.org/wp-content/a.jpg&quot;},' +
        '{&quot;url&quot;:&quot;https://example.org/wp-content/b.webp&quot;}',
      want:
        '{&quot;url&quot;:&quot;/wp-content/a.jpg&quot;},' +
        '{&quot;url&quot;:&quot;/wp-content/b.webp&quot;}',
    },
    {
      name: 'JSON-escaped slashes are preserved',
      domain: 'example.org',
      in: '"bg":"https:\\/\\/example.org\\/wp-content\\/c.png"',
      want: '"bg":"\\/wp-content\\/c.png"',
    },
    {
      name: 'www host is localized too',
      domain: 'example.org',
      in: '<img src="https://www.example.org/wp-content/d.jpg">',
      want: '<img src="/wp-content/d.jpg">',
    },
    {
      name: 'other hosts are left alone',
      domain: 'example.org',
      in: '<img src="https://cdn.other.com/e.jpg">',
      want: '<img src="https://cdn.other.com/e.jpg">',
    },
    {
      name: 'bare origin becomes root',
      domain: 'example.org',
      in: '<a href="https://example.org">home</a>',
      want: '<a href="/">home</a>',
    },
  ];
  let failed = 0;
  for (const c of cases) {
    const got = localizeLegacyUrls(c.in, c.domain);
    if (got !== c.want) {
      failed++;
      console.error(`FAIL ${c.name}\n  want: ${c.want}\n  got : ${got}`);
    } else {
      console.log(`ok   ${c.name}`);
    }
  }
  console.log(failed ? `${failed} failure(s)` : `${cases.length}/${cases.length} passed`);
  process.exit(failed ? 2 : 0);
}

const domain = arg('domain');
const out = arg('out');
const depth = parseInt(arg('depth', '8'), 10);
const timeoutSec = parseInt(arg('timeout', '600'), 10);
const extraExcludes = (arg('exclude', '') || '').split(',').filter(Boolean);
if (!domain || !out) {
  console.error('Usage: --domain <domain> --out <dir> [--depth N] [--timeout S] [--exclude /a,/b]');
  process.exit(2);
}

const UA = 'Mozilla/5.0 (FFC static-clone bot; +https://freeforcharity.org)';
// Asset hosts we DO want localized when referenced by the site's pages.
const assetHostFilters = ['+*.gstatic.com/*', '+fonts.googleapis.com/*', '+fonts.gstatic.com/*'];
// WordPress endpoints / dynamic cruft that should never be in a static clone.
const dropFilters = [
  `-${domain}/wp-json/*`,
  `-${domain}/xmlrpc*`,
  '-*/feed/*',
  '-*/comments/feed/*',
  '-*/wp-json/*',
  ...extraExcludes.map((p) => `-${domain}${p.startsWith('/') ? '' : '/'}${p.replace(/^\//, '')}/*`),
];

// httrack filter order matters: deny everything, then allow the target domain
// (HTML + same-host assets), then allow specific off-host asset providers.
// --near pulls images/objects referenced by a kept page even if off-domain,
// while -%e0 stops httrack from following <a> links into external HTML sites.
const filters = ['-*', `+${domain}/*`, ...assetHostFilters, ...dropFilters];

const args = [
  `https://${domain}/`,
  '-O',
  out,
  '--mirror',
  '--near', // grab page assets (images, etc.) even if hosted off-domain
  '-%e0', // external HTML depth 0: do NOT spider into linked external sites
  `-r${depth}`,
  '-c6', // connections
  '-A50000000', // per-file size cap (50 MB)
  '--robots=0',
  '--disable-security-limits',
  '-F',
  UA,
  '-%v', // verbose progress
  ...filters,
];

console.error(`[clone] httrack ${domain} (depth=${depth}, timeout=${timeoutSec}s)`);
const res = spawnSync('httrack', args, {
  stdio: ['ignore', 'ignore', 'inherit'],
  timeout: timeoutSec * 1000,
});
const timedOut = res.error && res.error.code === 'ETIMEDOUT';
if (timedOut)
  console.error('[clone] httrack hit the time budget; verifying what was captured so far.');
else if (res.status !== 0 && res.status !== null)
  console.error(`[clone] httrack exit ${res.status}`);

const siteRoot = join(out, domain);
if (!existsSync(join(siteRoot, 'index.html'))) {
  console.error(`[clone] ERROR: no index.html at site root ${siteRoot}`);
  process.exit(1);
}

// Walk the captured site root.
const IMG = new Set(['.png', '.jpg', '.jpeg', '.webp', '.gif', '.svg', '.avif', '.ico']);
let htmlPages = 0,
  images = 0,
  totalBytes = 0;
const htmlFiles = [];
function walk(dir) {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name);
    if (e.isDirectory()) {
      walk(p);
      continue;
    }
    const ext = extname(e.name).toLowerCase();
    totalBytes += statSync(p).size;
    if (ext === '.html' || ext === '.htm') {
      htmlPages++;
      htmlFiles.push(p);
    } else if (IMG.has(ext)) images++;
  }
}
walk(out);

// Localize legacy URLs httrack could not see, then count what is still external.
//
// httrack rewrites links it finds in markup, but page builders also embed URLs
// inside HTML-entity-escaped JSON (Elementor's data-settings, where a URL is
// delimited by `&quot;` rather than a quote character). Those survive the mirror
// pointing at the host being decommissioned, and after the DNS cutover they
// resolve back to this same static site and 404.
//
// The entity boundary has to stop the match itself, not be trimmed afterwards:
// a global replace resumes scanning after the whole match, so a pattern that
// runs past `&quot;` swallows the rest of the JSON blob and every following URL
// in it silently goes unrewritten.
let localizedInPlace = 0;
for (const f of htmlFiles) {
  const html = readFileSync(f, 'utf8');
  const rewritten = localizeLegacyUrls(html, domain);
  if (rewritten !== html) {
    writeFileSync(f, rewritten);
    localizedInPlace++;
  }
}

// Count http(s) references that remain external (not localized) in the HTML.
const externalRefs = new Set();
const refRe = /(?:src|href)\s*=\s*["'](https?:\/\/[^"']+)["']/gi;
for (const f of htmlFiles.slice(0, 400)) {
  const html = readFileSync(f, 'utf8');
  let m;
  while ((m = refRe.exec(html))) {
    try {
      const host = new URL(m[1]).host;
      if (host !== domain && !host.endsWith('.googleapis.com') && !host.endsWith('.gstatic.com')) {
        externalRefs.add(host);
      }
    } catch {
      /* ignore */
    }
  }
}

const report = {
  domain,
  siteRoot,
  timedOut: Boolean(timedOut),
  htmlPages,
  localizedImages: images,
  totalMB: +(totalBytes / 1048576).toFixed(1),
  localizedInPlace,
  remainingExternalHosts: [...externalRefs].sort(),
  // remainingExternalHosts is a static scan of src/href only. It cannot see
  // URLs that JavaScript assembles at runtime (Elementor's content-hashed
  // webpack chunks), so it is NOT sufficient as a cutover gate on its own.
  // Run scripts/sync-runtime-assets.mjs then scripts/verify-no-legacy.mjs.
  gate: 'run sync-runtime-assets.mjs + verify-no-legacy.mjs before cutover',
};
writeFileSync(join(out, 'clone-report.json'), JSON.stringify(report, null, 2));
console.error('[clone] ' + JSON.stringify(report, null, 2));
