#!/usr/bin/env node
/**
 * Shrink the heavy assets of a capture that is ALREADY ON DISK.
 *
 * WHY THIS EXISTS. `--recode-all-images` and `--shrink-all-pdfs` are flags on
 * `capture-wordpress-api.mjs`, applied while each asset is downloaded. 706
 * skips that step entirely when `reuse_capture_from_run` is set
 * (`if: needs.resolve.outputs.reuse_run == ''`), so on a reused capture both
 * flags are silently no-ops — they read as enabled in the run summary and do
 * nothing. That is issue #1401.
 *
 * Refusing the combination was the first answer. Measured on
 * newheightseducation.org it is the wrong one: re-crawling to get the
 * optimizations is not free and may not be possible. Run 36342303038 reused a
 * stored capture, passed every step, and failed ONLY the size gate at
 * 1169.6 MB against Pages' 1024 MB. The re-crawl that would have carried the
 * optimizations (run 36357186683) ran 2h46m and died at the completeness
 * floor — 330 of 352 entries, REST 500s on pages and posts, 217 assets
 * failing with HTTP 0 — because the origin is a charity's shared-hosting
 * WordPress that had been crawled three times that day. The stored capture
 * was complete; the origin was no longer able to give us another.
 *
 * So the fix is to make the optimizations available WITHOUT the origin.
 *
 * WHAT IT DOES NOT DO, and why that is the whole design. It never renames.
 * The capture's WebP pass renames `x.jpg` to `x.webp` and rewrites every
 * reference as part of mapping source URLs to local names — machinery that
 * only exists while the URL→local map is being built. Re-running it over a
 * tree that is already localized would mean a second, different rewrite
 * (local path → local path) across markup, srcset, inline CSS and `.css`
 * files, and every one of those is a chance to strand a reference.
 *
 * Both passes here keep the filename byte-for-byte, so there is nothing to
 * rewrite and nothing to strand:
 *
 *   PDF   Ghostscript /ebook, exactly as the capture's ladder does.
 *   JPEG  mozjpeg re-encode at the same quality, SAME extension. Measured on
 *         the 3,888 captured JPEGs: 175.8 -> 88.0 MB at q80 (50%), with the
 *         twelve heaviest differing from their originals by 0.10% of pixels
 *         on average and 0.28% at worst, dimensions unchanged.
 *
 * A file that does not come back smaller is left exactly as it was.
 */
import {
  readFileSync,
  writeFileSync,
  readdirSync,
  statSync,
  mkdtempSync,
  rmSync,
  existsSync,
} from 'node:fs';
import { join, resolve, extname } from 'node:path';
import { tmpdir } from 'node:os';
import { execFile } from 'node:child_process';
import { fileURLToPath } from 'node:url';

/** Ghostscript rung. Same profile the capture ladder reaches for first. */
export const PDF_PROFILE = '/ebook';

/** Default JPEG quality: the WebP ladder's first rung, for consistency. */
export const DEFAULT_JPEG_QUALITY = 85;

export const RECODABLE_JPEG = /\.jpe?g$/i;
export const IS_PDF = /\.pdf$/i;

/**
 * Keep a re-encode only if it is strictly smaller.
 *
 * No minimum-saving floor, deliberately, and the reasoning is the one
 * `worthShrinking` already records: a floor exists to justify a RENAME, and
 * the renamed file's stranded-reference risk is what it pays for. Nothing
 * here renames, so a 2% win is simply a 2% win.
 */
export function worthKeeping(originalBytes, encodedBytes) {
  if (!Number.isFinite(originalBytes) || !Number.isFinite(encodedBytes)) return false;
  if (originalBytes <= 0 || encodedBytes <= 0) return false;
  return encodedBytes < originalBytes;
}

/**
 * Whether a requested pass can actually run, given what is installed.
 *
 * A pass that was ASKED FOR and cannot run is an error, not a warning. The
 * first version of this script logged "sharp is not installed" and exited 0,
 * and the local end-to-end proved why that is wrong: 3,888 JPEGs shipped
 * untouched while the run reported "saved 108.8 MB" and succeeded. Downstream
 * that surfaces as a size gate failing by roughly the amount the missing pass
 * would have saved -- a number that makes no sense against the flags the
 * operator set, two steps away from the step that actually gave up.
 *
 * An encoder that is absent when nobody asked for its pass is not a problem,
 * which is why this takes the request and not just the availability.
 */
export function missingTooling({ jpegs, pdfs, haveSharp, haveGhostscript }) {
  const missing = [];
  if (jpegs && !haveSharp) missing.push('--recode-jpegs needs sharp, which failed to load');
  if (pdfs && !haveGhostscript)
    missing.push('--shrink-pdfs needs ghostscript (gs), which is not installed');
  return missing;
}

/** Every file under `dir`, recursively. */
export function walkFiles(dir, out = []) {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name);
    if (e.isDirectory()) walkFiles(p, out);
    else if (e.isFile()) out.push(p);
  }
  return out;
}

/** Which of `files` each pass should consider. */
export function selectTargets(files, { jpegs, pdfs }) {
  return {
    jpegs: jpegs ? files.filter((f) => RECODABLE_JPEG.test(f)) : [],
    pdfs: pdfs ? files.filter((f) => IS_PDF.test(f)) : [],
  };
}

/**
 * Downsample one PDF with Ghostscript.
 *
 * Shelling out for the same reason the capture does: there is no usable
 * pure-JS downsampler, and `gs` is present on `ubuntu-latest`. A missing
 * binary and a PDF that defeats Ghostscript need opposite responses -- stop
 * trying at all, versus skip this one file -- and ENOENT is what separates
 * them.
 */
export async function shrinkPdf(buf, state) {
  if (state.gsMissing) return null;
  const dir = mkdtempSync(join(tmpdir(), 'ffc-optpdf-'));
  const src = join(dir, 'in.pdf');
  const dest = join(dir, 'out.pdf');
  try {
    writeFileSync(src, buf);
    await new Promise((res, rej) => {
      execFile(
        'gs',
        [
          '-q',
          '-dNOPAUSE',
          '-dBATCH',
          '-dSAFER',
          '-sDEVICE=pdfwrite',
          `-dPDFSETTINGS=${PDF_PROFILE}`,
          '-dDetectDuplicateImages=true',
          '-o',
          dest,
          src,
        ],
        { maxBuffer: 1 << 20 },
        (err) => (err ? rej(err) : res()),
      );
    });
    return existsSync(dest) ? readFileSync(dest) : null;
  } catch (err) {
    // Preflight already refused a run whose gs is missing, so an ENOENT here
    // means it vanished mid-run; either way this file is not downsampled.
    if (err && err.code === 'ENOENT') state.gsMissing = true;
    return null;
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

function arg(name, def) {
  const i = process.argv.indexOf(`--${name}`);
  return i !== -1 && process.argv[i + 1] ? process.argv[i + 1] : def;
}
const flag = (name) => process.argv.includes(`--${name}`);

async function main() {
  const dir = arg('dir', '');
  if (!dir) {
    console.error(
      'Usage:\n' +
        '  --dir <capture dir> [--recode-jpegs] [--shrink-pdfs]\n' +
        '      [--jpeg-quality 85]\n' +
        '  --self-test',
    );
    return 2;
  }
  if (!existsSync(dir)) {
    console.error(`::error::no such directory: ${dir}`);
    return 1;
  }
  const doJpegs = flag('recode-jpegs');
  const doPdfs = flag('shrink-pdfs');
  if (!doJpegs && !doPdfs) {
    console.log('[optimize] neither pass requested; nothing to do.');
    return 0;
  }
  const quality = Number(arg('jpeg-quality', String(DEFAULT_JPEG_QUALITY)));
  if (!Number.isInteger(quality) || quality < 1 || quality > 100) {
    console.error(
      `::error::--jpeg-quality must be an integer 1..100 (got ${arg('jpeg-quality', '')})`,
    );
    return 1;
  }

  const files = walkFiles(resolve(dir));
  const targets = selectTargets(files, { jpegs: doJpegs, pdfs: doPdfs });
  const state = { gsMissing: false };
  const tally = { jpegKept: 0, jpegDeclined: 0, jpegFailed: 0, pdfKept: 0, pdfDeclined: 0 };
  let before = 0;
  let after = 0;

  let sharp = null;
  if (doJpegs) {
    try {
      ({ default: sharp } = await import('sharp'));
    } catch {
      /* reported by the preflight below, with the others */
    }
  }
  let haveGhostscript = true;
  if (doPdfs) {
    haveGhostscript = await new Promise((res) => execFile('gs', ['--version'], (err) => res(!err)));
  }
  // Fail before touching a single file, so a run that cannot do what was
  // asked stops here rather than producing a half-optimized tree.
  const missing = missingTooling({
    jpegs: doJpegs,
    pdfs: doPdfs,
    haveSharp: Boolean(sharp),
    haveGhostscript,
  });
  if (missing.length) {
    for (const m of missing) console.error(`::error::${m}`);
    return 1;
  }

  for (const f of targets.jpegs) {
    const buf = readFileSync(f);
    before += buf.length;
    try {
      const out = await sharp(buf).jpeg({ quality, mozjpeg: true }).toBuffer();
      if (worthKeeping(buf.length, out.length)) {
        writeFileSync(f, out);
        after += out.length;
        tally.jpegKept += 1;
      } else {
        after += buf.length;
        tally.jpegDeclined += 1;
      }
    } catch {
      after += buf.length;
      tally.jpegFailed += 1;
    }
  }

  for (const f of targets.pdfs) {
    const buf = readFileSync(f);
    before += buf.length;
    const out = await shrinkPdf(buf, state);
    if (out && worthKeeping(buf.length, out.length)) {
      writeFileSync(f, out);
      after += out.length;
      tally.pdfKept += 1;
    } else {
      after += buf.length;
      tally.pdfDeclined += 1;
    }
  }

  const mb = (n) => (n / 1048576).toFixed(1);
  if (doJpegs)
    console.log(
      `[optimize] jpeg q${quality}: ${tally.jpegKept} re-encoded, ` +
        `${tally.jpegDeclined} left as captured, ${tally.jpegFailed} could not be read`,
    );
  if (doPdfs)
    console.log(
      `[optimize] pdf ${PDF_PROFILE}: ${tally.pdfKept} downsampled, ` +
        `${tally.pdfDeclined} left as captured`,
    );
  console.log(
    `[optimize] ${mb(before)} MB -> ${mb(after)} MB (saved ${mb(before - after)} MB). ` +
      'Every file kept its name, so no reference was rewritten.',
  );
  return 0;
}

/* ---------------------------------------------------------------------
 * Self-test — `node scripts/optimize-captured-assets.mjs --self-test`
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

  // --- worthKeeping: strictly smaller, no floor -------------------------
  eq('a smaller encode is kept', worthKeeping(1000, 900), true);
  eq('a LARGER encode is refused', worthKeeping(1000, 1100), false);
  eq('an identical encode is refused', worthKeeping(1000, 1000), false);
  // No floor, unlike the renaming WebP pass: nothing here renames, so there
  // is no stranded-reference risk for a floor to pay for.
  eq('a 2% win is kept, because nothing is renamed to pay for', worthKeeping(1000, 980), true);
  eq('a zero-byte encode is refused', worthKeeping(1000, 0), false);
  eq('a NaN is refused rather than throwing', worthKeeping(NaN, 10), false);

  // --- target selection -------------------------------------------------
  const files = ['a/x.jpg', 'a/y.JPEG', 'b/z.png', 'b/w.webp', 'c/d.pdf', 'c/e.PDF', 'f/g.mp4'];
  eq(
    'jpeg selection is case-insensitive and takes .jpeg too',
    selectTargets(files, { jpegs: true, pdfs: false }).jpegs,
    ['a/x.jpg', 'a/y.JPEG'],
  );
  eq('pdf selection is case-insensitive', selectTargets(files, { jpegs: false, pdfs: true }).pdfs, [
    'c/d.pdf',
    'c/e.PDF',
  ]);
  // PNG and WebP are deliberately NOT candidates: shrinking a PNG means
  // re-encoding it to WebP, which RENAMES, which is the one thing this pass
  // exists to avoid. They stay with the capture-time pass.
  eq(
    'png and webp are not candidates, because shrinking those means renaming',
    selectTargets(files, { jpegs: true, pdfs: true }),
    { jpegs: ['a/x.jpg', 'a/y.JPEG'], pdfs: ['c/d.pdf', 'c/e.PDF'] },
  );
  eq(
    'a pass that was not requested selects nothing',
    selectTargets(files, { jpegs: false, pdfs: false }),
    { jpegs: [], pdfs: [] },
  );
  eq('mp4 is never a candidate', selectTargets(['f/g.mp4'], { jpegs: true, pdfs: true }), {
    jpegs: [],
    pdfs: [],
  });

  // --- preflight: a requested pass that cannot run is an ERROR -----------
  eq(
    'asking for jpegs without sharp is refused',
    missingTooling({ jpegs: true, pdfs: false, haveSharp: false, haveGhostscript: true }).length,
    1,
  );
  eq(
    'asking for pdfs without ghostscript is refused',
    missingTooling({ jpegs: false, pdfs: true, haveSharp: true, haveGhostscript: false }).length,
    1,
  );
  eq(
    'both missing is reported as both, not just the first',
    missingTooling({ jpegs: true, pdfs: true, haveSharp: false, haveGhostscript: false }).length,
    2,
  );
  // Absence only matters for a pass that was asked for.
  eq(
    'a missing encoder for a pass nobody requested is fine',
    missingTooling({ jpegs: false, pdfs: false, haveSharp: false, haveGhostscript: false }),
    [],
  );
  eq(
    'everything present is fine',
    missingTooling({ jpegs: true, pdfs: true, haveSharp: true, haveGhostscript: true }),
    [],
  );

  console.log(failures ? `\n${failures} self-test(s) failed` : '\nall self-tests passed');
  return failures ? 1 : 0;
}

if (process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url))) {
  if (process.argv.includes('--self-test')) process.exit(selfTest());
  main().then((code) => process.exit(code));
}
