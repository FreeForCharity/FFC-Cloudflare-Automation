#!/usr/bin/env node
/**
 * DNS / HTTPS cutover preflight for FFC-EX sites (fleet edition).
 *
 * Ported from FFC-EX-catnipandcattitude.org (scripts/preflight-cutover.mjs) and
 * generalized for any FFC-EX-<domain> site, including a comma-separated
 * multi-domain mode that prints one go/no-go verdict table — the read-only
 * companion to workflow 120 (bulk cutover staging -> apex).
 *
 * Run it BEFORE flipping DNS (confirms the GitHub Pages origin is healthy and
 * the domain's CAA won't block HTTPS) and again AFTER (confirms the custom
 * domain is live over HTTPS and serving the Pages site). It reads live DNS
 * over DoH (works anywhere — no `dig`, no local resolver config) and probes
 * HTTPS. It never changes anything.
 *
 * Per-domain questions it answers:
 *   - Where do the apex and www resolve right now? GitHub Pages, Cloudflare,
 *     or still the old host?
 *   - Do CAA records let GitHub's certificate authority (Let's Encrypt) issue,
 *     or would HTTPS provisioning silently fail?
 *   - Is the GitHub Pages origin we're cutting TO actually healthy?
 *   - Was the export built WITHOUT the github.io project basePath? A build
 *     that still emits root-relative "/FFC-EX-<repo>/…" asset/link refs serves
 *     fine on the project URL but 404s everything at the apex root after the
 *     cutover (issue #748) — a hard blocker 121/120's DNS checks can't see.
 *   - Once DNS is changed: is the custom domain live over HTTPS — including
 *     www, which the fleet DNS standard requires (www CNAME -> Pages)?
 *
 * Usage:
 *   node scripts/preflight-cutover.mjs --domains=example.org
 *   node scripts/preflight-cutover.mjs --domains=a.org,b.org,c.com
 *   node scripts/preflight-cutover.mjs --domains=example.org \
 *        --origin=https://freeforcharity.github.io/FFC-EX-example.org/ \
 *        --marker="Example Charity"
 *   node scripts/preflight-cutover.mjs --self-test
 *
 * When --origin is omitted, the FFC-EX repo's REAL name is resolved via the
 * GitHub API (repo names are mixed-case in places — e.g. FFC-EX-SRRN.net,
 * FFC-EX-AllTypeTowing.com) and the origin derived from it:
 * https://freeforcharity.github.io/<repo>/. When the API is unavailable
 * (unauthenticated / rate-limited) it falls back to the lowercase
 * naming-convention guess with a warning row. Set GITHUB_TOKEN (or GH_TOKEN)
 * to authenticate the lookup. An explicit --origin is only accepted in
 * single-domain mode. --marker is an optional content substring to require in
 * the served HTML; when omitted, content checks are skipped and the verdict
 * rests on DNS + HTTP status alone.
 *
 * Writes a markdown verdict table to $GITHUB_STEP_SUMMARY when set.
 *
 * Exit codes:
 *   0  every domain is ready to cut over (or already cut over and healthy)
 *   1  one or more domains have blockers
 *   2  invalid usage / self-test failure / crash
 */

import { pathToFileURL } from 'node:url';

// --------------------------------------------------------------------------
// Pure classification / verdict logic (exercised by --self-test and Pester).
// --------------------------------------------------------------------------

// GitHub Pages apex A records (documented, stable). A domain served by Pages
// at the apex resolves to these; a www host CNAMEs to <org>.github.io.
export const GH_PAGES_IPV4 = new Set([
  '185.199.108.153',
  '185.199.109.153',
  '185.199.110.153',
  '185.199.111.153',
]);

// GitHub Pages apex AAAA records (documented, stable) — the FFC DNS standard
// uses apex AAAA + www CNAME, so an AAAA-only apex must still classify as Pages.
export const GH_PAGES_IPV6 = new Set([
  '2606:50c0:8000::153',
  '2606:50c0:8001::153',
  '2606:50c0:8002::153',
  '2606:50c0:8003::153',
]);

// Cloudflare proxies return addresses in these ranges (104.16.0.0/13 &
// 172.64.0.0/13 are the common ones); enough to *label* the current host.
// /13 = second octet 16-23 and 64-71 respectively — kept tight so the label
// can't over-claim neighbouring space (e.g. 104.24+ or 172.72+ are NOT these).
export function isCloudflare(ip) {
  return /^104\.(1[6-9]|2[0-3])\./.test(ip) || /^172\.(6[4-9]|7[01])\./.test(ip);
}

export function classify(host) {
  if (host.ips.some((ip) => GH_PAGES_IPV4.has(ip) || GH_PAGES_IPV6.has(ip.toLowerCase()))) {
    return 'GitHub Pages';
  }
  if (host.chain.some((c) => /\.github\.io$/i.test(c.cname))) return 'GitHub Pages (CNAME)';
  if (host.ips.some(isCloudflare)) return 'Cloudflare (proxied)';
  if (host.ips.length) return `other (${host.ips.join(', ')})`;
  return 'unresolved';
}

// A DoH answer section can mix record types (e.g. a CNAME at the queried name
// plus the records it resolves to, or RRSIGs), so a CAA read must keep only
// real CAA answers (type 257) before looking at their data — the same
// type-filtering rule resolveHost applies to CNAME/A/AAAA answers.
export function caaRecordsFromAnswers(answers) {
  return answers.filter((a) => a.type === 257).map((a) => a.data);
}

// CAA record data -> the issue/issuewild property matches, if any.
// Data arrives as e.g. `0 issue "letsencrypt.org"` (flags, tag, value).
function caaIssuanceProperties(caaRecords) {
  return caaRecords
    .map((d) => /^\s*\d+\s+(issue|issuewild)\s+"?([^"]*)"?\s*$/i.exec(String(d)))
    .filter(Boolean);
}

// CAA records -> may Let's Encrypt (GitHub Pages' CA) issue?
// Per RFC 8659 §4.2 only `issue`/`issuewild` properties restrict issuance; a
// CAA RRset carrying none of them (e.g. iodef-only) leaves issuance
// unrestricted. So: no records, or no issue/issuewild properties -> any CA may
// issue; otherwise at least one issue/issuewild must name letsencrypt.org.
export function caaAllowsLetsEncrypt(caaRecords) {
  const issuance = caaIssuanceProperties(caaRecords);
  if (!issuance.length) return true;
  return issuance.some((m) => /letsencrypt\.org/i.test(m[2]));
}

// GitHub Pages project origin for a repo — preserves the repo name's case.
export function originForRepo(repoName) {
  return `https://freeforcharity.github.io/${repoName}/`;
}

// Fallback origin from the FFC-EX repo naming convention. Real fleet repos are
// mixed-case in places, so this lowercase guess is only used when the actual
// repo name can't be resolved via the GitHub API (see resolveRepoName) and no
// explicit --origin was given.
export function defaultOrigin(domain) {
  return originForRepo(`FFC-EX-${domain}`);
}

// Normalize a --domains value: strip scheme/paths, trim, drop empties, dedupe.
export function parseDomains(raw) {
  return [
    ...new Set(
      String(raw || '')
        .split(',')
        .map((d) =>
          d
            .trim()
            .toLowerCase()
            .replace(/^https?:\/\//, '')
            .replace(/\/.*$/, ''),
        )
        .filter(Boolean),
    ),
  ];
}

/**
 * Pure outcome of the apex DNS check. An apex with no usable A/AAAA answer is
 * a blocker (ok === false) so an unresolved domain can't yield a false READY.
 */
export function apexOutcome(host) {
  const cls = classify(host);
  if (cls === 'unresolved') {
    return { cls, ok: false, detail: 'no A/AAAA records (NXDOMAIN/NODATA)' };
  }
  return { cls, ok: 'info', detail: `currently served by: ${cls}` };
}

/**
 * Pure outcome of the CAA check ({ records } on success, { error } on lookup
 * failure). A failed lookup is a blocker AND reports caaOk=false — staying
 * conservative (we couldn't verify Let's Encrypt issuance isn't blocked) and
 * keeping the verdict table honest about the failure.
 */
export function caaOutcome({ records = [], error = '' }) {
  if (error) return { ok: false, caaOk: false, name: 'CAA lookup', detail: error };
  const caaOk = caaAllowsLetsEncrypt(records);
  if (!records.length) {
    return {
      ok: true,
      caaOk,
      name: 'CAA policy',
      detail: "none set — any CA may issue (Let's Encrypt OK)",
    };
  }
  const restrictsIssuance = caaIssuanceProperties(records).length > 0;
  return {
    ok: caaOk,
    caaOk,
    name: "CAA allows Let's Encrypt (GitHub Pages HTTPS)",
    detail: restrictsIssuance
      ? records.join(' | ')
      : `${records.join(' | ')} — no issue/issuewild properties, issuance unrestricted (RFC 8659)`,
  };
}

/**
 * Pure classification of the Pages origin probe (made with redirect:
 * 'manual'). A 200 is healthy. A redirect is ALSO healthy when it targets the
 * domain being cut over: fleet-wide the FFC-EX repos set a Pages custom
 * domain (cname = staging.<domain>), so the github.io origin 301s there —
 * that redirect is proof the origin exists and is bound to the right site,
 * not a failure. Any other status, or a redirect to an unrelated host, is
 * unhealthy. The redirect target is returned so callers can record it.
 */
export function originProbeOutcome({ domain, status, location = '', error = '' }) {
  if (error) return { healthy: false, redirectTarget: '', detail: error };
  if (status === 200) return { healthy: true, redirectTarget: '', detail: 'HTTP 200' };
  if ([301, 302, 307, 308].includes(status) && location) {
    let host = '';
    try {
      host = new URL(location).hostname.toLowerCase();
    } catch {
      // unparsable Location — treated as off-domain below
    }
    if (host === domain || host.endsWith(`.${domain}`)) {
      return {
        healthy: true,
        redirectTarget: location,
        detail: `origin healthy (redirects to ${location} — the repo's configured Pages domain)`,
      };
    }
    return {
      healthy: false,
      redirectTarget: location,
      detail: `HTTP ${status} redirects off-domain to ${location || '(no Location header)'}`,
    };
  }
  return { healthy: false, redirectTarget: '', detail: `HTTP ${status}` };
}

/**
 * The go/no-go verdict for one domain.
 *   originHealthy  the GitHub Pages origin returns 200 or redirects to the
 *                  repo's configured Pages domain (and serves the marker, if any)
 *   pointedAtPages apex classification starts with "GitHub Pages"
 *   blockerCount   number of failed (ok === false) checks recorded
 *   wwwHealthy     post-cutover only: www CNAMEs to Pages or still serves the
 *                  site. Defaults to true so pre-cutover verdicts (where www is
 *                  informational) are unaffected.
 * Returns { code, ok, label }: READY / CUTOVER_COMPLETE are ok; NOT_READY and
 * CUTOVER_INCOMPLETE_WWW are not.
 */
export function computeVerdict({ originHealthy, pointedAtPages, blockerCount, wwwHealthy = true }) {
  if (!originHealthy) {
    return { code: 'NOT_READY_ORIGIN', ok: false, label: 'NOT READY — Pages origin unhealthy' };
  }
  if (pointedAtPages && blockerCount === 0) {
    return wwwHealthy
      ? { code: 'CUTOVER_COMPLETE', ok: true, label: 'CUTOVER COMPLETE — live on Pages' }
      : {
          code: 'CUTOVER_INCOMPLETE_WWW',
          ok: false,
          label: 'CUTOVER INCOMPLETE — apex live on Pages but www unhealthy',
        };
  }
  if (!pointedAtPages) {
    return blockerCount === 0
      ? { code: 'READY', ok: true, label: 'READY TO CUT OVER' }
      : { code: 'NOT_READY', ok: false, label: `NOT READY — ${blockerCount} blocker(s)` };
  }
  return { code: 'NOT_READY', ok: false, label: `NOT READY — ${blockerCount} blocker(s)` };
}

// Detect root-relative references that still carry the project basePath in the
// served export, e.g. href="/FFC-EX-example.org/_next/static/…" or
// src="/FFC-EX-example.org/…". A build produced with the github.io project
// basePath works on the default Pages URL (/FFC-EX-<repo>/) but 404s EVERY
// asset and internal link the moment the apex cutover lands, because at the
// domain root the correct path is "/_next/…", not "/FFC-EX-<repo>/_next/…"
// (issue #748; fixed by the conditional-basePath pattern in FFC-EX PR #43).
//
// Only ROOT-RELATIVE refs count — the value must begin with "/FFC-EX-". An
// absolute external URL that merely contains the repo name (e.g. a GitHub
// LICENSE link https://github.com/FreeForCharity/FFC-EX-example.org/blob/…)
// starts with a scheme, so the leading-slash anchor exempts it. Returns the
// deduped list of offending attribute values.
export function basePathArtifactRefs(body) {
  const re = /\b(?:href|src)\s*=\s*["'](\/FFC-EX-[^"']*)["']/gi;
  const out = new Set();
  let m;
  while ((m = re.exec(String(body || ''))) !== null) out.add(m[1]);
  return [...out];
}

/**
 * Pure outcome of the basePath artifact check on the served export HTML.
 *   { ok: true }   no root-relative /FFC-EX-… refs — assets resolve at the root
 *   { ok: false }  basePath refs present — a hard blocker for 120 (they 404 at
 *                  the apex root after cutover); the offending refs are returned
 *   { ok: 'warn' } the export body couldn't be fetched, so identity is
 *                  unverifiable — non-blocking (origin health is judged
 *                  separately), but surfaced so the gap is visible
 */
export function basePathOutcome({ body = '', error = '' }) {
  if (error) {
    return {
      ok: 'warn',
      refs: [],
      detail: `could not fetch the export to check basePath refs: ${error}`,
    };
  }
  const refs = basePathArtifactRefs(body);
  if (!refs.length) {
    return {
      ok: true,
      refs: [],
      detail: 'no basePath (root-relative /FFC-EX-…) refs — assets resolve at the apex root',
    };
  }
  const sample = refs.slice(0, 3).join(', ');
  return {
    ok: false,
    refs,
    detail:
      `${refs.length} root-relative basePath ref(s) would 404 at the apex root after cutover ` +
      `(e.g. ${sample}) — rebuild with the conditional-basePath pattern (issue #748) before 120`,
  };
}

// --------------------------------------------------------------------------
// Self-test (offline; no network). Run via --self-test or the Pester wrapper.
// --------------------------------------------------------------------------

async function selfTest() {
  const { strict: assert } = await import('node:assert');

  // isCloudflare: tight /13 ranges only.
  assert.equal(isCloudflare('104.16.0.1'), true);
  assert.equal(isCloudflare('104.23.255.9'), true);
  assert.equal(isCloudflare('104.24.0.1'), false);
  assert.equal(isCloudflare('172.64.1.1'), true);
  assert.equal(isCloudflare('172.71.9.9'), true);
  assert.equal(isCloudflare('172.72.0.1'), false);
  assert.equal(isCloudflare('185.199.108.153'), false);

  // classify: Pages A records beat everything; CNAME chains count; label others.
  assert.equal(classify({ chain: [], ips: ['185.199.108.153'] }), 'GitHub Pages');
  assert.equal(
    classify({ chain: [{ from: 'www.x.org', cname: 'freeforcharity.github.io' }], ips: [] }),
    'GitHub Pages (CNAME)',
  );
  assert.equal(classify({ chain: [], ips: ['2606:50c0:8000::153'] }), 'GitHub Pages');
  assert.equal(classify({ chain: [], ips: ['2606:50C0:8003::153'] }), 'GitHub Pages');
  assert.equal(classify({ chain: [], ips: ['104.18.3.2'] }), 'Cloudflare (proxied)');
  assert.equal(classify({ chain: [], ips: ['203.0.113.7'] }), 'other (203.0.113.7)');
  assert.equal(classify({ chain: [], ips: [] }), 'unresolved');

  // caaRecordsFromAnswers: DoH answers can mix record types — keep CAA (257) only.
  assert.deepEqual(
    caaRecordsFromAnswers([
      { type: 5, data: 'alias.example.net' },
      { type: 257, data: '0 issue "letsencrypt.org"' },
      { type: 46, data: 'CAA 13 2 3600 ...' },
    ]),
    ['0 issue "letsencrypt.org"'],
  );
  assert.deepEqual(caaRecordsFromAnswers([{ type: 5, data: 'alias.example.net' }]), []);

  // caaAllowsLetsEncrypt: empty = permissive; iodef-only does not restrict
  // issuance (RFC 8659 §4.2); with issue/issuewild present, one must name
  // letsencrypt.org.
  assert.equal(caaAllowsLetsEncrypt([]), true);
  assert.equal(caaAllowsLetsEncrypt(['0 issue "letsencrypt.org"']), true);
  assert.equal(caaAllowsLetsEncrypt(['0 issuewild "letsencrypt.org"']), true);
  assert.equal(caaAllowsLetsEncrypt(['0 issue "digicert.com"']), false);
  assert.equal(caaAllowsLetsEncrypt(['0 issue "digicert.com"', '0 issue "letsencrypt.org"']), true);
  assert.equal(caaAllowsLetsEncrypt(['0 iodef "mailto:security@example.org"']), true);
  assert.equal(
    caaAllowsLetsEncrypt(['0 iodef "mailto:security@example.org"', '0 issue "digicert.com"']),
    false,
  );

  // originForRepo / defaultOrigin: preserve repo-name case; lowercase guess as
  // fallback (real fleet repos are mixed-case, e.g. FFC-EX-AllTypeTowing.com).
  assert.equal(
    originForRepo('FFC-EX-AllTypeTowing.com'),
    'https://freeforcharity.github.io/FFC-EX-AllTypeTowing.com/',
  );
  assert.equal(
    defaultOrigin('example.org'),
    'https://freeforcharity.github.io/FFC-EX-example.org/',
  );

  // parseDomains: trims, lowercases, strips scheme/path, dedupes, drops empties.
  assert.deepEqual(parseDomains('a.org, B.ORG ,a.org,,https://c.com/x'), [
    'a.org',
    'b.org',
    'c.com',
  ]);
  assert.deepEqual(parseDomains(''), []);

  // apexOutcome: unresolved apex is a blocker; resolved apex is informational.
  assert.equal(apexOutcome({ chain: [], ips: [] }).ok, false);
  assert.equal(apexOutcome({ chain: [], ips: [] }).detail, 'no A/AAAA records (NXDOMAIN/NODATA)');
  assert.equal(apexOutcome({ chain: [], ips: ['185.199.108.153'] }).ok, 'info');
  assert.equal(apexOutcome({ chain: [], ips: ['185.199.108.153'] }).cls, 'GitHub Pages');
  assert.equal(apexOutcome({ chain: [], ips: ['203.0.113.7'] }).ok, 'info');

  // caaOutcome: lookup failure is a blocker AND reports caaOk=false for the
  // verdict table; empty policy is permissive; explicit policy is respected;
  // an iodef-only RRset is allowed (and says why).
  assert.equal(caaOutcome({ error: 'DoH lookup failed' }).ok, false);
  assert.equal(caaOutcome({ error: 'DoH lookup failed' }).caaOk, false);
  assert.equal(caaOutcome({ records: [] }).ok, true);
  assert.equal(caaOutcome({ records: [] }).caaOk, true);
  assert.equal(caaOutcome({ records: ['0 issue "digicert.com"'] }).ok, false);
  assert.equal(caaOutcome({ records: ['0 issue "letsencrypt.org"'] }).ok, true);
  assert.equal(caaOutcome({ records: ['0 iodef "mailto:x@y.org"'] }).ok, true);
  assert.match(caaOutcome({ records: ['0 iodef "mailto:x@y.org"'] }).detail, /RFC 8659/);

  // originProbeOutcome: 200 healthy; redirect to the domain's own Pages custom
  // domain healthy (target recorded); off-domain or unparsable redirect,
  // non-200 status, and probe errors are unhealthy.
  assert.equal(originProbeOutcome({ domain: 'x.org', status: 200 }).healthy, true);
  {
    const redirected = originProbeOutcome({
      domain: 'x.org',
      status: 301,
      location: 'https://staging.x.org/',
    });
    assert.equal(redirected.healthy, true);
    assert.equal(redirected.redirectTarget, 'https://staging.x.org/');
    assert.match(redirected.detail, /origin healthy \(redirects to https:\/\/staging\.x\.org\//);
  }
  assert.equal(
    originProbeOutcome({ domain: 'x.org', status: 301, location: 'https://x.org/' }).healthy,
    true,
  );
  assert.equal(
    originProbeOutcome({ domain: 'x.org', status: 301, location: 'https://evil.example.com/' })
      .healthy,
    false,
  );
  assert.equal(
    originProbeOutcome({ domain: 'x.org', status: 301, location: 'https://evilx.org/' }).healthy,
    false,
  );
  assert.equal(
    originProbeOutcome({ domain: 'x.org', status: 301, location: '::not-a-url::' }).healthy,
    false,
  );
  assert.equal(originProbeOutcome({ domain: 'x.org', status: 404 }).healthy, false);
  assert.equal(originProbeOutcome({ domain: 'x.org', error: 'timeout' }).healthy, false);

  // computeVerdict: the four base outcomes, plus the post-cutover www rule.
  assert.equal(
    computeVerdict({ originHealthy: false, pointedAtPages: false, blockerCount: 1 }).code,
    'NOT_READY_ORIGIN',
  );
  assert.equal(
    computeVerdict({ originHealthy: true, pointedAtPages: true, blockerCount: 0 }).code,
    'CUTOVER_COMPLETE',
  );
  assert.equal(
    computeVerdict({ originHealthy: true, pointedAtPages: false, blockerCount: 0 }).code,
    'READY',
  );
  assert.equal(
    computeVerdict({ originHealthy: true, pointedAtPages: false, blockerCount: 2 }).code,
    'NOT_READY',
  );
  assert.equal(
    computeVerdict({ originHealthy: true, pointedAtPages: true, blockerCount: 1 }).code,
    'NOT_READY',
  );
  assert.equal(
    computeVerdict({ originHealthy: true, pointedAtPages: true, blockerCount: 1 }).ok,
    false,
  );
  assert.equal(
    computeVerdict({
      originHealthy: true,
      pointedAtPages: true,
      blockerCount: 0,
      wwwHealthy: false,
    }).code,
    'CUTOVER_INCOMPLETE_WWW',
  );
  assert.equal(
    computeVerdict({
      originHealthy: true,
      pointedAtPages: true,
      blockerCount: 0,
      wwwHealthy: false,
    }).ok,
    false,
  );
  assert.equal(
    computeVerdict({ originHealthy: true, pointedAtPages: true, blockerCount: 0, wwwHealthy: true })
      .code,
    'CUTOVER_COMPLETE',
  );
  // Pre-cutover verdicts ignore wwwHealthy (www is informational until the flip).
  assert.equal(
    computeVerdict({
      originHealthy: true,
      pointedAtPages: false,
      blockerCount: 0,
      wwwHealthy: false,
    }).code,
    'READY',
  );

  // basePathArtifactRefs: flags root-relative /FFC-EX-… href/src values,
  // dedupes them, and exempts absolute external URLs that merely contain the
  // repo name (they start with a scheme, not "/").
  assert.deepEqual(
    basePathArtifactRefs(
      '<link href="/FFC-EX-example.org/_next/static/x.css">' +
        '<script src="/FFC-EX-example.org/_next/chunk.js"></script>' +
        '<a href="/FFC-EX-example.org/about/">About</a>',
    ),
    [
      '/FFC-EX-example.org/_next/static/x.css',
      '/FFC-EX-example.org/_next/chunk.js',
      '/FFC-EX-example.org/about/',
    ],
  );
  assert.deepEqual(basePathArtifactRefs('<link href="/_next/static/x.css">'), []);
  // External LICENSE-style link containing the repo name is NOT root-relative.
  assert.deepEqual(
    basePathArtifactRefs(
      '<a href="https://github.com/FreeForCharity/FFC-EX-example.org/blob/main/LICENSE">LICENSE</a>',
    ),
    [],
  );
  // Dedupe repeated refs; tolerate whitespace/single quotes around the value.
  assert.deepEqual(
    basePathArtifactRefs("<img src = '/FFC-EX-x.org/a.png'><img src='/FFC-EX-x.org/a.png'>"),
    ['/FFC-EX-x.org/a.png'],
  );

  // basePathOutcome: clean export passes; basePath refs are a blocker (ok=false)
  // and are reported; an unfetchable body warns without blocking.
  assert.equal(basePathOutcome({ body: '<link href="/_next/x.css">' }).ok, true);
  {
    const bad = basePathOutcome({ body: '<link href="/FFC-EX-x.org/_next/x.css">' });
    assert.equal(bad.ok, false);
    assert.deepEqual(bad.refs, ['/FFC-EX-x.org/_next/x.css']);
    assert.match(bad.detail, /404 at the apex root/);
  }
  assert.equal(basePathOutcome({ error: 'timeout' }).ok, 'warn');

  console.log('self-test OK — classification + verdict logic passed');
}

// --------------------------------------------------------------------------
// Network probes (DoH + HTTPS + GitHub API).
// --------------------------------------------------------------------------

function arg(name, fallback) {
  const hit = process.argv.find((a) => a.startsWith(`--${name}=`));
  return hit ? hit.slice(name.length + 3) : fallback;
}

async function doh(name, type) {
  // Cloudflare first, Google as fallback — both speak the JSON DoH API.
  // Encode the query name so a malformed domain can't break the URL, and
  // bound each request with an AbortController so a hung DoH endpoint falls
  // through to the fallback (and can't stall the workflow to its job timeout).
  const q = `name=${encodeURIComponent(name)}&type=${encodeURIComponent(type)}`;
  const endpoints = [
    `https://cloudflare-dns.com/dns-query?${q}`,
    `https://dns.google/resolve?${q}`,
  ];
  let lastErr = null;
  for (const url of endpoints) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 10000);
    try {
      const res = await fetch(url, {
        headers: { accept: 'application/dns-json' },
        signal: controller.signal,
      });
      if (!res.ok) {
        lastErr = `HTTP ${res.status}`;
        continue;
      }
      const json = await res.json();
      return (json.Answer || []).map((a) => ({ type: a.type, data: a.data.replace(/\.$/, '') }));
    } catch (err) {
      lastErr = err?.message || String(err);
    } finally {
      clearTimeout(timer);
    }
  }
  throw new Error(`DoH lookup failed for ${name}/${type}: ${lastErr}`);
}

// Follow CNAME chains to the terminal A/AAAA records, collecting the chain.
// DoH JSON answers carry a numeric record `type`; a single response can mix
// types (e.g. a CNAME followed by the A records it resolves to), so always
// filter by type (CNAME = 5, A = 1, AAAA = 28) rather than trusting Answer[0]
// — otherwise an IP could be mistaken for the next hostname. AAAA is queried
// when there are no A records: the FFC DNS standard is apex AAAA + www CNAME.
async function resolveHost(host) {
  const chain = [];
  let current = host;
  for (let hops = 0; hops < 6; hops++) {
    const cname = (await doh(current, 'CNAME')).find((r) => r.type === 5);
    if (cname) {
      chain.push({ from: current, cname: cname.data });
      current = cname.data;
      continue;
    }
    const a = await doh(current, 'A');
    let ips = a.filter((r) => r.type === 1).map((r) => r.data);
    if (!ips.length) {
      const aaaa = await doh(current, 'AAAA');
      ips = aaaa.filter((r) => r.type === 28).map((r) => r.data.toLowerCase());
    }
    return { chain, terminal: current, ips };
  }
  return { chain, terminal: current, ips: [] };
}

// HTTPS probe. redirect defaults to 'follow' (final content); pass
// redirect: 'manual' to observe a redirect instead of chasing it (used for
// the Pages origin, whose 301 to the configured custom domain is itself the
// signal being measured). finalUrl reports where a followed probe landed so
// content checks can say which URL they actually inspected.
async function probeHttps(url, { redirect = 'follow' } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
  try {
    const res = await fetch(url, {
      signal: controller.signal,
      redirect,
      headers: { 'User-Agent': 'ffc-cutover-preflight' },
    });
    const body = await res.text();
    clearTimeout(timer);
    return {
      status: res.status,
      server: res.headers.get('server') || '',
      location: res.headers.get('location') || '',
      finalUrl: res.url || url,
      body,
    };
  } catch (err) {
    clearTimeout(timer);
    return { error: err?.message || String(err) };
  }
}

// Resolve the ACTUAL FFC-EX repo name (case included) for a domain via the
// GitHub API. Repo lookups are case-insensitive server-side, so the exact GET
// with the lowercase guess normally returns the canonical mixed-case name;
// the org repo search is a fallback for edge cases. Returns { name } on
// success or { error } — callers then fall back to the lowercase
// naming-convention guess with a warning (e.g. unauthenticated + rate-limited).
async function githubJson(url, token) {
  const headers = {
    accept: 'application/vnd.github+json',
    'user-agent': 'ffc-cutover-preflight',
  };
  if (token) headers.authorization = `Bearer ${token}`;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 10000);
  try {
    const res = await fetch(url, { headers, signal: controller.signal });
    if (!res.ok) return { status: res.status };
    return { status: res.status, json: await res.json() };
  } catch (err) {
    return { error: err?.message || String(err) };
  } finally {
    clearTimeout(timer);
  }
}

async function resolveRepoName(domain, token = '') {
  const guess = `FFC-EX-${domain}`;
  const exact = await githubJson(
    `https://api.github.com/repos/FreeForCharity/${encodeURIComponent(guess)}`,
    token,
  );
  if (exact.json?.name) return { name: exact.json.name };
  if (exact.error) return { error: exact.error };
  if (exact.status === 404) {
    // Case-insensitive safety net: search the org for the repo by name.
    const q = encodeURIComponent(`org:FreeForCharity ${guess} in:name`);
    const search = await githubJson(`https://api.github.com/search/repositories?q=${q}`, token);
    const hit = (search.json?.items || []).find(
      (r) => typeof r?.name === 'string' && r.name.toLowerCase() === guess.toLowerCase(),
    );
    if (hit) return { name: hit.name };
    return { error: 'repo not found (HTTP 404, and no case-insensitive match in org search)' };
  }
  return { error: `HTTP ${exact.status}` };
}

// --------------------------------------------------------------------------
// Per-domain preflight.
// --------------------------------------------------------------------------

async function preflightDomain(domain, origin, marker, originWarning = '') {
  const results = [];
  function record(ok, name, detail = '') {
    results.push({ ok, name, detail });
    const mark = ok === true ? '✓' : ok === false ? '✗' : ok === 'warn' ? '⚠' : 'ℹ';
    console.log(detail ? `${mark} ${name} — ${detail}` : `${mark} ${name}`);
  }

  console.log(`\n=== ${domain} ===`);

  // 1. Confirm the GitHub Pages origin we're cutting TO is healthy first.
  // Probe WITHOUT following redirects: fleet-wide the FFC-EX repos configure a
  // Pages custom domain (cname = staging.<domain>), so the github.io origin
  // 301s away — following it would measure the wrong host and hide origin
  // breakage. A 301 to the repo's own configured Pages domain counts as
  // healthy; redirects are only followed for the explicit marker check below.
  console.log('— Source origin (cutting TO) —');
  if (originWarning) record('warn', 'Origin derivation', originWarning);
  const originProbe = await probeHttps(origin, { redirect: 'manual' });
  const originRes = originProbeOutcome({
    domain,
    status: originProbe.status,
    location: originProbe.location,
    error: originProbe.error,
  });
  let originHealthy = originRes.healthy;
  record(originRes.healthy, `Pages origin healthy (${origin})`, originRes.detail);
  if (originRes.healthy) {
    // Fetch the served export ONCE (following any redirect to the repo's
    // configured Pages domain) and reuse it for both the basePath artifact
    // check and the optional marker check; say exactly which URL was inspected.
    const content = originRes.redirectTarget
      ? await probeHttps(originRes.redirectTarget)
      : originProbe;
    const checkedUrl = content.finalUrl || originRes.redirectTarget || origin;

    // basePath artifact check: a build carrying the github.io project basePath
    // serves /FFC-EX-<repo>/… root-relative refs that 404 at the apex root the
    // moment the cutover lands (issue #748). This is blind to DNS/Pages
    // plumbing, so 121/120's dry-run never caught it — a hard blocker for 120.
    const bpRes = basePathOutcome({
      body: content.error ? '' : content.body,
      error: content.error,
    });
    record(bpRes.ok, `Export has no basePath refs (checked on ${checkedUrl})`, bpRes.detail);

    if (marker) {
      const present = !content.error && content.body.includes(marker);
      record(
        present,
        'Pages origin serves the new site',
        `marker "${marker}" ${present ? 'present' : 'MISSING'} (checked on ${checkedUrl})`,
      );
      originHealthy = originHealthy && present;
    }
  }

  // 2. Where does the apex resolve now, and is it Pages-ready?
  console.log('— Target domain DNS —');
  let apexClass = 'unresolved';
  try {
    const apex = await resolveHost(domain);
    const apexRes = apexOutcome(apex);
    apexClass = apexRes.cls;
    for (const c of apex.chain) record('info', `CNAME ${c.from} → ${c.cname}`);
    record('info', `${domain} A/AAAA → ${apex.ips.join(', ') || '(none)'}`);
    if (apexRes.ok === false) {
      // No A/AAAA at the apex — a go verdict here would be a false green.
      record(false, `${domain} apex resolves`, apexRes.detail);
    } else {
      record('info', `${domain} ${apexRes.detail}`);
    }
  } catch (err) {
    record(false, `${domain} resolves`, err.message);
  }
  const pointedAtPages = apexClass.startsWith('GitHub Pages');

  // www is part of the fleet DNS standard (www CNAME -> Pages). Pre-cutover
  // it stays informational; post-cutover (apex already on Pages) an unhealthy
  // www fails the verdict — the fleet promises www works after a cutover.
  let wwwClass = 'unresolved';
  let wwwHealthy = true;
  try {
    const www = await resolveHost(`www.${domain}`);
    wwwClass = classify(www);
    record('info', `www.${domain} served by: ${wwwClass}`);
    if (pointedAtPages && !wwwClass.startsWith('GitHub Pages')) {
      // Not CNAMEd to Pages — accept it only if it still serves the site.
      const wwwProbe = await probeHttps(`https://www.${domain}/`);
      const wwwServes =
        !wwwProbe.error && wwwProbe.status < 400 && (!marker || wwwProbe.body.includes(marker));
      wwwHealthy = wwwServes;
      record(
        wwwServes ? true : 'warn',
        `www.${domain} serves the site (required post-cutover)`,
        wwwProbe.error
          ? wwwProbe.error
          : `HTTP ${wwwProbe.status}${marker ? `, marker ${wwwProbe.body.includes(marker) ? 'present' : 'MISSING'}` : ''}`,
      );
    }
  } catch (err) {
    wwwClass = 'no record';
    if (pointedAtPages) {
      wwwHealthy = false;
      record('warn', `www.${domain} lookup failed (required post-cutover)`, err.message);
    } else {
      record('info', `www.${domain} — no record (informational pre-cutover)`);
    }
  }

  // 3. CAA — will Let's Encrypt (GitHub Pages' CA) be allowed to issue?
  console.log('— Certificate authority (CAA) —');
  let caaLookup;
  try {
    caaLookup = { records: caaRecordsFromAnswers(await doh(domain, 'CAA')) };
  } catch (err) {
    caaLookup = { error: err.message };
  }
  const caaRes = caaOutcome(caaLookup);
  const caaOk = caaRes.caaOk;
  record(caaRes.ok, caaRes.name, caaRes.detail);

  // 4. If DNS already points at Pages, verify the live domain over HTTPS.
  console.log('— Live domain over HTTPS —');
  const live = await probeHttps(`https://${domain}/`);
  let liveStatus = '';
  if (live.error) {
    liveStatus = 'unreachable';
    record(!pointedAtPages ? 'info' : false, `https://${domain}/ reachable`, live.error);
  } else {
    liveStatus = `HTTP ${live.status}`;
    // Pre-cutover the old host's status is informational whatever it is — a
    // parked or suspended old host (>= 400) is often the very reason for the
    // cutover, so it must not block a READY verdict (consistent with the
    // unreachable case above). Post-cutover a failing status is a blocker.
    record(
      pointedAtPages ? live.status < 400 : 'info',
      `https://${domain}/ responds`,
      `HTTP ${live.status}${live.server ? ` (server: ${live.server})` : ''}${
        pointedAtPages ? '' : ' — old host, informational pre-cutover'
      }`,
    );
    if (marker) {
      const servesNew = live.body.includes(marker);
      if (pointedAtPages) {
        record(
          servesNew,
          'Custom domain serves the new site',
          servesNew ? 'marker present' : 'still the OLD site / stale cache',
        );
      } else {
        record(
          'info',
          'Custom domain content',
          servesNew ? 'new site' : 'old host (expected pre-cutover)',
        );
      }
    }
  }

  const blockerCount = results.filter((r) => r.ok === false).length;
  const verdict = computeVerdict({ originHealthy, pointedAtPages, blockerCount, wwwHealthy });
  console.log(`Verdict: ${verdict.label}`);

  return {
    domain,
    origin,
    originHealthy,
    originRedirect: originRes.redirectTarget || '',
    apexClass,
    wwwClass,
    wwwHealthy,
    caaOk,
    liveStatus,
    blockerCount,
    verdict,
  };
}

// --------------------------------------------------------------------------
// Main: fan out over domains, print + publish the verdict table.
// --------------------------------------------------------------------------

function renderTable(rows) {
  const header = [
    '| Domain | Pages origin | Apex serves | www serves | CAA (LE) | HTTPS | Verdict |',
    '| --- | --- | --- | --- | --- | --- | --- |',
  ];
  const body = rows.map(
    (r) =>
      `| ${[
        r.domain,
        r.originHealthy
          ? `healthy${r.originRedirect ? ` (→ ${r.originRedirect})` : ''}`
          : 'UNHEALTHY',
        r.apexClass,
        r.wwwHealthy ? r.wwwClass : `${r.wwwClass} (UNHEALTHY)`,
        r.caaOk ? 'ok' : 'BLOCKED',
        r.liveStatus || '?',
        `${r.verdict.ok ? '✅' : '❌'} ${r.verdict.label}`,
      ].join(' | ')} |`,
  );
  return [...header, ...body].join('\n');
}

async function main() {
  if (process.argv.includes('--self-test')) {
    await selfTest();
    return;
  }

  const domains = parseDomains(arg('domains', arg('domain', '')));
  if (!domains.length) {
    console.error(
      'Usage: node scripts/preflight-cutover.mjs --domains=<a.org[,b.org,...]> [--origin=<url>] [--marker=<text>]',
    );
    process.exit(2);
  }
  const originOverride = arg('origin', '');
  if (originOverride && domains.length > 1) {
    console.error('--origin is only valid with a single domain (defaults are derived per-domain).');
    process.exit(2);
  }
  const marker = arg('marker', '');
  const token = process.env.GITHUB_TOKEN || process.env.GH_TOKEN || '';

  console.log(`Fleet cutover preflight — ${domains.length} domain(s)`);

  const rows = [];
  for (const domain of domains) {
    let origin = originOverride;
    let originWarning = '';
    if (!origin) {
      const repo = await resolveRepoName(domain, token);
      if (repo.name) {
        origin = originForRepo(repo.name);
      } else {
        origin = defaultOrigin(domain);
        originWarning =
          `could not resolve the FFC-EX repo name via the GitHub API (${repo.error}); ` +
          `falling back to the lowercase naming-convention guess ${origin} — ` +
          `mixed-case repos (e.g. FFC-EX-SRRN.net) may probe the wrong path. ` +
          `Set GITHUB_TOKEN to authenticate the lookup.`;
      }
    }
    rows.push(await preflightDomain(domain, origin, marker, originWarning));
  }

  const table = renderTable(rows);
  console.log(`\n— Fleet verdict —\n${table}`);

  if (process.env.GITHUB_STEP_SUMMARY) {
    const { appendFileSync } = await import('node:fs');
    appendFileSync(
      process.env.GITHUB_STEP_SUMMARY,
      `## Fleet cutover preflight\n\n${table}\n\nRead-only check — nothing was changed. Pair with workflow 120 for the actual cutover.\n`,
    );
  }

  const failing = rows.filter((r) => !r.verdict.ok);
  if (failing.length) {
    console.log(
      `\n✗ ${failing.length} of ${rows.length} domain(s) NOT ready: ${failing.map((r) => r.domain).join(', ')}`,
    );
    process.exit(1);
  }
  console.log(`\n✓ All ${rows.length} domain(s) are go.`);
}

// Only run when executed as the entrypoint — the module stays importable by
// tests (Pester drives the exported pure functions directly).
const isEntrypoint = (() => {
  try {
    return import.meta.url === pathToFileURL(process.argv[1] ?? '').href;
  } catch {
    return false;
  }
})();

if (isEntrypoint) {
  main().catch((err) => {
    console.error('\npreflight crashed:', err?.stack || err);
    process.exit(2);
  });
}
