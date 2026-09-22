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
 * Read-only against the network. The only files it writes are text files under
 * the --scan roots, which default to --site but need not be inside it: the
 * integrated repo keeps its assets under public/ and its routes under src/.
 *
 * Usage:
 *   node scripts/heal-missing-asset-refs.mjs --site <assetsParent> [--scan <dir>]... [--dry-run]
 *
 * `--site` is the directory that CONTAINS `_ffc-assets`; `--scan` (repeatable)
 * names where to look for references, defaulting to `--site` itself. They are
 * the same directory for a capture and different ones for the integrated repo,
 * whose assets live under `public/` and whose routes live under `src/`.
 *   node scripts/heal-missing-asset-refs.mjs --self-test
 */
import {
  readdirSync,
  lstatSync,
  statSync,
  existsSync,
  readFileSync,
  writeFileSync,
  mkdirSync,
  mkdtempSync,
  rmSync,
} from 'node:fs';
import { join, dirname, resolve, relative, sep, extname, basename } from 'node:path';
import { tmpdir } from 'node:os';
import { pathToFileURL } from 'node:url';

const ASSETS_DIR = '_ffc-assets';

/**
 * Extensions whose contents can carry an asset reference.
 *
 * Same allowlist as `dedupe-capture-assets.mjs`, and for the same reason: a
 * binary cannot carry one, and rewriting a binary would corrupt it.
 */
const TEXT_EXT = new Set([
  '.html',
  '.htm',
  '.css',
  '.js',
  '.mjs',
  '.svg',
  '.xml',
  '.json',
  '.txt',
  // The conversion writes captured pages out as Next.js routes, so a reference
  // can arrive in the published tree having never existed as a literal in the
  // capture. Run 68 measured exactly that: the heal pass found and repaired 23
  // broken references in the capture and the gate still 404'd on one the pass
  // had never seen.
  '.tsx',
  '.ts',
  '.jsx',
]);

/**
 * Directories never walked, whatever root is given.
 *
 * `--scan` can now be pointed at a Next.js checkout rather than a capture, and
 * walking `node_modules` there would be slow and — since this pass REWRITES
 * what it walks — genuinely dangerous. `out`/`.next` are build output: rewriting
 * them changes nothing that survives the next build, and would make the pass
 * look effective while the source it was meant to fix stayed broken.
 */
const SKIP_DIRS = new Set(['.git', 'node_modules', '.next', 'out', '.vercel']);

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
      if (st.isDirectory()) {
        if (!SKIP_DIRS.has(e.name)) visit(abs);
      } else if (st.isFile()) out.push(abs);
    }
  };
  visit(root);
  return out.sort();
}

/** Text files under any of `roots` whose contents may reference an asset. */
function textFiles(roots) {
  const seen = new Set();
  for (const root of roots) {
    for (const abs of walk(root)) {
      if (TEXT_EXT.has(extname(abs).toLowerCase()) && !isCaptureReport(abs)) seen.add(abs);
    }
  }
  return [...seen].sort();
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

/**
 * A reference whose every segment is an ordinary name.
 *
 * The character class above admits `.` and `-`, so it also admits `..` as a
 * whole segment. `join(assetsRoot, ...name.split('/'))` would then normalise
 * the traversal away and probe a path OUTSIDE the capture's asset directory —
 * and a candidate resolved that way would be written back into a charity's
 * markup as a URL the browser normalises in turn.
 *
 * Skipping such a reference costs nothing, because there is nothing correct to
 * do with it: `capture-wordpress-api.mjs` refuses to WRITE outside the assets
 * dir (`isContainedPath`), so no file this pass is allowed to repoint at could
 * ever satisfy one. Reported by Copilot on #1355.
 */
export function isSafeReferenceName(name) {
  if (!name) return false;
  return name.split('/').every((seg) => seg !== '' && seg !== '.' && seg !== '..');
}

/** Distinct asset names referenced by `text`, traversal attempts excluded. */
export function referencesIn(text) {
  const names = new Set();
  for (const m of text.matchAll(REFERENCE_RE)) {
    const name = referenceName(m[1]);
    if (isSafeReferenceName(name)) names.add(name);
  }
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
 * A reference written RELATIVE to the document that carries it.
 *
 * This is the blind spot the path-based scanner above has by construction, and
 * it is not hypothetical: run 70 of workflow 706 failed its self-containment
 * gate on one image, and the gate's own diagnostic found the reference inside
 * `_ffc-assets/.../elementor/css/post-6271__ver-....css`, written relatively.
 * A browser resolves `url(../../../i0.wp.com/.../photo__fit-1280.jpeg)`
 * against the STYLESHEET's location and asks for `/_ffc-assets/i0.wp.com/...`;
 * the text contains no `_ffc-assets` anywhere, so `referencesIn` reads that
 * document as containing no references at all. WordPress page builders emit
 * these by the hundred, so the blind spot is a property of every capture, not
 * of this one site.
 *
 * The lookbehind excludes `/`, which is what keeps this from re-matching the
 * tail of a path the first pass already owns: in `/_ffc-assets/a/b/x__f.jpg`
 * every candidate start is preceded by a slash.
 *
 * `__` is required in the stem. That is the capture's own query-string fold
 * marker, so the token is one this pipeline created and cannot collide with an
 * author's prose. Without it this would match any word with a dot in it.
 */
export const RELATIVE_REF_RE =
  /(?<![A-Za-z0-9._~%/-])((?:\.\.?\/)*(?:[A-Za-z0-9._~%-]+\/)*)([A-Za-z0-9._~%-]*__[A-Za-z0-9._~%-]*\.[A-Za-z0-9]{2,5})/g;

/** Is `abs` inside `root`? */
export function isInside(root, abs) {
  const rel = relative(root, abs);
  return rel !== '' && !rel.startsWith('..') && !rel.startsWith(`..${sep}`);
}

/**
 * The member of `name`'s fold family that is actually in `dir`, if any.
 *
 * SAME directory, deliberately. The repair rewrites only the basename inside a
 * relative reference, leaving the path it is relative to untouched -- which is
 * the whole reason it is safe on a site served from a project Pages subpath,
 * where rewriting to an absolute `/_ffc-assets/...` would break every one of
 * them. A candidate from some other directory would not be what the rewritten
 * reference resolves to, so it is not a candidate at all.
 */
export function siblingInDir(dir, name) {
  const family = stripQueryFold(name) ?? name;
  let entries;
  try {
    entries = readdirSync(dir, { withFileTypes: true });
  } catch {
    return null;
  }
  return (
    entries
      .filter((e) => e.isFile() && e.name !== name && (stripQueryFold(e.name) ?? e.name) === family)
      .map((e) => e.name)
      // Shortest first, then lexicographic -- the same rule `indexByFoldFamily`
      // and the dedupe pass use, so every pass prefers the same member and a
      // re-run is a no-op rather than a reshuffle.
      .sort((a, b) => a.length - b.length || (a < b ? -1 : a > b ? 1 : 0))[0] ?? null
  );
}

/**
 * Relative references in one document that name a file which is not there.
 *
 * Resolution is real, not textual: the token is resolved against the
 * document's own directory and the result must land inside the assets tree.
 * A token that escapes it is ignored rather than guessed at, and a token whose
 * target exists is left alone.
 */
export function relativeFixesFor(assetsRoot, fileAbs, text) {
  const out = [];
  const seen = new Set();
  const from = dirname(fileAbs);
  for (const m of text.matchAll(RELATIVE_REF_RE)) {
    const [whole, prefix, name] = m;
    if (seen.has(whole)) continue;
    seen.add(whole);
    const target = resolve(from, `${prefix}${name}`);
    if (!isInside(assetsRoot, target)) continue;
    if (existsSync(target)) continue;
    out.push({ whole, prefix, name, target, to: siblingInDir(dirname(target), name) });
  }
  return out;
}

/** A regex matching `token` as a whole reference, never as part of a longer one. */
export function relativeRefRe(token) {
  const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return new RegExp(`(?<![A-Za-z0-9._~%/-])${escapeRe(token)}(?![A-Za-z0-9._~%-])`, 'g');
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
export function heal(siteRoot, { dryRun = false, scanRoots } = {}) {
  const assetsRoot = join(siteRoot, ASSETS_DIR);
  const foldIndex = indexByFoldFamily(assetsRoot);
  // The tree holding the assets and the trees holding the references are the
  // same directory for a capture and DIFFERENT ones for an integrated repo,
  // where the assets are under `public/` and the routes under `src/`.
  const docs = textFiles(scanRoots?.length ? scanRoots : [siteRoot]);

  const referenced = new Set();
  const textByFile = new Map();
  for (const abs of docs) {
    let text;
    try {
      text = readFileSync(abs, 'utf8');
    } catch {
      continue;
    }
    // Every document is kept, including ones with no `_ffc-assets` in them at
    // all. That string used to be the filter, and it is exactly the condition
    // a RELATIVE reference fails -- so the documents most likely to carry one
    // were the documents this pass refused to look at.
    textByFile.set(abs, text);
    if (!text.includes(ASSETS_DIR)) continue;
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
  const relativeResolved = new Map();
  const relativeUnresolved = [];

  for (const [abs, before] of textByFile) {
    let after = before;

    if (resolved.size && after.includes(ASSETS_DIR)) {
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
    }

    // Every document, not only the ones inside the assets tree. Restricting it
    // to those was the obvious narrowing and it was inert: mutation review
    // deleted the restriction and not one assertion changed, because the guard
    // that actually decides anything is the one below -- the token has to
    // RESOLVE to a path inside the assets tree. A guard no test can distinguish
    // from its own absence is not protection, it is decoration.
    {
      for (const fix of relativeFixesFor(assetsRoot, abs, after)) {
        const where = relative(assetsRoot, fix.target).split(sep).join('/');
        if (!fix.to) {
          if (!relativeUnresolved.includes(where)) relativeUnresolved.push(where);
          continue;
        }
        relativeResolved.set(where, fix.to);
        after = after.replace(relativeRefRe(fix.whole), () => {
          refsRewritten++;
          return `${fix.prefix}${fix.to}`;
        });
      }
    }

    if (after !== before) {
      filesChanged++;
      if (!dryRun) writeFileSync(abs, after);
    }
  }

  return {
    referenced: referenced.size,
    missing: resolved.size + unresolved.length,
    resolved,
    unresolved,
    relativeResolved,
    relativeUnresolved,
    filesChanged,
    refsRewritten,
  };
}

function report(result, { dryRun }) {
  const verb = dryRun ? 'would repoint' : 'repointed';
  const relFound = result.relativeResolved.size + result.relativeUnresolved.length;

  if (!result.missing && !relFound) {
    console.error(
      `[heal] ${result.referenced} asset reference(s) checked; every one resolves to a file in the capture.`,
    );
    return;
  }

  if (result.missing) {
    console.error(
      `[heal] ${result.missing} of ${result.referenced} asset reference(s) name a file the capture does not have.`,
    );
  }
  if (relFound) {
    // Counted apart from the figure above, and named as relative, because the
    // two are found by different means. A relative reference carries no
    // `_ffc-assets` path, so it is absent from the `referenced` total entirely
    // -- reporting them together would imply a denominator that never included
    // them.
    console.error(
      `[heal] ${relFound} further reference(s) are written RELATIVE to the document carrying them,` +
        ' where no _ffc-assets path appears and the scan above is blind by construction.',
    );
  }
  if (result.filesChanged) {
    console.error(
      `[heal] ${verb} ${result.refsRewritten} reference(s) across ${result.filesChanged} file(s).`,
    );
  }
  if (result.resolved.size) {
    for (const [from, to] of result.resolved)
      console.error(`[heal]   ${from}\n[heal]     -> ${to}`);
  }
  if (result.relativeResolved.size) {
    for (const [from, to] of result.relativeResolved)
      console.error(`[heal]   (relative) ${from}\n[heal]     -> ${to}`);
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
  if (result.relativeUnresolved.length) {
    console.error(
      `[heal] ${result.relativeUnresolved.length} RELATIVE reference(s) had no sibling in their own directory` +
        ' and were left untouched:',
    );
    for (const name of result.relativeUnresolved) console.error(`[heal]   ${name}`);
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
  eq(
    'referencesIn drops a reference that traverses out of the assets dir',
    [...referencesIn('<img src="/_ffc-assets/../../etc/passwd">')],
    [],
  );
  eq(
    'referencesIn drops a dot segment too',
    [...referencesIn('<img src="/_ffc-assets/x.org/./a.png">')],
    [],
  );
  eq(
    'referencesIn keeps a dotted DIRECTORY, which is an ordinary capture name',
    [...referencesIn('<img src="/_ffc-assets/x.org/v1.2/a.png">')],
    ['x.org/v1.2/a.png'],
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

    // The integrated-repo shape: assets under public/, references under src/,
    // and a node_modules that must never be walked. This is the arrangement
    // run 68 proved the capture-only scan cannot see.
    const repo = join(root, 'repo');
    mkdirSync(join(repo, 'public', ASSETS_DIR, 'x.org'), { recursive: true });
    writeFileSync(join(repo, 'public', ASSETS_DIR, 'x.org', 'hero.jpg'), 'HERO');
    mkdirSync(join(repo, 'src', 'app'), { recursive: true });
    writeFileSync(
      join(repo, 'src', 'app', 'page.tsx'),
      'export default () => <img src="/_ffc-assets/x.org/hero__fit-1280.jpg" />;',
    );
    // INSIDE a scanned root, deliberately. A node_modules beside the scanned
    // directories is skipped because it is out of range, not because the guard
    // works -- a fixture placed there passes with SKIP_DIRS deleted, which is
    // exactly the inert-fixture mistake the mutation run caught twice before.
    mkdirSync(join(repo, 'src', 'node_modules', 'pkg'), { recursive: true });
    writeFileSync(
      join(repo, 'src', 'node_modules', 'pkg', 'index.js'),
      'const u = "/_ffc-assets/x.org/hero__fit-1280.jpg";',
    );

    const integrated = heal(join(repo, 'public'), {
      scanRoots: [join(repo, 'public'), join(repo, 'src')],
    });
    eq(
      'a reference in a generated .tsx route is healed against public/_ffc-assets',
      readFileSync(join(repo, 'src', 'app', 'page.tsx'), 'utf8').includes(
        '/_ffc-assets/x.org/hero.jpg"',
      ),
      true,
    );
    eq('...and it is counted, not silently skipped', integrated.refsRewritten, 1);
    eq(
      'node_modules is never walked, let alone rewritten',
      readFileSync(join(repo, 'src', 'node_modules', 'pkg', 'index.js'), 'utf8').includes(
        'hero__fit-1280.jpg',
      ),
      true,
    );
    eq('...and still reports the unresolved one', again.unresolved, ['x.org/vanished.png']);

    // The run-70 shape, reproduced exactly: an Elementor stylesheet INSIDE the
    // assets tree referencing an upload RELATIVELY, across hosts, at a fold the
    // capture never fetched. No `_ffc-assets` appears anywhere in the file, so
    // the pass above reads it as containing nothing at all.
    const cap = join(root, 'relcap');
    const relAssets = join(cap, ASSETS_DIR);
    const uploads = join(relAssets, 'i0.wp.com', 'pub.example.org', 'wp-content', 'uploads');
    mkdirSync(join(uploads, '2024', '06'), { recursive: true });
    writeFileSync(join(uploads, '2024', '06', 'photo__fit-1024-2C858-ssl-1.jpeg'), 'A');
    writeFileSync(join(uploads, '2024', '06', 'photo__resize-550-2C536-ssl-1.jpeg'), 'B');
    writeFileSync(join(uploads, '2024', '06', 'kept__fit-100-ssl-1.png'), 'K');
    const cssDir = join(relAssets, 'pub.example.org', 'wp-content', 'uploads', 'elementor', 'css');
    mkdirSync(cssDir, { recursive: true });
    const up = '../../../../../';
    const toUploads = `${up}i0.wp.com/pub.example.org/wp-content/uploads/2024/06/`;
    const cssPath = join(cssDir, 'post-6271__ver-1790041496.css');
    writeFileSync(
      cssPath,
      `.a{background:url(${toUploads}photo__fit-1280-2C858-ssl-1.jpeg)}` +
        `.b{background:url(${toUploads}kept__fit-100-ssl-1.png)}` +
        `.c{background:url(${toUploads}orphan__fit-9-ssl-1.gif)}` +
        `.d{background:url(${up}${up}${up}${up}etc/passwd__x-1.conf)}` +
        // No `__`, so not a name this pipeline ever wrote. Without that
        // requirement the scanner would treat any dotted word as a reference.
        `.e{background:url(notes/README.md)}`,
    );

    const relRun = heal(cap);
    const css = readFileSync(cssPath, 'utf8');

    eq(
      'a RELATIVE reference to a missing fold is repointed at its sibling',
      css.includes(`${toUploads}photo__fit-1024-2C858-ssl-1.jpeg`),
      true,
    );
    // Load-bearing, not cosmetic: these sites are served from a project Pages
    // SUBPATH, so rewriting a relative reference to an absolute
    // `/_ffc-assets/...` would break every one of them.
    eq('...and it is still RELATIVE afterwards', css.includes(`/${ASSETS_DIR}/`), false);
    eq(
      '...and the missing fold is gone from the stylesheet',
      css.includes('photo__fit-1280-2C858-ssl-1.jpeg'),
      false,
    );
    eq(
      'a RELATIVE reference whose target EXISTS is untouched',
      css.includes(`${toUploads}kept__fit-100-ssl-1.png`),
      true,
    );
    // Untouched is not enough on its own: a reference that IS considered and
    // then found unrepairable is also left in place, and reads identically in
    // the file. The report is where the two come apart.
    eq(
      '...and is not even considered',
      relRun.relativeUnresolved.some((n) => n.includes('kept__')),
      false,
    );
    eq(
      'a RELATIVE reference with no sibling is left exactly as it was',
      css.includes(`${toUploads}orphan__fit-9-ssl-1.gif`),
      true,
    );
    eq('...and is reported by name', relRun.relativeUnresolved, [
      'i0.wp.com/pub.example.org/wp-content/uploads/2024/06/orphan__fit-9-ssl-1.gif',
    ]);
    // Resolution is real, so a token that climbs out of the assets tree is
    // ignored rather than guessed at.
    eq(
      'a RELATIVE token that escapes the assets tree is ignored',
      css.includes('etc/passwd__x-1.conf'),
      true,
    );
    eq(
      '...and is not reported as an unrepairable asset either',
      relRun.relativeUnresolved.some((n) => n.includes('passwd')),
      false,
    );
    eq(
      'a token with no capture fold marker is not a reference at all',
      relRun.relativeUnresolved.some((n) => n.includes('README')),
      false,
    );
    eq('the relative repair is counted, not silently applied', relRun.refsRewritten, 1);
    eq(
      '...and reported as relative, apart from the path-based total',
      [...relRun.relativeResolved.values()],
      ['photo__fit-1024-2C858-ssl-1.jpeg'],
    );
    eq('the path-based scan saw nothing here', relRun.referenced, 0);

    const relAgain = heal(cap);
    eq('a second relative run is a no-op', relAgain.refsRewritten, 0);

    // An ABSOLUTE reference, in a document inside the assets tree. The first
    // pass owns it; the relative pass must not also match its tail, which is
    // what the scanner's lookbehind is for. Without that lookbehind the tail
    // resolves against the wrong base and is reported as an unrepairable
    // asset that does not exist.
    const absRoot = join(root, 'relabs');
    const absAssets = join(absRoot, ASSETS_DIR);
    mkdirSync(join(absAssets, 'x.org'), { recursive: true });
    writeFileSync(join(absAssets, 'x.org', 'banner__w-100-ssl-1.png'), 'P');
    writeFileSync(
      join(absAssets, 'abs.css'),
      `.g{background:url(/${ASSETS_DIR}/x.org/banner__fit-900-2C300-ssl-1.png)}`,
    );
    const absRun = heal(absRoot);
    eq(
      'an ABSOLUTE reference is repaired by the path pass',
      readFileSync(join(absAssets, 'abs.css'), 'utf8').includes(
        `/${ASSETS_DIR}/x.org/banner__w-100-ssl-1.png`,
      ),
      true,
    );
    eq(
      '...and the relative pass does not also claim it',
      absRun.relativeUnresolved.length + absRun.relativeResolved.size,
      0,
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }

  console.log(failed ? `\n${failed} self-test(s) failed` : '\nAll self-tests passed');
  return failed === 0;
}

// ---------------------------------------------------------------------- cli

const USAGE =
  'usage: heal-missing-asset-refs.mjs --site <assetsParent> [--scan <dir>]... [--dry-run]';

/**
 * The value that follows a flag, or `null` when the flag has none.
 *
 * A flag at the end of `argv`, or one followed by another flag, has no value.
 * The caller REJECTS both spellings rather than skipping them, and that is the
 * point: a `--scan` that quietly did not take produces a pass that read fewer
 * files and still exits 0, reporting a clean tree for routes it never opened.
 * That is the reassuring-direction failure this whole script exists to stop,
 * so it must not be reachable from the script's own argument parsing.
 */
function flagValue(argv, i) {
  const value = argv[i + 1];
  return value === undefined || value.startsWith('--') ? null : value;
}

/** True if `value` is a directory this pass can actually walk. */
function usableRoot(flag, value) {
  if (!existsSync(value)) {
    console.error(`[heal] ${flag} ${value} does not exist`);
    return false;
  }
  // stat, not lstat: `walk` readdir's the root itself, which follows a symlink,
  // so a symlinked directory is a usable root. A FILE is not -- it satisfies
  // existsSync, walks to nothing, and yields an empty scan that exits 0.
  if (!statSync(value).isDirectory()) {
    console.error(`[heal] ${flag} ${value} is not a directory`);
    return false;
  }
  return true;
}

function main(argv) {
  if (argv.includes('--self-test')) return selfTest() ? 0 : 1;

  let site = null;
  const scanRoots = [];
  for (let i = 0; i < argv.length; i++) {
    const flag = argv[i];
    if (flag !== '--site' && flag !== '--scan') continue;
    const value = flagValue(argv, i);
    if (value === null) {
      console.error(`[heal] ${flag} requires a directory`);
      console.error(USAGE);
      return 2;
    }
    if (!usableRoot(flag, value)) return 2;
    if (flag === '--site') site = value;
    else scanRoots.push(value);
    i++; // the value is consumed; it is not a flag in its own right
  }
  if (site === null) {
    console.error(`[heal] --site is required`);
    console.error(USAGE);
    return 2;
  }

  const dryRun = argv.includes('--dry-run');
  report(heal(site, { dryRun, scanRoots }), { dryRun });
  return 0;
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? '').href) {
  process.exit(main(process.argv.slice(2)));
}
