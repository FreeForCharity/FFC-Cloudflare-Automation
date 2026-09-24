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
import { join, dirname, relative, resolve, sep } from 'node:path';
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
  detectTitleSuffix,
  stripTitleSuffix,
  extractMetaDescription,
  deriveDescription,
  collectHeadings,
  planHeadingLevels,
  applyHeadingLevels,
  mirrorHeadingSelectors,
  scopeCloneCss,
  fragmentHead,
  demoteWidgetTitles,
  removeDeadNamelessControls,
  ensureSingleH1,
  repairSocialShareChrome,
  stripLayoutDuplicates,
  removeDeadConsentUi,
  ensureImageAlt,
  nameAnonymousLinks,
  repairInlineShareButtons,
  repairEscapedAttributeQuotes,
  repairMalformedHrefs,
  nameGenericLinks,
  titleIframes,
  tokenizeAssetPaths,
  tokenizePageLinks,
  localizeRootAssetRefs,
  unlinkDeadPageLinks,
  dropMissingStylesheets,
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
/**
 * HTML still sitting in `public/` that should have become a route.
 *
 * `<assetsDir>/…` is excluded, and that exclusion is load-bearing rather than
 * a convenience. The asset localizer stores an `<iframe src>` under
 * `<assetsDir>/<host>/<path>`, and some of those targets legitimately serve
 * `text/html` — a video-player document, an embedded map, a widget. Those are
 * ASSETS the published site must keep, not pages that failed to become routes,
 * and the two are only distinguishable by where they live.
 *
 * Measured on run 35575009432 (newheightseducation.org): the gate failed on
 * two Animoto player documents under
 * `_ffc-assets/s3.amazonaws.com/embed.animoto.com/`. Deleting them would have
 * broken both embeds; failing the run on them blocked a conversion that was
 * correct.
 *
 * `startsWith` is anchored at the root on purpose: a captured page really
 * living at `foo/_ffc-assets/x.html` is a page, not an asset of this site.
 *
 * @param {string[]} files     paths relative to `public/`
 * @param {string}   assetsDir the localized-asset directory name
 */
export function unroutedHtml(files, assetsDir) {
  const prefix = `${assetsDir}/`;
  return files.filter((f) => f.endsWith('.html') && !f.startsWith(prefix));
}

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

    // `sanitizeSlug` keeps only `[a-z0-9-]`, so a permalink written entirely in
    // a non-Latin script (`/новости/`) or in punctuation (`/---/`) collapses to
    // the EMPTY string — which is the front page's slug. Measured before this
    // guard: `['новости/index.html', 'index.html']` assigned `['', '-2']`, so
    // a random post became the site root and the charity's actual front page
    // was published at `/-2/`. A leading-hyphen folder also fails the repo's
    // kebab-case drift rule, which is the symptom that would have been noticed
    // first and the least of it.
    //
    // Only the root may hold the empty slug, and the root is the one entry
    // whose `raw` is itself empty.
    const base = raw ? sanitizeSlug(raw) || 'page' : '';
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

  const resolveCapturedAsset = makeAssetResolver(publicDir, assetsDir);

  // Read every captured title before emitting any route: the brand suffix is a
  // property of the SITE, so it can only be derived once all of them are in
  // hand. Cheap — the documents are read again below, from the page cache.
  const capturedTitles = htmlFiles.map((f) =>
    extractTitle(readFileSync(join(publicDir, f), 'utf8')),
  );
  const detectedSuffix = detectTitleSuffix(capturedTitles);
  // Strip the suffix ONLY when the layout is going to put the same name back.
  //
  // `title.template` appends `siteConfig.name`, so on a repo that has been
  // rebranded to the charity, stripping turns
  // `About Us | Charity | Charity` into `About Us | Charity`. On a repo still
  // carrying the FFC template's identity it would instead turn
  // `About Us | Charity | Free For Charity` into `About Us | Free For Charity`
  // — deleting the charity's name from all 587 titles to leave only its
  // sponsor's.
  //
  // Keeping the suffix is therefore right, but it is only half the fix, and
  // the first version of this comment stopped here and called the leftover
  // "untidy". It is not: measured on the vpmin.org conversion, 595 of 596
  // exported pages rendered `… | Viewpoint Ministries International | Free For
  // Charity` — the sponsor's brand on every one of the charity's pages, and
  // titles up to 171 characters against Google's ~60. So the un-rebranded
  // branch ALSO emits `title: { absolute }` (see `routeSource`), which
  // suppresses the template rather than fighting it, and the page reproduces
  // the title the source site served. Raised on FFC-EX-vpmin.org#27, where it
  // was reported as double branding on 4 pages.
  const configuredName = readSiteConfigName(repo);
  const rebranded =
    detectedSuffix &&
    configuredName &&
    detectedSuffix.trim().toLowerCase() === configuredName.trim().toLowerCase();
  const titleSuffix = rebranded ? detectedSuffix : null;

  // The FFC template ships `pageMetadata()`, which already handles Next's
  // shallow metadata merge (per-page og/twitter that keep the site's image and
  // site_name). Prefer it when the repo has it; a repo without it gets the
  // inline form. Checked rather than assumed, because these routes are written
  // into whatever repo the workflow is pointed at.
  const pageMetadataHelper = existsSync(join(repo, 'src', 'lib', 'page-metadata.ts'));

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
  const routeSlugs = new Set(assigned.map((a) => a.slug));

  const frameHosts = new Set();
  const deadTargets = new Map();
  const shape = {};
  // A target is live if it is a captured page (by the path the markup names or
  // by the slug it became) or a file that ships in public/.
  const shippedFiles = new Set(files);
  const isLiveTarget = (target) =>
    rawToSlug.has(target) ||
    routeSlugs.has(target) ||
    shippedFiles.has(target) ||
    shippedFiles.has(`${target}/index.html`);

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
    shareLinksRepaired: 0,
    hrefsRepaired: 0,
    shareChromeRemoved: 0,
    shareLinksRefused: 0,
    widgetTitlesDemoted: 0,
    deadControlsRemoved: 0,
    footersDemoted: 0,
    footersKeptNested: 0,
    consentUiRemoved: 0,
    consentUiBytes: 0,
    headDropped: 0,
    assetRefs: 0,
    assetRefsRelinked: 0,
    deadLinksUnlinked: 0,
    inlineStyles: 0,
    missingSheetsDropped: 0,
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
    tally.footersDemoted += stripped.demotedFooters;
    tally.footersKeptNested += stripped.keptNestedFooters;
    const consent = removeDeadConsentUi(out);
    out = consent.html;
    tally.consentUiRemoved += consent.removed;
    tally.consentUiBytes += consent.bytes;

    // 4. Paths: page-relative in a file, base-relative in a route.
    const assetsTok = tokenizeAssetPaths(out, assetsDir);
    out = assetsTok.html;
    tally.assetRefs += assetsTok.rewritten;
    // The capture's own host, so an in-site link written ABSOLUTELY is
    // tokenized like a relative one. Without it a migration links away from
    // itself to the site it is replacing -- which still serves those pages
    // until cutover, so nothing looks broken while a reviewer is silently
    // reading the old site.
    const linksTok = tokenizePageLinks(out, rawToSlug, siteName ? [siteName] : []);
    out = linksTok.html;
    tally.pageRefs += linksTok.rewritten;
    // A `wp-content/…` reference the capture downloaded but never rewrote —
    // WordPress links an upload with a plain <a href>, which is neither a page
    // nor an asset the capture's rewriter sees.
    const relinked = localizeRootAssetRefs(out, resolveCapturedAsset);
    out = relinked.html;
    tally.assetRefsRelinked += relinked.rewritten;
    // A relative link left after tokenization points at a page this migration
    // does not have — a WordPress author archive, archive pagination.
    //
    // The predicate is a real lookup, NOT "anything still relative is dead".
    // That shortcut was written first and was wrong: links carrying a fragment
    // or a query were not tokenized at the time, so 351 links to live pages
    // read as dead and were unlinked. The tokenizer now carries those across,
    // and this asks rather than assumes — the two together, because either
    // alone still silently breaks working links.
    const deadLinks = unlinkDeadPageLinks(out, isLiveTarget);
    out = deadLinks.html;
    tally.deadLinksUnlinked += deadLinks.unlinked;
    for (const [target, n] of deadLinks.dead) {
      deadTargets.set(target, (deadTargets.get(target) ?? 0) + n);
    }

    // 5. The styles the fragment cannot render without.
    const fh = fragmentHead(head);
    tally.headDropped += fh.dropped;
    const fhTok = tokenizeAssetPaths(fh.html, assetsDir);
    tally.assetRefs += fhTok.rewritten;
    // Divi's per-taxonomy stylesheet is generated at request time, so for the
    // archive pages it was never fetched for, the link only produces a 404.
    const sheets = dropMissingStylesheets(fhTok.html, (t) => resolveCapturedAsset(t) !== null);
    tally.missingSheetsDropped += sheets.dropped;
    // Divi splits its presentation between linked stylesheets and a dozen
    // inline <style> blocks, and both halves have to be treated the same way.
    // Scoping only the files would leave the critical inline CSS — which is the
    // half that renders before anything else — still global.
    const fragmentCss = transformInlineStyles(sheets.html);
    tally.inlineStyles += fragmentCss.blocks;
    out = transformInlineStyles(out).html;

    // 6. Metadata.
    // The layout carries `title.template` (`%s | <site name>`), so a captured
    // title that still ends in the brand renders it twice.
    //
    // The front page is the exception, and Next.js is the reason: a template
    // applies to CHILD segments, and `app/page.tsx` shares the root segment
    // with `app/layout.tsx`, so nothing appends the brand there. Stripping it
    // leaves the site's front page titled `Home` — measured, and a worse title
    // than the one the capture came with.
    const capturedTitle = extractTitle(html);
    const title =
      (slug ? stripTitleSuffix(capturedTitle, titleSuffix) : capturedTitle) || slug || siteName;
    let description = extractMetaDescription(html);
    if (!description) {
      description = deriveDescription(body);
      if (description) tally.descriptionsDerived += 1;
      else tally.descriptionsMissing += 1;
    }

    // Share chrome first: the capture strips scripts, and a share plugin is
    // almost entirely script -- its links keep their destinations in data
    // attributes and its modal triggers keep nothing at all.
    const share = repairSocialShareChrome(`${fragmentCss.html}\n${out}`.trim() + '\n');
    tally.shareLinksRepaired += share.repaired;
    tally.shareChromeRemoved += share.removed;
    tally.shareLinksRefused += share.rejected;
    // The same plugin's INLINE row, which keeps the inputs to a share URL
    // rather than a finished one. Needs the slug, because its `data-url` is
    // relative to the page it sits on.
    const inline = repairInlineShareButtons(share.html, slug);
    tally.shareLinksRepaired += inline.repaired;
    tally.shareLinksRefused += inline.rejected;
    // Typos the SOURCE SITE shipped: a doubled `hhttps://`, a hostname with no
    // scheme, an href with a leading space. Repaired before the naming pass,
    // which reads the href to build the name.
    // Before the href repair: an escaped `src=\\"...\\"` is not a malformed
    // URL, it is an attribute the browser never parsed, so it has to become a
    // real attribute before anything can inspect its value.
    const unescaped = repairEscapedAttributeQuotes(inline.html);
    const hrefs = repairMalformedHrefs(unescaped.html);
    tally.hrefsRepaired += hrefs.repaired;
    // Naming comes AFTER every repair and BEFORE the removal. After, because a
    // repair turns `href="#"` into a real destination and this pass skips a
    // bare `#` on purpose -- run first, it leaves every repaired link nameless.
    // Before, because a named control is one the removal keeps.
    const named = nameAnonymousLinks(hrefs.html, siteName);
    tally.linksNamed += named.named;
    // The heading last, from the title computed just above: a WordPress
    // archive template often renders none, and the FFC template's
    // `verify:build` requires exactly one per indexable page.
    // After the share repair, which turns a parked destination into a real
    // href -- so anything still `href="#"` here genuinely has nowhere to go.
    const dead = removeDeadNamelessControls(named.html);
    tally.deadControlsRemoved += dead.removed;
    // Before the heading check, not after: a hidden widget title counts as
    // the page's <h1> otherwise, and the page keeps no heading a reader can
    // reach while every static check reports one.
    const widget = demoteWidgetTitles(dead.html);
    tally.widgetTitlesDemoted += widget.demoted;
    const fragment = ensureSingleH1(widget.html, title);
    const wrapperClass = [WRAPPER_CLASS, bodyClass].filter(Boolean).join(' ');
    if (!dryRun) {
      write(join(repo, 'src', 'clone-content', `${slug || 'index'}.html`), fragment);
      write(
        join(repo, 'src', 'app', slug, 'page.tsx'),
        routeSource({
          slug,
          title,
          description,
          wrapperClass,
          absoluteTitle: !rebranded,
          pageMetadataHelper,
        }),
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
    shape.verifyBuildScope = scopeVerifyBuildToRoutes(repo, assetsDir);
    shape.templateRoutes = restoreTemplateRoutes(repo);
    shape.trailingSlash = enableTrailingSlash(repo);
    shape.wiredComponents = wireGeneratedComponents(repo);
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

  const remainingHtml = unroutedHtml(walk(publicDir), assetsDir);

  console.log('--- conversion ---------------------------------------------');
  console.log(`site                  ${siteName || '(unknown)'}`);
  console.log(`routes written        ${tally.pages}`);
  console.log(
    `brand suffix in captured titles  ${detectedSuffix ?? '(none detected)'}` +
      `  ->  ${
        titleSuffix
          ? 'stripped (siteConfig.name matches; the layout re-appends it)'
          : `KEPT, and the layout template suppressed with title.absolute ` +
            `(siteConfig.name is ${JSON.stringify(configuredName)}, which is not this site's brand)`
      }`,
  );
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
  console.log(`share links repointed          ${tally.shareLinksRepaired}`);
  console.log(`malformed hrefs repaired       ${tally.hrefsRepaired}`);
  console.log(`dead share controls removed    ${tally.shareChromeRemoved}`);
  console.log(`widget titles demoted to h2    ${tally.widgetTitlesDemoted}`);
  console.log(`dead nameless controls removed ${tally.deadControlsRemoved}`);
  // Loud rather than silent: a refusal means the capture parked a
  // destination this conversion will not make live (javascript:, data:,
  // protocol-relative). Zero is the expected reading, and a non-zero one is
  // worth a look at the source site.
  if (tally.shareLinksRefused) {
    console.log(`share links REFUSED (unsafe)   ${tally.shareLinksRefused}`);
  }
  console.log(
    `captured page footers demoted to <div>  ${tally.footersDemoted}` +
      `  (nested, left as footers: ${tally.footersKeptNested})`,
  );
  console.log(
    `dead consent banners removed  ${tally.consentUiRemoved}` +
      `  (${(tally.consentUiBytes / 1024 / 1024).toFixed(1)} MB)`,
  );
  console.log(`head elements dropped (owned by Next)  ${tally.headDropped}`);
  console.log(`inline <style> blocks scoped  ${tally.inlineStyles}`);
  console.log(`stylesheet links dropped (file never captured)  ${tally.missingSheetsDropped}`);
  console.log(`asset refs tokenized  ${tally.assetRefs}`);
  console.log(`page links tokenized  ${tally.pageRefs}`);
  console.log(`references repointed at the captured file  ${tally.assetRefsRelinked}`);
  console.log(`links to pages the capture does not have, unlinked  ${tally.deadLinksUnlinked}`);
  for (const [t, n] of [...deadTargets].sort((a, b) => b[1] - a[1]).slice(0, 8)) {
    console.log(`  ${String(n).padStart(5)}  ${t}`);
  }
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
  // Say what the wiring did, always -- including when it did nothing. The
  // defect this step exists to prevent is a component that is generated,
  // committed, and imported by nothing: `clone-enhance` is the captured
  // pages' entire client-side runtime, and while it sat unwired a phone could
  // not open the menu on any of newheightseducation.org's 793 pages. That
  // failure is invisible in a build, in a link check and in a page's markup.
  // A step whose whole purpose is to close a silent gap cannot itself report
  // silently.
  if (shape.wiredComponents) {
    const report = describeWiring(shape.wiredComponents);
    console.log(report.headline);
    for (const n of report.notes) console.log(`  ${n}`);
  }
  if (dryRun) console.log('(dry run — nothing was written)');

  // A page that reached no route, or an HTML file left where a second copy of
  // the site would be published from, is a silent failure in the direction
  // nobody checks. Fail rather than report it in a line that scrolls past.
  // The whole conversion depends on `trailingSlash: true`: the captured pages
  // link to each other with a trailing slash, so without it the migrated site's
  // own navigation 404s. Reporting that and exiting 0 would hand back a
  // "successful" conversion that does not work.
  // A missing anchor is not "nothing to do" -- it means the layout this repo
  // actually has does not match what the step knows how to edit, so the
  // components were generated and left unreferenced. Treated the same way as
  // trailingSlash above and for the same reason: reporting it in a line that
  // scrolls past, and exiting 0, is how the gap lasted from the migration
  // until someone rendered the site at 390px by hand.
  const wiredWarnings = describeWiring(shape.wiredComponents).warnings;
  if (!dryRun && wiredWarnings.length) {
    console.error(
      `could not wire ${wiredWarnings.length} generated component(s) into src/app/layout.tsx:`,
    );
    for (const w of wiredWarnings) console.error(`  ${w}`);
    console.error(
      'The component was written to src/components/ and nothing imports it, so it will not' +
        ' run on any page. Wire it by hand in src/app/layout.tsx and re-run, or fix the' +
        ' anchor this step looks for.',
    );
    process.exit(1);
  }

  const ts = shape.trailingSlash;
  if (!dryRun && ts && !ts.changed && ts.reason !== 'already set') {
    console.error(
      `could not set trailingSlash in next.config.ts (${ts.reason}); the converted` +
        " site's own internal links would 404. Set `trailingSlash: true` and re-run.",
    );
    process.exit(1);
  }

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
    // NAME them. This gate used to print only a count, and the conversion it
    // stops runs after a 13-14 minute crawl of the charity's live site — so a
    // bare number costs another full crawl just to learn which files it meant.
    // Measured on newheightseducation.org (run 35571249633): "public/ still
    // holds 2 HTML files", and nothing in the run said which 2.
    console.error(
      `public/ still holds ${remainingHtml.length} HTML file(s) that never became a route:`,
    );
    for (const f of remainingHtml.slice(0, 20)) console.error(`  ${f}`);
    if (remainingHtml.length > 20) {
      console.error(`  ... and ${remainingHtml.length - 20} more`);
    }
    // Says the cause rather than making the reader rediscover it. The first
    // version of this hint named only the page case and was WRONG about the
    // first real failure it met — those files were localized assets, which is
    // why `<assetsDir>/` is now excluded above. Both cases stated, in the
    // order they are likely.
    console.error(
      'Only `<path>/index.html` becomes a route, so a captured PAGE whose URL already ends' +
        ' in `.html` lands here under its own name and would be published as a second,' +
        ' unrouted copy of that page.',
    );
    console.error(
      `(Localized assets under \`${assetsDir}/\` are not counted — an <iframe src> that serves` +
        ' HTML is an asset the site must keep, not a page that failed to convert.)',
    );
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
/** A `page.*` directly here — i.e. this directory IS a route someone owns. */
function hasRoutablePage(dir) {
  if (!existsSync(dir)) return false;
  return readdirSync(dir).some((name) => /^page\.(tsx|ts|jsx|js)$/.test(name));
}

/**
 * Move a parked route tree into place, tolerating a destination that already
 * exists as the empty husk integrate left behind.
 *
 * `renameSync` cannot merge into an existing directory, so the husk has to be
 * merged rather than renamed over. A file whose destination already exists is
 * left parked instead of overwriting captured content — the same "the capture
 * wins" rule the entry-level check applies, enforced per file so a partially
 * captured subtree cannot smuggle a template page over a real one.
 */
function mergeRouteDirectory(from, to) {
  mkdirSync(to, { recursive: true });
  for (const entry of readdirSync(from, { withFileTypes: true })) {
    const src = join(from, entry.name);
    const dest = join(to, entry.name);
    if (entry.isDirectory()) {
      mergeRouteDirectory(src, dest);
    } else if (!existsSync(dest)) {
      renameSync(src, dest);
    }
  }
  if (!readdirSync(from).length) rmSync(from, { recursive: true, force: true });
}

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
    // Collision means THE CAPTURE OWNS THIS ROUTE — a routable page already
    // sits there — not merely that the directory exists.
    //
    // `existsSync(to)` was the predicate until #1342, and it read every route
    // as collided, so NONE was ever restored. The cause is upstream:
    // integrate-clone-into-nextjs.mjs parks routes by moving the `page.tsx`
    // FILE, which leaves `src/app/<slug>/` behind as an empty directory.
    // Measured against pristine `main`: after integrate, all four of
    // donation-policy, free-for-charity-donation-policy,
    // vulnerability-disclosure-policy and privacy-policy exist with
    // `contents=[]`. Every one then took the `collided` branch here.
    //
    // The visible cost was the whole footer standard 404ing on every exported
    // page, which is what 706's self-containment gate reports as `0/3 pages
    // passed` — a failure that names template routes and so reads as a problem
    // with the captured site. The old self-test could not catch it because its
    // fixture built `src/app/about-us/` WITH a page.tsx, i.e. only the genuine
    // collision, never the empty husk the real pipeline produces.
    if (hasRoutablePage(to)) {
      collided.push(entry.name);
      continue;
    }
    mergeRouteDirectory(from, to);
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
/**
 * Render the two components this converter GENERATES.
 *
 * Step 8 copies `clone-enhance.tsx` and `ffc-footer.tsx` into the repo and
 * nothing has ever edited `layout.tsx` to use them, so both arrived orphaned.
 * Measured on newheightseducation.org, 2026-09-24, two days after delivery:
 *
 *   - `clone-enhance` is the captured pages' entire client-side runtime. Not
 *     rendered, a phone visitor got a hamburger that did nothing and ZERO
 *     visible navigation links on every one of 793 pages.
 *   - `ffc-footer` is the migration footer, whose own docblock explains that a
 *     captured page keeps its own visual footer and this strip carries the FFC
 *     attribution and policy links. Not rendered, the template's marketing
 *     footer shipped instead: a second 814px footer under the charity's own,
 *     with the supporting organization's contact details and seven links to
 *     anchors that a captured home page does not have.
 *
 * Both were generated correctly and wired nowhere, which no gate could see
 * because every gate checks the export against itself and an export missing a
 * component is perfectly self-consistent.
 *
 * Idempotent: 706 re-runs over a repo it has already converted, so each edit
 * checks for its own result first.
 */
/**
 * Turn a `wireGeneratedComponents` result into what the run should say about it.
 *
 * Pure, and separate from both callers, so the self-tests below exercise the
 * thing that actually decides -- a test that re-derived "is this a warning?"
 * from the notes itself would pass while the summary printed nothing.
 *
 * `warnings` is what the conversion exits non-zero on. A note is a warning
 * when the step could not find the anchor it edits, which means the component
 * was generated into src/components/ and left imported by nothing: the exact
 * state `clone-enhance` was in on newheightseducation.org, where the captured
 * pages' entire client-side runtime never ran and a phone could not open the
 * menu on any of 793 pages.
 */
function describeWiring(wired) {
  if (!wired) return { headline: 'layout.tsx wiring  not attempted', notes: [], warnings: [] };
  const notes = Array.isArray(wired.notes) ? wired.notes : [];
  const state = wired.changed ? 'edited' : (wired.reason ?? 'no change');
  return {
    headline: `layout.tsx wiring  ${state}`,
    notes,
    warnings: notes.filter((n) => String(n).startsWith('WARNING')),
  };
}

function wireGeneratedComponents(repo) {
  const path = join(repo, 'src', 'app', 'layout.tsx');
  let source;
  try {
    source = readFileSync(path, 'utf8');
  } catch {
    return { changed: false, reason: 'no src/app/layout.tsx' };
  }
  const before = source;
  const done = [];

  // 1. The footer. Repoint the existing import rather than adding a second
  //    one -- the template imports a default-exported `Footer` and renders
  //    `<Footer />`, so swapping the module keeps the JSX untouched.
  if (/from\s+['"][^'"]*components\/ffc-footer['"]/.test(source)) {
    done.push('footer already pointed at ffc-footer');
  } else {
    // `^...` with the `m` flag, not a leading `\n`: an import on the FIRST
    // line of the file has no newline before it. The fixture caught that --
    // this repo's layout happens to start with a `type` import, so the
    // newline form would have worked here and failed on the next repo.
    const footerImport = /^([ \t]*import\s+Footer\s+from\s+)(['"])([^'"]*components\/)footer\2/m;
    if (footerImport.test(source)) {
      source = source.replace(
        footerImport,
        (_m, head, q, prefix) => `${head}${q}${prefix}ffc-footer${q}`,
      );
      done.push('footer repointed');
    } else {
      done.push('WARNING: no `import Footer from .../footer` to repoint');
    }
  }

  // 2. The clone runtime. A component with no visual output, rendered beside
  //    the header so it mounts on every route including a client navigation.
  if (/components\/clone-enhance/.test(source)) {
    done.push('clone-enhance already wired');
  } else {
    const headerImport = /^([ \t]*import\s+Header\s+from\s+)(['"])([^'"]*components\/)header\2/m;
    const m = headerImport.exec(source);
    if (!m) {
      done.push('WARNING: no `import Header from .../header` to anchor to');
    } else {
      const quote = m[2];
      const prefix = m[3];
      source = source.replace(
        m[0],
        `${m[0]}\nimport CloneEnhance from ${quote}${prefix}clone-enhance${quote}`,
      );
      // Rendered right after <Header />, which every FFC layout has.
      const render = /(\n?[ \t]*)<Header\s*\/>/;
      if (render.test(source)) {
        source = source.replace(
          render,
          (_m2, indent) => `${indent}<Header />${indent}<CloneEnhance />`,
        );
        done.push('clone-enhance wired');
      } else {
        done.push('WARNING: no `<Header />` to render beside');
      }
    }
  }

  if (source === before) return { changed: false, notes: done };
  write(path, source);
  return { changed: true, notes: done };
}

function enableTrailingSlash(repo) {
  const path = join(repo, 'next.config.ts');
  let source;
  try {
    source = readFileSync(path, 'utf8');
  } catch {
    return { changed: false, reason: 'no next.config.ts' };
  }
  if (/\btrailingSlash\s*:/.test(source)) return { changed: false, reason: 'already set' };
  // Tolerant of how the line is written: either quote style, comma optional.
  // The original anchor required single quotes AND a trailing comma, and
  // matched 1 of those 4 formats — measured. The other three returned
  // "changed: false" and the conversion carried on, producing a site whose
  // own internal links 404. A formatting preference in the receiving repo is
  // not a reason to ship that.
  const anchor = /\n[ \t]*output:\s*['"]export['"][ \t]*,?/;
  const found = anchor.exec(source);
  if (!found) return { changed: false, reason: "no `output: 'export'` to anchor to" };
  const line = found[0];
  const withComma = line.endsWith(',') ? line : `${line},`;
  const note =
    '\n  // The source WordPress served every page at a trailing-slash URL, and the\n' +
    '  // converted pages link to each other the same way. Without this the export\n' +
    '  // writes `about-us.html` and `/about-us/` 404s on GitHub Pages — so this is\n' +
    '  // not a style preference: it is what keeps the migrated site\u2019s own internal\n' +
    '  // links working, and what keeps every inbound link and search result that\n' +
    '  // points at the old URLs landing on the page it used to.\n' +
    '  trailingSlash: true,';
  write(path, source.replace(line, `${withComma}${note}`));
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

/**
 * The localized path for a source-relative asset reference, or null.
 *
 * The capture writes assets under `<assetsDir>/<host>/<original path>`, and a
 * site can reference more than one host, so the lookup tries each host
 * directory rather than assuming the site's own. Returns the `%%BASE%%` token
 * form the fragments use, so a hit is usable verbatim.
 */
function makeAssetResolver(publicDir, assetsDirName) {
  let hosts;
  try {
    hosts = readdirSync(join(publicDir, assetsDirName), { withFileTypes: true })
      .filter((e) => e.isDirectory())
      .map((e) => e.name);
  } catch {
    hosts = [];
  }
  return (relPath) => {
    for (const host of hosts) {
      const hostRoot = join(publicDir, assetsDirName, host);
      for (const candidate of new Set([relPath, safeDecode(relPath)])) {
        const full = join(hostRoot, candidate);
        // Containment, not a blacklist. The first version rejected a literal
        // `..` segment and then ALSO tried the percent-decoded form without
        // re-checking it — so `wp-content/%2e%2e/%2e%2e/etc/passwd` passed the
        // guard, decoded to `../../etc/passwd` and resolved outside the assets
        // tree. Comparing the resolved path against the resolved root cannot be
        // stepped around by an encoding the check did not anticipate.
        if (!isInside(hostRoot, full)) continue;
        if (!existsSync(full)) continue;
        // Emit the path relative to the host root rather than the candidate as
        // written: a reference that stays inside but spells itself `a/../b`
        // would otherwise put `../` into the published URL.
        const clean = relative(hostRoot, full).split(sep).join('/');
        return `%%BASE%%/${assetsDirName}/${host}/${clean}`;
      }
    }
    return null;
  };
}

/** True when `candidate` resolves to `root` itself or something beneath it. */
function isInside(root, candidate) {
  const rootResolved = resolve(root);
  const target = resolve(candidate);
  return target === rootResolved || target.startsWith(rootResolved + sep);
}

/** decodeURIComponent that returns the input rather than throwing on bad input. */
function safeDecode(value) {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

/** `siteConfig.name` from the target repo, or null if it cannot be read. */
function readSiteConfigName(repo) {
  try {
    const src = readFileSync(join(repo, 'src', 'lib', 'site.config.ts'), 'utf8');
    // The first `name:` inside the exported object literal — the type
    // declaration above it has no value to match.
    return /^\s*name:\s*['"]([^'"]+)['"]/m.exec(src)?.[1] ?? null;
  } catch {
    return null;
  }
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

/**
 * Keep the built-output verifier out of the captured assets tree.
 *
 * `scripts/verify-build.mjs` walks every `.html` under `out/` and asserts one
 * `<h1>` and a self-referential canonical on each -- invariants about PAGES.
 * The capture localizes third-party embeds, and some of them are HTML: on
 * FFC-EX-newheightseducation.org an Animoto player lands at
 * `out/_ffc-assets/s3.amazonaws.com/embed.animoto.com/play__...html`, gets
 * audited as if it were a route, and fails both assertions. It is an iframe
 * document belonging to another site. Nothing about it can be fixed, because
 * there is nothing wrong with it.
 *
 * Patched in the target repo rather than worked around here: the assets
 * directory is this pipeline's convention, so the verifier cannot know about
 * it, and every migrated site hits this the moment a page embeds anything.
 *
 * Idempotent, and a hard error when the anchor is missing -- the same rule
 * `ensureEslintIgnoresPublic` follows in `integrate-clone-into-nextjs.mjs`. A
 * verifier this silently failed to patch would keep failing the delivery for a
 * reason no one could act on, which is worse than saying so here.
 */
function scopeVerifyBuildToRoutes(repo, assetsDirName) {
  const path = join(repo, 'scripts', 'verify-build.mjs');
  let src;
  try {
    src = readFileSync(path, 'utf8');
  } catch {
    return { patched: false, reason: 'no scripts/verify-build.mjs in the target repo' };
  }
  const anchor = /(\n(\s*)if \(entry\.isDirectory\(\)\) \{\n)/;
  const m = anchor.exec(src);
  const branchStart = m ? m.index + m[0].length : -1;
  const walkAt = m ? src.indexOf('await walkHtml', branchStart) : -1;
  if (!m || walkAt === -1) {
    throw new Error(
      `[convert] cannot scope ${path} to routes: its directory walk does not match the` +
        ' expected shape. The captured assets tree would be audited as if it were pages,' +
        ' which fails the build on documents belonging to other sites. Update this patch' +
        ' to the verifier the template now ships rather than skipping it.',
    );
  }
  // Is the walk ALREADY scoped? Asked of the region between the directory
  // branch and the recursive call it guards -- not of the file.
  //
  // `src.includes(assetsDirName)` was the first spelling and review was right
  // to call it weak: the directory name can appear in a comment, a constant or
  // an error message while the walk still recurses into that tree, and this
  // would then report `already scoped` and patch nothing. The delivery would
  // fail later, on an embedded player's HTML, with an error naming a file that
  // has nothing to do with the cause. A guard that can answer "yes" about a
  // comment is not reading the code it claims to have checked.
  const escaped = assetsDirName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const branchHead = src.slice(branchStart, walkAt);
  if (new RegExp(`entry\\.name === ['"\`]${escaped}['"\`]`).test(branchHead)) {
    return { patched: false, reason: 'already scoped' };
  }
  const indent = `${m[2]}  `;
  const guard =
    `${indent}// Captured third-party assets, not routes: an embedded player's own\n` +
    `${indent}// HTML has no <h1> and no canonical, and should not have.\n` +
    `${indent}if (entry.name === '${assetsDirName}') continue\n`;
  write(path, src.replace(anchor, `$1${guard}`));
  return { patched: true, skipped: assetsDirName };
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

  // --- leftover HTML ---------------------------------------------------
  // This rule decides whether a conversion may proceed, and it was wrong once
  // in production while living inline in main(), where no self-test could
  // reach it. That is the reason it is a function.
  eq(
    'an unrouted page is counted',
    unroutedHtml(['about/legacy.html', 'index.html'], '_ffc-assets'),
    ['about/legacy.html', 'index.html'],
  );
  eq(
    'a localized asset that serves HTML is NOT counted',
    unroutedHtml(['_ffc-assets/s3.amazonaws.com/embed.animoto.com/play__x.html'], '_ffc-assets'),
    [],
  );
  eq(
    'the real run-35575009432 mix: assets dropped, pages kept',
    unroutedHtml(
      [
        '_ffc-assets/s3.amazonaws.com/embed.animoto.com/play__a.html',
        '_ffc-assets/s3.amazonaws.com/embed.animoto.com/play__b.html',
        'stray-page.html',
      ],
      '_ffc-assets',
    ),
    ['stray-page.html'],
  );
  eq('non-HTML is never counted', unroutedHtml(['_ffc-assets/x.css', 'a.pdf'], '_ffc-assets'), []);
  // Anchored at the ROOT: a captured page that genuinely lives under a
  // directory of that name deeper in the tree is a page, not an asset. A
  // substring test would swallow it.
  eq(
    'the exclusion is anchored, not a substring match',
    unroutedHtml(['deep/_ffc-assets/x.html'], '_ffc-assets'),
    ['deep/_ffc-assets/x.html'],
  );
  eq(
    'a custom --assets-dir is honoured',
    unroutedHtml(['other/x.html', '_ffc-assets/x.html'], 'other'),
    ['_ffc-assets/x.html'],
  );

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

  // `sanitizeSlug` keeps only [a-z0-9-], so a wholly non-Latin or
  // punctuation-only permalink collapses to '' — the front page's slug.
  const cyrillic = assignSlugs(['новости/index.html', 'index.html']);
  eq(
    'a permalink that sanitizes to nothing does not take the root',
    cyrillic.assigned.map((a) => [a.localPath, a.slug]),
    [
      ['index.html', ''],
      ['новости/index.html', 'page'],
    ],
  );
  // Sort order put the Cyrillic entry first, and before the guard it claimed
  // '' and pushed the real front page to '-2'.
  eq(
    'the front page keeps the root whatever sorts before it',
    assignSlugs(['---/index.html', 'index.html']).assigned.find((a) => a.localPath === 'index.html')
      .slug,
    '',
  );
  eq(
    'several such pages get distinct, valid slugs',
    assignSlugs(['новости/index.html', 'статьи/index.html', 'index.html'])
      .assigned.map((a) => a.slug)
      .sort(),
    ['', 'page', 'page-2'],
  );
  // A leading-hyphen folder fails the repo's own kebab-case drift rule.
  eq(
    'no slug starts with a hyphen',
    assignSlugs(['---/index.html', 'новости/index.html', 'index.html']).assigned.some((a) =>
      a.slug.startsWith('-'),
    ),
    false,
  );

  // --- wiring the components this converter generates -------------------
  //
  // Asserted against a real file on disk, not against the source text. The
  // fault this fixes is precisely that the components were generated
  // correctly and rendered nowhere, so a test that reads the converter and
  // finds the right strings is the same kind of evidence that missed it.
  {
    const wd = mkdtempSync(join(tmpdir(), 'ffc-wire-'));
    try {
      const layoutDir = join(wd, 'src', 'app');
      mkdirSync(layoutDir, { recursive: true });
      const layoutPath = join(layoutDir, 'layout.tsx');
      const original = [
        "import Header from './../components/header'",
        "import Footer from './../components/footer'",
        'export default function RootLayout({ children }) {',
        '  return (',
        '    <body>',
        '      <Header />',
        '      <main>{children}</main>',
        '      <Footer />',
        '    </body>',
        '  )',
        '}',
        '',
      ].join('\n');
      writeFileSync(layoutPath, original, 'utf8');

      const first = wireGeneratedComponents(wd);
      const after = readFileSync(layoutPath, 'utf8');
      eq('wire: reports that it changed the layout', first.changed, true);
      eq(
        'wire: the footer import is repointed at the migration footer',
        /import Footer from '\.\/\.\.\/components\/ffc-footer'/.test(after),
        true,
      );
      eq(
        'wire: ...and the marketing footer is no longer imported',
        /components\/footer'/.test(after),
        false,
      );
      eq(
        'wire: the clone runtime is imported',
        /import CloneEnhance from '\.\/\.\.\/components\/clone-enhance'/.test(after),
        true,
      );
      // Imported and not rendered is the exact bug being fixed, so the render
      // is asserted separately from the import.
      eq('wire: ...and RENDERED', /<CloneEnhance \/>/.test(after), true);
      eq('wire: the existing <Footer /> JSX is untouched', /<Footer \/>/.test(after), true);

      // 706 re-runs over a repo it has already converted.
      const second = wireGeneratedComponents(wd);
      eq('wire: a second run changes nothing', second.changed, false);
      eq('wire: ...and does not duplicate the render', after, readFileSync(layoutPath, 'utf8'));
      eq(
        'wire: ...nor the import',
        (readFileSync(layoutPath, 'utf8').match(/clone-enhance/g) || []).length,
        1,
      );

      // A layout that does not match the template shape must be reported, not
      // silently skipped: a WARNING note is how an operator learns the repo
      // needs a hand.
      const odd = mkdtempSync(join(tmpdir(), 'ffc-wire2-'));
      try {
        mkdirSync(join(odd, 'src', 'app'), { recursive: true });
        writeFileSync(
          join(odd, 'src', 'app', 'layout.tsx'),
          'export default function L() {}\n',
          'utf8',
        );
        const r = wireGeneratedComponents(odd);
        eq(
          'wire: an unrecognised layout warns rather than passing silently',
          r.notes.some((n) => n.startsWith('WARNING')),
          true,
        );
        // ...and the warning has to reach the run, which is a separate
        // property: the notes existed from the first version of this step and
        // no caller read them, so the conversion reported success while the
        // components it had just written were imported by nothing.
        const bad = describeWiring(r);
        eq('wire: the warning is what the conversion exits on', bad.warnings.length > 0, true);
        eq('wire: ...and every note is printed, not just the warnings', bad.notes, r.notes);
      } finally {
        rmSync(odd, { recursive: true, force: true });
      }

      // The healthy run has to say so too. A reporter that speaks only on
      // failure leaves "wired" and "the step never ran" identical in the log.
      const good = describeWiring(second);
      eq('wire: a clean re-run still reports a headline', good.headline.length > 0, true);
      eq('wire: ...with no warnings', good.warnings.length, 0);
      eq(
        'wire: ...and names what it found rather than staying silent',
        good.notes.length > 0,
        true,
      );
      // A step that never ran is distinguishable from one that ran cleanly.
      eq(
        'wire: an absent result is reported as not attempted',
        describeWiring(undefined).headline,
        'layout.tsx wiring  not attempted',
      );
      eq('wire: ...and carries no warnings to exit on', describeWiring(undefined).warnings, []);
    } finally {
      rmSync(wd, { recursive: true, force: true });
    }
  }

  // --- the repo shape --------------------------------------------------
  const dir = mkdtempSync(join(tmpdir(), 'ffc-convert-'));
  try {
    mkdirSync(join(dir, '_disabled_template_routes', 'privacy-policy'), { recursive: true });
    mkdirSync(join(dir, '_disabled_template_routes', 'donation-policy'), { recursive: true });
    mkdirSync(join(dir, '_disabled_template_routes', 'about-us'), { recursive: true });
    mkdirSync(join(dir, 'src', 'app', 'about-us'), { recursive: true });
    // THE HUSK: integrate-clone-into-nextjs.mjs parks a route by moving its
    // `page.tsx` file, leaving `src/app/<slug>/` behind empty. This is what the
    // real pipeline hands this function, and reproducing it is the whole point
    // — the pre-#1342 fixture only ever built the `about-us` case below, so the
    // predicate could read "directory exists" as "collision" and still pass.
    mkdirSync(join(dir, 'src', 'app', 'donation-policy'), { recursive: true });
    write(join(dir, '_disabled_template_routes', 'privacy-policy', 'page.tsx'), 'x');
    write(join(dir, '_disabled_template_routes', 'donation-policy', 'page.tsx'), 'the policy');
    write(join(dir, '_disabled_template_routes', 'about-us', 'page.tsx'), 'x');
    write(join(dir, '_disabled_template_routes', 'page.tsx'), 'template home');
    write(join(dir, 'src', 'app', 'about-us', 'page.tsx'), 'the captured page');

    // A NESTED parked tree whose top level is free but whose child the capture
    // owns. The entry-level collision check passes it (no `page.*` directly in
    // `src/app/legal`), so the merge runs and its per-file guard is the only
    // thing standing between the template's `legal/terms` page and the
    // charity's. Without this case that guard is never executed by any test.
    mkdirSync(join(dir, '_disabled_template_routes', 'legal', 'terms'), { recursive: true });
    mkdirSync(join(dir, 'src', 'app', 'legal', 'terms'), { recursive: true });
    write(join(dir, '_disabled_template_routes', 'legal', 'page.tsx'), 'template legal index');
    write(join(dir, '_disabled_template_routes', 'legal', 'terms', 'page.tsx'), 'template terms');
    write(join(dir, 'src', 'app', 'legal', 'terms', 'page.tsx'), 'the captured terms');

    const routes = restoreTemplateRoutes(dir);
    // The footer standard links to these; leaving them parked ships 404s.
    // Sorted: readdir order is filesystem-dependent and is not the property
    // under test.
    eq('a parked template route comes back', [...routes.restored].sort(), [
      'donation-policy',
      'legal',
      'privacy-policy',
    ]);
    eq(
      'a nested template page lands where the capture left room for it',
      readFileSync(join(dir, 'src', 'app', 'legal', 'page.tsx'), 'utf8'),
      'template legal index',
    );
    eq(
      'but a nested page the capture owns is NOT overwritten by the merge',
      readFileSync(join(dir, 'src', 'app', 'legal', 'terms', 'page.tsx'), 'utf8'),
      'the captured terms',
    );
    eq(
      'an EMPTY src/app/<slug> left by integrate is not a collision',
      readFileSync(join(dir, 'src', 'app', 'donation-policy', 'page.tsx'), 'utf8'),
      'the policy',
    );
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
    // The original anchor required single quotes AND a trailing comma, so it
    // matched 1 of these 4 and silently no-op'd on the rest — a conversion that
    // reported success and shipped a site whose own links 404.
    for (const [label, line] of [
      ['single quotes, comma', "  output: 'export',"],
      ['double quotes, comma', '  output: "export",'],
      ['single quotes, no comma', "  output: 'export'"],
      ['double quotes, no comma', '  output: "export"'],
    ]) {
      write(join(dir, 'next.config.ts'), `const nextConfig = {\n${line}\n}\n`);
      eq(`trailingSlash is set for: ${label}`, enableTrailingSlash(dir).changed, true);
      const after = readFileSync(join(dir, 'next.config.ts'), 'utf8');
      eq(`  and the output line keeps its comma: ${label}`, /export['"],/.test(after), true);
      eq(
        `  and trailingSlash lands inside the object: ${label}`,
        /trailingSlash: true,\n\}/.test(after),
        true,
      );
    }
    write(join(dir, 'next.config.ts'), 'const nextConfig = {}\n');
    eq(
      'a config with no static export is left alone rather than guessed at',
      enableTrailingSlash(dir).reason,
      "no `output: 'export'` to anchor to",
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

    // --- the built-output verifier only audits ROUTES --------------------
    // A localized third-party embed is HTML and is not a page: it has no <h1>
    // and no canonical, and should not have. Shaped like the template's own
    // walker, indentation and all, because that is what the patch anchors to.
    mkdirSync(join(dir, 'scripts'), { recursive: true });
    const verifierSrc = [
      'async function walkHtml(dir, results = []) {',
      '  for (const entry of entries) {',
      '    const full = join(dir, entry.name)',
      '    if (entry.isDirectory()) {',
      '      await walkHtml(full, results)',
      "    } else if (entry.name.endsWith('.html')) {",
      '      results.push(full)',
      '    }',
      '  }',
      '  return results',
      '}',
      '',
    ].join('\n');
    write(join(dir, 'scripts', 'verify-build.mjs'), verifierSrc);
    const scoped = scopeVerifyBuildToRoutes(dir, '_ffc-assets');
    const patchedVerifier = readFileSync(join(dir, 'scripts', 'verify-build.mjs'), 'utf8');
    eq('the verifier is patched to skip the captured assets tree', scoped.patched, true);
    eq(
      '...with a guard INSIDE the directory branch, before the walk recurses',
      /if \(entry\.isDirectory\(\)\) \{\n(?:\s*\/\/[^\n]*\n)*\s*if \(entry\.name === '_ffc-assets'\) continue\n\s*await walkHtml/.test(
        patchedVerifier,
      ),
      true,
    );
    eq(
      '...and the walk it guards is still there',
      patchedVerifier.includes('await walkHtml(full, results)'),
      true,
    );
    eq('a second run is a no-op', scopeVerifyBuildToRoutes(dir, '_ffc-assets').patched, false);
    // Caught, because the mutation this case exists to detect -- rethrowing
    // instead of reporting -- makes the call THROW, and a throw here kills the
    // run before the harness prints anything. A crashed self-test is not a
    // detection, so the case would be satisfied by the very defect it names.
    // The case the weak predicate got wrong: the directory name appears in the
    // file, but the walk still recurses into that tree. Reporting "already
    // scoped" here patches nothing and fails the delivery later on an embedded
    // player's HTML, naming a file that has nothing to do with the cause.
    write(
      join(dir, 'scripts', 'verify-build.mjs'),
      verifierSrc.replace(
        'async function walkHtml',
        "// Assets captured under _ffc-assets are copied verbatim.\nconst NOTE = '_ffc-assets'\nasync function walkHtml",
      ),
    );
    const mentioned = scopeVerifyBuildToRoutes(dir, '_ffc-assets');
    eq('a verifier that merely MENTIONS the assets dir is still patched', mentioned.patched, true);
    // A SECOND directory walk, after `walkHtml`, that DOES skip the assets
    // tree -- while the walk that matters does not. This is what makes the
    // region narrowing testable rather than merely sensible: the string
    // `entry.name === '_ffc-assets'` is genuinely in the file, so a check that
    // reads the whole file calls this scoped and patches nothing.
    write(
      join(dir, 'scripts', 'verify-build.mjs'),
      `${verifierSrc}\nasync function walkAssets(dir) {\n` +
        '  for (const entry of entries) {\n' +
        '    if (entry.isDirectory()) {\n' +
        "      if (entry.name === '_ffc-assets') continue\n" +
        '      await walkAssets(join(dir, entry.name))\n' +
        '    }\n  }\n}\n',
    );
    eq(
      "another WALK's guard does not count as this one",
      scopeVerifyBuildToRoutes(dir, '_ffc-assets').patched,
      true,
    );
    write(join(dir, 'scripts', 'verify-build.mjs'), verifierSrc);
    scopeVerifyBuildToRoutes(dir, '_ffc-assets');
    eq(
      '...and patching it twice is still a no-op',
      scopeVerifyBuildToRoutes(dir, '_ffc-assets').patched,
      false,
    );
    // A guard on the WRONG directory is not this one, and must not count.
    write(
      join(dir, 'scripts', 'verify-build.mjs'),
      verifierSrc.replace(
        '      await walkHtml(full, results)',
        "      if (entry.name === 'node_modules') continue\n      await walkHtml(full, results)",
      ),
    );
    eq(
      "another directory's guard does not count as this one",
      scopeVerifyBuildToRoutes(dir, '_ffc-assets').patched,
      true,
    );
    // A walk whose branch never recurses is not the shape this patch anchors
    // to, and guessing where the guard belongs is how it lands somewhere that
    // never runs.
    write(
      join(dir, 'scripts', 'verify-build.mjs'),
      verifierSrc.replace('      await walkHtml(full, results)', '      results.push(full)'),
    );
    eq(
      'a directory branch that never recurses is refused, not guessed at',
      (() => {
        try {
          scopeVerifyBuildToRoutes(dir, '_ffc-assets');
          return 'NO THROW';
        } catch (err) {
          return /does not match the expected shape/.test(err.message) ? 'refused' : err.message;
        }
      })(),
      'refused',
    );
    eq(
      'a repo with no verifier is reported, not crashed on',
      (() => {
        try {
          return scopeVerifyBuildToRoutes(join(dir, 'nowhere'), '_ffc-assets').reason;
        } catch {
          return 'THREW';
        }
      })(),
      'no scripts/verify-build.mjs in the target repo',
    );
    // A verifier whose walk this patch no longer recognises is a hard error.
    // Reported as "silently skipped" it would fail every later delivery at a
    // step naming an embedded video, which is unactionable.
    write(join(dir, 'scripts', 'verify-build.mjs'), 'export const nothing = 1\n');
    eq(
      'an unrecognised verifier is refused, not silently left unpatched',
      (() => {
        try {
          scopeVerifyBuildToRoutes(dir, '_ffc-assets');
          return 'NO THROW';
        } catch (err) {
          return /does not match the expected shape/.test(err.message) ? 'refused' : err.message;
        }
      })(),
      'refused',
    );

    // --- the asset resolver cannot be walked out of ----------------------
    // Verified against a real filesystem: a resolver that only rejects a
    // LITERAL `..` still decodes `%2e%2e` afterwards, and `join` then escapes
    // the assets tree — measured at /repo/public/etc/passwd before this.
    mkdirSync(join(dir, 'public', '_ffc-assets', 'site.org', 'wp-content'), { recursive: true });
    write(join(dir, 'public', '_ffc-assets', 'site.org', 'wp-content', 'a.pdf'), 'x');
    write(join(dir, 'public', 'secret.txt'), 'not an asset');
    const resolveAsset = makeAssetResolver(join(dir, 'public'), '_ffc-assets');

    eq(
      'a captured file resolves to its token path',
      resolveAsset('wp-content/a.pdf'),
      '%%BASE%%/_ffc-assets/site.org/wp-content/a.pdf',
    );
    eq(
      'a file that was never captured resolves to nothing',
      resolveAsset('wp-content/nope.pdf'),
      null,
    );
    // Depth 3, because that is what actually reaches public/ from
    // _ffc-assets/site.org/wp-content. The first draft of these cases used 2
    // and passed against the OLD implementation too — proving nothing. The
    // old resolver at depth 3 returns
    // "%%BASE%%/_ffc-assets/site.org/wp-content/../../../secret.txt".
    eq(
      'a literal traversal cannot reach outside the assets tree',
      resolveAsset('wp-content/../../../secret.txt'),
      null,
    );
    // The gap Copilot found: the raw form has no `..`, so a blacklist passes
    // it, and only the decoded form escapes.
    eq(
      'a percent-encoded traversal cannot either',
      resolveAsset('wp-content/%2e%2e/%2e%2e/%2e%2e/secret.txt'),
      null,
    );
    eq(
      'nor a mixed-case percent-encoded one',
      resolveAsset('wp-content/%2E%2E/%2E%2E/%2E%2E/secret.txt'),
      null,
    );
    // Inside the tree but spelled with a dot segment: allowed, and normalised
    // so the published URL never carries `../`.
    eq(
      'a dotted path that stays inside is normalised, not echoed',
      resolveAsset('wp-content/../wp-content/a.pdf'),
      '%%BASE%%/_ffc-assets/site.org/wp-content/a.pdf',
    );

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
