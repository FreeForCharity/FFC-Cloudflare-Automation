#!/usr/bin/env node
/**
 * sync-runtime-assets.mjs — mirror the assets a clone only asks for at runtime.
 *
 * httrack mirrors what it can see in the markup. Elementor / Essential Addons /
 * ElementsKit load their widget handlers as content-hashed webpack chunks whose
 * URLs are assembled in JavaScript, so they appear in no attribute and httrack
 * never fetches them. The clone looks complete and the widgets are dead — that
 * is exactly how slopestohope.org shipped with its counter stuck at 0.
 *
 * This closes the gap empirically rather than by parsing: serve the clone, drive
 * every page in a real browser, note which same-origin requests 404, fetch those
 * from the live origin, and repeat. Repetition is required — a freshly fetched
 * chunk routinely requests further chunks, so one pass is never enough. The loop
 * stops when a round discovers nothing new.
 *
 * Run it after clone-site-static.mjs and before integrate-clone-into-nextjs.mjs.
 * Read-only against the live site (GETs only); only the clone dir is written.
 *
 * Playwright is imported dynamically so this repo stays dependency-free; CI
 * installs it just before invoking this script (see workflow 702).
 *
 * Usage:
 *   node scripts/sync-runtime-assets.mjs --domain example.org --dir /tmp/clone/example.org
 *   node scripts/sync-runtime-assets.mjs --domain example.org --dir <dir> --max-rounds 8
 *
 * Exit codes:
 *   0  the mirror is stable (no assets missing, or all missing ones fetched)
 *   1  some assets could not be fetched from the live origin
 *   2  invalid usage / could not start
 */

import { createServer } from 'node:http';
import { readFile, writeFile, mkdir, readdir, access } from 'node:fs/promises';
import { join, dirname, extname, relative, resolve, isAbsolute, sep } from 'node:path';
import { existsSync } from 'node:fs';

function arg(name, def = undefined) {
  const i = process.argv.indexOf(`--${name}`);
  return i > -1 && process.argv[i + 1] ? process.argv[i + 1] : def;
}

const domain = arg('domain');
const dir = arg('dir');
const maxRounds = parseInt(arg('max-rounds', '6'), 10);

if (!domain || !dir) {
  console.error('Usage: --domain <apexDomain> --dir <cloneSiteRoot> [--max-rounds N]');
  process.exit(2);
}
if (!existsSync(dir)) {
  console.error(`[runtime-sync] clone dir not found: ${dir}`);
  process.exit(2);
}

const ORIGIN = `https://${domain}`;

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

async function exists(p) {
  try {
    await access(p);
    return true;
  } catch {
    return false;
  }
}

/**
 * Resolve a path inside `root`, or null if it escapes.
 *
 * Used for both serving and writing. A `startsWith(root)` prefix test is not
 * containment: join() normalises `..`, so `/../site2/x` under root `/tmp/site`
 * yields `/tmp/site2/x`, which still shares the prefix.
 *
 * This matters more on the write side. Asset paths come from URLs observed at
 * runtime, i.e. from the cloned site's own JavaScript - content we do not
 * control. A page requesting `/%2e%2e/%2e%2e/x` decodes to `../../x` and would
 * otherwise write a remotely-fetched file outside the clone directory.
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

async function discoverPages(root) {
  const found = [];
  async function walk(d) {
    for (const e of await readdir(d, { withFileTypes: true })) {
      const p = join(d, e.name);
      if (e.isDirectory()) {
        if (e.name === '_next' || e.name === 'node_modules') continue;
        await walk(p);
      } else if (e.name === 'index.html') {
        const rel = relative(root, d).split(sep).filter(Boolean).join('/');
        found.push(rel ? `/${rel}/` : '/');
      }
    }
  }
  await walk(root);
  return found.sort((a, b) => a.split('/').length - b.split('/').length || a.localeCompare(b));
}

async function fetchWithRetry(url, attempts = 4) {
  let lastErr;
  for (let i = 0; i < attempts; i++) {
    try {
      const res = await fetch(url, { headers: { 'User-Agent': 'FFC static-clone bot' } });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const type = res.headers.get('content-type') || '';
      const isText = /javascript|css|json|xml|svg/.test(type);
      return {
        body: isText ? await res.text() : Buffer.from(await res.arrayBuffer()),
        isText,
      };
    } catch (err) {
      lastErr = err;
      if (i < attempts - 1) await new Promise((r) => setTimeout(r, 1500 * 2 ** i));
    }
  }
  throw lastErr;
}

/**
 * Strip the legacy scheme+host from absolute URLs in a fetched text asset,
 * leaving the path. Handles the JSON-escaped form (`https:\/\/host\/x`) too,
 * where the leading `\/` belongs to the path and must survive.
 */
function localizeLegacy(text) {
  return text.replace(
    new RegExp(`https?:(?:\\\\?/){2}(?:www\\.)?${domain.replace(/\./g, '\\.')}`, 'g'),
    '',
  );
}

async function collectMisses(origin, browser, pages) {
  const misses = new Set();
  for (const path of pages) {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const tab = await ctx.newPage();

    const note = (url) => {
      if (!url.startsWith(origin)) return;
      const u = new URL(url);
      misses.add(u.pathname + u.search);
    };
    tab.on('requestfailed', (r) => note(r.url()));
    tab.on('response', (r) => {
      if (r.status() === 404) note(r.url());
    });

    try {
      await tab.goto(origin + path, { waitUntil: 'load', timeout: 45000 });
      await tab.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
      await tab.waitForTimeout(2000);
      await tab.evaluate(() => window.scrollTo(0, 0));
      await tab.waitForTimeout(500);
    } catch {
      /* a page that fails to load simply yields no misses */
    }
    await ctx.close();
  }
  return misses;
}

async function main() {
  let chromium;
  try {
    ({ chromium } = await import('playwright'));
  } catch {
    console.error(
      '[runtime-sync] playwright is not installed.\n' +
        '        This repo is intentionally dependency-free; install it just for this run:\n' +
        '          npm i --no-save playwright && npx playwright install --with-deps chromium',
    );
    process.exit(2);
  }

  const server = await startServer(dir);
  const origin = `http://127.0.0.1:${server.address().port}`;
  const pages = await discoverPages(dir);
  console.error(`[runtime-sync] ${pages.length} page(s) in ${dir}`);

  const browser = await chromium.launch({
    executablePath: process.env.CHROMIUM_PATH || undefined,
  });

  const fetched = [];
  const unavailable = [];
  const seen = new Set();

  for (let round = 1; round <= maxRounds; round++) {
    const misses = await collectMisses(origin, browser, pages);
    const fresh = [...misses].filter((m) => !seen.has(m));
    if (!fresh.length) {
      console.error(`[runtime-sync] round ${round}: nothing new — mirror is stable.`);
      break;
    }
    console.error(`[runtime-sync] round ${round}: ${fresh.length} missing asset(s)`);

    for (const miss of fresh) {
      seen.add(miss);
      const pathOnly = miss.split('?')[0];
      const dest = resolveWithin(dir, decodeURIComponent(pathOnly));
      if (!dest) {
        // The clone's own JavaScript asked for a path outside the clone dir.
        // Never fetch-and-write that; record it and move on.
        unavailable.push(`${pathOnly} (refused: escapes clone directory)`);
        console.error(`   ! ${pathOnly} (refused: escapes clone directory)`);
        continue;
      }
      if (await exists(dest)) continue;
      try {
        const { body, isText } = await fetchWithRetry(ORIGIN + miss);
        const payload = isText ? localizeLegacy(body) : body;
        await mkdir(dirname(dest), { recursive: true });
        await writeFile(dest, Buffer.isBuffer(payload) ? payload : Buffer.from(payload, 'utf8'));
        fetched.push(pathOnly);
        console.error(`   + ${pathOnly}`);
      } catch (err) {
        unavailable.push(`${pathOnly} (${err.message})`);
        console.error(`   ! ${pathOnly} (${err.message})`);
      }
    }
  }

  await browser.close();
  server.close();

  const report = { domain, fetched: fetched.length, unavailable };
  await writeFile(join(dir, '..', 'runtime-assets-report.json'), JSON.stringify(report, null, 2));
  console.error('[runtime-sync] ' + JSON.stringify(report, null, 2));

  // A 404 on the live origin means the reference is already broken upstream —
  // worth reporting, but there is nothing to mirror and it is not this script's
  // failure. Anything else is.
  if (unavailable.some((u) => !u.includes('HTTP 404'))) process.exitCode = 1;
}

main().catch((err) => {
  console.error(err);
  process.exit(2);
});
