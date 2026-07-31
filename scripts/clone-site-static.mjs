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
 * A third blind spot sits alongside the two closed in #902: httrack does not
 * understand `srcset`. It treats the whole comma-separated attribute as ONE
 * opaque URL — percent-encoding the separators, fetching that pseudo-URL,
 * saving the 404 body under a filename built from the string, and rewriting the
 * attribute to point at that stub. Every responsive image then resolves to an
 * HTML error page wearing an image extension, and the fabricated files get
 * committed as if they were assets. `localizeLegacyUrls` cannot repair it: the
 * mangled reference is already relative and carries httrack's `https_/` form,
 * not `https://`. The mangling also truncates the attribute's tail, so the real
 * candidates are unrecoverable from the mirror — the pass below re-fetches each
 * page from the live site and rebuilds its srcset from the real markup.
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
import {
  readdirSync,
  statSync,
  writeFileSync,
  existsSync,
  readFileSync,
  mkdirSync,
  unlinkSync,
} from 'node:fs';
import { join, extname, relative, sep } from 'node:path';
import { createHash } from 'node:crypto';

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

/** Matches a `srcset`/`imagesrcset` attribute and captures its quote and value. */
export const SRCSET_ATTR_RE = /\b(srcset|imagesrcset)\s*=\s*(["'])([\s\S]*?)\2/gi;

/**
 * Parse a srcset attribute into candidates, per the WHATWG image-candidate
 * algorithm: a URL runs to the first ASCII whitespace, and trailing commas on
 * the URL token terminate the candidate. A URL may therefore contain commas —
 * this repo ships one (`Svgs/Goal-of-$1,000,000.svg`), which a naive split on
 * "," would corrupt.
 */
export function parseSrcset(input) {
  const out = [];
  const s = String(input);
  const isWs = (c) => c === ' ' || c === '\t' || c === '\n' || c === '\r' || c === '\f';
  let i = 0;
  while (i < s.length) {
    while (i < s.length && (isWs(s[i]) || s[i] === ',')) i++;
    if (i >= s.length) break;
    const start = i;
    while (i < s.length && !isWs(s[i])) i++;
    let url = s.slice(start, i);
    let sawTrailingComma = false;
    while (url.endsWith(',')) {
      url = url.slice(0, -1);
      sawTrailingComma = true;
    }
    let descriptor = '';
    if (!sawTrailingComma) {
      const dStart = i;
      while (i < s.length && s[i] !== ',') i++;
      descriptor = s.slice(dStart, i).trim();
      if (i < s.length) i++; // consume the separating comma
    }
    if (url) out.push({ url, descriptor });
  }
  return out;
}

/** Render candidates back into a srcset attribute value. */
export function serializeSrcset(candidates) {
  return candidates.map((c) => (c.descriptor ? `${c.url} ${c.descriptor}` : c.url)).join(', ');
}

/**
 * True when httrack collapsed a srcset into one percent-encoded pseudo-URL —
 * `%20<descriptor>%2c` is the fingerprint. Such an attribute cannot be repaired
 * from the mirror: the mangling truncates its tail.
 */
export function isCollapsedSrcset(value) {
  return /%20\d+(?:\.\d+)?[wx]%2c/i.test(value);
}

/**
 * True for FILESYSTEM paths httrack fabricated from an unparsed srcset. Note the
 * separators are literal here (`… 300w, https_/…`) even though the same string
 * appears percent-encoded inside the attribute — the two forms are why this and
 * isCollapsedSrcset are separate predicates. A descriptor embedded in a filename
 * is the tell; a real URL path never carries one.
 */
export function isSrcsetArtifactPath(p) {
  return /\s\d+(?:\.\d+)?[wx],/.test(p);
}

/** Local filename for a remote asset: stable, collision-free, extension-preserving. */
export function localAssetName(url) {
  const hash = createHash('sha1').update(url).digest('hex').slice(0, 16);
  let ext = '';
  try {
    ext = extname(new URL(url).pathname).toLowerCase();
  } catch {
    /* keep empty */
  }
  if (!/^\.[a-z0-9]{1,5}$/.test(ext)) ext = '';
  return `${hash}${ext}`;
}

/**
 * The live URL a mirrored page came from. httrack writes directory pages as
 * `<path>/index.html`, so those map back to the directory URL.
 */
export function originUrlForPage(pageRelPath, domain) {
  const rel = pageRelPath.split(sep).join('/').replace(/^\/+/, '');
  if (rel === 'index.html') return `https://${domain}/`;
  if (rel.endsWith('/index.html')) return `https://${domain}/${rel.slice(0, -'index.html'.length)}`;
  return `https://${domain}/${rel}`;
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

  // srcset repair. The mangled strings below are verbatim from the clone that
  // shipped in FFC-EX-canadatokeywestcoastalrun.org#68, where 167 attributes and
  // 37 fabricated files went out with a green build.
  const MANGLED_ATTR =
    '../../../../wp-content/uploads/2015/11/20151104_083235-300x169.jpg%20300w%2c%20' +
    'https_/x.org/wp-content/uploads/2015/11/20151104_083235-1024x576.jp/20151104_083235.e3.dela';
  const MANGLED_PATH =
    'public/wp-content/smush-webp/2015/07/20150713_114143-300x169.jpg.webp 300w, ' +
    'https_/x.org/wp-content/smush-webp/2015/07/20150713_114143-/20150713_114143.148.del';
  const checks = [
    [
      'srcset: three WordPress candidates parse',
      parseSrcset('a-300.jpg 300w, a-1024.jpg 1024w, a.jpg 2048w').length === 3,
    ],
    [
      'srcset: a comma inside a URL is not a separator',
      parseSrcset('/Svgs/Goal-of-$1,000,000.svg 300w, /Svgs/big.svg 600w').length === 2 &&
        parseSrcset('/Svgs/Goal-of-$1,000,000.svg 300w')[0].url === '/Svgs/Goal-of-$1,000,000.svg',
    ],
    [
      'srcset: round trip is lossless',
      serializeSrcset(parseSrcset('a-300.jpg 300w, a.jpg 2048w')) === 'a-300.jpg 300w, a.jpg 2048w',
    ],
    ['srcset: candidate without a descriptor', parseSrcset('a.jpg, b.jpg 2x').length === 2],
    ['collapsed attribute is detected (percent-encoded)', isCollapsedSrcset(MANGLED_ATTR)],
    [
      'collapsed detection ignores a healthy attribute',
      !isCollapsedSrcset('/a-300.jpg 300w, /a.jpg 2048w'),
    ],
    ['artifact path is detected (literal separators)', isSrcsetArtifactPath(MANGLED_PATH)],
    [
      'artifact detection spares a normal sized-image filename',
      !isSrcsetArtifactPath('public/uploads/a-300x169.jpg'),
    ],
    [
      'artifact detection spares a real filename containing commas',
      !isSrcsetArtifactPath('public/Svgs/Goal-of-$1,000,000.svg'),
    ],
    [
      'asset name is deterministic and keeps the extension',
      localAssetName('https://x.org/a.jpg.webp') === localAssetName('https://x.org/a.jpg.webp') &&
        localAssetName('https://x.org/a.jpg.webp').endsWith('.webp'),
    ],
    [
      'asset name survives a query string',
      localAssetName('https://x.org/a.png?ver=6.8').endsWith('.png'),
    ],
    [
      'directory pages map back to the directory URL',
      originUrlForPage('blog/post/index.html', 'x.org') === 'https://x.org/blog/post/',
    ],
    [
      'the site root maps to the apex',
      originUrlForPage('index.html', 'x.org') === 'https://x.org/',
    ],
  ];
  for (const [name, ok] of checks) {
    if (ok) console.log(`ok   ${name}`);
    else {
      failed++;
      console.error(`FAIL ${name}`);
    }
  }

  const total = cases.length + checks.length;
  console.log(failed ? `${failed} failure(s)` : `${total}/${total} passed`);
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
const artifactFiles = [];
function walk(dir) {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name);
    if (e.isDirectory()) {
      walk(p);
      continue;
    }
    if (isSrcsetArtifactPath(p)) {
      // An HTML error page wearing an image extension — never a real asset.
      artifactFiles.push(p);
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

// Repair srcset. httrack collapsed each attribute into one percent-encoded
// pseudo-URL pointing at a 404 stub, and the mangling truncated the tail, so the
// real candidates only exist upstream — re-fetch each page and rebuild from its
// live markup. Paths are emitted root-relative to match localizeLegacyUrls.
const REQUEST_TIMEOUT_MS = 30_000; // --timeout bounds httrack only
const assetsDirName = '_ffc-assets';
const assetsDir = join(siteRoot, assetsDirName);
const downloaded = new Map(); // url -> root-relative path, or null when the fetch failed
let srcsetAttrs = 0,
  srcsetRebuilt = 0,
  srcsetUnalignable = 0,
  pagesRefetched = 0;

async function fetchAsset(url) {
  if (downloaded.has(url)) return downloaded.get(url);
  let localPath = null;
  try {
    const r = await fetch(url, {
      headers: { 'User-Agent': UA },
      redirect: 'follow',
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
    if (r.ok) {
      const buf = Buffer.from(await r.arrayBuffer());
      if (buf.length > 0) {
        mkdirSync(assetsDir, { recursive: true });
        const name = localAssetName(url);
        writeFileSync(join(assetsDir, name), buf);
        localPath = `/${assetsDirName}/${name}`;
      }
    } else {
      console.error(`[clone] srcset asset HTTP ${r.status}: ${url}`);
    }
  } catch (e) {
    console.error(`[clone] srcset asset failed: ${url} (${e.message})`);
  }
  downloaded.set(url, localPath);
  return localPath;
}

async function fetchText(url) {
  try {
    const r = await fetch(url, {
      headers: { 'User-Agent': UA },
      redirect: 'follow',
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
    return r.ok ? await r.text() : null;
  } catch {
    return null;
  }
}

for (const page of htmlFiles) {
  const html = readFileSync(page, 'utf8');
  const attrs = [];
  SRCSET_ATTR_RE.lastIndex = 0;
  let m;
  while ((m = SRCSET_ATTR_RE.exec(html)))
    attrs.push({
      start: m.index,
      end: m.index + m[0].length,
      attr: m[1],
      quote: m[2],
      value: m[3],
    });
  if (!attrs.length) continue;
  srcsetAttrs += attrs.length;

  const live = await fetchText(originUrlForPage(relative(siteRoot, page), domain));
  pagesRefetched++;
  const liveValues = [];
  if (live) {
    SRCSET_ATTR_RE.lastIndex = 0;
    let lm;
    while ((lm = SRCSET_ATTR_RE.exec(live))) liveValues.push(lm[3]);
  }

  const edits = [];
  if (live && liveValues.length === attrs.length) {
    for (let i = 0; i < attrs.length; i++) {
      const cands = parseSrcset(liveValues[i]);
      for (const c of cands) {
        let abs;
        try {
          abs = new URL(c.url, `https://${domain}/`);
        } catch {
          continue;
        }
        const host = abs.host;
        if (
          host !== domain &&
          host !== `www.${domain}` &&
          !/(\.googleapis|\.gstatic)\.com$/i.test(host)
        )
          continue; // genuinely external, leave alone
        const localPath = await fetchAsset(abs.href);
        if (localPath) c.url = localPath;
      }
      const a = attrs[i];
      edits.push({
        start: a.start,
        end: a.end,
        text: `${a.attr}=${a.quote}${serializeSrcset(cands)}${a.quote}`,
      });
      srcsetRebuilt++;
    }
  } else {
    // Cannot align live markup with the mirror. A collapsed attribute left in
    // place renders a broken image, so drop it and let `src` serve.
    srcsetUnalignable++;
    for (const a of attrs) if (isCollapsedSrcset(a.value)) edits.push({ ...a, text: '' });
    console.error(
      `[clone] srcset: could not align ${page} (live=${liveValues.length}, mirror=${attrs.length})`,
    );
  }

  if (edits.length) {
    let next = html;
    for (const e of edits.sort((x, y) => y.start - x.start))
      next = next.slice(0, e.start) + e.text + next.slice(e.end);
    writeFileSync(page, next);
  }
}

// Drop httrack's fabricated srcset files; nothing references them now.
let artifactsRemoved = 0;
for (const f of artifactFiles) {
  try {
    unlinkSync(f);
    artifactsRemoved++;
  } catch {
    /* best effort */
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
  srcsetAttributes: srcsetAttrs,
  srcsetAttributesRebuilt: srcsetRebuilt,
  srcsetPagesRefetched: pagesRefetched,
  // Non-zero means the live page and the mirror disagreed on attribute count, so
  // those responsive images were dropped rather than repaired — re-clone.
  srcsetPagesUnalignable: srcsetUnalignable,
  srcsetArtifactsRemoved: artifactsRemoved,
  srcsetAssetsDownloaded: [...downloaded.values()].filter(Boolean).length,
  srcsetAssetsFailed: [...downloaded.values()].filter((v) => v === null).length,
  remainingExternalHosts: [...externalRefs].sort(),
  // remainingExternalHosts is a static scan of src/href only. It cannot see
  // URLs that JavaScript assembles at runtime (Elementor's content-hashed
  // webpack chunks), so it is NOT sufficient as a cutover gate on its own.
  // Run scripts/sync-runtime-assets.mjs then scripts/verify-no-legacy.mjs.
  gate: 'run sync-runtime-assets.mjs + verify-no-legacy.mjs before cutover',
};
writeFileSync(join(out, 'clone-report.json'), JSON.stringify(report, null, 2));
console.error('[clone] ' + JSON.stringify(report, null, 2));
