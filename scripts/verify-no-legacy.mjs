#!/usr/bin/env node
/**
 * verify-no-legacy.mjs — prove an FFC-EX static clone is genuinely
 * self-contained before the DNS cutover.
 *
 * The fidelity signals we had were both blind in the same place.
 * `clone-site-static.mjs` reports `remainingExternalHosts` from a regex over
 * `src`/`href` attributes, and `ffc-ex-clone-fidelity-audit.md` compares image
 * counts. Neither can see:
 *
 *   1. URLs inside HTML-entity-escaped JSON. Elementor stores widget config in
 *      `data-settings`, where a URL is delimited by `&quot;` rather than a quote
 *      character, so no `src=`/`href=` match exists.
 *   2. URLs assembled at runtime. Elementor / Essential Addons / ElementsKit
 *      build content-hashed webpack chunk URLs in JavaScript. They appear in no
 *      attribute anywhere, so no static scan of any kind can find them.
 *
 * Class 2 is why slopestohope.org shipped with its "pounds distributed" counter
 * frozen at 0 for months: the counter markup ships a literal 0 and relies on
 * `counter.<hash>.bundle.min.js` to animate it. httrack never mirrored the
 * chunk, and the clone report was clean.
 *
 * This gate is immune to both because it does not scan anything. It loads each
 * page in a real browser with every request to the legacy host aborted, and
 * fails on either:
 *
 *   - a request to the legacy host  -> a dependency survived the clone
 *   - a same-origin request that 404s -> the mirror is missing an asset
 *
 * The second condition matters as much as the first. An earlier version treated
 * same-origin failures as non-fatal and reported 13/13 pages passing while four
 * Elementor bundles were silently 404ing.
 *
 * Playwright is imported dynamically so this repo stays dependency-free; CI
 * installs it just before invoking this script (see workflow 702).
 *
 * Usage:
 *   node scripts/verify-no-legacy.mjs --domain example.org --dir ../FFC-EX-example.org/out
 *   node scripts/verify-no-legacy.mjs --domain example.org --base https://example.org
 *   node scripts/verify-no-legacy.mjs --domain example.org --dir out --pages /,/about/
 *   node scripts/verify-no-legacy.mjs --domain example.org --dir out --shots /tmp/shots
 *
 * Exit codes:
 *   0  every page is self-contained
 *   1  at least one page has a surviving legacy dependency or a missing asset
 *   2  invalid usage / could not start
 */

import { createServer } from 'node:http';
import { readFile, mkdir, readdir } from 'node:fs/promises';
import { join, extname, relative, resolve, isAbsolute, sep } from 'node:path';
import { existsSync } from 'node:fs';

function arg(name, def = undefined) {
  const i = process.argv.indexOf(`--${name}`);
  return i > -1 && process.argv[i + 1] ? process.argv[i + 1] : def;
}

if (process.argv.includes('--self-test')) {
  const d = 'example.org';
  const legacy = (url) => {
    let hostname;
    try {
      hostname = new URL(url).hostname.toLowerCase();
    } catch {
      return false;
    }
    return hostname === d || hostname.endsWith(`.${d}`);
  };
  const cases = [
    ['apex is legacy', legacy('https://example.org/a.jpg'), true],
    ['www is legacy', legacy('https://www.example.org/a.jpg'), true],
    ['any subdomain is legacy', legacy('https://staging.example.org/a.jpg'), true],
    ['uppercase host still matches', legacy('https://EXAMPLE.ORG/a.jpg'), true],
    // A substring test would abort this request; it is a third party, not the
    // origin being retired.
    [
      'domain in a query string is NOT legacy',
      legacy('https://cdn.other.com/x?ref=example.org'),
      false,
    ],
    [
      'host merely ending in the bare name is NOT legacy',
      legacy('https://charity.rallyup.com/w'),
      false,
    ],
    // join() normalises `..`, so a startsWith() prefix test would pass here.
    [
      'sibling-dir traversal is contained',
      resolveWithin('/tmp/site', '/../site2/x') === null,
      true,
    ],
    [
      'encoded traversal is contained',
      resolveWithin('/tmp/site', decodeURIComponent('/%2e%2e/x')) === null,
      true,
    ],
    ['normal path resolves', resolveWithin('/tmp/site', '/wp-content/a.jpg') !== null, true],
  ];
  let failed = 0;
  for (const [name, got, want] of cases) {
    if (got !== want) {
      failed++;
      console.error(`FAIL ${name} (got ${got}, want ${want})`);
    } else {
      console.log(`ok   ${name}`);
    }
  }
  console.log(failed ? `${failed} failure(s)` : `${cases.length}/${cases.length} passed`);
  process.exit(failed ? 2 : 0);
}

const domain = arg('domain');
const dir = arg('dir');
const base = arg('base');
const shots = arg('shots');
const pagesArg = arg('pages');
const maxPages = parseInt(arg('max-pages', '40'), 10);

if (!domain || (!dir && !base)) {
  console.error(
    'Usage: --domain <apexDomain> (--dir <staticDir> | --base <url>) [--pages /a/,/b/] [--shots <dir>] [--max-pages N]',
  );
  process.exit(2);
}
if (dir && !existsSync(dir)) {
  console.error(`[verify] static dir not found: ${dir}`);
  process.exit(2);
}

/**
 * Is this URL served by the WordPress origin being retired?
 *
 * Compares the parsed hostname rather than substring-matching the whole URL.
 * A substring test both false-matches (`https://cdn.other.com/?ref=example.org`
 * is not a legacy dependency, but would be aborted) and false-misses (a
 * differently-cased or punycoded host would slip through the gate).
 *
 * Any subdomain counts: staging.<domain> is the same install being retired.
 * Note this deliberately does not match a third-party host that merely *ends*
 * with the bare name, e.g. `<charity>.rallyup.com`.
 */
function isLegacy(url) {
  let hostname;
  try {
    hostname = new URL(url).hostname.toLowerCase();
  } catch {
    return false;
  }
  const d = domain.toLowerCase();
  return hostname === d || hostname.endsWith(`.${d}`);
}

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css',
  '.js': 'text/javascript',
  '.mjs': 'text/javascript',
  '.json': 'application/json',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.svg': 'image/svg+xml',
  '.webp': 'image/webp',
  '.avif': 'image/avif',
  '.ico': 'image/x-icon',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.ttf': 'font/ttf',
  '.eot': 'application/vnd.ms-fontobject',
  '.mp4': 'video/mp4',
  '.webm': 'video/webm',
};

/**
 * Resolve a request path inside `root`, or null if it escapes.
 *
 * A `startsWith(root)` prefix test is not containment: join() normalises `..`,
 * so `/../site2/x` under root `/tmp/site` yields `/tmp/site2/x`, which still
 * shares the prefix. Compare the relative path instead.
 */
function resolveWithin(root, requestPath) {
  const abs = resolve(root, requestPath.replace(/^\/+/, ''));
  const rel = relative(resolve(root), abs);
  if (rel.startsWith('..') || isAbsolute(rel)) return null;
  return abs;
}

function startServer(root) {
  const server = createServer(async (req, res) => {
    try {
      let p = decodeURIComponent(req.url.split('?')[0]);
      if (p.endsWith('/')) p += 'index.html';
      const file = resolveWithin(root, p);
      if (!file) {
        res.writeHead(403).end();
        return;
      }
      const body = await readFile(file);
      res.writeHead(200, {
        'Content-Type': MIME[extname(file).toLowerCase()] || 'application/octet-stream',
      });
      res.end(body);
    } catch {
      res.writeHead(404, { 'Content-Type': 'text/plain' }).end('Not Found');
    }
  });
  return new Promise((resolve) => server.listen(0, () => resolve(server)));
}

/** Every directory containing an index.html becomes a page path to check. */
async function discoverPages(root) {
  const found = [];
  async function walk(d) {
    for (const e of await readdir(d, { withFileTypes: true })) {
      const p = join(d, e.name);
      if (e.isDirectory()) {
        // Framework output, not site pages.
        if (e.name === '_next' || e.name === 'node_modules') continue;
        await walk(p);
      } else if (e.name === 'index.html') {
        const rel = relative(root, d).split(sep).filter(Boolean).join('/');
        found.push(rel ? `/${rel}/` : '/');
      }
    }
  }
  await walk(root);
  // Shallowest first, so the homepage and top-level pages are checked even when
  // the cap trims a large mirror.
  return found.sort((a, b) => a.split('/').length - b.split('/').length || a.localeCompare(b));
}

async function main() {
  let chromium;
  try {
    ({ chromium } = await import('playwright'));
  } catch {
    console.error(
      '[verify] playwright is not installed.\n' +
        '        This repo is intentionally dependency-free; install it just for this run:\n' +
        '          npm i --no-save playwright && npx playwright install --with-deps chromium',
    );
    process.exit(2);
  }

  let server;
  let origin = base;
  if (!origin) {
    server = await startServer(dir);
    origin = `http://127.0.0.1:${server.address().port}`;
  }

  let pages = pagesArg ? pagesArg.split(',').filter(Boolean) : null;
  if (!pages) {
    pages = dir ? await discoverPages(dir) : ['/'];
  }
  let truncated = 0;
  if (pages.length > maxPages) {
    truncated = pages.length - maxPages;
    pages = pages.slice(0, maxPages);
  }

  if (shots) await mkdir(shots, { recursive: true });

  const browser = await chromium.launch({
    executablePath: process.env.CHROMIUM_PATH || undefined,
  });

  const results = [];
  for (const path of pages) {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const tab = await ctx.newPage();

    const legacyHits = [];
    const localMissing = [];
    const thirdPartyFailed = [];

    // Aborting rather than allowing is the whole point: a surviving dependency
    // fails loudly here instead of quietly in production after cutover.
    await tab.route('**://*/**', (route) => {
      const url = route.request().url();
      if (isLegacy(url)) {
        legacyHits.push(url);
        return route.abort();
      }
      return route.continue();
    });

    tab.on('requestfailed', (r) => {
      const url = r.url();
      if (isLegacy(url)) return;
      const entry = `${url} (${r.failure()?.errorText})`;
      // Same-origin failures mean the mirror is incomplete. Third-party hosts
      // may simply be unreachable from CI, so those are reported, not fatal.
      if (url.startsWith(origin)) localMissing.push(entry);
      else thirdPartyFailed.push(entry);
    });
    tab.on('response', (r) => {
      const url = r.url();
      if (url.startsWith(origin) && r.status() === 404) {
        localMissing.push(`${url} (HTTP 404)`);
      }
    });

    const problems = [];
    try {
      const resp = await tab.goto(origin + path, { waitUntil: 'load', timeout: 45000 });
      if (resp && resp.status() >= 400) problems.push(`HTTP ${resp.status()}`);

      // Drive the page so lazily-initialised widgets request their chunks; a
      // widget that never scrolls into view never reveals a missing handler.
      await tab.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
      await tab.waitForTimeout(2500);
      await tab.evaluate(() => window.scrollTo(0, 0));
      await tab.waitForTimeout(800);

      if (shots) {
        const name = path === '/' ? 'home' : path.replace(/^\/|\/$/g, '').replace(/\//g, '_');
        await tab.screenshot({ path: join(shots, `${name}.png`) });
      }
    } catch (err) {
      problems.push(`navigation error: ${err.message}`);
    }

    if (legacyHits.length) problems.push(`${legacyHits.length} request(s) to ${domain}`);
    if (localMissing.length) problems.push(`${localMissing.length} missing local asset(s)`);

    results.push({ path, problems, legacyHits, localMissing, thirdPartyFailed });
    await ctx.close();
  }

  await browser.close();
  if (server) server.close();

  console.log(`\nVerified ${results.length} page(s) of ${domain} against ${origin}\n`);
  let failures = 0;
  for (const r of results) {
    const ok = r.problems.length === 0;
    if (!ok) failures++;
    console.log(`${ok ? 'PASS' : 'FAIL'}  ${r.path}`);
    for (const p of r.problems) console.log(`        ! ${p}`);
    for (const u of [...new Set(r.legacyHits)].slice(0, 8)) console.log(`        legacy: ${u}`);
    for (const u of [...new Set(r.localMissing)].slice(0, 8))
      console.log(`        MISSING LOCAL: ${u}`);
    for (const u of [...new Set(r.thirdPartyFailed)].slice(0, 3)) {
      console.log(`        third-party unreachable (not fatal): ${u}`);
    }
  }

  if (truncated) {
    console.log(`\nNote: ${truncated} further page(s) were not checked (--max-pages ${maxPages}).`);
  }
  console.log(`\n${results.length - failures}/${results.length} pages passed`);

  if (process.env.GITHUB_STEP_SUMMARY) {
    const { appendFileSync } = await import('node:fs');
    const rows = results
      .map((r) => `| ${r.path} | ${r.problems.length ? '❌ ' + r.problems.join('; ') : '✅'} |`)
      .join('\n');
    appendFileSync(
      process.env.GITHUB_STEP_SUMMARY,
      `\n### Clone self-containment — ${domain}\n\n| page | verdict |\n| --- | --- |\n${rows}\n\n` +
        `${results.length - failures}/${results.length} pages passed\n`,
    );
  }

  if (failures) process.exitCode = 1;
}

main().catch((err) => {
  console.error(err);
  process.exit(2);
});
