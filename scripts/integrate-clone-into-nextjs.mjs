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
 * Read-only against the network; only touches the given repo working tree.
 *
 * Usage:
 *   node scripts/integrate-clone-into-nextjs.mjs \
 *     --clone <cloneSiteRoot> --repo <nextRepoDir> --domain <apexDomain>
 */
import {
  cpSync,
  rmSync,
  mkdirSync,
  existsSync,
  readdirSync,
  statSync,
  renameSync,
  writeFileSync,
  readFileSync,
} from 'node:fs';
import { join, relative, dirname } from 'node:path';

function arg(name, def) {
  const i = process.argv.indexOf(`--${name}`);
  return i > -1 && process.argv[i + 1] ? process.argv[i + 1] : def;
}
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

// 1) Clean httrack bookkeeping out of the clone before it becomes public/.
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

// 2) Replace public/ with the clone (preserve an existing CNAME if present).
const publicDir = join(repo, 'public');
const cnamePath = join(publicDir, 'CNAME');
const keptCname = existsSync(cnamePath) ? readFileSync(cnamePath, 'utf8').trim() : '';
rmSync(publicDir, { recursive: true, force: true });
mkdirSync(publicDir, { recursive: true });
cpSync(clone, publicDir, { recursive: true });

// 3) Move template app page routes aside so they don't collide with the clone.
const appDir = join(repo, 'src', 'app');
const backupDir = join(repo, '_disabled_template_routes');
const movedRoutes = [];
const PAGE = /^page\.(tsx|ts|jsx|js)$/;
function disablePages(dir) {
  if (!existsSync(dir)) return;
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name);
    if (e.isDirectory()) {
      disablePages(p);
    } else if (PAGE.test(e.name)) {
      const rel = relative(appDir, p);
      const dest = join(backupDir, rel);
      mkdirSync(dirname(dest), { recursive: true });
      renameSync(p, dest);
      movedRoutes.push(rel);
    }
  }
}
disablePages(appDir);

// 4) Guarantee at least one valid route remains so `next build` succeeds while
//    public/index.html serves '/'. (A bare app dir with no page errors out.)
const sentinelDir = join(appDir, '_clone-host');
mkdirSync(sentinelDir, { recursive: true });
writeFileSync(
  join(sentinelDir, 'page.tsx'),
  'export const dynamic = "force-static";\n' +
    '// Placeholder so Next has a route; the real site is the static clone in public/.\n' +
    'export default function CloneHost() {\n  return null;\n}\n',
);

// 5) Ensure CNAME (apex) is present for GitHub Pages.
writeFileSync(cnamePath, (keptCname || domain) + '\n');

// 6) The cloned WordPress assets in public/ are not source and must not be run
//    through the repo's Prettier/ESLint (minified CSS/JS would fail). Make sure
//    the repo's ignore files exclude public/ so its CI (and pre-commit hooks)
//    skip the clone.
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

const report = {
  domain,
  publicFiles: countFiles(publicDir),
  disabledTemplateRoutes: movedRoutes,
  cname: keptCname || domain,
  note: 'Run `npm ci && npm run build` in the repo; the static export (out/) serves the clone.',
};
function countFiles(d) {
  let n = 0;
  for (const e of readdirSync(d, { withFileTypes: true }))
    n += e.isDirectory() ? countFiles(join(d, e.name)) : 1;
  return n;
}
console.error('[integrate] ' + JSON.stringify(report, null, 2));
