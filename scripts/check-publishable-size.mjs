#!/usr/bin/env node
/**
 * Refuse a converted site that a push or GitHub Pages would reject.
 *
 * WHY THIS EXISTS, precisely. On 2026-09-21 a three-host capture of
 * newheightseducation.org crawled for 40 minutes, passed every per-host
 * completeness gate, converted cleanly, passed the self-containment gate at
 * 120/120 pages, and then died at `git push`:
 *
 *   remote: error: File public/_ffc-assets/publications.newheightseducation.org/
 *   wp-content/uploads/2026/05/NHEG-May-June-2026-5.pdf is 167.22 MB;
 *   this exceeds GitHub's file size limit of 100.00 MB
 *   ! [remote rejected] wp-convert/... (pre-receive hook declined)
 *
 * Everything upstream asked "is this tree a coherent site?" and nothing asked
 * "can this tree be pushed?". The cost of that gap is the whole crawl plus a
 * human approval, both spent before the answer arrives. This script asks the
 * second question, in the `convert` job, where the answer is free.
 *
 * TWO LIMITS, AND THEY ARE NOT THE SAME KIND OF LIMIT.
 *
 *   * The per-file limit is a HARD git rejection. GitHub's pre-receive hook
 *     refuses the push outright and no repository setting changes it.
 *   * The total-size limit is GitHub Pages' published-site limit. A site over
 *     it is a deployment problem rather than a push problem, so it is reported
 *     separately and can be demoted to a warning with --total-warn-only, for
 *     the case where the operator has already decided to host the bulk
 *     elsewhere and wants the push to proceed meanwhile.
 *
 * Both are reported with FILENAMES rather than counts. An operator who is told
 * "3 files are too large" has to re-run a 40-minute crawl to learn which three.
 */

import { readdirSync, statSync } from 'node:fs';
import { join, relative, sep } from 'node:path';
import { pathToFileURL } from 'node:url';

/** GitHub refuses any file larger than this at the push. Not configurable there. */
export const GITHUB_MAX_FILE_BYTES = 100 * 1024 * 1024;

/** GitHub Pages' documented published-site limit. */
export const PAGES_MAX_SITE_BYTES = 1024 * 1024 * 1024;

/** Directories never shipped to the target repo, so never counted. */
const SKIP_DIRS = new Set(['.git', 'node_modules', '.next', '.github']);

/**
 * Every file under `dir`, as `{ path, bytes }` with POSIX-separated paths.
 *
 * Separators are normalized because the output is compared against, and
 * printed beside, the paths a `git push` rejection names -- which are always
 * POSIX. A Windows-separated path in that message reads as a different file.
 */
export function walkWithSizes(dir, root = dir, out = []) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.isDirectory()) {
      if (SKIP_DIRS.has(entry.name)) continue;
      walkWithSizes(join(dir, entry.name), root, out);
    } else if (entry.isFile()) {
      const full = join(dir, entry.name);
      out.push({ path: relative(root, full).split(sep).join('/'), bytes: statSync(full).size });
    }
  }
  return out;
}

/**
 * Files at or over the limit, largest first.
 *
 * `>` and not `>=`: GitHub's message reads "exceeds ... 100.00 MB", and a file
 * of exactly the limit is accepted. Using `>=` here would refuse a tree the
 * push would have taken, which is the more embarrassing direction to be wrong
 * in -- it blocks correct work rather than merely failing late.
 */
export function oversizedFiles(files, maxFileBytes = GITHUB_MAX_FILE_BYTES) {
  return files.filter((f) => f.bytes > maxFileBytes).sort((a, b) => b.bytes - a.bytes);
}

/** Total bytes across every file. */
export function totalBytes(files) {
  return files.reduce((n, f) => n + f.bytes, 0);
}

/** The heaviest `n` files, for the report when a total is over budget. */
export function heaviestFiles(files, n = 10) {
  return [...files].sort((a, b) => b.bytes - a.bytes).slice(0, n);
}

export const mb = (n) => (n / 1048576).toFixed(1);

/**
 * The whole verdict, as data.
 *
 * Returned rather than printed so it is testable without capturing stdout,
 * and so the caller decides which findings are fatal.
 */
export function assessTree(files, { maxFileBytes, maxTotalBytes, totalWarnOnly = false } = {}) {
  const maxFile = maxFileBytes ?? GITHUB_MAX_FILE_BYTES;
  const maxTotal = maxTotalBytes ?? PAGES_MAX_SITE_BYTES;
  const oversized = oversizedFiles(files, maxFile);
  const total = totalBytes(files);
  const overTotal = total > maxTotal;
  return {
    fileCount: files.length,
    total,
    maxFile,
    maxTotal,
    oversized,
    overTotal,
    // A total over budget is fatal unless the operator has said otherwise; an
    // oversized file always is, because no flag here can make git accept it.
    ok: oversized.length === 0 && (!overTotal || totalWarnOnly),
  };
}

function usage(msg) {
  console.error(`check-publishable-size: ${msg}`);
  console.error(
    'usage: check-publishable-size.mjs <dir> [--max-file-mb N] [--max-total-mb N] [--total-warn-only]',
  );
  return 2;
}

function main(argv) {
  if (argv.includes('--self-test')) return selfTest();
  const positional = argv.filter((a) => !a.startsWith('--'));
  if (positional.length !== 1) return usage('expected exactly one directory');
  const dir = positional[0];
  try {
    if (!statSync(dir).isDirectory()) return usage(`${dir} is not a directory`);
  } catch {
    return usage(`${dir} does not exist`);
  }

  const num = (name, fallback) => {
    const hit = argv.find((a) => a.startsWith(`--${name}=`));
    if (!hit) return fallback;
    const v = Number(hit.split('=')[1]);
    return Number.isFinite(v) && v > 0 ? v * 1048576 : null;
  };
  const maxFileBytes = num('max-file-mb', GITHUB_MAX_FILE_BYTES);
  const maxTotalBytes = num('max-total-mb', PAGES_MAX_SITE_BYTES);
  if (maxFileBytes === null || maxTotalBytes === null)
    return usage('--max-file-mb / --max-total-mb expect a positive number of MB');

  const files = walkWithSizes(dir);
  const v = assessTree(files, {
    maxFileBytes,
    maxTotalBytes,
    totalWarnOnly: argv.includes('--total-warn-only'),
  });

  console.log(`${v.fileCount} files, ${mb(v.total)} MB total`);

  if (v.oversized.length) {
    console.error(
      `::error::${v.oversized.length} file(s) exceed GitHub's ${mb(v.maxFile)} MB per-file limit.` +
        ' A git push of this tree WILL be rejected by the pre-receive hook.',
    );
    for (const f of v.oversized) console.error(`  ${mb(f.bytes).padStart(8)} MB  ${f.path}`);
  }

  if (v.overTotal) {
    const how = v.ok ? '::warning::' : '::error::';
    console.error(
      `${how}the tree is ${mb(v.total)} MB, over the ${mb(v.maxTotal)} MB GitHub Pages` +
        ' publishes per site. The heaviest files:',
    );
    for (const f of heaviestFiles(files))
      console.error(`  ${mb(f.bytes).padStart(8)} MB  ${f.path}`);
  }

  if (v.ok && !v.overTotal) console.log('within both the per-file and total budgets');
  return v.ok ? 0 : 1;
}

function selfTest() {
  let fails = 0;
  const eq = (name, got, want) => {
    const g = JSON.stringify(got);
    const w = JSON.stringify(want);
    if (g === w) console.log(`ok   ${name}`);
    else {
      console.error(`FAIL ${name}\n  got  ${g}\n  want ${w}`);
      fails += 1;
    }
  };

  const MB = 1048576;
  const files = [
    { path: 'a.pdf', bytes: 167 * MB },
    { path: 'b.pdf', bytes: 116 * MB },
    { path: 'c.html', bytes: 1000 },
    { path: 'd.pdf', bytes: 90 * MB },
  ];

  eq(
    'oversizedFiles names the offenders, largest first',
    oversizedFiles(files).map((f) => f.path),
    ['a.pdf', 'b.pdf'],
  );
  // The real rejection message reads "exceeds ... 100.00 MB", so a file of
  // exactly the limit is fine. Refusing it would block a tree git accepts.
  eq(
    'a file of exactly the limit is not oversized',
    oversizedFiles([{ path: 'x', bytes: GITHUB_MAX_FILE_BYTES }]).length,
    0,
  );
  eq(
    'one byte over the limit is oversized',
    oversizedFiles([{ path: 'x', bytes: GITHUB_MAX_FILE_BYTES + 1 }]).length,
    1,
  );
  eq('totalBytes sums every file', totalBytes(files), 167 * MB + 116 * MB + 1000 + 90 * MB);
  eq('an empty tree totals zero', totalBytes([]), 0);

  const bad = assessTree(files);
  eq('a tree with an oversized file is not ok', bad.ok, false);
  eq('...and it names them', bad.oversized.length, 2);

  const good = assessTree([{ path: 'a', bytes: 10 * MB }]);
  eq('a small tree is ok', good.ok, true);
  eq('...and is not over the total', good.overTotal, false);

  // The two limits are independent: a tree can be over one and under the other.
  const manySmall = Array.from({ length: 2000 }, (_, i) => ({ path: `f${i}`, bytes: MB }));
  const overTotalOnly = assessTree(manySmall);
  eq('2000 x 1 MB has no oversized FILE', overTotalOnly.oversized.length, 0);
  eq('...but is over the Pages total', overTotalOnly.overTotal, true);
  eq('...and is therefore not ok by default', overTotalOnly.ok, false);
  eq(
    '--total-warn-only demotes the total, and ONLY the total',
    assessTree(manySmall, { totalWarnOnly: true }).ok,
    true,
  );
  eq(
    '--total-warn-only can NOT rescue an oversized file, because nothing can',
    assessTree(files, { totalWarnOnly: true }).ok,
    false,
  );

  eq(
    'heaviestFiles reports the worst offenders for a total overrun',
    heaviestFiles(files, 2).map((f) => f.path),
    ['a.pdf', 'b.pdf'],
  );
  eq('heaviestFiles does not mutate its input', files[0].path, 'a.pdf');

  eq('the per-file default is GitHub’s hard limit', GITHUB_MAX_FILE_BYTES, 100 * MB);
  eq('the total default is the Pages limit', PAGES_MAX_SITE_BYTES, 1024 * MB);

  console.log(fails ? `\n${fails} self-test(s) FAILED` : '\nall self-tests passed');
  return fails ? 1 : 0;
}

const isMain = process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href;
if (isMain) process.exit(main(process.argv.slice(2)));
