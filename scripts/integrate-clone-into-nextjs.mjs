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
import { join, relative, dirname, sep } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

function arg(name, def) {
  const i = process.argv.indexOf(`--${name}`);
  return i > -1 && process.argv[i + 1] ? process.argv[i + 1] : def;
}

const PAGE = /^page\.(tsx|ts|jsx|js)$/;

/**
 * Names at the ROOT of `public/` that must NOT be carried across the wipe.
 *
 * Everything else directly under `public/` is carried, because the template
 * ships eight files there and the named list this replaces tracked three of
 * them. Measured on FFC-EX-newheightseducation.org, whose `public/` root at
 * the template commit held `_headers`, `security.txt`, `favicon.ico`,
 * `icon.png`, `apple-icon.png`, `android-chrome-192x192.png`,
 * `android-chrome-512x512.png` and `web-app-manifest-512x512.png`. The wipe
 * kept the first two and deleted the other six, so the migrated site lost
 * every icon it publishes — the browser tab, the iOS home screen, the web app
 * manifest — and nothing failed, because an icon 404 is not a broken link in
 * any markup the gates crawl.
 *
 * This is the same lesson `PRESERVED_PUBLIC_DIRS` already records one level
 * down, and recording it there did not fix it here: a snapshot of the template
 * stops tracking the template. So the rule is inverted. What survives is
 * "whatever the template has", and what needs naming is the short list of
 * names THIS PIPELINE owns — a list that cannot drift, because this repo is
 * what writes them.
 */
export const UNCARRIED_PUBLIC_ROOT_FILES = [
  // The capture's own inventory, written into the clone root by
  // `capture-wordpress-api.mjs` and copied per-host by 706. A previous run's
  // copy MUST NOT survive into a run that captured a different set of hosts:
  // `convert-clone-to-routes` reads `wp-capture-report.json` as the page
  // inventory, and `verify-reused-capture` reads every
  // `wp-capture-report.<label>.json` in the directory. A stale one is not a
  // lost icon, it is a wrong answer about what was captured.
  /^wp-capture-report(\.[^/]+)?\.json$/,
  // HTML, which the template never ships at this root: `public/` holds assets
  // only -- zero HTML -- and every page is a route under `src/app`. So HTML
  // here is a PREVIOUS run's captured page, which is pipeline output by the
  // same argument as the report above.
  //
  // Raised by review against the first version of this rule, which carried it.
  // The stated consequence was wrong -- `convert-clone-to-routes` removes
  // every `*/index.html` it converts, and `exit(1)`s on any unrouted HTML left
  // under `public/`, so a resurrected page could not reach a visitor. The cost
  // is the other one: a stale `foo.html` is not `*/index.html`, so it is
  // neither converted nor removed, and it fails the conversion of a site it
  // has nothing to do with, after the crawl has already been paid for.
  /\.x?html?$/i,
  // CNAME is carried by its own step, which then writes `keptCname || domain`
  // unconditionally — so carrying it here would make the "clone wins on a
  // collision" rule below false for exactly one entry. That is the right
  // behaviour for CNAME (the published domain is an FFC deployment decision,
  // not content captured from the source site), and the wrong thing to express
  // through a mechanism that promises the opposite. Behaviour-neutral on its
  // own, since the explicit write lands either way; it is here so the
  // mechanism's promise stays true of everything the mechanism handles.
  /^CNAME$/,
];

/** Is this root-level name one the pipeline owns rather than the template's? */
export function isUncarriedPublicRootFile(name) {
  return UNCARRIED_PUBLIC_ROOT_FILES.some((re) => re.test(name));
}

/**
 * Template ASSET directories that must survive the wipe, listed as directories
 * rather than as filenames.
 *
 * The template's own components reference these by absolute path — measured on
 * FFC-EX-newheightseducation.org, 19 assets across 12 files under `src/`. The
 * wipe deleted every one of them, and the template code that names them
 * survives integration, so each became a 404 in the export.
 *
 * Run 35698011512 is what made this visible: three of them
 * (`Images/figma-hero-img.webp`, `Images/logo.webp`, `Svgs/footerImage.svg`)
 * failed the self-containment gate. The other sixteen were equally deleted and
 * simply sat on pages that run's crawl did not reach — so the observed count
 * was a property of the crawl, not of the damage.
 *
 * ORDER OF DISCOVERY MATTERS HERE. Every earlier delivery of this site died at
 * the publishable-size gate, which runs BEFORE the self-containment gate, so
 * this defect was unreachable rather than absent. It has been in every
 * integration since `public/` started being wiped.
 *
 * Listed as DIRECTORIES on purpose. A filename list is what failed: the
 * template gains and renames assets, and a list of names silently stops
 * tracking it — which is exactly how nineteen live references came to point at
 * deleted files with nothing failing until a gate happened to crawl the right
 * page. A directory tracks the template instead of a snapshot of it.
 *
 * Small enough to hold in memory: 380 KB across 20 files, measured.
 *
 * DIRECTORIES stay a named list while root FILES are carried wholesale, and
 * the asymmetry is the size of what each sweep can pick up. The template's
 * `public/` root is eight small files (~525 KB in total, almost all of it one
 * favicon); its directories are unbounded, and a rule that swept them all
 * would carry `public/videos/mission-video.mp4` — 5.7 MB — into every
 * migration. That video is referenced only from `src/components/home-page/`,
 * whose page 706 parks in `_disabled_template_routes/`, so after an
 * integration nothing can reach it. Carrying it would be 5.7 MB of dead weight
 * charged against the publishable-size gate on every charity site.
 *
 * `.well-known` earns its place the other way: `security.txt` under it is what
 * the target repo's own drift check fails on, and it is the artifact FFC
 * requires so a charity site has a way to receive vulnerability reports. It is
 * listed as a directory rather than as `.well-known/security.txt` for the same
 * reason the other two are — a path is a snapshot, a directory tracks.
 */
export const PRESERVED_PUBLIC_DIRS = ['Images', 'Svgs', '.well-known'];

/**
 * Read the preserved files before the wipe. Missing ones are simply absent.
 *
 * Directory entries are expanded to one map entry per file, so the
 * clone-wins-on-collision rule below stays per-file: a captured site that
 * happens to ship `Images/logo.webp` keeps its own, and the rest of the
 * template's directory still comes back.
 */
export function readPreservedPublicFiles(publicDir) {
  const kept = new Map();
  if (!existsSync(publicDir)) return kept;
  for (const e of readdirSync(publicDir, { withFileTypes: true })) {
    if (!e.isFile() || isUncarriedPublicRootFile(e.name)) continue;
    kept.set(e.name, readFileSync(join(publicDir, e.name)));
  }
  for (const dir of PRESERVED_PUBLIC_DIRS) {
    const root = join(publicDir, dir);
    if (!existsSync(root)) continue;
    const walk = (abs) => {
      for (const e of readdirSync(abs, { withFileTypes: true })) {
        const child = join(abs, e.name);
        if (e.isDirectory()) walk(child);
        else if (e.isFile()) {
          // Forward slashes: these keys are compared against clone paths and
          // printed in the report, and `relative` spells them with `\` on the
          // Windows host this repo's own Conductor runs on.
          kept.set(relative(publicDir, child).split(sep).join('/'), readFileSync(child));
        }
      }
    };
    walk(root);
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

/**
 * Templates written verbatim into the charity repo, and where they land.
 *
 * They live as real files rather than string literals so they are reviewable,
 * type-checked by the receiving repo's own CI, and formatted in that repo's
 * Prettier style (no semicolons) — which is why `.prettierignore` here excludes
 * them from this repo's own formatter.
 */
const CLONE_SOURCE_FILES = [
  ['clone-sitemap.ts', join('src', 'app', 'sitemap.ts')],
  ['clone-sitemap.test.ts', join('__tests__', 'app', 'sitemap.test.ts')],
];

/**
 * Replace the template's route-derived sitemap with one derived from the clone.
 *
 * The FFC template builds `/sitemap.xml` from a hand-maintained list of
 * `src/app/**` routes, and guards it with a unit test that diffs that list
 * against the filesystem. Both are correct for a repo whose pages are app
 * routes, and both are wrong the moment this script runs: every template route
 * is moved into `_disabled_template_routes/` precisely so the captured pages do
 * not collide with them, so the sitemap ends up advertising eight routes that
 * no longer exist and none of the several hundred pages that do — and its test
 * fails comparing that list against an empty directory.
 *
 * The replacement derives the list from `public/` at build time. Note this is
 * not "delete the failing test": the module under test is replaced in the same
 * step, the test is replaced with one asserting the same property (the sitemap
 * cannot silently fall behind what is published) against where the pages now
 * live, and it covers every captured page rather than eight template ones.
 *
 * Skipped entirely on a repo that has no `src/app/sitemap.ts` — an FFC-EX repo
 * that does not carry the template's sitemap has nothing here to correct, and
 * writing one in would be inventing a route it never asked for.
 */
function installCloneSitemap(repo, assetsDir) {
  const target = join(repo, 'src', 'app', 'sitemap.ts');
  if (!existsSync(target)) return [];
  const written = [];
  for (const [src, rel] of CLONE_SOURCE_FILES) {
    const from = join(assetsDir, src);
    if (!existsSync(from)) {
      throw new Error(
        `${from} is missing from this checkout. It is written into the charity repo verbatim, ` +
          'so refusing to deliver a clone whose sitemap would describe routes that no longer exist.',
      );
    }
    const dest = join(repo, rel);
    mkdirSync(dirname(dest), { recursive: true });
    cpSync(from, dest);
    written.push(rel.split(sep).join('/'));
  }
  return written;
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

/**
 * Flat-config filenames in ESLint's own resolution order, highest priority
 * first.
 *
 * Copied from `lib/config/config-loader.js` in the shipped package rather than
 * from memory, and verified against eslint 10.1.0 — the first draft of this
 * list put `.mjs` ahead of `.js` and omitted the three TypeScript variants
 * outright. Both errors have the same consequence as the bug this function
 * exists to fix: a repo carrying `eslint.config.js` alongside `.mjs` would
 * have had the ignore written to the file ESLint does not read, and a repo
 * using `eslint.config.ts` would have fallen through to the `.eslintignore`
 * branch, which ESLint 9+ ignores. Silent, in both cases.
 */
const ESLINT_FLAT_CONFIGS = [
  'eslint.config.js',
  'eslint.config.mjs',
  'eslint.config.cjs',
  'eslint.config.ts',
  'eslint.config.mts',
  'eslint.config.cts',
];

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

  // Extend an existing ignores array where there is one.
  const extend = /ignores:\s*\[/;
  if (extend.test(src)) {
    writeFileSync(
      configPath,
      src.replace(
        extend,
        "ignores: [\n      // Static WordPress clone assets (not source), added by workflow 706.\n      'public/**',",
      ),
    );
    return;
  }
  // A flat config with no `ignores` at all is still perfectly valid, and
  // refusing it would break a conversion for a repo that has done nothing
  // wrong. A leading ignores-only element is the documented way to add global
  // ignores, and inserting one after the array's opening bracket needs no
  // understanding of what follows.
  //
  // Both module systems, because this list carries `.cjs` and `.cts` and a
  // plain `.js` is CommonJS in any package without `"type": "module"` — which
  // is the default. Matching only `export default [` meant every one of those
  // fell through to the throw below and failed a conversion for a repo whose
  // config is perfectly valid. Caught in review on #1235; the filename list
  // said the shapes were supported and the code only handled one of them.
  const prepend = /(?:export\s+default|module\.exports\s*=)\s*\[/;
  if (prepend.test(src)) {
    writeFileSync(
      configPath,
      src.replace(
        prepend,
        (m) =>
          `${m}\n  // Static WordPress clone assets (not source), added by workflow 706.\n  { ignores: ['public/**'] },`,
      ),
    );
    return;
  }
  throw new Error(
    `${configPath} has neither an \`ignores: [\` array to extend nor an \`export default [\` ` +
      `or \`module.exports = [\` to prepend to, so the WordPress mirror under public/ would ` +
      `be linted as source. Add ` +
      `"public/**" to its ignores and re-run — refusing to write an ignore file ESLint 9 ` +
      `does not read.`,
  );
}

function countFiles(d) {
  let n = 0;
  for (const e of readdirSync(d, { withFileTypes: true }))
    n += e.isDirectory() ? countFiles(join(d, e.name)) : 1;
  return n;
}

/** This checkout's assets/ directory, resolved from the script rather than the cwd. */
const ASSETS_DIR = join(dirname(fileURLToPath(import.meta.url)), '..', 'assets');

function integrate({ clone, repo, domain, assetsDir = ASSETS_DIR }) {
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
  // Order-independent of the two lines above, deliberately: the sitemap this
  // installs reads `public/` at build time and never looks at `src/app`, and
  // `disableTemplatePages` moves only `page.*` files, so neither step can see
  // the other's work. Said explicitly because the first draft of this line
  // claimed the opposite, and a mutation that swapped the order proved the
  // claim untestable — which is the tell that it was not true.
  const sitemapFiles = installCloneSitemap(repo, assetsDir);

  // Ensure CNAME (apex) is present for GitHub Pages.
  writeFileSync(cnamePath, (keptCname || domain) + '\n');

  ensurePublicIgnored(repo);

  return {
    domain,
    publicFiles: countFiles(publicDir),
    disabledTemplateRoutes: movedRoutes,
    cloneSourceFilesWritten: sitemapFiles,
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
  // The template's own asset directories. `nested/` is not decoration: the
  // expansion walks recursively, and a flat readdir would pass every other
  // assertion here while dropping a subdirectory on the real template.
  mkdirSync(join(repo, 'public', 'Images', 'nested'), { recursive: true });
  writeFileSync(join(repo, 'public', 'Images', 'figma-hero-img.webp'), 'HERO');
  writeFileSync(join(repo, 'public', 'Images', 'logo.webp'), 'TEMPLATE-LOGO');
  writeFileSync(join(repo, 'public', 'Images', 'nested', 'deep.webp'), 'DEEP');
  mkdirSync(join(repo, 'public', 'Svgs'), { recursive: true });
  writeFileSync(join(repo, 'public', 'Svgs', 'footerImage.svg'), '<svg/>');
  // The template's root-level icons. `src/app/manifest.ts` and
  // `src/app/layout.tsx` survive integration and name these by absolute path,
  // so losing them is six live 404s on every migrated site — measured on
  // FFC-EX-newheightseducation.org, where `manifest.webmanifest` shipped
  // pointing at two PNGs the wipe had deleted.
  writeFileSync(join(repo, 'public', 'favicon.ico'), 'ICO');
  writeFileSync(join(repo, 'public', 'icon.png'), 'ICON');
  writeFileSync(join(repo, 'public', 'apple-icon.png'), 'APPLE');
  writeFileSync(join(repo, 'public', 'android-chrome-192x192.png'), 'A192');
  writeFileSync(join(repo, 'public', 'android-chrome-512x512.png'), 'A512');
  // A root-level file under a name nothing in this repo lists, which is the
  // case the five above cannot prove. Restoring a NAME LIST that happened to
  // spell those five would pass every assertion about them; only a file the
  // rule has never heard of distinguishes "these names are carried" from
  // "whatever the template has is carried". The next asset the template adds
  // at this root arrives under exactly such a name.
  writeFileSync(join(repo, 'public', 'template-gained-this-later.txt'), 'UNLISTED');
  // …and the two things at that root the pipeline owns rather than the
  // template. The stale per-host report is the case that makes the exclusion
  // more than tidiness: this run captures no `oldsub` host, and a resurrected
  // `wp-capture-report.oldsub.json` is what `verify-reused-capture` would read
  // as evidence that it did.
  writeFileSync(join(repo, 'public', 'wp-capture-report.json'), '{"stale":true}');
  writeFileSync(join(repo, 'public', 'wp-capture-report.oldsub.json'), '{"stale":true}');
  // A previous run's captured page. `.html` at this root is never the
  // template's -- `public/` is assets only -- so carrying it would hand the
  // next conversion a page from a site it has nothing to do with.
  writeFileSync(join(repo, 'public', 'stale-page.html'), '<h1>from a previous run</h1>');
  // A template directory the list deliberately does NOT name. It is here so
  // the wholesale root sweep cannot be mistaken for a wholesale sweep: a
  // 5.7 MB video nothing can reach after integration must still be wiped.
  mkdirSync(join(repo, 'public', 'videos'), { recursive: true });
  writeFileSync(join(repo, 'public', 'videos', 'mission-video.mp4'), 'MP4');
  // An ESLint 9 flat config, shaped like the real template's.
  writeFileSync(
    join(repo, 'eslint.config.mjs'),
    "const eslintConfig = [\n  {\n    ignores: [\n      'node_modules/**',\n      '.next/**',\n    ],\n  },\n]\n\nexport default eslintConfig\n",
  );
  // …and a clone that ships its own file at one preserved path, to prove the
  // captured site is not overwritten by the template.
  writeFileSync(join(clone, '_headers'), '/*\n  X-From: from-the-clone\n');
  // …and its own file inside a preserved DIRECTORY, which must also win. A
  // directory entry is not a licence to overwrite the charity's content.
  mkdirSync(join(clone, 'Images'), { recursive: true });
  writeFileSync(join(clone, 'Images', 'logo.webp'), 'CLONE-LOGO');
  // …and its own CNAME, which must NOT win: the published domain is an FFC
  // deployment decision, not content captured from the source site.
  writeFileSync(join(clone, 'CNAME'), 'from-the-clone.example\n');
  // …and the report for the host it actually captured, which is the only one
  // that may exist afterwards.
  writeFileSync(join(clone, 'wp-capture-report.json'), '{"stale":false}');

  // The template's route-derived sitemap and the unit test that guards it.
  mkdirSync(join(repo, '__tests__', 'app'), { recursive: true });
  writeFileSync(
    join(appDir, 'sitemap.ts'),
    "export const routes = [{ path: '/privacy-policy' }]\n",
  );
  writeFileSync(
    join(repo, '__tests__', 'app', 'sitemap.test.ts'),
    "import { routes } from '../../src/app/sitemap'\n",
  );

  const report = integrate({ clone, repo, domain: 'fallback.example' });

  // --- The sitemap follows the routes ---------------------------------------
  // Both files are replaced together. Replacing only the test would be
  // quarantining a failure; replacing only the module would leave a test
  // importing symbols that no longer exist. The property the template's test
  // protected — the sitemap cannot silently fall behind what is published — is
  // kept and retargeted at public/, where the pages now are.
  check(
    'the route-derived sitemap is replaced with the clone-derived one',
    readFileSync(join(appDir, 'sitemap.ts'), 'utf8').includes('discoverExportedPages'),
  );
  check(
    'and its test is replaced in the same step, not deleted',
    readFileSync(join(repo, '__tests__', 'app', 'sitemap.test.ts'), 'utf8').includes(
      'lists every exported page exactly once',
    ),
  );
  check(
    'both replacements are reported rather than done silently',
    JSON.stringify(report.cloneSourceFilesWritten) ===
      JSON.stringify(['src/app/sitemap.ts', '__tests__/app/sitemap.test.ts']),
  );
  // A repo that never carried the template's sitemap has nothing to correct,
  // and writing one in would invent a route it never asked for.
  {
    const bare = join(root, 'no-sitemap-repo');
    mkdirSync(join(bare, 'src', 'app'), { recursive: true });
    mkdirSync(join(bare, 'public'), { recursive: true });
    const bareClone = join(root, 'no-sitemap-clone');
    mkdirSync(bareClone, { recursive: true });
    writeFileSync(join(bareClone, 'index.html'), '<html>home</html>');
    const r = integrate({ clone: bareClone, repo: bare, domain: 'x.example' });
    check(
      'a repo with no template sitemap is left alone',
      r.cloneSourceFilesWritten.length === 0 && !existsSync(join(bare, 'src', 'app', 'sitemap.ts')),
    );
  }
  // The templates are delivered verbatim, so a checkout missing one must stop
  // the run rather than ship a sitemap describing routes that no longer exist.
  {
    const orphan = join(root, 'orphan-repo');
    mkdirSync(join(orphan, 'src', 'app'), { recursive: true });
    mkdirSync(join(orphan, 'public'), { recursive: true });
    writeFileSync(join(orphan, 'src', 'app', 'sitemap.ts'), 'export const routes = []\n');
    const orphanClone = join(root, 'orphan-clone');
    mkdirSync(orphanClone, { recursive: true });
    writeFileSync(join(orphanClone, 'index.html'), '<html>home</html>');
    let threw = '';
    try {
      integrate({
        clone: orphanClone,
        repo: orphan,
        domain: 'x.example',
        assetsDir: join(root, 'nowhere'),
      });
    } catch (err) {
      threw = err?.message ?? '';
    }
    check(
      'a missing template stops the run and names the file',
      threw.includes('clone-sitemap.ts') && threw.includes('missing'),
    );
  }

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
  // The exception PRESERVED_PUBLIC_FILES documents, asserted as behaviour.
  // Removing CNAME from that list is behaviour-neutral on its own — the
  // explicit write overwrites either way — so only this pins the rule that
  // actually governs it, and distinguishes CNAME from `_headers` above.
  check(
    'the CNAME the repo already published beats one shipped by the clone',
    readFileSync(join(repo, 'public', 'CNAME'), 'utf8').trim() === 'kept.example',
  );
  check(
    'CNAME is governed by its own step, not by the carried-file mechanism',
    isUncarriedPublicRootFile('CNAME'),
  );
  // Both directions. A predicate that answered `true` for everything would
  // satisfy the three positives alone -- and would wipe the root wholesale,
  // which is the defect this rule replaced. The labelled report name is the
  // one 706 writes per host, and it is spelled out rather than derived
  // because the pattern has to survive a hostname full of dots.
  check(
    'the pipeline owns exactly the report names, CNAME and HTML, and nothing else',
    isUncarriedPublicRootFile('wp-capture-report.json') &&
      isUncarriedPublicRootFile('wp-capture-report.school.newheightseducation.org.json') &&
      isUncarriedPublicRootFile('index.html') &&
      isUncarriedPublicRootFile('About-Us.HTM') &&
      !isUncarriedPublicRootFile('favicon.ico') &&
      !isUncarriedPublicRootFile('android-chrome-512x512.png') &&
      !isUncarriedPublicRootFile('_headers') &&
      // `.htm` is HTML; `.htaccess` and `.html-template` are not, and a
      // pattern loose enough to catch them would start eating real files.
      !isUncarriedPublicRootFile('.htaccess') &&
      !isUncarriedPublicRootFile('notes.html.bak'),
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
  // A config with no `ignores` is still a valid config, and refusing it would
  // break a conversion for a repo that has done nothing wrong. `export default
  // [` takes a leading ignores-only element — the documented way to declare
  // global ignores — which needs no understanding of what follows it.
  check(
    'a flat config with no ignores array gets an ignores-only element prepended',
    (() => {
      const bare = mkdtempSync(join(tmpdir(), 'integrate-noanchor-'));
      const cfg = join(bare, 'eslint.config.mjs');
      writeFileSync(cfg, 'export default [\n  { rules: {} },\n]\n');
      // Caught rather than allowed to escape: without this the prepend path
      // is still "detected" if it regresses, but as a crashed self-test
      // reporting zero failures, which reads as a passing suite that died.
      try {
        ensureEslintIgnoresPublic(bare);
      } catch {
        return false;
      }
      const after = readFileSync(cfg, 'utf8');
      return (
        after.includes("{ ignores: ['public/**'] }") &&
        spawnSync(process.execPath, ['--check', cfg], { encoding: 'utf8' }).status === 0
      );
    })(),
  );
  // …but a shape neither anchor recognises is still LOUD rather than silently
  // unignored, which is precisely how the .eslintignore version shipped broken.
  // Without this case that guard is untestable: every other fixture matches an
  // anchor, so deleting the throw changes nothing and the mutation survives.
  check(
    'a flat config matching neither anchor is a hard error, not a silent no-op',
    (() => {
      const odd = mkdtempSync(join(tmpdir(), 'integrate-unknown-'));
      writeFileSync(
        join(odd, 'eslint.config.mjs'),
        'const c = [{ rules: {} }]\nexport default c\n',
      );
      try {
        ensureEslintIgnoresPublic(odd);
        return false; // it returned quietly: the mirror would be linted as source
      } catch (err) {
        return /ignores|public\/\*\*/.test(err.message);
      }
    })(),
  );
  // A CommonJS flat config. `eslint.config.cjs` and `.cts` are in the filename
  // list, and a plain `eslint.config.js` is CommonJS in any package without
  // `"type": "module"` — the default — so this is the common shape, not an
  // exotic one. It matched neither branch and fell through to the throw,
  // failing a conversion for a repo whose config is perfectly valid.
  {
    const cjs = join(root, 'cjs-repo');
    mkdirSync(join(cjs, 'src', 'app'), { recursive: true });
    mkdirSync(join(cjs, 'public'), { recursive: true });
    writeFileSync(join(cjs, 'eslint.config.cjs'), 'module.exports = [\n  { rules: {} },\n]\n');
    const cjsClone = join(root, 'cjs-clone');
    mkdirSync(cjsClone, { recursive: true });
    writeFileSync(join(cjsClone, 'index.html'), '<html>home</html>');
    let threw = '';
    try {
      integrate({ clone: cjsClone, repo: cjs, domain: 'x.example' });
    } catch (err) {
      threw = err?.message ?? String(err);
    }
    const after = existsSync(join(cjs, 'eslint.config.cjs'))
      ? readFileSync(join(cjs, 'eslint.config.cjs'), 'utf8')
      : '';
    check(
      'a CommonJS flat config is prepended to rather than rejected',
      threw === '' && after.includes("{ ignores: ['public/**'] }"),
    );
    check(
      'and the prepend lands inside the array, not before module.exports',
      after.indexOf('module.exports = [') < after.indexOf("{ ignores: ['public/**'] }"),
    );
    // The legacy file ESLint 9 does not read must NOT be the fallback here.
    check(
      'no .eslintignore is written for a repo that has a flat config',
      !existsSync(join(cjs, '.eslintignore')),
    );
  }

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
  // Reads that tolerate absence. A bare readFileSync here THROWS when the file
  // is missing, which is precisely the state these three cases exist to detect
  // — so the self-test would die with ENOENT instead of printing FAIL, and a
  // crashed run is indistinguishable from a harness that could not start.
  // Found by mutation testing: dropping the recursive walk, and dropping the
  // map write, both produced a stack trace rather than a named failure.
  const keptText = (...parts) => {
    const abs = join(repo, 'public', ...parts);
    return existsSync(abs) ? readFileSync(abs, 'utf8') : null;
  };
  check(
    'the template asset directories survive the public/ wipe',
    keptText('Images', 'figma-hero-img.webp') === 'HERO' &&
      keptText('Svgs', 'footerImage.svg') === '<svg/>',
  );
  check(
    'a preserved directory is walked recursively, not just its top level',
    keptText('Images', 'nested', 'deep.webp') === 'DEEP',
  );
  check(
    'the captured site still wins inside a preserved directory',
    keptText('Images', 'logo.webp') === 'CLONE-LOGO',
  );
  check(
    'the captured site wins where it ships its own file at a preserved path',
    readFileSync(join(repo, 'public', '_headers'), 'utf8').includes('from-the-clone'),
  );
  check(
    'a root-level template file NO list names survives — the rule tracks, not enumerates',
    keptText('template-gained-this-later.txt') === 'UNLISTED',
  );
  check(
    'every template icon at the root of public/ survives the wipe',
    keptText('favicon.ico') === 'ICO' &&
      keptText('icon.png') === 'ICON' &&
      keptText('apple-icon.png') === 'APPLE' &&
      keptText('android-chrome-192x192.png') === 'A192' &&
      keptText('android-chrome-512x512.png') === 'A512',
  );
  check(
    "a previous run's captured PAGE does NOT survive the wipe",
    keptText('stale-page.html') === null,
  );
  check(
    "a previous run's capture report does NOT survive the wipe",
    keptText('wp-capture-report.oldsub.json') === null,
  );
  check(
    "…and the report at the plain name is this run's, not the one that was there",
    keptText('wp-capture-report.json') === '{"stale":false}',
  );
  check(
    'a template DIRECTORY the list does not name is still wiped',
    keptText('videos', 'mission-video.mp4') === null,
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

  // -- flat-config precedence, measured against ESLint's own loader ---------
  // Writing the ignore into a config ESLint does not read is the SAME failure
  // this function was written to fix, one layer over — and it is silent, so
  // only an explicit case catches it.
  {
    const both = join(root, 'eslint-precedence');
    mkdirSync(both, { recursive: true });
    const shape = (marker) =>
      `const c = [\n  {\n    ignores: [\n      '${marker}/**',\n    ],\n  },\n]\n\nexport default c\n`;
    writeFileSync(join(both, 'eslint.config.js'), shape('from-js'));
    writeFileSync(join(both, 'eslint.config.mjs'), shape('from-mjs'));
    ensureEslintIgnoresPublic(both);
    check(
      'with both .js and .mjs present the ignore goes into .js, which ESLint prefers',
      readFileSync(join(both, 'eslint.config.js'), 'utf8').includes("'public/**'") &&
        !readFileSync(join(both, 'eslint.config.mjs'), 'utf8').includes("'public/**'"),
    );
  }
  {
    // A TypeScript flat config is a config, not an absent one. Omitting these
    // sent such a repo down the .eslintignore branch, which ESLint 9+ ignores.
    const ts = join(root, 'eslint-ts');
    mkdirSync(ts, { recursive: true });
    writeFileSync(
      join(ts, 'eslint.config.ts'),
      "const c = [\n  {\n    ignores: [\n      'node_modules/**',\n    ],\n  },\n]\n\nexport default c\n",
    );
    ensureEslintIgnoresPublic(ts);
    check(
      'an eslint.config.ts is edited rather than falling through to .eslintignore',
      readFileSync(join(ts, 'eslint.config.ts'), 'utf8').includes("'public/**'") &&
        !existsSync(join(ts, '.eslintignore')),
    );
  }
  check(
    "the filename list matches ESLint's documented resolution order exactly",
    JSON.stringify(ESLINT_FLAT_CONFIGS) ===
      JSON.stringify([
        'eslint.config.js',
        'eslint.config.mjs',
        'eslint.config.cjs',
        'eslint.config.ts',
        'eslint.config.mts',
        'eslint.config.cts',
      ]),
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
