#!/usr/bin/env node
/**
 * verify-no-legacy.mjs — prove an FFC-EX static clone is genuinely
 * self-contained before the DNS cutover.
 *
 * The fidelity signals we had were both blind in the same place.
 * `clone-site-static.mjs` reports `remainingExternalHosts` from a regex over
 * `src`/`href` attributes, and `ffc-ex-clone-fidelity-audit.md` compares image
 * counts. Neither can see:
 *
 *   1. URLs inside HTML-entity-escaped JSON. Elementor stores widget config in
 *      `data-settings`, where a URL is delimited by `&quot;` rather than a quote
 *      character, so no `src=`/`href=` match exists.
 *   2. URLs assembled at runtime. Elementor / Essential Addons / ElementsKit
 *      build content-hashed webpack chunk URLs in JavaScript. They appear in no
 *      attribute anywhere, so no static scan of any kind can find them.
 *
 * Class 2 is why slopestohope.org shipped with its "pounds distributed" counter
 * frozen at 0 for months: the counter markup ships a literal 0 and relies on
 * `counter.<hash>.bundle.min.js` to animate it. httrack never mirrored the
 * chunk, and the clone report was clean.
 *
 * This gate is immune to both because it does not scan anything. It loads each
 * page in a real browser with every request to the legacy host aborted, and
 * fails on either:
 *
 *   - a request to the legacy host  -> a dependency survived the clone
 *   - a same-origin request that 404s -> the mirror is missing an asset
 *
 * The second condition matters as much as the first. An earlier version treated
 * same-origin failures as non-fatal and reported 13/13 pages passing while four
 * Elementor bundles were silently 404ing.
 *
 * Playwright is imported dynamically so this repo stays dependency-free; CI
 * installs it just before invoking this script (see workflow 702).
 *
 * Usage:
 *   node scripts/verify-no-legacy.mjs --domain example.org --dir ../FFC-EX-example.org/out
 *   node scripts/verify-no-legacy.mjs --domain example.org --base https://example.org
 *   node scripts/verify-no-legacy.mjs --domain example.org --dir out --pages /,/about/
 *   node scripts/verify-no-legacy.mjs --domain example.org --dir out --shots /tmp/shots
 *
 * Exit codes:
 *   0  every page is self-contained
 *   1  at least one page has a surviving legacy dependency or a missing asset
 *   2  invalid usage / could not start
 *
 * A same-origin asset the clone cannot serve is excused ONLY when the source
 * answers 404 or 410 for it — dead before the migration, reproduced faithfully
 * rather than introduced. Everything else stays fatal: an asset the source
 * serves, a path the clone itself builds (`/_next/`, `/_ffc-assets/`), and any
 * answer that merely means the origin would not talk to us (401/403/429/5xx).
 * Pass --no-source-probe to skip the check, which makes every missing asset
 * fatal again.
 */

import { createServer } from 'node:http';
import { readFile, mkdir, readdir } from 'node:fs/promises';
import { join, extname, relative, resolve, isAbsolute, sep } from 'node:path';
import { existsSync } from 'node:fs';

/**
 * Paths the CLONE owns, which the source cannot speak to.
 *
 * A Next.js chunk under `/_next/` and a localized asset under `/_ffc-assets/`
 * exist only because this pipeline created them. Asking a WordPress origin for
 * one gets a 404 that means "that was never a URL here" — not "the file is
 * absent", which is what the excuse below is built on. Probing them would let a
 * genuinely broken export pass: a missing Next.js chunk is exactly the defect
 * this gate was written to catch (slopestohope.org's frozen counter), and it
 * would be excused by the source's 404 on a path the source never had.
 *
 * An explicit list, not a heuristic. The tempting rule — "a leading-underscore
 * segment is ours" — is wrong on this very migration: the source site really
 * serves `/_static/??-eJx…` concatenated CSS, a WordPress.com path that must
 * stay excusable.
 */
export const CLONE_OWNED_PREFIXES = ['/_next/', '/_ffc-assets/'];

/**
 * WordPress PHP entry points. A static export cannot serve these by
 * construction — there is no PHP — so their absence is a property of the
 * migration, not a defect in it.
 *
 * They need naming explicitly because the source-probe cannot distinguish
 * them: `admin-ajax.php` answers **HTTP 200 to almost anything**, so "the
 * source serves this, the clone lost it" is literally true and completely
 * unactionable. Measured on the first clean conversion of
 * viewpointministriesinternational.org, where the Hustle plugin fires
 * `admin-ajax.php?action=hustle_module_viewed` from the front page and took
 * the gate to 118/120 — a site whose only remaining fault was that it is
 * static.
 *
 * Deliberately narrow: PHP endpoints only, matched on the path. `/wp-json/`
 * is NOT here — a page fetching the REST API at runtime has lost real
 * function, and that deserves a human's attention rather than an excuse.
 */
export const DYNAMIC_WP_ENDPOINTS = [
  '/wp-admin/admin-ajax.php',
  '/wp-admin/admin-post.php',
  '/wp-comments-post.php',
  '/wp-cron.php',
  '/wp-login.php',
  '/xmlrpc.php',
];

/** Is this one of the PHP endpoints no static host can answer? */
export function isDynamicWordPressEndpoint(url, endpoints = DYNAMIC_WP_ENDPOINTS) {
  let path;
  try {
    path = new URL(url, 'http://x.invalid').pathname;
  } catch {
    return false;
  }
  return endpoints.some((e) => path === e || path.endsWith(e));
}

export function isCloneOwned(url, prefixes = CLONE_OWNED_PREFIXES) {
  let path;
  try {
    path = new URL(url).pathname;
  } catch {
    path = url.split('#')[0].split('?')[0];
  }
  return prefixes.some((prefix) => path.startsWith(prefix));
}

/**
 * Decide whether a same-origin request the mirror could not satisfy is a clone
 * defect or a reference that was already dead before the migration.
 *
 * The gate's rule — "a same-origin 404 means the mirror is incomplete" — is
 * right for the case it was written for (a webpack chunk that the source
 * serves and the clone missed) and wrong for a reference the SOURCE does not
 * serve either. A charity site accumulates those: on the first delivery of
 * viewpointministriesinternational.org every one of 120 pages failed on
 * `/dist/widgets.css?v=2110`, a URL fabricated at runtime by a leftover
 * WordPress.com script and 404 on the live site as well. No capture of any
 * kind can mirror a file the origin does not have, so failing on it would mean
 * FFC can never migrate a site carrying one stale reference — which is most of
 * them. This is the same rule already applied at the completeness gate, one
 * layer down.
 *
 * Fail-closed on purpose: only a probe that positively PROVES the source is
 * also missing the file may excuse it. A probe that errored, timed out or was
 * never run leaves the finding fatal, because "we could not check" must never
 * read as "it is fine".
 *
 * Returns { fatal, reason }.
 */
/**
 * Split the missing URLs into "already decided" and "must ask the source".
 *
 * ONE pass decides both, and that is the point. These used to be two
 * expressions — a loop that seeded verdicts for clone-owned paths, and a
 * filter that chose what to probe — and adding WordPress PHP endpoints to the
 * filter without adding them to the loop left them with no verdict at all.
 * Downstream, a URL absent from the map defaults to FATAL, so the skip
 * silently re-created the exact failure the excuse was written to remove.
 *
 * Caught in review before it shipped, on a run that was already in flight.
 * Deriving both halves from one predicate makes that class of mistake
 * unrepresentable rather than merely fixed.
 */
/**
 * Split one page's findings into fatal and excused, by the source's verdict.
 *
 * Missing local assets and legacy hits ask the SAME question — is this the
 * clone's fault, or is the reference already dead on the source? — so they get
 * the same answer here rather than two rules that drift apart.
 *
 * A legacy hit used to be fatal unconditionally, which is wrong for the case
 * that actually occurs: the front page of this repo's first real conversion
 * referenced two uploads the source itself 404s. No capture could localize
 * them, so the reference stayed absolute, and the gate failed the migration
 * over two images that are equally broken on the live site. Failing on that
 * means FFC can never migrate a site carrying one dead image.
 *
 * A legacy hit the source SERVES is still fatal: we had the chance to localize
 * it and did not. And the default is fail-closed — a url with no verdict at
 * all (never probed, probe errored, past the cap) counts as fatal.
 */
export function splitFindings(result, verdictFor) {
  const isFatal = (url) => (verdictFor.get(url) ?? { fatal: true }).fatal;
  const localMissing = result?.localMissing ?? [];
  const legacyHits = result?.legacyHits ?? [];
  return {
    fatalMissing: localMissing.filter((m) => isFatal(m.url)),
    excusedMissing: localMissing.filter((m) => !isFatal(m.url)),
    fatalLegacy: legacyHits.filter((u) => isFatal(u)),
    excusedLegacy: legacyHits.filter((u) => !isFatal(u)),
  };
}

export function planProbes(allMissing) {
  const decided = new Map();
  const toProbe = [];
  for (const url of allMissing) {
    // Settled without a request: the source has no opinion on the clone's own
    // build paths, and it cannot give a useful answer about its PHP endpoints.
    // Asking either would point a burst of guaranteed-useless traffic at a
    // charity's live server.
    if (isCloneOwned(url) || isDynamicWordPressEndpoint(url)) {
      decided.set(url, classifyMissing({ url }));
    } else {
      toProbe.push(url);
    }
  }
  return { decided, toProbe };
}

export function classifyMissing({ status = 0, contentType = '', error = null, url = '' } = {}) {
  if (isCloneOwned(url))
    return {
      fatal: true,
      reason: 'this path is built by the clone, so the source cannot excuse it',
    };
  // Before the source probe, because the probe cannot answer this question:
  // `admin-ajax.php` returns 200 for almost any request, so the source ALWAYS
  // appears to serve it and every such reference would be fatal forever.
  if (isDynamicWordPressEndpoint(url))
    return {
      fatal: false,
      reason: 'a WordPress PHP endpoint; a static export cannot serve it by construction',
    };
  if (error) return { fatal: true, reason: `could not check the source site (${error})` };
  // Only these two prove the origin does not HAVE the file. A 401 or 403 (a WAF
  // blocking the runner — FFC has migrated a site in exactly that state), a 429,
  // or a 5xx says the origin would not answer us, which is a different claim.
  // Reading them as "absent" is the fail-open this function exists to avoid, and
  // it fails open across the whole site at once: one WAF and every missing asset
  // is excused.
  if (status === 404 || status === 410)
    return { fatal: false, reason: `dead on the source site too (HTTP ${status})` };
  if (status >= 400)
    return {
      fatal: true,
      reason: `the source site would not answer (HTTP ${status}); that does not prove the file is absent`,
    };
  if (status === 0) return { fatal: true, reason: 'the source site was never checked' };

  // A soft 404: WordPress answers an unknown path with a themed error PAGE at
  // HTTP 200. Trusting the status alone would call that "the source serves it"
  // and fail the migration over a file that does not exist there either.
  // Narrow on purpose — it only applies where an HTML body cannot be the real
  // asset, i.e. the request is for a stylesheet, script, font or image.
  const path = url.split('#')[0].split('?')[0];
  const ext = /\.([a-z0-9]{2,5})$/i.exec(path)?.[1] ?? '';
  const assetExt =
    /^(css|js|mjs|json|png|jpe?g|gif|svg|webp|avif|ico|woff2?|ttf|eot|mp4|webm|pdf)$/i;
  if (assetExt.test(ext) && /^text\/html\b/i.test(contentType)) {
    return {
      fatal: false,
      reason: `dead on the source site too (HTTP ${status} but served as ${contentType} — a soft 404)`,
    };
  }
  return { fatal: true, reason: `the source site serves this (HTTP ${status}); the clone lost it` };
}

function arg(name, def = undefined) {
  const i = process.argv.indexOf(`--${name}`);
  return i > -1 && process.argv[i + 1] ? process.argv[i + 1] : def;
}

if (process.argv.includes('--self-test')) {
  const d = 'example.org';
  const legacy = (url) => {
    let hostname;
    try {
      hostname = new URL(url).hostname.toLowerCase();
    } catch {
      return false;
    }
    return hostname === d || hostname.endsWith(`.${d}`);
  };
  const cases = [
    ['apex is legacy', legacy('https://example.org/a.jpg'), true],
    ['www is legacy', legacy('https://www.example.org/a.jpg'), true],
    ['any subdomain is legacy', legacy('https://staging.example.org/a.jpg'), true],
    ['uppercase host still matches', legacy('https://EXAMPLE.ORG/a.jpg'), true],
    // A substring test would abort this request; it is a third party, not the
    // origin being retired.
    [
      'domain in a query string is NOT legacy',
      legacy('https://cdn.other.com/x?ref=example.org'),
      false,
    ],
    [
      'host merely ending in the bare name is NOT legacy',
      legacy('https://charity.rallyup.com/w'),
      false,
    ],
    // join() normalises `..`, so a startsWith() prefix test would pass here.
    [
      'sibling-dir traversal is contained',
      resolveWithin('/tmp/site', '/../site2/x') === null,
      true,
    ],
    [
      'encoded traversal is contained',
      resolveWithin('/tmp/site', decodeURIComponent('/%2e%2e/x')) === null,
      true,
    ],
    ['normal path resolves', resolveWithin('/tmp/site', '/wp-content/a.jpg') !== null, true],

    // --- classifyMissing ---------------------------------------------------
    // The case this exists for: /dist/widgets.css?v=2110 is fabricated at
    // runtime by a leftover WordPress.com script and 404s on the live site too.
    [
      'a 404 on the source is not the clone losing something',
      classifyMissing({ url: 'https://x.org/dist/widgets.css?v=1', status: 404 }).fatal,
      false,
    ],
    [
      'a 410 on the source is excused the same way',
      classifyMissing({ url: 'https://x.org/a.png', status: 410 }).fatal,
      false,
    ],
    // The property the gate is FOR: slopestohope.org shipped with a counter
    // frozen at 0 because a webpack chunk the source served never got mirrored.
    [
      'an asset the source still serves stays fatal',
      classifyMissing({
        url: 'https://x.org/counter.abc.bundle.min.js',
        status: 200,
        contentType: 'text/javascript',
      }).fatal,
      true,
    ],
    [
      'a redirect that resolves on the source stays fatal',
      classifyMissing({ url: 'https://x.org/a.css', status: 200, contentType: 'text/css' }).fatal,
      true,
    ],
    // Fail-closed. "We could not check" must never read as "it is fine".
    [
      'a probe that errored is fatal, not excused',
      classifyMissing({ url: 'https://x.org/a.css', error: 'timed out' }).fatal,
      true,
    ],
    [
      'a URL that was never probed is fatal',
      classifyMissing({ url: 'https://x.org/a.css' }).fatal,
      true,
    ],
    // WordPress answers an unknown path with a themed error page at HTTP 200.
    // Reading the status alone would call that "the source serves it".
    [
      'a soft 404 — HTML served for a stylesheet — is excused',
      classifyMissing({
        url: 'https://x.org/dist/widgets.css?v=2110',
        status: 200,
        contentType: 'text/html; charset=UTF-8',
      }).fatal,
      false,
    ],
    // …but only where an HTML body cannot be the real asset. An extensionless
    // endpoint may legitimately return HTML, so it is not excused.
    [
      'an extensionless 200 is NOT written off as a soft 404',
      classifyMissing({ url: 'https://x.org/cdn-cgi/rum', status: 200, contentType: 'text/html' })
        .fatal,
      true,
    ],
    [
      'a real stylesheet served as CSS is not a soft 404',
      classifyMissing({ url: 'https://x.org/a.css', status: 200, contentType: 'text/css' }).fatal,
      true,
    ],
    // --- Copilot #1229: a non-404 error code does not prove absence ---------
    // The whole excuse rests on "the origin does not have this file". A WAF
    // blocking the runner says nothing of the kind — and it fails open across
    // the entire site at once, since one WAF excuses every missing asset.
    [
      'a 403 does not excuse anything — a WAF is not proof of absence',
      classifyMissing({ url: 'https://x.org/a.css', status: 403 }).fatal,
      true,
    ],
    ['a 401 stays fatal', classifyMissing({ url: 'https://x.org/a.css', status: 401 }).fatal, true],
    ['a 429 stays fatal', classifyMissing({ url: 'https://x.org/a.css', status: 429 }).fatal, true],
    [
      'a 500 stays fatal — the origin erroring is not the file being gone',
      classifyMissing({ url: 'https://x.org/a.css', status: 500 }).fatal,
      true,
    ],
    [
      'a 405 stays fatal — method-not-allowed means the path EXISTS',
      classifyMissing({ url: 'https://x.org/cdn-cgi/rum', status: 405 }).fatal,
      true,
    ],
    [
      'the reason for an unanswerable probe says it did not prove absence',
      /does not prove the file is absent/.test(
        classifyMissing({ url: 'https://x.org/a.css', status: 403 }).reason,
      ),
      true,
    ],

    // --- Copilot #1229: the clone's own build paths ------------------------
    // A missing Next.js chunk is the exact defect this gate was written for.
    // The source 404s /_next/ because it was never a WordPress URL, so probing
    // would excuse a genuinely broken export.
    [
      'a missing Next.js chunk is NEVER excused by the source 404ing it',
      classifyMissing({ url: 'https://x.org/_next/static/chunks/main-abc.js', status: 404 }).fatal,
      true,
    ],
    [
      'a missing localized asset is not excused either',
      classifyMissing({ url: 'https://x.org/_ffc-assets/x.org__logo.png', status: 404 }).fatal,
      true,
    ],
    [
      'a clone-owned path stays fatal even when the source SERVES something there',
      classifyMissing({
        url: 'https://x.org/_next/static/chunks/main-abc.js',
        status: 200,
        contentType: 'text/javascript',
      }).fatal,
      true,
    ],
    ['/_next/ is clone-owned', isCloneOwned('https://x.org/_next/a.js'), true],
    ['/_ffc-assets/ is clone-owned', isCloneOwned('https://x.org/_ffc-assets/a.png'), true],
    // The heuristic that would have been wrong: this source really serves
    // /_static/??-eJx… concatenated CSS, and it must stay excusable.
    [
      'a leading-underscore SOURCE path is not treated as ours',
      isCloneOwned('https://x.org/_static/??-eJx7kA.css'),
      false,
    ],
    [
      'and it is still excused when the source 404s it',
      classifyMissing({ url: 'https://x.org/_static/??-eJx7kA.css', status: 404 }).fatal,
      false,
    ],
    ['an ordinary path is not clone-owned', isCloneOwned('https://x.org/wp-content/a.png'), false],
    // A prefix test, not a substring test: a real page could mention the name.
    [
      'the prefix is anchored at the path root',
      isCloneOwned('https://x.org/blog/_next/story/'),
      false,
    ],
    [
      'the reason names the status so a log reader can check the call',
      /HTTP 404/.test(classifyMissing({ url: 'https://x.org/a.css', status: 404 }).reason),
      true,
    ],

    // A WordPress PHP endpoint cannot exist on a static host. The source probe
    // cannot tell us that — admin-ajax.php answers 200 to almost anything — so
    // without this the gate calls it "the source serves it, the clone lost it",
    // which is true and unactionable, and no site using Hustle, Contact Form 7
    // or WooCommerce could ever be migrated.
    [
      'a WordPress PHP endpoint is excused — a static export cannot serve it',
      classifyMissing({
        url: 'https://x.org/wp-admin/admin-ajax.php?action=hustle_module_viewed',
        status: 200,
      }).fatal,
      false,
    ],
    [
      'the excuse holds against a 200, which is what admin-ajax always returns',
      /cannot serve it by construction/.test(
        classifyMissing({ url: 'https://x.org/wp-admin/admin-ajax.php', status: 200 }).reason,
      ),
      true,
    ],
    [
      'an ordinary asset the source serves is still FATAL — this is not a blanket',
      classifyMissing({ url: 'https://x.org/wp-content/t.js', status: 200 }).fatal,
      true,
    ],
    [
      '/wp-json/ is deliberately NOT excused — a lost REST call is lost function',
      classifyMissing({ url: 'https://x.org/wp-json/wp/v2/posts', status: 200 }).fatal,
      true,
    ],
    [
      'clone-owned still wins: /_next/wp-login.php is ours, not WordPress',
      classifyMissing({ url: 'https://x.org/_next/wp-login.php', status: 200 }).fatal,
      true,
    ],
    [
      'the endpoint match ignores the query string',
      isDynamicWordPressEndpoint('https://x.org/xmlrpc.php?rsd'),
      true,
    ],
    [
      'a path that merely CONTAINS an endpoint name is not one',
      isDynamicWordPressEndpoint('https://x.org/wp-content/xmlrpc.php.txt'),
      false,
    ],
    ['a malformed url is not an endpoint', isDynamicWordPressEndpoint('not a url at all'), false],

    // A legacy hit gets the SAME verdict rule as a missing asset. The front
    // page of the first real conversion referenced two uploads the source
    // itself 404s; no capture could localize them, and failing there means no
    // site with one dead image can ever be migrated.
    [
      'a legacy hit the source 404s is excused, not fatal',
      splitFindings(
        { legacyHits: ['https://x.org/wp-content/uploads/gone.png'], localMissing: [] },
        new Map([['https://x.org/wp-content/uploads/gone.png', { fatal: false }]]),
      ).fatalLegacy.length,
      0,
    ],
    [
      'a legacy hit the source SERVES stays fatal — we could have localized it',
      splitFindings(
        { legacyHits: ['https://x.org/wp-content/uploads/live.png'], localMissing: [] },
        new Map([['https://x.org/wp-content/uploads/live.png', { fatal: true }]]),
      ).fatalLegacy.length,
      1,
    ],
    [
      'a legacy hit with NO verdict fails closed',
      splitFindings({ legacyHits: ['https://x.org/a.png'], localMissing: [] }, new Map())
        .fatalLegacy.length,
      1,
    ],
    [
      'the excused legacy hit is still reported, not silently dropped',
      splitFindings(
        { legacyHits: ['https://x.org/gone.png'], localMissing: [] },
        new Map([['https://x.org/gone.png', { fatal: false }]]),
      ).excusedLegacy.join(','),
      'https://x.org/gone.png',
    ],
    [
      'missing assets are split by the same rule, and the two do not cross over',
      (() => {
        const out = splitFindings(
          {
            legacyHits: ['https://x.org/dead.png'],
            localMissing: [{ url: 'https://x.org/chunk.js' }],
          },
          new Map([
            ['https://x.org/dead.png', { fatal: false }],
            ['https://x.org/chunk.js', { fatal: true }],
          ]),
        );
        return `${out.fatalMissing.length}${out.excusedMissing.length}${out.fatalLegacy.length}${out.excusedLegacy.length}`;
      })(),
      '1001',
    ],
    [
      'a page with no findings at all yields four empty lists',
      (() => {
        const o = splitFindings({}, new Map());
        return (
          o.fatalMissing.length +
          o.excusedMissing.length +
          o.fatalLegacy.length +
          o.excusedLegacy.length
        );
      })(),
      0,
    ],

    // The wiring, not just the predicate. Downstream, a URL with NO entry in
    // the verdict map defaults to fatal — so anything skipped from probing
    // must also be decided here, or the skip silently re-creates the failure.
    // Reviewed into existence: the first version skipped the endpoints from
    // probing and seeded nothing, which read as correct and gated as broken.
    [
      'every url skipped from probing still gets a verdict',
      (() => {
        const all = [
          'https://x.org/wp-admin/admin-ajax.php?action=hustle_module_viewed',
          'https://x.org/_next/chunk.js',
          'https://x.org/wp-content/real.js',
        ];
        const { decided, toProbe } = planProbes(all);
        return all.every((u) => decided.has(u) || toProbe.includes(u));
      })(),
      true,
    ],
    // `?? 'ABSENT'` rather than `.fatal` directly: a regression that leaves the
    // url undecided must read as a NAMED failure. Written the obvious way, the
    // map lookup returns undefined and the assertion throws, so the mutation
    // that reproduces the real bug was detected only by a crashed self-test
    // reporting zero failures — the weak signal this repo keeps meeting.
    [
      'a WordPress endpoint is decided as EXCUSED without a probe',
      planProbes(['https://x.org/wp-admin/admin-ajax.php']).decided.get(
        'https://x.org/wp-admin/admin-ajax.php',
      )?.fatal ?? 'ABSENT',
      false,
    ],
    [
      'a clone-owned path is decided as FATAL without a probe',
      planProbes(['https://x.org/_next/chunk.js']).decided.get('https://x.org/_next/chunk.js')
        ?.fatal ?? 'ABSENT',
      true,
    ],
    [
      // Joined to a string on purpose: this harness compares with !==, so an
      // array literal is compared by reference and can never match.
      'an ordinary asset is left to be probed, not decided',
      planProbes(['https://x.org/wp-content/real.js']).toProbe.join(','),
      'https://x.org/wp-content/real.js',
    ],
    [
      'and it is NOT pre-decided, so the probe still gets to speak',
      planProbes(['https://x.org/wp-content/real.js']).decided.size,
      0,
    ],
    [
      'nothing is both decided and probed',
      (() => {
        const { decided, toProbe } = planProbes([
          'https://x.org/wp-admin/admin-ajax.php',
          'https://x.org/_next/c.js',
          'https://x.org/a.css',
        ]);
        return toProbe.some((u) => decided.has(u));
      })(),
      false,
    ],
  ];
  let failed = 0;
  for (const [name, got, want] of cases) {
    if (got !== want) {
      failed++;
      console.error(`FAIL ${name} (got ${got}, want ${want})`);
    } else {
      console.log(`ok   ${name}`);
    }
  }
  console.log(failed ? `${failed} failure(s)` : `${cases.length}/${cases.length} passed`);
  process.exit(failed ? 2 : 0);
}

const domain = arg('domain');
const dir = arg('dir');
const base = arg('base');
const shots = arg('shots');
const pagesArg = arg('pages');
const maxPages = parseInt(arg('max-pages', '40'), 10);
// Probing asks the SOURCE site whether it serves an asset the clone is missing,
// so it only makes sense when checking a local export. Opt-out, not opt-in: the
// default has to be the one that does not fail a migration over the charity's
// own pre-existing dead links.
const probeSource = Boolean(dir) && !process.argv.includes('--no-source-probe');
const UA = 'ffc-verify-no-legacy (+https://github.com/FreeForCharity)';

if (!domain || (!dir && !base)) {
  console.error(
    'Usage: --domain <apexDomain> (--dir <staticDir> | --base <url>) [--pages /a/,/b/] [--shots <dir>] [--max-pages N] [--no-source-probe]',
  );
  process.exit(2);
}
if (dir && !existsSync(dir)) {
  console.error(`[verify] static dir not found: ${dir}`);
  process.exit(2);
}

/**
 * Is this URL served by the WordPress origin being retired?
 *
 * Compares the parsed hostname rather than substring-matching the whole URL.
 * A substring test both false-matches (`https://cdn.other.com/?ref=example.org`
 * is not a legacy dependency, but would be aborted) and false-misses (a
 * differently-cased or punycoded host would slip through the gate).
 *
 * Any subdomain counts: staging.<domain> is the same install being retired.
 * Note this deliberately does not match a third-party host that merely *ends*
 * with the bare name, e.g. `<charity>.rallyup.com`.
 */
function isLegacy(url) {
  let hostname;
  try {
    hostname = new URL(url).hostname.toLowerCase();
  } catch {
    return false;
  }
  const d = domain.toLowerCase();
  return hostname === d || hostname.endsWith(`.${d}`);
}

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css',
  '.js': 'text/javascript',
  '.mjs': 'text/javascript',
  '.json': 'application/json',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.svg': 'image/svg+xml',
  '.webp': 'image/webp',
  '.avif': 'image/avif',
  '.ico': 'image/x-icon',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.ttf': 'font/ttf',
  '.eot': 'application/vnd.ms-fontobject',
  '.mp4': 'video/mp4',
  '.webm': 'video/webm',
};

/**
 * Resolve a request path inside `root`, or null if it escapes.
 *
 * A `startsWith(root)` prefix test is not containment: join() normalises `..`,
 * so `/../site2/x` under root `/tmp/site` yields `/tmp/site2/x`, which still
 * shares the prefix. Compare the relative path instead.
 */
function resolveWithin(root, requestPath) {
  const abs = resolve(root, requestPath.replace(/^\/+/, ''));
  const rel = relative(resolve(root), abs);
  if (rel.startsWith('..') || isAbsolute(rel)) return null;
  return abs;
}

function startServer(root) {
  const server = createServer(async (req, res) => {
    try {
      let p = decodeURIComponent(req.url.split('?')[0]);
      if (p.endsWith('/')) p += 'index.html';
      const file = resolveWithin(root, p);
      if (!file) {
        res.writeHead(403).end();
        return;
      }
      const body = await readFile(file);
      res.writeHead(200, {
        'Content-Type': MIME[extname(file).toLowerCase()] || 'application/octet-stream',
      });
      res.end(body);
    } catch {
      res.writeHead(404, { 'Content-Type': 'text/plain' }).end('Not Found');
    }
  });
  return new Promise((resolve) => server.listen(0, () => resolve(server)));
}

/** Every directory containing an index.html becomes a page path to check. */
async function discoverPages(root) {
  const found = [];
  async function walk(d) {
    for (const e of await readdir(d, { withFileTypes: true })) {
      const p = join(d, e.name);
      if (e.isDirectory()) {
        // Framework output, not site pages.
        if (e.name === '_next' || e.name === 'node_modules') continue;
        await walk(p);
      } else if (e.name === 'index.html') {
        const rel = relative(root, d).split(sep).filter(Boolean).join('/');
        found.push(rel ? `/${rel}/` : '/');
      }
    }
  }
  await walk(root);
  // Shallowest first, so the homepage and top-level pages are checked even when
  // the cap trims a large mirror.
  return found.sort((a, b) => a.split('/').length - b.split('/').length || a.localeCompare(b));
}

async function main() {
  let chromium;
  try {
    ({ chromium } = await import('playwright'));
  } catch {
    console.error(
      '[verify] playwright is not installed.\n' +
        '        This repo is intentionally dependency-free; install it just for this run:\n' +
        '          npm i --no-save playwright && npx playwright install --with-deps chromium',
    );
    process.exit(2);
  }

  let server;
  let origin = base;
  if (!origin) {
    server = await startServer(dir);
    origin = `http://127.0.0.1:${server.address().port}`;
  }

  let pages = pagesArg ? pagesArg.split(',').filter(Boolean) : null;
  if (!pages) {
    pages = dir ? await discoverPages(dir) : ['/'];
  }
  let truncated = 0;
  if (pages.length > maxPages) {
    truncated = pages.length - maxPages;
    pages = pages.slice(0, maxPages);
  }

  if (shots) await mkdir(shots, { recursive: true });

  const browser = await chromium.launch({
    executablePath: process.env.CHROMIUM_PATH || undefined,
  });

  const results = [];
  for (const path of pages) {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const tab = await ctx.newPage();

    const legacyHits = [];
    const localMissing = [];
    const thirdPartyFailed = [];

    // Aborting rather than allowing is the whole point: a surviving dependency
    // fails loudly here instead of quietly in production after cutover.
    await tab.route('**://*/**', (route) => {
      const url = route.request().url();
      if (isLegacy(url)) {
        legacyHits.push(url);
        return route.abort();
      }
      return route.continue();
    });

    tab.on('requestfailed', (r) => {
      const url = r.url();
      if (isLegacy(url)) return;
      const entry = `${url} (${r.failure()?.errorText})`;
      // Same-origin failures mean the mirror is incomplete. Third-party hosts
      // may simply be unreachable from CI, so those are reported, not fatal.
      if (url.startsWith(origin)) localMissing.push({ url, why: r.failure()?.errorText });
      else thirdPartyFailed.push(entry);
    });
    tab.on('response', (r) => {
      const url = r.url();
      if (url.startsWith(origin) && r.status() === 404) {
        localMissing.push({ url, why: 'HTTP 404' });
      }
    });

    const problems = [];
    try {
      const resp = await tab.goto(origin + path, { waitUntil: 'load', timeout: 45000 });
      if (resp && resp.status() >= 400) problems.push(`HTTP ${resp.status()}`);

      // Drive the page so lazily-initialised widgets request their chunks; a
      // widget that never scrolls into view never reveals a missing handler.
      await tab.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
      await tab.waitForTimeout(2500);
      await tab.evaluate(() => window.scrollTo(0, 0));
      await tab.waitForTimeout(800);

      if (shots) {
        const name = path === '/' ? 'home' : path.replace(/^\/|\/$/g, '').replace(/\//g, '_');
        await tab.screenshot({ path: join(shots, `${name}.png`) });
      }
    } catch (err) {
      problems.push(`navigation error: ${err.message}`);
    }

    // NEITHER the legacy-hit nor the missing-asset problem is added here. Both
    // are added AFTER the source probe below, because both ask the same
    // question — is this the clone's fault, or is the reference already dead
    // on the source? A reference the source 404s could not have been localized
    // by any capture: the visitor gets the same broken image either way, and
    // failing on it means FFC can never migrate a site carrying one dead
    // image, which is most real charity sites. A reference the source SERVES
    // is a genuine defect and stays fatal.

    results.push({ path, problems, legacyHits, localMissing, thirdPartyFailed });
    await ctx.close();
  }

  await browser.close();
  if (server) server.close();

  // --- Is a missing asset the clone's fault, or already dead on the source? --
  //
  // Probed once per distinct URL, and only for URLs that actually went missing,
  // so a clean run makes no network calls at all.
  // Legacy hits are probed alongside missing assets, and by the same rule.
  const allMissing = [
    ...new Set([
      ...results.flatMap((r) => r.localMissing.map((m) => m.url)),
      ...results.flatMap((r) => r.legacyHits),
    ]),
  ];
  const plan = planProbes(allMissing);
  const verdictFor = plan.decided; // url -> { fatal, reason }
  let distinctMissing = plan.toProbe;
  // A badly broken clone could name hundreds of distinct URLs, and probing them
  // all would mean pointing a burst of traffic at a charity's live site to
  // diagnose our own export. Cap it; anything past the cap stays FATAL, so the
  // cap can only ever make the gate stricter.
  const PROBE_CAP = 50;
  if (distinctMissing.length > PROBE_CAP) {
    console.log(
      `\n[verify] ${distinctMissing.length} distinct assets are missing — probing the first ${PROBE_CAP}; the rest stay fatal.`,
    );
    distinctMissing = distinctMissing.slice(0, PROBE_CAP);
  }
  if (distinctMissing.length && probeSource) {
    console.log(
      `\n[verify] ${distinctMissing.length} distinct asset(s) missing from the clone; asking ${domain} whether it serves them.`,
    );
    for (const url of distinctMissing) {
      const u = new URL(url);
      const target = `https://${domain}${u.pathname}${u.search}`;
      let probe;
      try {
        const res = await fetch(target, {
          redirect: 'follow',
          headers: { 'user-agent': UA },
          signal: AbortSignal.timeout(20000),
        });
        // Read and discard: leaving the body open holds the socket.
        await res.arrayBuffer().catch(() => {});
        probe = { url, status: res.status, contentType: res.headers.get('content-type') ?? '' };
      } catch (err) {
        probe = { url, error: err.name === 'TimeoutError' ? 'timed out' : err.message };
      }
      const v = classifyMissing(probe);
      verdictFor.set(url, v);
      console.log(`        ${v.fatal ? 'FATAL' : 'ok   '} ${u.pathname}${u.search} — ${v.reason}`);
    }
  } else if (distinctMissing.length) {
    for (const url of distinctMissing) {
      verdictFor.set(url, { fatal: true, reason: 'the source site was never checked' });
    }
  }

  for (const r of results) {
    Object.assign(r, splitFindings(r, verdictFor));
    if (r.fatalMissing.length) r.problems.push(`${r.fatalMissing.length} missing local asset(s)`);
    if (r.fatalLegacy.length)
      r.problems.push(`${r.fatalLegacy.length} request(s) to ${domain} the source still serves`);
  }

  console.log(`\nVerified ${results.length} page(s) of ${domain} against ${origin}\n`);
  let failures = 0;
  for (const r of results) {
    const ok = r.problems.length === 0;
    if (!ok) failures++;
    console.log(`${ok ? 'PASS' : 'FAIL'}  ${r.path}`);
    for (const p of r.problems) console.log(`        ! ${p}`);
    for (const u of [...new Set(r.fatalLegacy)].slice(0, 8)) console.log(`        legacy: ${u}`);
    for (const u of [...new Set(r.excusedLegacy)].slice(0, 3))
      console.log(`        legacy but dead on the source too (not fatal): ${u}`);
    for (const u of [...new Set(r.fatalMissing.map((m) => `${m.url} (${m.why})`))].slice(0, 8))
      console.log(`        MISSING LOCAL: ${u}`);
    for (const u of [...new Set(r.excusedMissing.map((m) => m.url))].slice(0, 3)) {
      console.log(`        dead on the source too (not fatal): ${u}`);
    }
    for (const u of [...new Set(r.thirdPartyFailed)].slice(0, 3)) {
      console.log(`        third-party unreachable (not fatal): ${u}`);
    }
  }

  if (truncated) {
    console.log(`\nNote: ${truncated} further page(s) were not checked (--max-pages ${maxPages}).`);
  }
  console.log(`\n${results.length - failures}/${results.length} pages passed`);

  if (process.env.GITHUB_STEP_SUMMARY) {
    const { appendFileSync } = await import('node:fs');
    const rows = results
      .map((r) => `| ${r.path} | ${r.problems.length ? '❌ ' + r.problems.join('; ') : '✅'} |`)
      .join('\n');
    appendFileSync(
      process.env.GITHUB_STEP_SUMMARY,
      `\n### Clone self-containment — ${domain}\n\n| page | verdict |\n| --- | --- |\n${rows}\n\n` +
        `${results.length - failures}/${results.length} pages passed\n`,
    );
  }

  if (failures) process.exitCode = 1;
}

main().catch((err) => {
  console.error(err);
  process.exit(2);
});
