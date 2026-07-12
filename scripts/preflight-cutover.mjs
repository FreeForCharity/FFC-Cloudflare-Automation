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
 *   - Once DNS is changed: is the custom domain live over HTTPS?
 *
 * Usage:
 *   node scripts/preflight-cutover.mjs --domains=example.org
 *   node scripts/preflight-cutover.mjs --domains=a.org,b.org,c.com
 *   node scripts/preflight-cutover.mjs --domains=example.org \
 *        --origin=https://freeforcharity.github.io/FFC-EX-example.org/ \
 *        --marker="Example Charity"
 *   node scripts/preflight-cutover.mjs --self-test
 *
 * When --origin is omitted it is derived from the FFC-EX repo naming
 * convention: https://freeforcharity.github.io/FFC-EX-<domain>/ (an explicit
 * --origin is only accepted in single-domain mode). --marker is an optional
 * content substring to require in the served HTML; when omitted, content
 * checks are skipped and the verdict rests on DNS + HTTP status alone.
 *
 * Writes a markdown verdict table to $GITHUB_STEP_SUMMARY when set.
 *
 * Exit codes:
 *   0  every domain is ready to cut over (or already cut over and healthy)
 *   1  one or more domains have blockers
 *   2  invalid usage / self-test failure / crash
 */

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

// Cloudflare proxies return addresses in these ranges (104.16.0.0/13 &
// 172.64.0.0/13 are the common ones); enough to *label* the current host.
// /13 = second octet 16-23 and 64-71 respectively — kept tight so the label
// can't over-claim neighbouring space (e.g. 104.24+ or 172.72+ are NOT these).
export function isCloudflare(ip) {
  return /^104\.(1[6-9]|2[0-3])\./.test(ip) || /^172\.(6[4-9]|7[01])\./.test(ip);
}

export function classify(host) {
  if (host.ips.some((ip) => GH_PAGES_IPV4.has(ip))) return 'GitHub Pages';
  if (host.chain.some((c) => /\.github\.io$/i.test(c.cname))) return 'GitHub Pages (CNAME)';
  if (host.ips.some(isCloudflare)) return 'Cloudflare (proxied)';
  if (host.ips.length) return `other (${host.ips.join(', ')})`;
  return 'unresolved';
}

// CAA answers -> may Let's Encrypt (GitHub Pages' CA) issue?
export function caaAllowsLetsEncrypt(caaRecords) {
  if (!caaRecords.length) return true; // no CAA = any CA may issue
  return caaRecords.some((d) => /letsencrypt\.org/i.test(d));
}

// Derive the default GitHub Pages origin from the FFC-EX repo naming convention.
export function defaultOrigin(domain) {
  return `https://freeforcharity.github.io/FFC-EX-${domain}/`;
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
 * The go/no-go verdict for one domain.
 *   originHealthy  the GitHub Pages origin returns 200 (and the marker, if any)
 *   pointedAtPages apex classification starts with "GitHub Pages"
 *   blockerCount   number of failed (ok === false) checks recorded
 * Returns { code, ok, label }: READY / CUTOVER_COMPLETE are ok; NOT_READY is not.
 */
export function computeVerdict({ originHealthy, pointedAtPages, blockerCount }) {
  if (!originHealthy) {
    return { code: 'NOT_READY_ORIGIN', ok: false, label: 'NOT READY — Pages origin unhealthy' };
  }
  if (pointedAtPages && blockerCount === 0) {
    return { code: 'CUTOVER_COMPLETE', ok: true, label: 'CUTOVER COMPLETE — live on Pages' };
  }
  if (!pointedAtPages) {
    return blockerCount === 0
      ? { code: 'READY', ok: true, label: 'READY TO CUT OVER' }
      : { code: 'NOT_READY', ok: false, label: `NOT READY — ${blockerCount} blocker(s)` };
  }
  return { code: 'NOT_READY', ok: false, label: `NOT READY — ${blockerCount} blocker(s)` };
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
  assert.equal(classify({ chain: [], ips: ['104.18.3.2'] }), 'Cloudflare (proxied)');
  assert.equal(classify({ chain: [], ips: ['203.0.113.7'] }), 'other (203.0.113.7)');
  assert.equal(classify({ chain: [], ips: [] }), 'unresolved');

  // caaAllowsLetsEncrypt: empty = permissive; must include letsencrypt.org.
  assert.equal(caaAllowsLetsEncrypt([]), true);
  assert.equal(caaAllowsLetsEncrypt(['0 issue "letsencrypt.org"']), true);
  assert.equal(caaAllowsLetsEncrypt(['0 issue "digicert.com"']), false);
  assert.equal(caaAllowsLetsEncrypt(['0 issue "digicert.com"', '0 issue "letsencrypt.org"']), true);

  // defaultOrigin: FFC-EX naming convention.
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

  // computeVerdict: the four outcomes.
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

  console.log('self-test OK — classification + verdict logic passed');
}

// --------------------------------------------------------------------------
// Network probes (DoH + HTTPS).
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

// Follow CNAME chains to the terminal A records, collecting the chain.
// DoH JSON answers carry a numeric record `type`; a single response can mix
// types (e.g. a CNAME followed by the A records it resolves to), so always
// filter by type (CNAME = 5, A = 1) rather than trusting Answer[0] — otherwise
// an IP could be mistaken for the next hostname.
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
    return { chain, terminal: current, ips: a.filter((r) => r.type === 1).map((r) => r.data) };
  }
  return { chain, terminal: current, ips: [] };
}

async function probeHttps(url) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
  try {
    const res = await fetch(url, {
      signal: controller.signal,
      redirect: 'follow',
      headers: { 'User-Agent': 'ffc-cutover-preflight' },
    });
    const body = await res.text();
    clearTimeout(timer);
    return { status: res.status, server: res.headers.get('server') || '', body };
  } catch (err) {
    clearTimeout(timer);
    return { error: err?.message || String(err) };
  }
}

// --------------------------------------------------------------------------
// Per-domain preflight.
// --------------------------------------------------------------------------

async function preflightDomain(domain, origin, marker) {
  const results = [];
  function record(ok, name, detail = '') {
    results.push({ ok, name, detail });
    const mark = ok === true ? '✓' : ok === false ? '✗' : 'ℹ';
    console.log(detail ? `${mark} ${name} — ${detail}` : `${mark} ${name}`);
  }

  console.log(`\n=== ${domain} ===`);

  // 1. Confirm the GitHub Pages origin we're cutting TO is healthy first.
  console.log('— Source origin (cutting TO) —');
  const originProbe = await probeHttps(origin);
  let originHealthy = false;
  if (originProbe.error) {
    record(false, `Pages origin reachable (${origin})`, originProbe.error);
  } else {
    record(
      originProbe.status === 200,
      `Pages origin returns 200 (${origin})`,
      `HTTP ${originProbe.status}`,
    );
    originHealthy = originProbe.status === 200;
    if (marker) {
      const present = originProbe.body.includes(marker);
      record(
        present,
        'Pages origin serves the new site',
        `marker "${marker}" ${present ? 'present' : 'MISSING'}`,
      );
      originHealthy = originHealthy && present;
    }
  }

  // 2. Where does the apex resolve now, and is it Pages-ready?
  console.log('— Target domain DNS —');
  let apexClass = 'unresolved';
  try {
    const apex = await resolveHost(domain);
    apexClass = classify(apex);
    for (const c of apex.chain) record('info', `CNAME ${c.from} → ${c.cname}`);
    record('info', `${domain} A → ${apex.ips.join(', ') || '(none)'}`);
    record('info', `${domain} currently served by: ${apexClass}`);
  } catch (err) {
    record(false, `${domain} resolves`, err.message);
  }

  let wwwClass = 'unresolved';
  try {
    const www = await resolveHost(`www.${domain}`);
    wwwClass = classify(www);
    record('info', `www.${domain} served by: ${wwwClass}`);
  } catch {
    wwwClass = 'no record';
    record('info', `www.${domain} — no record`);
  }

  // 3. CAA — will Let's Encrypt (GitHub Pages' CA) be allowed to issue?
  console.log('— Certificate authority (CAA) —');
  let caaOk = null;
  try {
    const caa = await doh(domain, 'CAA');
    const issuers = caa.map((r) => r.data);
    caaOk = caaAllowsLetsEncrypt(issuers);
    if (!issuers.length) {
      record(true, 'CAA policy', "none set — any CA may issue (Let's Encrypt OK)");
    } else {
      record(caaOk, "CAA allows Let's Encrypt (GitHub Pages HTTPS)", issuers.join(' | '));
    }
  } catch (err) {
    record('info', 'CAA lookup', err.message);
  }

  // 4. If DNS already points at Pages, verify the live domain over HTTPS.
  console.log('— Live domain over HTTPS —');
  const pointedAtPages = apexClass.startsWith('GitHub Pages');
  const live = await probeHttps(`https://${domain}/`);
  let liveStatus = '';
  if (live.error) {
    liveStatus = 'unreachable';
    record(!pointedAtPages ? 'info' : false, `https://${domain}/ reachable`, live.error);
  } else {
    liveStatus = `HTTP ${live.status}`;
    record(
      live.status < 400,
      `https://${domain}/ responds`,
      `HTTP ${live.status}${live.server ? ` (server: ${live.server})` : ''}`,
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
  const verdict = computeVerdict({ originHealthy, pointedAtPages, blockerCount });
  console.log(`Verdict: ${verdict.label}`);

  return {
    domain,
    origin,
    originHealthy,
    apexClass,
    wwwClass,
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
        r.originHealthy ? 'healthy' : 'UNHEALTHY',
        r.apexClass,
        r.wwwClass,
        r.caaOk === null ? '?' : r.caaOk ? 'ok' : 'BLOCKED',
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

  console.log(`Fleet cutover preflight — ${domains.length} domain(s)`);

  const rows = [];
  for (const domain of domains) {
    rows.push(await preflightDomain(domain, originOverride || defaultOrigin(domain), marker));
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

main().catch((err) => {
  console.error('\npreflight crashed:', err?.stack || err);
  process.exit(2);
});
