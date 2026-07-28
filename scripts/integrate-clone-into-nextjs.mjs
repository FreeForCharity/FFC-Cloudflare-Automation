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

function arg(name, def) {
  const i = process.argv.indexOf(`--${name}`);
  return i > -1 && process.argv[i + 1] ? process.argv[i + 1] : def;
}

const PAGE = /^page\.(tsx|ts|jsx|js)$/;

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

/** Count surviving `page.*` files under src/app, so the report states the end
 *  state as an observation rather than asserting an invariant. */
function countPages(dir) {
  if (!existsSync(dir)) return 0;
  let n = 0;
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    if (e.isDirectory()) n += countPages(join(dir, e.name));
    else if (PAGE.test(e.name)) n++;
  }
  return n;
}

/**
 * The cloned WordPress assets in public/ are not source and must not be run
 * through the repo's Prettier/ESLint (minified CSS/JS would fail). Make sure the
 * repo's ignore files exclude public/ so its CI (and pre-commit hooks) skip it.
 */
function ensurePublicIgnored(repo) {
  for (const ignoreName of ['.prettierignore', '.eslintignore']) {
    const ignorePath = join(repo, ignoreName);
    const existing = existsSync(ignorePath) ? readFileSync(ignorePath, 'utf8') : '';
    const lines = existing.split(/\r?\n/);
    if (!lines.includes('public/')) {
      const sep = existing && !existing.endsWith('\n') ? '\n' : '';
      writeFileSync(
        ignorePath,
        existing + sep + '# Static WordPress clone assets (not source)\npublic/\n',
      );
    }
  }
}

function countFiles(d) {
  let n = 0;
  for (const e of readdirSync(d, { withFileTypes: true }))
    n += e.isDirectory() ? countFiles(join(d, e.name)) : 1;
  return n;
}

function integrate({ clone, repo, domain }) {
  cleanCloneCruft(clone);

  // Replace public/ with the clone (preserve an existing CNAME if present).
  const publicDir = join(repo, 'public');
  const cnamePath = join(publicDir, 'CNAME');
  const keptCname = existsSync(cnamePath) ? readFileSync(cnamePath, 'utf8').trim() : '';
  rmSync(publicDir, { recursive: true, force: true });
  mkdirSync(publicDir, { recursive: true });
  cpSync(clone, publicDir, { recursive: true });

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
    appRoutesRemaining: countPages(appDir),
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
  check('no `page.*` survives under src/app', report.appRoutesRemaining === 0);
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
    'public/ is excluded from Prettier and ESLint',
    readFileSync(join(repo, '.prettierignore'), 'utf8').includes('public/') &&
      readFileSync(join(repo, '.eslintignore'), 'utf8').includes('public/'),
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
