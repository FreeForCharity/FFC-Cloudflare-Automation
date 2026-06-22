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
  remainingExternalHosts: [...externalRefs].sort(),
};
writeFileSync(join(out, 'clone-report.json'), JSON.stringify(report, null, 2));
console.error('[clone] ' + JSON.stringify(report, null, 2));
