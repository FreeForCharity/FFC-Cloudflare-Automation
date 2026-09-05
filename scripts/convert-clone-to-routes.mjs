#!/usr/bin/env node
/**
 * convert-clone-to-routes.mjs — turn a captured static clone into an FFC-EX
 * repository with real Next.js routes.
 *
 * `scripts/integrate-clone-into-nextjs.mjs` drops the capture into `public/`,
 * which deploys — `output: 'export'` copies that directory verbatim — and
 * leaves the repo in a shape no production FFC-EX site uses. There are no app
 * routes, so the template's per-page canonicals, its `<main>` landmark, its
 * skip-to-content target and its one-h1-per-page build check have nothing to
 * act on; the charity's pages inherit none of the FFC chrome; and every page
 * the capture recorded is a file rather than a route, invisible to the whole
 * toolchain that a Next.js site is checked with.
 *
 * This script produces the shape measured on FFC-EX-catnipandcattitude.org,
 * itself a WordPress clone that was converted rather than hand-written:
 *
 *   src/clone-content/<slug>.html   the page markup, with %%BASE%% tokens
 *   src/app/<slug>/page.tsx         title, description, canonical
 *   src/lib/clone-content.ts        the token substitution
 *   src/components/{ffc-footer,clone-enhance}/
 *   public/                         assets only — zero HTML
 *
 * and on the way through it applies the corrections the capture cannot make
 * from a single page in isolation: exactly one h1 per page with no skipped
 * level (and the Divi per-module CSS retargeted to match), a description on
 * every page that has words of its own to quote, an alt attribute on every
 * image, and an accessible name on every link that wraps only a decorative one.
 *
 * Every decision lives in scripts/clone-to-routes-lib.mjs, which is pure and
 * self-tested (`node scripts/clone-to-routes-lib.mjs --self-test`); this file
 * is the filesystem around it.
 *
 * Usage:
 *   node scripts/convert-clone-to-routes.mjs --repo <path-to-FFC-EX-repo> \
 *        [--site-name "Charity Name"] [--assets-dir _ffc-assets] [--dry-run]
 */
import {
  readFileSync,
  writeFileSync,
  readdirSync,
  mkdirSync,
  rmSync,
  renameSync,
  existsSync,
  statSync,
} from 'node:fs';
import { join, dirname, relative, resolve } from 'node:path';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';

import {
  slugForLocalPath,
  sanitizeSlug,
  normalizePercentEncoding,
  extractBody,
  extractHead,
  extractTitle,
  extractMetaDescription,
  deriveDescription,
  collectHeadings,
  planHeadingLevels,
  applyHeadingLevels,
  mirrorHeadingSelectors,
  scopeCloneCss,
  fragmentHead,
  stripLayoutDuplicates,
  removeDeadConsentUi,
  ensureImageAlt,
  nameAnonymousLinks,
  nameGenericLinks,
  titleIframes,
  tokenizeAssetPaths,
  tokenizePageLinks,
  routeSource,
} from './clone-to-routes-lib.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const ASSETS = join(HERE, '..', 'assets');
const WRAPPER_CLASS = 'ffc-clone';

function arg(name, fallback = null) {
  const i = process.argv.indexOf(`--${name}`);
  return i === -1 || i === process.argv.length - 1 ? fallback : process.argv[i + 1];
}
const FLAG = (name) => process.argv.includes(`--${name}`);

/** Every file under `dir`, as paths relative to it, with forward slashes. */
function walk(dir, base = dir) {
  const out = [];
  let entries;
  try {
    entries = readdirSync(dir, { withFileTypes: true });
  } catch {
    return out;
  }
  for (const e of entries) {
    const full = join(dir, e.name);
    if (e.isDirectory()) out.push(...walk(full, base));
    else if (e.isFile()) out.push(relative(base, full).split('\\').join('/'));
  }
  return out;
}

function write(path, content) {
  mkdirSync(dirname(path), { recursive: true });
  // newline is pinned so a Windows host and a Linux runner commit the same
  // bytes; the FFC-EX repos check formatting in CI and would disagree otherwise.
  writeFileSync(path, content, { encoding: 'utf8' });
}

function copyTemplate(name, dest) {
  write(dest, readFileSync(join(ASSETS, name), 'utf8'));
}

/**
 * Assign each captured page a unique route slug.
 *
 * Two captured paths can name one page, and two can land on one slug, and the
 * right answer is different for each.
 *
 * **Same URL.** RFC 3986 makes the hex digits of a percent-escape
 * case-insensitive, so this capture's `…-the-us%EF%BF%BC/` and
 * `…-the-us%ef%bf%bc/` are one address that the source's sitemap advertises
 * twice. They are COLLAPSED: both spellings route to one page. Publishing them
 * separately would put the charity's article at two URLs with identical text —
 * a duplicate-content penalty inherited from a defect in the source, not chosen
 * by anyone.
 *
 * **Different URLs that sanitize alike.** `sanitizeSlug` is lossy on purpose
 * (it strips characters a directory name and a URL cannot both carry), so two
 * genuinely different pages can want the same slug. Whoever sorts first keeps
 * the clean one and the rest are SUFFIXED, never dropped: a page silently
 * missing from a migration is the failure mode nobody notices.
 */
export function assignSlugs(localPaths) {
  const taken = new Set();
  const assigned = [];
  const collisions = [];
  const duplicates = [];
  const byUrl = new Map();
  for (const localPath of [...localPaths].sort()) {
    const raw = slugForLocalPath(localPath);
    if (raw === null) continue;

    const canonical = normalizePercentEncoding(raw);
    const already = byUrl.get(canonical);
    if (already) {
      duplicates.push({ localPath, raw, slug: already.slug, sameAs: already.raw });
      // Still an alias, so links written with either spelling reach the page.
      already.aliases.push(raw);
      continue;
    }

    const base = sanitizeSlug(raw);
    let slug = base;
    let n = 2;
    while (taken.has(slug)) {
      slug = `${base}-${n}`;
      n += 1;
    }
    if (slug !== base) collisions.push({ localPath, base, slug });
    taken.add(slug);
    const entry = { localPath, raw, slug, aliases: [] };
    byUrl.set(canonical, entry);
    assigned.push(entry);
  }
  return { assigned, collisions, duplicates };
}

function main() {
  const repo = resolve(arg('repo') ?? process.cwd());
  const assetsDir = arg('assets-dir', '_ffc-assets');
  const dryRun = FLAG('dry-run');
  const publicDir = join(repo, 'public');

  const report = readJsonIfPresent(join(publicDir, 'wp-capture-report.json'));
  const siteName = arg('site-name') ?? report?.domain ?? '';

  const files = walk(publicDir);
  const htmlFiles = files.filter((f) => f.endsWith('/index.html') || f === 'index.html');
  if (!htmlFiles.length) {
    console.error(`No captured pages under ${publicDir} — nothing to convert.`);
    process.exit(1);
  }

  const { assigned, collisions, duplicates } = assignSlugs(htmlFiles);
  // Link rewriting is keyed on the path the capture actually wrote, because
  // that is what the markup references; the sanitized slug is where the route
  // ends up. Both are needed, and conflating them silently drops every link
  // whose target got sanitized. Aliases are in here too, so a link written with
  // the other spelling of a collapsed duplicate still resolves.
  const rawToSlug = new Map();
  for (const a of assigned) {
    rawToSlug.set(a.raw, a.slug);
    for (const alias of a.aliases) rawToSlug.set(alias, a.slug);
  }

  const frameHosts = new Set();
  const shape = {};
  const tally = {
    pages: 0,
    h1Fixed: 0,
    levelsChanged: 0,
    descriptionsDerived: 0,
    descriptionsMissing: 0,
    altsAdded: 0,
    linksNamed: 0,
    genericLinksNamed: 0,
    iframesTitled: 0,
    scriptsRemoved: 0,
    consentUiRemoved: 0,
    consentUiBytes: 0,
    headDropped: 0,
    assetRefs: 0,
    inlineStyles: 0,
    pageRefs: 0,
  };

  for (const { localPath, slug } of assigned) {
    const html = readFileSync(join(publicDir, localPath), 'utf8');
    const { body, bodyClass } = extractBody(html);
    const head = extractHead(html);

    // 1. Headings: exactly one h1, no skipped level, CSS retargeted to match.
    const headings = collectHeadings(body);
    const planned = planHeadingLevels(headings);
    const beforeH1 = headings.filter((h) => h.level === 1).length;
    const retagged = applyHeadingLevels(body, planned);
    let out = retagged.html;
    tally.levelsChanged += retagged.changes.length;
    if (beforeH1 !== 1) tally.h1Fixed += 1;

    // 2. Accessibility repairs the capture cannot make per-page.
    const alt = ensureImageAlt(out);
    out = alt.html;
    tally.altsAdded += alt.added;
    const named = nameAnonymousLinks(out, siteName);
    out = named.html;
    tally.linksNamed += named.named;
    const generic = nameGenericLinks(out);
    out = generic.html;
    tally.genericLinksNamed += generic.named;
    const framed = titleIframes(out);
    out = framed.html;
    tally.iframesTitled += framed.titled;
    // Every external frame host has to be in the CSP's `frame-src`, in BOTH the
    // layout's meta tag and public/_headers, or the charity's own podcast and
    // video embeds are refused by the browser. Reported rather than edited: the
    // CSP is the repo's security posture, and widening it is a reviewed change.
    for (const m of out.matchAll(/<iframe\b[^>]*\bsrc="https?:\/\/([^/"]+)/gi)) {
      frameHosts.add(m[1].toLowerCase());
    }

    // 3. Remove what the root layout already provides, and what the source
    //    site left behind that can no longer work.
    const stripped = stripLayoutDuplicates(out);
    out = stripped.html;
    tally.scriptsRemoved += stripped.removedScripts;
    const consent = removeDeadConsentUi(out);
    out = consent.html;
    tally.consentUiRemoved += consent.removed;
    tally.consentUiBytes += consent.bytes;

    // 4. Paths: page-relative in a file, base-relative in a route.
    const assetsTok = tokenizeAssetPaths(out, assetsDir);
    out = assetsTok.html;
    tally.assetRefs += assetsTok.rewritten;
    const linksTok = tokenizePageLinks(out, rawToSlug);
    out = linksTok.html;
    tally.pageRefs += linksTok.rewritten;

    // 5. The styles the fragment cannot render without.
    const fh = fragmentHead(head);
    tally.headDropped += fh.dropped;
    const fhTok = tokenizeAssetPaths(fh.html, assetsDir);
    tally.assetRefs += fhTok.rewritten;
    // Divi splits its presentation between linked stylesheets and a dozen
    // inline <style> blocks, and both halves have to be treated the same way.
    // Scoping only the files would leave the critical inline CSS — which is the
    // half that renders before anything else — still global.
    const fragmentCss = transformInlineStyles(fhTok.html);
    tally.inlineStyles += fragmentCss.blocks;
    out = transformInlineStyles(out).html;

    // 6. Metadata.
    const title = extractTitle(html) || slug || siteName;
    let description = extractMetaDescription(html);
    if (!description) {
      description = deriveDescription(body);
      if (description) tally.descriptionsDerived += 1;
      else tally.descriptionsMissing += 1;
    }

    const fragment = `${fragmentCss.html}\n${out}`.trim() + '\n';
    const wrapperClass = [WRAPPER_CLASS, bodyClass].filter(Boolean).join(' ');
    if (!dryRun) {
      write(join(repo, 'src', 'clone-content', `${slug || 'index'}.html`), fragment);
      write(
        join(repo, 'src', 'app', slug, 'page.tsx'),
        routeSource({ slug, title, description, wrapperClass }),
      );
    }
    tally.pages += 1;
  }

  // 7. The CSS half, applied to every stylesheet the capture downloaded: each
  //    heading selector gains a twin naming the marker class the retag left on
  //    the element, and every selector is then confined to the wrapper so the
  //    charity's theme cannot restyle the FFC components that now share the
  //    document with it.
  let cssFiles = 0;
  let headingRules = 0;
  let bodyRules = 0;
  for (const f of files) {
    if (!f.endsWith('.css')) continue;
    const path = join(publicDir, f);
    const css = readFileSync(path, 'utf8');
    const mirrored = mirrorHeadingSelectors(css);
    const scopedCss = scopeCloneCss(mirrored.css, WRAPPER_CLASS);
    if (mirrored.mirrored || scopedCss.scoped) {
      cssFiles += 1;
      headingRules += mirrored.mirrored;
      bodyRules += scopedCss.scoped;
      if (!dryRun) writeFileSync(path, scopedCss.css, { encoding: 'utf8' });
    }
  }

  // 8. Repo scaffolding: the pieces a route-shaped site needs and a
  //    public/-shaped one did not.
  if (!dryRun) {
    copyTemplate('clone-content-lib.ts', join(repo, 'src', 'lib', 'clone-content.ts'));
    copyTemplate(
      'clone-enhance.tsx',
      join(repo, 'src', 'components', 'clone-enhance', 'index.tsx'),
    );
    copyTemplate('ffc-footer.tsx', join(repo, 'src', 'components', 'ffc-footer', 'index.tsx'));
    copyTemplate('clone-routes-sitemap.ts', join(repo, 'src', 'app', 'sitemap.ts'));
    // The template's sitemap test diffs the sitemap against a hand-maintained
    // array; leaving it in place would fail against a derived one. The property
    // it protected is kept and retargeted — see the template's own header.
    copyTemplate('clone-routes-sitemap.test.ts', join(repo, '__tests__', 'app', 'sitemap.test.ts'));
    appendFooterStyles(join(repo, 'src', 'app', 'globals.css'));
    ignoreCloneContent(join(repo, '.prettierignore'));
    shape.templateRoutes = restoreTemplateRoutes(repo);
    shape.trailingSlash = enableTrailingSlash(repo);
    // Two converted pages so the audit covers the migration, not only the
    // template's policy pages. The front page is already in every config.
    shape.lighthouse = retargetLighthouseUrls(
      repo,
      assigned
        .filter((a) => a.slug && !a.slug.includes('/'))
        .slice(0, 2)
        .map((a) => a.slug),
    );

    // 9. public/ holds assets only. Every page is a route now, and leaving the
    //    HTML behind would publish two copies of the site at two URLs — the
    //    duplicate-content defect, and the sitemap would describe only one.
    for (const f of htmlFiles) rmSync(join(publicDir, f), { force: true });
    pruneEmptyDirs(publicDir);
    rmSync(join(publicDir, assetsDir, 'clone-enhance.js'), { force: true });
  }

  const remainingHtml = walk(publicDir).filter((f) => f.endsWith('.html'));

  console.log('--- conversion ---------------------------------------------');
  console.log(`site                  ${siteName || '(unknown)'}`);
  console.log(`routes written        ${tally.pages}`);
  console.log(`slug collisions       ${collisions.length}`);
  for (const c of collisions) console.log(`  ${c.base} -> ${c.slug}  (${c.localPath})`);
  console.log(`duplicate URLs collapsed  ${duplicates.length}`);
  for (const d of duplicates) console.log(`  ${d.raw}  ==  ${d.sameAs}  -> /${d.slug}`);
  console.log(`pages whose h1 count was wrong  ${tally.h1Fixed}`);
  console.log(`headings retagged     ${tally.levelsChanged}`);
  console.log(`descriptions derived  ${tally.descriptionsDerived}`);
  console.log(`pages still without a description  ${tally.descriptionsMissing}`);
  console.log(`alt attributes added  ${tally.altsAdded}`);
  console.log(`links given a name    ${tally.linksNamed}`);
  console.log(`"Read More" links given a destination  ${tally.genericLinksNamed}`);
  console.log(`embeds given a title  ${tally.iframesTitled}`);
  console.log(`external frame hosts (must be in the CSP frame-src)  ${frameHosts.size}`);
  for (const h of [...frameHosts].sort()) console.log(`  https://${h}`);
  console.log(`scripts removed from fragments  ${tally.scriptsRemoved}`);
  console.log(
    `dead consent banners removed  ${tally.consentUiRemoved}` +
      `  (${(tally.consentUiBytes / 1024 / 1024).toFixed(1)} MB)`,
  );
  console.log(`head elements dropped (owned by Next)  ${tally.headDropped}`);
  console.log(`inline <style> blocks scoped  ${tally.inlineStyles}`);
  console.log(`asset refs tokenized  ${tally.assetRefs}`);
  console.log(`page links tokenized  ${tally.pageRefs}`);
  console.log(
    `stylesheets rewritten ${cssFiles}  (heading twins ${headingRules}, scoped ${bodyRules})`,
  );
  console.log(`HTML left in public/  ${remainingHtml.length}`);
  if (shape.templateRoutes) {
    console.log(`FFC template routes restored  ${shape.templateRoutes.restored.length}`);
    for (const c of shape.templateRoutes.collided) {
      console.log(`  NOT restored (a captured page owns this slug): ${c}`);
    }
  }
  if (shape.trailingSlash) {
    console.log(
      `trailingSlash  ${shape.trailingSlash.changed ? 'enabled' : shape.trailingSlash.reason}`,
    );
  }
  if (shape.lighthouse?.changed) {
    console.log('Lighthouse URLs retargeted:');
    for (const u of shape.lighthouse.urls) console.log(`  ${u}`);
  }
  if (dryRun) console.log('(dry run — nothing was written)');

  // A page that reached no route, or an HTML file left where a second copy of
  // the site would be published from, is a silent failure in the direction
  // nobody checks. Fail rather than report it in a line that scrolls past.
  // Every captured file is either a route or a deliberately collapsed duplicate
  // of one. Anything else means a page fell out of the migration silently.
  if (tally.pages + duplicates.length !== htmlFiles.length) {
    console.error(
      `accounted for ${tally.pages} routes + ${duplicates.length} duplicates` +
        ` of ${htmlFiles.length} captured pages`,
    );
    process.exit(1);
  }
  if (!dryRun && remainingHtml.length) {
    console.error(`public/ still holds ${remainingHtml.length} HTML files`);
    process.exit(1);
  }
}

/** Apply the same two CSS transforms to every inline <style> block. */
function transformInlineStyles(html) {
  let blocks = 0;
  const out = html.replace(/(<style\b[^>]*>)([\s\S]*?)(<\/style>)/gi, (whole, open, css, close) => {
    blocks += 1;
    const mirrored = mirrorHeadingSelectors(css);
    return open + scopeCloneCss(mirrored.css, WRAPPER_CLASS).css + close;
  });
  return { html: out, blocks };
}

/**
 * Bring back the FFC template routes the `public/`-shaped integration parked.
 *
 * `integrate-clone-into-nextjs.mjs` moves every template route into
 * `_disabled_template_routes/` because the captured pages under `public/` would
 * otherwise be shadowed by them. Once the capture IS the routes there is no
 * collision left to avoid, and the parked routes are the FFC template features
 * the site is supposed to keep — the privacy, cookie, terms, donation,
 * vulnerability-disclosure and security-acknowledgements pages that the footer
 * standard links to. Leaving them parked ships a footer of links to 404s.
 *
 * The template's own home page is the one exception: the charity's front page
 * owns `/` now, so it is dropped rather than restored.
 */
function restoreTemplateRoutes(repo) {
  const parked = join(repo, '_disabled_template_routes');
  const restored = [];
  const collided = [];
  let entries;
  try {
    entries = readdirSync(parked, { withFileTypes: true });
  } catch {
    return { restored, collided };
  }
  for (const entry of entries) {
    const from = join(parked, entry.name);
    if (entry.isFile()) {
      // page.tsx at the top level is the template home page.
      rmSync(from, { force: true });
      continue;
    }
    const to = join(repo, 'src', 'app', entry.name);
    if (existsSync(to)) {
      collided.push(entry.name);
      continue;
    }
    renameSync(from, to);
    restored.push(entry.name);
  }
  if (!readdirSync(parked).length) rmSync(parked, { recursive: true, force: true });
  return { restored, collided };
}

/**
 * Serve every route at the trailing-slash URL the source site used.
 *
 * WordPress served `/about-us/`, the captured markup links to `/about-us/`, and
 * without this Next writes `about-us.html` and nothing answers at the slashed
 * form — so the migrated site's own internal links 404, and so does every
 * inbound link and search result pointing at the old URLs. It is the one
 * next.config change the conversion requires.
 */
function enableTrailingSlash(repo) {
  const path = join(repo, 'next.config.ts');
  let source;
  try {
    source = readFileSync(path, 'utf8');
  } catch {
    return { changed: false, reason: 'no next.config.ts' };
  }
  if (/\btrailingSlash\s*:/.test(source)) return { changed: false, reason: 'already set' };
  const anchor = /(\n\s*output:\s*'export',)/;
  if (!anchor.test(source)) return { changed: false, reason: "no `output: 'export'` to anchor to" };
  const note =
    '\n  // The source WordPress served every page at a trailing-slash URL, and the\n' +
    '  // converted pages link to each other the same way. Without this the export\n' +
    '  // writes `about-us.html` and `/about-us/` 404s on GitHub Pages — so this is\n' +
    '  // not a style preference: it is what keeps the migrated site’s own internal\n' +
    '  // links working, and what keeps every inbound link and search result that\n' +
    '  // points at the old URLs landing on the page it used to.\n' +
    '  trailingSlash: true,';
  write(path, source.replace(anchor, `$1${note}`));
  return { changed: true };
}

/**
 * Point Lighthouse CI at URLs that exist, and at the migrated content.
 *
 * The template's `lighthouserc.json` audits `privacy-policy.html`; with
 * trailing slashes that file is gone and lhci would audit 404s — whose
 * accessibility and SEO assertions are error-level, so CI fails on the harness
 * rather than the site. Two converted pages are added because auditing only the
 * template's policy pages would leave the actual migration unmeasured.
 */
function retargetLighthouseUrls(repo, extraSlugs) {
  const path = join(repo, 'lighthouserc.json');
  let config;
  let source;
  try {
    source = readFileSync(path, 'utf8');
    config = JSON.parse(source);
  } catch {
    return { changed: false };
  }
  const urls = config?.ci?.collect?.url;
  if (!Array.isArray(urls)) return { changed: false };
  const slashed = urls.map((u) =>
    u.replace(/\/([a-z0-9-]+)\.html$/i, (whole, slug) =>
      slug === 'index' ? '/index.html' : `/${slug}/index.html`,
    ),
  );
  for (const slug of extraSlugs) {
    const url = `http://localhost/${slug}/index.html`;
    if (!slashed.includes(url)) slashed.splice(1, 0, url);
  }
  if (slashed.join('\n') === urls.join('\n')) return { changed: false };
  config.ci.collect.url = slashed;
  write(path, `${JSON.stringify(config, null, 2)}\n`);
  return { changed: true, urls: slashed };
}

function readJsonIfPresent(path) {
  try {
    return JSON.parse(readFileSync(path, 'utf8'));
  } catch {
    return null;
  }
}

const FOOTER_MARK = '/* --- FFC attribution footer (added by workflow 706)';
function appendFooterStyles(globalsPath) {
  let css = '';
  try {
    css = readFileSync(globalsPath, 'utf8');
  } catch {
    /* a repo without globals.css gets one holding just these rules */
  }
  if (css.includes(FOOTER_MARK)) return;
  const add = readFileSync(join(ASSETS, 'ffc-footer.css'), 'utf8');
  write(globalsPath, `${css.replace(/\s*$/, '')}\n\n${add}`);
}

const IGNORE_MARK = 'src/clone-content/';
/**
 * Keep Prettier out of the captured fragments.
 *
 * They are several hundred files of machine-generated markup, and reformatting
 * them is not merely churn: Prettier reflows HTML, and inside the captured
 * markup whitespace between inline elements is rendered text. `public/` is
 * already excluded for the same reason; the conversion moves the pages, so the
 * exclusion has to move with them.
 */
function ignoreCloneContent(path) {
  let text = '';
  try {
    text = readFileSync(path, 'utf8');
  } catch {
    /* a repo without one gets a file holding just this rule */
  }
  if (text.includes(IGNORE_MARK)) return;
  write(
    path,
    `${text.replace(/\s*$/, '')}\n\n# Captured page markup (generated, whitespace-sensitive)\n${IGNORE_MARK}\n`,
  );
}

function pruneEmptyDirs(dir) {
  let entries;
  try {
    entries = readdirSync(dir, { withFileTypes: true });
  } catch {
    return true;
  }
  let empty = true;
  for (const e of entries) {
    if (e.isDirectory()) {
      if (pruneEmptyDirs(join(dir, e.name)))
        rmSync(join(dir, e.name), { recursive: true, force: true });
      else empty = false;
    } else empty = false;
  }
  return empty && statSync(dir).isDirectory();
}

/* ------------------------------------------------------------------ *
 * Self-test — `node scripts/convert-clone-to-routes.mjs --self-test`
 *
 * The string transforms are tested in clone-to-routes-lib.mjs, which is pure.
 * What is left here is everything that touches the filesystem, and those are
 * the decisions that change a repository irreversibly: which captured paths
 * become which routes, which parked template routes come back, and the two
 * config edits without which the converted site serves 404s at its own links.
 * They are exercised against a real temporary repo rather than mocked, because
 * a mock of `renameSync` cannot tell you a collision was handled.
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

  // --- slug assignment -------------------------------------------------
  // RFC 3986 makes a percent-escape's hex digits case-insensitive, so these
  // two sitemap entries are one page. Publishing both would put the charity's
  // article at two URLs with identical text.
  const dup = assignSlugs([
    'index.html',
    'a-%EF%BF%BC/index.html',
    'a-%ef%bf%bc/index.html',
    '_ffc-assets/x.css',
  ]);
  eq('a duplicate spelling of one URL collapses', dup.assigned.length, 2);
  eq('and is recorded rather than dropped silently', dup.duplicates.length, 1);
  eq(
    'both spellings route to the same page',
    dup.duplicates[0].slug,
    dup.assigned.find((a) => a.localPath.startsWith('a-')).slug,
  );
  eq(
    'a non-page file is not a route',
    dup.assigned.some((a) => a.localPath.endsWith('.css')),
    false,
  );

  // Two genuinely different pages that sanitize alike must BOTH survive: a
  // page silently missing from a migration is the failure nobody notices.
  const clash = assignSlugs(['a b/index.html', 'a_b/index.html']);
  eq('two different pages that sanitize alike both get routes', clash.assigned.length, 2);
  eq('the second is suffixed, not dropped', clash.collisions.length, 1);
  eq('and the two slugs are distinct', new Set(clash.assigned.map((a) => a.slug)).size, 2);

  // --- the repo shape --------------------------------------------------
  const dir = mkdtempSync(join(tmpdir(), 'ffc-convert-'));
  try {
    mkdirSync(join(dir, '_disabled_template_routes', 'privacy-policy'), { recursive: true });
    mkdirSync(join(dir, '_disabled_template_routes', 'about-us'), { recursive: true });
    mkdirSync(join(dir, 'src', 'app', 'about-us'), { recursive: true });
    write(join(dir, '_disabled_template_routes', 'privacy-policy', 'page.tsx'), 'x');
    write(join(dir, '_disabled_template_routes', 'about-us', 'page.tsx'), 'x');
    write(join(dir, '_disabled_template_routes', 'page.tsx'), 'template home');
    write(join(dir, 'src', 'app', 'about-us', 'page.tsx'), 'the captured page');

    const routes = restoreTemplateRoutes(dir);
    // The footer standard links to these; leaving them parked ships 404s.
    eq('a parked template route comes back', routes.restored, ['privacy-policy']);
    // The charity's front page owns / now.
    eq(
      'the template home page is dropped, not restored',
      existsSync(join(dir, 'src', 'app', 'page.tsx')),
      false,
    );
    // Restoring over a captured page would delete the charity's content.
    eq('a route the capture owns is not overwritten', routes.collided, ['about-us']);
    eq(
      'and the captured page is still there',
      readFileSync(join(dir, 'src', 'app', 'about-us', 'page.tsx'), 'utf8'),
      'the captured page',
    );

    // Without this the export writes about-us.html and every internal link,
    // every inbound link and every search result 404s.
    write(join(dir, 'next.config.ts'), "const nextConfig = {\n  output: 'export',\n}\n");
    eq('trailingSlash is enabled', enableTrailingSlash(dir).changed, true);
    eq(
      'and it lands inside the config object',
      /output:\s*'export',\s*\n(?:\s*\/\/[^\n]*\n)*\s*trailingSlash: true,/.test(
        readFileSync(join(dir, 'next.config.ts'), 'utf8'),
      ),
      true,
    );
    eq('a second run does not add it twice', enableTrailingSlash(dir).changed, false);
    write(join(dir, 'next.config.ts'), 'const nextConfig = {}\n');
    eq(
      'a config with no static export is left alone rather than guessed at',
      enableTrailingSlash(dir).changed,
      false,
    );

    // lhci asserts accessibility and SEO at ERROR level, so auditing 404s
    // fails CI on the harness rather than on the site.
    write(
      join(dir, 'lighthouserc.json'),
      JSON.stringify({
        ci: {
          collect: {
            url: ['http://localhost/index.html', 'http://localhost/privacy-policy.html'],
          },
        },
      }),
    );
    const lh = retargetLighthouseUrls(dir, ['about-us']);
    eq('the audited policy URL gains its trailing slash', lh.urls, [
      'http://localhost/index.html',
      'http://localhost/about-us/index.html',
      'http://localhost/privacy-policy/index.html',
    ]);
    eq('the front page keeps its own shape', lh.urls[0], 'http://localhost/index.html');
    eq('a second run is a no-op', retargetLighthouseUrls(dir, ['about-us']).changed, false);

    // --- inline styles get the same treatment as the linked ones -------
    const styled = transformInlineStyles('<style>body.single h1{color:#fff}</style>');
    eq(
      'an inline style block is scoped and mirrored like a stylesheet',
      styled.html,
      '<style>.ffc-clone.single h1,.ffc-clone.single .ffc-h1{color:#fff}</style>',
    );
    eq('and the block is counted', styled.blocks, 1);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }

  console.log(failures ? `\n${failures} self-test(s) failed` : '\nall self-tests passed');
  return failures ? 1 : 0;
}

if (process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url))) {
  if (process.argv.includes('--self-test')) process.exit(selfTest());
  main();
}
