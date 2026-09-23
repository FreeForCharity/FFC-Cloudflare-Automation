#!/usr/bin/env node
/**
 * dedupe-capture-assets.mjs — collapse byte-identical assets in a captured site.
 *
 * A capture keys its asset files on the URL it fetched them from, so one file
 * served at two URLs lands on disk twice. The commonest source is a query
 * string: `capture-wordpress-api.mjs` folds `?1` into the FILENAME (see the
 * `__${q}` suffix there), so `Characters.mp4` and `Characters.mp4?1` become
 * `Characters.mp4` and `Characters__1.mp4` — two names, one file, twice the
 * bytes. WordPress media libraries also genuinely hold the same image under
 * several names.
 *
 * That is not a cosmetic waste. Measured on newheightseducation.org (run
 * 35671915958) the captured tree came to 1139.3 MB against the 1024.0 MB
 * GitHub Pages will publish, and two duplicate PAIRS in the ten heaviest files
 * alone accounted for 85.9 MB of the 115.3 MB overage.
 *
 * This pass runs over the capture tree rather than inside the capture, which
 * buys two things. It catches duplicates whatever produced them, not only the
 * query-string ones; and it can be re-run against a capture artifact from an
 * earlier run, so fixing the size of a five-hour crawl costs minutes and puts
 * no further load on the charity's server.
 *
 * ORDER MATTERS, and it is rewrite -> verify -> delete, never delete first.
 * If the verification finds a reference that was not rewritten, nothing has
 * been removed yet: every reference points at a canonical file that exists,
 * the duplicates are merely unused, and the tree is still publishable. Deleting
 * first and rewriting after would turn the same bug into 404s in a charity's
 * live site.
 *
 * Read-only against the network; only rewrites and removes files under --site.
 *
 * Usage:
 *   node scripts/dedupe-capture-assets.mjs --site <siteRoot> [--dry-run]
 *   node scripts/dedupe-capture-assets.mjs --self-test
 */
import {
  readdirSync,
  lstatSync,
  readFileSync,
  writeFileSync,
  rmSync,
  mkdirSync,
  mkdtempSync,
  openSync,
  readSync,
  closeSync,
} from 'node:fs';
import { join, relative, sep, extname, basename, dirname } from 'node:path';
import { createHash } from 'node:crypto';
import { tmpdir } from 'node:os';
import { pathToFileURL } from 'node:url';

const ASSETS_DIR = '_ffc-assets';

/**
 * Extensions whose contents can carry an asset reference.
 *
 * A binary cannot, and rewriting one would corrupt it, so the list is an
 * allowlist rather than a denylist of the formats we happen to have seen.
 */
const TEXT_EXT = new Set(['.html', '.htm', '.css', '.js', '.mjs', '.svg', '.xml', '.json', '.txt']);

/**
 * The capture's own report is a RECORD of what the capture did, not a set of
 * live references. Rewriting it would falsify the record, and a record naming
 * a since-deduplicated file is not a broken link — so it is excluded from both
 * the rewrite and the verification.
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
      // could hash or rewrite something outside the capture.
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

/** The name an asset is referenced by: its path under `_ffc-assets`, POSIX-style. */
export function assetName(assetsRoot, abs) {
  return relative(assetsRoot, abs).split(sep).join('/');
}

function escapeRe(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/** How much of a file is held in memory at once while hashing it. */
export const HASH_CHUNK = 1024 * 1024;

/**
 * sha256 of a file's bytes, read in fixed-size chunks.
 *
 * `readFileSync` would be one line shorter and is the wrong tool here: this
 * pass exists BECAUSE the captures it runs on carry very large media. The tree
 * that motivated it held a 98 MB video, two 54.9 MB videos and four PDFs over
 * 100 MB before downsampling, and reading one of those whole just to hash it is
 * an avoidable peak on a hosted runner. One reusable buffer covers every file.
 *
 * `subarray(0, n)` is not decoration. The final read of any file almost never
 * fills the buffer, and hashing the whole buffer would mix in whatever the
 * PREVIOUS file left in the tail — so two identical files would hash
 * differently depending on what preceded them, and the pass would silently
 * stop finding duplicates. That failure points the reassuring way: fewer
 * duplicates found looks like a clean capture.
 */
export function hashFile(abs, buf = Buffer.allocUnsafe(HASH_CHUNK)) {
  const h = createHash('sha256');
  const fd = openSync(abs, 'r');
  try {
    let n;
    while ((n = readSync(fd, buf, 0, buf.length, null)) > 0) h.update(buf.subarray(0, n));
  } finally {
    closeSync(fd);
  }
  return h.digest('hex');
}

/**
 * A reference to `name`, and nothing that merely starts with it.
 *
 * Without the lookahead, deleting `logo.png` would also rewrite the unrelated
 * `logo.png.bak` and `logo.png2` — the characters excluded are exactly those
 * that could continue a path or filename.
 */
export function referenceRe(name) {
  return new RegExp(`${escapeRe(`${ASSETS_DIR}/${name}`)}(?![A-Za-z0-9._~%/-])`, 'g');
}

/**
 * Which of a set of identical files keeps its name.
 *
 * Shortest first, then lexicographic. Both halves are deliberate: the shortest
 * is the one without the `__<query>` suffix the capture appended, i.e. the name
 * the site's own markup is most likely to use; the lexicographic tie-break
 * makes the choice deterministic, so two runs over the same capture produce the
 * same tree and a re-run is a no-op rather than a reshuffle.
 */
export function chooseCanonical(names) {
  return [...names].sort((a, b) => a.length - b.length || (a < b ? -1 : a > b ? 1 : 0))[0];
}

/**
 * Groups of byte-identical assets, largest saving first.
 *
 * Files are grouped by SIZE before anything is hashed. A file whose size is
 * unique cannot have a twin, so on a 6,800-file capture this reads a few dozen
 * files instead of all of them.
 *
 * Zero-byte files are skipped: collapsing them saves nothing, and an empty
 * asset is usually a failed fetch rather than real content worth rewiring.
 */
export function findDuplicateGroups(assetsRoot) {
  const bySize = new Map();
  for (const abs of walk(assetsRoot)) {
    const size = lstatSync(abs).size;
    if (size === 0) continue;
    if (!bySize.has(size)) bySize.set(size, []);
    bySize.get(size).push(abs);
  }

  const byHash = new Map();
  const buf = Buffer.allocUnsafe(HASH_CHUNK);
  for (const [size, list] of bySize) {
    if (list.length < 2) continue;
    for (const abs of list) {
      const key = `${size}:${hashFile(abs, buf)}`;
      if (!byHash.has(key)) byHash.set(key, { size, paths: [] });
      byHash.get(key).paths.push(abs);
    }
  }

  const groups = [];
  for (const { size, paths } of byHash.values()) {
    if (paths.length < 2) continue;
    const names = paths.map((p) => assetName(assetsRoot, p));
    const canonical = chooseCanonical(names);
    groups.push({
      canonical,
      duplicates: names.filter((n) => n !== canonical).sort(),
      size,
      bytesSaved: size * (paths.length - 1),
    });
  }
  return groups.sort((a, b) => b.bytesSaved - a.bytesSaved || (a.canonical < b.canonical ? -1 : 1));
}

/** Text files whose contents may reference an asset. */
function textFiles(siteRoot) {
  return walk(siteRoot).filter(
    (abs) => TEXT_EXT.has(extname(abs).toLowerCase()) && !isCaptureReport(abs),
  );
}

/**
 * Point every reference to a duplicate at its canonical file.
 *
 * The `includes` guard before the regex is not micro-optimization: a capture is
 * hundreds of documents wide and the replacement set is bounded by the number
 * of duplicates, so skipping the files that cannot match keeps this a scan
 * rather than a cross product.
 */
export function rewriteReferences(siteRoot, groups, { dryRun = false } = {}) {
  const replacements = [];
  for (const g of groups) {
    for (const dup of g.duplicates) {
      replacements.push({
        needle: `${ASSETS_DIR}/${dup}`,
        re: referenceRe(dup),
        to: `${ASSETS_DIR}/${g.canonical}`,
      });
    }
  }

  let filesChanged = 0;
  let refsRewritten = 0;
  for (const abs of textFiles(siteRoot)) {
    const before = readFileSync(abs, 'utf8');
    let after = before;
    for (const r of replacements) {
      if (!after.includes(r.needle)) continue;
      after = after.replace(r.re, () => {
        refsRewritten++;
        return r.to;
      });
    }
    if (after !== before) {
      filesChanged++;
      if (!dryRun) writeFileSync(abs, after);
    }
  }
  return { filesChanged, refsRewritten };
}

/**
 * References to a duplicate that survived the rewrite.
 *
 * This runs BEFORE anything is deleted. A non-empty result means the rewrite
 * has a blind spot — an encoding this pass does not recognise, say — and the
 * correct response is to stop with the tree intact, not to delete the files
 * those references point at.
 */
export function findStaleReferences(siteRoot, groups) {
  const stale = [];
  const needles = [];
  for (const g of groups) {
    for (const dup of g.duplicates) {
      needles.push({ name: dup, needle: `${ASSETS_DIR}/${dup}`, re: referenceRe(dup) });
    }
  }
  for (const abs of textFiles(siteRoot)) {
    const text = readFileSync(abs, 'utf8');
    for (const n of needles) {
      if (!text.includes(n.needle)) continue;
      n.re.lastIndex = 0;
      if (n.re.test(text)) stale.push({ file: relative(siteRoot, abs), name: n.name });
    }
  }
  return stale;
}

function fmtMb(bytes) {
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function dedupe(siteRoot, { dryRun = false } = {}) {
  const assetsRoot = join(siteRoot, ASSETS_DIR);
  let assetsExist = true;
  try {
    assetsExist = lstatSync(assetsRoot).isDirectory();
  } catch {
    assetsExist = false;
  }
  if (!assetsExist) {
    return { groups: [], bytesSaved: 0, filesRemoved: 0, filesChanged: 0, refsRewritten: 0 };
  }

  const groups = findDuplicateGroups(assetsRoot);
  if (groups.length === 0) {
    return { groups, bytesSaved: 0, filesRemoved: 0, filesChanged: 0, refsRewritten: 0 };
  }

  const { filesChanged, refsRewritten } = rewriteReferences(siteRoot, groups, { dryRun });

  const stale = findStaleReferences(siteRoot, groups);
  if (stale.length) {
    return { groups, stale, bytesSaved: 0, filesRemoved: 0, filesChanged, refsRewritten };
  }

  let filesRemoved = 0;
  let bytesSaved = 0;
  for (const g of groups) {
    for (const dup of g.duplicates) {
      const abs = join(assetsRoot, ...dup.split('/'));
      if (!dryRun) rmSync(abs, { force: true });
      filesRemoved++;
      bytesSaved += g.size;
    }
  }
  return { groups, bytesSaved, filesRemoved, filesChanged, refsRewritten };
}

function selfTest() {
  let failed = 0;
  const eq = (name, actual, expected) => {
    const a = JSON.stringify(actual);
    const b = JSON.stringify(expected);
    if (a === b) {
      console.log(`ok   ${name}`);
    } else {
      failed++;
      console.error(`FAIL ${name}\n  expected ${b}\n  actual   ${a}`);
    }
  };

  eq(
    'chooseCanonical prefers the name without the query suffix',
    chooseCanonical(['h/a/Characters__1.mp4', 'h/a/Characters.mp4']),
    'h/a/Characters.mp4',
  );
  eq(
    'chooseCanonical is deterministic when lengths tie',
    chooseCanonical(['h/b.png', 'h/a.png']),
    'h/a.png',
  );

  eq(
    'referenceRe does not match a longer name sharing the prefix',
    referenceRe('h/logo.png').test('../_ffc-assets/h/logo.png.bak'),
    false,
  );
  eq(
    'referenceRe matches a real reference',
    referenceRe('h/logo.png').test('../_ffc-assets/h/logo.png"'),
    true,
  );

  const root = mkdtempSync(join(tmpdir(), 'dedupe-selftest-'));
  const write = (rel, body) => {
    const abs = join(root, ...rel.split('/'));
    mkdirSync(dirname(abs), { recursive: true });
    writeFileSync(abs, body);
    return abs;
  };

  // Hashing is chunked, so a file larger than one chunk is the case that can
  // silently diverge. Compared against a whole-buffer digest of the same bytes
  // rather than a hardcoded hex string, so this still holds if the chunk size
  // changes. The tail is deliberately NOT a chunk multiple: a full final read
  // would not exercise the `subarray(0, n)` that keeps the previous file's
  // bytes out of the digest.
  const spanning = Buffer.concat([Buffer.alloc(HASH_CHUNK, 0x61), Buffer.alloc(777, 0x62)]);
  const spanningPath = write(`${ASSETS_DIR}/h/up/spanning.bin`, spanning);
  eq(
    'hashFile agrees with a whole-buffer digest across a chunk boundary',
    hashFile(spanningPath),
    createHash('sha256').update(spanning).digest('hex'),
  );

  const video = 'VIDEOBYTES'.repeat(50);
  write(`${ASSETS_DIR}/h/up/Characters.mp4`, video);
  write(`${ASSETS_DIR}/h/up/Characters__1.mp4`, video);
  // Same SIZE as the pair, different bytes: must survive.
  write(`${ASSETS_DIR}/h/up/Different.mp4`, 'OTHERBYTES'.repeat(50));
  // Empty files are left alone even though they are byte-identical.
  write(`${ASSETS_DIR}/h/up/empty-a.txt`, '');
  write(`${ASSETS_DIR}/h/up/empty-b.txt`, '');
  // A name that merely starts with a DUPLICATE's name must not be rewritten.
  // It has to share the duplicate's prefix, not the canonical's: only duplicates
  // are ever replacement targets, so a `Characters.mp4.bak` here would be at no
  // risk at all and this case would pass with the guard removed. Mutation
  // testing caught exactly that -- the first version of this fixture asserted
  // something that could not fail.
  write(`${ASSETS_DIR}/h/up/Characters__1.mp4.bak`, 'unrelated');
  write(
    'index.html',
    '<video src="./_ffc-assets/h/up/Characters__1.mp4"></video>' +
      '<a href="./_ffc-assets/h/up/Characters__1.mp4.bak">bak</a>',
  );
  write(
    'deep/page/index.html',
    '<video src="../../_ffc-assets/h/up/Characters__1.mp4"></video>' +
      '<video src="../../_ffc-assets/h/up/Different.mp4"></video>',
  );
  write('wp-capture-report.json', JSON.stringify({ wrote: '_ffc-assets/h/up/Characters__1.mp4' }));

  const dry = dedupe(root, { dryRun: true });
  eq('dry run finds the one duplicate pair', dry.groups.length, 1);
  eq('dry run rewrites both referencing pages', dry.filesChanged, 2);
  eq(
    'dry run leaves the duplicate on disk',
    lstatSync(join(root, ASSETS_DIR, 'h/up/Characters__1.mp4')).isFile(),
    true,
  );

  const res = dedupe(root);
  eq('one duplicate group', res.groups.length, 1);
  eq('canonical is the unsuffixed name', res.groups[0].canonical, 'h/up/Characters.mp4');
  eq('one file removed', res.filesRemoved, 1);
  eq('bytes saved equals the file size', res.bytesSaved, video.length);
  eq('two references rewritten', res.refsRewritten, 2);
  eq('no stale references', res.stale, undefined);

  const gone = (() => {
    try {
      lstatSync(join(root, ASSETS_DIR, 'h/up/Characters__1.mp4'));
      return false;
    } catch {
      return true;
    }
  })();
  eq('the duplicate file is gone', gone, true);
  eq(
    'the canonical file survives',
    lstatSync(join(root, ASSETS_DIR, 'h/up/Characters.mp4')).isFile(),
    true,
  );
  eq(
    'a same-size different-content file survives',
    lstatSync(join(root, ASSETS_DIR, 'h/up/Different.mp4')).isFile(),
    true,
  );
  eq(
    'empty files are left alone',
    lstatSync(join(root, ASSETS_DIR, 'h/up/empty-a.txt')).isFile() &&
      lstatSync(join(root, ASSETS_DIR, 'h/up/empty-b.txt')).isFile(),
    true,
  );
  eq(
    'the reference now points at the canonical file, and a longer name sharing the duplicate prefix is untouched',
    readFileSync(join(root, 'index.html'), 'utf8'),
    '<video src="./_ffc-assets/h/up/Characters.mp4"></video>' +
      '<a href="./_ffc-assets/h/up/Characters__1.mp4.bak">bak</a>',
  );
  eq(
    'a relative prefix of any depth is rewritten',
    readFileSync(join(root, 'deep/page/index.html'), 'utf8').includes(
      '../../_ffc-assets/h/up/Characters.mp4"',
    ),
    true,
  );
  eq(
    "the capture's own report is left untouched",
    JSON.parse(readFileSync(join(root, 'wp-capture-report.json'), 'utf8')).wrote,
    '_ffc-assets/h/up/Characters__1.mp4',
  );

  // Re-running must be a no-op, or the step is not safe to repeat.
  const again = dedupe(root);
  eq('a second run finds nothing to do', again.filesRemoved, 0);

  rmSync(root, { recursive: true, force: true });

  if (failed) {
    console.error(`\n${failed} self-test failure(s)`);
    process.exit(1);
  }
  console.log('\nall self-tests passed');
}

const isMain = process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href;

function arg(name, def = '') {
  const i = process.argv.indexOf(`--${name}`);
  return i > -1 && process.argv[i + 1] && !process.argv[i + 1].startsWith('--')
    ? process.argv[i + 1]
    : def;
}

if (isMain) {
  if (process.argv.includes('--self-test')) {
    selfTest();
  } else {
    const site = arg('site');
    const dryRun = process.argv.includes('--dry-run');
    if (!site) {
      console.error('Usage: node scripts/dedupe-capture-assets.mjs --site <siteRoot> [--dry-run]');
      process.exit(64);
    }

    const res = dedupe(site, { dryRun });

    if (res.stale && res.stale.length) {
      console.error(
        `::error::${res.stale.length} reference(s) to a duplicate asset could not be rewritten, so nothing was deleted.` +
          ' The tree is unchanged and still publishable; this is a bug in the rewrite, not in the capture.',
      );
      for (const s of res.stale.slice(0, 10)) console.error(`    ${s.file} -> ${s.name}`);
      process.exit(1);
    }

    if (res.groups.length === 0) {
      console.log('[dedupe] no byte-identical assets found; nothing to collapse.');
    } else {
      console.log(
        `[dedupe] ${res.filesRemoved} duplicate asset(s) collapsed into ${res.groups.length} file(s):` +
          ` ${fmtMb(res.bytesSaved)} saved${dryRun ? ' (dry run, nothing written)' : ''}.`,
      );
      console.log(
        `[dedupe] ${res.refsRewritten} reference(s) repointed across ${res.filesChanged} file(s).`,
      );
      for (const g of res.groups.slice(0, 10)) {
        console.log(
          `    ${fmtMb(g.bytesSaved).padStart(9)}  ${g.canonical}  <- ${g.duplicates.join(', ')}`,
        );
      }
    }
  }
}
