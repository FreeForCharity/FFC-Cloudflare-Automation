/**
 * clone-to-routes-lib.mjs — turn a captured static clone into real Next.js routes.
 *
 * Dropping the capture into `public/` deploys, because `output: 'export'` copies
 * that directory verbatim. It also leaves the repo in a shape no FFC-EX site
 * actually uses: no app routes, so the template's sitemap, its per-page
 * canonicals, its `<main>` landmark, its skip-to-content link and its
 * one-h1-per-page build check all have nothing to act on, and the charity's
 * pages inherit none of the FFC chrome.
 *
 * The production shape — measured on FFC-EX-catnipandcattitude.org, which is
 * itself a WordPress clone that was converted rather than hand-written — is:
 *
 *   src/clone-content/<slug>.html   the page's markup, with %%BASE%% asset tokens
 *   src/app/<slug>/page.tsx         a route carrying title, description, canonical
 *   src/app/layout.tsx              FFC chrome: <main>, skip link, footer, consent
 *   public/                         assets only
 *
 * This module is the pure half of that conversion: every function here is a
 * string transform with no filesystem or network, so the decisions can be
 * tested rather than inspected after the fact.
 */

import { resolve as resolvePath } from 'node:path';
import { fileURLToPath } from 'node:url';

/** Route slug for a captured local path. `index.html` is the site root. */
export function slugForLocalPath(localPath) {
  if (typeof localPath !== 'string' || !localPath) return null;
  const trimmed = localPath.replace(/\\/g, '/').replace(/^\.?\//, '');
  if (!trimmed.endsWith('/index.html')) return trimmed === 'index.html' ? '' : null;
  return trimmed.slice(0, -'/index.html'.length);
}

/**
 * A route slug is a directory name in `src/app`, so it has to survive a
 * filesystem and a URL both. WordPress permalinks routinely carry characters
 * that do neither: this migration's own capture contains
 * `…-russia-ukraine-and-the-us￼/`, an OBJECT REPLACEMENT CHARACTER that a
 * human never typed and that the artifact upload flagged as a case-insensitive
 * collision. Kept deliberately narrow — lowercase, strip anything that is not
 * an unreserved URL character, collapse separators — because a slug is also the
 * page's public address and rewriting it more than necessary breaks inbound
 * links and search rankings.
 */
export function sanitizeSlugSegment(segment) {
  if (typeof segment !== 'string') return '';
  return (
    segment
      .normalize('NFKD')
      .replace(/[\u0300-\u036f]/g, '')
      .toLowerCase()
      // Underscore becomes a hyphen rather than surviving: FFC's drift guard
      // requires kebab-case route folders (`/^[a-z0-9]+(?:-[a-z0-9]+)*$/`) on
      // Google Search Central's advice that hyphens are read as word separators
      // and underscores are not. Nine of this capture's permalinks carry one,
      // each of them WordPress's rendering of a colon or a period in a title
      // rather than anything the charity chose.
      .replace(/[^a-z0-9-]+/g, '-')
      .replace(/-{2,}/g, '-')
      .replace(/^-|-$/g, '')
  );
}

/**
 * Uppercase the hex digits of every percent-escape in a path.
 *
 * RFC 3986 §6.2.2.1 makes those digits case-insensitive, so `%EF%BF%BC` and
 * `%ef%bf%bc` are the SAME URL. This capture's sitemap advertises two pages
 * that differ in nothing else, and treating them as distinct publishes the
 * charity's article at two addresses with identical text — the duplicate-
 * content defect, arrived at by mirroring a defect in the source's sitemap
 * rather than by any decision anyone made.
 *
 * Only the escape's digits are touched; the rest of the path keeps its case,
 * because a path really is case-sensitive everywhere else.
 */
export function normalizePercentEncoding(path) {
  if (typeof path !== 'string') return '';
  return path.replace(/%([0-9a-fA-F]{2})/g, (whole, hex) => `%${hex.toUpperCase()}`);
}

export function sanitizeSlug(slug) {
  if (!slug) return '';
  return slug
    .split('/')
    .map((s) => sanitizeSlugSegment(s))
    .filter(Boolean)
    .join('/');
}

/** The inner HTML of `<body>`, which is what a route renders. */
export function extractBody(html) {
  const m = /<body\b([^>]*)>([\s\S]*)<\/body>/i.exec(html);
  if (!m) return { body: html, bodyClass: '' };
  const cls = /\bclass\s*=\s*["']([^"']*)["']/i.exec(m[1])?.[1] ?? '';
  return { body: m[2], bodyClass: cls };
}

/** `<title>` with the site-name suffix left intact; Next.js owns the template. */
export function extractTitle(html) {
  const raw = /<title[^>]*>([\s\S]*?)<\/title>/i.exec(html)?.[1] ?? '';
  return decodeEntities(raw).replace(/\s+/g, ' ').trim();
}

export function extractMetaDescription(html) {
  const m =
    /<meta[^>]*\bname\s*=\s*["']description["'][^>]*\bcontent\s*=\s*["']([^"']*)["']/i.exec(html) ??
    /<meta[^>]*\bcontent\s*=\s*["']([^"']*)["'][^>]*\bname\s*=\s*["']description["']/i.exec(html);
  const v = decodeEntities(m?.[1] ?? '')
    .replace(/\s+/g, ' ')
    .trim();
  return v || null;
}

const ENTITIES = {
  amp: '&',
  lt: '<',
  gt: '>',
  quot: '"',
  apos: "'",
  nbsp: ' ',
  '#039': "'",
  '#8217': '’',
  '#8216': '‘',
  '#8220': '“',
  '#8221': '”',
  '#8211': '–',
  '#8212': '—',
  hellip: '…',
  rsquo: '’',
  lsquo: '‘',
  ldquo: '“',
  rdquo: '”',
  ndash: '–',
  mdash: '—',
  '#8230': '…',
};
export function decodeEntities(s) {
  if (typeof s !== 'string') return '';
  return s.replace(/&(#x?[0-9a-f]+|[a-z]+);/gi, (whole, name) => {
    const key = name.toLowerCase();
    if (Object.prototype.hasOwnProperty.call(ENTITIES, key)) return ENTITIES[key];
    if (/^#x/i.test(name)) {
      const cp = parseInt(name.slice(2), 16);
      return Number.isFinite(cp) ? String.fromCodePoint(cp) : whole;
    }
    if (/^#/.test(name)) {
      const cp = parseInt(name.slice(1), 10);
      return Number.isFinite(cp) ? String.fromCodePoint(cp) : whole;
    }
    return whole;
  });
}

/**
 * A description derived from the page's own first substantive paragraph.
 *
 * 343 of this migration's 589 pages ship without one, which Lighthouse SEO
 * reports directly and which costs the charity a search snippet on more than
 * half its site. Derived, never invented: the text is the page's own, and a
 * page with nothing substantive to quote gets no description rather than a
 * fabricated one.
 */
export function deriveDescription(bodyHtml, { min = 60, max = 155 } = {}) {
  if (typeof bodyHtml !== 'string') return null;
  // Chrome first: the header and footer templates repeat on every page, so a
  // description taken from them would be identical site-wide — which is the
  // duplicate-description defect, not a fix for the missing-description one.
  const withoutChrome = bodyHtml
    .replace(/<div[^>]*_tb_header[\s\S]*?(?=<div[^>]*id="main-content")/i, '')
    .replace(/<div[^>]*_tb_footer[\s\S]*$/i, '');
  const source = withoutChrome.length > 200 ? withoutChrome : bodyHtml;
  const noTags = source
    .replace(/<(script|style|noscript)\b[\s\S]*?<\/\1>/gi, ' ')
    // Visually-hidden labels are written for a screen reader mid-interaction,
    // not as prose: on this capture two pages otherwise get "Share on YouTube
    // Share on Facebook Share on Email…" as their entire search snippet, taken
    // from a share widget's `.hustle-screen-reader` spans. They are the page's
    // own words and still the wrong ones.
    .replace(
      /<(span|a|div)\b[^>]*class\s*=\s*["'][^"']*(?:screen-reader|sr-only|visually-hidden)[^"']*["'][^>]*>[\s\S]*?<\/\1>/gi,
      ' ',
    )
    .replace(/<nav\b[\s\S]*?<\/nav>/gi, ' ')
    .replace(/<[^>]+>/g, ' ');
  const text = decodeEntities(noTags).replace(/\s+/g, ' ').trim();
  if (text.length < min) return null;
  if (text.length <= max) return text;
  const cut = text.slice(0, max + 1);
  const at = cut.lastIndexOf(' ');
  return (at > min ? cut.slice(0, at) : text.slice(0, max)).replace(/[\s,;:.\-]+$/, '') + '…';
}

/* ------------------------------------------------------------------ *
 * Heading normalization
 * ------------------------------------------------------------------ */

/**
 * Every heading in document order, with the enclosing Divi module class.
 *
 * The module class is what makes the CSS half of this safe. Divi styles
 * headings per module — `.et_pb_text_0_tb_footer h1{font-size:28px;
 * color:#FFFFFF!important}` — so retagging an element without retargeting that
 * selector silently changes how the page looks. Measured on this capture:
 * 73 of 1418 stylesheets carry such a rule.
 */
export function collectHeadings(html) {
  const out = [];
  const re = /<h([1-6])\b([^>]*)>/gi;
  let m;
  while ((m = re.exec(html)) !== null) {
    const before = html.slice(0, m.index);
    // Nearest preceding module class wins: Divi emits it on the wrapper a few
    // elements above the heading, and the LAST one before this position is the
    // innermost still-open module.
    const mods = before.match(/et_pb_[a-z_]*?_\d+(?:_tb_(?:header|footer|body))?/g);
    out.push({
      index: m.index,
      length: m[0].length,
      level: Number(m[1]),
      attrs: m[2],
      module: mods ? mods[mods.length - 1] : null,
      inChrome: /_tb_(header|footer)$/.test(mods ? mods[mods.length - 1] : ''),
    });
  }
  return out;
}

/**
 * Assign each heading its corrected level.
 *
 * Two properties, and they are separate requirements that happen to share a
 * pass. **Exactly one h1**, which `scripts/verify-build.mjs` enforces on every
 * FFC-EX site and which 243 of 589 pages here break. **No level skipped**,
 * which Lighthouse's `heading-order` audit reports and which every page here
 * breaks — the measured shapes are `h1 -> h3` (141 of 200 sampled), `h2 -> h4`
 * (47) and `h1 -> h4` (12), because Divi's module headers default to h4
 * regardless of what precedes them.
 *
 * Relative nesting is preserved rather than flattened: a stack of the ORIGINAL
 * levels decides depth, so a document that went 1,3,4,4,3 comes out 1,2,3,3,2 —
 * the same outline, minus the gaps. Flattening every subheading to h2 would
 * also pass both checks and would destroy the page's structure for a screen
 * reader, which is the thing the checks exist to protect.
 *
 * The primary h1 is the first heading outside the header/footer templates,
 * because the chrome repeats on all 589 pages and its heading is never what the
 * page is about. A page whose content has no heading at all keeps its chrome
 * heading as the h1 rather than being left with none.
 */
export function planHeadingLevels(headings) {
  if (!headings.length) return [];
  let primary = headings.findIndex((h) => !h.inChrome);
  if (primary === -1) primary = 0;

  const stack = [];
  return headings.map((h, i) => {
    if (i === primary) {
      stack.length = 0;
      stack.push(1);
      return { ...h, newLevel: 1 };
    }
    // Anything else that was an h1 must nest under the primary, or the page
    // ends with two h1s again. The floor of 2 also covers headings that come
    // BEFORE the primary — the header template's own heading outranks nothing,
    // and an empty stack would otherwise hand it level 1. That case is not
    // hypothetical: it is the shape of every page in this capture, and the
    // first draft of this function shipped two h1s because of it.
    const effective = h.level === 1 ? 2 : h.level;
    while (stack.length && stack[stack.length - 1] >= effective) stack.pop();
    const newLevel = Math.max(2, Math.min(stack.length + 1, 6));
    stack.push(effective);
    return { ...h, newLevel };
  });
}

/** Apply the plan to the markup, right-to-left so earlier indices stay valid. */
export function applyHeadingLevels(html, planned) {
  let out = html;
  const changes = [];
  for (const h of [...planned].sort((a, b) => b.index - a.index)) {
    if (h.newLevel === h.level) continue;
    const close = findMatchingClose(out, h.index, h.level);
    if (close === -1) continue; // unbalanced markup: leave it rather than corrupt it
    out = out.slice(0, close) + `</h${h.newLevel}>` + out.slice(close + `</h${h.level}>`.length);
    out =
      out.slice(0, h.index) +
      `<h${h.newLevel}${withLevelClass(h.attrs, h.level)}>` +
      out.slice(h.index + h.length);
    changes.push({ module: h.module, from: h.level, to: h.newLevel });
  }
  return { html: out, changes };
}

/** The marker class that remembers what level a heading used to be. */
export function headingLevelClass(level) {
  return `ffc-h${level}`;
}

/**
 * Add the original-level marker to a heading's attribute string.
 *
 * A heading with no class attribute gets one; a heading that has one keeps
 * everything already in it. The marker is appended rather than prepended so a
 * reader diffing the fragment sees the page's own classes first.
 */
export function withLevelClass(attrs, level) {
  const marker = headingLevelClass(level);
  const m = /\bclass\s*=\s*(["'])([\s\S]*?)\1/i.exec(attrs);
  if (!m) return `${attrs} class="${marker}"`;
  if (m[2].split(/\s+/).includes(marker)) return attrs;
  const replaced = `class=${m[1]}${m[2] ? `${m[2]} ` : ''}${marker}${m[1]}`;
  return attrs.slice(0, m.index) + replaced + attrs.slice(m.index + m[0].length);
}

function findMatchingClose(html, openIndex, level) {
  const close = `</h${level}>`;
  const at = html.indexOf(close, openIndex);
  return at === -1 ? -1 : at;
}

/**
 * Let every CSS rule that styled a heading keep styling it after the retag.
 *
 * Retagging an `h1` to an `h2` silently restyles it, and the failure is not the
 * obvious one. Divi styles headings per module — `.et_pb_text_0_tb_footer h1
 * {font-size:28px;color:#FFF!important}` — but it ALSO carries bare `h1{…}`
 * global rules, and the theme sets a default heading colour of `#333333`. So a
 * white h1 on the site's blue `#1c75b9` band becomes a `#333333` h2 at a
 * contrast ratio of 2.58:1, which is both an accessibility failure and visibly
 * wrong. Measured on this capture's front page before this function existed.
 *
 * An earlier version of this rewrote the module rule's `h1` to `h2`. That fixes
 * the module half and cannot fix the global half — a bare `h1{…}` is not scoped
 * to the module, so there is nothing to retarget it to that would not also hit
 * every other h1 on the page.
 *
 * So instead of moving the rules, the ELEMENT carries a marker of what it used
 * to be (`applyHeadingLevels` adds `ffc-h1`) and every selector naming a
 * heading element gains a twin naming the marker class:
 *
 *   h1{color:#fff}                 ->  h1,.ffc-h1{color:#fff}
 *   .et_pb_text_0 h1{font-size:28px} -> .et_pb_text_0 h1,.et_pb_text_0 .ffc-h1{…}
 *
 * The twin is strictly more specific than the same selector with the element
 * (a class beats an element), so a retagged heading keeps exactly the
 * presentation it had — module rules and global rules alike — while the
 * document outline is the corrected one. Nothing that did not move is touched,
 * which is the property the retargeting version could not offer.
 */
export function mirrorHeadingSelectors(css) {
  if (typeof css !== 'string') return { css: '', mirrored: 0 };
  let mirrored = 0;
  // Selector lists only: the prelude of a rule, i.e. everything from the end of
  // the previous block to the `{` that opens this one. Matching on the whole
  // stylesheet would rewrite `h1` inside a declaration value or a string.
  // `{` is in the lead set because Divi's stylesheets are mostly media queries,
  // and the rules inside one are preceded by the query's own opening brace. A
  // declaration block cannot be mistaken for a prelude: it always contains a
  // `;` or the closing `}`, both of which the prelude class excludes.
  const out = css.replace(/(^|[{};])([^{}();@]*?)\{/g, (whole, lead, prelude) => {
    if (!/\bh[1-6]\b/i.test(prelude)) return whole;
    const parts = prelude.split(',');
    const extra = [];
    for (const part of parts) {
      if (!/(^|[\s>+~])h[1-6]\b/i.test(part)) continue;
      const twin = part.replace(/(^|[\s>+~])h([1-6])\b/gi, (m, before, n) => `${before}.ffc-h${n}`);
      if (twin !== part) extra.push(twin);
    }
    if (!extra.length) return whole;
    mirrored += extra.length;
    return `${lead}${prelude},${extra.join(',')}{`;
  });
  return { css: out, mirrored };
}

function escapeRe(s) {
  return String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/* ------------------------------------------------------------------ *
 * Accessibility repairs that add no words of their own
 * ------------------------------------------------------------------ */

/**
 * Give every `<img>` an `alt` attribute.
 *
 * A missing `alt` and `alt=""` are different things to a screen reader: the
 * first makes the reader announce the filename, the second marks the image
 * decorative and skips it. 102 of this capture's 2558 images have no attribute
 * at all. Adding `alt=""` is the honest repair — it says "this image carries no
 * information the surrounding text does not", which is true of the spacers and
 * background flourishes that make up this set, and it invents nothing. Writing
 * descriptive alt text for a charity's photographs is their call, not this
 * script's.
 */
export function ensureImageAlt(html) {
  let added = 0;
  const out = html.replace(/<img\b([^>]*?)(\/?)>/gi, (whole, attrs, selfClose) => {
    if (/\balt\s*=/i.test(attrs)) return whole;
    added += 1;
    return `<img${attrs} alt=""${selfClose ? '/' : ''}>`;
  });
  return { html: out, added };
}

/**
 * Give a link that wraps only a decorative image an accessible name.
 *
 * 71 links here contain nothing but an `alt=""` image, so they announce as
 * "link" with no destination — Lighthouse's `link-name`, and one of the two
 * audits keeping this site's accessibility score below its threshold. The name
 * is derived from where the link goes, never from guessing what the image
 * shows: a link to the site root is the site name, and anything else falls back
 * to its own path. A link that already has text, a title or an aria-label is
 * left completely alone.
 */
export function nameAnonymousLinks(html, siteName) {
  let named = 0;
  const out = html.replace(/<a\b([^>]*)>([\s\S]*?)<\/a>/gi, (whole, attrs, inner) => {
    if (/\baria-label\s*=/i.test(attrs) || /\btitle\s*=/i.test(attrs)) return whole;
    if (decodeEntities(inner.replace(/<[^>]+>/g, '')).trim()) return whole;
    const alts = [...inner.matchAll(/<img[^>]*\balt\s*=\s*["']([^"']*)["']/gi)].map((m) => m[1]);
    if (!alts.length || alts.some((a) => a.trim())) return whole;
    const href = /\bhref\s*=\s*["']([^"']*)["']/i.exec(attrs)?.[1] ?? '';
    const label = labelForHref(href, siteName);
    if (!label) return whole;
    named += 1;
    return `<a${attrs} aria-label="${escapeAttr(label)}">${inner}</a>`;
  });
  return { html: out, named };
}

/**
 * Phrases that describe the action rather than the destination.
 *
 * Kept to the ones a page builder actually emits. A longer list would start
 * catching real link text — "More ministries" says where it goes.
 */
const GENERIC_LINK_TEXT = new Set([
  'read more',
  'read more...',
  'read more »',
  'learn more',
  'click here',
  'continue reading',
  'more',
  'more...',
  'here',
  'details',
  'view',
  'view more',
]);

/**
 * Give a "Read More" link a name that says where it goes.
 *
 * Lighthouse's `link-text` audit and WCAG 2.4.4 both want a link's name to
 * identify its destination. Divi's blog module emits "Read More" for every
 * post, so the front page offers a screen-reader user or a crawler three
 * identical links to three different articles.
 *
 * The destination is appended as a visually-hidden span rather than as an
 * `aria-label`, and that choice is load-bearing in two directions. An
 * aria-label is invisible to `link-text`, which reads the rendered text and
 * would go on reporting the page as failing — measured here, with the labels in
 * place. And a label REPLACES the accessible name, so a voice-control user
 * saying "click read more" stops matching the link. A hidden span is part of
 * the rendered text for both, while the page keeps looking the way the charity
 * designed it.
 *
 * The destination is read from the link's own href, never invented.
 */
export function nameGenericLinks(html) {
  let named = 0;
  const out = html.replace(/<a\b([^>]*)>([\s\S]*?)<\/a>/gi, (whole, attrs, inner) => {
    if (/\baria-label\s*=/i.test(attrs) || /\baria-labelledby\s*=/i.test(attrs)) return whole;
    if (/ffc-sr-only/.test(inner)) return whole; // already named
    const text = decodeEntities(inner.replace(/<[^>]+>/g, ' '))
      .replace(/\s+/g, ' ')
      .trim();
    if (!text) return whole; // nameAnonymousLinks owns the empty case
    if (!GENERIC_LINK_TEXT.has(text.toLowerCase().replace(/[\s.…»>]+$/, ''))) return whole;
    const href = /\bhref\s*=\s*["']([^"']*)["']/i.exec(attrs)?.[1] ?? '';
    const destination = labelForHref(href, '');
    if (!destination || destination === 'Home') return whole;
    named += 1;
    return `<a${attrs}>${inner}<span class="ffc-sr-only"> about ${escapeHtml(destination)}</span></a>`;
  });
  return { html: out, named };
}

/** Escape text for insertion between tags. */
export function escapeHtml(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

export function labelForHref(href, siteName) {
  if (typeof href !== 'string' || !href) return null;
  if (/^(#|mailto:|tel:|javascript:)/i.test(href)) return null;
  const path = href.replace(/^https?:\/\/[^/]+/i, '');
  if (path === '' || path === '/' || /^\.{1,2}\/?$/.test(path) || /^\/?index\.html$/.test(path)) {
    return siteName ? `${siteName} — home` : 'Home';
  }
  const seg = path.split(/[?#]/)[0].replace(/\/+$/, '').split('/').filter(Boolean).pop();
  if (!seg) return siteName ? `${siteName} — home` : 'Home';
  const words = decodeURIComponent(seg)
    .replace(/\.[a-z0-9]+$/i, '')
    .replace(/[-_]+/g, ' ')
    .trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : null;
}

/**
 * Give every embedded frame a title.
 *
 * `<iframe>` is a document inside a document, and a screen reader announces it
 * by its `title`. Without one it announces "iframe" — WCAG 4.1.2 and
 * Lighthouse's `frame-title`. This capture ships ten embeds (five podcast
 * episodes, four videos, one scripture reading) and six have no title.
 *
 * The title is read off the embed's own URL — the provider plus the slug it
 * already carries — so it names the actual episode rather than restating the
 * medium. Nothing is invented: an embed whose URL yields no slug is left alone
 * rather than titled "Embedded content", which tells a listener nothing they
 * did not already know.
 */
export function titleIframes(html) {
  let titled = 0;
  const out = html.replace(/<iframe\b([^>]*?)(\/?)>/gi, (whole, attrs, selfClose) => {
    if (/\btitle\s*=/i.test(attrs)) return whole;
    const src = /\bsrc\s*=\s*["']([^"']*)["']/i.exec(attrs)?.[1] ?? '';
    const title = titleForEmbed(src);
    if (!title) return whole;
    titled += 1;
    return `<iframe${attrs} title="${escapeAttr(title)}"${selfClose ? '/' : ''}>`;
  });
  return { html: out, titled };
}

/** A human title for an embed URL, or null when its URL says nothing useful. */
export function titleForEmbed(src) {
  if (typeof src !== 'string' || !src) return null;
  let url;
  try {
    url = new URL(src, 'https://example.invalid');
  } catch {
    return null;
  }
  const host = url.hostname.replace(/^www\./i, '');
  const segments = url.pathname.split('/').filter(Boolean);
  // Provider path furniture — "embed", "episodes", "watch" — names the medium,
  // not the thing. The last segment that is not furniture is the slug.
  const FURNITURE = new Set([
    'embed',
    'embeds',
    'episodes',
    'episode',
    'watch',
    'v',
    'e',
    'player',
  ]);
  let slug = null;
  for (let i = segments.length - 1; i >= 0; i -= 1) {
    if (FURNITURE.has(segments[i].toLowerCase())) continue;
    slug = segments[i];
    break;
  }
  if (!slug) return null;
  const words = decodeURIComponent(slug)
    .replace(/\.[a-z0-9]{1,5}$/i, '')
    .replace(/[-_]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  // A bare opaque id (a YouTube video id, a hash) is not a description. Titling
  // an embed "dQw4w9WgXcQ" is worse than leaving the provider to be announced.
  if (!words || !/\s/.test(words)) return host ? `Embedded content from ${host}` : null;
  return `${words.charAt(0).toUpperCase()}${words.slice(1)} — embedded from ${host}`;
}

export function escapeAttr(s) {
  return String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
}

/* ------------------------------------------------------------------ *
 * Fitting the captured document into a layout that already owns <body>
 * ------------------------------------------------------------------ */

/**
 * Confine the captured stylesheets to the captured markup.
 *
 * A converted page is no longer a whole document: the charity's markup sits
 * inside `<div class="ffc-clone …">` alongside the FFC template's own
 * components — the cookie-consent dialog, the attribution footer, the policy
 * pages. Divi's stylesheets are written for a document it owns entirely, so
 * left global they restyle those components too. Measured here: the consent
 * dialog rendered with no background and no positioning, its text painted over
 * the page behind it, because Divi's rules beat Tailwind's utilities on
 * specificity. The `<h3>` inside it also failed colour contrast at 2.58:1 —
 * which is how the leak first surfaced, as an accessibility audit failure that
 * named an element the conversion never touched.
 *
 * Every selector is therefore prefixed with the wrapper's class, and the
 * document-level selectors (`html`, `body`, `:root`) BECOME the wrapper, since
 * the wrapper is the captured document's root now. Two things are deliberately
 * left alone: at-rule preludes, and the contents of `@keyframes`, whose
 * "selectors" are percentages and would be destroyed by a prefix.
 *
 * This is a superset of scoping `body.<class>` selectors specifically, and it
 * is idempotent: a selector already starting with the wrapper is untouched.
 */
export function scopeCloneCss(css, wrapper = 'ffc-clone') {
  if (typeof css !== 'string') return { css: '', scoped: 0 };
  const dot = `.${wrapper}`;
  let out = '';
  let buf = '';
  let scoped = 0;
  let depth = 0;
  // Depth at which the enclosing @keyframes block sits, or -1 for none.
  let keyframesDepth = -1;
  let i = 0;

  while (i < css.length) {
    const ch = css[i];

    // Comments and strings are copied verbatim: a `{` or `}` inside either is
    // not structure, and a selector-looking string is not a selector.
    if (ch === '/' && css[i + 1] === '*') {
      const end = css.indexOf('*/', i + 2);
      const stop = end === -1 ? css.length : end + 2;
      buf += css.slice(i, stop);
      i = stop;
      continue;
    }
    if (ch === '"' || ch === "'") {
      let j = i + 1;
      while (j < css.length && css[j] !== ch) j += css[j] === '\\' ? 2 : 1;
      buf += css.slice(i, Math.min(j + 1, css.length));
      i = j + 1;
      continue;
    }

    if (ch === '{') {
      const prelude = buf;
      buf = '';
      const trimmed = prelude.trim();
      const isAtRule = trimmed.startsWith('@');
      const insideKeyframes = keyframesDepth !== -1 && depth > keyframesDepth;
      if (isAtRule || insideKeyframes) {
        out += prelude + '{';
        if (isAtRule && /^@(-[a-z]+-)?keyframes\b/i.test(trimmed) && keyframesDepth === -1) {
          keyframesDepth = depth;
        }
      } else {
        const { selector, changed } = scopeSelectorList(prelude, dot);
        scoped += changed;
        out += selector + '{';
      }
      depth += 1;
      i += 1;
      continue;
    }

    if (ch === '}') {
      out += buf + '}';
      buf = '';
      depth -= 1;
      if (keyframesDepth !== -1 && depth <= keyframesDepth) keyframesDepth = -1;
      i += 1;
      continue;
    }

    buf += ch;
    i += 1;
  }
  return { css: out + buf, scoped };
}

/** Scope one comma-separated selector list, preserving its whitespace shape. */
function scopeSelectorList(prelude, dot) {
  let changed = 0;
  const scoped = prelude.split(',').map((part) => {
    const lead = part.match(/^\s*/)[0];
    const tail = part.match(/\s*$/)[0];
    const sel = part.slice(lead.length, part.length - tail.length);
    if (!sel) return part;
    if (
      sel === dot ||
      sel.startsWith(`${dot} `) ||
      sel.startsWith(`${dot}.`) ||
      sel.startsWith(`${dot}:`) ||
      sel.startsWith(`${dot}[`)
    ) {
      return part;
    }
    changed += 1;
    // `html`, `body` and `:root` name the captured document's root, which is
    // now the wrapper — so they are REPLACED rather than prefixed. Prefixing
    // would produce `.ffc-clone body`, which matches nothing.
    const rooted = sel.replace(
      /^(?:html\b[^\s>+~]*\s*)?(?:body\b([^\s>+~]*)|:root\b([^\s>+~]*))/i,
      (whole, bodyQual, rootQual) => `${dot}${bodyQual ?? rootQual ?? ''}`,
    );
    if (rooted !== sel) return `${lead}${rooted}${tail}`;
    return `${lead}${dot} ${sel}${tail}`;
  });
  return { selector: scoped.join(','), changed };
}

/** `<head>` inner HTML, or '' when the document has none. */
export function extractHead(html) {
  return /<head\b[^>]*>([\s\S]*?)<\/head>/i.exec(html)?.[1] ?? '';
}

/**
 * The subset of the captured `<head>` that has to travel with the fragment.
 *
 * Divi's presentation is split across a dozen inline `<style>` blocks and as
 * many per-post stylesheets, so a fragment without them renders as unstyled
 * markup. Everything the App Router generates for itself is dropped instead of
 * duplicated: a second `<title>`, a second `<meta name="description">` and — the
 * one that actually costs ranking — a second `rel="canonical"` pointing at the
 * capture's relative path rather than the route's real URL.
 *
 * `<link rel="preload" as="style" onload="this.rel='stylesheet'">` is Divi's
 * async-CSS trick. It is converted to a plain stylesheet link rather than
 * carried across: an inline event handler is the one construct here that needs
 * `script-src 'unsafe-inline'` to survive, and the whole point of the deferral
 * — not blocking first paint on a late stylesheet — is moot for a page whose
 * HTML is already on disk.
 */
export function fragmentHead(headHtml) {
  if (typeof headHtml !== 'string' || !headHtml) return { html: '', styles: 0, dropped: 0 };
  const kept = [];
  let dropped = 0;

  const styleBlocks = [...headHtml.matchAll(/<style\b[^>]*>[\s\S]*?<\/style>/gi)];
  const links = [...headHtml.matchAll(/<link\b[^>]*?\/?>/gi)];

  // Document order matters: a later stylesheet is meant to override an earlier
  // one, and interleaved <style>/<link> is exactly how Divi expresses that.
  const nodes = [...styleBlocks, ...links].sort((a, b) => a.index - b.index);

  for (const node of nodes) {
    const tag = node[0];
    if (/^<style/i.test(tag)) {
      kept.push(tag);
      continue;
    }
    const rel = /\brel\s*=\s*["']([^"']*)["']/i.exec(tag)?.[1]?.toLowerCase() ?? '';
    const as = /\bas\s*=\s*["']([^"']*)["']/i.exec(tag)?.[1]?.toLowerCase() ?? '';
    if (rel === 'stylesheet') {
      kept.push(tag);
    } else if (rel === 'preload' && as === 'style') {
      // Strip the inline onload handler along with the deferral it drove.
      kept.push(
        tag
          .replace(/\bonload\s*=\s*(["'])[\s\S]*?\1/i, '')
          .replace(/\brel\s*=\s*["']preload["']/i, "rel='stylesheet'")
          .replace(/\bas\s*=\s*["']style["']/i, '')
          .replace(/\s{2,}/g, ' '),
      );
    } else if (rel === 'preload' && as === 'font') {
      kept.push(tag);
    } else {
      dropped += 1;
    }
  }
  return { html: kept.join('\n'), styles: kept.length, dropped };
}

/**
 * Remove the source site's dead cookie-consent UI.
 *
 * The captured pages carry WordPress's Cookie Law Info banner and its
 * "settings" modal — 11.9 KB on every one of 587 pages — and the plugin's
 * JavaScript is gone, so nothing can dismiss it, nothing records a choice, and
 * the modal it opens cannot be opened. It says "By clicking Accept, you consent
 * to the use of ALL the cookies", which is a statement the page can no longer
 * make true. Shipping it is the consent-banner version of shipping a contact
 * form that posts to a dead endpoint, and the FFC template supplies a working
 * consent banner in its place.
 *
 * It also breaks the working one: two `role="dialog"` elements where the site's
 * own tests and any assistive technology expect one.
 *
 * The plugin brackets its own markup in `googleoff`/`googleon` comments — it
 * emits them to keep the consent text out of search snippets — which is an
 * exact boundary rather than a guessed one. The block is only removed when it
 * actually contains the plugin's markup, so the marker pair alone can never
 * delete a section of the charity's content.
 */
export function removeDeadConsentUi(html) {
  if (typeof html !== 'string') return { html: '', removed: 0, bytes: 0 };
  let removed = 0;
  let bytes = 0;
  const out = html.replace(
    /<!--\s*googleoff:\s*all\s*-->([\s\S]*?)<!--\s*googleon:\s*all\s*-->/gi,
    (whole, inner) => {
      if (!/cookie-law-info|cli-modal|cliSettingsPopup/i.test(inner)) return whole;
      removed += 1;
      bytes += whole.length;
      return '';
    },
  );
  // The overlay divs the plugin appends after its own block, if any escaped it.
  const swept = out.replace(/<div class="cli-modal-backdrop[^"]*"><\/div>/gi, () => {
    bytes += 1;
    return '';
  });
  return { html: swept, removed, bytes };
}

/**
 * Remove what the root layout already provides, so the page has one of each.
 *
 * The capture gives the content wrapper `id="main-content" role="main"` so that
 * a standalone HTML file has a main landmark and a skip-link target. Inside a
 * route both already exist on the layout's `<main id="main-content">`, and
 * keeping the captured pair produces a duplicate id and a second `main`
 * landmark — `landmark-one-main` and `duplicate-id`, two audits that a
 * standalone page passes and a routed one fails.
 *
 * `<script>` goes too. Static export writes this fragment into the emitted
 * HTML, so a `<script src>` here really would run on a cold load and then
 * silently not run on any client-side navigation — a behaviour that is present
 * or absent depending on how the visitor arrived. The clone's one script is a
 * React client component instead.
 */
export function stripLayoutDuplicates(bodyHtml) {
  let removedMain = 0;
  let removedScripts = 0;
  let out = bodyHtml.replace(
    /<div\b([^>]*\bid\s*=\s*["']main-content["'][^>]*)>/i,
    (whole, attrs) => {
      removedMain += 1;
      const cleaned = attrs
        .replace(/\bid\s*=\s*["']main-content["']/i, '')
        .replace(/\brole\s*=\s*["']main["']/i, '')
        .replace(/\s{2,}/g, ' ')
        .trim();
      return `<div${cleaned ? ` ${cleaned}` : ''}>`;
    },
  );
  out = out.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, () => {
    removedScripts += 1;
    return '';
  });
  out = out.replace(/<script\b[^>]*\/>/gi, () => {
    removedScripts += 1;
    return '';
  });
  return { html: out, removedMain, removedScripts };
}

/* ------------------------------------------------------------------ *
 * Asset references and the emitted route
 * ------------------------------------------------------------------ */

/**
 * Rewrite the capture's page-relative asset paths to a `%%BASE%%` token.
 *
 * The captured markup addresses assets relative to the page's own depth
 * (`../../_ffc-assets/…`), which is correct for a file served from that
 * directory and wrong for a React component rendered at an arbitrary route.
 * The token is substituted with `NEXT_PUBLIC_BASE_PATH` at build time, which is
 * how the same markup serves correctly from both the project-pages subpath and
 * a custom domain — and it keeps raw asset URLs out of the repo's `assetPath()`
 * drift scan, which is why the production pattern uses a token rather than an
 * absolute path.
 */
export function tokenizeAssetPaths(html, assetsDirName = '_ffc-assets') {
  const re = new RegExp(
    `(?:\\.\\./)+${escapeRe(assetsDirName)}/|\\./${escapeRe(assetsDirName)}/`,
    'g',
  );
  let rewritten = 0;
  const out = html.replace(re, () => {
    rewritten += 1;
    return `%%BASE%%/${assetsDirName}/`;
  });
  return { html: out, rewritten };
}

/**
 * Rewrite in-clone page links to root-absolute route paths.
 *
 * Same reason as the assets: `../about-us/` resolves against the file's
 * directory, and a route is not a file. `%%BASE%%` carries the subpath.
 */
export function tokenizePageLinks(html, routes) {
  // The capture's own path and the route's slug are not the same string:
  // `sanitizeSlug` strips characters a directory name and a URL cannot both
  // carry, so a link has to be looked up by the path the markup names and
  // rewritten to the slug the route ended up at. Accepting a bare list of
  // slugs (where the two happen to coincide) keeps the simple case simple.
  const known = routes instanceof Map ? routes : new Map([...routes].map((s) => [s, s]));
  let rewritten = 0;
  // The query and fragment are captured and carried across rather than
  // excluded from the match. Excluding them looks harmless and is not: this
  // capture links to its own posts as `../a-new-heart/#comments` and
  // `../gossip-about-god/?replytocom=1`, so a pattern that stops at `#` or `?`
  // leaves 351 links to LIVE pages unrewritten — which then read as links to
  // pages the capture does not have, and were very nearly unlinked as dead.
  const out = html.replace(
    /\bhref="((?:\.\.\/)+|\.\/)([^"#?]*?)([?#][^"]*)?"/g,
    (whole, prefix, rest, suffix = '') => {
      const target = rest.replace(/\/+$/, '');
      if (target === '') {
        rewritten += 1;
        return `href="%%BASE%%/${suffix}"`;
      }
      if (!known.has(target)) return whole;
      const slug = known.get(target);
      rewritten += 1;
      return `href="%%BASE%%/${slug}/${suffix}"`;
    },
  );
  return { html: out, rewritten };
}

/**
 * A TypeScript string literal in the FFC-EX house style.
 *
 * The receiving repos run Prettier with `singleQuote: true` and check the
 * formatting in CI, so `JSON.stringify` — the obvious choice, and correct
 * JavaScript — produces 587 files that fail their own repo's lint on the first
 * commit. Built from JSON.stringify's escaping rather than by hand so control
 * characters and non-ASCII stay handled.
 */
export function tsString(value) {
  const json = JSON.stringify(String(value ?? ''));
  const inner = json.slice(1, -1).replace(/\\"/g, '"').replace(/'/g, "\\'");
  return `'${inner}'`;
}

/**
 * Point a reference at the copy of the file the capture actually downloaded.
 *
 * WordPress links its uploads with an ordinary `<a href>` — a tract PDF, a
 * flyer — and a PDF is not a page, so the capture's page-link pass has no
 * inventory entry to rewrite it against, while its asset pass only rewrites
 * references it fetched THROUGH. The file is downloaded (something else on the
 * site references it as an asset) and this one reference is left pointing at
 * `../wp-content/uploads/…`, which resolves to a path the static site does not
 * serve. Both of this capture's downloadable tracts were dead this way.
 *
 * `resolve` is supplied by the caller because only it can see the filesystem:
 * it takes the source-relative path (`wp-content/uploads/x.pdf`) and returns
 * the localized path if that file was captured, or null. A reference whose file
 * was never captured is LEFT ALONE rather than pointed somewhere plausible —
 * `unlinkDeadPageLinks` deals with it, and inventing a target would turn a
 * visible 404 into a silently wrong link.
 */
export function localizeRootAssetRefs(html, resolve) {
  let rewritten = 0;
  let unresolved = 0;
  const out = html.replace(
    /(["'(])((?:\.\.\/)+|\.\/)(wp-content\/[^"')\s]+)/g,
    (whole, open, prefix, rest) => {
      const target = resolve(rest);
      if (!target) {
        unresolved += 1;
        return whole;
      }
      rewritten += 1;
      return `${open}${target}`;
    },
  );
  return { html: out, rewritten, unresolved };
}

/**
 * Turn a link to a page this migration does not have into plain text.
 *
 * WordPress fills a theme with navigation to pages that only a database can
 * produce: an author archive (`/author/admin/`, on the byline of all 817 posts
 * here) and archive pagination (`/tag/<x>/page/2/`). Neither exists in a static
 * capture, so every one of them is a 404 for a real visitor and a dead end for
 * a crawler ranking the site.
 *
 * The anchor is unwrapped rather than deleted: the byline still reads
 * "Viewpoint Ministries", the pager still shows its numbers, and not one word
 * of the charity's own content is removed — only the promise that clicking it
 * goes somewhere. Deleting the element would be the tidier-looking change and
 * would silently drop content this script has no business editing.
 *
 * `isLive` is the caller's: a target is live if it is a captured page or a file
 * that shipped. Only in-clone relative hrefs are considered — an external link,
 * an anchor, a `mailto:` and an already-rewritten `%%BASE%%` link are all left
 * exactly as they are.
 */
export function unlinkDeadPageLinks(html, isLive) {
  let unlinked = 0;
  const dead = new Map();
  const out = html.replace(/<a\b([^>]*)>([\s\S]*?)<\/a>/gi, (whole, attrs, inner) => {
    const href = /\bhref\s*=\s*["']([^"']*)["']/i.exec(attrs)?.[1];
    if (!href || !/^(?:\.\.\/|\.\/)/.test(href)) return whole;
    const target = href
      .split(/[?#]/)[0]
      .replace(/\/+$/, '')
      .replace(/^(?:\.\.\/|\.\/)+/, '');
    if (isLive(target)) return whole;
    unlinked += 1;
    dead.set(target, (dead.get(target) ?? 0) + 1);
    return inner;
  });
  return { html: out, unlinked, dead };
}

/**
 * Drop a stylesheet link whose file did not ship.
 *
 * Divi builds a per-taxonomy stylesheet at request time
 * (`wp-content/et-cache/taxonomy/post_tag/188/…`), and for ten of this
 * capture's archive pages that file was never fetched — it exists only when
 * WordPress generates it. The link survives into the fragment and every visit
 * to those pages makes a request that 404s.
 *
 * Removing it is safe in a way that removing markup usually is not: a
 * stylesheet that cannot be fetched has never applied a rule, so the page looks
 * identical with it gone. What changes is a failed request and a console error.
 *
 * Only relative in-clone hrefs are considered — a `%%BASE%%` link has already
 * been resolved against a file that exists, and an external stylesheet is not
 * this function's business.
 */
export function dropMissingStylesheets(html, isShipped) {
  let dropped = 0;
  const out = html.replace(/<link\b([^>]*?)\/?>\s*/gi, (whole, attrs) => {
    if (!/\brel\s*=\s*["']stylesheet["']/i.test(attrs)) return whole;
    const href = /\bhref\s*=\s*["']([^"']*)["']/i.exec(attrs)?.[1];
    if (!href || !/^(?:\.\.\/|\.\/)/.test(href)) return whole;
    const target = href.split(/[?#]/)[0].replace(/^(?:\.\.\/|\.\/)+/, '');
    if (isShipped(target)) return whole;
    dropped += 1;
    return '';
  });
  return { html: out, dropped };
}

/** The `page.tsx` for one route. */
export function routeSource({ slug, title, description, wrapperClass = 'ffc-clone' }) {
  // The trailing slash is not cosmetic. The export runs with
  // `trailingSlash: true`, because the source WordPress served every page at a
  // trailing-slash URL and the converted pages link to each other that way — so
  // `/about-us` is not a page on this host at all. A canonical without it names
  // a 404 on all 596 pages, which is worse than having no canonical: it tells a
  // crawler the real page is a duplicate of a URL that does not exist.
  const canonical = slug ? `/${slug}/` : '/';
  const lines = [
    "import type { Metadata } from 'next'",
    "import { loadCloneContent } from '@/lib/clone-content'",
    '',
    '// Generated by workflow 706 from the live site capture. Edit the source site,',
    '// or the converter in FFC-Cloudflare-Automation, rather than this file.',
    'export const metadata: Metadata = {',
    `  title: ${tsString(title)},`,
  ];
  if (description) lines.push(`  description: ${tsString(description)},`);
  lines.push(
    `  alternates: { canonical: ${tsString(canonical)} },`,
    '}',
    '',
    'export default function Page() {',
    '  return (',
    // The captured <body> class list moves onto this wrapper, and
    // rescopeBodySelectors() retargets Divi's 9,525 `body.<class>` rules at
    // it. Both halves are required: the classes without the CSS rewrite match
    // nothing, and the rewrite without the classes has nothing to match.
    `    <div`,
    `      className=${JSON.stringify(wrapperClass)}`,
    `      dangerouslySetInnerHTML={{ __html: loadCloneContent(${tsString(slug || 'index')}) }}`,
    '    />',
    '  )',
    '}',
    '',
  );
  return lines.join('\n');
}

/* ------------------------------------------------------------------ *
 * Self-test — `node scripts/clone-to-routes-lib.mjs --self-test`
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

  // --- slugs ---------------------------------------------------------
  eq('the site root is the empty slug', slugForLocalPath('index.html'), '');
  eq('a page directory becomes its slug', slugForLocalPath('about-us/index.html'), 'about-us');
  eq('a nested page keeps its path', slugForLocalPath('a/b/index.html'), 'a/b');
  eq('a non-page file has no slug', slugForLocalPath('_ffc-assets/x.css'), null);
  // This capture really contains `…-the-us￼/`, an OBJECT REPLACEMENT
  // CHARACTER no human typed; the artifact upload flagged it as a collision.
  eq(
    'an invisible control character is stripped from a slug',
    sanitizeSlug('our-world-russia-ukraine-and-the-us￼'),
    'our-world-russia-ukraine-and-the-us',
  );
  // FFC's drift guard requires kebab-case route folders, so an underscore in a
  // WordPress permalink (its rendering of a colon or period in a title) becomes
  // a hyphen rather than a route the repo's own CI rejects.
  eq(
    'an underscore becomes a hyphen',
    sanitizeSlug('the-pulpit_-impurities_w_-mr-folarin'),
    'the-pulpit-impurities-w-mr-folarin',
  );
  eq('separators collapse rather than doubling', sanitizeSlug('a--b'), 'a-b');
  eq('a nested slug sanitizes per segment', sanitizeSlug('A B/C  D'), 'a-b/c-d');
  eq('an already-clean slug is untouched', sanitizeSlug('about-us'), 'about-us');

  // RFC 3986 §6.2.2.1: the hex digits of a percent-escape are case-insensitive,
  // so these two sitemap entries name one page, not two.
  eq(
    'two spellings of one percent-escape normalize together',
    normalizePercentEncoding('a-%ef%bf%bc') === normalizePercentEncoding('a-%EF%BF%BC'),
    true,
  );
  eq(
    'the rest of the path keeps its case',
    normalizePercentEncoding('About-Us/%2f'),
    'About-Us/%2F',
  );

  // --- head extraction -----------------------------------------------
  eq(
    'the title is decoded, not passed through raw',
    extractTitle('<title>Let&#8217;s Talk &amp; Listen</title>'),
    'Let’s Talk & Listen',
  );
  eq(
    'a description is read whichever order the attributes come in',
    extractMetaDescription('<meta content="Hi there" name="description">'),
    'Hi there',
  );
  eq(
    'an empty description reads as absent, not as an empty string',
    extractMetaDescription('<meta name="description" content="   ">'),
    null,
  );

  // --- derived descriptions ------------------------------------------
  // 343 of 589 pages ship without one. Derived from the page's own words.
  const LONG = 'a'.repeat(30) + ' ' + 'b'.repeat(30) + ' ' + 'c'.repeat(200);
  eq(
    'a derived description is capped at a word boundary',
    deriveDescription(`<p>${LONG}</p>`).length <= 156,
    true,
  );
  eq(
    'and it ends with an ellipsis when it was cut',
    deriveDescription(`<p>${LONG}</p>`).endsWith('…'),
    true,
  );
  eq(
    'a page with nothing substantive gets none rather than a fabricated one',
    deriveDescription('<p>Hi</p>'),
    null,
  );
  eq(
    'markup and entities do not leak into the description',
    deriveDescription('<p>' + 'Caring for the flock &amp; the city. '.repeat(3) + '</p>').includes(
      '&amp;',
    ),
    false,
  );
  // Chrome repeats on every page, so quoting it would make 589 identical
  // descriptions — the duplicate defect wearing the missing defect's clothes.
  const WITH_CHROME =
    '<div class="et_pb_section_0_tb_header">Site navigation menu home about contact</div>' +
    '<div id="main-content"><p>' +
    'The real subject of this particular page. '.repeat(5) +
    '</p></div>' +
    '<div class="et_pb_section_0_tb_footer">For more enquires please contact us</div>';
  eq(
    'the description comes from the page, not the repeated chrome',
    deriveDescription(WITH_CHROME).startsWith('The real subject'),
    true,
  );

  eq(
    'a screen-reader-only label is not a search snippet',
    deriveDescription(
      '<div><span class="hustle-screen-reader">Share on YouTube</span>' +
        '<p>Viewpoint Ministries is a Christian outreach that publishes daily devotions, ' +
        'podcasts and printed tracts for its community.</p></div>',
    ).startsWith('Viewpoint Ministries is a Christian outreach'),
    true,
  );
  eq(
    'the site navigation is not a search snippet either',
    deriveDescription(
      '<nav><a>Home</a><a>Resources</a><a>Podcast</a><a>Contact Us</a><a>E-tracts</a></nav>' +
        '<p>Viewpoint Ministries is a Christian outreach that publishes daily devotions, ' +
        'podcasts and printed tracts for its community.</p>',
    ).startsWith('Viewpoint Ministries is a Christian outreach'),
    true,
  );

  // --- heading normalization -----------------------------------------
  // Measured shape of this capture: page title, Divi module headers that jump
  // straight to h3/h4, then the footer template's own h1.
  const DOC =
    '<div class="et_pb_text_0_tb_header"><h2>Nav</h2></div>' +
    '<div id="main-content"><div class="et_pb_text_1_tb_body"><h1 class="entry-title">Page</h1>' +
    '<div class="et_pb_blurb_2_tb_body"><h3>Sub</h3></div>' +
    '<div class="et_pb_blurb_3_tb_body"><h4>Deep</h4></div></div></div>' +
    '<div class="et_pb_text_0_tb_footer"><h1>For more enquires</h1></div>';
  const planned = planHeadingLevels(collectHeadings(DOC));
  eq(
    'levels are planned without gaps and with one h1',
    planned.map((h) => h.newLevel),
    [2, 1, 2, 3, 2],
  );
  const applied = applyHeadingLevels(DOC, planned);
  eq('exactly one h1 survives', (applied.html.match(/<h1[\s>]/g) || []).length, 1);
  eq(
    'and it is the page title, not the chrome',
    /<h1 class="entry-title">Page<\/h1>/.test(applied.html),
    true,
  );
  eq(
    'closing tags are rewritten too, not just the opening ones',
    /<h2 class="ffc-h1">For more enquires<\/h2>/.test(applied.html),
    true,
  );
  // Every retagged heading must carry its marker, or mirrorHeadingSelectors has
  // nothing to match and the retag silently restyles the page.
  eq(
    'no retagged heading is left without its marker',
    [...applied.html.matchAll(/<h[1-6][^>]*>/g)].filter((m) => !/ffc-h[1-6]/.test(m[0])).length,
    2,
  );
  // Flattening everything to h2 would also pass both checks and would destroy
  // the outline, so the nesting is asserted rather than just the counts.
  eq(
    'relative nesting survives the compression',
    [...applied.html.matchAll(/<h([1-6])[\s>]/g)].map((m) => Number(m[1])),
    [2, 1, 2, 3, 2],
  );
  // A page whose only heading is in the chrome keeps it rather than ending
  // with no h1 at all.
  eq(
    'a page with only a chrome heading still gets an h1',
    planHeadingLevels(
      collectHeadings('<div class="et_pb_text_0_tb_footer"><h1>Only</h1></div>'),
    ).map((h) => h.newLevel),
    [1],
  );
  eq('a page with no headings plans nothing', planHeadingLevels(collectHeadings('<p>hi</p>')), []);

  // --- the CSS half --------------------------------------------------
  // Measured regression: the front page's white h1 on the site's blue #1c75b9
  // band became a #333333 h2 at 2.58:1 when only the tag was changed.
  eq(
    'a global heading rule gains a twin naming the marker class',
    mirrorHeadingSelectors('h1{color:#fff}').css,
    'h1,.ffc-h1{color:#fff}',
  );
  eq(
    'a module-scoped heading rule keeps its scope in the twin',
    mirrorHeadingSelectors('.et_pb_text_0_tb_footer h1{color:#FFF}').css,
    '.et_pb_text_0_tb_footer h1,.et_pb_text_0_tb_footer .ffc-h1{color:#FFF}',
  );
  eq(
    'every selector in a list gets its own twin',
    mirrorHeadingSelectors('h1,h2{margin:0}').css,
    'h1,h2,.ffc-h1,.ffc-h2{margin:0}',
  );
  // Divi ships almost all of its layout inside media queries, so a rewrite that
  // only sees top-level rules misses most of the stylesheet.
  eq(
    'a rule inside a media query is mirrored too',
    mirrorHeadingSelectors('@media (max-width:600px){h2{font-size:1rem}}').css,
    '@media (max-width:600px){h2,.ffc-h2{font-size:1rem}}',
  );
  eq(
    'a heading that is not the subject still mirrors correctly',
    mirrorHeadingSelectors('h1 span{color:red}').css,
    'h1 span,.ffc-h1 span{color:red}',
  );
  // Three ways `h1` appears in a stylesheet without being an element selector.
  eq(
    'a class merely named h1 is left alone',
    mirrorHeadingSelectors('.h1{color:red}').css,
    '.h1{color:red}',
  );
  eq(
    'an h1 inside a url() is left alone',
    mirrorHeadingSelectors('.x{background:url(a-h1.png)}').css,
    '.x{background:url(a-h1.png)}',
  );
  eq(
    'an h1 inside a declaration value is left alone',
    mirrorHeadingSelectors('p{content:"h1"}').css,
    'p{content:"h1"}',
  );
  eq(
    'a stylesheet with no headings is returned unchanged',
    mirrorHeadingSelectors('.a{b:c}').mirrored,
    0,
  );

  // --- the element half ----------------------------------------------
  const retagged = applyHeadingLevels(
    '<h1 class="title">A</h1><h3>B</h3>',
    planHeadingLevels(collectHeadings('<h1 class="title">A</h1><h3>B</h3>')),
  );
  eq(
    'a retagged heading remembers the level it used to be',
    retagged.html,
    '<h1 class="title">A</h1><h2 class="ffc-h3">B</h2>',
  );
  eq(
    'the marker joins an existing class list rather than replacing it',
    withLevelClass(' class="a b" id="x"', 1),
    ' class="a b ffc-h1" id="x"',
  );
  eq(
    'a heading with no class attribute gains one',
    withLevelClass(' id="x"', 3),
    ' id="x" class="ffc-h3"',
  );
  eq('the marker is not added twice', withLevelClass(' class="ffc-h1"', 1), ' class="ffc-h1"');

  // --- accessibility repairs -----------------------------------------
  eq(
    'an image with no alt attribute gets an empty one',
    ensureImageAlt('<img src="a.png">').html,
    '<img src="a.png" alt="">',
  );
  eq(
    'a self-closing image keeps its slash',
    ensureImageAlt('<img src="a.png"/>').html,
    '<img src="a.png" alt=""/>',
  );
  // alt="" and a missing alt are different things to a screen reader; only the
  // second is a defect, so an existing empty alt must not be counted or touched.
  eq(
    'an existing empty alt is left exactly as it was',
    ensureImageAlt('<img alt="" src="a.png">').added,
    0,
  );
  eq('a described image is left alone', ensureImageAlt('<img alt="A cat" src="a.png">').added, 0);

  eq(
    'a link wrapping only a decorative image is named from its destination',
    nameAnonymousLinks('<a href="../"><img src="logo.png" alt=""></a>', 'Viewpoint').html,
    '<a href="../" aria-label="Viewpoint — home"><img src="logo.png" alt=""></a>',
  );
  eq(
    'a link with visible text is left alone',
    nameAnonymousLinks('<a href="/x">Read more</a>', 'V').named,
    0,
  );
  // The case above passes even without the text check, because a link with no
  // <img> is skipped for a different reason. This one has BOTH visible text and
  // a decorative image, so only the text check can save it — the shape of a
  // "Read more →" link, which this site uses throughout.
  eq(
    'a link with text AND a decorative image is still left alone',
    nameAnonymousLinks('<a href="/x">Read more <img src="arrow.png" alt=""></a>', 'V').named,
    0,
  );
  eq(
    'a link that already has an aria-label is left alone',
    nameAnonymousLinks('<a href="/" aria-label="Set"><img alt=""></a>', 'V').named,
    0,
  );
  // An image with real alt text already names its link.
  eq(
    'a link around a described image is left alone',
    nameAnonymousLinks('<a href="/x"><img alt="A cat"></a>', 'V').named,
    0,
  );
  eq('a slug label is humanised', labelForHref('../about-us/', 'V'), 'About us');
  eq('an anchor is not a destination worth naming', labelForHref('#top', 'V'), null);

  // Divi's blog module emits "Read More" for every post, so a front page listing
  // three articles offers three identical links to three different places.
  const generic = nameGenericLinks(
    '<a href="../challenges-of-abundant-living/">Read More</a>' +
      '<a href="../about-us/">About our ministry</a>',
  );
  // An aria-label would satisfy a screen reader and NOT Lighthouse's link-text
  // audit, which reads the rendered text — measured, with labels in place.
  eq(
    'a generic link says where it goes, in text',
    generic.html.includes(
      '>Read More<span class="ffc-sr-only"> about Challenges of abundant living</span></a>',
    ),
    true,
  );
  eq(
    'the visible label is unchanged, so the page still looks the same',
    generic.html.includes('>Read More<'),
    true,
  );
  eq(
    'a descriptive link is left completely alone',
    generic.html.includes('About our ministry</a>'),
    true,
  );
  eq('only the generic one was renamed', generic.named, 1);
  eq(
    'a link that already has a label is not relabelled',
    nameGenericLinks('<a href="../x/" aria-label="Existing">Read More</a>').named,
    0,
  );
  eq('naming is idempotent — a second pass adds nothing', nameGenericLinks(generic.html).named, 0);
  eq(
    'an empty link is left to nameAnonymousLinks',
    nameGenericLinks('<a href="../x/"><img alt=""></a>').named,
    0,
  );

  // Six of this capture's ten embeds have no title, so a screen reader
  // announces them as "iframe".
  const framed = titleIframes(
    '<iframe src="https://anchor.fm/viewpointministries/embed/episodes/Trust-If-e1abc"></iframe>' +
      '<iframe title="Kept" src="https://x.test/a-b"></iframe>',
  );
  eq(
    'an embed is titled from the slug its own URL carries',
    /title="Trust If e1abc — embedded from anchor\.fm"/.test(framed.html),
    true,
  );
  eq('an embed that already has a title keeps it', framed.titled, 1);
  // "embed" and "episodes" name the medium; the slug after them names the thing.
  eq(
    'provider path furniture is skipped when finding the slug',
    titleForEmbed('https://anchor.fm/show/embed/episodes/A-Long-Name'),
    'A Long Name — embedded from anchor.fm',
  );
  // Titling an embed with an opaque id is worse than naming the provider.
  eq(
    'an opaque id falls back to naming the provider',
    titleForEmbed('https://www.youtube.com/embed/dQw4w9WgXcQ'),
    'Embedded content from youtube.com',
  );
  eq('an embed with no src is left alone', titleIframes('<iframe></iframe>').titled, 0);

  // --- tokenization ---------------------------------------------------
  eq(
    'a page-relative asset path becomes a base token',
    tokenizeAssetPaths('<img src="../../_ffc-assets/a/b.png">').html,
    '<img src="%%BASE%%/_ffc-assets/a/b.png">',
  );
  eq(
    'a same-directory asset path too',
    tokenizeAssetPaths('<img src="./_ffc-assets/x.png">').html,
    '<img src="%%BASE%%/_ffc-assets/x.png">',
  );
  eq(
    'a known page link becomes a route path',
    tokenizePageLinks('<a href="../about-us/">x</a>', ['about-us']).html,
    '<a href="%%BASE%%/about-us/">x</a>',
  );
  eq(
    'the root link resolves to the base itself',
    tokenizePageLinks('<a href="../">x</a>', ['about-us']).html,
    '<a href="%%BASE%%/">x</a>',
  );
  // A relative href that is not a captured page is left alone rather than
  // rewritten into a route that does not exist.
  eq(
    'an unknown relative link is left alone',
    tokenizePageLinks('<a href="../nope/">x</a>', ['about-us']).rewritten,
    0,
  );

  // 351 links in this capture carry one; a pattern that stops at `#` or `?`
  // leaves them unrewritten, and they then read as links to pages that do not
  // exist.
  eq(
    'a fragment is carried across rather than stopping the match',
    tokenizePageLinks('<a href="../a-new-heart/#comments">x</a>', ['a-new-heart']).html,
    '<a href="%%BASE%%/a-new-heart/#comments">x</a>',
  );
  eq(
    'a query string is carried across too',
    tokenizePageLinks('<a href="../gossip/?replytocom=1">x</a>', ['gossip']).html,
    '<a href="%%BASE%%/gossip/?replytocom=1">x</a>',
  );

  eq(
    'a link is rewritten to the slug its target actually became',
    tokenizePageLinks(
      '<a href="../our-world-us\uFFFC/">x</a>',
      new Map([['our-world-us\uFFFC', 'our-world-us']]),
    ).html,
    '<a href="%%BASE%%/our-world-us/">x</a>',
  );

  // --- fitting the document into a layout that owns <body> -------------
  // Divi's stylesheets assume they own the document. Left global they restyle
  // the FFC components that now share it: the consent dialog rendered with no
  // background, its text painted over the page behind it.
  eq(
    'every ordinary selector is confined to the wrapper',
    scopeCloneCss('.a,.b{x:y}').css,
    '.ffc-clone .a,.ffc-clone .b{x:y}',
  );
  // 9,525 selectors in this capture start `body.`; they carry Divi's row widths
  // and per-template layout, and the wrapper is the document root now.
  eq(
    'a body-qualified selector BECOMES the wrapper rather than nesting under it',
    scopeCloneCss('body.single #page-container .et_pb_row{width:80%}').css,
    '.ffc-clone.single #page-container .et_pb_row{width:80%}',
  );
  eq(
    'a bare body rule styles the wrapper',
    scopeCloneCss('body{margin:0}').css,
    '.ffc-clone{margin:0}',
  );
  eq(
    'html and :root are the same root, not ancestors of it',
    [scopeCloneCss('html body .x{color:red}').css, scopeCloneCss(':root{--a:1px}').css],
    ['.ffc-clone .x{color:red}', '.ffc-clone{--a:1px}'],
  );
  // Divi ships almost all of its layout inside media queries.
  eq(
    'a rule inside a media query is scoped, its at-rule prelude is not',
    scopeCloneCss('@media (max-width:600px){h2{font-size:1rem}}').css,
    '@media (max-width:600px){.ffc-clone h2{font-size:1rem}}',
  );
  // Keyframe "selectors" are percentages; a prefix destroys the animation.
  eq(
    'keyframe steps are left alone',
    scopeCloneCss('@keyframes fade{0%{opacity:0}100%{opacity:1}}').css,
    '@keyframes fade{0%{opacity:0}100%{opacity:1}}',
  );
  eq(
    'an @font-face block is left alone',
    scopeCloneCss('@font-face{font-family:X;src:url(a.woff2)}').css,
    '@font-face{font-family:X;src:url(a.woff2)}',
  );
  // A brace inside a string is not structure; reading it as one desynchronises
  // the walker for the rest of the file.
  eq(
    'a brace inside a declaration string does not end the block',
    scopeCloneCss('.x{content:"}"}').css,
    '.ffc-clone .x{content:"}"}',
  );
  eq(
    'scoping is idempotent — a second pass changes nothing',
    scopeCloneCss(scopeCloneCss('body.single .x{a:b}').css).css,
    '.ffc-clone.single .x{a:b}',
  );
  eq('the scope reports how many selectors it moved', scopeCloneCss('.a{}.b{}').scoped, 2);

  // --- the fragment's head --------------------------------------------
  const HEAD = [
    '<title>About Us</title>',
    '<meta name="description" content="x">',
    '<link rel="canonical" href="../about-us/">',
    '<link rel="dns-prefetch" href="//cdn.example">',
    '<style id="critical">.a{color:red}</style>',
    '<link rel="stylesheet" href="../_ffc-assets/a.css">',
    '<link rel="preload" as="style" href="../_ffc-assets/late.css" onload="this.rel=\'stylesheet\'">',
    '<link rel="preload" as="font" href="../_ffc-assets/f.woff2" crossorigin>',
  ].join('\n');
  const fh = fragmentHead(HEAD);
  eq(
    'the critical inline style travels with the fragment',
    fh.html.includes('.a{color:red}'),
    true,
  );
  eq('a real stylesheet travels with the fragment', fh.html.includes('_ffc-assets/a.css'), true);
  eq('a font preload travels with the fragment', fh.html.includes('f.woff2'), true);
  // Next.js writes these itself; a second canonical is the one that costs
  // ranking rather than merely duplicating.
  eq('the captured canonical is dropped', fh.html.includes('canonical'), false);
  eq('the captured title is dropped', fh.html.includes('<title'), false);
  eq('the captured description is dropped', fh.html.includes('name="description"'), false);
  eq(
    'an async style preload becomes a plain stylesheet',
    /rel=.stylesheet.[^>]*late\.css|late\.css[^>]*rel=.stylesheet./.test(fh.html),
    true,
  );
  eq('its inline onload handler does not survive', fh.html.includes('onload'), false);
  eq(
    'document order is preserved so a later sheet still overrides an earlier one',
    fh.html.indexOf('critical') < fh.html.indexOf('a.css'),
    true,
  );

  // 11.9 KB on every one of 587 pages: a consent banner whose JavaScript is
  // gone, so it cannot be dismissed and records nothing, next to a working one.
  const CONSENT =
    '<p>keep me</p><!--googleoff: all--><div id="cookie-law-info-bar">Accept</div>' +
    '<div class="cli-modal" id="cliSettingsPopup" role="dialog"></div><!--googleon: all-->' +
    '<div class="cli-modal-backdrop cli-fade"></div><p>and me</p>';
  const consent = removeDeadConsentUi(CONSENT);
  eq('the dead consent banner is gone', consent.html.includes('cookie-law-info-bar'), false);
  eq('so is the modal it could never open', consent.html.includes('cliSettingsPopup'), false);
  eq('and its backdrop', consent.html.includes('cli-modal-backdrop'), false);
  eq('the page content around it is untouched', consent.html, '<p>keep me</p><p>and me</p>');
  // The marker pair is the plugin's own; another plugin using it must not lose
  // a section of the charity's content to this.
  eq(
    'a googleoff block that is not the consent plugin is left alone',
    removeDeadConsentUi('<!--googleoff: all--><p>a phone number</p><!--googleon: all-->').removed,
    0,
  );

  // --- what the layout already provides --------------------------------
  const dup = stripLayoutDuplicates(
    '<div id="main-content" role="main" class="keep"><p>hi</p></div><script src="x.js"></script>',
  );
  eq('the duplicate main id is removed', dup.html.includes('main-content'), false);
  eq('the duplicate main role is removed', dup.html.includes('role="main"'), false);
  eq('the wrapper keeps its other attributes', dup.html.includes('class="keep"'), true);
  eq('the content itself is untouched', dup.html.includes('<p>hi</p>'), true);
  eq('the script is removed', dup.html.includes('<script'), false);
  eq('the removals are counted', [dup.removedMain, dup.removedScripts], [1, 1]);
  eq(
    'a page with neither is left exactly as it was',
    stripLayoutDuplicates('<div><p>hi</p></div>').html,
    '<div><p>hi</p></div>',
  );

  // The receiving repos format with `singleQuote: true` and check it in CI.
  eq('a plain string comes out single-quoted', tsString('hi'), "'hi'");
  eq('an apostrophe is escaped', tsString("it's"), "'it" + String.fromCharCode(92) + "'s'");
  eq('a double quote is NOT escaped inside single quotes', tsString('say "hi"'), '\'say "hi"\'');
  eq(
    'a backslash survives as an escaped backslash',
    tsString('a' + String.fromCharCode(92) + 'b'),
    "'a" + String.fromCharCode(92) + String.fromCharCode(92) + "b'",
  );
  eq(
    'a newline becomes an escape, never a real line break',
    tsString('a\nb').includes('\n'),
    false,
  );

  // Both of this capture's downloadable tracts were dead: WordPress links an
  // upload with a plain <a href>, which is neither a page nor an asset the
  // capture rewrote, while the file itself was downloaded via another
  // reference.
  const relinked = localizeRootAssetRefs(
    '<a href="../wp-content/uploads/2022/07/Tract.pdf">Read</a>' +
      '<a href="../wp-content/et-cache/taxonomy/category/1/x.css">Gone</a>',
    (rel) => (rel.endsWith('Tract.pdf') ? `%%BASE%%/_ffc-assets/site.org/${rel}` : null),
  );
  eq(
    'a reference is pointed at the copy that was captured',
    relinked.html.includes(
      'href="%%BASE%%/_ffc-assets/site.org/wp-content/uploads/2022/07/Tract.pdf"',
    ),
    true,
  );
  // Inventing a target turns a visible 404 into a silently wrong link.
  eq(
    'a reference whose file was never captured is left alone',
    relinked.html.includes('href="../wp-content/et-cache/taxonomy/category/1/x.css"'),
    true,
  );
  eq('both outcomes are counted', [relinked.rewritten, relinked.unresolved], [1, 1]);
  eq(
    'a url() in a style attribute is rewritten too, not only an href',
    localizeRootAssetRefs(
      '<span style="background:url(./wp-content/a.jpg)">',
      (r) => `%%BASE%%/${r}`,
    ).html,
    '<span style="background:url(%%BASE%%/wp-content/a.jpg)">',
  );

  // 817 of this capture's posts carry a byline linking to /author/admin/, a
  // WordPress archive only a database can produce.
  const live = (t) => t === 'about-us';
  const unlinked = unlinkDeadPageLinks(
    '<a href="../../author/admin/">Viewpoint Ministries</a>' +
      '<a href="../about-us/">About</a>' +
      '<a href="https://example.org/x">Out</a>' +
      '<a href="#top">Top</a>' +
      '<a href="%%BASE%%/podcast/">Podcast</a>',
    live,
  );
  // The byline still reads the same; only the promise that it goes somewhere
  // is gone. Deleting the element would drop the charity's own words.
  eq(
    'a link to a page the capture does not have becomes its own text',
    unlinked.html.includes('Viewpoint Ministries</a>'),
    false,
  );
  eq('and that text survives', unlinked.html.includes('Viewpoint Ministries'), true);
  eq('a link to a captured page is untouched', unlinked.html.includes('href="../about-us/"'), true);
  eq('an external link is untouched', unlinked.html.includes('https://example.org/x'), true);
  eq('an in-page anchor is untouched', unlinked.html.includes('href="#top"'), true);
  eq(
    'an already-rewritten link is untouched',
    unlinked.html.includes('href="%%BASE%%/podcast/"'),
    true,
  );
  eq('exactly one was unlinked', unlinked.unlinked, 1);
  eq('and the dead target is reported by name', [...unlinked.dead.keys()], ['author/admin']);
  // A query string is not part of the target: /resources/page/2/?et_blog is
  // the same dead page with or without it.
  eq(
    'a query string does not hide a dead target',
    unlinkDeadPageLinks('<a href="../resources/page/2/?et_blog">2</a>', live).unlinked,
    1,
  );

  // Divi builds a per-taxonomy stylesheet at request time; for ten of this
  // capture's archive pages that file was never fetched, so every visit made a
  // request that 404s. A stylesheet that cannot load has never applied a rule.
  const sheets = dropMissingStylesheets(
    '<link rel="stylesheet" href="../../wp-content/et-cache/taxonomy/post_tag/188/x.css">' +
      '<link rel="stylesheet" href="../shipped.css">' +
      '<link rel="stylesheet" href="%%BASE%%/_ffc-assets/a.css">' +
      '<link rel="stylesheet" href="https://fonts.example/x.css">' +
      '<link rel="preload" as="font" href="../missing.woff2">',
    (t) => t === 'shipped.css',
  );
  eq('a stylesheet that did not ship is dropped', sheets.html.includes('et-cache'), false);
  eq('one that did is kept', sheets.html.includes('href="../shipped.css"'), true);
  eq('an already-resolved link is kept', sheets.html.includes('%%BASE%%/_ffc-assets/a.css'), true);
  eq('an external stylesheet is kept', sheets.html.includes('fonts.example'), true);
  // Narrow on purpose: a font preload is not a stylesheet, and dropping one
  // would be a different decision made silently.
  eq('a non-stylesheet link is kept', sheets.html.includes('missing.woff2'), true);
  eq('exactly one was dropped', sheets.dropped, 1);

  // --- emitted route --------------------------------------------------
  const src = routeSource({ slug: 'about-us', title: 'About Us', description: 'Who we are.' });
  // The trailing slash is load-bearing: the export writes about-us/index.html
  // and nothing answers at /about-us, so a canonical without it names a 404.
  eq(
    'the route declares its own canonical, at the URL it is actually served at',
    src.includes("alternates: { canonical: '/about-us/' }"),
    true,
  );
  eq('the route carries its description', src.includes("description: 'Who we are.'"), true);
  eq('the route loads its own fragment', src.includes("loadCloneContent('about-us')"), true);
  eq(
    'the root route loads the index fragment and canonicalises to /',
    routeSource({ slug: '', title: 'Home' }).includes("loadCloneContent('index')"),
    true,
  );
  eq(
    'the route puts the captured body classes on its wrapper',
    routeSource({ slug: 'x', title: 'X', wrapperClass: 'ffc-clone single postid-9' }).includes(
      'className="ffc-clone single postid-9"',
    ),
    true,
  );
  eq(
    'a route with no description omits the key rather than emitting an empty one',
    routeSource({ slug: 'x', title: 'X' }).includes('description'),
    false,
  );

  console.log(failures ? `\n${failures} self-test failure(s)` : '\nall self-tests passed');
  return failures ? 1 : 0;
}

// Guarded on being the ENTRY POINT, not merely on the flag: without the check
// this module runs its own suite and exits during the `import` performed by any
// other script invoked with `--self-test` — which is how the converter's suite
// silently never ran while reporting "all self-tests passed".
const isEntryPoint =
  process.argv[1] && resolvePath(process.argv[1]) === resolvePath(fileURLToPath(import.meta.url));
if (isEntryPoint && process.argv.includes('--self-test')) process.exit(selfTest());
