#!/usr/bin/env node
/**
 * fetch-asset-subresources.mjs — fetch the files a LOCALIZED HTML ASSET asks
 * for relatively, which the capture never followed.
 *
 * `capture-wordpress-api.mjs` downloads a third-party HTML asset (an embed's
 * player page, an iframe's document) and rewrites the PAGE's reference to it,
 * but it does not read that file and go after what IT references. A relative
 * `src` inside it therefore resolves, at serve time, against the directory the
 * asset was written into — and nothing ever put a file there.
 *
 * Measured on newheightseducation.org, run 36045432773. The capture holds
 *
 *   _ffc-assets/s3.amazonaws.com/embed.animoto.com/play__w-swf-…-ea706737d16a0972.html
 *
 * 1,438 bytes, and inside it `<script src="js/main-48d3ed6a76.js">`. That
 * resolves to `/_ffc-assets/s3.amazonaws.com/embed.animoto.com/js/main-48d3ed6a76.js`,
 * which has never existed in this repo. `/volunteer-with-nheg/` was the one
 * page of 120 that failed the self-containment gate, twice, for that file.
 *
 * WHY IT SURFACED ONLY NOW. The CSP had no `frame-src 'self'`, so the browser
 * refused the iframe before it could load the player page, and the player page
 * never asked for its script. Allowing the charity's own localized embeds
 * (FFC-EX-newheightseducation.org#24) is what let the request happen — the gate
 * did not start failing because the export got worse, it started failing
 * because the export finally got far enough to notice.
 *
 * WHY A POST-CAPTURE PASS. Same reasoning as `heal-missing-asset-refs.mjs`: the
 * capture artifact costs hours of deliberately-slow crawling against a
 * charity's shared hosting and already exists. A fix inside the capture cannot
 * reach one already taken; this runs on a reused capture in seconds.
 *
 * WHAT IT WILL NOT DO, which is the part worth arguing with, because this is
 * the only pass in the pipeline that fetches from the network after the crawl:
 *
 *   - It never invents a host. The URL is built from the asset's own location
 *     under `_ffc-assets/<host>/…`, so it can only ever request a host the
 *     capture already downloaded from. Nothing in the file's CONTENT chooses a
 *     host; content chooses only a path.
 *   - It follows RELATIVE references only. An absolute URL, a protocol-relative
 *     `//host/…`, a root-relative `/…`, and anything whose resolved path
 *     escapes `_ffc-assets/<host>/` are all skipped. That is what keeps the
 *     first bullet true.
 *   - It never overwrites. A file already on disk is left exactly as captured.
 *   - It does not fail the run. The self-containment gate is the authority on
 *     whether an export is broken, and it speaks about references a visitor can
 *     actually reach. This pass reports what it could not fetch, by name, and
 *     lets the gate decide — the same division of labour `heal-missing-asset-refs`
 *     already draws.
 *
 * Usage:
 *   node scripts/fetch-asset-subresources.mjs --site <assetsParent> [--delay-ms 250]
 *                                             [--max 200] [--dry-run]
 *   node scripts/fetch-asset-subresources.mjs --self-test
 *
 * `--site` is the directory that CONTAINS `_ffc-assets`.
 */
import {
  readdirSync,
  statSync,
  existsSync,
  readFileSync,
  writeFileSync,
  mkdirSync,
  mkdtempSync,
  rmSync,
} from 'node:fs';
import { join, dirname, resolve, relative, sep, posix } from 'node:path';
import { tmpdir } from 'node:os';

const ASSETS_DIR = '_ffc-assets';

/** Documents that can carry a relative reference to a sibling file. */
const DOCUMENT_EXT = new Set(['.html', '.htm']);

/**
 * `src="…"` / `href="…"` on a tag that fetches a SUB-RESOURCE.
 *
 * Deliberately not every `href`: an `<a href>` is navigation, and pre-fetching
 * every link a third-party embed happens to carry would turn a repair into a
 * crawl. Only `script`, `link`, `img`, `iframe`, `source` and `embed` are read.
 */
const SUBRESOURCE_TAG = /<(script|link|img|iframe|source|embed)\b[^>]*>/gi;
const REF_ATTR = /\b(?:src|href)\s*=\s*(?:"([^"]*)"|'([^']*)')/i;

/** A reference this pass may follow: relative, no scheme, no root, no traversal. */
export function isFollowableRelativeRef(ref) {
  if (typeof ref !== 'string') return false;
  const value = ref.trim();
  if (!value) return false;
  // A scheme (`https:`, `data:`, `mailto:`), a protocol-relative `//host/…`,
  // a root-relative `/…`, and a fragment or query-only reference are all
  // either not ours to resolve or not a file.
  if (/^[a-z][a-z0-9+.-]*:/i.test(value)) return false;
  // One check, not two: the protocol-relative `//host/…` form starts with `/`,
  // so a separate test for it can never be the reason anything is refused. It
  // was here, and no mutation of it could be made to fail a test — which is the
  // signal that it was decoration rather than a guard.
  // Backslash counts as a separator and as a root, because on Windows
  // `path.resolve` treats it as both: `resolve('C:/a/b', '..\\..\\x.js')` is
  // `C:\\x.js`, and `resolve('C:/a/b', '\\x.js')` is `C:\\x.js` too. On Linux the
  // same strings are an ordinary filename, so a `/`-only guard is correct on
  // the runner and wrong anywhere this is run by hand — and it is run by hand.
  // A backslash in a web reference is a mistake or an escape, never a path a
  // server resolves the way the document meant, so refusing it loses nothing.
  if (value.startsWith('/') || value.startsWith('\\')) return false;
  if (value.startsWith('#') || value.startsWith('?')) return false;
  // Strip the fragment and query: they address a part of the file, not a
  // different file, and the capture writes the file under its bare path.
  const bare = value.split('#')[0].split('?')[0];
  if (!bare) return false;
  // Traversal is tested on the DECODED segment, not the literal one.
  //
  // `%2e%2e` decodes to `..`, and `encodeURIComponent('..')` is `..` — dots are
  // unreserved, so they do not re-encode. Once `sourceUrlFor` decodes a segment
  // to avoid double-encoding, a literal-only test lets an embed smuggle a live
  // `..` into the fetch URL: the file resolves on disk under the inert name
  // `%2e%2e` while the request goes somewhere else entirely. The decode and
  // this check have to agree about what a segment MEANS, or the guard is
  // checking a different string from the one that gets sent.
  for (const segment of bare.split(/[\\/]+/)) {
    let meaning = segment;
    try {
      meaning = decodeURIComponent(segment);
    } catch {
      // Not decodable, so it cannot become `..` on the way out either.
    }
    if (meaning === '..' || meaning === '.') return false;
  }
  return true;
}

/** Every `src`/`href` on a sub-resource tag in this document. */
export function subresourceRefs(html) {
  const found = [];
  for (const tag of String(html).match(SUBRESOURCE_TAG) ?? []) {
    const m = tag.match(REF_ATTR);
    if (!m) continue;
    const ref = m[1] ?? m[2] ?? '';
    if (isFollowableRelativeRef(ref)) found.push(ref.trim().split('#')[0].split('?')[0]);
  }
  return found;
}

/**
 * Percent-encode one path segment WITHOUT double-encoding one that already is.
 *
 * The on-disk name is whatever the document referenced, resolved against its
 * directory — so it arrives either decoded (`Crèche.png`, which is what
 * `assetLocalName` writes when decoding is safe) or still encoded
 * (`Cr%C3%A8che.png`, which is what a relative reference in the document
 * literally says). Encoding the second form again yields `Cr%25C3%25A8che.png`,
 * a different URL, and the fetch 404s on a file that is really there.
 *
 * Decoding first makes both forms converge. A segment whose escapes are
 * malformed is not decodable and is encoded literally, which is correct: a
 * filename containing a bare `%` is a filename containing a bare `%`.
 */
function encodePathSegment(segment) {
  let decoded = segment;
  try {
    decoded = decodeURIComponent(segment);
  } catch {
    // Not a valid encoding, so the `%` is part of the name, not an escape.
  }
  return encodeURIComponent(decoded);
}

/**
 * The URL a file under `_ffc-assets/<host>/<rest>` was downloaded from.
 *
 * The host comes from the PATH, never from the document, which is what bounds
 * this pass to hosts the capture already reached.
 */
export function sourceUrlFor(assetsRoot, absPath) {
  const rel = relative(assetsRoot, absPath).split(sep).join('/');
  if (!rel || rel.startsWith('../')) return null;
  const slash = rel.indexOf('/');
  if (slash <= 0) return null;
  const host = rel.slice(0, slash);
  const path = rel.slice(slash + 1);
  if (!path) return null;
  // A host directory is a hostname, nothing else. Anything with a character a
  // hostname cannot contain is not one, and is not fetched.
  if (!/^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$/i.test(host)) {
    return null;
  }
  return `https://${host}/${path.split('/').map(encodePathSegment).join('/')}`;
}

function walk(dir, out = []) {
  let entries;
  try {
    entries = readdirSync(dir, { withFileTypes: true });
  } catch {
    return out;
  }
  for (const entry of entries) {
    const p = join(dir, entry.name);
    if (entry.isDirectory()) walk(p, out);
    else if (entry.isFile()) out.push(p);
  }
  return out;
}

/**
 * Every (document, missing sibling) pair in the capture.
 *
 * Pure: it reads the tree and reports, so the fetching half can be tested
 * without a network and this half without a filesystem full of fixtures.
 */
export function planFetches(assetsRoot) {
  const wanted = new Map();
  for (const file of walk(assetsRoot)) {
    const ext = file.slice(file.lastIndexOf('.')).toLowerCase();
    if (!DOCUMENT_EXT.has(ext)) continue;
    let html;
    try {
      html = readFileSync(file, 'utf8');
    } catch {
      continue;
    }
    for (const ref of subresourceRefs(html)) {
      const target = resolve(dirname(file), ref);
      // The resolve must stay inside the capture. A reference that climbs out
      // is refused rather than clamped: clamping would silently fetch a
      // DIFFERENT path than the document asked for.
      //
      // UNREACHABLE GIVEN THE FILTER ABOVE, and saying so is the point.
      // `isFollowableRelativeRef` rejects a `.`/`..` segment on either
      // separator and a root on either separator, so no input that survives it
      // can escape. No mutation of this line fails a test.
      //
      // That claim used to be written without the qualifier, and it was wrong
      // on Windows: the filter split on `/` only, while `path.resolve` there
      // also honours `\\`, so `..\\..\\x.js` reached this line and THIS was the
      // guard that stopped it. The filter now covers both separators, which is
      // what makes the sentence true again.
      // It is kept as a second barrier because the filter and this resolve are
      // separated by a function boundary, and the day someone relaxes the
      // filter is the day it earns its place — but it is defence in depth, not
      // a tested guard, and the tests below deliberately do not pretend
      // otherwise.
      const relToRoot = relative(assetsRoot, target);
      if (!relToRoot || relToRoot.startsWith('..') || relToRoot.includes(`..${sep}`)) continue;
      if (existsSync(target)) continue;
      const url = sourceUrlFor(assetsRoot, target);
      if (!url) continue;
      if (!wanted.has(target)) wanted.set(target, { target, url, referencedBy: [] });
      wanted.get(target).referencedBy.push(file);
    }
  }
  return [...wanted.values()].sort((a, b) => a.target.localeCompare(b.target));
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/**
 * Fetch, following redirects ONLY while the host does not change.
 *
 * `redirect: 'follow'` would have made the "never invents a host" bound a
 * statement about the FIRST request rather than about this pass: the host it
 * lands on is then chosen by whatever the origin returns, not by the capture.
 * And a stalled origin with no timeout hangs a job that has already spent a
 * multi-hour crawl.
 *
 * So redirects are handled here rather than by the runtime: each hop is
 * checked against the host the capture already reached, a cross-host hop is
 * reported as a miss instead of being followed, and every request carries a
 * deadline.
 *
 * `fetchImpl` is injectable so the hop logic is tested without a network —
 * otherwise the one part of this file with a real security bound would be the
 * one part no test could reach.
 */
export async function fetchSameHost(url, opts = {}) {
  const { fetchImpl = fetch, maxHops = 3, timeoutMs = 20000 } = opts;
  const origin = new URL(url);
  let current = url;
  for (let hop = 0; hop <= maxHops; hop += 1) {
    const res = await fetchImpl(current, {
      redirect: 'manual',
      signal: AbortSignal.timeout(timeoutMs),
    });
    const status = res.status ?? 0;
    if (status >= 300 && status < 400) {
      const location = res.headers?.get?.('location');
      if (!location) return { ok: false, reason: `HTTP ${status} with no Location` };
      let next;
      try {
        next = new URL(location, current);
      } catch {
        return { ok: false, reason: `HTTP ${status} to an unparseable Location` };
      }
      if (next.hostname !== origin.hostname) {
        return { ok: false, reason: `redirect to a different host (${next.hostname})` };
      }
      current = next.toString();
      continue;
    }
    if (!res.ok) return { ok: false, reason: `HTTP ${status}` };
    return { ok: true, body: Buffer.from(await res.arrayBuffer()) };
  }
  return { ok: false, reason: `more than ${maxHops} redirects` };
}

async function main(argv) {
  const arg = (name, fallback = null) => {
    const i = argv.indexOf(name);
    return i >= 0 && argv[i + 1] !== undefined ? argv[i + 1] : fallback;
  };
  const site = arg('--site');
  const dryRun = argv.includes('--dry-run');
  const delayMs = Number(arg('--delay-ms', '250')) || 0;
  const max = Number(arg('--max', '200')) || 200;
  const timeoutMs = Number(arg('--timeout-ms', '20000')) || 20000;

  if (!site) {
    console.error('::error::--site is required (the directory that contains _ffc-assets).');
    process.exit(1);
  }
  const assetsRoot = join(site, ASSETS_DIR);
  if (!existsSync(assetsRoot)) {
    console.log(`[subresources] no ${ASSETS_DIR} under ${site}; nothing to do.`);
    return;
  }

  const plan = planFetches(assetsRoot);
  if (!plan.length) {
    console.log('[subresources] every relative reference inside a localized document resolves.');
    return;
  }

  console.log(
    `[subresources] ${plan.length} file(s) referenced relatively by a localized document are absent.`,
  );
  let fetched = 0;
  let failed = 0;
  for (const item of plan.slice(0, max)) {
    const shown = relative(assetsRoot, item.target).split(sep).join('/');
    if (dryRun) {
      console.log(`  WOULD FETCH ${shown}  <- ${item.url}`);
      continue;
    }
    try {
      const res = await fetchSameHost(item.url, { timeoutMs });
      if (!res.ok) {
        console.log(`  MISS ${shown} — ${res.reason} from ${item.url}`);
        failed += 1;
      } else {
        mkdirSync(dirname(item.target), { recursive: true });
        writeFileSync(item.target, res.body);
        console.log(`  GOT  ${shown} (${res.body.length} bytes)`);
        fetched += 1;
      }
    } catch (err) {
      console.log(`  MISS ${shown} — ${err?.message ?? err}`);
      failed += 1;
    }
    if (delayMs) await sleep(delayMs);
  }
  if (plan.length > max) {
    console.log(
      `[subresources] ${plan.length - max} further reference(s) not attempted (--max ${max}).`,
    );
  }
  console.log(`[subresources] fetched ${fetched}, unresolved ${failed}.`);
  if (failed) {
    console.log(
      '[subresources] an unresolved reference is not fatal here — the self-containment gate decides, because it speaks only about what a visitor actually requests.',
    );
  }
}

// --- self-test -------------------------------------------------------------

function selfTest() {
  let failures = 0;
  const eq = (label, actual, expected) => {
    const a = JSON.stringify(actual);
    const e = JSON.stringify(expected);
    if (a === e) {
      console.log(`ok   ${label}`);
    } else {
      console.log(`FAIL ${label}\n  expected ${e}\n  actual   ${a}`);
      failures += 1;
    }
  };

  eq('a bare relative path is followable', isFollowableRelativeRef('js/main.js'), true);
  eq('an absolute URL is not', isFollowableRelativeRef('https://cdn.example/x.js'), false);
  eq('a protocol-relative URL is not', isFollowableRelativeRef('//cdn.example/x.js'), false);
  eq('a root-relative path is not', isFollowableRelativeRef('/x.js'), false);
  eq('a data: URI is not', isFollowableRelativeRef('data:text/js,alert(1)'), false);
  eq('a fragment is not', isFollowableRelativeRef('#top'), false);
  eq('a traversal is not', isFollowableRelativeRef('../../etc/passwd'), false);
  // `%2e%2e` decodes to `..`, and `..` does not re-encode — so before this was
  // tested on the decoded segment, an embed could put a live traversal into the
  // fetch URL. The double-encoding fix is what made it reachable.
  eq(
    'a percent-encoded traversal is not, either',
    isFollowableRelativeRef('%2e%2e/%2e%2e/secret'),
    false,
  );
  eq('a mixed-case percent-encoded traversal is not', isFollowableRelativeRef('%2E%2e/x'), false);
  eq('a percent-encoded single dot is not', isFollowableRelativeRef('%2e/x'), false);
  eq('a filename that merely CONTAINS dots is fine', isFollowableRelativeRef('a..b/x.js'), true);
  // On Windows `path.resolve` honours `\\` as a separator and as a root, so a
  // `/`-only guard is correct on the runner and wrong on the machine an
  // operator runs this from.
  eq('a backslash traversal is not followable', isFollowableRelativeRef('..\\..\\x.js'), false);
  eq('a backslash root is not followable', isFollowableRelativeRef('\\x.js'), false);
  eq(
    'a backslash traversal hidden in escapes is not either',
    isFollowableRelativeRef('%2e%2e\\x.js'),
    false,
  );
  eq('a mixed-separator traversal is not', isFollowableRelativeRef('js/..\\..\\x'), false);
  eq('an empty reference is not', isFollowableRelativeRef('   '), false);

  eq(
    'a script src is a sub-resource',
    subresourceRefs('<script src="js/main-48d3ed6a76.js"></script>'),
    ['js/main-48d3ed6a76.js'],
  );
  eq(
    'an anchor href is NOT — following it would be a crawl, not a repair',
    subresourceRefs('<a href="other-page.html">next</a>'),
    [],
  );
  eq(
    'the query and fragment are dropped, because they address part of one file',
    subresourceRefs('<img src="a/b.png?ver=2#frag">'),
    ['a/b.png'],
  );
  eq(
    'an absolute src is left to the network',
    subresourceRefs('<script src="https://cdn.example/player-bundle.js"></script>'),
    [],
  );

  eq(
    'the fetch URL comes from the PATH, not the document',
    sourceUrlFor('/cap/_ffc-assets', '/cap/_ffc-assets/s3.amazonaws.com/embed.animoto.com/js/m.js'),
    'https://s3.amazonaws.com/embed.animoto.com/js/m.js',
  );
  eq(
    'a file directly in the assets root has no host, so no URL',
    sourceUrlFor('/cap/_ffc-assets', '/cap/_ffc-assets/loose.js'),
    null,
  );
  eq(
    'a host directory that is not a hostname is refused',
    sourceUrlFor('/cap/_ffc-assets', '/cap/_ffc-assets/not a host/x.js'),
    null,
  );
  eq(
    'a single-label host directory is refused',
    sourceUrlFor('/cap/_ffc-assets', '/cap/_ffc-assets/localhost/x.js'),
    null,
  );

  // A relative reference in a document says what it says. An already-encoded
  // segment must reach the network unchanged, not as `%25…`.
  eq(
    'an already-encoded segment is not encoded twice',
    sourceUrlFor('/cap/_ffc-assets', '/cap/_ffc-assets/ex.com/img/Cr%C3%A8che.png'),
    'https://ex.com/img/Cr%C3%A8che.png',
  );
  eq(
    'a decoded segment is encoded, so both spellings converge on one URL',
    sourceUrlFor('/cap/_ffc-assets', '/cap/_ffc-assets/ex.com/img/Cr\u00e8che.png'),
    'https://ex.com/img/Cr%C3%A8che.png',
  );
  eq(
    'a bare % that is not an escape is part of the name',
    sourceUrlFor('/cap/_ffc-assets', '/cap/_ffc-assets/ex.com/img/100%.png'),
    'https://ex.com/img/100%25.png',
  );
  eq(
    'a slash encoded inside a segment round-trips',
    sourceUrlFor('/cap/_ffc-assets', '/cap/_ffc-assets/ex.com/a%2Fb.js'),
    'https://ex.com/a%2Fb.js',
  );

  // --- the tree shape ------------------------------------------------------
  const dir = mkdtempSync(join(tmpdir(), 'ffc-subres-'));
  try {
    const root = join(dir, ASSETS_DIR);
    const host = join(root, 's3.amazonaws.com', 'embed.animoto.com');
    mkdirSync(host, { recursive: true });
    writeFileSync(
      join(host, 'play__w-x.html'),
      '<script src="js/main-48d3ed6a76.js"></script>' +
        '<script src="https://d150hyw1dtprld.cloudfront.net/player/x/player-bundle.js"></script>' +
        '<img src="present.png">' +
        '<a href="elsewhere.html">n</a>',
    );
    writeFileSync(join(host, 'present.png'), 'x');

    const plan = planFetches(root);
    eq(
      'only the missing relative sub-resource is planned',
      plan.map((p) => p.url),
      ['https://s3.amazonaws.com/embed.animoto.com/js/main-48d3ed6a76.js'],
    );
    eq(
      'and it records which document asked for it',
      plan[0].referencedBy.map((f) => f.endsWith('play__w-x.html')),
      [true],
    );

    // A reference that climbs out of the assets root is refused, not clamped.
    const evil = join(root, 'evil.example.com');
    mkdirSync(evil, { recursive: true });
    writeFileSync(join(evil, 'p.html'), '<script src="../../../../etc/passwd"></script>');
    // This is killed by `isFollowableRelativeRef`'s `..` check, NOT by the
    // second barrier inside `planFetches` — removing that barrier leaves this
    // test green. Named accordingly, so the next reader is not told the barrier
    // is under test when it is not.
    eq(
      'a traversal is refused before it is ever resolved',
      planFetches(root).some((p) => p.url.includes('passwd')),
      false,
    );

    // Once the file is there, nothing is planned for it — so the pass is
    // idempotent and a retried conversion does not re-fetch what it already has.
    mkdirSync(join(host, 'js'), { recursive: true });
    writeFileSync(join(host, 'js', 'main-48d3ed6a76.js'), 'the fetched script');
    eq(
      'a reference that now resolves is not planned again',
      planFetches(root).map((p) => p.url),
      [],
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }

  return failures;
}

/** The redirect bound, exercised with a fake network. */
async function selfTestFetch() {
  let failures = 0;
  const eq = (label, actual, expected) => {
    const a = JSON.stringify(actual);
    const e = JSON.stringify(expected);
    if (a === e) {
      console.log(`ok   ${label}`);
    } else {
      console.log(`FAIL ${label}\n  expected ${e}\n  actual   ${a}`);
      failures += 1;
    }
  };

  const res = (status, headers = {}, body = '') => ({
    status,
    ok: status >= 200 && status < 300,
    headers: { get: (k) => headers[k.toLowerCase()] ?? null },
    arrayBuffer: async () => Buffer.from(body),
  });
  const scripted = (map) => {
    const seen = [];
    const impl = async (url) => {
      seen.push(url);
      return map[url] ?? res(404);
    };
    impl.seen = seen;
    return impl;
  };

  {
    const impl = scripted({ 'https://ex.com/a.js': res(200, {}, 'body') });
    const r = await fetchSameHost('https://ex.com/a.js', { fetchImpl: impl });
    eq('a plain 200 is fetched', [r.ok, r.body.toString()], [true, 'body']);
  }
  {
    const impl = scripted({
      'https://ex.com/a.js': res(302, { location: '/b.js' }),
      'https://ex.com/b.js': res(200, {}, 'moved'),
    });
    const r = await fetchSameHost('https://ex.com/a.js', { fetchImpl: impl });
    eq('a same-host redirect is followed', [r.ok, r.body.toString()], [true, 'moved']);
  }
  {
    const impl = scripted({
      'https://ex.com/a.js': res(302, { location: 'https://evil.example/x.js' }),
      'https://evil.example/x.js': res(200, {}, 'PAYLOAD'),
    });
    const r = await fetchSameHost('https://ex.com/a.js', { fetchImpl: impl });
    eq(
      'a CROSS-host redirect is refused',
      [r.ok, r.reason],
      [false, 'redirect to a different host (evil.example)'],
    );
    eq('...and the other host is never requested', impl.seen, ['https://ex.com/a.js']);
  }
  {
    const impl = scripted({
      'https://ex.com/a.js': res(302, { location: '/a.js' }),
    });
    const r = await fetchSameHost('https://ex.com/a.js', { fetchImpl: impl, maxHops: 2 });
    eq('a redirect loop stops', [r.ok, r.reason], [false, 'more than 2 redirects']);
  }
  {
    const impl = scripted({ 'https://ex.com/a.js': res(404) });
    const r = await fetchSameHost('https://ex.com/a.js', { fetchImpl: impl });
    eq('a 404 is a miss, named', [r.ok, r.reason], [false, 'HTTP 404']);
  }
  {
    let sawSignal = false;
    const impl = async (_u, init) => {
      sawSignal = !!init?.signal;
      return res(200, {}, 'x');
    };
    await fetchSameHost('https://ex.com/a.js', { fetchImpl: impl });
    eq('every request carries a deadline', sawSignal, true);
  }
  {
    let sawManual = false;
    const impl = async (_u, init) => {
      sawManual = init?.redirect === 'manual';
      return res(200, {}, 'x');
    };
    await fetchSameHost('https://ex.com/a.js', { fetchImpl: impl });
    eq("redirects are ours to decide, not the runtime's", sawManual, true);
  }

  return failures;
}

async function runSelfTests() {
  const failures = selfTest() + (await selfTestFetch());
  console.log('');
  console.log(failures ? `${failures} self-test(s) failed` : 'all self-tests passed');
  return failures ? 1 : 0;
}

const argv = process.argv.slice(2);
if (argv.includes('--self-test')) {
  runSelfTests().then((code) => process.exit(code));
} else {
  main(argv).catch((err) => {
    console.error(`::error::${err?.stack ?? err}`);
    process.exit(1);
  });
}
