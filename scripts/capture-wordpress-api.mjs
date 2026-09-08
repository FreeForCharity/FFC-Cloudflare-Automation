#!/usr/bin/env node
/**
 * capture-wordpress-api.mjs — capture a live WordPress site from its own REST
 * API plus rendered-HTML scraping.
 *
 * Why this exists alongside clone-site-static.mjs (httrack):
 *
 *   httrack discovers pages by FOLLOWING LINKS. Anything the theme does not
 *   link to — an orphaned landing page, a page reachable only from a menu the
 *   mobile breakpoint hides, a post past the blog pagination depth — is simply
 *   not in the mirror, and nothing in the output says so. The mirror's page
 *   count is an artifact of the theme's navigation, not of the site.
 *
 *   WordPress already knows the answer. /wp-json/wp/v2/pages returns every
 *   published page with an `X-WP-Total` header, so the inventory is the CMS's
 *   own count rather than a crawl's fixed point. That makes completeness
 *   CHECKABLE: captured N of X-WP-Total M is an assertion the run can fail on.
 *
 * ...and why the REST API is not sufficient on its own:
 *
 *   `content.rendered` is the POST BODY only. It carries no theme chrome — no
 *   header, nav, footer, or the CSS that makes any of it look like the site.
 *   Page builders (Elementor, Divi, WPBakery) are worse: they keep their real
 *   markup in postmeta and `content.rendered` is a placeholder or a shortcode
 *   husk. A REST-only capture of an Elementor site reproduces almost nothing.
 *
 * So: the REST API supplies the URL INVENTORY (authoritative, complete,
 * countable) and an HTTP fetch of each of those URLs supplies the RENDERED
 * MARKUP (faithful, theme-complete). Neither half is optional.
 *
 * A cPanel/FTP backup is deliberately not a source here. It hands you PHP,
 * a database dump and a plugin tree — the inputs to a rendering engine that
 * will not exist after the migration — rather than the rendered artifact that
 * is actually being migrated. Nothing downstream of this script can run PHP.
 *
 * Usage:
 *   node scripts/capture-wordpress-api.mjs --domain <domain> --inspect
 *   node scripts/capture-wordpress-api.mjs --domain <domain> --out <dir> \
 *        [--max 500] [--delay 250] [--include-posts] [--timeout 30]
 *   node scripts/capture-wordpress-api.mjs --self-test
 *
 * Exit codes:
 *   0  success (inspect: the API is usable; capture: every gate passed)
 *   1  the site or its API could not be used (inspect verdict NOT ok, or a
 *      capture gate failed — incomplete inventory, unlocalized assets)
 *   2  invalid usage / self-test failure / crash
 */

import { mkdirSync, writeFileSync, readFileSync, existsSync } from 'node:fs';
import { join, dirname, extname, resolve as resolvePath, sep } from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';

const UA = 'Mozilla/5.0 (FFC static-capture bot; +https://freeforcharity.org)';

// ---------------------------------------------------------------------------
// Pure logic. Everything below the NETWORK banner does I/O; everything above
// it is exercised by --self-test, which is why the split is worth keeping.
// ---------------------------------------------------------------------------

/**
 * Reduce whatever the operator typed to a bare apex-ish host.
 * Accepts `https://www.x.org/path`, `www.x.org`, `x.org/`.
 */
export function normalizeDomain(input) {
  if (typeof input !== 'string') return '';
  let d = input.trim().toLowerCase();
  d = d.replace(/^[a-z][a-z0-9+.-]*:\/\//, ''); // scheme
  d = d.replace(/^www\./, '');
  d = d.split('/')[0].split('?')[0].split('#')[0];
  d = d.replace(/:\d+$/, ''); // port
  return d;
}

/**
 * REST root candidates, in preference order.
 *
 * Three flavors, because "WordPress" is three different hosting shapes and only
 * the first is the one everyone pictures:
 *
 *   pretty — self-hosted, pretty permalinks: https://site/wp-json/
 *   query  — self-hosted, plain permalinks or a host that rewrites /wp-json/
 *            away: https://site/?rest_route=/
 *   dotcom — WordPress.com-hosted (and Jetpack-connected) sites, which serve
 *            404 on their OWN /wp-json/ and expose the same wp/v2 collections
 *            from public-api.wordpress.com instead.
 *
 * The third one is not an edge case, and omitting it is actively misleading:
 * vpmin.org answers 200 with `<meta name="generator" content="WordPress.com">`
 * and 404 on /wp-json/, so a two-candidate probe reports NO_REST_API for a site
 * whose full REST inventory is one URL away. That false negative is expensive
 * in the wrong direction — it sends the operator looking for a cPanel/FTP
 * backup of a site that has no cPanel behind it at all.
 */
export function restRootCandidates(domain) {
  return [
    { kind: 'pretty', indexUrl: `https://${domain}/wp-json/` },
    { kind: 'query', indexUrl: `https://${domain}/?rest_route=/` },
    { kind: 'dotcom', indexUrl: `https://public-api.wordpress.com/wp/v2/sites/${domain}` },
  ];
}

/**
 * Build a wp/v2 collection URL for a given REST flavor.
 *
 * Each flavor puts the `wp/v2` segment somewhere different, and the dotcom form
 * has it before the site rather than after — so this cannot be one template
 * with a variable prefix.
 */
export function collectionUrlFor(kind, domain, collection, params = {}) {
  const qs = new URLSearchParams(params).toString();
  const suffix = qs ? `?${qs}` : '';
  if (kind === 'query') {
    const q = qs ? `&${qs}` : '';
    return `https://${domain}/?rest_route=/wp/v2/${collection}${q}`;
  }
  if (kind === 'dotcom') {
    return `https://public-api.wordpress.com/wp/v2/sites/${domain}/${collection}${suffix}`;
  }
  return `https://${domain}/wp-json/wp/v2/${collection}${suffix}`;
}

/**
 * URLs out of a sitemap, and whether this document is an INDEX of other
 * sitemaps rather than a list of pages.
 *
 * This is the second, independent inventory source, and it exists because the
 * first one can under-report without saying so. Measured on vpmin.org: the
 * WordPress.com public API answers 200 for `pages` but 401 for `media` — proof
 * that this endpoint restricts per collection on a live FFC site. If `pages`
 * were ever restricted the same way, "captured 1 of 1 reported" would be a
 * green gate over a site with pages missing, which is the precise failure the
 * X-WP-Total check was introduced to prevent. A gate that can only compare a
 * number against itself is not a gate.
 */
export function parseSitemapUrls(xml) {
  const isIndex = /<sitemapindex[\s>]/i.test(xml);
  const urls = [];
  for (const m of xml.matchAll(/<loc>\s*([^<\s]+)\s*<\/loc>/gi)) {
    urls.push(m[1].replace(/&amp;/g, '&').trim());
  }
  return { isIndex, urls };
}

/**
 * Sitemap entries that are plausibly capturable HTML pages on this site.
 * Drops other hosts, and drops feed/asset URLs a static capture has no use for.
 */
export function sitemapPageUrls(urls, domain) {
  return urls.filter((u) => {
    let parsed;
    try {
      parsed = new URL(u);
    } catch {
      return false;
    }
    const host = parsed.hostname.replace(/^www\./, '');
    if (host !== domain && !host.endsWith(`.${domain}`)) return false;
    if (/\.(xml|xsl|json|jpe?g|png|gif|webp|svg|pdf|css|js)$/i.test(parsed.pathname)) return false;
    if (/\/(feed|comments)\/?$/i.test(parsed.pathname)) return false;
    return true;
  });
}

/**
 * Choose which probed candidate the run should report, given every outcome
 * that was actually observed.
 *
 * Preference order is `ok` > `blocked` > `unreachable` > nothing, and the last
 * two matter more than they look. The resolver previously handled only `ok`
 * and `blocked` and fell through to a default of `absent`, so a candidate that
 * could not be reached at all — DNS failure, TLS failure, timeout — was
 * reported as "this is not WordPress". That is the exact collapse
 * `classifyRestIndex` is three-valued to prevent: "not WordPress" sends the
 * operator to a different capture path, while "unreachable" sends them to
 * check the network or the host. Returns null when nothing better than
 * `absent` was seen.
 */
export function pickRestOutcome(observed) {
  for (const wanted of ['ok', 'blocked', 'unreachable']) {
    const hit = observed.find((o) => o && o.verdict === wanted);
    if (hit) return hit;
  }
  return null;
}

/**
 * Turn a REST index response into a verdict.
 *
 * Deliberately three-valued. "not 200" collapses two states that need
 * different human responses: a 401/403 is WordPress with the API locked down
 * (a plugin, or a WAF) and is worth an operator conversation, whereas a 404 is
 * "this is not WordPress" and means picking a different capture path entirely.
 * Reporting both as "failed" sends the operator to the wrong remedy.
 */
export function classifyRestIndex(status, body) {
  if (status === 200) {
    const looksWp =
      body &&
      typeof body === 'object' &&
      (body.namespaces || body.routes || body.name !== undefined);
    return looksWp ? 'ok' : 'absent';
  }
  if (status === 401 || status === 403 || status === 405 || status === 429) return 'blocked';
  if (status === 0) return 'unreachable';
  return 'absent';
}

/**
 * Map a live page URL to the local path its captured HTML belongs at.
 * `https://x.org/about-us/` -> `about-us/index.html`; the home page -> `index.html`.
 */
export function localPathForLink(link, domain) {
  let path;
  try {
    path = new URL(link).pathname;
  } catch {
    return null;
  }
  path = path.replace(/^\/+/, '').replace(/\/+$/, '');
  if (path === '') return 'index.html';
  // A link that already names a file keeps its name; a directory-style link
  // becomes <dir>/index.html so a static host serves it at the same URL.
  if (/\.[a-z0-9]{2,5}$/i.test(path)) return path;
  return `${path}/index.html`;
}

/**
 * Would writing `relative` under `root` stay inside `root`?
 *
 * Every path this script writes is derived from a URL found in someone else's
 * markup, so containment must be ENFORCED rather than inferred. As it happens
 * the obvious traversal vectors do not survive `new URL()`: it normalises
 * `/../../etc/passwd` and `/%2e%2e/%2e%2e/etc/passwd` both to `/etc/passwd`,
 * and `..%2f..%2f` survives only as one literal directory name that `join`
 * does not treat as traversal. Measured, not assumed — and that is exactly why
 * this guard is worth having: it is a property of WHATWG URL parsing today,
 * held in a dependency this script does not control, and the cost of pinning
 * it here is one comparison per write.
 */
export function isContainedPath(root, relative) {
  const base = resolvePath(root);
  const target = resolvePath(base, relative);
  return target === base || target.startsWith(base + sep);
}

/** Depth of a local path, for building a relative prefix back to the root. */
export function relativePrefix(localPath) {
  const depth = localPath.split('/').length - 1;
  return depth === 0 ? './' : '../'.repeat(depth);
}

/**
 * Every asset URL referenced by a chunk of HTML: src, poster and data-src;
 * asset-bearing <link rel>; srcset candidates; og:image / twitter:image meta
 * content; and url() inside <style> blocks and style attributes.
 */
export function collectAssetUrls(html) {
  const urls = new Set();
  const push = (u) => {
    if (!u) return;
    // `&amp;` is how a multi-parameter URL is spelled in an HTML attribute, so
    // the real URL is the decoded one. Fetching the literal `&amp;` asks the
    // origin for a query string it does not have — measured on this migration:
    // `bilmur.min.js?i=17&amp;m=202636` was requested, and reported, with the
    // entity still in it. WordPress emits the numeric form as well.
    const t = u.trim().replace(/&(?:amp|#0*38|#x0*26);/gi, '&');
    if (
      !t ||
      t.startsWith('data:') ||
      t.startsWith('#') ||
      t.startsWith('mailto:') ||
      t.startsWith('tel:')
    )
      return;
    urls.add(t);
  };

  // src= / poster= — always an asset.
  for (const m of html.matchAll(/\b(?:src|poster|data-src)\s*=\s*["']([^"']+)["']/gi)) push(m[1]);

  // href= — only when the rel says it is an asset, never a navigation link.
  for (const m of html.matchAll(/<link\b[^>]*>/gi)) {
    const tag = m[0];
    const rel = /\brel\s*=\s*["']([^"']+)["']/i.exec(tag)?.[1]?.toLowerCase() ?? '';
    if (!/stylesheet|icon|preload|apple-touch-icon|manifest/.test(rel)) continue;
    const href = /\bhref\s*=\s*["']([^"']+)["']/i.exec(tag)?.[1];
    push(href);
  }

  // srcset / imagesrcset — comma-separated "<url> <descriptor>" candidates.
  // Splitting on a bare comma would cut data: URIs and any URL carrying one,
  // so the split has to require the comma be followed by a URL-ish token.
  for (const m of html.matchAll(/\b(?:srcset|imagesrcset|data-srcset)\s*=\s*["']([^"']+)["']/gi)) {
    for (const cand of m[1].split(/,(?=\s*[^\s,]+\s*(?:[\d.]+[wx])?\s*(?:,|$))/)) {
      push(cand.trim().split(/\s+/)[0]);
    }
  }

  // URLs inside inline <script> config blocks, which live in no attribute and
  // which every attribute-based scan therefore misses. WordPress emits
  //
  //   window._wpemojiSettings = {"source":{"concatemoji":"https:\/\/site\/wp-includes\/js\/wp-emoji-release.min.js?ver=…"}}
  //
  // and the browser loads that file at runtime. Measured on the fourth delivery
  // of viewpointministriesinternational.org: it was the ONE asset the source
  // still served (HTTP 200) that the clone had lost — the self-containment gate
  // caught it on 2 of 120 pages, which is exactly the class of defect the gate
  // exists for and no attribute scan can see.
  //
  // Restricted to strings that look like an asset reference, so a script's
  // ordinary string literals are not dragged in as URLs.
  for (const m of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)) {
    const body = m[1];
    if (!body) continue;
    for (const lit of body.matchAll(/["'`]([^"'`\s\\]*(?:\\.[^"'`\s\\]*)*)["'`]/g)) {
      // JSON inside HTML escapes its slashes: "https:\/\/host\/path".
      const raw = lit[1].replace(/\\\//g, '/');
      if (
        /\.(?:css|js|mjs|png|jpe?g|gif|svg|webp|avif|ico|woff2?|ttf|eot|mp4|webm)(?:[?#]|$)/i.test(
          raw,
        )
      ) {
        push(raw);
      }
    }
  }

  // Social preview images. These are real assets the page references, and
  // after the migration they would otherwise keep pointing at the host being
  // decommissioned. Restricted to the image properties: a blanket `content=`
  // scan would drag in every description and viewport string on the page.
  for (const m of html.matchAll(/<meta\b[^>]*>/gi)) {
    const tag = m[0];
    const key = /\b(?:property|name)\s*=\s*["']([^"']+)["']/i.exec(tag)?.[1]?.toLowerCase() ?? '';
    if (!/^(og:image(:secure_url|:url)?|twitter:image(:src)?)$/.test(key)) continue;
    const content = /\bcontent\s*=\s*["']([^"']+)["']/i.exec(tag)?.[1];
    if (looksLikeAssetRef(content)) push(content);
  }

  // Inline style="background:url(...)" and <style> blocks ONLY.
  //
  // Scanning the whole document for `url(` is what the first live capture did,
  // and it is wrong for a reason that is invisible until it runs: the pattern
  // is case-insensitive, so it also matches JavaScript's `new URL(r)` inside
  // any minified script on the page. The vpmin.org run turned two such matches
  // into fetches of https://vpmin.org/r and https://vpmin.org/about/r, both
  // 404, and failed its own gate on them. A page's <script> tags are not a
  // place asset references live; its CSS is.
  for (const m of html.matchAll(/<style\b[^>]*>([\s\S]*?)<\/style>/gi)) {
    for (const u of collectCssUrls(m[1])) push(u);
  }
  for (const m of html.matchAll(/\bstyle\s*=\s*"([^"]*)"/gi)) {
    for (const u of collectCssUrls(m[1])) push(u);
  }
  for (const m of html.matchAll(/\bstyle\s*=\s*'([^']*)'/gi)) {
    for (const u of collectCssUrls(m[1])) push(u);
  }

  return [...urls];
}

/**
 * Could this string be a reference to a file?
 *
 * A CSS `url()` argument always names a path, so it carries a slash, an
 * extension, or a scheme. A bare identifier does not — and a bare identifier is
 * exactly what a mis-scanned `URL(r)` yields. Rejecting those here means a
 * false match costs nothing instead of costing a 404 and a failed gate.
 */
export function looksLikeAssetRef(value) {
  if (!value) return false;
  const v = value.trim();
  if (!v || v.startsWith('data:') || v.startsWith('#')) return false;
  if (v.startsWith('/') || v.startsWith('./') || v.startsWith('../')) return true;
  if (/^https?:\/\//i.test(v)) return true;
  if (/^[a-z][a-z0-9+.-]*:/i.test(v)) return false; // some other scheme — not ours to fetch
  return v.includes('/') || /\.[a-z0-9]{2,5}([?#]|$)/i.test(v);
}

/** Asset URLs referenced from inside a stylesheet. */
export function collectCssUrls(css) {
  const urls = new Set();
  for (const m of css.matchAll(/url\(\s*["']?([^"')]+)["']?\s*\)/gi)) {
    const u = m[1].trim();
    if (looksLikeAssetRef(u)) urls.add(u);
  }
  for (const m of css.matchAll(/@import\s+(?:url\()?\s*["']([^"']+)["']/gi)) {
    const u = m[1].trim();
    if (looksLikeAssetRef(u)) urls.add(u);
  }
  return [...urls];
}

/**
 * Absolute-ise a possibly-relative reference against the page it appeared on.
 * Returns null for anything that is not http(s) once resolved.
 */
export function absolutize(ref, baseUrl) {
  try {
    const u = new URL(ref, baseUrl);
    if (u.protocol !== 'http:' && u.protocol !== 'https:') return null;
    return u.toString();
  } catch {
    return null;
  }
}

/**
 * Should this absolute asset URL be pulled local?
 *
 * Same-site assets always. Off-site assets only when they are the kind of thing
 * a page LOADS (fonts, images, CSS, JS on a CDN) — an <a> to another charity is
 * a link and stays external. Social/video embeds stay external by design: they
 * are third-party runtime services, not page assets, and localizing them breaks
 * them.
 */
export function shouldLocalize(absUrl, domain, ignoreHosts = []) {
  let u;
  try {
    u = new URL(absUrl);
  } catch {
    return false;
  }
  const host = u.hostname.replace(/^www\./, '');
  // Never fetch into a private network on a page's say-so, even for the site
  // being captured — a same-domain hostname resolving to a private literal is
  // still a request the runner makes on someone else's behalf.
  if (isPrivateHost(u.hostname)) return false;
  // The ignore list wins over every other rule, including same-site: a
  // reference to a host that serves nothing is not an asset.
  if (isIgnoredHost(absUrl, ignoreHosts)) return false;
  if (host === domain || host.endsWith(`.${domain}`)) return true;

  const KEEP_EXTERNAL = [
    'youtube.com',
    'youtu.be',
    'vimeo.com',
    'facebook.com',
    'instagram.com',
    'twitter.com',
    'x.com',
    'linkedin.com',
    'givebutter.com',
    'donorbox.org',
    'paypal.com',
    'google.com',
    'googletagmanager.com',
    'google-analytics.com',
  ];
  if (KEEP_EXTERNAL.some((k) => host === k || host.endsWith(`.${k}`))) return false;

  // Known asset providers, plus anything with an asset-ish extension.
  const ASSET_HOSTS = [
    'fonts.googleapis.com',
    'fonts.gstatic.com',
    'gstatic.com',
    'gravatar.com',
    // WordPress.com / Jetpack asset CDNs. A .com-hosted site serves nearly
    // every image from <site>.files.wordpress.com and i0/i1/i2.wp.com, and its
    // core CSS/JS from s0.wp.com and s.w.org — so without these a .com capture
    // localizes almost nothing and still reports success on the page fetches.
    'wp.com',
    'files.wordpress.com',
    'w.org',
    'cloudfront.net',
    'amazonaws.com',
    'cdnjs.cloudflare.com',
    'jsdelivr.net',
    'unpkg.com',
    'bootstrapcdn.com',
  ];
  if (ASSET_HOSTS.some((h) => host === h || host.endsWith(`.${h}`))) return true;

  return /\.(css|js|png|jpe?g|gif|webp|avif|svg|ico|woff2?|ttf|eot|otf|mp4|webm|mp3|pdf)(\?|$)/i.test(
    u.pathname + u.search,
  );
}

/**
 * Hosts this script must never fetch, whatever a page asks for.
 *
 * Every URL fetched here comes from markup on a site named by whoever
 * dispatched the workflow, and the fetch happens inside a CI runner. A page
 * that references `http://169.254.169.254/…` or `http://10.0.0.5/x.png` would
 * otherwise have the runner reach into its own network on that page's behalf.
 * Nothing sensitive is loaded into this job, so the exposure is small — but the
 * cost of refusing is one comparison, and "small" is not a property that
 * survives someone reusing this script somewhere else.
 */
export function isPrivateHost(hostname) {
  if (!hostname) return true;
  const h = String(hostname)
    .toLowerCase()
    .replace(/^\[|\]$/g, '');
  if (
    h === 'localhost' ||
    h.endsWith('.localhost') ||
    h.endsWith('.local') ||
    h.endsWith('.internal')
  )
    return true;
  // Any IPv6 literal, which includes ::1 and the unique-local fc00::/7 range.
  // Public IPv6 assets are rare enough that refusing the whole family costs
  // nothing next to enumerating its private ranges correctly.
  if (h.includes(':')) return true;
  const m = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(h);
  if (!m) return false;
  const [a, b] = [Number(m[1]), Number(m[2])];
  if ([a, Number(m[2]), Number(m[3]), Number(m[4])].some((n) => n > 255)) return true;
  if (a === 0 || a === 127 || a === 10) return true; // this-network, loopback, private
  if (a === 169 && b === 254) return true; // link-local, incl. cloud metadata
  if (a === 172 && b >= 16 && b <= 31) return true; // private
  if (a === 192 && b === 168) return true; // private
  if (a === 100 && b >= 64 && b <= 127) return true; // carrier-grade NAT
  return false;
}

/**
 * Same-site page links actually present in this page's markup.
 *
 * The rewrite pass used to add a replacement for EVERY entry in the inventory
 * to EVERY page, and `rewriteRefs` does a split/join over the whole document
 * per pair. On the 590-entry site that is ~1,180 full-document passes per page
 * across 590 pages — work quadratic in the size of the site, for a set of
 * links of which any one page references a handful. Reading the page's own
 * hrefs once and rewriting only those is linear in the document.
 */
export function collectPageLinks(html) {
  const links = new Set();
  for (const m of html.matchAll(/\bhref\s*=\s*["']([^"']+)["']/gi)) links.add(m[1].trim());
  return links;
}

/**
 * Parse a positive-integer CLI option, or return null when it is not one.
 *
 * `parseInt` answers NaN for junk, and NaN is silently catastrophic here rather
 * than loud: `setTimeout(fn, NaN)` fires immediately, so every request would
 * abort instantly, and `items.length < NaN` is false, so a collection would
 * paginate zero times and report an empty site as complete. Both look like
 * findings about the site rather than about the arguments.
 */
/**
 * How long to wait between ASSET downloads.
 *
 * This used to be `Math.min(delayMs, 100)` — a cap that quietly took the
 * politeness control away from the operator for most of the crawl. Assets are
 * the bulk of the traffic (1,838 of ~2,430 requests on this repo's first real
 * conversion), so `--delay 750` still hammered the origin at 10 requests a
 * second and the setting could not be used to slow anything down.
 *
 * Measured consequence: three full crawls of a charity's site inside an hour
 * got the runner blocked by the site's own security plugin (REST index
 * answering 401/403/429), which is a live-site effect caused entirely by our
 * request rate. The delay the operator asks for is now the delay they get.
 */
export function assetSleepMs(delayMs) {
  const n = Number(delayMs);
  return Number.isFinite(n) && n > 0 ? n : 0;
}

export function parsePositiveInt(raw, { min = 0, max = Number.MAX_SAFE_INTEGER } = {}) {
  if (raw === undefined || raw === null || String(raw).trim() === '') return null;
  if (!/^\d+$/.test(String(raw).trim())) return null;
  const n = Number(String(raw).trim());
  if (!Number.isFinite(n) || n < min || n > max) return null;
  return n;
}

/**
 * Should references to this host be dropped from the capture entirely?
 *
 * viewpointministriesinternational.org emits its Divi cache stylesheets as
 * `https://vpmin.org/wp-content/et-cache/...`. vpmin.org is a blank, unrelated
 * domain that serves none of them, so those URLs are dead on the live site and
 * would be dead in the clone. They are not assets to fetch, not failures to
 * report, and not an "unlocalized external host" to fail the gate on — they are
 * references to nothing.
 *
 * Dropping them is deliberate and reported, never silent: the run prints how
 * many references it ignored and on which host, so an ignore list that is
 * quietly swallowing something real stays visible.
 */
/**
 * The host a WordPress declares as its OWN home, when that is not the host
 * actually serving it.
 *
 * WordPress writes `siteurl`/`home` into every self-referential URL it emits:
 * `wp/v2` `link` fields, sitemap entries, and the `/wp-content/uploads/` paths
 * in rendered markup. If those options still name a domain that no longer
 * serves the site — a rename, a staging-to-production move, a domain bought
 * ahead of a migration — the CMS keeps insisting it lives somewhere it does
 * not, and every URL it hands out 404s.
 *
 * This is not an "alias" and not a second site to capture. It is one site with
 * a stale self-reference, and it is diagnosable from the REST index rather
 * than from an operator's guess, which is why nothing here takes a parameter
 * naming the wrong host.
 *
 * Measured on viewpointministriesinternational.org (2026-08-31): the REST
 * index reports `url` and `home` as `https://vpmin.org`, all 19 `wp/v2/pages`
 * links are on vpmin.org, and every one of them 404s there while the same path
 * returns 200 on the serving domain — 11/11 probed, pages and uploads alike.
 * Left uncorrected that cost 247 of 590 pages and 822 of 823 assets.
 *
 * Returns null when the declared home agrees with the capture domain, which is
 * the normal case and must stay a no-op.
 */
export function declaredSelfHost(restIndex, domain) {
  for (const field of ['home', 'url']) {
    const value = restIndex?.[field];
    if (typeof value !== 'string' || !value) continue;
    let host;
    try {
      host = new URL(value).hostname.replace(/^www\./, '');
    } catch {
      continue;
    }
    if (!host) continue;
    if (host === domain || host.endsWith(`.${domain}`)) continue;
    return host;
  }
  return null;
}

/**
 * Rewrite a stale self-reference onto the host that actually serves the site.
 *
 * Only `selfHost` is rewritten, and only ever TO `domain` — the one host the
 * operator named. So this can never widen what the run fetches: a page cannot
 * steer the capture at a third party by declaring one, and the worst case for
 * a site whose declared home is genuinely a different system is the 404 it was
 * already getting.
 */
export function normalizeSelfHost(absUrl, selfHost, domain) {
  // A fast path, not the guard: with selfHost null the hostname comparison
  // below can never match, so correctness does not depend on this line.
  if (!selfHost) return absUrl;
  let u;
  try {
    u = new URL(absUrl);
  } catch {
    return absUrl;
  }
  if (u.hostname.replace(/^www\./, '') !== selfHost) return absUrl;
  u.hostname = domain;
  u.port = '';
  return u.toString();
}

/**
 * Is this URL served by the site being captured?
 *
 * `redirect: 'follow'` means a 200 says nothing about WHOSE page came back. A
 * WordPress whose `home` option names a domain it does not serve answers its
 * own root with a canonical redirect to that domain — and if something else
 * lives there, the capture stores a stranger's page under the charity's URL
 * with every gate green. Measured on this repo's first real conversion: the
 * source's `/` redirected off-site and `public/index.html` shipped as a parked
 * WordPress.com landing page while 588 other pages were correct.
 *
 * `www.` is folded and subdomains count, matching `declaredSelfHost`.
 */
export function isSiteHost(absUrl, domain) {
  let u;
  try {
    u = new URL(absUrl);
  } catch {
    return false;
  }
  if (u.protocol !== 'http:' && u.protocol !== 'https:') return false;
  const host = u.hostname.replace(/^www\./, '');
  return host === domain || host.endsWith(`.${domain}`);
}

/**
 * What the scrape loop should do with one page response.
 *
 * Extracted so the decision is testable on its own: the loop that used to hold
 * it inline stored anything with `status === 200`, and the one case that
 * mattered — a 200 belonging to a different site — is invisible to every
 * status check. Mirrors `classifyMissing` in verify-no-legacy.mjs.
 */
export function classifyPageResponse({ status = 0, finalUrl = '', requestUrl = '', domain = '' }) {
  if (status !== 200) return { action: 'skip', status, reason: `HTTP ${status}` };
  const landed = finalUrl || requestUrl;
  if (!isSiteHost(landed, domain))
    return {
      action: 'skip',
      status: -2,
      offSite: true,
      finalUrl: landed,
      reason: `followed off-site to ${landed}`,
    };
  return { action: 'store', status: 200 };
}

/** A same-site link key: absolute, stale-host normalized, trailing slash dropped. */
export function linkKey(absUrl) {
  if (typeof absUrl !== 'string' || !absUrl) return null;
  return absUrl.endsWith('/') ? absUrl.slice(0, -1) : absUrl;
}

/**
 * This page's hrefs, indexed by the canonical URL each one resolves to.
 *
 * The rewrite that keeps navigation inside the clone used to compare an
 * entry's link against the page's raw href STRINGS. Those two are written in
 * different alphabets whenever the site declares a stale `home`: the entry
 * list is normalized onto the serving host, while the markup still says the
 * stale one, so `present.has(e.link)` was false for every link on the site and
 * not one was rewritten. 562 of 589 delivered pages navigated to the dead
 * domain, and no gate could see it — the links resolve today, and a nav href
 * is not a subresource, so nothing fetches it at page load.
 *
 * Indexing by the RESOLVED url compares like with like, and folds in the two
 * other spellings of the same destination for free: a relative href, and a
 * root-absolute one (which would otherwise survive into a project-pages
 * deploy, where the site is mounted under a prefix and `/about-us/` is a 404).
 * The value is the set of raw strings to key replacements on, because those
 * are what actually appear in the document.
 */
export function normalizedLinkIndex(html, pageUrl, selfHost, domain) {
  const index = new Map();
  for (const raw of collectPageLinks(html)) {
    const abs = absolutize(raw, pageUrl);
    if (!abs) continue;
    const key = linkKey(normalizeSelfHost(abs, selfHost, domain));
    if (!key) continue;
    if (!index.has(key)) index.set(key, new Set());
    index.get(key).add(raw);
  }
  return index;
}

export function isIgnoredHost(absUrl, ignoreHosts = []) {
  if (!ignoreHosts.length) return false;
  let u;
  try {
    u = new URL(absUrl);
  } catch {
    return false;
  }
  const host = u.hostname.replace(/^www\./, '');
  return ignoreHosts.includes(host);
}

/**
 * Forms on a captured page, and any contact addresses the page advertises.
 *
 * A static host has no backend, so a Forminator form ships as markup that
 * silently swallows every submission — worse than no form, because a visitor
 * believes they have made contact. The capture reports them rather than
 * guessing a replacement: substituting an address nobody confirmed would put a
 * wrong contact route on a charity's website.
 */
export function detectForms(html) {
  // Not `<form\b`: \b matches before a hyphen, so a custom element such as
  // <form-widget> would be counted as a form.
  const forms = [...html.matchAll(/<form(?=[\s/>])[^>]*>/gi)].length;
  const forminator = /forminator|wpcf7|gravity[_-]?form|ninja[_-]?forms/i.test(html) ? 1 : 0;
  const emails = new Set();
  for (const m of html.matchAll(/mailto:([^"'?>\s]+@[^"'?>\s]+)/gi)) emails.add(m[1].toLowerCase());
  return { forms, hasFormPlugin: forminator === 1, emails: [...emails] };
}

/**
 * Decode Cloudflare's email obfuscation back into real addresses.
 *
 * Cloudflare rewrites `mailto:` links at the edge into
 * `/cdn-cgi/l/email-protection#<hex>` plus a `<span class="__cf_email__"
 * data-cfemail="<hex>">` placeholder, and ships `email-decode.min.js` from
 * `/cdn-cgi/` to undo it in the browser. That script is edge-served, so after
 * migration it 404s and every obfuscated address on the site renders as hex.
 *
 * Decoding here is what makes `stripEdgeInjectedTags` safe: without it,
 * removing the decoder would leave the placeholders permanently broken. The
 * cipher is a documented XOR against the first byte.
 */
export function decodeCloudflareEmails(html) {
  const decode = (hex) => {
    if (!/^[0-9a-f]+$/i.test(hex) || hex.length < 4 || hex.length % 2) return null;
    const key = parseInt(hex.slice(0, 2), 16);
    let out = '';
    for (let i = 2; i < hex.length; i += 2) {
      out += String.fromCharCode(parseInt(hex.slice(i, i + 2), 16) ^ key);
    }
    // Only accept a plausible address. A failed decode must leave the markup
    // alone rather than write nonsense into the page.
    return /^[^\s@<>"']+@[^\s@<>"']+\.[^\s@<>"']+$/.test(out) ? out : null;
  };

  let decoded = 0;
  let out = html.replace(
    /<span\b[^>]*\bdata-cfemail\s*=\s*["']([0-9a-fA-F]+)["'][^>]*>[\s\S]*?<\/span>/gi,
    (whole, hex) => {
      const addr = decode(hex);
      if (!addr) return whole;
      decoded++;
      return addr;
    },
  );
  out = out.replace(
    /(href\s*=\s*["'])(?:https?:\/\/[^"']*)?\/cdn-cgi\/l\/email-protection#([0-9a-fA-F]+)(["'])/gi,
    (whole, pre, hex, post) => {
      const addr = decode(hex);
      if (!addr) return whole;
      decoded++;
      return `${pre}mailto:${addr}${post}`;
    },
  );
  return { html: out, decoded };
}

/**
 * Remove instrumentation the CDN injected at the edge, which is not the
 * charity's content and cannot survive the migration.
 *
 * Measured on the first delivery of viewpointministriesinternational.org: every
 * one of 120 pages failed the self-containment gate on a same-origin request to
 * `/cdn-cgi/rum?`. Nothing in the captured HTML referenced that URL — the
 * capture's own asset inventory never saw it — because it is fabricated at
 * runtime by Cloudflare's beacon script. Only removing the requester stops it.
 *
 * These are edge endpoints, not origin files: `/cdn-cgi/*` is answered by
 * Cloudflare itself and exists on no origin, so no capture of any kind could
 * mirror it. Left in place, the exported site would go on trying to report
 * analytics to a CDN account it is no longer behind.
 *
 * Deliberately narrow: only script tags, and only the two Cloudflare surfaces.
 * Site content served through the CDN is untouched.
 */
export function stripEdgeInjectedTags(html) {
  const removed = [];
  const out = html.replace(/<script\b[^>]*>[\s\S]*?<\/script>|<script\b[^>]*\/>/gi, (tag) => {
    const src = /\bsrc\s*=\s*["']([^"']+)["']/i.exec(tag)?.[1] ?? '';
    // Cloudflare Web Analytics / Speed RUM. The loader is third-party, but the
    // beacon it starts POSTs to the SAME-ORIGIN /cdn-cgi/rum, which is what the
    // gate sees. `data-cf-beacon` catches the inline-config variant too.
    if (
      /(^|\/\/|\.)static\.cloudflareinsights\.com\//i.test(src) ||
      /\bdata-cf-beacon\b/i.test(tag)
    ) {
      removed.push(src || 'inline cf-beacon');
      return '';
    }
    // Rocket Loader, email-decode and friends: same-origin /cdn-cgi/ scripts
    // that the origin never served and a static host cannot serve.
    if (/(?:^|\/\/[^/]*)\/cdn-cgi\//i.test(src)) {
      removed.push(src);
      return '';
    }
    return tag;
  });
  return { html: out, removed };
}

/** The runtime this capture ships in place of the CMS's own script payload. */
export const CLONE_RUNTIME_NAME = 'clone-enhance.js';

/**
 * Strip every client script the CMS shipped.
 *
 * A captured site is a static export, so the JavaScript a CMS enqueues is at
 * best inert and at worst actively wrong. Measured on this migration: 1.9 MB
 * across 37 files — jQuery, jQuery Migrate, Underscore, the Divi theme bundle,
 * Divi Plus, Swiper, Hustle, MediaElement, a cookie-consent plugin, two
 * analytics beacons and eight Hummingbird concatenations of the same. What it
 * serves, counted against the DOM those 589 pages actually contain, is a
 * hamburger menu and a fade-in.
 *
 * Three separate reasons to remove it rather than keep it:
 *
 *   - It reports to properties the charity no longer owns. The analytics
 *     bundles beacon to the decommissioned site's Google property, and the
 *     consent banner exists to collect permission for exactly those beacons.
 *   - It calls endpoints a static host cannot answer: every Divi and Hustle
 *     bundle posts to `admin-ajax.php`, which is PHP and is gone.
 *   - Minified vendor bundles dominate any static analysis of the result. The
 *     charity's repository inherits every finding in jQuery 3.x and a page
 *     builder, on code nobody there can patch.
 *
 * `application/ld+json` is deliberately kept: it is the site's structured-data
 * block, it is parsed as data and never executed, and dropping it would cost
 * real search visibility for no security gain.
 */
export function stripClientScripts(html) {
  const removedSrc = [];
  let removedInline = 0;
  const out = html.replace(/<script\b[^>]*>[\s\S]*?<\/script>|<script\b[^>]*\/>/gi, (tag) => {
    const type = /\btype\s*=\s*["']([^"']+)["']/i.exec(tag)?.[1]?.trim() ?? '';
    if (/^application\/ld\+json$/i.test(type)) return tag;
    const src = /\bsrc\s*=\s*["']([^"']+)["']/i.exec(tag)?.[1] ?? '';
    if (src) removedSrc.push(src);
    else removedInline += 1;
    return '';
  });
  return { html: out, removedSrc, removedInline };
}

/**
 * Turn a stylesheet the page injects at runtime into a real `<link>`.
 *
 * This must run BEFORE the scripts are stripped, and getting the order wrong is
 * silent. Divi does not enqueue its per-page "late" stylesheet; it ships an
 * inline script that builds a `<link>` and inserts it after the inline style
 * block. Remove that script and the export loses the stylesheet twice over —
 * the page no longer references it, and the capture's asset inventory, which
 * reads URLs out of inline script bodies precisely because builders hide them
 * there, no longer finds it to download. The result is a correct-looking page
 * with a chunk of its styling missing and nothing in any log to say so.
 *
 * Hoisting is also the better artifact: a static `<link>` is discovered by the
 * preload scanner instead of after a script runs, so the styling arrives
 * without a flash of unstyled content and without depending on JavaScript.
 *
 * Recognised by the injection signature — `document.createElement('link')` —
 * rather than by "mentions a .css", so a config blob that merely names a
 * stylesheet does not cause one to be loaded.
 */
export function hoistRuntimeStylesheets(html) {
  const present = new Set();
  for (const m of html.matchAll(/<link\b[^>]*>/gi)) {
    const rel = (/\brel\s*=\s*["']([^"']+)["']/i.exec(m[0])?.[1] ?? '').toLowerCase();
    if (!rel.includes('stylesheet')) continue;
    const href = /\bhref\s*=\s*["']([^"']+)["']/i.exec(m[0])?.[1];
    if (href) present.add(href);
  }
  const hoisted = [];
  for (const m of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)) {
    const type = /\btype\s*=\s*["']([^"']+)["']/i.exec(m[1])?.[1]?.trim() ?? '';
    if (/^application\/ld\+json$/i.test(type)) continue;
    if (!/createElement\(\s*["']link["']\s*\)/.test(m[2])) continue;
    for (const s of m[2].matchAll(/["']([^"']+\.css(?:\?[^"']*)?)["']/g)) {
      // Builders store these as JSON, where every slash is escaped.
      const href = s[1].replace(/\\\//g, '/');
      // The href becomes a double-quoted attribute, so a value that could close
      // it or open a tag is refused rather than emitted. Nothing legitimate is
      // lost: a URL carrying these characters is invalid unencoded anyway.
      if (/["<>]/.test(href)) continue;
      if (present.has(href) || hoisted.includes(href)) continue;
      hoisted.push(href);
    }
  }
  if (!hoisted.length) return { html, hoisted };
  const tags = hoisted.map((h) => `<link rel="stylesheet" href="${h}">`).join('');
  const i = html.toLowerCase().lastIndexOf('</head>');
  return {
    html: i === -1 ? `${tags}${html}` : `${html.slice(0, i)}${tags}${html.slice(i)}`,
    hoisted,
  };
}

/**
 * Head elements that advertise a WordPress backend which no longer exists.
 *
 * These are not decoration. `EditURI` and `wlwmanifest` point at `xmlrpc.php`,
 * the oEmbed and `api.w.org` links point at `/wp-json/`, and the feed links
 * point at `/feed/` — all four are PHP routes that a static export cannot
 * serve, and every one of them was still carrying the ABSOLUTE origin URL, so
 * they lead a reader (or a crawler) straight back to the host being
 * decommissioned. `<meta name="generator">` merely announces the stack and its
 * version to anyone scanning for it.
 *
 * Kept narrow on purpose: only these rels, and only a `rel="alternate"` whose
 * href is a WordPress route — a genuine hreflang or RSS alternate for content
 * the site still serves is untouched.
 */
const WP_PLUMBING_RELS = new Set([
  'edituri',
  'wlwmanifest',
  'https://api.w.org/',
  'shortlink',
  'pingback',
  'profile',
]);
const WP_PLUMBING_HREF = /(?:xmlrpc\.php|wlwmanifest|\/wp-json\/|\/feed\/?(?:$|[?#])|osd\.xml)/i;

export function stripWordPressHeadLinks(html) {
  const removed = [];
  let out = html.replace(/<link\b[^>]*>/gi, (tag) => {
    const rel = (/\brel\s*=\s*["']([^"']+)["']/i.exec(tag)?.[1] ?? '').trim().toLowerCase();
    const href = /\bhref\s*=\s*["']([^"']+)["']/i.exec(tag)?.[1] ?? '';
    const as = (/\bas\s*=\s*["']([^"']+)["']/i.exec(tag)?.[1] ?? '').trim().toLowerCase();
    // A preload for a script is dead the moment the scripts are stripped, and
    // it is the one plumbing link that survives a rel-and-href filter: it names
    // no WordPress route, it just warms a fetch for a bundle nothing imports.
    // Measured here: 3 pages preloaded @wordpress/interactivity after every
    // other trace of it was gone.
    const scriptPreload = rel === 'modulepreload' || (rel === 'preload' && as === 'script');
    if (
      WP_PLUMBING_RELS.has(rel) ||
      scriptPreload ||
      (rel === 'alternate' && WP_PLUMBING_HREF.test(href))
    ) {
      removed.push(rel || href);
      return '';
    }
    return tag;
  });
  out = out.replace(/<meta\b[^>]*\bname\s*=\s*["']generator["'][^>]*>/gi, (tag) => {
    removed.push('generator');
    return '';
  });
  return { html: out, removed };
}

/**
 * Remove the rule that makes Divi's content invisible until JavaScript runs.
 *
 * This is the single most dangerous line in the whole de-WordPressing pass, and
 * it is invisible in a diff of the scripts. Divi paints every animated element
 * at `opacity: 0` and reveals it from a scroll handler in the bundle above.
 * Measured here: `.et-waypoint:not(.et_pb_counters){opacity:0}` appears in the
 * inline `<style>` of **589 of 589** captured pages. Remove the bundle and
 * leave this rule and the export is not a degraded site, it is 589 blank
 * pages — and every automated check still passes, because the markup, the
 * titles, the links and the byte counts are all exactly right.
 *
 * So visibility stops depending on JavaScript at all: the rule is dropped, the
 * content paints, and `clone-enhance.js` adds `.et-animated` purely to start
 * the keyframes the stylesheet already defines.
 *
 * Only the `opacity: 0` form is touched. Divi ships many `.et-waypoint … {
 * opacity: 1 }` rules — the animation-off variants — and those must survive,
 * which is why this matches on the declaration and not on the selector alone.
 */
export function neutralizeWaypointHiding(css) {
  let removed = 0;
  const out = css.replace(/([^{}]+)\{\s*opacity\s*:\s*0\s*;?\s*\}/g, (rule, selectors) => {
    if (!/\.et-waypoint\b/.test(selectors)) return rule;
    const all = selectors.split(',');
    const kept = all.filter((s) => !/\.et-waypoint\b/.test(s));
    removed += all.length - kept.length;
    // A rule whose ONLY selectors were waypoints disappears; one that also hid
    // something else keeps that half, or removing the hiding rule for a
    // waypoint would silently un-hide an unrelated element too.
    return kept.length ? `${kept.join(',')}{opacity:0}` : '';
  });
  return { css: out, removed };
}

/** Apply `neutralizeWaypointHiding` to each inline `<style>` block. */
export function neutralizeWaypointHidingInHtml(html) {
  let removed = 0;
  const out = html.replace(
    /(<style\b[^>]*>)([\s\S]*?)(<\/style>)/gi,
    (whole, open, body, close) => {
      const r = neutralizeWaypointHiding(body);
      removed += r.removed;
      return `${open}${r.css}${close}`;
    },
  );
  return { html: out, removed };
}

/**
 * Undo two accessibility defects the CMS emits, neither of which is content.
 *
 * **Pinch-zoom.** Divi ships
 * `<meta name="viewport" content="… maximum-scale=1.0, user-scalable=0">`,
 * which disables zoom on every page. For a charity whose audience is largely
 * on phones, that is not a nit — it is the difference between a readable page
 * and an unreadable one for anyone with low vision, and iOS/Android both honour
 * it. Lighthouse weights it 10, more than any other accessibility audit failing
 * on this site, and it is fixed by deleting two declarations that a static
 * export has no reason to carry.
 *
 * **A main landmark.** The document has none, so a screen-reader user has no
 * "skip to content" target and must tab through the whole header on every page.
 * `role="main"` is added to Divi's existing `#main-content` wrapper rather than
 * retagging it to `<main>`: retagging means finding its matching `</div>`
 * through arbitrarily nested markup, and a mis-balanced close would corrupt
 * every page. The role satisfies the same requirement with one attribute and
 * no possibility of that.
 *
 * Both are corrections to the CMS's own output, not edits to the charity's
 * words. What is deliberately NOT touched here is anything that would mean
 * writing content on their behalf — heading levels, link text, alt text. Those
 * are the site's, and a migration that silently rewrites them is no longer a
 * mirror.
 */
export function normalizeAccessibility(html) {
  const fixed = [];
  // `(?:^|\s)` rather than `\b` before each attribute name, for the reason
  // already written out on the `role` check below: `-` is a non-word
  // character, so `\b` sits INSIDE `data-name` and `data-content`. Measured
  // before this fix, on `<meta name="viewport" data-content="user-scalable=no"
  // content="width=device-width">`: the read took `data-content` (leftmost
  // match) as the viewport's directives, and the write then replaced
  // `data-content` too, emitting `data-content=""` — an unrelated attribute
  // silently emptied while the real viewport went unfiltered. Raised in review
  // on #1239 for the mirroring assertion in test_705; the pass had it too.
  let out = html.replace(/<meta(?:\s[^>]*)?\sname\s*=\s*["']viewport["'][^>]*>/gi, (tag) => {
    const content = /(?:^|\s)content\s*=\s*["']([^"']*)["']/i.exec(tag)?.[1];
    if (!content) return tag;
    const kept = content
      .split(',')
      .map((p) => p.trim())
      .filter((p) => {
        if (/^user-scalable\s*=\s*(no|0)$/i.test(p)) return false;
        const m = /^maximum-scale\s*=\s*([\d.]+)$/i.exec(p);
        // Lighthouse's threshold, and the WCAG one: anything under 5x is a
        // restriction. A larger cap is the author's business.
        if (m && Number(m[1]) < 5) return false;
        return true;
      });
    if (kept.length === content.split(',').length) return tag;
    fixed.push('viewport');
    return tag.replace(/(^|\s)content\s*=\s*["'][^"']*["']/i, `$1content="${kept.join(', ')}"`);
  });
  out = out.replace(/<div((?:\s[^>]*)?\sid\s*=\s*["']main-content["'][^>]*)>/i, (tag, attrs) => {
    // `\brole` is not enough: `-` is a non-word character, so `\b` sits inside
    // `data-role` and the test matches it. A theme that puts `data-role` on the
    // main wrapper would silently lose its landmark — the check would report
    // "already has a role" about an attribute that is not one. Anchored to the
    // start of the attribute list or a preceding space instead. Caught in
    // review on #1239.
    if (/(?:^|\s)role\s*=/i.test(attrs)) return tag;
    fixed.push('main-landmark');
    return `<div${attrs} role="main">`;
  });
  return { html: out, fixed };
}

/**
 * Add the clone's own runtime just before `</body>`.
 *
 * `defer` rather than `async`: it must not run before the document it wires up
 * exists, and ordering against nothing else matters. A page with no `</body>`
 * (a fragment, or truncated markup) gets it appended, so the script is never
 * silently dropped from a page that is merely malformed.
 */
export function injectCloneRuntime(html, href) {
  const tag = `<script src="${href}" defer></script>`;
  if (html.includes(tag)) return html;
  const i = html.toLowerCase().lastIndexOf('</body>');
  if (i === -1) return `${html}${tag}`;
  return `${html.slice(0, i)}${tag}${html.slice(i)}`;
}

/**
 * The whole de-WordPressing pass, in the order the steps depend on each other.
 *
 * Scripts go first so nothing later has to reason about markup inside a script
 * body. The waypoint rule is neutralized straight after, because between those
 * two steps the document is in the one state that must never be written to
 * disk. The runtime is injected last so it is not itself stripped.
 */
export function deWordPress(html, { runtimeHref } = {}) {
  const sheets = hoistRuntimeStylesheets(html);
  const scripts = stripClientScripts(sheets.html);
  const waypoints = neutralizeWaypointHidingInHtml(scripts.html);
  const links = stripWordPressHeadLinks(waypoints.html);
  const a11y = normalizeAccessibility(links.html);
  const out = runtimeHref ? injectCloneRuntime(a11y.html, runtimeHref) : a11y.html;
  return {
    html: out,
    accessibilityFixes: a11y.fixed,
    stylesheetsHoisted: sheets.hoisted,
    scriptsRemoved: scripts.removedSrc.length + scripts.removedInline,
    scriptSrcRemoved: scripts.removedSrc,
    inlineScriptsRemoved: scripts.removedInline,
    waypointRulesRemoved: waypoints.removed,
    headLinksRemoved: links.removed,
  };
}

/**
 * Local filename for an asset URL, namespaced by host so two providers cannot
 * collide on `/style.css`, and carrying the query string so `?ver=6.4` variants
 * stay distinct (WordPress cache-busts nearly every enqueued asset this way —
 * dropping the query merges genuinely different files).
 */
export function assetLocalName(absUrl) {
  const u = new URL(absUrl);
  const host = u.hostname.replace(/^www\./, '');
  // `u.pathname` is percent-ENCODED. Writing that verbatim produces a file whose
  // NAME literally contains `%C3%97`, while every request for it is
  // percent-DECODED before the filesystem lookup — so the server goes looking
  // for `×` and 404s on a file that is sitting right there. Measured on this
  // migration: an upload named `…-2160-×-1080-px.jpg` was unreachable in the
  // export, and would have been unreachable on GitHub Pages too. Any non-ASCII
  // filename hits this, which on a charity site means any upload named by a
  // human. Decoding here makes the round trip close.
  let p = u.pathname;
  try {
    const decoded = decodeURIComponent(p);
    // Decode ONLY where it cannot change the shape of the path. `new URL()`
    // normalises a real `../` away, so any `..` still present is percent-encoded
    // — an inert literal directory name that decoding would turn into genuine
    // traversal. Same for a NUL, which would truncate the write. Those fall back
    // to the encoded form, which is what this function always used to emit and
    // is provably safe; everything else — the accented and multi-byte filenames
    // this exists for — decodes.
    const segments = decoded.split('/');
    if (!segments.includes('..') && !segments.includes('.') && !decoded.includes('\0')) {
      p = decoded;
    }
  } catch {
    // A malformed escape is not decodable; keep it literal rather than throwing
    // and losing the asset entirely.
  }
  p = p.replace(/^\/+/, '');
  if (p === '' || p.endsWith('/')) p += 'index';
  let ext = extname(p);
  if (u.search) {
    let q = u.search.replace(/[^a-z0-9]+/gi, '-').replace(/^-|-$/g, '');
    // The query string becomes part of a FILENAME, and filenames are capped
    // at 255 bytes on the filesystems this ends up on. WordPress enqueues
    // Google Fonts as one request naming every family+weight the theme uses;
    // on a heavy theme that string alone can run past 4,000 characters, so
    // writing it verbatim doesn't truncate the file -- it crashes the write
    // with ENAMETOOLONG before any content lands (measured on ksgf.org).
    // Hash the overflow instead of embedding it raw: keeps a human-legible
    // prefix for debugging, keeps the mapping deterministic (same URL ->
    // same file, so re-running the capture doesn't rewrite every reference
    // to it), and costs nothing on the vast majority of URLs that never hit
    // the cap.
    const QUERY_NAME_CAP = 100;
    if (q.length > QUERY_NAME_CAP) {
      const hash = createHash('sha256').update(u.search).digest('hex').slice(0, 16);
      q = `${q.slice(0, QUERY_NAME_CAP)}-${hash}`;
    }
    // Keep the extension last so content-type sniffing and static hosts behave.
    p = ext ? `${p.slice(0, -ext.length)}__${q}${ext}` : `${p}__${q}`;
  }
  ext = extname(p);
  if (!ext) p += '.bin';
  return `${host}/${p}`.replace(/\/{2,}/g, '/');
}

/**
 * Formats worth re-encoding, and the one they are re-encoded to.
 *
 * GIF is excluded because it may be animated and a still WebP would silently
 * drop the animation; SVG because it is not raster; WebP and AVIF because they
 * are already the destination format.
 */
const RECODABLE = /\.(png|jpe?g)(\?|$)/i;

/** Quality ladder: the first rung that lands under budget wins. */
export const IMAGE_QUALITY_LADDER = [85, 78, 70];

/**
 * Decide whether a re-encoded image is worth keeping.
 *
 * Two independent conditions, and both matter. It must actually be smaller —
 * re-encoding an already-optimised JPEG routinely produces a LARGER file, and
 * shipping that would make the site heavier while reporting an optimisation.
 * And the saving must be worth a format change: a 5% win is not worth renaming
 * a file and rewriting every reference to it, because each rewrite is a chance
 * to strand a reference.
 */
export function worthReencoding(originalBytes, encodedBytes, minSavingRatio = 0.25) {
  if (!Number.isFinite(originalBytes) || !Number.isFinite(encodedBytes)) return false;
  if (encodedBytes <= 0 || originalBytes <= 0) return false;
  return encodedBytes <= originalBytes * (1 - minSavingRatio);
}

/** The local name an image takes once re-encoded to WebP. */
export function webpName(name) {
  return name.replace(/\.[^./]+$/, '') + '.webp';
}

/**
 * Whether this asset is a candidate for re-encoding at all.
 *
 * Size is part of the predicate, not a separate check: an image already under
 * budget is left byte-identical to what the charity uploaded. Only the ones
 * that would be a problem for a visitor are touched.
 */
export function shouldReencodeImage(absUrl, bytes, maxBytes) {
  if (!RECODABLE.test(absUrl)) return false;
  return Number.isFinite(bytes) && Number.isFinite(maxBytes) && bytes > maxBytes;
}

/**
 * Rewrite every reference in `html` that we localized.
 *
 * Longest-first is load-bearing: a plain global replace of the short URL would
 * corrupt the long one when one asset URL is a prefix of another (the common
 * `.../logo.png` vs `.../logo.png?ver=2` pair), leaving a mangled reference
 * that 404s and looks like a download failure rather than a rewrite bug.
 */
export function rewriteRefs(text, replacements) {
  const pairs = [...replacements.entries()].sort((a, b) => b[0].length - a[0].length);
  let out = text;
  for (const [from, to] of pairs) {
    if (!to) continue;
    out = out.split(from).join(to);
    // Page builders store URLs inside HTML-entity-escaped JSON, where the
    // delimiter is &quot; rather than a quote. Those copies are real references
    // and survive a markup-only rewrite pointing at the decommissioned host.
    const escaped = from.replace(/\//g, '\\/');
    if (escaped !== from) out = out.split(escaped).join(to.replace(/\//g, '\\/'));
  }
  return out;
}

/**
 * External http(s) references still present after localization, excluding the
 * ones we deliberately keep. This is the "zero external asset hosts" gate.
 */
export function remainingExternalAssetHosts(html, domain, ignoreHosts = []) {
  const hosts = new Set();
  for (const ref of collectAssetUrls(html)) {
    if (!/^https?:\/\//i.test(ref)) continue;
    if (isIgnoredHost(ref, ignoreHosts)) continue;
    if (shouldLocalize(ref, domain, ignoreHosts)) {
      try {
        hosts.add(new URL(ref).hostname);
      } catch {
        /* malformed reference — not a host we can report */
      }
    }
  }
  return [...hosts];
}

/**
 * Break the captured set down by content type and by which inventory found it.
 *
 * The report used to set `pages.captured` to the total number of rendered
 * documents, which is every page PLUS every post, every sitemap-only URL and
 * the synthetic front page. On a site with 19 pages and a 587-URL sitemap that
 * prints `pages: {captured: 587, reported: 19}` — a number that reads as a
 * spectacular over-achievement rather than as the category error it is, which
 * is exactly the kind of wrong that survives review.
 *
 * `posts.captured` had the mirror-image fault: it counted the REST items
 * FETCHED rather than the pages actually RENDERED, so a post whose page failed
 * to download still counted as captured.
 */
/**
 * Same-site navigation links that survived the rewrite, by host.
 *
 * The self-containment gate loads each page with the source host blocked, so
 * it sees SUBRESOURCES. A nav `href` is fetched only when a visitor clicks it,
 * which is never during a gate run — so a clone whose every menu item points
 * at the domain being decommissioned passes cleanly. That is exactly what
 * shipped: 562 of 589 pages, all green.
 *
 * Reported by host so the operator sees which domain the clone still leans on,
 * and counted per page so "one stray link" and "the whole navigation" are not
 * the same finding.
 */
export function remainingSelfHostLinks(html, domain, selfHost) {
  const hosts = new Map();
  for (const raw of collectPageLinks(html)) {
    if (!/^https?:\/\//i.test(raw)) continue;
    let host;
    try {
      host = new URL(raw).hostname.replace(/^www\./, '');
    } catch {
      continue;
    }
    // The site's own two names are the ones that must have been rewritten to
    // local paths. Any other host is a genuine outbound link and is left alone.
    const isSelf = host === domain || host.endsWith(`.${domain}`);
    const isStale = selfHost && (host === selfHost || host.endsWith(`.${selfHost}`));
    if (!isSelf && !isStale) continue;
    hosts.set(host, (hosts.get(host) ?? 0) + 1);
  }
  return hosts;
}

export function summarizeCaptured(entries, renderedPaths) {
  const byType = {};
  const bySource = {};
  let total = 0;
  for (const e of entries) {
    if (!e || !e.localPath || !renderedPaths.has(e.localPath)) continue;
    total++;
    byType[e.type ?? 'unknown'] = (byType[e.type ?? 'unknown'] ?? 0) + 1;
    bySource[e.source ?? 'unknown'] = (bySource[e.source ?? 'unknown'] ?? 0) + 1;
  }
  return { total, byType, bySource };
}

/**
 * Group fetch failures by status and by host.
 *
 * The first real capture emitted 823 identical-looking `[asset] … HTTP 404`
 * lines and a verdict that said only "823 asset download(s) failed". Both are
 * true and neither is a diagnosis: the actual finding was that every one of
 * those URLs was on a DIFFERENT HOST than the site being captured
 * (viewpointministriesinternational.org's pages reference vpmin.org for their
 * Divi cache CSS, and vpmin.org does not serve /wp-content/). A count cannot
 * say that; a breakdown by host says it in one line.
 */
export function tallyFailures(failures) {
  const byStatus = {};
  const byHost = {};
  for (const f of failures) {
    const status = String(f?.status ?? 0);
    byStatus[status] = (byStatus[status] ?? 0) + 1;
    let host = 'unparseable';
    try {
      host = new URL(f.url).hostname;
    } catch {
      /* keep the placeholder */
    }
    byHost[host] = (byHost[host] ?? 0) + 1;
  }
  return {
    total: failures.length,
    byStatus,
    byHost,
    sample: failures.slice(0, 8).map((f) => `HTTP ${f?.status ?? 0} ${f?.url}`),
  };
}

/**
 * A one-line reading of a failure tally, or null when there is nothing to say.
 *
 * Names the dominant host when it is NOT the site being captured, because that
 * is a statement about the live site's own configuration — its pages point at
 * a host that does not serve them — rather than about this capture.
 */
export function describeFailures(tally, domain) {
  if (!tally || tally.total === 0) return null;
  const hosts = Object.entries(tally.byHost).sort((a, b) => b[1] - a[1]);
  const [topHost, topCount] = hosts[0];
  const statuses = Object.entries(tally.byStatus)
    .sort((a, b) => b[1] - a[1])
    .map(([s, n]) => `${n}×HTTP ${s}`)
    .join(', ');
  const foreign = topHost !== domain && !topHost.endsWith(`.${domain}`);
  const where = foreign
    ? `${topCount} of them on ${topHost}, which is NOT the site being captured — the live pages reference a host that does not serve those files`
    : `${topCount} of them on ${topHost}`;
  return `${tally.total} failed (${statuses}); ${where}`;
}

/**
 * Capture verdict. `ok` only when the inventory is complete AND nothing that
 * should have been localized is still pointing off-site.
 */
export function captureVerdict({
  expected,
  captured,
  externalHosts,
  failedAssets,
  assetFailureNote,
  frontPageCaptured = true,
  strandedStaleLinks = 0,
  strandedStalePages = 0,
  staleHost = null,
}) {
  const problems = [];
  // The front page is not one entry among many. A percentage floor cannot
  // express that: losing it costs 1 of 590 — 99.8%, comfortably inside any
  // sane threshold — while being the one page every visitor sees first.
  if (!frontPageCaptured)
    problems.push(
      "the site's front page was not captured; a clone with no index.html serves 404 at its root",
    );
  if (expected > 0 && captured < expected)
    problems.push(
      `captured ${captured} of ${expected} inventory entries (REST collections + sitemap union)`,
    );
  if (externalHosts.length) problems.push(`unlocalized asset hosts: ${externalHosts.join(', ')}`);
  // Only the STALE host is fatal. A link to the serving domain may have no
  // local equivalent — /feed/, /wp-json/, wp-admin — and gating on those would
  // refuse every real WordPress site. A link to a host the site does not serve
  // is broken for visitors today, whatever happens to DNS later.
  if (strandedStaleLinks > 0)
    problems.push(
      `${strandedStaleLinks} navigation link(s) across ${strandedStalePages} page(s) still point at ` +
        `${staleHost ?? 'the stale host'}, which does not serve this site`,
    );
  // Prefer the diagnostic sentence when one is available: "823 asset
  // download(s) failed" is a count, and the operator's next question is always
  // "failed how, and where?".
  if (failedAssets > 0)
    problems.push(
      assetFailureNote ? `assets: ${assetFailureNote}` : `${failedAssets} asset download(s) failed`,
    );
  return { ok: problems.length === 0, problems };
}

// ---------------------------------------------------------------------------
// Self-test — offline, no network. Must precede the usage guard so that
// `--self-test` runs without --domain.
// ---------------------------------------------------------------------------

function selfTest() {
  let failures = 0;
  const eq = (label, actual, expected) => {
    const a = JSON.stringify(actual);
    const e = JSON.stringify(expected);
    if (a !== e) {
      console.error(`FAIL ${label}\n  expected ${e}\n  actual   ${a}`);
      failures++;
    } else {
      console.log(`ok   ${label}`);
    }
  };

  eq(
    'normalizeDomain strips scheme/www/path',
    normalizeDomain('https://www.VpMin.org/about/'),
    'vpmin.org',
  );
  eq('normalizeDomain strips port', normalizeDomain('example.org:8080'), 'example.org');
  eq('normalizeDomain rejects non-string', normalizeDomain(null), '');

  eq(
    'restRootCandidates offers all three flavors',
    restRootCandidates('x.org').map((c) => c.kind),
    ['pretty', 'query', 'dotcom'],
  );
  eq(
    'restRootCandidates dotcom index targets public-api',
    restRootCandidates('x.org')[2].indexUrl,
    'https://public-api.wordpress.com/wp/v2/sites/x.org',
  );
  eq(
    'collectionUrlFor pretty',
    collectionUrlFor('pretty', 'x.org', 'pages', { per_page: '1' }),
    'https://x.org/wp-json/wp/v2/pages?per_page=1',
  );
  eq(
    'collectionUrlFor query keeps rest_route and appends with &',
    collectionUrlFor('query', 'x.org', 'pages', { per_page: '1' }),
    'https://x.org/?rest_route=/wp/v2/pages&per_page=1',
  );
  eq(
    'collectionUrlFor dotcom puts wp/v2 before the site',
    collectionUrlFor('dotcom', 'x.org', 'media', { per_page: '1' }),
    'https://public-api.wordpress.com/wp/v2/sites/x.org/media?per_page=1',
  );
  eq(
    'collectionUrlFor omits a bare ? when there are no params',
    collectionUrlFor('dotcom', 'x.org', 'pages'),
    'https://public-api.wordpress.com/wp/v2/sites/x.org/pages',
  );

  const urlset = `<?xml version="1.0"?><urlset><url><loc>https://x.org/</loc></url>
    <url><loc>https://x.org/about/</loc></url><url><loc>https://x.org/feed/</loc></url>
    <url><loc>https://other.org/nope/</loc></url><url><loc>https://x.org/a.pdf</loc></url></urlset>`;
  eq('parseSitemapUrls reads a urlset', parseSitemapUrls(urlset).urls.length, 5);
  eq('parseSitemapUrls knows a urlset is not an index', parseSitemapUrls(urlset).isIndex, false);
  eq(
    'parseSitemapUrls detects a sitemap index',
    parseSitemapUrls(
      '<sitemapindex><sitemap><loc>https://x.org/s1.xml</loc></sitemap></sitemapindex>',
    ).isIndex,
    true,
  );
  eq(
    'parseSitemapUrls unescapes &amp;',
    parseSitemapUrls('<urlset><url><loc>https://x.org/?a=1&amp;b=2</loc></url></urlset>').urls,
    ['https://x.org/?a=1&b=2'],
  );
  eq(
    'sitemapPageUrls keeps pages, drops feeds/assets/other hosts',
    sitemapPageUrls(parseSitemapUrls(urlset).urls, 'x.org').sort(),
    ['https://x.org/', 'https://x.org/about/'],
  );

  // The resolver used to handle only ok/blocked and default to `absent`, so an
  // unreachable host was reported as "not WordPress" — collapsing exactly the
  // distinction classifyRestIndex is three-valued to preserve, and sending the
  // operator to a different capture path instead of to the network.
  eq(
    'pickRestOutcome prefers ok',
    pickRestOutcome([
      { verdict: 'absent' },
      { verdict: 'blocked' },
      { verdict: 'ok', kind: 'dotcom' },
    ])?.kind ?? null,
    'dotcom',
  );
  eq(
    'pickRestOutcome prefers blocked over unreachable',
    pickRestOutcome([{ verdict: 'unreachable' }, { verdict: 'blocked' }])?.verdict ?? null,
    'blocked',
  );
  eq(
    'pickRestOutcome surfaces unreachable rather than absent',
    pickRestOutcome([{ verdict: 'absent' }, { verdict: 'unreachable' }])?.verdict ?? null,
    'unreachable',
  );
  eq(
    'pickRestOutcome returns null when everything is absent',
    pickRestOutcome([{ verdict: 'absent' }]),
    null,
  );
  eq('pickRestOutcome handles an empty probe list', pickRestOutcome([]), null);

  eq('classify 200 + namespaces = ok', classifyRestIndex(200, { namespaces: ['wp/v2'] }), 'ok');
  eq('classify 200 + junk = absent', classifyRestIndex(200, 'not json'), 'absent');
  eq('classify 401 = blocked', classifyRestIndex(401, null), 'blocked');
  eq('classify 403 = blocked', classifyRestIndex(403, null), 'blocked');
  eq('classify 404 = absent', classifyRestIndex(404, null), 'absent');
  eq('classify 0 = unreachable', classifyRestIndex(0, null), 'unreachable');

  eq('localPath home', localPathForLink('https://x.org/', 'x.org'), 'index.html');
  eq('localPath nested', localPathForLink('https://x.org/a/b/', 'x.org'), 'a/b/index.html');
  eq('localPath file keeps name', localPathForLink('https://x.org/feed.xml', 'x.org'), 'feed.xml');
  eq('localPath rejects garbage', localPathForLink('not a url', 'x.org'), null);

  // The politeness delay must apply to ASSETS too — they are the bulk of the
  // crawl. A cap here meant `--delay` could not slow the run down at all, and
  // that got the runner blocked by a charity's security plugin.
  eq('assetSleepMs honours the operator delay rather than capping it', assetSleepMs(750), 750);
  eq('assetSleepMs passes the default through', assetSleepMs(250), 250);
  eq(
    'assetSleepMs tolerates junk without becoming a busy loop or NaN',
    [assetSleepMs(0), assetSleepMs(-5), assetSleepMs('abc'), assetSleepMs(undefined)],
    [0, 0, 0, 0],
  );
  eq('relativePrefix root', relativePrefix('index.html'), './');
  eq('relativePrefix nested', relativePrefix('a/b/index.html'), '../../');

  // Asset collection
  const html = `
    <link rel="stylesheet" href="/style.css?ver=1">
    <link rel="canonical" href="https://x.org/about/">
    <img src="/a.png" srcset="/a-300.png 300w, /a-600.png 600w">
    <div style="background:url('/bg.jpg')"></div>
    <a href="/contact/">Contact</a>`;
  const found = collectAssetUrls(html).sort();
  eq('collectAssetUrls finds assets, not nav links', found, [
    '/a-300.png',
    '/a-600.png',
    '/a.png',
    '/bg.jpg',
    '/style.css?ver=1',
  ]);
  eq(
    'collectAssetUrls skips rel=canonical',
    collectAssetUrls('<link rel="canonical" href="https://x.org/">'),
    [],
  );
  eq(
    'collectAssetUrls skips data: URIs',
    collectAssetUrls('<img src="data:image/png;base64,AAA">'),
    [],
  );

  // Containment. The first three pin what WHATWG URL actually does today
  // (measured, not assumed); the last two pin that the guard would still catch
  // an escape if that ever changed, since it is a dependency we do not control.
  eq(
    'new URL normalises ../ away before it reaches a path',
    localPathForLink('https://h.org/a/../../../etc/passwd', 'h.org'),
    'etc/passwd/index.html',
  );
  eq(
    'new URL normalises percent-encoded ../ away too',
    localPathForLink('https://h.org/%2e%2e/%2e%2e/etc/passwd', 'h.org'),
    'etc/passwd/index.html',
  );
  eq(
    'an encoded slash survives only as a literal directory name',
    assetLocalName('https://h.org/..%2f..%2fetc/x.png'),
    'h.org/..%2f..%2fetc/x.png',
  );
  // A stale WordPress self-reference: siteurl/home naming a host that no longer
  // serves the site. Measured live on 2026-08-31 — see declaredSelfHost.
  eq(
    'declaredSelfHost reports a home that disagrees with the capture domain',
    declaredSelfHost({ url: 'https://old.org', home: 'https://old.org' }, 'new.org'),
    'old.org',
  );
  eq(
    'declaredSelfHost prefers home over url',
    declaredSelfHost({ url: 'https://a.org', home: 'https://b.org' }, 'new.org'),
    'b.org',
  );
  eq(
    'declaredSelfHost falls back to url when home is absent',
    declaredSelfHost({ url: 'https://a.org' }, 'new.org'),
    'a.org',
  );
  eq(
    'declaredSelfHost is null when the home AGREES — the normal case must be a no-op',
    declaredSelfHost({ url: 'https://new.org', home: 'https://new.org' }, 'new.org'),
    null,
  );
  eq(
    'declaredSelfHost ignores a leading www. on the declared home',
    declaredSelfHost({ home: 'https://www.new.org' }, 'new.org'),
    null,
  );
  // The case above is decided by the SUBDOMAIN rule ('www.new.org' ends with
  // '.new.org'), not by the www strip — so it passes with the strip removed.
  // This one is decided by the strip alone, and it matters: normalizeSelfHost
  // compares a www-stripped hostname against selfHost, so a selfHost that kept
  // its 'www.' would match nothing and normalization would silently do nothing
  // at all on a site whose home is declared with www.
  eq(
    'declaredSelfHost strips www. from a STALE home, so normalizeSelfHost can match it',
    declaredSelfHost({ home: 'https://www.old.org' }, 'new.org'),
    'old.org',
  );
  eq(
    'the two functions agree end to end on a www-declared stale home',
    (() => {
      const h = declaredSelfHost({ home: 'https://www.old.org' }, 'new.org');
      return [
        normalizeSelfHost('https://old.org/a/', h, 'new.org'),
        normalizeSelfHost('https://www.old.org/a/', h, 'new.org'),
      ];
    })(),
    ['https://new.org/a/', 'https://new.org/a/'],
  );
  eq(
    'declaredSelfHost treats a subdomain of the capture domain as the same site',
    declaredSelfHost({ home: 'https://cdn.new.org' }, 'new.org'),
    null,
  );
  eq('declaredSelfHost tolerates a missing index', declaredSelfHost(null, 'new.org'), null);
  eq(
    'declaredSelfHost tolerates a non-URL home',
    declaredSelfHost({ home: 'not a url', url: '' }, 'new.org'),
    null,
  );

  eq(
    'normalizeSelfHost moves a stale page URL onto the serving host',
    normalizeSelfHost('https://old.org/ministries/', 'old.org', 'new.org'),
    'https://new.org/ministries/',
  );
  eq(
    'normalizeSelfHost keeps the path, query and fragment intact',
    normalizeSelfHost('https://old.org/wp-content/a.png?ver=2#x', 'old.org', 'new.org'),
    'https://new.org/wp-content/a.png?ver=2#x',
  );
  eq(
    'normalizeSelfHost rewrites a www. form of the stale host too',
    normalizeSelfHost('https://www.old.org/a/', 'old.org', 'new.org'),
    'https://new.org/a/',
  );
  // The containment property that makes this safe to do automatically: a page
  // cannot steer the capture at a third party, because the ONLY host ever
  // rewritten is the one the REST index declared, and the only destination is
  // the domain the operator named.
  eq(
    'normalizeSelfHost leaves every other host alone',
    [
      normalizeSelfHost('https://cdn.example.net/a.png', 'old.org', 'new.org'),
      normalizeSelfHost('https://new.org/a.png', 'old.org', 'new.org'),
      normalizeSelfHost('https://notold.org/a.png', 'old.org', 'new.org'),
    ],
    ['https://cdn.example.net/a.png', 'https://new.org/a.png', 'https://notold.org/a.png'],
  );
  eq(
    'normalizeSelfHost is a no-op when no stale host was detected',
    normalizeSelfHost('https://old.org/a/', null, 'new.org'),
    'https://old.org/a/',
  );
  eq(
    'normalizeSelfHost tolerates a malformed reference',
    normalizeSelfHost('not a url', 'old.org', 'new.org'),
    'not a url',
  );

  // Ignored hosts: a host the source site emits references to that serves
  // nothing we want. Such a reference is not an asset — do not fetch it, do not
  // count it as a failure, and do not let it hold the "zero external hosts"
  // gate open. This is a DROP, not a rewrite: nothing is re-pointed at --domain.
  eq(
    'isIgnoredHost matches a listed host',
    isIgnoredHost('https://drop.example/a.css', ['drop.example']),
    true,
  );
  eq(
    'isIgnoredHost ignores a leading www. on the reference',
    isIgnoredHost('https://www.drop.example/a.css', ['drop.example']),
    true,
  );
  eq(
    'isIgnoredHost does not match a sibling host by prefix',
    isIgnoredHost('https://drop.example.net/a.css', ['drop.example']),
    false,
  );
  eq(
    'isIgnoredHost does not match a subdomain of a listed host',
    isIgnoredHost('https://cdn.drop.example/a.css', ['drop.example']),
    false,
  );
  eq(
    'isIgnoredHost is a no-op with an empty list',
    isIgnoredHost('https://drop.example/a.css', []),
    false,
  );
  eq(
    'isIgnoredHost tolerates a malformed reference',
    isIgnoredHost('not a url', ['drop.example']),
    false,
  );
  eq(
    'shouldLocalize refuses an ignored host even though it would otherwise localize',
    [
      shouldLocalize('https://drop.example/wp-content/a.css', 'h.org', []),
      shouldLocalize('https://drop.example/wp-content/a.css', 'h.org', ['drop.example']),
    ],
    [true, false],
  );
  eq(
    'the ignore list beats even the same-site rule',
    shouldLocalize('https://h.org/wp-content/a.css', 'h.org', ['h.org']),
    false,
  );
  eq(
    'remainingExternalAssetHosts does not report an ignored host',
    [
      remainingExternalAssetHosts('<img src="https://drop.example/a.png">', 'h.org', []),
      remainingExternalAssetHosts('<img src="https://drop.example/a.png">', 'h.org', [
        'drop.example',
      ]),
    ],
    [['drop.example'], []],
  );

  // Forms have no backend once static; report them rather than guess a replacement.
  eq(
    'detectForms finds a form and its plugin',
    (() => {
      const d = detectForms('<form class="forminator-ui"></form>');
      return [d.forms, d.hasFormPlugin];
    })(),
    [1, true],
  );
  eq(
    'detectForms harvests mailto addresses, lowercased',
    detectForms('<a href="mailto:Info@VPMI.org">contact</a>').emails,
    ['info@vpmi.org'],
  );
  eq('detectForms reports nothing on a plain page', detectForms('<p>hi</p>').forms, 0);

  // --- Edge-injected CDN instrumentation -----------------------------------
  // Every page of the first live delivery failed the self-containment gate on
  // /cdn-cgi/rum?, a URL that appears in no attribute anywhere: the beacon
  // script fabricates it at runtime, so only removing the script stops it.
  const CF_BEACON =
    '<p>hi</p><script defer src="https://static.cloudflareinsights.com/beacon.min.js" ' +
    'data-cf-beacon=\'{"token":"abc"}\'></script>';
  eq('the Cloudflare beacon script is removed', stripEdgeInjectedTags(CF_BEACON).html, '<p>hi</p>');
  eq('removal is reported, not silent', stripEdgeInjectedTags(CF_BEACON).removed, [
    'https://static.cloudflareinsights.com/beacon.min.js',
  ]);
  eq(
    'a same-origin /cdn-cgi/ script is removed',
    stripEdgeInjectedTags(
      '<script src="/cdn-cgi/scripts/7d0fa10a/cloudflare-static/rocket-loader.min.js"></script>a',
    ).html,
    'a',
  );
  eq(
    'an absolute /cdn-cgi/ script is removed too',
    stripEdgeInjectedTags(
      '<script src="https://x.org/cdn-cgi/scripts/x/email-decode.min.js"></script>b',
    ).html,
    'b',
  );
  // The narrowness IS the property. A blanket "drop third-party scripts" would
  // strip the site's own analytics, embeds and player code.
  eq(
    "the site's own scripts survive",
    stripEdgeInjectedTags(
      '<script src="/wp-content/themes/divi/js/custom.js"></script>' +
        '<script src="https://www.googletagmanager.com/gtag/js?id=G-1"></script>',
    ).removed.length,
    0,
  );
  eq(
    'a host merely CONTAINING the beacon name is not stripped',
    stripEdgeInjectedTags(
      '<script src="https://notstatic.cloudflareinsights.com.evil.test/a.js"></script>',
    ).removed.length,
    0,
  );
  eq(
    'a link or image mentioning cdn-cgi is left alone — only scripts are removed',
    stripEdgeInjectedTags('<img src="/cdn-cgi/image/w=80/logo.png">').html,
    '<img src="/cdn-cgi/image/w=80/logo.png">',
  );

  // --- Oversized image re-encoding -----------------------------------------
  // 194 of 335 captured images were over the receiving repo's 400 KB per-file
  // budget and accounted for 186 MB of the 205 MB the clone shipped. They are
  // photographic event flyers exported as PNG, which is the wrong container for
  // them; the cost lands on a visitor's phone connection.
  eq(
    'an oversized PNG is a re-encode candidate',
    shouldReencodeImage('https://x.org/a/flyer.png', 900_000, 400 * 1024),
    true,
  );
  eq(
    'a PNG already under budget is left byte-identical to what was uploaded',
    shouldReencodeImage('https://x.org/a/logo.png', 12_000, 400 * 1024),
    false,
  );
  eq(
    'a JPEG is a candidate too — WordPress ships oversized ones as readily',
    shouldReencodeImage('https://x.org/a/hero.JPEG?ver=2', 900_000, 400 * 1024),
    true,
  );
  // A still WebP would silently drop an animation, and an SVG is not raster.
  eq(
    'a GIF is never re-encoded',
    shouldReencodeImage('https://x.org/a/spinner.gif', 900_000, 400 * 1024),
    false,
  );
  eq(
    'an SVG is never re-encoded',
    shouldReencodeImage('https://x.org/a/icon.svg', 900_000, 400 * 1024),
    false,
  );
  eq(
    'an image already in the destination format is not re-encoded',
    shouldReencodeImage('https://x.org/a/photo.webp', 900_000, 400 * 1024),
    false,
  );
  // Re-encoding an already-optimised JPEG routinely produces a LARGER file.
  // Shipping that would make the site heavier while reporting an optimisation.
  eq('a larger result is refused', worthReencoding(100_000, 120_000), false);
  eq('an equal result is refused', worthReencoding(100_000, 100_000), false);
  // A marginal win is not worth a rename, because every rename is a chance to
  // strand a reference.
  eq('a 10% saving is not worth the rename', worthReencoding(100_000, 90_000), false);
  eq('an 87% saving is', worthReencoding(2_297_000, 305_000), true);
  eq(
    'a zero-byte encode is refused rather than treated as a perfect win',
    worthReencoding(100_000, 0),
    false,
  );
  eq(
    'webpName replaces the extension rather than appending',
    webpName('x/a/flyer.png'),
    'x/a/flyer.webp',
  );
  eq('webpName leaves a dotted directory alone', webpName('x/v1.2/flyer.png'), 'x/v1.2/flyer.webp');
  eq(
    'webpName handles the query-suffixed names assetLocalName produces',
    webpName('x/a/flyer__ver-2.png'),
    'x/a/flyer__ver-2.webp',
  );

  // --- De-WordPressing: the CMS script payload -----------------------------
  // A static export cannot run PHP, so every bundle that posts to
  // admin-ajax.php is dead weight; the analytics beacons report to a property
  // the charity no longer owns; and the vendored minified bundles dominate any
  // static analysis of the repository the charity inherits. Measured on this
  // migration: 1.9 MB across 37 files, serving a hamburger and a fade.
  const WP_PAGE =
    '<html><head>' +
    '<script src="/wp-includes/js/jquery/jquery.min.js?ver=3.7.1"></script>' +
    '<script type="application/ld+json">{"@type":"Organization"}</script>' +
    '<script>var monsterinsights_frontend = {"js_events_tracking":"true"};</script>' +
    '</head><body><p>hi</p></body></html>';
  eq('a vendored CMS bundle is removed', stripClientScripts(WP_PAGE).removedSrc, [
    '/wp-includes/js/jquery/jquery.min.js?ver=3.7.1',
  ]);
  eq('inline CMS config blocks are removed too', stripClientScripts(WP_PAGE).removedInline, 1);
  eq(
    'structured data survives — it is parsed as data and never executed',
    stripClientScripts(WP_PAGE).html.includes('application/ld+json'),
    true,
  );
  eq(
    'the ld+json PAYLOAD survives, not just its type attribute',
    stripClientScripts(WP_PAGE).html.includes('{"@type":"Organization"}'),
    true,
  );
  eq('the page content is untouched', stripClientScripts(WP_PAGE).html.includes('<p>hi</p>'), true);
  eq(
    'a self-closing script tag is removed rather than left dangling',
    stripClientScripts('<script src="/a.js"/>x').html,
    'x',
  );
  eq(
    'ld+json matching is exact — a type merely containing it is still removed',
    stripClientScripts('<script type="application/ld+json-evil">x</script>').removedInline,
    1,
  );
  eq(
    'speculationrules is removed: it prefetches /wp-*.php routes that are gone',
    stripClientScripts('<script type="speculationrules">{"prefetch":[]}</script>').removedInline,
    1,
  );
  eq(
    'markup inside a script body cannot survive as markup',
    stripClientScripts('<script>var a="</div><img src=x>";</script>b').html,
    'b',
  );

  // --- De-WordPressing: head plumbing --------------------------------------
  // Every one of these named the ABSOLUTE origin and pointed at a PHP route,
  // so they lead a reader or a crawler back to the host being decommissioned.
  const WP_HEAD =
    '<link rel="EditURI" type="application/rsd+xml" href="https://x.org/xmlrpc.php?rsd" />' +
    '<link rel="wlwmanifest" href="https://x.org/wlwmanifest.xml" />' +
    '<link rel="https://api.w.org/" href="https://x.org/wp-json/" />' +
    '<link rel="alternate" type="application/json+oembed" href="https://x.org/wp-json/oembed/1.0/embed?url=y" />' +
    '<link rel="shortlink" href="https://x.org/?p=2" />' +
    '<link rel="canonical" href="https://x.org/about/" />' +
    '<link rel="stylesheet" href="/style.css" />' +
    '<meta name="generator" content="WordPress 6.8.1" />';
  eq('WordPress head plumbing is removed', stripWordPressHeadLinks(WP_HEAD).removed.length, 6);
  eq(
    'the canonical link survives — it is the one head link a static export needs',
    stripWordPressHeadLinks(WP_HEAD).html.includes('rel="canonical"'),
    true,
  );
  eq('the stylesheet survives', stripWordPressHeadLinks(WP_HEAD).html.includes('/style.css'), true);
  eq(
    'a modulepreload for a stripped bundle is removed',
    stripWordPressHeadLinks('<link rel="modulepreload" href="/wp-includes/js/x.js">').removed
      .length,
    1,
  );
  eq(
    'so is a rel=preload as=script',
    stripWordPressHeadLinks('<link rel="preload" as="script" href="/a.js">').removed.length,
    1,
  );
  // The font and stylesheet preloads are what make the page paint quickly and
  // have nothing to do with the CMS; a blanket "drop preloads" would cost a
  // visible flash of unstyled text on every page.
  eq(
    'a font or style preload survives',
    stripWordPressHeadLinks(
      '<link rel="preload" as="font" href="/f.woff2"><link rel="preload" as="style" href="/s.css">',
    ).removed.length,
    0,
  );
  eq(
    'a real hreflang alternate is NOT collateral damage',
    stripWordPressHeadLinks('<link rel="alternate" hreflang="fr" href="/fr/">').removed.length,
    0,
  );
  eq(
    'an RSS alternate for a feed route IS removed — the route is PHP and is gone',
    stripWordPressHeadLinks(
      '<link rel="alternate" type="application/rss+xml" href="https://x.org/feed/">',
    ).removed.length,
    1,
  );

  // --- De-WordPressing: the blank-page trap --------------------------------
  // The single most dangerous line in this pass, and the one invisible in a
  // diff of the scripts. Divi paints animated content at opacity 0 and reveals
  // it from the bundle above; the rule was present in 589 of 589 captured
  // pages. Strip the bundle, leave the rule, and the export is 589 blank pages
  // that pass every markup, title, link and byte-count check there is.
  eq(
    'the JavaScript-dependent hiding rule is removed',
    neutralizeWaypointHiding('.et-waypoint:not(.et_pb_counters){opacity:0}').css,
    '',
  );
  eq(
    'and the removal is counted, not silent',
    neutralizeWaypointHiding('.et-waypoint:not(.et_pb_counters){opacity:0}').removed,
    1,
  );
  eq(
    'whitespace variants are caught',
    neutralizeWaypointHiding('.et-waypoint:not(.et_pb_counters) { opacity: 0; }').css,
    '',
  );
  // Divi ships many `.et-waypoint … {opacity:1}` rules — the animation-off
  // variants. Matching on the selector alone would delete those too and break
  // every element whose animation is deliberately disabled.
  eq(
    'an opacity:1 waypoint rule is left alone',
    neutralizeWaypointHiding('.et-waypoint.et_pb_animation_off{opacity:1}').css,
    '.et-waypoint.et_pb_animation_off{opacity:1}',
  );
  eq(
    'a non-waypoint opacity:0 rule is left alone',
    neutralizeWaypointHiding('.screen-reader-text{opacity:0}').css,
    '.screen-reader-text{opacity:0}',
  );
  // A shared rule must lose only its waypoint half, or removing the hiding
  // rule for animated content silently un-hides something unrelated.
  eq(
    'a shared selector list keeps its non-waypoint half',
    neutralizeWaypointHiding('.a,.et-waypoint,.b{opacity:0}').css,
    '.a,.b{opacity:0}',
  );
  eq(
    'only the inline <style> is rewritten, never the body text',
    neutralizeWaypointHidingInHtml(
      '<style>.et-waypoint:not(.et_pb_counters){opacity:0}</style>' +
        '<p>.et-waypoint:not(.et_pb_counters){opacity:0}</p>',
    ).html,
    '<style></style><p>.et-waypoint:not(.et_pb_counters){opacity:0}</p>',
  );

  // --- De-WordPressing: the stylesheet that is not in the markup -----------
  // Divi does not enqueue its per-page "late" stylesheet; an inline script
  // builds the <link> at runtime. Strip that script without hoisting first and
  // the page loses the stylesheet AND the capture stops downloading it, which
  // renders as a correct-looking page with a chunk of its styling gone.
  const LATE_CSS =
    '<html><head><link rel="stylesheet" href="/main.css"></head><body>' +
    '<script>(function(){var file=["\\/wp-content\\/et-cache\\/2\\/et-late.css"];' +
    "var link=document.createElement('link');link.rel='stylesheet';link.href=file;" +
    '})();</script></body></html>';
  eq('a runtime-injected stylesheet is hoisted', hoistRuntimeStylesheets(LATE_CSS).hoisted, [
    '/wp-content/et-cache/2/et-late.css',
  ]);
  eq(
    'and it lands in the head, where the preload scanner can see it',
    hoistRuntimeStylesheets(LATE_CSS).html.includes(
      '<link rel="stylesheet" href="/wp-content/et-cache/2/et-late.css"></head>',
    ),
    true,
  );
  eq(
    'a stylesheet the page already links is not duplicated',
    hoistRuntimeStylesheets(
      '<head><link rel="stylesheet" href="/a.css"></head>' +
        "<script>var l=document.createElement('link');l.rel='stylesheet';l.href=\"/a.css\";</script>",
    ).hoisted,
    [],
  );
  // Recognised by the injection signature, not by "mentions a .css" — a config
  // blob naming a stylesheet must not cause one to be loaded.
  eq(
    'a script that merely names a stylesheet injects nothing',
    hoistRuntimeStylesheets('<script>var cfg={"admin":"/wp-admin/css/edit.css"};</script>').hoisted,
    [],
  );
  eq(
    'structured data is never treated as an injector',
    hoistRuntimeStylesheets(
      '<script type="application/ld+json">{"x":"document.createElement(\'link\') /a.css"}</script>',
    ).hoisted,
    [],
  );
  // The value becomes a double-quoted attribute. Two things keep that safe and
  // they are worth separating, because only one of them is a check.
  //
  // A quote cannot reach the attribute at all: the extraction matches
  // `[^"']+`, so a `"` terminates the literal instead of being captured. That
  // is structural, and this case pins it — a script whose string tries to
  // break out yields the harmless prefix, never the payload.
  eq(
    'a quote terminates the literal instead of escaping into the attribute',
    hoistRuntimeStylesheets(
      "<script>document.createElement('link');var f='/a.css\" onload=\"x';</script>",
    ).hoisted,
    ['/a.css'],
  );
  // Angle brackets CAN survive the extraction, so those are refused explicitly.
  eq(
    'an angle-bracket href is refused rather than emitted',
    hoistRuntimeStylesheets(
      "<script>document.createElement('link');var f='/a.css?v=<img src=x>';</script>",
    ).hoisted,
    [],
  );
  eq(
    'hoisting runs before the strip, so the whole pass keeps the stylesheet',
    deWordPress(LATE_CSS).html.includes('href="/wp-content/et-cache/2/et-late.css"'),
    true,
  );

  // --- Accessibility defects the CMS emits, which are not the site's words --
  // Measured with Lighthouse on the real capture: these two carry weights 10
  // and 3, the largest accessibility failures on the page, and fixing them
  // moved the score 79 -> 95.
  eq(
    'a viewport that disables pinch-zoom is relaxed',
    normalizeAccessibility(
      '<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=0">',
    ).html,
    '<meta name="viewport" content="width=device-width, initial-scale=1.0">',
  );
  eq(
    'the useful half of the viewport survives',
    normalizeAccessibility('<meta name="viewport" content="width=device-width, user-scalable=no">')
      .html,
    '<meta name="viewport" content="width=device-width">',
  );
  eq(
    'user-scalable=yes is left alone — nothing to fix',
    normalizeAccessibility('<meta name="viewport" content="width=device-width, user-scalable=yes">')
      .fixed,
    [],
  );
  // 5x is the WCAG/Lighthouse threshold; a larger cap is the author's business.
  eq(
    'a zoom cap of 5 or more is the author’s call and is kept',
    normalizeAccessibility('<meta name="viewport" content="maximum-scale=6.0">').fixed,
    [],
  );
  eq(
    'a cap under 5 is a restriction and goes',
    normalizeAccessibility('<meta name="viewport" content="maximum-scale=2">').fixed,
    ['viewport'],
  );
  eq(
    'a non-viewport meta is untouched',
    normalizeAccessibility('<meta name="description" content="user-scalable=0">').fixed,
    [],
  );
  // The same `\b`-inside-a-hyphenated-attribute trap the `data-role` case
  // below documents, on the meta side. Both were live: measured before the
  // fix, `data-name="viewport"` was rewritten as though it were the viewport
  // tag, and — worse — a real viewport tag carrying `data-content` had that
  // attribute read as its directives and then OVERWRITTEN with the filtered
  // value, emitting `data-content=""` while the actual viewport went
  // unfiltered. An unrelated attribute silently emptied is a fidelity defect,
  // not a cosmetic one. Raised in review on #1239 against the mirroring
  // assertion in test_705; the pass had it too.
  eq(
    'a data-name attribute does not masquerade as the viewport meta',
    normalizeAccessibility(
      '<meta data-name="viewport" content="width=device-width, user-scalable=no">',
    ).fixed,
    [],
  );
  eq(
    'a data-content attribute is neither read as nor overwritten by the filter',
    normalizeAccessibility(
      '<meta name="viewport" data-content="keep-me" content="width=device-width, user-scalable=no">',
    ).html,
    '<meta name="viewport" data-content="keep-me" content="width=device-width">',
  );
  // The pass advertises itself as case- and whitespace-insensitive (`gi`, and
  // `\s*` around each `=`). Asserted rather than assumed, because the smoke
  // test that reads this output was written case-SENSITIVE and would have
  // failed on correct markup.
  eq(
    'an uppercase viewport tag is filtered too',
    normalizeAccessibility('<META NAME="viewport" CONTENT="width=device-width, user-scalable=no">')
      .fixed,
    ['viewport'],
  );
  eq(
    'spaces around the attribute equals signs do not hide the tag',
    normalizeAccessibility(
      '<meta name = "viewport" content = "width=device-width, maximum-scale=1">',
    ).fixed,
    ['viewport'],
  );
  eq(
    'the main wrapper gains a landmark role',
    normalizeAccessibility('<div id="main-content" class="x">a</div>').html,
    '<div id="main-content" class="x" role="main">a</div>',
  );
  // Retagging to <main> would mean finding the matching </div> through
  // arbitrarily nested markup, and a mis-balanced close corrupts every page.
  eq(
    'an existing role is not overwritten',
    normalizeAccessibility('<div id="main-content" role="region">a</div>').fixed,
    [],
  );
  // `data-role` is not a role. `\b` sits inside it, so the naive check reported
  // "already has a role" and dropped the landmark.
  eq(
    'a data-role attribute does not masquerade as a role',
    normalizeAccessibility('<div id="main-content" data-role="banner">a</div>').fixed,
    ['main-landmark'],
  );
  eq(
    'a role attribute at the very start of the attribute list still counts',
    normalizeAccessibility('<div role="region" id="main-content">a</div>').fixed,
    [],
  );
  eq(
    'a page with no main wrapper is left alone rather than guessed at',
    normalizeAccessibility('<div id="content">a</div>').fixed,
    [],
  );
  eq(
    'both fixes are reported by the whole pass',
    deWordPress(
      '<html><head><meta name="viewport" content="width=device-width, user-scalable=no">' +
        '</head><body><div id="main-content">a</div></body></html>',
    ).accessibilityFixes,
    ['viewport', 'main-landmark'],
  );

  // --- De-WordPressing: the replacement runtime ----------------------------
  eq(
    'the runtime is injected just inside </body>',
    injectCloneRuntime('<html><body><p>a</p></body></html>', '../x/clone-enhance.js'),
    '<html><body><p>a</p><script src="../x/clone-enhance.js" defer></script></body></html>',
  );
  eq(
    'a page with no </body> still gets the runtime rather than silently losing it',
    injectCloneRuntime('<p>a</p>', './r.js'),
    '<p>a</p><script src="./r.js" defer></script>',
  );
  eq(
    'injection is idempotent',
    injectCloneRuntime(injectCloneRuntime('<body>a</body>', './r.js'), './r.js'),
    '<body>a<script src="./r.js" defer></script></body>',
  );
  // The LAST closer, not the first. A captured page can carry a literal
  // `</body>` before its real one — inside an HTML comment, a <template>, or a
  // builder shortcode that stored a whole document as text. Injecting at the
  // first match puts the runtime inside that fragment, where it is inert, and
  // the page still looks perfectly well-formed.
  eq(
    'the runtime goes before the LAST </body>, not a literal one in the markup',
    injectCloneRuntime('<body>a<!-- </body> --><p>b</p></body>', './r.js'),
    '<body>a<!-- </body> --><p>b</p><script src="./r.js" defer></script></body>',
  );

  // --- De-WordPressing: the whole pass -------------------------------------
  const DEWP = deWordPress(
    '<html><head><link rel="shortlink" href="https://x.org/?p=2">' +
      '<style>.et-waypoint:not(.et_pb_counters){opacity:0}</style>' +
      '<script src="/wp-includes/js/jquery/jquery.min.js"></script></head>' +
      '<body><p>hi</p></body></html>',
    { runtimeHref: './_a/clone-enhance.js' },
  );
  eq('the pass reports every script it removed', DEWP.scriptsRemoved, 1);
  eq('the pass reports the hiding rules it removed', DEWP.waypointRulesRemoved, 1);
  eq('the pass reports the head links it removed', DEWP.headLinksRemoved.length, 1);
  eq(
    'the only script left is the clone runtime',
    [...DEWP.html.matchAll(/<script\b[^>]*>/gi)].map((m) => m[0]),
    ['<script src="./_a/clone-enhance.js" defer>'],
  );
  eq('and the content survives all of it', DEWP.html.includes('<p>hi</p>'), true);
  eq(
    'without a runtime href the pass injects nothing — the capture adds it per page',
    deWordPress('<body>a</body>').html.includes('<script'),
    false,
  );

  // --- Cloudflare email obfuscation ----------------------------------------
  // Decoding is what makes the strip above safe: remove the decoder without
  // this and every obfuscated address renders as hex forever.
  // "info@vpmi.org" XOR-encoded against key 0x2a, per Cloudflare's scheme.
  const cfHex = (addr, key = 0x2a) =>
    key.toString(16).padStart(2, '0') +
    [...addr].map((c) => (c.charCodeAt(0) ^ key).toString(16).padStart(2, '0')).join('');
  const enc = cfHex('info@vpmi.org');
  eq(
    'an obfuscated address span decodes to the real address',
    decodeCloudflareEmails(
      `<span class="__cf_email__" data-cfemail="${enc}">[email&#160;protected]</span>`,
    ).html,
    'info@vpmi.org',
  );
  eq(
    'an email-protection href becomes a real mailto',
    decodeCloudflareEmails(`<a href="/cdn-cgi/l/email-protection#${enc}">write</a>`).html,
    '<a href="mailto:info@vpmi.org">write</a>',
  );
  eq(
    'a decode that does not yield an address leaves the markup untouched',
    decodeCloudflareEmails('<span data-cfemail="2a2a2a2a">x</span>').html,
    '<span data-cfemail="2a2a2a2a">x</span>',
  );
  eq(
    'non-hex is refused rather than decoded into nonsense',
    decodeCloudflareEmails('<a href="/cdn-cgi/l/email-protection#zzzz">w</a>').html,
    '<a href="/cdn-cgi/l/email-protection#zzzz">w</a>',
  );
  eq('a page with no obfuscation is unchanged', decodeCloudflareEmails('<p>a@b.co</p>').decoded, 0);

  // --- Delivery attempt 4: the two defects the gate caught at 118/120 -------
  // 1. A percent-encoded filename was written to disk verbatim, so the file was
  //    named `…%C3%97…` while every request for it decodes to `…×…`. The asset
  //    was unreachable in the export and would have been on GitHub Pages too.
  eq(
    'a non-ASCII filename is stored decoded, so the request round-trips',
    assetLocalName('https://h.org/wp-content/uploads/Digest-2160-%C3%97-1080-px.jpg'),
    'h.org/wp-content/uploads/Digest-2160-×-1080-px.jpg',
  );
  eq(
    'the round trip actually closes: what a browser asks for is what is on disk',
    decodeURIComponent(encodeURI(assetLocalName('https://h.org/a/Digest-2160-%C3%97-1080-px.jpg'))),
    assetLocalName('https://h.org/a/Digest-2160-%C3%97-1080-px.jpg'),
  );
  eq(
    'an accented upload decodes too',
    assetLocalName('https://h.org/uploads/Cr%C3%A8che.png'),
    'h.org/uploads/Crèche.png',
  );
  // The safety property the decode must not cost. `new URL()` normalises a real
  // `../` away, so a surviving `..` is percent-encoded — decoding it would turn
  // an inert literal into genuine traversal.
  eq(
    'encoded traversal is NOT decoded into real traversal',
    assetLocalName('https://h.org/..%2f..%2fetc/x.png'),
    'h.org/..%2f..%2fetc/x.png',
  );
  // Three forms, and they are defended in two different places — worth stating
  // because only the third is this decode's responsibility.
  eq(
    'encoded DOTS with a real slash are normalised away by the URL parser',
    assetLocalName('https://h.org/a/%2e%2e/x.png'),
    'h.org/x.png',
  );
  eq(
    'a fully-encoded ../ stays literal, uppercase included',
    assetLocalName('https://h.org/a/%2E%2E%2Fx.png'),
    'h.org/a/%2E%2E%2Fx.png',
  );
  eq(
    'a malformed escape keeps the asset rather than throwing',
    assetLocalName('https://h.org/a/100%.png'),
    'h.org/a/100%.png',
  );

  // 2. An asset referenced ONLY inside an inline <script> config blob. This was
  //    the one URL the source still served (HTTP 200) that the clone had lost.
  const EMOJI =
    '<script>window._wpemojiSettings = {"source":{"concatemoji":' +
    '"https:\\/\\/h.org\\/wp-includes\\/js\\/wp-emoji-release.min.js?ver=7.1"}};</script>';
  eq(
    'a URL inside an inline script blob is collected',
    [...collectAssetUrls(EMOJI)],
    ['https://h.org/wp-includes/js/wp-emoji-release.min.js?ver=7.1'],
  );
  eq(
    "a script's ordinary string literals are not dragged in as assets",
    [...collectAssetUrls('<script>var a="hello world";var b="/api/v1/thing";</script>')],
    [],
  );
  eq(
    'a single-quoted script URL is collected too',
    [...collectAssetUrls("<script>load('/wp-content/x.js')</script>")],
    ['/wp-content/x.js'],
  );

  // 3. `&amp;` is how a multi-parameter URL is spelled in an attribute. Fetching
  //    the literal entity asks the origin for a query string it does not have.
  eq(
    'an &amp; in an attribute URL is decoded before fetching',
    [...collectAssetUrls('<script src="/wp-content/js/bilmur.min.js?i=17&amp;m=202636"></script>')],
    ['/wp-content/js/bilmur.min.js?i=17&m=202636'],
  );
  eq(
    'the numeric entity form is decoded as well',
    [...collectAssetUrls('<img src="/a.png?x=1&#038;y=2">')],
    ['/a.png?x=1&y=2'],
  );
  // EXACTLY once, and a doubly-escaped entity is the case that proves it.
  // An HTML serializer escapes an attribute value once, so one pass recovers
  // the text the browser sees — and the browser then requests that text
  // verbatim. Measured on this migration: the page carried `?i=17&amp;m=…`
  // and Playwright requested `?i=17&m=…`. If a page really carries
  // `&amp;amp;`, the browser requests a parameter literally named `amp;m`,
  // and the capture must fetch the same thing or it is mirroring a URL the
  // live site never serves.
  //
  // Decoding "until stable" would also make the number of passes depend on
  // the CONTENT rather than on the known encoding layers, which is the
  // double-decode anti-pattern: a query value that legitimately contains the
  // text `&amp;` would be silently rewritten.
  eq(
    'a doubly-escaped entity is decoded ONCE, matching what a browser requests',
    [...collectAssetUrls('<img src="/a.png?x=1&amp;amp;y=2">')],
    ['/a.png?x=1&amp;y=2'],
  );

  // Never fetch into a private network on a page's say-so.
  eq('isPrivateHost blocks localhost', isPrivateHost('localhost'), true);
  eq('isPrivateHost blocks loopback', isPrivateHost('127.0.0.1'), true);
  eq('isPrivateHost blocks cloud metadata', isPrivateHost('169.254.169.254'), true);
  eq('isPrivateHost blocks 10/8', isPrivateHost('10.1.2.3'), true);
  eq('isPrivateHost blocks 172.16/12', isPrivateHost('172.20.0.1'), true);
  eq('isPrivateHost allows 172.15, outside the range', isPrivateHost('172.15.0.1'), false);
  eq('isPrivateHost allows 172.32, outside the range', isPrivateHost('172.32.0.1'), false);
  eq('isPrivateHost blocks 192.168/16', isPrivateHost('192.168.1.1'), true);
  eq('isPrivateHost blocks an IPv6 literal', isPrivateHost('[::1]'), true);
  eq('isPrivateHost blocks .internal', isPrivateHost('db.internal'), true);
  eq('isPrivateHost allows a normal public host', isPrivateHost('vpmin.org'), false);
  eq('isPrivateHost allows a public IPv4', isPrivateHost('93.184.216.34'), false);
  eq(
    'shouldLocalize refuses a private host even with an asset extension',
    shouldLocalize('http://169.254.169.254/latest/meta-data/x.png', 'x.org'),
    false,
  );

  // The rewrite must only touch links the page actually contains.
  // -- isSiteHost: a 200 does not prove the body came from the site ----------
  eq(
    'isSiteHost accepts the domain, its www form and a subdomain',
    [
      isSiteHost('https://new.org/a/', 'new.org'),
      isSiteHost('https://www.new.org/a/', 'new.org'),
      isSiteHost('https://cdn.new.org/a/', 'new.org'),
    ],
    [true, true, true],
  );
  eq(
    'isSiteHost REFUSES the stale home host — the parked-page case',
    isSiteHost('https://old.org/', 'new.org'),
    false,
  );
  eq(
    'isSiteHost is not fooled by a domain that merely ends with the name',
    isSiteHost('https://notnew.org/a/', 'new.org'),
    false,
  );
  eq(
    'isSiteHost refuses a non-http scheme and a malformed url',
    [isSiteHost('data:text/html,x', 'new.org'), isSiteHost('not a url', 'new.org')],
    [false, false],
  );

  // -- classifyPageResponse: the decision the scrape loop dispatches on -----
  eq(
    'classifyPageResponse stores a 200 that stayed on the site',
    classifyPageResponse({
      status: 200,
      finalUrl: 'https://new.org/about-us/',
      requestUrl: 'https://new.org/about-us/',
      domain: 'new.org',
    }).action,
    'store',
  );
  eq(
    'classifyPageResponse REFUSES a 200 that redirected to the stale home host',
    classifyPageResponse({
      status: 200,
      finalUrl: 'https://old.org/',
      requestUrl: 'https://new.org/',
      domain: 'new.org',
    }),
    {
      action: 'skip',
      status: -2,
      offSite: true,
      finalUrl: 'https://old.org/',
      reason: 'followed off-site to https://old.org/',
    },
  );
  eq(
    'classifyPageResponse skips a non-200 without calling it off-site',
    classifyPageResponse({ status: 404, requestUrl: 'https://new.org/x/', domain: 'new.org' }),
    { action: 'skip', status: 404, reason: 'HTTP 404' },
  );
  eq(
    'classifyPageResponse treats an absent final url as the requested one',
    classifyPageResponse({
      status: 200,
      finalUrl: '',
      requestUrl: 'https://new.org/a/',
      domain: 'new.org',
    }).action,
    'store',
  );
  eq(
    'classifyPageResponse refuses an unreachable response rather than storing it',
    classifyPageResponse({ status: 0, requestUrl: 'https://new.org/a/', domain: 'new.org' }).action,
    'skip',
  );

  // -- normalizedLinkIndex: the bug that shipped 562 pages of off-site nav ---
  // The entry list is normalized onto the serving host; the markup still says
  // the stale one. Comparing the two as strings finds nothing.
  {
    const html =
      '<a href="https://old.org/about-us/">A</a>' +
      '<a href="https://old.org/donate/">D</a>' +
      '<a href="https://elsewhere.net/x/">X</a>';
    const idx = normalizedLinkIndex(html, 'https://new.org/', 'old.org', 'new.org');
    eq(
      'normalizedLinkIndex resolves a STALE-host href onto the entry key',
      [...(idx.get('https://new.org/about-us') ?? [])],
      ['https://old.org/about-us/'],
    );
    eq(
      'normalizedLinkIndex leaves a genuinely third-party link on its own key',
      idx.get('https://new.org/x'),
      undefined,
    );
    // The regression this replaced: a raw-string comparison against the
    // normalized entry link.
    eq(
      'the old string comparison would have matched nothing',
      collectPageLinks(html).has('https://new.org/about-us/'),
      false,
    );
  }
  {
    // All three spellings of one destination collapse onto one key. The
    // root-absolute form matters on project pages, where the site is mounted
    // under a prefix and `/about-us/` resolves outside it.
    const html =
      '<a href="https://new.org/about-us/">abs</a>' +
      '<a href="/about-us/">root</a>' +
      '<a href="../about-us/">rel</a>';
    const idx = normalizedLinkIndex(html, 'https://new.org/ministries/', null, 'new.org');
    eq(
      'normalizedLinkIndex folds absolute, root-absolute and relative spellings together',
      [...(idx.get('https://new.org/about-us') ?? [])].sort(),
      ['../about-us/', '/about-us/', 'https://new.org/about-us/'],
    );
  }
  eq(
    'linkKey folds the trailing slash so /a/ and /a share one key',
    [linkKey('https://x.org/a/'), linkKey('https://x.org/a')],
    ['https://x.org/a', 'https://x.org/a'],
  );

  // -- remainingSelfHostLinks: the gate's blind spot, closed ----------------
  {
    const page =
      '<a href="https://old.org/about-us/">nav</a>' +
      '<a href="https://old.org/donate/">nav</a>' +
      '<a href="https://new.org/feed/">feed</a>' +
      '<a href="https://example.net/partner/">outbound</a>' +
      '<a href="../contact/">local</a>';
    const found = remainingSelfHostLinks(page, 'new.org', 'old.org');
    eq(
      'remainingSelfHostLinks counts the stale host and the serving host apart',
      [...found.entries()].sort(),
      [
        ['new.org', 1],
        ['old.org', 2],
      ],
    );
    eq(
      'remainingSelfHostLinks leaves genuine outbound links alone',
      found.has('example.net'),
      false,
    );
    eq(
      'remainingSelfHostLinks reports nothing once the links are local',
      [...remainingSelfHostLinks('<a href="../about-us/">a</a>', 'new.org', 'old.org').keys()],
      [],
    );
  }
  eq(
    'captureVerdict fails a clone whose navigation still points at the stale host',
    captureVerdict({
      expected: 5,
      captured: 5,
      externalHosts: [],
      failedAssets: 0,
      strandedStaleLinks: 44,
      strandedStalePages: 562,
      staleHost: 'old.org',
    }).problems.filter((s) => s.includes('old.org')).length,
    1,
  );
  eq(
    'captureVerdict does NOT fail on links to the serving domain — /feed/ has no local copy',
    captureVerdict({
      expected: 5,
      captured: 5,
      externalHosts: [],
      failedAssets: 0,
      strandedStaleLinks: 0,
      staleHost: 'old.org',
    }).ok,
    true,
  );

  // -- the front page is not one entry among many ---------------------------
  // Asserted on the PROBLEM TEXT, not on `ok`. At 589/590 the completeness
  // term fails too, so `ok === false` stays false with this rule deleted — the
  // first draft of this test passed a mutation that removed the thing it was
  // written to pin.
  eq(
    'captureVerdict names the front page as its own problem, not just a count',
    captureVerdict({
      expected: 590,
      captured: 589,
      externalHosts: [],
      failedAssets: 0,
      frontPageCaptured: false,
    }).problems.filter((s) => s.includes('front page')).length,
    1,
  );
  eq(
    'captureVerdict fails on a missing front page even when nothing else is wrong',
    captureVerdict({
      expected: 590,
      captured: 590,
      externalHosts: [],
      failedAssets: 0,
      frontPageCaptured: false,
    }).ok,
    false,
  );
  eq(
    'captureVerdict stays silent about the front page when it was captured',
    captureVerdict({
      expected: 590,
      captured: 590,
      externalHosts: [],
      failedAssets: 0,
      frontPageCaptured: true,
    }).problems,
    [],
  );

  eq(
    'collectPageLinks reads hrefs',
    [...collectPageLinks('<a href="https://x.org/a/">A</a><a href=\'/b\'>B</a>')].sort(),
    ['/b', 'https://x.org/a/'],
  );
  eq('collectPageLinks is empty for a page with no links', [...collectPageLinks('<p>hi</p>')], []);

  // NaN is silently catastrophic here, not loud.
  eq('parsePositiveInt accepts an integer', parsePositiveInt('500', { min: 1, max: 1000 }), 500);
  eq('parsePositiveInt rejects junk', parsePositiveInt('abc', { min: 1, max: 1000 }), null);
  eq('parsePositiveInt rejects a negative', parsePositiveInt('-5', { min: 0, max: 1000 }), null);
  eq('parsePositiveInt rejects a float', parsePositiveInt('2.5', { min: 0, max: 1000 }), null);
  eq(
    'parsePositiveInt rejects out of range',
    parsePositiveInt('99999', { min: 1, max: 1000 }),
    null,
  );
  eq('parsePositiveInt rejects empty', parsePositiveInt('', { min: 0, max: 10 }), null);
  eq(
    'parsePositiveInt rejects what parseInt would silently truncate',
    parsePositiveInt('30s', { min: 1, max: 600 }),
    null,
  );
  eq('parsePositiveInt allows zero when min is 0', parsePositiveInt('0', { min: 0, max: 10 }), 0);

  eq(
    'isContainedPath allows a normal nested path',
    isContainedPath('/out', 'a/b/index.html'),
    true,
  );
  eq('isContainedPath allows the root itself', isContainedPath('/out', '.'), true);
  eq('isContainedPath rejects a parent escape', isContainedPath('/out', '../evil.html'), false);
  eq(
    'isContainedPath rejects a deep parent escape',
    isContainedPath('/out', 'a/../../../etc/passwd'),
    false,
  );
  eq('isContainedPath rejects an absolute path', isContainedPath('/out', '/etc/passwd'), false);
  eq(
    'isContainedPath is not fooled by a sibling with a shared prefix',
    isContainedPath('/out', '../outside/x'),
    false,
  );

  // og:image / twitter:image — the docstring claimed these and the code did not
  // read them, so a social preview kept pointing at the decommissioned host.
  eq(
    'collectAssetUrls reads og:image',
    collectAssetUrls('<meta property="og:image" content="https://x.org/social.png">'),
    ['https://x.org/social.png'],
  );
  eq(
    'collectAssetUrls reads twitter:image',
    collectAssetUrls('<meta name="twitter:image" content="/tw.png">'),
    ['/tw.png'],
  );
  eq(
    'collectAssetUrls ignores non-image meta content',
    collectAssetUrls(
      '<meta name="description" content="A charity in Ohio."><meta name="viewport" content="width=device-width">',
    ),
    [],
  );
  // The case above passes even with no property filter at all, because
  // looksLikeAssetRef rejects prose — so it proves nothing about the filter.
  // og:url is the discriminating case: it IS a well-formed URL, and it is a
  // page rather than an asset, so only the property filter can exclude it.
  eq(
    'collectAssetUrls ignores og:url — a page URL, not an asset',
    collectAssetUrls('<meta property="og:url" content="https://x.org/about/">'),
    [],
  );

  eq(
    'collectCssUrls finds url() and @import',
    collectCssUrls("@import url('a.css'); body{background:url(b.png)}").sort(),
    ['a.css', 'b.png'],
  );

  // Regression anchors from the first live vpmin.org capture, which fetched
  // https://vpmin.org/r and https://vpmin.org/about/r (both 404) and failed
  // its own gate, because a case-insensitive whole-document `url(` scan
  // matched JavaScript's `new URL(r)` in a minified script.
  eq(
    'collectAssetUrls ignores url( inside <script>',
    collectAssetUrls('<script>var u=new URL(r);fetch(URL(x))</script>'),
    [],
  );
  eq(
    'collectAssetUrls still finds url() in a <style> block',
    collectAssetUrls('<style>.hero{background:url(/img/hero.jpg)}</style>'),
    ['/img/hero.jpg'],
  );
  eq(
    'collectAssetUrls still finds url() in a style attribute',
    collectAssetUrls(`<div style="background:url('/bg.png')"></div>`),
    ['/bg.png'],
  );
  eq('looksLikeAssetRef rejects a bare identifier', looksLikeAssetRef('r'), false);
  eq('looksLikeAssetRef rejects a css keyword', looksLikeAssetRef('transparent'), false);
  eq('looksLikeAssetRef accepts a root-relative path', looksLikeAssetRef('/a/b.png'), true);
  eq(
    'looksLikeAssetRef accepts a bare filename with an extension',
    looksLikeAssetRef('b.png'),
    true,
  );
  eq('looksLikeAssetRef accepts an absolute URL', looksLikeAssetRef('https://x.org/a.png'), true);
  eq('looksLikeAssetRef rejects a non-http scheme', looksLikeAssetRef('about:blank'), false);

  eq('absolutize relative', absolutize('/a.png', 'https://x.org/about/'), 'https://x.org/a.png');
  eq(
    'absolutize protocol-relative',
    absolutize('//cdn.io/a.png', 'https://x.org/'),
    'https://cdn.io/a.png',
  );
  eq('absolutize rejects mailto', absolutize('mailto:a@b.c', 'https://x.org/'), null);

  eq('shouldLocalize same host', shouldLocalize('https://x.org/a.png', 'x.org'), true);
  eq('shouldLocalize www of same host', shouldLocalize('https://www.x.org/a.png', 'x.org'), true);
  eq(
    'shouldLocalize google fonts',
    shouldLocalize('https://fonts.gstatic.com/f.woff2', 'x.org'),
    true,
  );
  eq(
    'shouldLocalize wordpress.com image CDN',
    shouldLocalize('https://i0.wp.com/x.org/a.png', 'x.org'),
    true,
  );
  eq(
    'shouldLocalize wordpress.com media library',
    shouldLocalize('https://vpmin.files.wordpress.com/2024/logo.png', 'x.org'),
    true,
  );
  eq(
    'shouldLocalize wordpress.com core assets',
    shouldLocalize('https://s.w.org/a.js', 'x.org'),
    true,
  );
  eq(
    'shouldLocalize keeps youtube external',
    shouldLocalize('https://youtube.com/embed/1', 'x.org'),
    false,
  );
  eq(
    'shouldLocalize keeps donorbox external',
    shouldLocalize('https://donorbox.org/w', 'x.org'),
    false,
  );
  eq(
    'shouldLocalize foreign html page',
    shouldLocalize('https://other.org/about/', 'x.org'),
    false,
  );
  eq(
    'shouldLocalize foreign asset by extension',
    shouldLocalize('https://other.org/a.png', 'x.org'),
    true,
  );

  eq('assetLocalName namespaces by host', assetLocalName('https://x.org/a/b.png'), 'x.org/a/b.png');
  eq(
    'assetLocalName keeps query distinct, extension last',
    assetLocalName('https://x.org/s.css?ver=6.4'),
    'x.org/s-ver-6-4.css'.replace('s-ver', 's__ver'),
  );
  eq(
    'assetLocalName gives extensionless a suffix',
    assetLocalName('https://x.org/thing'),
    'x.org/thing.bin',
  );
  {
    // A Google Fonts request naming dozens of families -- ksgf.org's actually
    // ran past 4,000 characters -- must not be embedded raw: the filename
    // (not just the URL) is capped at 255 bytes on the filesystems this ends
    // up on, and writing it verbatim crashes with ENAMETOOLONG.
    const longQuery = `?family=${'A'.repeat(500)}`;
    const longName = assetLocalName(`https://fonts.googleapis.com/css${longQuery}`);
    const basename = longName.split('/').pop();
    eq('assetLocalName caps an oversized query-derived filename', basename.length < 200, true);
    eq(
      'assetLocalName stays deterministic past the cap (same URL -> same file)',
      assetLocalName(`https://fonts.googleapis.com/css${longQuery}`),
      longName,
    );
    eq(
      'assetLocalName still varies past the cap (distinct URLs -> distinct files)',
      assetLocalName(`https://fonts.googleapis.com/css?family=${'B'.repeat(500)}`) !== longName,
      true,
    );
  }

  // Longest-first rewriting: the short URL is a prefix of the long one.
  const reps = new Map([
    ['https://x.org/logo.png', 'assets/logo.png'],
    ['https://x.org/logo.png?ver=2', 'assets/logo__ver-2.png'],
  ]);
  eq(
    'rewriteRefs does not corrupt prefix-sharing URLs',
    rewriteRefs('<img src="https://x.org/logo.png?ver=2">', reps),
    '<img src="assets/logo__ver-2.png">',
  );
  eq(
    'rewriteRefs also rewrites escaped-JSON copies',
    rewriteRefs('{"u":"https:\\/\\/x.org\\/logo.png"}', reps),
    '{"u":"assets\\/logo.png"}',
  );

  eq(
    'remainingExternalAssetHosts reports an unlocalized asset host',
    remainingExternalAssetHosts('<img src="https://cdn.example.net/a.png">', 'x.org'),
    ['cdn.example.net'],
  );
  eq(
    'remainingExternalAssetHosts ignores kept-external embeds',
    remainingExternalAssetHosts('<iframe src="https://youtube.com/embed/1"></iframe>', 'x.org'),
    [],
  );

  // Regression anchor for the conflated counter: 19 pages and a 587-URL
  // sitemap must not report `pages.captured` as the whole rendered set.
  const sc = summarizeCaptured(
    [
      { localPath: 'index.html', type: 'front', source: 'front' },
      { localPath: 'a/index.html', type: 'page', source: 'rest' },
      { localPath: 'b/index.html', type: 'page', source: 'rest' },
      { localPath: 'p1/index.html', type: 'post', source: 'rest' },
      { localPath: 's1/index.html', type: 'sitemap', source: 'sitemap' },
      { localPath: 'missed/index.html', type: 'page', source: 'rest' },
    ],
    new Set(['index.html', 'a/index.html', 'b/index.html', 'p1/index.html', 's1/index.html']),
  );
  eq('summarizeCaptured counts only rendered entries', sc.total, 5);
  eq('summarizeCaptured separates pages from the total', sc.byType.page, 2);
  eq('summarizeCaptured counts posts separately', sc.byType.post, 1);
  eq('summarizeCaptured counts sitemap-only entries separately', sc.byType.sitemap, 1);
  eq('summarizeCaptured attributes by inventory source', sc.bySource, {
    front: 1,
    rest: 3,
    sitemap: 1,
  });
  eq(
    'summarizeCaptured omits an entry that never rendered',
    Object.values(sc.byType).reduce((a, b) => a + b, 0),
    5,
  );

  // Regression anchor from the first capture of the real site: 823 asset
  // failures, all on a host that is NOT the site being captured. The count
  // alone said nothing; the breakdown by host is the diagnosis.
  const failTally = tallyFailures([
    { url: 'https://vpmin.org/wp-content/et-cache/a.css', status: 404 },
    { url: 'https://vpmin.org/wp-content/et-cache/b.css', status: 404 },
    { url: 'https://vpmin.org/wp-content/et-cache/c.css', status: 404 },
    { url: 'https://x.org/missing.png', status: 500 },
  ]);
  eq('tallyFailures totals', failTally.total, 4);
  eq('tallyFailures groups by status', failTally.byStatus, { 404: 3, 500: 1 });
  eq('tallyFailures groups by host', failTally.byHost, { 'vpmin.org': 3, 'x.org': 1 });
  eq(
    'tallyFailures handles an unparseable url',
    tallyFailures([{ url: '???', status: 0 }]).byHost,
    {
      unparseable: 1,
    },
  );
  eq(
    'describeFailures is null when nothing failed',
    describeFailures(tallyFailures([]), 'x.org'),
    null,
  );
  eq(
    'describeFailures names a foreign dominant host as a live-site problem',
    /NOT the site being captured/.test(describeFailures(failTally, 'x.org')),
    true,
  );
  eq(
    'describeFailures does not cry foreign when the host IS the site',
    /NOT the site being captured/.test(
      describeFailures(tallyFailures([{ url: 'https://x.org/a.png', status: 404 }]), 'x.org'),
    ),
    false,
  );
  eq(
    'captureVerdict prefers the diagnostic note over a bare count',
    captureVerdict({
      expected: 1,
      captured: 1,
      externalHosts: [],
      failedAssets: 823,
      assetFailureNote: 'all on vpmin.org',
    }).problems,
    ['assets: all on vpmin.org'],
  );

  eq(
    'captureVerdict clean',
    captureVerdict({ expected: 5, captured: 5, externalHosts: [], failedAssets: 0 }).ok,
    true,
  );
  eq(
    'captureVerdict flags short inventory',
    captureVerdict({ expected: 5, captured: 4, externalHosts: [], failedAssets: 0 }).problems
      .length,
    1,
  );
  eq(
    'captureVerdict flags external hosts and failed assets',
    captureVerdict({ expected: 5, captured: 5, externalHosts: ['cdn.io'], failedAssets: 2 })
      .problems.length,
    2,
  );

  console.log(failures ? `\n${failures} self-test failure(s)` : '\nall self-tests passed');
  process.exit(failures ? 2 : 0);
}

/**
 * True only when this file was executed directly, rather than imported.
 *
 * Without this the module cannot be imported at all: the usage guard below
 * calls process.exit(2) the moment there is no --domain, so `import(...)` from
 * a test or a sibling script dies before it can reach a single exported
 * function. The pure helpers are the reusable half of this file, and they are
 * only reusable if importing is side-effect free.
 */
const isMain = process.argv[1] ? import.meta.url === pathToFileURL(process.argv[1]).href : false;

if (isMain && process.argv.includes('--self-test')) selfTest();

// ---------------------------------------------------------------------------
// NETWORK
// ---------------------------------------------------------------------------

function arg(name, def = undefined) {
  const i = process.argv.indexOf(`--${name}`);
  return i > -1 && process.argv[i + 1] && !process.argv[i + 1].startsWith('--')
    ? process.argv[i + 1]
    : def;
}
const flag = (name) => process.argv.includes(`--${name}`);

const domain = normalizeDomain(arg('domain', ''));
const inspectOnly = flag('inspect');
const outDir = arg('out', '');
// Validated rather than parseInt'd: NaN here is silently catastrophic, not
// loud. See parsePositiveInt.
const numericOptions = [
  ['max', arg('max', '500'), { min: 1, max: 100000 }],
  ['delay', arg('delay', '250'), { min: 0, max: 60000 }],
  ['timeout', arg('timeout', '30'), { min: 1, max: 600 }],
  // Matches the per-file image budget the FFC-EX repos enforce in
  // `__tests__/assets/image-weight.test.ts`. Kept as an option rather than a
  // constant so a repo that raises its own budget can say so here.
  ['max-image-kb', arg('max-image-kb', '400'), { min: 16, max: 100000 }],
];
const parsedOptions = {};
const badOptions = [];
for (const [name, raw, bounds] of numericOptions) {
  const value = parsePositiveInt(raw, bounds);
  if (value === null)
    badOptions.push(`--${name}=${raw} (expected an integer ${bounds.min}..${bounds.max})`);
  else parsedOptions[name] = value;
}
const maxItems = parsedOptions.max;
const delayMs = parsedOptions.delay;
const timeoutMs = parsedOptions.timeout * 1000;
const includePosts = flag('include-posts');
// On by default: an oversized upload is a cost the visitor pays on every view,
// and the receiving repo fails CI on it. `--no-optimize-images` ships the
// captured bytes verbatim, which is the right choice only when the originals
// are themselves the deliverable.
const optimizeImages = !flag('no-optimize-images');
const maxImageBytes = parsedOptions['max-image-kb'] * 1024;
const jsonOut = arg('json-out', '');
// Hosts whose references are dropped from the capture entirely: not fetched,
// not counted as failures, not counted against the "zero external asset hosts"
// gate. See isIgnoredHost.
const ignoreHosts = (arg('ignore-hosts', '') || '')
  .split(',')
  .map((d) => normalizeDomain(d))
  .filter(Boolean);

if (isMain && (!domain || (!inspectOnly && !outDir))) {
  console.error(
    'Usage:\n' +
      '  --domain <domain> --inspect [--json-out <file>]\n' +
      '  --domain <domain> --out <dir> [--max 500] [--delay 250] [--include-posts] [--timeout 30]\n' +
      '      [--no-optimize-images] [--max-image-kb 400]\n' +
      '  --self-test',
  );
  process.exit(2);
}

if (isMain && badOptions.length) {
  console.error(`Invalid numeric option(s):\n  ${badOptions.join('\n  ')}`);
  process.exit(2);
}

const origin = `https://${domain}`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/**
 * One HTTP request with a timeout and a bounded retry.
 *
 * Never throws for an HTTP status — a 404 is data, and the callers all branch
 * on it. It throws only when the request could not be made at all, and returns
 * `{ status: 0 }` when even the retry could not complete, so "unreachable" is
 * a value the verdict logic can see rather than a crash.
 */
async function request(url, { method = 'GET', accept = '*/*', retries = 2 } = {}) {
  for (let attempt = 0; attempt <= retries; attempt++) {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), timeoutMs);
    try {
      const res = await fetch(url, {
        method,
        redirect: 'follow',
        signal: ctl.signal,
        headers: { 'User-Agent': UA, Accept: accept },
      });
      clearTimeout(timer);
      return res;
    } catch (err) {
      clearTimeout(timer);
      if (attempt === retries) {
        console.error(
          `[net] ${method} ${url} failed after ${retries + 1} attempt(s): ${err.message}`,
        );
        return { status: 0, ok: false, headers: new Map(), url, _failed: true };
      }
      await sleep(500 * (attempt + 1));
    }
  }
}

/** Fetch and JSON-parse; returns { status, body } with body null on parse failure. */
async function getJson(url) {
  const res = await request(url, { accept: 'application/json' });
  if (!res || res.status === 0) return { status: 0, body: null, headers: null };
  let body = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }
  return { status: res.status, body, headers: res.headers };
}

/**
 * Resolve which REST flavor this site actually answers on.
 *
 * A `blocked` answer from a SELF-HOSTED candidate stops the search: it is a
 * definite statement about this site (a security plugin or a WAF), and falling
 * through to the dotcom endpoint would then report the vaguer 404 and hide the
 * real, actionable cause. A blocked answer from the dotcom endpoint is the last
 * candidate anyway, so it is reported either way.
 */
async function resolveRestRoot() {
  const observed = [];
  for (const candidate of restRootCandidates(domain)) {
    const { status, body } = await getJson(candidate.indexUrl);
    const verdict = classifyRestIndex(status, body);
    const outcome = { ...candidate, verdict, index: verdict === 'ok' ? body : null, status };
    observed.push(outcome);
    if (verdict === 'ok') return outcome;
    // An explicitly locked-down candidate is a definite answer about this site;
    // stop probing rather than falling through to report the vaguer 404.
    if (verdict === 'blocked') break;
  }
  return (
    pickRestOutcome(observed) ?? {
      kind: null,
      indexUrl: null,
      verdict: 'absent',
      index: null,
      status: 404,
    }
  );
}

/** Collection URL for the resolved flavor. */
function collectionUrl(rest, collection, params) {
  return collectionUrlFor(rest.kind, domain, collection, params);
}

/**
 * Every page URL the site's sitemap advertises, following a sitemap INDEX one
 * level down. WordPress serves this at /wp-sitemap.xml (5.5+) or /sitemap.xml
 * (Yoast, Rank Math, WordPress.com), so both are tried.
 *
 * Bounded to `sitemapChildLimit` children: an index on a large site can name
 * dozens of child sitemaps, and this is a cross-check on the inventory, not a
 * second crawler.
 */
const sitemapChildLimit = 10;
async function collectSitemapUrls() {
  for (const path of ['/wp-sitemap.xml', '/sitemap.xml', '/sitemap_index.xml']) {
    const res = await request(`${origin}${path}`, { accept: 'application/xml', retries: 0 });
    if (!res || res.status !== 200) continue;
    let xml;
    try {
      xml = await res.text();
    } catch {
      continue;
    }
    const { isIndex, urls } = parseSitemapUrls(xml);
    if (!isIndex) return { source: path, urls: sitemapPageUrls(urls, domain) };

    const collected = new Set();
    for (const child of urls.slice(0, sitemapChildLimit)) {
      const childRes = await request(child, { accept: 'application/xml', retries: 0 });
      if (!childRes || childRes.status !== 200) continue;
      let childXml;
      try {
        childXml = await childRes.text();
      } catch {
        continue;
      }
      for (const u of sitemapPageUrls(parseSitemapUrls(childXml).urls, domain)) collected.add(u);
      await sleep(delayMs);
    }
    return {
      source: path,
      urls: [...collected],
      childrenRead: Math.min(urls.length, sitemapChildLimit),
    };
  }
  return { source: null, urls: [] };
}

/**
 * Page through a REST collection.
 *
 * Returns the items AND the server's own `X-WP-Total`, because the completeness
 * gate compares the two. A collection that returns fewer items than the header
 * promised is the failure this whole approach exists to be able to notice.
 */
async function fetchCollection(rest, collection, extraParams = {}) {
  const perPage = 100;
  const items = [];
  let total = null;
  let totalPages = 1;
  let lastStatus = 0;

  for (let page = 1; page <= totalPages && items.length < maxItems; page++) {
    const url = collectionUrl(rest, collection, {
      per_page: String(perPage),
      page: String(page),
      ...extraParams,
    });
    const { status, body, headers } = await getJson(url);
    lastStatus = status;
    if (status !== 200 || !Array.isArray(body)) {
      console.error(`[rest] ${collection} page ${page}: HTTP ${status} — stopping this collection`);
      break;
    }
    if (page === 1 && headers) {
      const t = headers.get?.('x-wp-total');
      const tp = headers.get?.('x-wp-totalpages');
      if (t !== null && t !== undefined && t !== '') total = parseInt(t, 10);
      if (tp) totalPages = parseInt(tp, 10);
    }
    items.push(...body);
    await sleep(delayMs);
  }
  return { items, total: total ?? items.length, totalPages, lastStatus };
}

// --- inspect ---------------------------------------------------------------

async function inspect() {
  const report = { domain, checkedAt: new Date().toISOString() };

  const home = await request(`${origin}/`, { accept: 'text/html' });
  report.home = { status: home.status };
  let homeHtml = '';
  if (home.status && home.status !== 0) {
    report.home.server = home.headers.get?.('server') ?? null;
    report.home.poweredBy = home.headers.get?.('x-powered-by') ?? null;
    report.home.finalUrl = home.url;
    try {
      homeHtml = await home.text();
    } catch {
      homeHtml = '';
    }
    const gen = /<meta[^>]+name=["']generator["'][^>]+content=["']([^"']+)["']/i.exec(
      homeHtml,
    )?.[1];
    report.home.generator = gen ?? null;
    report.home.bytes = homeHtml.length;
  }

  // Which builder/theme, so the operator knows what content.rendered will miss.
  report.builders = [
    'elementor',
    'divi',
    'wpbakery',
    'js_composer',
    'beaver',
    'bricks',
    'gutenberg',
    'avada',
  ].filter((b) => new RegExp(b, 'i').test(homeHtml));
  report.theme = /wp-content\/themes\/([a-z0-9_-]+)/i.exec(homeHtml)?.[1] ?? null;
  report.usesWpContent = /wp-content\//i.test(homeHtml);

  const rest = await resolveRestRoot();
  report.rest = {
    kind: rest.kind,
    indexUrl: rest.indexUrl,
    verdict: rest.verdict,
    status: rest.status,
  };
  // Which flavor answered is the operator-facing fact: `dotcom` means the site
  // is WordPress.com-hosted, so there is no origin server, no cPanel and no
  // filesystem to take a backup from — this API IS the only structured source.
  report.hosting =
    rest.kind === 'dotcom'
      ? 'wordpress.com (no origin filesystem)'
      : rest.kind
        ? 'self-hosted'
        : /wordpress\.com/i.test(report.home.generator ?? '')
          ? 'wordpress.com (REST not reachable)'
          : 'unknown';
  if (rest.index) {
    report.rest.name = rest.index.name ?? null;
    report.rest.description = rest.index.description ?? null;
    report.rest.homeUrl = rest.index.home ?? rest.index.url ?? null;
    report.rest.namespaces = rest.index.namespaces ?? null;
  }

  if (rest.verdict === 'ok') {
    report.collections = {};
    for (const c of ['pages', 'posts', 'media', 'categories', 'tags']) {
      // `status=publish` is invalid on media (attachments are `inherit`), so it
      // is added per collection rather than filtered back out of a built URL.
      const params = { per_page: '1' };
      if (c !== 'media') params.status = 'publish';
      const { status, headers } = await getJson(collectionUrl(rest, c, params));
      report.collections[c] =
        status === 200
          ? { total: parseInt(headers?.get?.('x-wp-total') ?? '0', 10), status }
          : { status };
      await sleep(delayMs);
    }
  }

  for (const [key, path] of [
    ['sitemap', '/wp-sitemap.xml'],
    ['sitemapLegacy', '/sitemap.xml'],
    ['robots', '/robots.txt'],
  ]) {
    const r = await request(`${origin}${path}`, { method: 'GET', accept: '*/*', retries: 0 });
    report[key] = { status: r.status };
  }

  // Second inventory source, compared against the first. A restricted
  // collection (see the media 401 on vpmin.org) makes the REST total an
  // under-count that reports itself as complete, so the two numbers are
  // printed side by side and their disagreement is named.
  const sm = await collectSitemapUrls();
  const pagesTotal = report.collections?.pages?.total ?? 0;
  report.sitemapInventory = {
    source: sm.source,
    pageUrls: sm.urls.length,
    sample: sm.urls.slice(0, 10),
  };
  report.inventoryAgreement =
    sm.source === null
      ? 'no sitemap — REST is the only inventory source'
      : sm.urls.length > pagesTotal
        ? `DISAGREE: sitemap lists ${sm.urls.length} page URL(s), REST reports ${pagesTotal} — capture will take the union`
        : 'agree (sitemap adds nothing beyond the REST inventory)';
  report.restrictedCollections = Object.entries(report.collections ?? {})
    .filter(([, v]) => v.status !== 200)
    .map(([k, v]) => `${k} (HTTP ${v.status})`);

  // Posts count. A blog-only WordPress site legitimately has 0 pages and N
  // posts, and reporting it NO_REST_API would send the operator to a different
  // capture path for a site whose REST API answered perfectly well.
  const postsTotal = report.collections?.posts?.total ?? 0;
  const usable = rest.verdict === 'ok' && (pagesTotal > 0 || postsTotal > 0 || sm.urls.length > 0);
  report.verdict = usable
    ? 'CAPTURE_READY'
    : rest.verdict === 'blocked'
      ? 'API_BLOCKED'
      : rest.verdict === 'unreachable'
        ? 'UNREACHABLE'
        : 'NO_REST_API';

  const text = JSON.stringify(report, null, 2);
  console.log(text);
  if (jsonOut) {
    mkdirSync(dirname(jsonOut), { recursive: true });
    writeFileSync(jsonOut, text, { encoding: 'utf8' });
  }
  if (process.env.GITHUB_STEP_SUMMARY) {
    const s = [
      `## WordPress capture inspect — ${domain}`,
      '',
      `**Verdict: ${report.verdict}**`,
      '',
      `- Home: HTTP ${report.home.status}${report.home.server ? ` (server: ${report.home.server})` : ''}`,
      `- Generator: ${report.home.generator ?? '—'}`,
      `- Theme: ${report.theme ?? '—'}${report.builders.length ? ` · builders: ${report.builders.join(', ')}` : ''}`,
      `- Hosting: ${report.hosting}`,
      `- REST root: ${report.rest.indexUrl ?? '—'} (flavor: ${report.rest.kind ?? 'none'}, ${report.rest.verdict}, HTTP ${report.rest.status})`,
      `- Site name: ${report.rest.name ?? '—'}`,
      '',
      '| Collection | Total |',
      '| ---------- | ----- |',
      ...Object.entries(report.collections ?? {}).map(
        ([k, v]) => `| ${k} | ${v.total ?? `HTTP ${v.status}`} |`,
      ),
      '',
      `- Sitemap: HTTP ${report.sitemap.status} (legacy ${report.sitemapLegacy.status}) · robots.txt: HTTP ${report.robots.status}`,
      `- Sitemap inventory: ${report.sitemapInventory.pageUrls} page URL(s) from ${report.sitemapInventory.source ?? 'none'}`,
      `- Inventory agreement: ${report.inventoryAgreement}`,
      ...(report.restrictedCollections.length
        ? [
            `- ⚠️ Restricted collections (not readable unauthenticated): ${report.restrictedCollections.join(', ')}`,
          ]
        : []),
    ].join('\n');
    writeFileSync(process.env.GITHUB_STEP_SUMMARY, s + '\n', { flag: 'a', encoding: 'utf8' });
  }
  process.exit(report.verdict === 'CAPTURE_READY' ? 0 : 1);
}

// --- capture ---------------------------------------------------------------

async function capture() {
  const rest = await resolveRestRoot();
  if (rest.verdict !== 'ok') {
    console.error(`[capture] REST API is not usable (${rest.verdict}). Run --inspect for detail.`);
    process.exit(1);
  }
  console.error(`[capture] REST root: ${rest.indexUrl} (flavor: ${rest.kind})`);

  // Does this WordPress agree with itself about where it lives? If siteurl/home
  // still name a host that no longer serves the site, every URL the CMS emits
  // — page links, sitemap entries, /wp-content/uploads/ references — points at
  // that host and 404s. Detected here, from the site's own REST index, so no
  // operator has to notice it and name the wrong host by hand.
  const selfHost = declaredSelfHost(rest.index, domain);
  if (selfHost) {
    console.error(
      `[capture] the site declares its home as ${selfHost}, not ${domain}. ` +
        `Its own URLs point at a host that does not serve it; rewriting self-references onto ${domain}.`,
    );
  }

  // 1. INVENTORY from the CMS.
  const pages = await fetchCollection(rest, 'pages', { status: 'publish' });
  console.error(`[capture] pages: ${pages.items.length} of ${pages.total} reported`);
  let posts = { items: [], total: 0 };
  if (includePosts) {
    posts = await fetchCollection(rest, 'posts', { status: 'publish' });
    console.error(`[capture] posts: ${posts.items.length} of ${posts.total} reported`);
  }
  // The media library is a convenience, not a requirement: on a WordPress.com
  // site it answers 401 unauthenticated (measured on vpmin.org). Losing it
  // costs only the images no captured page happens to reference, so it must
  // not fail the run — but it is reported, because silently capturing fewer
  // images than the site has is exactly the kind of quiet shortfall this
  // script exists to make visible.
  const media = await fetchCollection(rest, 'media');
  if (media.lastStatus !== 200) {
    console.error(
      `[capture] media collection unavailable (HTTP ${media.lastStatus}) — falling back to images referenced by the captured pages`,
    );
  } else {
    console.error(`[capture] media: ${media.items.length} of ${media.total} reported`);
  }

  const entries = [...pages.items, ...posts.items]
    .filter((it) => it && it.link)
    .map((it) => ({
      id: it.id,
      type: it.type,
      slug: it.slug,
      link: it.link,
      title: it.title?.rendered ?? '',
      date: it.date,
      modified: it.modified,
      parent: it.parent ?? 0,
      menuOrder: it.menu_order ?? 0,
      template: it.template ?? '',
      source: 'rest',
      localPath: localPathForLink(it.link, domain),
    }))
    // localPathForLink maps by PATHNAME, so a stale-host link still yields a
    // correct local path — the entry looks fine and only the fetch fails. That
    // is why this normalization is easy to omit and expensive to omit: the
    // inventory count stays right while a quarter of the site 404s.
    .map((e) => ({ ...e, link: normalizeSelfHost(e.link, selfHost, domain) }))
    .filter((e) => e.localPath);

  // 1b. SECOND INVENTORY: the sitemap. Anything it advertises that the REST
  // collections did not return gets captured too. This is what keeps the
  // completeness gate honest — without it the run compares the REST total
  // against itself, so a restricted `pages` collection would report a green
  // "captured 1 of 1" for a site with pages missing.
  const sm = await collectSitemapUrls();
  const known = new Set(entries.map((e) => e.localPath));
  let fromSitemap = 0;
  for (const raw of sm.urls) {
    const url = normalizeSelfHost(raw, selfHost, domain);
    const localPath = localPathForLink(url, domain);
    if (!localPath || known.has(localPath)) continue;
    known.add(localPath);
    fromSitemap++;
    entries.push({
      id: null,
      type: 'sitemap',
      slug: '',
      link: url,
      title: '',
      parent: 0,
      menuOrder: 0,
      template: '',
      source: 'sitemap',
      localPath,
    });
  }
  console.error(
    `[capture] sitemap (${sm.source ?? 'none'}): ${sm.urls.length} page URL(s), ${fromSitemap} not in the REST inventory`,
  );

  // The home page is often a page whose `link` is the site root, but on a
  // "latest posts" front page it is not in the pages collection at all — and
  // an index.html is not optional for a static host.
  if (!entries.some((e) => e.localPath === 'index.html')) {
    entries.unshift({
      id: 0,
      type: 'front',
      slug: '',
      link: `${origin}/`,
      title: 'Home',
      localPath: 'index.html',
      parent: 0,
      menuOrder: 0,
      template: '',
      source: 'front',
    });
  }

  mkdirSync(outDir, { recursive: true });
  const assetsDirName = '_ffc-assets';

  // 2. SCRAPE the rendered markup for each inventoried URL.
  const rendered = new Map(); // localPath -> html
  const pageFailures = [];
  // Edge-injected instrumentation removed, and Cloudflare-obfuscated addresses
  // restored, across the whole scrape. Counted so a run reports what it did
  // rather than silently editing the charity's markup.
  // Every tally this function reports on is declared HERE, before the first
  // pass runs, rather than beside the pass that fills it in. That is not tidying:
  // `imageRecode` was declared beside the asset loop and reported on in the
  // page-loop's summary, which is earlier in the same function — a temporal
  // dead zone that `node --check` cannot see, the self-tests never reach
  // (they do not call `capture()`), and which therefore surfaced as a
  // ReferenceError six minutes into a live crawl of a charity's site. One
  // declaration site removes the whole class.
  let cfEmailsDecoded = 0;
  const edgeTagsRemoved = [];
  const usedAssetNames = new Set(); // guards the .png -> .webp rename against collisions
  const imageRecode = {
    recoded: 0,
    declined: 0,
    skippedNoEncoder: 0,
    collisions: [],
    stillOverBudget: 0,
    bytesBefore: 0,
    bytesAfter: 0,
    available: null,
  };
  const cmsScriptsRemoved = [];
  let cmsInlineScriptsRemoved = 0;
  let waypointRulesRemoved = 0;
  let cmsHeadLinksRemoved = 0;
  // Paths that would have escaped the output directory. Expected to stay empty;
  // recorded rather than merely skipped so an empty list is evidence.
  const escapedPaths = [];
  // Pages whose fetch left the site entirely. Recorded separately from an HTTP
  // failure because the response was a perfectly good 200 — of somebody else's
  // page. See isSiteHost.
  const offSiteRedirects = [];
  // Per-failure lines are capped. The first real capture printed 823 of them,
  // which pushed the actual diagnosis off the readable end of the log and made
  // a one-cause failure look like 823 unrelated ones. The tally below reports
  // the whole set; these lines are only for spotting a pattern early.
  const LOG_CAP = 20;
  for (const e of entries) {
    const res = await request(e.link, { accept: 'text/html' });
    // A 200 is not enough: `redirect: 'follow'` will happily deliver another
    // site's page under this URL. Refusing it is deliberate — writing a
    // stranger's markup into a charity's repository is worse than a gap,
    // because a gap is visible and a substitution is not.
    const verdictForPage = classifyPageResponse({
      status: res?.status ?? 0,
      finalUrl: res?.url ?? '',
      requestUrl: e.link,
      domain,
    });
    if (verdictForPage.action !== 'store') {
      if (pageFailures.length < LOG_CAP)
        console.error(`[scrape] ${e.link}: ${verdictForPage.reason} — skipped`);
      if (verdictForPage.offSite)
        offSiteRedirects.push({ url: e.link, finalUrl: verdictForPage.finalUrl });
      pageFailures.push({ url: e.link, status: verdictForPage.status });
      await sleep(delayMs);
      continue;
    }
    let html;
    try {
      html = await res.text();
    } catch {
      pageFailures.push({ url: e.link, status: -1 });
      await sleep(delayMs);
      continue;
    }
    // Strip the CDN's edge-injected instrumentation BEFORE anything else reads
    // this HTML, so the removed tags never reach the asset inventory either.
    const mail = decodeCloudflareEmails(html);
    const edge = stripEdgeInjectedTags(mail.html);
    // De-WordPress before the asset inventory reads this page. The order is the
    // point: a script tag removed here is a script never downloaded, so the
    // 1.9 MB of vendor bundles costs no requests and reaches no artifact.
    // The runtime that replaces them is injected in the write loop instead,
    // where the page's own relative prefix is already known — injecting it here
    // would hand the inventory a reference to a file that exists on no origin,
    // and it would dutifully go and 404 on it.
    const wp = deWordPress(edge.html);
    html = wp.html;
    cfEmailsDecoded += mail.decoded;
    for (const r of edge.removed) edgeTagsRemoved.push(r);
    for (const r of wp.scriptSrcRemoved) cmsScriptsRemoved.push(r);
    cmsInlineScriptsRemoved += wp.inlineScriptsRemoved;
    waypointRulesRemoved += wp.waypointRulesRemoved;
    cmsHeadLinksRemoved += wp.headLinksRemoved.length;
    rendered.set(e.localPath, html);
    e.bytes = html.length;
    await sleep(delayMs);
  }
  const pageTally = tallyFailures(pageFailures);
  console.error(`[capture] rendered ${rendered.size} page(s), ${pageTally.total} failure(s)`);
  if (offSiteRedirects.length) {
    console.error(
      `[capture] ${offSiteRedirects.length} page(s) redirected off ${domain} and were refused.` +
        ' This is what a stale WordPress `home`/`siteurl` does: the CMS sends its own visitors' +
        ' to a domain it does not serve. Fix it at the source with a search-replace on those' +
        ' two options.',
    );
    for (const r of offSiteRedirects.slice(0, LOG_CAP))
      console.error(`        ${r.url} -> ${r.finalUrl}`);
  }
  if (edgeTagsRemoved.length) {
    const kinds = [...new Set(edgeTagsRemoved)].slice(0, 5).join(', ');
    console.error(
      `[capture] removed ${edgeTagsRemoved.length} edge-injected script tag(s) the CDN added and a static host cannot serve: ${kinds}`,
    );
  }
  if (cfEmailsDecoded) {
    console.error(`[capture] decoded ${cfEmailsDecoded} Cloudflare-obfuscated email address(es)`);
  }
  if (cmsScriptsRemoved.length || cmsInlineScriptsRemoved) {
    const distinct = new Set(cmsScriptsRemoved).size;
    console.error(
      `[capture] removed the CMS script payload: ${cmsScriptsRemoved.length} tag(s) across` +
        ` ${distinct} distinct file(s) and ${cmsInlineScriptsRemoved} inline block(s).` +
        ` They are replaced by ${CLONE_RUNTIME_NAME}, which has no dependencies and` +
        ' reaches no network. Structured data (application/ld+json) is kept.',
    );
  }
  if (cmsHeadLinksRemoved) {
    console.error(
      `[capture] removed ${cmsHeadLinksRemoved} head link(s) advertising the CMS backend` +
        ' (xmlrpc, wlwmanifest, oEmbed, REST, feeds, generator) — all PHP routes a static' +
        ' host cannot serve, and all of them still naming the origin being decommissioned.',
    );
  }
  if (pageTally.total) console.error(`[capture] pages: ${describeFailures(pageTally, domain)}`);

  // 3. Localize assets, following CSS one level deep so @font-face and
  //    background images inside a stylesheet come along too. Without that pass
  //    the page's CSS downloads fine and every font it names still 404s.
  const assetsRoot = join(outDir, assetsDirName);
  const downloaded = new Map(); // absolute URL -> local name (or null on failure)
  const assetFailures = [];

  /**
   * Re-encode one raster image to WebP, walking a quality ladder.
   *
   * `sharp` is imported dynamically so this repo stays dependency-free — the
   * same arrangement `sync-runtime-assets.mjs` uses for Playwright. When it is
   * absent the capture says so once and ships the originals, which is a
   * heavier site but never a broken one.
   *
   * The ladder exists so the result lands under the budget the receiving repo
   * enforces rather than merely near it. It stops at the first rung that fits,
   * so most images are encoded once and only a genuinely heavy one pays for
   * three attempts.
   */
  let sharpModule;
  async function encodeWebp(buf, budget) {
    if (imageRecode.available === false) return null;
    if (!sharpModule) {
      try {
        sharpModule = (await import('sharp')).default;
        imageRecode.available = true;
      } catch {
        imageRecode.available = false;
        console.error(
          '[asset] sharp is not installed, so oversized images ship as captured.' +
            ' Install it before the capture step to re-encode them.',
        );
        return null;
      }
    }
    let best = null;
    for (const quality of IMAGE_QUALITY_LADDER) {
      let out;
      try {
        out = await sharpModule(buf).webp({ quality, effort: 6 }).toBuffer();
      } catch {
        // An image sharp cannot decode is not a failure of the capture; the
        // original is already downloaded and gets shipped unchanged.
        return null;
      }
      if (!best || out.length < best.length) best = out;
      if (out.length <= budget) return { buffer: out, quality };
    }
    return best
      ? { buffer: best, quality: IMAGE_QUALITY_LADDER[IMAGE_QUALITY_LADDER.length - 1] }
      : null;
  }

  async function localizeAsset(rawUrl) {
    // Normalized HERE rather than at each call site: assets arrive from three
    // independent paths (the page loop, the CSS-to-CSS recursion, and the
    // media pre-pull), and a fix applied to only some of them looks like it
    // worked — the failure count drops and the remainder reads as a different
    // problem. Doing it at the choke point also keeps assetLocalName's
    // host-namespacing consistent, so one file cannot land under two names.
    const absUrl = normalizeSelfHost(rawUrl, selfHost, domain);
    if (downloaded.has(absUrl)) return downloaded.get(absUrl);
    downloaded.set(absUrl, null); // claim it first so a cycle cannot recurse forever
    const res = await request(absUrl);
    if (!res || res.status !== 200) {
      if (assetFailures.length < LOG_CAP)
        console.error(`[asset] ${absUrl}: HTTP ${res?.status ?? 0}`);
      assetFailures.push({ url: absUrl, status: res?.status ?? 0 });
      return null;
    }
    let buf;
    try {
      buf = Buffer.from(await res.arrayBuffer());
    } catch {
      assetFailures.push({ url: absUrl, status: -1 });
      return null;
    }
    let name = assetLocalName(absUrl);

    // Re-encode oversized raster uploads.
    //
    // Measured on this migration: 194 of 335 images are over the 400 KB budget
    // the charity's own repository enforces, and they account for 186 MB of the
    // 205 MB the clone ships. 165 of them are the same shape — 1366x768, 8-bit
    // RGB(A), about 2 bytes per pixel — which is a photograph, not a diagram.
    // They are event flyers: portraits composited on a gradient, exported from
    // a design tool at browser-window size and saved as PNG. PNG is simply the
    // wrong container for that, and the cost lands on the visitor: a 2.2 MB
    // poster on a phone connection, on a site whose audience is largely on
    // phone connections.
    //
    // Re-encoded rather than merely recompressed: a lossless PNG pass on that
    // same file gives 2243 KB -> 658 KB, which is a real 71% win and still over
    // budget, while WebP q=85 gives 298 KB with no visible difference in the
    // text, the faces or the gradient.
    //
    // The rename is what makes this safe to do here rather than in a later
    // pass: `localizeAsset` returns the local name, and every reference — in
    // markup, in srcset, in CSS — is rewritten from that return value, so a
    // renamed file cannot leave a stale reference behind.
    if (optimizeImages && shouldReencodeImage(absUrl, buf.length, maxImageBytes)) {
      const target = webpName(name);
      // A collision would silently overwrite a real .webp the site already
      // ships, so the original encoding is kept instead.
      if (usedAssetNames.has(target) && target !== name) {
        imageRecode.collisions.push(name);
      } else {
        const encoded = await encodeWebp(buf, maxImageBytes);
        if (encoded && worthReencoding(buf.length, encoded.buffer.length)) {
          imageRecode.recoded += 1;
          imageRecode.bytesBefore += buf.length;
          imageRecode.bytesAfter += encoded.buffer.length;
          if (encoded.buffer.length > maxImageBytes) imageRecode.stillOverBudget += 1;
          buf = encoded.buffer;
          name = target;
        } else if (imageRecode.available === false) {
          // Not the same thing as declining on merit, and reporting it as one
          // is a lie the operator cannot check: "re-encoding would not have
          // been meaningfully smaller" about an image nothing tried to encode.
          imageRecode.skippedNoEncoder += 1;
        } else {
          imageRecode.declined += 1;
        }
      }
    }
    usedAssetNames.add(name);

    if (!isContainedPath(assetsRoot, name)) {
      console.error(`[asset] refusing to write outside the assets dir: ${name}`);
      escapedPaths.push(name);
      assetFailures.push({ url: absUrl, status: -2 });
      return null;
    }
    const dest = join(assetsRoot, name);
    mkdirSync(dirname(dest), { recursive: true });

    const isCss =
      /\.css($|\?)/i.test(absUrl) || /text\/css/i.test(res.headers.get?.('content-type') ?? '');
    if (isCss) {
      let css = buf.toString('utf8');
      const cssReps = new Map();
      for (const ref of collectCssUrls(css)) {
        const abs = normalizeSelfHost(absolutize(ref, res.url || absUrl), selfHost, domain);
        if (!abs || !shouldLocalize(abs, domain, ignoreHosts)) continue;
        // This path needs the same ignore-list filtering as the page loop, and
        // it is easy to miss: stylesheets reference other stylesheets, so a
        // host filtered only in the page loop comes straight back in here.
        // shouldLocalize applies the list, which is why the check is shared.
        const inner = await localizeAsset(abs);
        if (inner) {
          // Both are under assetsRoot, so the reference from one stylesheet to
          // another asset is relative to the stylesheet's own directory.
          const fromDir = dirname(name);
          const rel =
            fromDir === '.' ? inner : `${'../'.repeat(fromDir.split('/').length)}${inner}`;
          cssReps.set(ref, rel);
        }
      }
      css = rewriteRefs(css, cssReps);
      // The hiding rule was measured only in the inline <style> of each page,
      // but a stylesheet is the natural place for it and a single missed copy
      // blanks every page that loads that file. Applying the same transform
      // here costs one pass and removes the possibility.
      const cssWaypoints = neutralizeWaypointHiding(css);
      css = cssWaypoints.css;
      waypointRulesRemoved += cssWaypoints.removed;
      writeFileSync(dest, css, { encoding: 'utf8' });
    } else {
      writeFileSync(dest, buf);
    }
    downloaded.set(absUrl, name);
    await sleep(assetSleepMs(delayMs));
    return name;
  }

  // Media-library files first: they are the charity's own images, and pulling
  // them from the REST inventory catches any that no captured page happens to
  // reference (a gallery behind a builder widget, for instance).
  for (const m of media.items) {
    if (!m?.source_url) continue;
    const src = normalizeSelfHost(m.source_url, selfHost, domain);
    if (shouldLocalize(src, domain, ignoreHosts)) await localizeAsset(src);
  }

  // Paths actually written, and the stranded-navigation tally accumulated as
  // each page is written. Deliberately NOT a map of localPath -> HTML: this
  // site is 589 Divi documents and `rendered` already holds all of them, so
  // retaining the rewritten copies too doubles peak memory for a tally that
  // can be computed one page at a time and thrown away.
  // The runtime every page is about to reference. Written before the pages so
  // a failure to find it stops the capture instead of shipping 589 pages whose
  // only script 404s — a broken menu that no gate downstream can see, because
  // every one of them measures markup and this is a missing file.
  const runtimeSrc = resolvePath(
    dirname(fileURLToPath(import.meta.url)),
    '..',
    'assets',
    CLONE_RUNTIME_NAME,
  );
  if (!existsSync(runtimeSrc)) {
    console.error(
      `[capture] the clone runtime is missing from this checkout: ${runtimeSrc}.` +
        ' Every captured page references it, so refusing to write a clone without it.',
    );
    process.exit(2);
  }
  mkdirSync(assetsRoot, { recursive: true });
  // No `encoding` option: `readFileSync` already returns a Buffer, and
  // `writeFileSync` writes a Buffer verbatim. (`{ encoding: null }` is also
  // valid and does not throw — measured on Node 20 in a live run and on 22
  // here — but it says nothing the Buffer does not already say.)
  writeFileSync(join(assetsRoot, CLONE_RUNTIME_NAME), readFileSync(runtimeSrc));

  const writtenPages = new Set(); // localPath
  const strandedNav = new Map(); // host -> { pages, links }
  for (const [localPath, html] of rendered) {
    const pageUrl = entries.find((e) => e.localPath === localPath)?.link ?? origin;
    const reps = new Map();
    for (const ref of collectAssetUrls(html)) {
      const abs = normalizeSelfHost(absolutize(ref, pageUrl), selfHost, domain);
      if (!abs || !shouldLocalize(abs, domain, ignoreHosts)) continue;
      // Fetch from the canonical host; the replacement still keys on `ref`,
      // the string that actually appears in this page's markup.
      const name = await localizeAsset(abs);
      if (name) reps.set(ref, `${relativePrefix(localPath)}${assetsDirName}/${name}`);
    }
    // Same-site page links must point at the captured copies, or every nav
    // click leaves the static site for the host being decommissioned — which
    // still resolves today and silently stops after the DNS cutover.
    //
    // Only links this page actually contains. Adding a replacement for every
    // entry made the rewrite quadratic in the size of the site: rewriteRefs
    // does a full-document split/join per pair, so a 590-entry inventory meant
    // ~1,180 passes over every one of 590 documents.
    const linkIndex = normalizedLinkIndex(html, pageUrl, selfHost, domain);
    for (const e of entries) {
      // A page's link to ITSELF is not exempt. Skipping it left the one link
      // every page carries — the header logo, which points at the site root —
      // absolute on the front page, so clicking the logo there left the static
      // site for the host being decommissioned. It showed only on the front
      // page, because everywhere else the root is a different entry and was
      // rewritten normally, which is exactly why it survived the stranded-nav
      // fix: 588 pages looked right. `relativePrefix` resolves the self case to
      // `./` or `../<slug>/`, both of which stay inside the clone.
      const raws = linkIndex.get(linkKey(e.link));
      if (!raws) continue;
      const target = `${relativePrefix(localPath)}${e.localPath.replace(/index\.html$/, '')}`;
      for (const raw of raws) reps.set(raw, target || './');
    }
    const out = injectCloneRuntime(
      rewriteRefs(html, reps),
      `${relativePrefix(localPath)}${assetsDirName}/${CLONE_RUNTIME_NAME}`,
    );
    if (!isContainedPath(outDir, localPath)) {
      console.error(`[capture] refusing to write outside the output dir: ${localPath}`);
      escapedPaths.push(localPath);
      continue;
    }
    const dest = join(outDir, localPath);
    mkdirSync(dirname(dest), { recursive: true });
    writeFileSync(dest, out, { encoding: 'utf8' });
    writtenPages.add(localPath);
    for (const [host, n] of remainingSelfHostLinks(out, domain, selfHost)) {
      const cur = strandedNav.get(host) ?? { pages: 0, links: 0 };
      cur.pages += 1;
      cur.links += n;
      strandedNav.set(host, cur);
    }
  }

  // 4. Gates.
  let externalHosts = new Set();
  // Navigation that never came home — tallied above, as each page was written,
  // so it measures the artifact the charity would receive rather than what was
  // fetched.
  if (strandedNav.size) {
    console.error(
      '[capture] navigation still points off the clone — every one of these links leaves the' +
        ' static site for a host that is being decommissioned:',
    );
    for (const [host, s] of strandedNav)
      console.error(`        ${host}: ${s.links} link(s) across ${s.pages} page(s)`);
  }
  for (const localPath of rendered.keys()) {
    const dest = join(outDir, localPath);
    if (!existsSync(dest)) continue;
    const html = (await import('node:fs')).readFileSync(dest, 'utf8');
    for (const h of remainingExternalAssetHosts(html, domain, ignoreHosts)) externalHosts.add(h);
  }

  // Forms have no backend on a static host. Collected across every captured
  // page so the migration PR can say exactly which pages need a mailto (or an
  // external provider) before this goes anywhere near the apex.
  const formPages = [];
  const contactEmails = new Set();
  for (const [localPath, html] of rendered) {
    const d = detectForms(html);
    for (const e of d.emails) contactEmails.add(e);
    if (d.forms > 0 || d.hasFormPlugin) {
      formPages.push({ localPath, forms: d.forms, plugin: d.hasFormPlugin });
    }
  }
  if (formPages.length) {
    console.error(
      `[capture] ${formPages.length} page(s) carry a form with no backend after migration; ` +
        `contact addresses found on the site: ${[...contactEmails].join(', ') || 'none'}`,
    );
  }

  const captureSummary = summarizeCaptured(entries, new Set(rendered.keys()));

  // Measured against the MERGED inventory, not the REST total. Comparing the
  // REST total against pages fetched from the REST list is a tautology; the
  // union with the sitemap is the only figure that can be short.
  // Reported HERE, after the asset pass that fills these in — not in the
  // page-loop summary above, where every number would read zero. The first
  // draft put it there and crashed on the temporal dead zone instead, which
  // was the loud version of the same mistake; a report that merely printed
  // `re-encoded 0 image(s)` would have been the quiet one.
  // Down here with the image report, and for the same reason: the CSS pass
  // strips this rule from stylesheets too, so the count is still climbing
  // while the page loop's summary prints. The fixture run caught it saying
  // 3 where the report said 6 — harmless in itself, and the identical
  // mistake that made the image tally read before it existed.
  console.error(
    `[capture] removed ${waypointRulesRemoved} rule(s) hiding animated content behind` +
      ' JavaScript, so the export reads with scripting disabled.',
  );
  if (!waypointRulesRemoved && cmsScriptsRemoved.length) {
    // Not fatal — a site with no scroll animations has no such rule — but it is
    // worth saying out loud, because the failure it guards against (589 blank
    // pages that pass every markup check) is invisible in any other output.
    console.error(
      '[capture] note: no JavaScript-dependent hiding rule was found. If this theme animates' +
        ' content in, check a written page renders with scripting off before shipping.',
    );
  }
  if (imageRecode.recoded) {
    const mb = (n) => (n / 1048576).toFixed(1);
    console.error(
      `[capture] re-encoded ${imageRecode.recoded} oversized image(s) to WebP:` +
        ` ${mb(imageRecode.bytesBefore)} MB -> ${mb(imageRecode.bytesAfter)} MB` +
        ` (${(100 - (imageRecode.bytesAfter / imageRecode.bytesBefore) * 100).toFixed(1)}% smaller).` +
        ' Every reference was rewritten with the rename.',
    );
    if (imageRecode.stillOverBudget)
      console.error(
        `[capture] ${imageRecode.stillOverBudget} of them are still over the` +
          ` ${Math.round(maxImageBytes / 1024)} KB budget at the lowest quality tried.`,
      );
  }
  if (imageRecode.declined)
    console.error(
      `[capture] ${imageRecode.declined} oversized image(s) shipped as captured —` +
        ' re-encoding them would not have been meaningfully smaller.',
    );
  if (imageRecode.skippedNoEncoder)
    console.error(
      `[capture] ${imageRecode.skippedNoEncoder} oversized image(s) shipped as captured because` +
        ' no encoder was available. This is NOT a judgement that they were already optimal:' +
        ' nothing tried. Install sharp before the capture step.',
    );
  if (imageRecode.collisions.length)
    console.error(
      `[capture] ${imageRecode.collisions.length} image(s) kept their original encoding because` +
        ` the .webp name was already taken: ${imageRecode.collisions.slice(0, 5).join(', ')}`,
    );
  const assetTally = tallyFailures(assetFailures);
  const assetFailureNote = describeFailures(assetTally, domain);
  if (assetFailureNote) console.error(`[capture] assets: ${assetFailureNote}`);

  // `index.html` is what a static host serves at `/`. Read from what was
  // actually WRITTEN, not from what was fetched: a page can be rendered and
  // then refused at the write (path containment), and the comment here used to
  // claim the stronger property while the code checked the weaker one.
  const frontPageCaptured = writtenPages.has('index.html');
  const staleNav = selfHost ? (strandedNav.get(selfHost) ?? { pages: 0, links: 0 }) : null;
  const verdict = captureVerdict({
    expected: entries.length,
    captured: rendered.size,
    externalHosts: [...externalHosts],
    failedAssets: assetTally.total,
    assetFailureNote,
    frontPageCaptured,
    strandedStaleLinks: staleNav?.links ?? 0,
    strandedStalePages: staleNav?.pages ?? 0,
    staleHost: selfHost,
  });

  const report = {
    domain,
    capturedAt: new Date().toISOString(),
    restRoot: rest.indexUrl,
    restFlavor: rest.kind,
    inventory: {
      restPages: pages.total,
      restPosts: posts.total,
      sitemapSource: sm.source,
      sitemapPageUrls: sm.urls.length,
      addedBySitemap: fromSitemap,
      merged: entries.length,
    },
    captured: captureSummary,
    frontPageCaptured,
    offSiteRedirects,
    strandedNav: Object.fromEntries(strandedNav),
    pages: { captured: captureSummary.byType.page ?? 0, reported: pages.total },
    posts: { captured: captureSummary.byType.post ?? 0, reported: posts.total },
    media: {
      reported: media.total,
      collectionStatus: media.lastStatus,
      available: media.lastStatus === 200,
    },
    assets: {
      downloaded: [...downloaded.values()].filter(Boolean).length,
      failed: assetTally.total,
      failures: assetTally,
    },
    pageFetch: { failed: pageTally.total, failures: pageTally },
    // Recorded even when the correction worked. A capture that silently fixes
    // the site's own broken self-reference hides a real defect in the LIVE
    // site: every one of these URLs is 404ing for actual visitors right now,
    // and whoever owns that WordPress should be told rather than have it
    // quietly papered over by the migration.
    declaredSelfHost: selfHost,
    // What replaced the CMS's own client payload. Recorded so a reviewer can
    // see the size of the change without diffing 589 pages, and so a later run
    // that quietly stops stripping is visible as a number going to zero.
    deWordPressed: {
      runtime: CLONE_RUNTIME_NAME,
      scriptTagsRemoved: cmsScriptsRemoved.length,
      distinctScriptsRemoved: new Set(cmsScriptsRemoved).size,
      inlineScriptsRemoved: cmsInlineScriptsRemoved,
      waypointHidingRulesRemoved: waypointRulesRemoved,
      headLinksRemoved: cmsHeadLinksRemoved,
    },
    imageOptimization: {
      enabled: optimizeImages,
      encoderAvailable: imageRecode.available,
      maxImageKb: Math.round(maxImageBytes / 1024),
      recoded: imageRecode.recoded,
      declined: imageRecode.declined,
      skippedNoEncoder: imageRecode.skippedNoEncoder,
      stillOverBudget: imageRecode.stillOverBudget,
      collisions: imageRecode.collisions.length,
      bytesBefore: imageRecode.bytesBefore,
      bytesAfter: imageRecode.bytesAfter,
    },
    remainingExternalHosts: [...externalHosts],
    escapedPaths,
    ignoreHosts,
    forms: {
      pageCount: formPages.length,
      pages: formPages.slice(0, 15),
      truncated: Math.max(0, formPages.length - 15),
      contactEmailsFound: [...contactEmails],
    },
    entries: entries.map(
      ({ id, type, slug, link, title, localPath, parent, menuOrder, source, bytes }) => ({
        id,
        type,
        slug,
        link,
        title,
        localPath,
        parent,
        menuOrder,
        source,
        bytes: bytes ?? 0,
      }),
    ),
    verdict,
  };
  writeFileSync(join(outDir, 'wp-capture-report.json'), JSON.stringify(report, null, 2), {
    encoding: 'utf8',
  });
  console.error(
    JSON.stringify({ ...report, entries: `${report.entries.length} entries` }, null, 2),
  );

  if (process.env.GITHUB_STEP_SUMMARY) {
    const s = [
      `## WordPress capture — ${domain}`,
      '',
      `**${verdict.ok ? '✅ capture gates passed' : '⚠️ capture gates failed'}**`,
      ...(verdict.ok ? [] : ['', ...verdict.problems.map((p) => `- ${p}`)]),
      '',
      `- Captured ${rendered.size} of ${entries.length} merged inventory entries ` +
        `(${captureSummary.byType.page ?? 0} pages, ${captureSummary.byType.post ?? 0} posts, ` +
        `${captureSummary.byType.sitemap ?? 0} sitemap-only)`,
      `- Inventory: REST ${pages.total} page(s) + ${posts.total} post(s); sitemap ${sm.urls.length} URL(s) (${fromSitemap} not in REST)`,
      `- Media collection: ${report.media.available ? `${media.total} item(s)` : `unavailable (HTTP ${media.lastStatus}) — images taken from the captured pages instead`}`,
      `- Assets localized: ${report.assets.downloaded}` +
        (assetFailureNote ? ` — ${assetFailureNote}` : ' (0 failed)'),
      ...(pageTally.total ? [`- Page fetches: ${describeFailures(pageTally, domain)}`] : []),
      `- Remaining external asset hosts: ${externalHosts.size ? [...externalHosts].join(', ') : 'none'}`,
      ...(selfHost
        ? [
            `- ⚠️ The live site declares its home as **${selfHost}**, not ${domain}. Its own page` +
              ` links, sitemap entries and /wp-content/uploads/ references point at a host that` +
              ` 404s — rewritten onto ${domain} for this capture. The LIVE site still has this bug.`,
          ]
        : []),
      ...(ignoreHosts.length
        ? [`- Ignored hosts (references dropped): ${ignoreHosts.join(', ')}`]
        : []),
      ...(formPages.length
        ? [
            `- ⚠️ ${formPages.length} page(s) carry a form that will have no backend once static` +
              ` — contact addresses found: ${[...contactEmails].join(', ') || 'none'}`,
          ]
        : []),
    ].join('\n');
    writeFileSync(process.env.GITHUB_STEP_SUMMARY, s + '\n', { flag: 'a', encoding: 'utf8' });
  }

  process.exit(verdict.ok ? 0 : 1);
}

if (isMain) await (inspectOnly ? inspect() : capture());
