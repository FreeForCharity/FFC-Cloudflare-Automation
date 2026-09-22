#!/usr/bin/env node
/**
 * heal-missing-asset-refs.mjs — repoint captured references that name a file
 * the capture does not have.
 *
 * `capture-wordpress-api.mjs` only rewrites a reference when the download
 * SUCCEEDED — `rewriteRefs` filters out a replacement whose target is empty,
 * so a failed fetch leaves the original absolute URL in the markup. A local
 * `_ffc-assets/...` reference to a file that is not on disk therefore cannot
 * come from a failed download. Something wrote the file and something else
 * later renamed or removed it, and the reference did not follow.
 *
 * Measured on newheightseducation.org (runs 35698011512 and 35725221081, the
 * same capture artifact): two pages reference
 * `_ffc-assets/i0.wp.com/publications.newheightseducation.org/wp-content/uploads/2024/06/books-book-pages-read-literature-159866__fit-1280-2C858-ssl-1.jpeg`
 * and the file is absent. The self-containment gate found it because those two
 * pages are inside its 120-page crawl; 673 further pages were not checked, so
 * the count the gate reports is a property of the crawl, not of the damage —
 * the same lesson the `public/` wipe taught in #1354.
 *
 * WHY A REPAIR RATHER THAN A ROOT-CAUSE FIX, FOR NOW. The capture artifact
 * that costs five hours of deliberately-slow crawling against a charity's
 * shared hosting already exists and already has this damage in it. A fix
 * inside the capture cannot reach it; this pass can, exactly as
 * `dedupe-capture-assets.mjs` does, so a retry costs minutes and puts no
 * further load on their origin.
 *
 * WHAT IT WILL AND WILL NOT DO. It only ever repoints a reference at a file
 * that is ALREADY IN THE CAPTURE — never at the network, never at the origin
 * being decommissioned, and it never deletes or creates an asset. So the worst
 * case is that it changes nothing.
 *
 * AND IT DOES NOT FAIL THE RUN. That is deliberate, and it is the one decision
 * here worth arguing with. The authority on whether an export is broken is the
 * self-containment gate, which loads real pages in a real browser and so speaks
 * only about references a visitor can actually reach. This pass scans every
 * text file in the capture, including pages nothing links to and records no
 * visitor loads. Failing on those would convert a two-page problem into a hard
 * stop on a charity's migration without anyone being worse off for shipping.
 * It reports what it could not resolve, loudly and by name, and lets the gate
 * decide.
 *
 * Read-only against the network; only rewrites text files under --site.
 *
 * Usage:
 *   node scripts/heal-missing-asset-refs.mjs --site <siteRoot> [--dry-run]
 *   node scripts/heal-missing-asset-refs.mjs --self-test
 */
import {
  readdirSync,
  lstatSync,
  existsSync,
  readFileSync,
  writeFileSync,
  mkdirSync,
  mkdtempSync,
  rmSync,
} from 'node:fs';
import { join, relative, sep, extname, basename } from 'node:path';
import { tmpdir } from 'node:os';
import { pathToFileURL } from 'node:url';

const ASSETS_DIR = '_ffc-assets';

/**
 * Extensions whose contents can carry an asset reference.
 *
 * Same allowlist as `dedupe-capture-assets.mjs`, and for the same reason: a
 * binary cannot carry one, and rewriting a binary would corrupt it.
 */
const TEXT_EXT = new Set(['.html', '.htm', '.css', '.js', '.mjs', '.svg', '.xml', '.json', '.txt']);

/**
 * The capture's own report is a RECORD of what the capture did, not a set of
 * live references, so it is neither scanned nor rewritten. A record naming a
 * file that was later renamed is still a true record.
 */
function isCaptureReport(absPath) {
  return basename(absPath).startsWith('wp-capture-report');
}

/** Every regular file under `root`, absolute, sorted for determinism. */
function walk(root) {
  const out = [];
  const visit = (dir) => {
    let entries;
    try {
      entries = readdirSync(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const e of entries) {
      const abs = join(dir, e.name);
      // lstat, not stat: a symlink is not a regular file, and following one
      // could rewrite something outside the capture.
      let st;
      try {
        st = lstatSync(abs);
      } catch {
        continue;
      }
      if (st.isDirectory()) visit(abs);
      else if (st.isFile()) out.push(abs);
    }
  };
  visit(root);
  return out.sort();
}

/** Text files whose contents may reference an asset. */
function textFiles(siteRoot) {
  return walk(siteRoot).filter(
    (abs) => TEXT_EXT.has(extname(abs).toLowerCase()) && !isCaptureReport(abs),
  );
}

/**
 * Every `_ffc-assets/...` reference in a document, plain or JSON-escaped.
 *
 * The `\\?\/` is load-bearing and is the reason this is a scanner rather than
 * a set of literal needles. WordPress inlines configuration as JSON inside
 * `<script>`, where every slash arrives escaped — `_ffc-assets\/i0.wp.com\/…`.
 * A plain-literal matcher reads that document as containing no references at
 * all, which is the failure mode to fear here: it is silent, and it points the
 * reassuring way (nothing to repair).
 *
 * The character class is the set a capture filename can contain, so a match
 * stops at the quote, bracket or space that ends the reference rather than
 * swallowing the rest of the line.
 */
const REFERENCE_RE = new RegExp(`${ASSETS_DIR}((?:\\\\?/[A-Za-z0-9._~%-]+)+)`, 'g');

/** The asset name a matched reference points at, with any escaping removed. */
export function referenceName(matchedTail) {
  return matchedTail.replace(/\\\//g, '/').replace(/^\//, '');
}

/** Distinct asset names referenced by `text`. */
export function referencesIn(text) {
  const names = new Set();
  for (const m of text.matchAll(REFERENCE_RE)) names.add(referenceName(m[1]));
  return names;
}

/**
 * The name without the `__<query>` suffix the capture folds into filenames.
 *
 * `capture-wordpress-api.mjs` writes `stem__query.ext` for `stem.ext?query`,
 * so the un-folded name is the same bytes under the name the site's own markup
 * uses. Returns null when there is no fold to strip, and when the stem STARTS
 * with the separator — `__x.png` has no base name to fall back to, and
 * returning `.png` there would be a candidate that could collide with a real
 * dotfile.
 */
export function stripQueryFold(name) {
  const slash = name.lastIndexOf('/');
  const dir = slash === -1 ? '' : name.slice(0, slash + 1);
  const file = name.slice(slash + 1);
  const ext = extname(file);
  const stem = ext ? file.slice(0, -ext.length) : file;
  const cut = stem.lastIndexOf('__');
  if (cut <= 0) return null;
  return `${dir}${stem.slice(0, cut)}${ext}`;
}

/**
 * The name the image re-encode pass would have written.
 *
 * The capture renames an oversized PNG/JPEG to `.webp` and rewrites references
 * from the returned name, so a stale reference to the original extension is
 * exactly what a missed rewrite there looks like. Mirrors `webpName` in the
 * capture; returns null for a name that is already WebP.
 */
export function asWebp(name) {
  if (/\.webp$/i.test(name)) return null;
  if (!/\.[^./]+$/.test(name)) return null;
  return name.replace(/\.[^./]+$/, '.webp');
}

/**
 * The same asset without its CDN host prefix.
 *
 * Jetpack serves a WordPress upload from `i0.wp.com/<origin-host>/<path>`, so
 * the capture stores the same bytes under both that name and the origin's own
 * `<origin-host>/<path>`. Requiring BOTH leading segments to look like hosts
 * is what keeps this from firing on an ordinary directory: a wrong guess is
 * harmless anyway, because a candidate is only ever used when the file it
 * names is really there.
 */
export function dropCdnHost(name) {
  const parts = name.split('/');
  if (parts.length < 3) return null;
  if (!parts[0].includes('.') || !parts[1].includes('.')) return null;
  return parts.slice(1).join('/');
}

/**
 * Names on disk, indexed by what they look like with any query fold removed.
 *
 * This is what lets a reference to one resized variant fall back to a
 * DIFFERENT resized variant of the same upload when the base name itself was
 * never stored — `photo__fit-1280.jpg` finding `photo__fit-640.jpg`. The image
 * renders at a different intrinsic size; the page's CSS decides how big it is
 * drawn, so the visitor sees the picture instead of a broken-image icon.
 */
export function indexByFoldFamily(assetsRoot) {
  const index = new Map();
  if (!existsSync(assetsRoot)) return index;
  for (const abs of walk(assetsRoot)) {
    const name = relative(assetsRoot, abs).split(sep).join('/');
    const key = stripQueryFold(name) ?? name;
    if (!index.has(key)) index.set(key, []);
    index.get(key).push(name);
  }
  // Shortest first, then lexicographic — the same rule `chooseCanonical` uses
  // in the dedupe pass, so both passes prefer the same member of a family and
  // a re-run is a no-op rather than a reshuffle.
  for (const list of index.values()) {
    list.sort((a, b) => a.length - b.length || (a < b ? -1 : a > b ? 1 : 0));
  }
  return index;
}

/**
 * Replacement names to try for a missing reference, best first.
 *
 * Order is by confidence, not convenience: an exact un-folded name is the same
 * bytes, a `.webp` sibling is the same picture re-encoded, and a different
 * fold of the same upload is the same picture at another size. Only after all
 * of those does it try the cross-host forms, which are the most speculative.
 */
export function candidateNames(name, foldIndex) {
  const out = [];
  const push = (n) => {
    if (n && n !== name && !out.includes(n)) out.push(n);
  };

  const forms = [name];
  const withoutCdn = dropCdnHost(name);
  if (withoutCdn) forms.push(withoutCdn);

  for (const form of forms) {
    if (form !== name) push(form);
    push(stripQueryFold(form));
    push(asWebp(form));
    const base = stripQueryFold(form);
    if (base) push(asWebp(base));
    for (const sibling of foldIndex.get(base ?? form) ?? []) push(sibling);
  }
  return out;
}

/**
 * A reference to `name`, and nothing that merely starts with it.
 *
 * Both the plain and the escaped spelling, because a document can carry the
 * same reference in both. The lookahead excludes exactly the characters that
 * could continue a path or a filename, so healing `logo.png` cannot touch
 * `logo.png.bak` or `logo.png2`.
 */
export function referenceRes(name) {
  const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const tail = '(?![A-Za-z0-9._~%/-])';
  const plain = `${ASSETS_DIR}/${name}`;
  const escaped = plain.split('/').join('\\/');
  return [
    { from: plain, re: new RegExp(`${escapeRe(plain)}${tail}`, 'g'), escaped: false },
    { from: escaped, re: new RegExp(`${escapeRe(escaped)}${tail}`, 'g'), escaped: true },
  ];
}

/**
 * Repoint every reference naming a file that is not in the capture.
 *
 * Resolution happens BEFORE anything is written, so a document is rewritten
 * only with replacements already known to exist on disk. Nothing is ever
 * deleted, and a reference with no resolvable candidate is reported and left
 * exactly as it was — a reference to a missing file is a broken image; a
 * reference silently pointed at the wrong picture is a lie in a charity's
 * publication.
 */
export function heal(siteRoot, { dryRun = false } = {}) {
  const assetsRoot = join(siteRoot, ASSETS_DIR);
  const foldIndex = indexByFoldFamily(assetsRoot);
  const docs = textFiles(siteRoot);

  const referenced = new Set();
  const textByFile = new Map();
  for (const abs of docs) {
    let text;
    try {
      text = readFileSync(abs, 'utf8');
    } catch {
      continue;
    }
    if (!text.includes(ASSETS_DIR)) continue;
    textByFile.set(abs, text);
    for (const name of referencesIn(text)) referenced.add(name);
  }

  const resolved = new Map();
  const unresolved = [];
  for (const name of [...referenced].sort()) {
    if (existsSync(join(assetsRoot, ...name.split('/')))) continue;
    const pick = candidateNames(name, foldIndex).find((c) =>
      existsSync(join(assetsRoot, ...c.split('/'))),
    );
    if (pick) resolved.set(name, pick);
    else unresolved.push(name);
  }

  let filesChanged = 0;
  let refsRewritten = 0;
  if (resolved.size) {
    for (const [abs, before] of textByFile) {
      let after = before;
      for (const [from, to] of resolved) {
        for (const spec of referenceRes(from)) {
          if (!after.includes(spec.from)) continue;
          const target = spec.escaped
            ? `${ASSETS_DIR}/${to}`.split('/').join('\\/')
            : `${ASSETS_DIR}/${to}`;
          after = after.replace(spec.re, () => {
            refsRewritten++;
            return target;
          });
        }
      }
      if (after !== before) {
        filesChanged++;
        if (!dryRun) writeFileSync(abs, after);
      }
    }
  }

  return {
    referenced: referenced.size,
    missing: resolved.size + unresolved.length,
    resolved,
    unresolved,
    filesChanged,
    refsRewritten,
  };
}

function report(result, { dryRun }) {
  const verb = dryRun ? 'would repoint' : 'repointed';
  if (!result.missing) {
    console.error(
      `[heal] ${result.referenced} asset reference(s) checked; every one resolves to a file in the capture.`,
    );
    return;
  }
  console.error(
    `[heal] ${result.missing} of ${result.referenced} asset reference(s) name a file the capture does not have.`,
  );
  if (result.resolved.size) {
    console.error(
      `[heal] ${verb} ${result.refsRewritten} reference(s) across ${result.filesChanged} file(s):`,
    );
    for (const [from, to] of result.resolved)
      console.error(`[heal]   ${from}\n[heal]     -> ${to}`);
  }
  if (result.unresolved.length) {
    // Named in full rather than counted. An operator cannot act on "3
    // unresolved"; the name says whether it is a stray thumbnail or the
    // charity's masthead, and it is the only record of what shipped broken.
    console.error(
      `[heal] ${result.unresolved.length} reference(s) could NOT be resolved from the capture and were left untouched.` +
        ' The self-containment gate decides whether any of them is reachable by a visitor:',
    );
    for (const name of result.unresolved) console.error(`[heal]   ${name}`);
  }
}

// ---------------------------------------------------------------- self-test

function selfTest() {
  let failed = 0;
  const eq = (label, actual, expected) => {
    const a = JSON.stringify(actual);
    const b = JSON.stringify(expected);
    if (a === b) {
      console.log(`PASS ${label}`);
    } else {
      failed++;
      console.log(`FAIL ${label}\n  expected ${b}\n  actual   ${a}`);
    }
  };

  eq(
    'stripQueryFold removes a folded query',
    stripQueryFold('a/b/photo__fit-1280.jpg'),
    'a/b/photo.jpg',
  );
  eq('stripQueryFold returns null without a fold', stripQueryFold('a/b/photo.jpg'), null);
  eq('stripQueryFold refuses a stem that is only a fold', stripQueryFold('a/__x.png'), null);
  eq('asWebp renames the extension', asWebp('a/photo.jpeg'), 'a/photo.webp');
  eq('asWebp leaves a webp alone', asWebp('a/photo.webp'), null);
  eq('asWebp leaves a dotted directory alone', asWebp('a/v1.2/photo.png'), 'a/v1.2/photo.webp');
  eq(
    'dropCdnHost strips a CDN host',
    dropCdnHost('i0.wp.com/pub.x.org/u/a.jpg'),
    'pub.x.org/u/a.jpg',
  );
  eq('dropCdnHost leaves a plain directory alone', dropCdnHost('x.org/uploads/a.jpg'), null);

  eq(
    'referencesIn finds a plain reference',
    [...referencesIn('<img src="/_ffc-assets/x.org/a.png">')],
    ['x.org/a.png'],
  );
  eq(
    'referencesIn finds a JSON-escaped reference',
    [...referencesIn('{"u":"\\/_ffc-assets\\/x.org\\/a.png"}')],
    ['x.org/a.png'],
  );

  const root = mkdtempSync(join(tmpdir(), 'heal-'));
  try {
    const site = join(root, 'site');
    const assets = join(site, ASSETS_DIR);
    const write = (rel, body) => {
      const abs = join(site, ...rel.split('/'));
      mkdirSync(join(abs, '..'), { recursive: true });
      writeFileSync(abs, body);
    };
    const asset = (rel, body) => write(`${ASSETS_DIR}/${rel}`, body);
    const read = (rel) => readFileSync(join(site, ...rel.split('/')), 'utf8');

    // Present: the reference that must be left exactly alone.
    asset('x.org/present.png', 'PRESENT');
    // A folded reference whose base name is on disk.
    asset('x.org/photo.jpg', 'PHOTO');
    // A re-encoded image: only the .webp survives.
    asset('x.org/poster.webp', 'POSTER');
    // Only a different fold of this upload survives.
    asset('x.org/banner__fit-640.jpg', 'BANNER-640');
    // The Photon copy is gone; the origin copy remains.
    asset('pub.x.org/u/books.jpeg', 'BOOKS');
    // Prefix-safety neighbour. It must share the prefix of the name being
    // REPLACED, not of the replacement: only a missing name is ever a
    // rewrite target, so a `.bak` beside the canonical file is at no risk
    // and would let a dropped lookahead pass unnoticed. This one sits
    // beside the folded name that IS being rewritten.
    asset('x.org/photo__fit-1280-2C858.jpg.bak', 'BAK');

    write(
      'index.html',
      [
        '<img src="/_ffc-assets/x.org/present.png">',
        '<img src="/_ffc-assets/x.org/photo__fit-1280-2C858.jpg">',
        '<img src="/_ffc-assets/x.org/poster__ver-2.png">',
        '<img src="/_ffc-assets/x.org/banner__fit-1280.jpg">',
        '<img src="/_ffc-assets/i0.wp.com/pub.x.org/u/books__fit-1280-2C858-ssl-1.jpeg">',
        '<img src="/_ffc-assets/x.org/photo__fit-1280-2C858.jpg.bak">',
        '<img src="/_ffc-assets/x.org/vanished.png">',
      ].join('\n'),
    );
    write(
      'escaped.html',
      '<script>{"u":"\\/_ffc-assets\\/x.org\\/photo__fit-1280-2C858.jpg"}</script>',
    );
    write('wp-capture-report.json', '{"asset":"_ffc-assets/x.org/photo__fit-1280-2C858.jpg"}');
    const binary = join(site, 'logo.png');
    writeFileSync(binary, Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x00, 0xff]));

    const dry = heal(site, { dryRun: true });
    // The closing quote is load-bearing. The prefix-safety neighbour above is
    // `photo__fit-1280-2C858.jpg.bak`, so a bare substring check for the folded
    // name stays true even after the reference it is watching has been
    // rewritten -- the assertion would pass whether or not the dry run wrote.
    eq(
      'a dry run writes nothing',
      read('index.html').includes('photo__fit-1280-2C858.jpg">'),
      true,
    );
    eq('a dry run still reports what it would change', dry.filesChanged > 0, true);

    const result = heal(site);
    const html = read('index.html');

    eq(
      'an existing reference is left alone',
      html.includes('/_ffc-assets/x.org/present.png'),
      true,
    );
    eq(
      'a folded reference falls back to its base file',
      html.includes('/_ffc-assets/x.org/photo.jpg"'),
      true,
    );
    eq(
      'a stale extension falls back to the re-encoded webp',
      html.includes('/_ffc-assets/x.org/poster.webp'),
      true,
    );
    eq(
      'a missing fold falls back to another fold of the same upload',
      html.includes('/_ffc-assets/x.org/banner__fit-640.jpg'),
      true,
    );
    eq(
      'a missing CDN copy falls back to the origin copy',
      html.includes('/_ffc-assets/pub.x.org/u/books.jpeg'),
      true,
    );
    eq(
      'the escaped spelling is repointed too',
      read('escaped.html').includes('\\/_ffc-assets\\/x.org\\/photo.jpg'),
      true,
    );
    eq(
      'the escaped spelling stays escaped',
      read('escaped.html').includes('/_ffc-assets/x.org/photo.jpg'),
      false,
    );
    eq(
      'a prefix-sharing neighbour is not rewritten',
      html.includes('/_ffc-assets/x.org/photo__fit-1280-2C858.jpg.bak'),
      true,
    );
    eq(
      'an unresolvable reference is left exactly as it was',
      html.includes('/_ffc-assets/x.org/vanished.png'),
      true,
    );
    eq('...and is reported by name', result.unresolved, ['x.org/vanished.png']);
    eq(
      'the capture report is not rewritten',
      read('wp-capture-report.json').includes('photo__fit-1280-2C858.jpg'),
      true,
    );
    eq('a binary file is untouched', readFileSync(binary).length, 6);
    eq(
      'nothing is ever deleted',
      existsSync(join(assets, 'x.org', 'photo__fit-1280-2C858.jpg.bak')),
      true,
    );

    const again = heal(site);
    eq('a second run is a no-op', again.refsRewritten, 0);
    eq('...and still reports the unresolved one', again.unresolved, ['x.org/vanished.png']);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }

  console.log(failed ? `\n${failed} self-test(s) failed` : '\nAll self-tests passed');
  return failed === 0;
}

// ---------------------------------------------------------------------- cli

function main(argv) {
  if (argv.includes('--self-test')) return selfTest() ? 0 : 1;
  const siteIdx = argv.indexOf('--site');
  if (siteIdx === -1 || !argv[siteIdx + 1]) {
    console.error('usage: heal-missing-asset-refs.mjs --site <siteRoot> [--dry-run]');
    return 2;
  }
  const site = argv[siteIdx + 1];
  if (!existsSync(site)) {
    console.error(`[heal] --site ${site} does not exist`);
    return 2;
  }
  const dryRun = argv.includes('--dry-run');
  report(heal(site, { dryRun }), { dryRun });
  return 0;
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? '').href) {
  process.exit(main(process.argv.slice(2)));
}
