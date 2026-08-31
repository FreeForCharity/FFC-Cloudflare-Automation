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
 *
 * A same-origin asset the clone cannot serve is fatal ONLY when the source site
 * still serves it. One that is 404 on the source as well was dead before the
 * migration, is reproduced faithfully rather than introduced, and is reported
 * instead. Pass --no-source-probe to skip that check, which makes every missing
 * asset fatal again.
 */

import { createServer } from 'node:http';
import { readFile, mkdir, readdir } from 'node:fs/promises';
import { join, extname, relative, resolve, isAbsolute, sep } from 'node:path';
import { existsSync } from 'node:fs';

/**
 * Decide whether a same-origin request the mirror could not satisfy is a clone
 * defect or a reference that was already dead before the migration.
 *
 * The gate's rule — "a same-origin 404 means the mirror is incomplete" — is
 * right for the case it was written for (a webpack chunk that the source
 * serves and the clone missed) and wrong for a reference the SOURCE does not
 * serve either. A charity site accumulates those: on the first delivery of
 * viewpointministriesinternational.org every one of 120 pages failed on
 * `/dist/widgets.css?v=2110`, a URL fabricated at runtime by a leftover
 * WordPress.com script and 404 on the live site as well. No capture of any
 * kind can mirror a file the origin does not have, so failing on it would mean
 * FFC can never migrate a site carrying one stale reference — which is most of
 * them. This is the same rule already applied at the completeness gate, one
 * layer down.
 *
 * Fail-closed on purpose: only a probe that positively PROVES the source is
 * also missing the file may excuse it. A probe that errored, timed out or was
 * never run leaves the finding fatal, because "we could not check" must never
 * read as "it is fine".
 *
 * Returns { fatal, reason }.
 */
export function classifyMissing({ status = 0, contentType = '', error = null, url = '' } = {}) {
  if (error) return { fatal: true, reason: `could not check the source site (${error})` };
  if (status >= 400)
    return { fatal: false, reason: `dead on the source site too (HTTP ${status})` };
  if (status === 0) return { fatal: true, reason: 'the source site was never checked' };

  // A soft 404: WordPress answers an unknown path with a themed error PAGE at
  // HTTP 200. Trusting the status alone would call that "the source serves it"
  // and fail the migration over a file that does not exist there either.
  // Narrow on purpose — it only applies where an HTML body cannot be the real
  // asset, i.e. the request is for a stylesheet, script, font or image.
  const path = url.split('#')[0].split('?')[0];
  const ext = /\.([a-z0-9]{2,5})$/i.exec(path)?.[1] ?? '';
  const assetExt =
    /^(css|js|mjs|json|png|jpe?g|gif|svg|webp|avif|ico|woff2?|ttf|eot|mp4|webm|pdf)$/i;
  if (assetExt.test(ext) && /^text\/html\b/i.test(contentType)) {
    return {
      fatal: false,
      reason: `dead on the source site too (HTTP ${status} but served as ${contentType} — a soft 404)`,
    };
  }
  return { fatal: true, reason: `the source site serves this (HTTP ${status}); the clone lost it` };
}

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

    // --- classifyMissing ---------------------------------------------------
    // The case this exists for: /dist/widgets.css?v=2110 is fabricated at
    // runtime by a leftover WordPress.com script and 404s on the live site too.
    [
      'a 404 on the source is not the clone losing something',
      classifyMissing({ url: 'https://x.org/dist/widgets.css?v=1', status: 404 }).fatal,
      false,
    ],
    [
      'a 410 on the source is excused the same way',
      classifyMissing({ url: 'https://x.org/a.png', status: 410 }).fatal,
      false,
    ],
    // The property the gate is FOR: slopestohope.org shipped with a counter
    // frozen at 0 because a webpack chunk the source served never got mirrored.
    [
      'an asset the source still serves stays fatal',
      classifyMissing({
        url: 'https://x.org/counter.abc.bundle.min.js',
        status: 200,
        contentType: 'text/javascript',
      }).fatal,
      true,
    ],
    [
      'a redirect that resolves on the source stays fatal',
      classifyMissing({ url: 'https://x.org/a.css', status: 200, contentType: 'text/css' }).fatal,
      true,
    ],
    // Fail-closed. "We could not check" must never read as "it is fine".
    [
      'a probe that errored is fatal, not excused',
      classifyMissing({ url: 'https://x.org/a.css', error: 'timed out' }).fatal,
      true,
    ],
    [
      'a URL that was never probed is fatal',
      classifyMissing({ url: 'https://x.org/a.css' }).fatal,
      true,
    ],
    // WordPress answers an unknown path with a themed error page at HTTP 200.
    // Reading the status alone would call that "the source serves it".
    [
      'a soft 404 — HTML served for a stylesheet — is excused',
      classifyMissing({
        url: 'https://x.org/dist/widgets.css?v=2110',
        status: 200,
        contentType: 'text/html; charset=UTF-8',
      }).fatal,
      false,
    ],
    // …but only where an HTML body cannot be the real asset. An extensionless
    // endpoint may legitimately return HTML, so it is not excused.
    [
      'an extensionless 200 is NOT written off as a soft 404',
      classifyMissing({ url: 'https://x.org/cdn-cgi/rum', status: 200, contentType: 'text/html' })
        .fatal,
      true,
    ],
    [
      'a real stylesheet served as CSS is not a soft 404',
      classifyMissing({ url: 'https://x.org/a.css', status: 200, contentType: 'text/css' }).fatal,
      true,
    ],
    [
      'the reason names the status so a log reader can check the call',
      /HTTP 404/.test(classifyMissing({ url: 'https://x.org/a.css', status: 404 }).reason),
      true,
    ],
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
// Probing asks the SOURCE site whether it serves an asset the clone is missing,
// so it only makes sense when checking a local export. Opt-out, not opt-in: the
// default has to be the one that does not fail a migration over the charity's
// own pre-existing dead links.
const probeSource = Boolean(dir) && !process.argv.includes('--no-source-probe');
const UA = 'ffc-verify-no-legacy (+https://github.com/FreeForCharity)';

if (!domain || (!dir && !base)) {
  console.error(
    'Usage: --domain <apexDomain> (--dir <staticDir> | --base <url>) [--pages /a/,/b/] [--shots <dir>] [--max-pages N] [--no-source-probe]',
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
      if (url.startsWith(origin)) localMissing.push({ url, why: r.failure()?.errorText });
      else thirdPartyFailed.push(entry);
    });
    tab.on('response', (r) => {
      const url = r.url();
      if (url.startsWith(origin) && r.status() === 404) {
        localMissing.push({ url, why: 'HTTP 404' });
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
    // The missing-asset problem is added AFTER the source probe below: whether
    // a same-origin 404 is a clone defect depends on what the source serves.

    results.push({ path, problems, legacyHits, localMissing, thirdPartyFailed });
    await ctx.close();
  }

  await browser.close();
  if (server) server.close();

  // --- Is a missing asset the clone's fault, or already dead on the source? --
  //
  // Probed once per distinct URL, and only for URLs that actually went missing,
  // so a clean run makes no network calls at all.
  const verdictFor = new Map(); // url -> { fatal, reason }
  let distinctMissing = [...new Set(results.flatMap((r) => r.localMissing.map((m) => m.url)))];
  // A badly broken clone could name hundreds of distinct URLs, and probing them
  // all would mean pointing a burst of traffic at a charity's live site to
  // diagnose our own export. Cap it; anything past the cap stays FATAL, so the
  // cap can only ever make the gate stricter.
  const PROBE_CAP = 50;
  if (distinctMissing.length > PROBE_CAP) {
    console.log(
      `\n[verify] ${distinctMissing.length} distinct assets are missing — probing the first ${PROBE_CAP}; the rest stay fatal.`,
    );
    distinctMissing = distinctMissing.slice(0, PROBE_CAP);
  }
  if (distinctMissing.length && probeSource) {
    console.log(
      `\n[verify] ${distinctMissing.length} distinct asset(s) missing from the clone; asking ${domain} whether it serves them.`,
    );
    for (const url of distinctMissing) {
      const u = new URL(url);
      const target = `https://${domain}${u.pathname}${u.search}`;
      let probe;
      try {
        const res = await fetch(target, {
          redirect: 'follow',
          headers: { 'user-agent': UA },
          signal: AbortSignal.timeout(20000),
        });
        // Read and discard: leaving the body open holds the socket.
        await res.arrayBuffer().catch(() => {});
        probe = { url, status: res.status, contentType: res.headers.get('content-type') ?? '' };
      } catch (err) {
        probe = { url, error: err.name === 'TimeoutError' ? 'timed out' : err.message };
      }
      const v = classifyMissing(probe);
      verdictFor.set(url, v);
      console.log(`        ${v.fatal ? 'FATAL' : 'ok   '} ${u.pathname}${u.search} — ${v.reason}`);
    }
  } else if (distinctMissing.length) {
    for (const url of distinctMissing) {
      verdictFor.set(url, { fatal: true, reason: 'the source site was never checked' });
    }
  }

  for (const r of results) {
    r.fatalMissing = r.localMissing.filter((m) => (verdictFor.get(m.url) ?? { fatal: true }).fatal);
    r.excusedMissing = r.localMissing.filter(
      (m) => !(verdictFor.get(m.url) ?? { fatal: true }).fatal,
    );
    if (r.fatalMissing.length) r.problems.push(`${r.fatalMissing.length} missing local asset(s)`);
  }

  console.log(`\nVerified ${results.length} page(s) of ${domain} against ${origin}\n`);
  let failures = 0;
  for (const r of results) {
    const ok = r.problems.length === 0;
    if (!ok) failures++;
    console.log(`${ok ? 'PASS' : 'FAIL'}  ${r.path}`);
    for (const p of r.problems) console.log(`        ! ${p}`);
    for (const u of [...new Set(r.legacyHits)].slice(0, 8)) console.log(`        legacy: ${u}`);
    for (const u of [...new Set(r.fatalMissing.map((m) => `${m.url} (${m.why})`))].slice(0, 8))
      console.log(`        MISSING LOCAL: ${u}`);
    for (const u of [...new Set(r.excusedMissing.map((m) => m.url))].slice(0, 3)) {
      console.log(`        dead on the source too (not fatal): ${u}`);
    }
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
