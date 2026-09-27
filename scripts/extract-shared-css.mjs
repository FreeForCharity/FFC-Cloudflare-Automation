#!/usr/bin/env node
/**
 * Lift byte-identical inline <style> blocks out of captured pages into shared
 * stylesheets, replacing each with a <link> AT THE SAME POSITION.
 *
 * WHY THIS EXISTS. A WordPress capture inlines the same plugin and theme CSS
 * into every page it renders, and nothing downstream deduplicates it. Measured
 * on newheightseducation.org's apex export: 130.2 MB of HTML across 785
 * fragments, mean 170 KB, SMALLEST page 106 KB -- that floor is boilerplate.
 * 64.3 MB of the total is inline <style> and 62.5 MB of that is byte-identical
 * repetition, one 10 KB block appearing on 430 pages and several 85-92 KB
 * blocks on 84-245 pages each.
 *
 * Unlike the image and PDF passes this is not a trade. The visitor stops
 * re-downloading the same CSS on every page and starts getting it once from
 * cache, so the page gets lighter AND the site gets faster; the only cost is
 * more requests on the very first view, which HTTP/2 multiplexes.
 *
 * WHY IN PLACE, NOT BUNDLED. CSS cascade is document order, and <link> and
 * <style> participate in the same order, so replacing each block where it
 * stands preserves the cascade exactly. Concatenating into one bundle does
 * not: the corpus carries 81 DISTINCT ordered sequences of shared blocks, and
 * only 349 of 785 pages' sequences are even an ordered subsequence of the most
 * common one, so no single file reproduces every page.
 *
 * WHAT IS REFUSED, and why each would be a silent break rather than a loud
 * one -- both are about what a reference RESOLVES AGAINST once it moves:
 *
 *   url(#fragment)  In an external stylesheet this resolves against the
 *                   STYLESHEET, not the document. The corpus uses
 *                   url(#ast-img-color-filter-2), and that SVG filter really
 *                   is present on all 245 pages that reference it, so moving
 *                   the rule breaks a live effect. 5 blocks, 20.0 MB.
 *   %%BASE%%        Substituted only by loadCloneContent() when a fragment is
 *                   READ. A .css under public/ is served verbatim, so the
 *                   token would ship literally and break every url() in it.
 *                   12 blocks, 5.7 MB.
 *
 * Refusing both leaves 55 blocks and 36.7 MB of inline CSS removed for 0.18 MB
 * of .css plus 0.43 MB of <link> markup: NET 36.1 MB off the repo. In the
 * built export that is 133.9 -> 113.9 MB of HTML plus 187.5 -> 157.6 MB of
 * Next's RSC payloads, which embed the same markup -- 49.9 MB off what ships.
 *
 * PROVEN TWO WAYS, because each misses what the other catches:
 *   1. Invertibility, per page, every run: re-inlining what was extracted must
 *      reproduce the input byte for byte. Cheap, total, and it proves nothing
 *      was lost or reordered -- but it cannot prove the BROWSER agrees, since
 *      order is the thing being moved.
 *   2. A built-vs-built pixel diff of the two exports. See the PR for the run.
 */
import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync, mkdirSync, readdirSync, statSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

/**
 * A block has to appear at least this many times to be worth a file.
 *
 * At 1 it is not shared and extracting it ADDS a request and a file for no
 * saving at all -- the inline copy becomes a .css plus a <link> that is longer
 * than nothing.
 */
export const MIN_PAGES = 2;

/**
 * ...and be at least this large.
 *
 * A <link> costs ~86 bytes of markup, so below a few hundred bytes the
 * transform can cost more than it saves on a block that appears only twice.
 * 256 keeps the smallest win comfortably positive.
 */
export const MIN_BYTES = 256;

const STYLE = /<style\b([^>]*)>([\s\S]*?)<\/style\s*>/gi;

/** Why this CSS must stay inline, or null if it may be extracted. */
export function refuseReason(css) {
  if (typeof css !== 'string') return 'not a string';
  if (css.includes('%%BASE%%')) return 'contains %%BASE%%';
  for (const m of css.matchAll(/url\s*\(\s*["']?([^"')]*)/gi)) {
    if ((m[1] || '').trim().startsWith('#')) return 'contains url(#fragment)';
  }
  return null;
}

export function hashCss(css) {
  return createHash('sha256').update(css, 'utf8').digest('hex').slice(0, 16);
}

/**
 * The `media` of a <style>, to carry onto the <link> that replaces it.
 *
 * Dropping it is not cosmetic. A `<style media="screen">` that becomes an
 * unconditional <link> starts applying to PRINT as well, so the page gains
 * rules in the medium nobody checks. Three extractable blocks in the
 * newheightseducation.org corpus carry media="screen", on 2, 110 and 428
 * pages -- 540 occurrences that would have changed behaviour silently.
 *
 * `id` is dropped deliberately, which is the opposite call on similar-looking
 * evidence. 223 distinct ids appear on extractable blocks and NOT ONE is
 * referenced -- by getElementById, by querySelector, or by a selector --
 * anywhere in the 785 pages or the 4,392 captured asset files. One block's
 * identical CSS ships under three different ids across 215 pages, which is
 * what they are: per-render noise, not identity.
 *
 * `media="all"` is the default and is normalized away rather than emitted.
 */
export function mediaOf(attrString) {
  // `(?<![-\w])`, not `\b`. A word boundary exists between the `-` and the
  // `media` of `data-media="x"`, so `\bmedia\s*=` reads that attribute as a
  // media query and would emit `media="x"` on the link. Caught by this file's
  // own self-test; it is the same guard verify-fidelity.mjs uses on `class=`.
  const m = /(?<![-\w])media\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/i.exec(attrString || '');
  const value = ((m && (m[1] ?? m[2] ?? m[3])) || '').trim();
  return value && value.toLowerCase() !== 'all' ? value : null;
}

/** Which blocks are worth extracting, given the whole corpus. */
export function plan(pages) {
  const count = new Map();
  const text = new Map();
  for (const { html } of pages) {
    for (const m of html.matchAll(STYLE)) {
      const css = m[2];
      const h = hashCss(css);
      count.set(h, (count.get(h) ?? 0) + 1);
      text.set(h, css);
    }
  }
  const chosen = new Map();
  const refused = new Map();
  for (const [h, n] of count) {
    const css = text.get(h);
    if (n < MIN_PAGES || css.length < MIN_BYTES) continue;
    const why = refuseReason(css);
    if (why) refused.set(h, { css, n, why });
    else chosen.set(h, { css, n });
  }
  return { chosen, refused };
}

/**
 * Rewrite one page against a plan.
 *
 * Returns the new HTML AND an ordered log of what each emitted <link> stood in
 * for. The log is what makes the transform provably invertible: the original
 * opening tags vary (`<style id="critical-path-css" type="text/css">`, a bare
 * `<style>`, and 223 other ids), so an inverse that rebuilds a FIXED opening
 * tag cannot reproduce the input. The first draft did exactly that and
 * reported all 785 pages as lossy -- which reads as "the transform is broken"
 * rather than "the check is", and is the more expensive way to be wrong.
 */
export function rewrite(html, chosen, hrefFor) {
  const log = [];
  const out = html.replace(STYLE, (whole, attrString, css) => {
    const h = hashCss(css);
    if (!chosen.has(h)) return whole;
    const media = mediaOf(attrString);
    const link = media
      ? `<link rel="stylesheet" media="${media}" href="${hrefFor(h)}" />`
      : `<link rel="stylesheet" href="${hrefFor(h)}" />`;
    log.push({ link, whole });
    return link;
  });
  return { html: out, log };
}

/**
 * Inverse of `rewrite`, used only to PROVE the transform lost nothing.
 *
 * Walks the log in emission order and undoes one occurrence at a time, so two
 * identical links on one page are restored to their own original tags rather
 * than both to the first one's. Returns null if a link it emitted is gone,
 * which is not invertible and must fail the run rather than be reported as a
 * match.
 */
export function reinline(html, log) {
  let out = '';
  let rest = html;
  for (const { link, whole } of log) {
    const at = rest.indexOf(link);
    if (at === -1) return null;
    out += rest.slice(0, at) + whole;
    rest = rest.slice(at + link.length);
  }
  return out + rest;
}

function htmlFiles(dir) {
  const out = [];
  for (const e of readdirSync(dir)) {
    const p = join(dir, e);
    if (statSync(p).isDirectory()) out.push(...htmlFiles(p));
    else if (e.endsWith('.html')) out.push(p);
  }
  return out;
}

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  return i !== -1 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}

function main() {
  const contentDir = arg('content-dir', '');
  const cssDir = arg('css-dir', '');
  const hrefBase = arg('href-base', '%%BASE%%/_ffc-css');
  if (!contentDir || !cssDir) {
    console.error(
      'Usage:\n' +
        '  --content-dir <dir> --css-dir <dir> [--href-base %%BASE%%/_ffc-css]\n' +
        '  --self-test',
    );
    return 2;
  }
  const files = htmlFiles(contentDir);
  if (!files.length) {
    console.error(`::error::no .html under ${contentDir} — refusing to report a vacuous 0 saved`);
    return 1;
  }
  const pages = files.map((f) => ({ f, html: readFileSync(f, 'utf8') }));
  const { chosen, refused } = plan(pages);
  const hrefFor = (h) => `${hrefBase}/${h}.css`;

  // Everything is rewritten and CHECKED before anything is written, so a
  // corpus that cannot round-trip leaves the tree untouched rather than
  // half-converted.
  const staged = [];
  const lossy = [];
  for (const { f, html } of pages) {
    const { html: next, log } = rewrite(html, chosen, hrefFor);
    if (reinline(next, log) !== html) lossy.push(relative(contentDir, f));
    staged.push({ f, html, next });
  }
  if (lossy.length) {
    console.error(
      `::error::${lossy.length} page(s) did not round-trip; nothing was written: ` +
        lossy.slice(0, 10).join(', '),
    );
    return 1;
  }

  mkdirSync(cssDir, { recursive: true });
  for (const [h, { css }] of chosen) writeFileSync(join(cssDir, `${h}.css`), css, 'utf8');
  let before = 0;
  let after = 0;
  for (const { f, html, next } of staged) {
    before += Buffer.byteLength(html);
    after += Buffer.byteLength(next);
    writeFileSync(f, next, 'utf8');
  }
  const cssBytes = [...chosen.values()].reduce((a, b) => a + Buffer.byteLength(b.css), 0);
  const mb = (n) => (n / 1048576).toFixed(2);
  const byReason = new Map();
  for (const r of refused.values()) byReason.set(r.why, (byReason.get(r.why) ?? 0) + 1);
  console.log(`[css] ${pages.length} page(s), ${chosen.size} block(s) extracted`);
  for (const [why, n] of byReason) console.log(`[css] ${n} block(s) left inline: ${why}`);
  console.log(
    `[css] html ${mb(before)} -> ${mb(after)} MB, css ${mb(cssBytes)} MB, ` +
      `net ${mb(before - after - cssBytes)} MB saved`,
  );
  console.log(`[css] round-trip verified on ${pages.length}/${pages.length} page(s)`);
  return 0;
}

/* ---------------------------------------------------------------------
 * Self-test — `node scripts/extract-shared-css.mjs --self-test`
 * ------------------------------------------------------------------ */
function selfTest() {
  let failures = 0;
  const eq = (name, actual, expected) => {
    const a = JSON.stringify(actual);
    const e = JSON.stringify(expected);
    const ok = a === e;
    console.log(`${ok ? 'ok  ' : 'FAIL'} ${name}`);
    if (!ok) {
      console.log(`  expected ${e}`);
      console.log(`  actual   ${a}`);
      failures += 1;
    }
  };
  const big = (seed) => `.x{content:"${seed}"}`.padEnd(400, ' ');

  // --- what may be extracted -------------------------------------------
  eq('plain CSS is extractable', refuseReason('.a{color:red}'), null);
  eq(
    'a data: url is extractable — it carries its own content, not a path',
    refuseReason('.a{background:url(data:image/png;base64,iVBOR)}'),
    null,
  );
  eq(
    'an absolute url is extractable — it does not depend on where it is',
    refuseReason('.a{background:url(https://x.org/i.png)}'),
    null,
  );
  eq('a root-relative url is extractable', refuseReason('.a{background:url(/img/i.png)}'), null);

  // --- what must stay inline -------------------------------------------
  // Both of these resolve against something that CHANGES when the rule moves.
  eq(
    'a bare fragment url is refused: it would resolve against the stylesheet',
    refuseReason('.a{filter:url(#ast-img-color-filter-2)}'),
    'contains url(#fragment)',
  );
  eq(
    'quoted, and with whitespace, is still refused',
    refuseReason(".a{filter:url( '#f' )}"),
    'contains url(#fragment)',
  );
  eq(
    'a %%BASE%% url is refused: a static .css never gets it substituted',
    refuseReason('.a{background:url(%%BASE%%/_ffc-assets/i.png)}'),
    'contains %%BASE%%',
  );
  eq(
    'one bad url among good ones still refuses the whole block',
    refuseReason('.a{background:url(data:x)}.b{filter:url(#f)}'),
    'contains url(#fragment)',
  );

  // --- media, which changes behaviour if dropped ------------------------
  eq('media="screen" is carried', mediaOf(' id="x" media="screen"'), 'screen');
  eq('single quotes are read too', mediaOf("media='print'"), 'print');
  eq('an unquoted value is read too', mediaOf('media=print'), 'print');
  eq('media="all" is the default and is normalized away', mediaOf('media="all"'), null);
  eq('mixed case "ALL" is still the default', mediaOf('media="ALL"'), null);
  eq('no media attribute', mediaOf(' id="x"'), null);
  eq('no attributes at all', mediaOf(''), null);
  eq('a media-less tag is not confused by another attribute', mediaOf(' data-media="x"'), null);
  eq('...nor by a same-suffix attribute without the dash', mediaOf(' xmedia="x"'), null);
  eq(
    '...and the real attribute is still found beside a decoy',
    mediaOf(' data-media="x" media="print"'),
    'print',
  );

  // --- the plan ---------------------------------------------------------
  const shared = big('shared');
  const once = big('once');
  const tiny = '.t{color:red}';
  const pages = [
    { html: `<style>${shared}</style><style>${tiny}</style><style>${once}</style>` },
    { html: `<style id="a">${shared}</style><style>${tiny}</style>` },
  ];
  const { chosen, refused } = plan(pages);
  eq('a block on 2 pages is chosen', chosen.has(hashCss(shared)), true);
  eq('a block on 1 page is not', chosen.has(hashCss(once)), false);
  eq('a block under MIN_BYTES is not, however often it appears', chosen.has(hashCss(tiny)), false);
  eq('nothing was refused in this corpus', refused.size, 0);
  eq('identical CSS under DIFFERENT ids is one block, not two', chosen.get(hashCss(shared)).n, 2);
  const refusable = `.a{filter:url(#f)}`.padEnd(400, ' ');
  const { chosen: c2, refused: r2 } = plan([
    { html: `<style>${refusable}</style>` },
    { html: `<style>${refusable}</style>` },
  ]);
  eq('a shared but refusable block is refused, not chosen', [c2.size, r2.size], [0, 1]);

  // --- rewrite and its inverse -----------------------------------------
  const href = (h) => `/_ffc-css/${h}.css`;
  const page = `<h1>hi</h1><style id="critical-path-css" type="text/css">${shared}</style><p>x</p>`;
  const { html: out, log } = rewrite(page, chosen, href);
  eq(
    'the block becomes a link at the same position',
    out,
    `<h1>hi</h1><link rel="stylesheet" href="${href(hashCss(shared))}" />\n<p>x</p>`.replace(
      '\n',
      '',
    ),
  );
  eq('re-inlining reproduces the input exactly', reinline(out, log), page);
  const mediaPage = `<style media="screen">${shared}</style>`;
  const { html: mOut, log: mLog } = rewrite(mediaPage, chosen, href);
  eq('media survives onto the link', mOut.includes('media="screen"'), true);
  eq('...and the media page re-inlines exactly too', reinline(mOut, mLog), mediaPage);
  const twice = `<style>${shared}</style><style id="second">${shared}</style>`;
  const { html: tOut, log: tLog } = rewrite(twice, chosen, href);
  eq(
    'two identical blocks are restored to their OWN original tags, not both to the first',
    reinline(tOut, tLog),
    twice,
  );
  eq(
    'an unchosen block is left exactly as it was, attributes and all',
    rewrite(`<style id="keep" media="print">${tiny}</style>`, chosen, href).html,
    `<style id="keep" media="print">${tiny}</style>`,
  );
  eq('reinline reports a missing link rather than claiming a match', reinline('', tLog), null);

  console.log(failures ? `\n${failures} self-test(s) failed` : '\nall self-tests passed');
  return failures ? 1 : 0;
}

if (process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url))) {
  if (process.argv.includes('--self-test')) process.exit(selfTest());
  process.exit(main());
}
