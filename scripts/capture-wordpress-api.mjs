#!/usr/bin/env node
/**
 * capture-wordpress-api.mjs — capture a live WordPress site from its own REST
 * API plus rendered-HTML scraping.
 *
 * Why this exists alongside clone-site-static.mjs (httrack):
 *
 *   httrack discovers pages by FOLLOWING LINKS. Anything the theme does not
 *   link to — an orphaned landing page, a page reachable only from a menu the
 *   mobile breakpoint hides, a post past the blog pagination depth — is simply
 *   not in the mirror, and nothing in the output says so. The mirror's page
 *   count is an artifact of the theme's navigation, not of the site.
 *
 *   WordPress already knows the answer. /wp-json/wp/v2/pages returns every
 *   published page with an `X-WP-Total` header, so the inventory is the CMS's
 *   own count rather than a crawl's fixed point. That makes completeness
 *   CHECKABLE: captured N of X-WP-Total M is an assertion the run can fail on.
 *
 * ...and why the REST API is not sufficient on its own:
 *
 *   `content.rendered` is the POST BODY only. It carries no theme chrome — no
 *   header, nav, footer, or the CSS that makes any of it look like the site.
 *   Page builders (Elementor, Divi, WPBakery) are worse: they keep their real
 *   markup in postmeta and `content.rendered` is a placeholder or a shortcode
 *   husk. A REST-only capture of an Elementor site reproduces almost nothing.
 *
 * So: the REST API supplies the URL INVENTORY (authoritative, complete,
 * countable) and an HTTP fetch of each of those URLs supplies the RENDERED
 * MARKUP (faithful, theme-complete). Neither half is optional.
 *
 * A cPanel/FTP backup is deliberately not a source here. It hands you PHP,
 * a database dump and a plugin tree — the inputs to a rendering engine that
 * will not exist after the migration — rather than the rendered artifact that
 * is actually being migrated. Nothing downstream of this script can run PHP.
 *
 * Usage:
 *   node scripts/capture-wordpress-api.mjs --domain <domain> --inspect
 *   node scripts/capture-wordpress-api.mjs --domain <domain> --out <dir> \
 *        [--max 500] [--delay 250] [--include-posts] [--timeout 30]
 *   node scripts/capture-wordpress-api.mjs --self-test
 *
 * Exit codes:
 *   0  success (inspect: the API is usable; capture: every gate passed)
 *   1  the site or its API could not be used (inspect verdict NOT ok, or a
 *      capture gate failed — incomplete inventory, unlocalized assets)
 *   2  invalid usage / self-test failure / crash
 */

import { mkdirSync, writeFileSync, existsSync } from 'node:fs';
import { join, dirname, extname } from 'node:path';

const UA = 'Mozilla/5.0 (FFC static-capture bot; +https://freeforcharity.org)';

// ---------------------------------------------------------------------------
// Pure logic. Everything below the NETWORK banner does I/O; everything above
// it is exercised by --self-test, which is why the split is worth keeping.
// ---------------------------------------------------------------------------

/**
 * Reduce whatever the operator typed to a bare apex-ish host.
 * Accepts `https://www.x.org/path`, `www.x.org`, `x.org/`.
 */
export function normalizeDomain(input) {
  if (typeof input !== 'string') return '';
  let d = input.trim().toLowerCase();
  d = d.replace(/^[a-z][a-z0-9+.-]*:\/\//, ''); // scheme
  d = d.replace(/^www\./, '');
  d = d.split('/')[0].split('?')[0].split('#')[0];
  d = d.replace(/:\d+$/, ''); // port
  return d;
}

/**
 * REST root candidates, in preference order.
 *
 * The pretty form is the default, but a site with plain permalinks — or one
 * whose host rewrites /wp-json/ away — still answers on the query form. Trying
 * only the pretty one reports "no REST API" for a site that has a perfectly
 * good one, which is a false NOT_WORDPRESS on a migratable site.
 */
export function restRootCandidates(domain) {
  return [`https://${domain}/wp-json/`, `https://${domain}/?rest_route=/`];
}

/**
 * Turn a REST index response into a verdict.
 *
 * Deliberately three-valued. "not 200" collapses two states that need
 * different human responses: a 401/403 is WordPress with the API locked down
 * (a plugin, or a WAF) and is worth an operator conversation, whereas a 404 is
 * "this is not WordPress" and means picking a different capture path entirely.
 * Reporting both as "failed" sends the operator to the wrong remedy.
 */
export function classifyRestIndex(status, body) {
  if (status === 200) {
    const looksWp =
      body &&
      typeof body === 'object' &&
      (body.namespaces || body.routes || body.name !== undefined);
    return looksWp ? 'ok' : 'absent';
  }
  if (status === 401 || status === 403 || status === 405 || status === 429) return 'blocked';
  if (status === 0) return 'unreachable';
  return 'absent';
}

/**
 * Map a live page URL to the local path its captured HTML belongs at.
 * `https://x.org/about-us/` -> `about-us/index.html`; the home page -> `index.html`.
 */
export function localPathForLink(link, domain) {
  let path;
  try {
    path = new URL(link).pathname;
  } catch {
    return null;
  }
  path = path.replace(/^\/+/, '').replace(/\/+$/, '');
  if (path === '') return 'index.html';
  // A link that already names a file keeps its name; a directory-style link
  // becomes <dir>/index.html so a static host serves it at the same URL.
  if (/\.[a-z0-9]{2,5}$/i.test(path)) return path;
  return `${path}/index.html`;
}

/** Depth of a local path, for building a relative prefix back to the root. */
export function relativePrefix(localPath) {
  const depth = localPath.split('/').length - 1;
  return depth === 0 ? './' : '../'.repeat(depth);
}

/**
 * Every asset URL referenced by a chunk of HTML: src, href, srcset candidates,
 * inline style url(), and the content attribute of og:image-style meta tags.
 */
export function collectAssetUrls(html) {
  const urls = new Set();
  const push = (u) => {
    if (!u) return;
    const t = u.trim();
    if (
      !t ||
      t.startsWith('data:') ||
      t.startsWith('#') ||
      t.startsWith('mailto:') ||
      t.startsWith('tel:')
    )
      return;
    urls.add(t);
  };

  // src= / poster= — always an asset.
  for (const m of html.matchAll(/\b(?:src|poster|data-src)\s*=\s*["']([^"']+)["']/gi)) push(m[1]);

  // href= — only when the rel says it is an asset, never a navigation link.
  for (const m of html.matchAll(/<link\b[^>]*>/gi)) {
    const tag = m[0];
    const rel = /\brel\s*=\s*["']([^"']+)["']/i.exec(tag)?.[1]?.toLowerCase() ?? '';
    if (!/stylesheet|icon|preload|apple-touch-icon|manifest/.test(rel)) continue;
    const href = /\bhref\s*=\s*["']([^"']+)["']/i.exec(tag)?.[1];
    push(href);
  }

  // srcset / imagesrcset — comma-separated "<url> <descriptor>" candidates.
  // Splitting on a bare comma would cut data: URIs and any URL carrying one,
  // so the split has to require the comma be followed by a URL-ish token.
  for (const m of html.matchAll(/\b(?:srcset|imagesrcset|data-srcset)\s*=\s*["']([^"']+)["']/gi)) {
    for (const cand of m[1].split(/,(?=\s*[^\s,]+\s*(?:[\d.]+[wx])?\s*(?:,|$))/)) {
      push(cand.trim().split(/\s+/)[0]);
    }
  }

  // Inline style="background:url(...)" and <style> blocks.
  for (const m of html.matchAll(/url\(\s*["']?([^"')]+)["']?\s*\)/gi)) push(m[1]);

  return [...urls];
}

/** Asset URLs referenced from inside a stylesheet. */
export function collectCssUrls(css) {
  const urls = new Set();
  for (const m of css.matchAll(/url\(\s*["']?([^"')]+)["']?\s*\)/gi)) {
    const u = m[1].trim();
    if (u && !u.startsWith('data:') && !u.startsWith('#')) urls.add(u);
  }
  for (const m of css.matchAll(/@import\s+(?:url\()?\s*["']([^"']+)["']/gi)) urls.add(m[1].trim());
  return [...urls];
}

/**
 * Absolute-ise a possibly-relative reference against the page it appeared on.
 * Returns null for anything that is not http(s) once resolved.
 */
export function absolutize(ref, baseUrl) {
  try {
    const u = new URL(ref, baseUrl);
    if (u.protocol !== 'http:' && u.protocol !== 'https:') return null;
    return u.toString();
  } catch {
    return null;
  }
}

/**
 * Should this absolute asset URL be pulled local?
 *
 * Same-site assets always. Off-site assets only when they are the kind of thing
 * a page LOADS (fonts, images, CSS, JS on a CDN) — an <a> to another charity is
 * a link and stays external. Social/video embeds stay external by design: they
 * are third-party runtime services, not page assets, and localizing them breaks
 * them.
 */
export function shouldLocalize(absUrl, domain) {
  let u;
  try {
    u = new URL(absUrl);
  } catch {
    return false;
  }
  const host = u.hostname.replace(/^www\./, '');
  if (host === domain || host.endsWith(`.${domain}`)) return true;

  const KEEP_EXTERNAL = [
    'youtube.com',
    'youtu.be',
    'vimeo.com',
    'facebook.com',
    'instagram.com',
    'twitter.com',
    'x.com',
    'linkedin.com',
    'givebutter.com',
    'donorbox.org',
    'paypal.com',
    'google.com',
    'googletagmanager.com',
    'google-analytics.com',
  ];
  if (KEEP_EXTERNAL.some((k) => host === k || host.endsWith(`.${k}`))) return false;

  // Known asset providers, plus anything with an asset-ish extension.
  const ASSET_HOSTS = [
    'fonts.googleapis.com',
    'fonts.gstatic.com',
    'gstatic.com',
    'gravatar.com',
    'wp.com',
    'cloudfront.net',
    'amazonaws.com',
    'cdnjs.cloudflare.com',
    'jsdelivr.net',
    'unpkg.com',
    'bootstrapcdn.com',
  ];
  if (ASSET_HOSTS.some((h) => host === h || host.endsWith(`.${h}`))) return true;

  return /\.(css|js|png|jpe?g|gif|webp|avif|svg|ico|woff2?|ttf|eot|otf|mp4|webm|mp3|pdf)(\?|$)/i.test(
    u.pathname + u.search,
  );
}

/**
 * Local filename for an asset URL, namespaced by host so two providers cannot
 * collide on `/style.css`, and carrying the query string so `?ver=6.4` variants
 * stay distinct (WordPress cache-busts nearly every enqueued asset this way —
 * dropping the query merges genuinely different files).
 */
export function assetLocalName(absUrl) {
  const u = new URL(absUrl);
  const host = u.hostname.replace(/^www\./, '');
  let p = u.pathname.replace(/^\/+/, '');
  if (p === '' || p.endsWith('/')) p += 'index';
  let ext = extname(p);
  if (u.search) {
    const q = u.search.replace(/[^a-z0-9]+/gi, '-').replace(/^-|-$/g, '');
    // Keep the extension last so content-type sniffing and static hosts behave.
    p = ext ? `${p.slice(0, -ext.length)}__${q}${ext}` : `${p}__${q}`;
  }
  ext = extname(p);
  if (!ext) p += '.bin';
  return `${host}/${p}`.replace(/\/{2,}/g, '/');
}

/**
 * Rewrite every reference in `html` that we localized.
 *
 * Longest-first is load-bearing: a plain global replace of the short URL would
 * corrupt the long one when one asset URL is a prefix of another (the common
 * `.../logo.png` vs `.../logo.png?ver=2` pair), leaving a mangled reference
 * that 404s and looks like a download failure rather than a rewrite bug.
 */
export function rewriteRefs(text, replacements) {
  const pairs = [...replacements.entries()].sort((a, b) => b[0].length - a[0].length);
  let out = text;
  for (const [from, to] of pairs) {
    if (!to) continue;
    out = out.split(from).join(to);
    // Page builders store URLs inside HTML-entity-escaped JSON, where the
    // delimiter is &quot; rather than a quote. Those copies are real references
    // and survive a markup-only rewrite pointing at the decommissioned host.
    const escaped = from.replace(/\//g, '\\/');
    if (escaped !== from) out = out.split(escaped).join(to.replace(/\//g, '\\/'));
  }
  return out;
}

/**
 * External http(s) references still present after localization, excluding the
 * ones we deliberately keep. This is the "zero external asset hosts" gate.
 */
export function remainingExternalAssetHosts(html, domain) {
  const hosts = new Set();
  for (const ref of collectAssetUrls(html)) {
    if (!/^https?:\/\//i.test(ref)) continue;
    if (shouldLocalize(ref, domain)) {
      try {
        hosts.add(new URL(ref).hostname);
      } catch {
        /* malformed reference — not a host we can report */
      }
    }
  }
  return [...hosts];
}

/**
 * Capture verdict. `ok` only when the inventory is complete AND nothing that
 * should have been localized is still pointing off-site.
 */
export function captureVerdict({ expected, captured, externalHosts, failedAssets }) {
  const problems = [];
  if (expected > 0 && captured < expected)
    problems.push(`captured ${captured} of ${expected} pages the REST API reported`);
  if (externalHosts.length) problems.push(`unlocalized asset hosts: ${externalHosts.join(', ')}`);
  if (failedAssets > 0) problems.push(`${failedAssets} asset download(s) failed`);
  return { ok: problems.length === 0, problems };
}

// ---------------------------------------------------------------------------
// Self-test — offline, no network. Must precede the usage guard so that
// `--self-test` runs without --domain.
// ---------------------------------------------------------------------------

function selfTest() {
  let failures = 0;
  const eq = (label, actual, expected) => {
    const a = JSON.stringify(actual);
    const e = JSON.stringify(expected);
    if (a !== e) {
      console.error(`FAIL ${label}\n  expected ${e}\n  actual   ${a}`);
      failures++;
    } else {
      console.log(`ok   ${label}`);
    }
  };

  eq(
    'normalizeDomain strips scheme/www/path',
    normalizeDomain('https://www.VpMin.org/about/'),
    'vpmin.org',
  );
  eq('normalizeDomain strips port', normalizeDomain('example.org:8080'), 'example.org');
  eq('normalizeDomain rejects non-string', normalizeDomain(null), '');

  eq('restRootCandidates offers both forms', restRootCandidates('x.org').length, 2);

  eq('classify 200 + namespaces = ok', classifyRestIndex(200, { namespaces: ['wp/v2'] }), 'ok');
  eq('classify 200 + junk = absent', classifyRestIndex(200, 'not json'), 'absent');
  eq('classify 401 = blocked', classifyRestIndex(401, null), 'blocked');
  eq('classify 403 = blocked', classifyRestIndex(403, null), 'blocked');
  eq('classify 404 = absent', classifyRestIndex(404, null), 'absent');
  eq('classify 0 = unreachable', classifyRestIndex(0, null), 'unreachable');

  eq('localPath home', localPathForLink('https://x.org/', 'x.org'), 'index.html');
  eq('localPath nested', localPathForLink('https://x.org/a/b/', 'x.org'), 'a/b/index.html');
  eq('localPath file keeps name', localPathForLink('https://x.org/feed.xml', 'x.org'), 'feed.xml');
  eq('localPath rejects garbage', localPathForLink('not a url', 'x.org'), null);

  eq('relativePrefix root', relativePrefix('index.html'), './');
  eq('relativePrefix nested', relativePrefix('a/b/index.html'), '../../');

  // Asset collection
  const html = `
    <link rel="stylesheet" href="/style.css?ver=1">
    <link rel="canonical" href="https://x.org/about/">
    <img src="/a.png" srcset="/a-300.png 300w, /a-600.png 600w">
    <div style="background:url('/bg.jpg')"></div>
    <a href="/contact/">Contact</a>`;
  const found = collectAssetUrls(html).sort();
  eq('collectAssetUrls finds assets, not nav links', found, [
    '/a-300.png',
    '/a-600.png',
    '/a.png',
    '/bg.jpg',
    '/style.css?ver=1',
  ]);
  eq(
    'collectAssetUrls skips rel=canonical',
    collectAssetUrls('<link rel="canonical" href="https://x.org/">'),
    [],
  );
  eq(
    'collectAssetUrls skips data: URIs',
    collectAssetUrls('<img src="data:image/png;base64,AAA">'),
    [],
  );

  eq(
    'collectCssUrls finds url() and @import',
    collectCssUrls("@import url('a.css'); body{background:url(b.png)}").sort(),
    ['a.css', 'b.png'],
  );

  eq('absolutize relative', absolutize('/a.png', 'https://x.org/about/'), 'https://x.org/a.png');
  eq(
    'absolutize protocol-relative',
    absolutize('//cdn.io/a.png', 'https://x.org/'),
    'https://cdn.io/a.png',
  );
  eq('absolutize rejects mailto', absolutize('mailto:a@b.c', 'https://x.org/'), null);

  eq('shouldLocalize same host', shouldLocalize('https://x.org/a.png', 'x.org'), true);
  eq('shouldLocalize www of same host', shouldLocalize('https://www.x.org/a.png', 'x.org'), true);
  eq(
    'shouldLocalize google fonts',
    shouldLocalize('https://fonts.gstatic.com/f.woff2', 'x.org'),
    true,
  );
  eq(
    'shouldLocalize keeps youtube external',
    shouldLocalize('https://youtube.com/embed/1', 'x.org'),
    false,
  );
  eq(
    'shouldLocalize keeps donorbox external',
    shouldLocalize('https://donorbox.org/w', 'x.org'),
    false,
  );
  eq(
    'shouldLocalize foreign html page',
    shouldLocalize('https://other.org/about/', 'x.org'),
    false,
  );
  eq(
    'shouldLocalize foreign asset by extension',
    shouldLocalize('https://other.org/a.png', 'x.org'),
    true,
  );

  eq('assetLocalName namespaces by host', assetLocalName('https://x.org/a/b.png'), 'x.org/a/b.png');
  eq(
    'assetLocalName keeps query distinct, extension last',
    assetLocalName('https://x.org/s.css?ver=6.4'),
    'x.org/s-ver-6-4.css'.replace('s-ver', 's__ver'),
  );
  eq(
    'assetLocalName gives extensionless a suffix',
    assetLocalName('https://x.org/thing'),
    'x.org/thing.bin',
  );

  // Longest-first rewriting: the short URL is a prefix of the long one.
  const reps = new Map([
    ['https://x.org/logo.png', 'assets/logo.png'],
    ['https://x.org/logo.png?ver=2', 'assets/logo__ver-2.png'],
  ]);
  eq(
    'rewriteRefs does not corrupt prefix-sharing URLs',
    rewriteRefs('<img src="https://x.org/logo.png?ver=2">', reps),
    '<img src="assets/logo__ver-2.png">',
  );
  eq(
    'rewriteRefs also rewrites escaped-JSON copies',
    rewriteRefs('{"u":"https:\\/\\/x.org\\/logo.png"}', reps),
    '{"u":"assets\\/logo.png"}',
  );

  eq(
    'remainingExternalAssetHosts reports an unlocalized asset host',
    remainingExternalAssetHosts('<img src="https://cdn.example.net/a.png">', 'x.org'),
    ['cdn.example.net'],
  );
  eq(
    'remainingExternalAssetHosts ignores kept-external embeds',
    remainingExternalAssetHosts('<iframe src="https://youtube.com/embed/1"></iframe>', 'x.org'),
    [],
  );

  eq(
    'captureVerdict clean',
    captureVerdict({ expected: 5, captured: 5, externalHosts: [], failedAssets: 0 }).ok,
    true,
  );
  eq(
    'captureVerdict flags short inventory',
    captureVerdict({ expected: 5, captured: 4, externalHosts: [], failedAssets: 0 }).problems
      .length,
    1,
  );
  eq(
    'captureVerdict flags external hosts and failed assets',
    captureVerdict({ expected: 5, captured: 5, externalHosts: ['cdn.io'], failedAssets: 2 })
      .problems.length,
    2,
  );

  console.log(failures ? `\n${failures} self-test failure(s)` : '\nall self-tests passed');
  process.exit(failures ? 2 : 0);
}

if (process.argv.includes('--self-test')) selfTest();

// ---------------------------------------------------------------------------
// NETWORK
// ---------------------------------------------------------------------------

function arg(name, def = undefined) {
  const i = process.argv.indexOf(`--${name}`);
  return i > -1 && process.argv[i + 1] && !process.argv[i + 1].startsWith('--')
    ? process.argv[i + 1]
    : def;
}
const flag = (name) => process.argv.includes(`--${name}`);

const domain = normalizeDomain(arg('domain', ''));
const inspectOnly = flag('inspect');
const outDir = arg('out', '');
const maxItems = parseInt(arg('max', '500'), 10);
const delayMs = parseInt(arg('delay', '250'), 10);
const timeoutMs = parseInt(arg('timeout', '30'), 10) * 1000;
const includePosts = flag('include-posts');
const jsonOut = arg('json-out', '');

if (!domain || (!inspectOnly && !outDir)) {
  console.error(
    'Usage:\n' +
      '  --domain <domain> --inspect [--json-out <file>]\n' +
      '  --domain <domain> --out <dir> [--max 500] [--delay 250] [--include-posts] [--timeout 30]\n' +
      '  --self-test',
  );
  process.exit(2);
}

const origin = `https://${domain}`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/**
 * One HTTP request with a timeout and a bounded retry.
 *
 * Never throws for an HTTP status — a 404 is data, and the callers all branch
 * on it. It throws only when the request could not be made at all, and returns
 * `{ status: 0 }` when even the retry could not complete, so "unreachable" is
 * a value the verdict logic can see rather than a crash.
 */
async function request(url, { method = 'GET', accept = '*/*', retries = 2 } = {}) {
  for (let attempt = 0; attempt <= retries; attempt++) {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), timeoutMs);
    try {
      const res = await fetch(url, {
        method,
        redirect: 'follow',
        signal: ctl.signal,
        headers: { 'User-Agent': UA, Accept: accept },
      });
      clearTimeout(timer);
      return res;
    } catch (err) {
      clearTimeout(timer);
      if (attempt === retries) {
        console.error(
          `[net] ${method} ${url} failed after ${retries + 1} attempt(s): ${err.message}`,
        );
        return { status: 0, ok: false, headers: new Map(), url, _failed: true };
      }
      await sleep(500 * (attempt + 1));
    }
  }
}

/** Fetch and JSON-parse; returns { status, body } with body null on parse failure. */
async function getJson(url) {
  const res = await request(url, { accept: 'application/json' });
  if (!res || res.status === 0) return { status: 0, body: null, headers: null };
  let body = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }
  return { status: res.status, body, headers: res.headers };
}

/** Resolve which REST root this site actually answers on. */
async function resolveRestRoot() {
  for (const candidate of restRootCandidates(domain)) {
    const { status, body } = await getJson(candidate);
    const verdict = classifyRestIndex(status, body);
    if (verdict === 'ok') return { root: candidate, verdict, index: body, status };
    // A blocked API is a definite answer about this site; stop and report it
    // rather than trying the other form and reporting the vaguer 404.
    if (verdict === 'blocked') return { root: candidate, verdict, index: null, status };
  }
  return { root: null, verdict: 'absent', index: null, status: 404 };
}

/** Build a collection URL that works for both the pretty and query REST forms. */
function collectionUrl(root, collection, params) {
  const qs = new URLSearchParams(params).toString();
  if (root.includes('?rest_route=')) return `${origin}/?rest_route=/wp/v2/${collection}&${qs}`;
  return `${root}wp/v2/${collection}?${qs}`;
}

/**
 * Page through a REST collection.
 *
 * Returns the items AND the server's own `X-WP-Total`, because the completeness
 * gate compares the two. A collection that returns fewer items than the header
 * promised is the failure this whole approach exists to be able to notice.
 */
async function fetchCollection(root, collection, extraParams = {}) {
  const perPage = 100;
  const items = [];
  let total = null;
  let totalPages = 1;

  for (let page = 1; page <= totalPages && items.length < maxItems; page++) {
    const url = collectionUrl(root, collection, {
      per_page: String(perPage),
      page: String(page),
      ...extraParams,
    });
    const { status, body, headers } = await getJson(url);
    if (status !== 200 || !Array.isArray(body)) {
      console.error(`[rest] ${collection} page ${page}: HTTP ${status} — stopping this collection`);
      break;
    }
    if (page === 1 && headers) {
      const t = headers.get?.('x-wp-total');
      const tp = headers.get?.('x-wp-totalpages');
      if (t !== null && t !== undefined && t !== '') total = parseInt(t, 10);
      if (tp) totalPages = parseInt(tp, 10);
    }
    items.push(...body);
    await sleep(delayMs);
  }
  return { items, total: total ?? items.length, totalPages };
}

// --- inspect ---------------------------------------------------------------

async function inspect() {
  const report = { domain, checkedAt: new Date().toISOString() };

  const home = await request(`${origin}/`, { accept: 'text/html' });
  report.home = { status: home.status };
  let homeHtml = '';
  if (home.status && home.status !== 0) {
    report.home.server = home.headers.get?.('server') ?? null;
    report.home.poweredBy = home.headers.get?.('x-powered-by') ?? null;
    report.home.finalUrl = home.url;
    try {
      homeHtml = await home.text();
    } catch {
      homeHtml = '';
    }
    const gen = /<meta[^>]+name=["']generator["'][^>]+content=["']([^"']+)["']/i.exec(
      homeHtml,
    )?.[1];
    report.home.generator = gen ?? null;
    report.home.bytes = homeHtml.length;
  }

  // Which builder/theme, so the operator knows what content.rendered will miss.
  report.builders = [
    'elementor',
    'divi',
    'wpbakery',
    'js_composer',
    'beaver',
    'bricks',
    'gutenberg',
    'avada',
  ].filter((b) => new RegExp(b, 'i').test(homeHtml));
  report.theme = /wp-content\/themes\/([a-z0-9_-]+)/i.exec(homeHtml)?.[1] ?? null;
  report.usesWpContent = /wp-content\//i.test(homeHtml);

  const rest = await resolveRestRoot();
  report.rest = { root: rest.root, verdict: rest.verdict, status: rest.status };
  if (rest.index) {
    report.rest.name = rest.index.name ?? null;
    report.rest.description = rest.index.description ?? null;
    report.rest.homeUrl = rest.index.home ?? rest.index.url ?? null;
    report.rest.namespaces = rest.index.namespaces ?? null;
  }

  if (rest.verdict === 'ok') {
    report.collections = {};
    for (const c of ['pages', 'posts', 'media', 'categories', 'tags']) {
      const url = collectionUrl(rest.root, c, {
        per_page: '1',
        status: c === 'media' ? '' : 'publish',
      });
      const { status, headers } = await getJson(
        url.replace('&status=', '').replace('?status=&', '?'),
      );
      report.collections[c] =
        status === 200
          ? { total: parseInt(headers?.get?.('x-wp-total') ?? '0', 10), status }
          : { status };
      await sleep(delayMs);
    }
  }

  for (const [key, path] of [
    ['sitemap', '/wp-sitemap.xml'],
    ['sitemapLegacy', '/sitemap.xml'],
    ['robots', '/robots.txt'],
  ]) {
    const r = await request(`${origin}${path}`, { method: 'GET', accept: '*/*', retries: 0 });
    report[key] = { status: r.status };
  }

  const pagesTotal = report.collections?.pages?.total ?? 0;
  const usable = rest.verdict === 'ok' && pagesTotal > 0;
  report.verdict = usable
    ? 'CAPTURE_READY'
    : rest.verdict === 'blocked'
      ? 'API_BLOCKED'
      : rest.verdict === 'unreachable'
        ? 'UNREACHABLE'
        : 'NO_REST_API';

  const text = JSON.stringify(report, null, 2);
  console.log(text);
  if (jsonOut) {
    mkdirSync(dirname(jsonOut), { recursive: true });
    writeFileSync(jsonOut, text, { encoding: 'utf8' });
  }
  if (process.env.GITHUB_STEP_SUMMARY) {
    const s = [
      `## WordPress capture inspect — ${domain}`,
      '',
      `**Verdict: ${report.verdict}**`,
      '',
      `- Home: HTTP ${report.home.status}${report.home.server ? ` (server: ${report.home.server})` : ''}`,
      `- Generator: ${report.home.generator ?? '—'}`,
      `- Theme: ${report.theme ?? '—'}${report.builders.length ? ` · builders: ${report.builders.join(', ')}` : ''}`,
      `- REST root: ${report.rest.root ?? '—'} (${report.rest.verdict}, HTTP ${report.rest.status})`,
      `- Site name: ${report.rest.name ?? '—'}`,
      '',
      '| Collection | Total |',
      '| ---------- | ----- |',
      ...Object.entries(report.collections ?? {}).map(
        ([k, v]) => `| ${k} | ${v.total ?? `HTTP ${v.status}`} |`,
      ),
      '',
      `- Sitemap: HTTP ${report.sitemap.status} (legacy ${report.sitemapLegacy.status}) · robots.txt: HTTP ${report.robots.status}`,
    ].join('\n');
    writeFileSync(process.env.GITHUB_STEP_SUMMARY, s + '\n', { flag: 'a', encoding: 'utf8' });
  }
  process.exit(report.verdict === 'CAPTURE_READY' ? 0 : 1);
}

// --- capture ---------------------------------------------------------------

async function capture() {
  const rest = await resolveRestRoot();
  if (rest.verdict !== 'ok') {
    console.error(`[capture] REST API is not usable (${rest.verdict}). Run --inspect for detail.`);
    process.exit(1);
  }
  console.error(`[capture] REST root: ${rest.root}`);

  // 1. INVENTORY from the CMS.
  const pages = await fetchCollection(rest.root, 'pages', { status: 'publish' });
  console.error(`[capture] pages: ${pages.items.length} of ${pages.total} reported`);
  let posts = { items: [], total: 0 };
  if (includePosts) {
    posts = await fetchCollection(rest.root, 'posts', { status: 'publish' });
    console.error(`[capture] posts: ${posts.items.length} of ${posts.total} reported`);
  }
  const media = await fetchCollection(rest.root, 'media');
  console.error(`[capture] media: ${media.items.length} of ${media.total} reported`);

  const entries = [...pages.items, ...posts.items]
    .filter((it) => it && it.link)
    .map((it) => ({
      id: it.id,
      type: it.type,
      slug: it.slug,
      link: it.link,
      title: it.title?.rendered ?? '',
      date: it.date,
      modified: it.modified,
      parent: it.parent ?? 0,
      menuOrder: it.menu_order ?? 0,
      template: it.template ?? '',
      localPath: localPathForLink(it.link, domain),
    }))
    .filter((e) => e.localPath);

  // The home page is often a page whose `link` is the site root, but on a
  // "latest posts" front page it is not in the pages collection at all — and
  // an index.html is not optional for a static host.
  if (!entries.some((e) => e.localPath === 'index.html')) {
    entries.unshift({
      id: 0,
      type: 'front',
      slug: '',
      link: `${origin}/`,
      title: 'Home',
      localPath: 'index.html',
      parent: 0,
      menuOrder: 0,
      template: '',
    });
  }

  mkdirSync(outDir, { recursive: true });
  const assetsDirName = '_ffc-assets';

  // 2. SCRAPE the rendered markup for each inventoried URL.
  const rendered = new Map(); // localPath -> html
  let fetchFailures = 0;
  for (const e of entries) {
    const res = await request(e.link, { accept: 'text/html' });
    if (!res || res.status !== 200) {
      console.error(`[scrape] ${e.link}: HTTP ${res?.status ?? 0} — skipped`);
      fetchFailures++;
      await sleep(delayMs);
      continue;
    }
    let html;
    try {
      html = await res.text();
    } catch {
      fetchFailures++;
      await sleep(delayMs);
      continue;
    }
    rendered.set(e.localPath, html);
    e.bytes = html.length;
    await sleep(delayMs);
  }
  console.error(`[capture] rendered ${rendered.size} page(s), ${fetchFailures} failure(s)`);

  // 3. Localize assets, following CSS one level deep so @font-face and
  //    background images inside a stylesheet come along too. Without that pass
  //    the page's CSS downloads fine and every font it names still 404s.
  const assetsRoot = join(outDir, assetsDirName);
  const downloaded = new Map(); // absolute URL -> local name (or null on failure)
  let failedAssets = 0;

  async function localizeAsset(absUrl) {
    if (downloaded.has(absUrl)) return downloaded.get(absUrl);
    downloaded.set(absUrl, null); // claim it first so a cycle cannot recurse forever
    const res = await request(absUrl);
    if (!res || res.status !== 200) {
      console.error(`[asset] ${absUrl}: HTTP ${res?.status ?? 0}`);
      failedAssets++;
      return null;
    }
    let buf;
    try {
      buf = Buffer.from(await res.arrayBuffer());
    } catch {
      failedAssets++;
      return null;
    }
    const name = assetLocalName(absUrl);
    const dest = join(assetsRoot, name);
    mkdirSync(dirname(dest), { recursive: true });

    const isCss =
      /\.css($|\?)/i.test(absUrl) || /text\/css/i.test(res.headers.get?.('content-type') ?? '');
    if (isCss) {
      let css = buf.toString('utf8');
      const cssReps = new Map();
      for (const ref of collectCssUrls(css)) {
        const abs = absolutize(ref, res.url || absUrl);
        if (!abs || !shouldLocalize(abs, domain)) continue;
        const inner = await localizeAsset(abs);
        if (inner) {
          // Both are under assetsRoot, so the reference from one stylesheet to
          // another asset is relative to the stylesheet's own directory.
          const fromDir = dirname(name);
          const rel =
            fromDir === '.' ? inner : `${'../'.repeat(fromDir.split('/').length)}${inner}`;
          cssReps.set(ref, rel);
        }
      }
      css = rewriteRefs(css, cssReps);
      writeFileSync(dest, css, { encoding: 'utf8' });
    } else {
      writeFileSync(dest, buf);
    }
    downloaded.set(absUrl, name);
    await sleep(Math.min(delayMs, 100));
    return name;
  }

  // Media-library files first: they are the charity's own images, and pulling
  // them from the REST inventory catches any that no captured page happens to
  // reference (a gallery behind a builder widget, for instance).
  for (const m of media.items) {
    if (!m?.source_url) continue;
    if (shouldLocalize(m.source_url, domain)) await localizeAsset(m.source_url);
  }

  for (const [localPath, html] of rendered) {
    const pageUrl = entries.find((e) => e.localPath === localPath)?.link ?? origin;
    const reps = new Map();
    for (const ref of collectAssetUrls(html)) {
      const abs = absolutize(ref, pageUrl);
      if (!abs || !shouldLocalize(abs, domain)) continue;
      const name = await localizeAsset(abs);
      if (name) reps.set(ref, `${relativePrefix(localPath)}${assetsDirName}/${name}`);
    }
    // Same-site page links must point at the captured copies, or every nav
    // click leaves the static site for the host being decommissioned — which
    // still resolves today and silently stops after the DNS cutover.
    for (const e of entries) {
      if (e.localPath === localPath) continue;
      const target = `${relativePrefix(localPath)}${e.localPath.replace(/index\.html$/, '')}`;
      reps.set(e.link, target || './');
      if (e.link.endsWith('/')) reps.set(e.link.slice(0, -1), target || './');
    }
    const out = rewriteRefs(html, reps);
    const dest = join(outDir, localPath);
    mkdirSync(dirname(dest), { recursive: true });
    writeFileSync(dest, out, { encoding: 'utf8' });
  }

  // 4. Gates.
  let externalHosts = new Set();
  for (const localPath of rendered.keys()) {
    const dest = join(outDir, localPath);
    if (!existsSync(dest)) continue;
    const html = (await import('node:fs')).readFileSync(dest, 'utf8');
    for (const h of remainingExternalAssetHosts(html, domain)) externalHosts.add(h);
  }

  const verdict = captureVerdict({
    expected: pages.total,
    captured: rendered.size,
    externalHosts: [...externalHosts],
    failedAssets,
  });

  const report = {
    domain,
    capturedAt: new Date().toISOString(),
    restRoot: rest.root,
    pages: { captured: rendered.size, reported: pages.total },
    posts: { captured: posts.items.length, reported: posts.total },
    media: { downloaded: [...downloaded.values()].filter(Boolean).length, reported: media.total },
    assets: { downloaded: [...downloaded.values()].filter(Boolean).length, failed: failedAssets },
    remainingExternalHosts: [...externalHosts],
    entries: entries.map(
      ({ id, type, slug, link, title, localPath, parent, menuOrder, bytes }) => ({
        id,
        type,
        slug,
        link,
        title,
        localPath,
        parent,
        menuOrder,
        bytes: bytes ?? 0,
      }),
    ),
    verdict,
  };
  writeFileSync(join(outDir, 'wp-capture-report.json'), JSON.stringify(report, null, 2), {
    encoding: 'utf8',
  });
  console.error(
    JSON.stringify({ ...report, entries: `${report.entries.length} entries` }, null, 2),
  );

  if (process.env.GITHUB_STEP_SUMMARY) {
    const s = [
      `## WordPress capture — ${domain}`,
      '',
      `**${verdict.ok ? '✅ capture gates passed' : '⚠️ capture gates failed'}**`,
      ...(verdict.ok ? [] : ['', ...verdict.problems.map((p) => `- ${p}`)]),
      '',
      `- Pages: ${rendered.size} captured / ${pages.total} reported by the REST API`,
      `- Posts: ${posts.items.length} captured / ${posts.total} reported`,
      `- Assets localized: ${report.assets.downloaded} (${failedAssets} failed)`,
      `- Remaining external asset hosts: ${externalHosts.size ? [...externalHosts].join(', ') : 'none'}`,
    ].join('\n');
    writeFileSync(process.env.GITHUB_STEP_SUMMARY, s + '\n', { flag: 'a', encoding: 'utf8' });
  }

  process.exit(verdict.ok ? 0 : 1);
}

await (inspectOnly ? inspect() : capture());
