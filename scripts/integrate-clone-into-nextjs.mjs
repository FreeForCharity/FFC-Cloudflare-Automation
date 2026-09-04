#!/usr/bin/env node
/**
 * integrate-clone-into-nextjs.mjs — wire a static site clone (produced by
 * clone-site-static.mjs) into an FFC-EX Next.js repo so that `next build`
 * (output: 'export') serves the exact cloned WordPress visuals.
 *
 * The FFC-EX repos are Next.js apps with `output: 'export'`: at build time Next
 * renders the routes under src/app AND copies everything in public/ verbatim
 * into out/. We exploit the second half: drop the clone into public/ so the
 * export ships the faithful clone. Next refuses to build when a public/ file and
 * an app route resolve to the same path (e.g. public/contact/index.html vs
 * src/app/contact/page.tsx), so we move the template's page routes aside into a
 * backup folder (nothing is deleted; it stays in git history and the backup).
 *
 * The end state is a repo with **zero app routes**, which is correct and
 * supported: the site is public/, and any surviving route would both risk
 * colliding with a cloned path and export a stray page into the published site.
 *
 * Read-only against the network; only touches the given repo working tree.
 *
 * Usage:
 *   node scripts/integrate-clone-into-nextjs.mjs \
 *     --clone <cloneSiteRoot> --repo <nextRepoDir> --domain <apexDomain>
 *   node scripts/integrate-clone-into-nextjs.mjs --self-test
 */
import {
  cpSync,
  rmSync,
  mkdirSync,
  mkdtempSync,
  existsSync,
  readdirSync,
  statSync,
  renameSync,
  writeFileSync,
  readFileSync,
} from 'node:fs';
import { join, relative, dirname } from 'node:path';
import { tmpdir } from 'node:os';
import { spawnSync } from 'node:child_process';

function arg(name, def) {
  const i = process.argv.indexOf(`--${name}`);
  return i > -1 && process.argv[i + 1] ? process.argv[i + 1] : def;
}

const PAGE = /^page\.(tsx|ts|jsx|js)$/;

/**
 * Template files under public/ that must survive the wipe.
 *
 * `public/` is replaced wholesale by the clone, and only CNAME used to be
 * carried across. The first real delivery therefore shipped a PR that failed
 * the target repo's own drift check:
 *
 *   ❌ public/.well-known/security.txt is missing. Restore it from the template.
 *   ⚠️  public/_headers is missing.
 *
 * These are not template decoration. security.txt is the security-contact
 * artifact FFC requires on every charity site, and it exists at both the
 * well-known path and the root fallback the drift check looks for. Deleting it
 * during a migration silently removes a site's way of receiving vulnerability
 * reports.
 *
 * Kept as a named list rather than a filter so that what survives is legible
 * and testable, instead of being an emergent property of the copy order.
 */
export const PRESERVED_PUBLIC_FILES = [
  'CNAME',
  '_headers',
  'security.txt',
  join('.well-known', 'security.txt'),
];

/** Read the preserved files before the wipe. Missing ones are simply absent. */
export function readPreservedPublicFiles(publicDir) {
  const kept = new Map();
  for (const rel of PRESERVED_PUBLIC_FILES) {
    const p = join(publicDir, rel);
    if (existsSync(p)) kept.set(rel, readFileSync(p));
  }
  return kept;
}

/**
 * Put them back after the clone lands.
 *
 * The clone wins on a genuine collision: if the captured site shipped its own
 * file at that path it is the site's content, and overwriting it with the
 * template's would be the migration editing the charity's site.
 */
export function restorePreservedPublicFiles(publicDir, kept) {
  const restored = [];
  for (const [rel, buf] of kept) {
    const dest = join(publicDir, rel);
    if (existsSync(dest)) continue;
    mkdirSync(dirname(dest), { recursive: true });
    writeFileSync(dest, buf);
    restored.push(rel);
  }
  return restored;
}

/** httrack bookkeeping that must not become part of public/. */
function cleanCloneCruft(clone) {
  const cruft = ['hts-cache', 'backblue.gif', 'fade.gif', 'cdn-cgi', 'index.html.tmp'];
  for (const c of cruft) {
    const p = join(clone, c);
    if (existsSync(p)) rmSync(p, { recursive: true, force: true });
  }
  // httrack leaves a hash-named cache dir (16 hex chars) at the root — drop it.
  for (const e of readdirSync(clone)) {
    if (/^[0-9a-f]{16}$/.test(e) && statSync(join(clone, e)).isDirectory()) {
      rmSync(join(clone, e), { recursive: true, force: true });
    }
  }
}

/**
 * Remove the dead `_clone-host` sentinel written by earlier runs of this script.
 *
 * It existed to "guarantee at least one valid route remains so `next build`
 * succeeds", but **underscore-prefixed folders are private in the App Router**
 * and are excluded from routing entirely — so it never produced a route and
 * never provided that guarantee. Every FFC-EX build that has ever succeeded did
 * so with zero app routes, which is the decisive evidence that the guarantee was
 * never needed. Verified directly on Next 16.2.12: with the sentinel present the
 * route table is just the framework's `/404`, and with `src/app` holding only
 * `layout.tsx` the build and export still succeed.
 *
 * Left in place it is dead weight carrying a false claim about a build
 * invariant, which is worse than nothing for anyone debugging the pipeline.
 * Removing it here also cleans the repos where 702 already committed it.
 *
 * Runs before disableTemplatePages() so the sentinel is deleted outright rather
 * than filed into the backup as if it were a real template route. Earlier runs
 * did exactly that (this script recreated the sentinel after disabling pages),
 * so the backup copy is swept too. See #901.
 */
function removeDeadSentinel(appDir, backupDir) {
  const removed = [];
  for (const dir of [join(appDir, '_clone-host'), join(backupDir, '_clone-host')]) {
    if (existsSync(dir)) {
      rmSync(dir, { recursive: true, force: true });
      removed.push(dir);
    }
  }
  return removed;
}

/** Move template app page routes aside so they don't collide with the clone. */
function disableTemplatePages(appDir, backupDir) {
  const movedRoutes = [];
  function walk(dir) {
    if (!existsSync(dir)) return;
    for (const e of readdirSync(dir, { withFileTypes: true })) {
      const p = join(dir, e.name);
      if (e.isDirectory()) {
        walk(p);
      } else if (PAGE.test(e.name)) {
        const rel = relative(appDir, p);
        const dest = join(backupDir, rel);
        mkdirSync(dirname(dest), { recursive: true });
        renameSync(p, dest);
        movedRoutes.push(rel);
      }
    }
  }
  walk(appDir);
  return movedRoutes;
}

/**
 * Count surviving *routable* pages under src/app, so the report states the end
 * state as an observation rather than asserting an invariant.
 *
 * Underscore-prefixed directories are private and excluded from routing, so a
 * `page.*` inside one is not a route and must not be counted as one — the exact
 * distinction this script previously got wrong. Route groups `(name)` are not
 * skipped: unlike private folders they do route, they just don't add a segment.
 *
 * Note this is deliberately stricter than disableTemplatePages(), which moves
 * every page-shaped file aside regardless of where it sits. Disabling is a
 * conservative safety net over a backup; counting is a claim about routes, so it
 * has to mean what it says.
 */
function countRoutablePages(dir) {
  if (!existsSync(dir)) return 0;
  let n = 0;
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    if (e.isDirectory()) {
      if (e.name.startsWith('_')) continue;
      n += countRoutablePages(join(dir, e.name));
    } else if (PAGE.test(e.name)) n++;
  }
  return n;
}

/**
 * The cloned WordPress assets in public/ are not source and must not be run
 * through the repo's Prettier/ESLint (minified CSS/JS would fail). Make sure the
 * repo's ignore files exclude public/ so its CI (and pre-commit hooks) skip it.
 */
function ensurePublicIgnored(repo) {
  // Prettier still reads .prettierignore, so this half works as written.
  const ignorePath = join(repo, '.prettierignore');
  const existing = existsSync(ignorePath) ? readFileSync(ignorePath, 'utf8') : '';
  if (!existing.split(/\r?\n/).includes('public/')) {
    const sep = existing && !existing.endsWith('\n') ? '\n' : '';
    writeFileSync(
      ignorePath,
      existing + sep + '# Static WordPress clone assets (not source)\npublic/\n',
    );
  }
  ensureEslintIgnoresPublic(repo);
}

/** Flat-config files ESLint 9 actually reads, in resolution order. */
const ESLINT_FLAT_CONFIGS = ['eslint.config.mjs', 'eslint.config.js', 'eslint.config.cjs'];

/**
 * Exclude the mirror from ESLint.
 *
 * This used to write `public/` into `.eslintignore` and stop there. ESLint 9
 * flat config IGNORES that file entirely — it says so on stderr and carries on
 * linting:
 *
 *   ESLintIgnoreWarning: The ".eslintignore" file is no longer supported.
 *   Switch to using the "ignores" property in "eslint.config.js"
 *
 * So the exclusion was inert, and the first real delivery linted 1,838 mirrored
 * WordPress assets and failed on minified plugin bundles:
 *
 *   public/_ffc-assets/…/hustle-ui.min.js
 *     24:18856  error  Component definition is missing display name
 *
 * A warning nobody reads is how a "handled" case ships broken. The ignore now
 * goes where ESLint looks, and a config whose shape we cannot edit is a hard
 * error rather than another silent no-op.
 */
function ensureEslintIgnoresPublic(repo) {
  const found = ESLINT_FLAT_CONFIGS.map((n) => join(repo, n)).filter((p) => existsSync(p));
  if (!found.length) {
    // No flat config: a legacy repo that genuinely still reads .eslintignore.
    const legacy = join(repo, '.eslintignore');
    const existing = existsSync(legacy) ? readFileSync(legacy, 'utf8') : '';
    if (!existing.split(/\r?\n/).includes('public/')) {
      const sep = existing && !existing.endsWith('\n') ? '\n' : '';
      writeFileSync(
        legacy,
        existing + sep + '# Static WordPress clone assets (not source)\npublic/\n',
      );
    }
    return;
  }
  const configPath = found[0];
  const src = readFileSync(configPath, 'utf8');
  if (/["'`]public\/\*\*["'`]/.test(src)) return; // already excluded

  const anchor = /ignores:\s*\[/;
  if (!anchor.test(src)) {
    throw new Error(
      `${configPath} has no \`ignores: [\` array to extend, so the WordPress mirror under ` +
        `public/ would be linted as source. Add "public/**" to its ignores and re-run — ` +
        `refusing to write an ignore file ESLint 9 does not read.`,
    );
  }
  writeFileSync(
    configPath,
    src.replace(
      anchor,
      "ignores: [\n      // Static WordPress clone assets (not source), added by workflow 706.\n      'public/**',",
    ),
  );
}

function countFiles(d) {
  let n = 0;
  for (const e of readdirSync(d, { withFileTypes: true }))
    n += e.isDirectory() ? countFiles(join(d, e.name)) : 1;
  return n;
}

function integrate({ clone, repo, domain }) {
  cleanCloneCruft(clone);

  // Replace public/ with the clone, carrying the template's own files across.
  const publicDir = join(repo, 'public');
  const cnamePath = join(publicDir, 'CNAME');
  const keptCname = existsSync(cnamePath) ? readFileSync(cnamePath, 'utf8').trim() : '';
  const carried = readPreservedPublicFiles(publicDir);
  rmSync(publicDir, { recursive: true, force: true });
  mkdirSync(publicDir, { recursive: true });
  cpSync(clone, publicDir, { recursive: true });
  restorePreservedPublicFiles(publicDir, carried);

  const appDir = join(repo, 'src', 'app');
  const backupDir = join(repo, '_disabled_template_routes');

  const removedSentinels = removeDeadSentinel(appDir, backupDir);
  const movedRoutes = disableTemplatePages(appDir, backupDir);

  // Ensure CNAME (apex) is present for GitHub Pages.
  writeFileSync(cnamePath, (keptCname || domain) + '\n');

  ensurePublicIgnored(repo);

  return {
    domain,
    publicFiles: countFiles(publicDir),
    disabledTemplateRoutes: movedRoutes,
    removedDeadSentinels: removedSentinels.map((p) => relative(repo, p)),
    appRoutesRemaining: countRoutablePages(appDir),
    cname: keptCname || domain,
    note: 'Run `npm ci && npm run build` in the repo; the static export (out/) serves the clone.',
  };
}

/* ------------------------------------------------------------------ *
 * Self-test — must precede the usage guard so `--self-test` runs
 * without --clone/--repo/--domain.
 * ------------------------------------------------------------------ */
function selfTest() {
  let failures = 0;
  const check = (name, cond) => {
    console.log(`${cond ? 'ok  ' : 'FAIL'} ${name}`);
    if (!cond) failures++;
  };

  const root = mkdtempSync(join(tmpdir(), 'integrate-selftest-'));
  const clone = join(root, 'clone');
  const repo = join(root, 'repo');

  // A clone with a page, a nested page, and httrack cruft.
  mkdirSync(join(clone, 'contact'), { recursive: true });
  mkdirSync(join(clone, 'hts-cache'), { recursive: true });
  mkdirSync(join(clone, '0123456789abcdef'), { recursive: true });
  writeFileSync(join(clone, 'index.html'), '<html>home</html>');
  writeFileSync(join(clone, 'contact', 'index.html'), '<html>contact</html>');
  writeFileSync(join(clone, 'hts-cache', 'new.txt'), 'cruft');
  writeFileSync(join(clone, '0123456789abcdef', 'x'), 'cruft');

  // A template repo mid-pipeline: real routes, plus a `_clone-host` sentinel in
  // both places an earlier run of this script could have left one.
  const appDir = join(repo, 'src', 'app');
  mkdirSync(join(appDir, 'contact'), { recursive: true });
  mkdirSync(join(appDir, '_clone-host'), { recursive: true });
  mkdirSync(join(repo, '_disabled_template_routes', '_clone-host'), { recursive: true });
  mkdirSync(join(repo, 'public'), { recursive: true });
  writeFileSync(join(appDir, 'layout.tsx'), 'export default function L(){return null}');
  writeFileSync(join(appDir, 'page.tsx'), 'export default function P(){return null}');
  writeFileSync(join(appDir, 'contact', 'page.tsx'), 'export default function C(){return null}');
  writeFileSync(
    join(appDir, '_clone-host', 'page.tsx'),
    'export default function S(){return null}',
  );
  writeFileSync(
    join(repo, '_disabled_template_routes', '_clone-host', 'page.tsx'),
    'export default function S(){return null}',
  );
  writeFileSync(join(repo, 'public', 'CNAME'), 'kept.example\n');
  // The template files a real FFC-EX repo carries and its drift check requires.
  mkdirSync(join(repo, 'public', '.well-known'), { recursive: true });
  writeFileSync(
    join(repo, 'public', '.well-known', 'security.txt'),
    'Contact: mailto:security@freeforcharity.org\n',
  );
  writeFileSync(
    join(repo, 'public', 'security.txt'),
    'Contact: mailto:security@freeforcharity.org\n',
  );
  writeFileSync(
    join(repo, 'public', '_headers'),
    '/*\n  Content-Security-Policy: default-src self\n',
  );
  // An ESLint 9 flat config, shaped like the real template's.
  writeFileSync(
    join(repo, 'eslint.config.mjs'),
    "const eslintConfig = [\n  {\n    ignores: [\n      'node_modules/**',\n      '.next/**',\n    ],\n  },\n]\n\nexport default eslintConfig\n",
  );
  // …and a clone that ships its own file at one preserved path, to prove the
  // captured site is not overwritten by the template.
  writeFileSync(join(clone, '_headers'), '/*\n  X-From: from-the-clone\n');

  const report = integrate({ clone, repo, domain: 'fallback.example' });

  check(
    'the `_clone-host` sentinel is deleted, not filed into the backup',
    !existsSync(join(appDir, '_clone-host')) &&
      !existsSync(join(repo, '_disabled_template_routes', '_clone-host')) &&
      report.removedDeadSentinels.length === 2,
  );
  check(
    'real template routes are moved to the backup, not deleted',
    report.disabledTemplateRoutes.length === 2 &&
      existsSync(join(repo, '_disabled_template_routes', 'page.tsx')) &&
      existsSync(join(repo, '_disabled_template_routes', 'contact', 'page.tsx')),
  );
  check('no routable page survives under src/app', report.appRoutesRemaining === 0);
  check('layout.tsx is left alone', existsSync(join(appDir, 'layout.tsx')));
  check(
    'the clone replaces public/ and httrack cruft is gone',
    existsSync(join(repo, 'public', 'index.html')) &&
      existsSync(join(repo, 'public', 'contact', 'index.html')) &&
      !existsSync(join(repo, 'public', 'hts-cache')) &&
      !existsSync(join(repo, 'public', '0123456789abcdef')),
  );
  check(
    'an existing CNAME wins over --domain and survives the public/ wipe',
    readFileSync(join(repo, 'public', 'CNAME'), 'utf8').trim() === 'kept.example' &&
      report.cname === 'kept.example',
  );
  check(
    'public/ is excluded from Prettier',
    readFileSync(join(repo, '.prettierignore'), 'utf8').includes('public/'),
  );
  // The ESLint half asserted that `.eslintignore` CONTAINED public/ — which it
  // did, while ESLint 9 ignored that file and linted the mirror anyway. The
  // assertion has to be about the file ESLint actually reads.
  check(
    'public/ is excluded in the flat config ESLint 9 actually reads',
    readFileSync(join(repo, 'eslint.config.mjs'), 'utf8').includes("'public/**'"),
  );
  // A real parse, not a substring match: the ignore is inserted by regex, and a
  // config this script corrupts would fail ESLint at run time in the charity's
  // repo rather than here. `node --check` parses without executing.
  check(
    'the flat config still parses as an ES module after the edit',
    spawnSync(process.execPath, ['--check', join(repo, 'eslint.config.mjs')], {
      encoding: 'utf8',
    }).status === 0,
  );
  // The guard exists so an unrecognised config is LOUD rather than silently
  // unignored — which is precisely how the .eslintignore version shipped broken.
  // Without this case the guard is untestable: every other fixture has the
  // anchor, so deleting the check changes nothing and the mutation survives.
  check(
    'a flat config with no ignores array is a hard error, not a silent no-op',
    (() => {
      const odd = mkdtempSync(join(tmpdir(), 'integrate-noanchor-'));
      writeFileSync(join(odd, 'eslint.config.mjs'), 'export default []\n');
      try {
        ensureEslintIgnoresPublic(odd);
        return false; // it returned quietly: the mirror would be linted as source
      } catch (err) {
        return /no .*ignores.* array|public\/\*\*/.test(err.message);
      }
    })(),
  );
  check(
    'a repo with no flat config still gets the legacy .eslintignore',
    (() => {
      const legacyRepo = mkdtempSync(join(tmpdir(), 'integrate-legacy-'));
      ensureEslintIgnoresPublic(legacyRepo);
      return readFileSync(join(legacyRepo, '.eslintignore'), 'utf8').includes('public/');
    })(),
  );
  check(
    'the edit keeps the entries that were already there',
    (() => {
      const src = readFileSync(join(repo, 'eslint.config.mjs'), 'utf8');
      return src.includes("'node_modules/**'") && src.includes("'.next/**'");
    })(),
  );
  check(
    'a template file the drift check requires survives the public/ wipe',
    existsSync(join(repo, 'public', '.well-known', 'security.txt')) &&
      readFileSync(join(repo, 'public', '.well-known', 'security.txt'), 'utf8').includes(
        'Contact:',
      ),
  );
  check(
    'the root-path security.txt fallback and _headers survive too',
    existsSync(join(repo, 'public', 'security.txt')) &&
      existsSync(join(repo, 'public', '_headers')),
  );
  check(
    'the captured site wins where it ships its own file at a preserved path',
    readFileSync(join(repo, 'public', '_headers'), 'utf8').includes('from-the-clone'),
  );

  // Re-clone and integrate again, as a repeat 702 dispatch does: nothing is
  // left to move or remove, and the ignore files must not grow a duplicate
  // entry. This is the pass where the old code recreated the sentinel.
  const again = integrate({ clone, repo, domain: 'fallback.example' });
  check(
    're-running is idempotent',
    again.disabledTemplateRoutes.length === 0 &&
      again.removedDeadSentinels.length === 0 &&
      again.appRoutesRemaining === 0 &&
      readFileSync(join(repo, '.prettierignore'), 'utf8').split('public/').length === 2,
  );

  // countRoutablePages() is exercised directly, not through integrate(): that
  // sweeps every page-shaped file first, so a private page never survives long
  // enough for the report to disagree. The distinction is the whole point of
  // this change, so it gets pinned at the source.
  const routeFixture = join(root, 'routes', 'app');
  mkdirSync(join(routeFixture, '_private'), { recursive: true });
  mkdirSync(join(routeFixture, '(group)'), { recursive: true });
  mkdirSync(join(routeFixture, 'about'), { recursive: true });
  for (const d of ['_private', '(group)', 'about', '.']) {
    writeFileSync(join(routeFixture, d, 'page.tsx'), 'export default function P(){return null}');
  }
  check(
    'a page in a private folder is not counted as a route, but a route group is',
    countRoutablePages(routeFixture) === 3,
  );

  rmSync(root, { recursive: true, force: true });
  console.log(failures ? `\n${failures} self-test failure(s)` : '\nall self-tests passed');
  process.exit(failures ? 1 : 0);
}

if (process.argv.includes('--self-test')) selfTest();

const clone = arg('clone');
const repo = arg('repo');
const domain = arg('domain');
if (!clone || !repo || !domain) {
  console.error('Usage: --clone <cloneSiteRoot> --repo <nextRepoDir> --domain <apexDomain>');
  process.exit(2);
}
if (!existsSync(join(clone, 'index.html'))) {
  console.error(`[integrate] no index.html in clone root ${clone}`);
  process.exit(1);
}

console.error('[integrate] ' + JSON.stringify(integrate({ clone, repo, domain }), null, 2));
