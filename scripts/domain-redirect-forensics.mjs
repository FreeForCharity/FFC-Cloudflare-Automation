#!/usr/bin/env node
/**
 * Domain redirect forensics — answers "why is this domain redirecting, and who
 * configured it?" for a domain FFC does not necessarily control end to end.
 *
 * WHY THIS EXISTS. On 2026-09-04 viewpointministriesinternational.org was found
 * forwarding to vpmin.org before the replacement site existed, taking a live
 * charity site off the air. Establishing WHERE that redirect lived took several
 * hours of guessing, because the two tools that could have answered it cannot:
 *
 *   - Workflow 121 follows redirects and reports only the FINAL status, so a
 *     `301 -> other-domain -> 200` is indistinguishable from a direct `200`.
 *     It reported "HTTP 200" for a domain that was not serving its own site.
 *   - Workflow 101 writes its Cloudflare audit to an ARTIFACT. An agent session
 *     restricted to api.github.com cannot download an Actions artifact — the
 *     blob storage is a different host — so the one report naming the zone's
 *     rules was unreadable by the only party trying to read it.
 *
 * So this tool has two hard design rules, and both are load-bearing:
 *
 *   1. NEVER follow redirects silently. Every hop is walked manually and
 *      printed — status, Location, and the headers that identify the responder.
 *      A redirect chain that is summarized into its endpoint has destroyed the
 *      evidence the question needed.
 *   2. EVERYTHING goes to stdout, which becomes the job log. No artifacts, no
 *      step-summary-only output. The job log is readable through the REST API
 *      by a caller with no general network egress; an artifact is not.
 *
 * It is strictly read-only: GETs against the live domain and the Cloudflare API,
 * nothing else. It loads no credentials of its own — the Cloudflare half is
 * skipped with a printed notice when no token is present, so the HTTP half still
 * runs anywhere.
 *
 * Usage:
 *   node scripts/domain-redirect-forensics.mjs --domains=example.org
 *   node scripts/domain-redirect-forensics.mjs --domains=a.org,b.org
 *   node scripts/domain-redirect-forensics.mjs --domains=a.org --skip-cloudflare
 *   node scripts/domain-redirect-forensics.mjs --self-test
 *
 * Cloudflare tokens are read from CLOUDFLARE_API_TOKEN_FFC and
 * CLOUDFLARE_API_TOKEN_CM (either or both). Both accounts are searched, because
 * an FFC domain can legitimately live in either and "not in FFC" is a different
 * finding from "not in Cloudflare at all".
 */

const CF_API = 'https://api.cloudflare.com/client/v4';
const MAX_HOPS = 10;
const HOP_TIMEOUT_MS = 15000;

// ---------------------------------------------------------------------------
// Pure helpers (covered by --self-test)
// ---------------------------------------------------------------------------

/**
 * Identify who answered a request from its response headers.
 *
 * Deliberately reports the EDGE separately from the ORIGIN where it can: a
 * Cloudflare-proxied hostname always answers `server: cloudflare`, which says
 * nothing about what is behind it. Callers must not read "Cloudflare" as "the
 * redirect was configured in Cloudflare" — it is exactly as consistent with an
 * origin redirect passing through the proxy. That ambiguity is the reason the
 * Cloudflare-rules half of this tool exists.
 */
export function identifyResponder(headers) {
  const get = (k) => (headers && (headers[k] ?? headers[k.toLowerCase()])) || '';
  const server = String(get('server')).toLowerCase();
  const via = String(get('via')).toLowerCase();
  const powered = String(get('x-powered-by')).toLowerCase();
  const hostedBy = String(get('x-hosted-by')).toLowerCase();
  const signals = [];

  if (server.includes('cloudflare')) signals.push('Cloudflare edge');
  if (get('x-github-request-id') || server.includes('github')) signals.push('GitHub Pages');
  if (
    hostedBy.includes('wordpress') ||
    powered.includes('wordpress') ||
    via.includes('wordpress')
  ) {
    signals.push('WordPress.com');
  }
  if (server.includes('litespeed')) signals.push('LiteSpeed (cPanel/shared host)');
  if (server.includes('apache')) signals.push('Apache (cPanel/shared host)');
  if (server.includes('nginx')) signals.push('nginx');

  return signals.length ? signals.join(' + ') : server || '(no server header)';
}

/**
 * Reduce a walked chain to a verdict.
 *
 * `redirects-offsite` is called out separately from `redirects` because it is
 * the shape that takes a site off the air: the domain you asked about is
 * answering for a DIFFERENT registrable domain. A same-domain http->https or
 * apex->www hop is routine and must not read as an incident.
 */
export function classifyChain(hops, startHost) {
  if (!hops.length) return { verdict: 'no-response', detail: 'nothing answered' };
  const err = hops.find((h) => h.error);
  if (err) return { verdict: 'error', detail: err.error };

  const seen = new Set();
  for (const hop of hops) {
    if (seen.has(hop.url)) return { verdict: 'loop', detail: `revisits ${hop.url}` };
    seen.add(hop.url);
  }

  const last = hops[hops.length - 1];
  if (hops.length === 1 && !last.location) {
    return { verdict: 'no-redirect', detail: `HTTP ${last.status}` };
  }
  if (last.location) {
    return { verdict: 'truncated', detail: `still redirecting after ${MAX_HOPS} hops` };
  }

  const finalHost = safeHost(last.url);
  const offsite = registrableDomain(finalHost) !== registrableDomain(startHost);
  return {
    verdict: offsite ? 'redirects-offsite' : 'redirects-onsite',
    detail: `${hops.length - 1} hop(s) -> ${last.url} (HTTP ${last.status})`,
  };
}

/** Naive eTLD+1. Sufficient here: FFC domains are all single-label public suffixes. */
export function registrableDomain(host) {
  const parts = String(host || '')
    .toLowerCase()
    .replace(/\.$/, '')
    .split('.')
    .filter(Boolean);
  if (parts.length <= 2) return parts.join('.');
  return parts.slice(-2).join('.');
}

function safeHost(url) {
  try {
    return new URL(url).host;
  } catch {
    return '';
  }
}

/**
 * Render one Cloudflare dynamic-redirect rule as a single readable line.
 * Kept pure so the formatting is testable without an API.
 */
export function describeRedirectRule(rule) {
  const target =
    rule?.action_parameters?.from_value?.target_url?.value ??
    rule?.action_parameters?.from_value?.target_url?.expression ??
    '(no target)';
  const status = rule?.action_parameters?.from_value?.status_code ?? '(no status)';
  const enabled = rule?.enabled === false ? 'DISABLED' : 'enabled';
  return `[${enabled}] ${status} when ${rule?.expression ?? '(no expression)'} -> ${target}`;
}

/**
 * Extract a legacy Page Rule's forwarding target, or null when the rule does
 * not forward. Page Rules are the single most common place a "why is this
 * domain redirecting" answer hides, because they predate Rulesets and are shown
 * in a different part of the dashboard.
 */
export function describePageRuleForwarding(pageRule) {
  const fwd = (pageRule?.actions || []).find((a) => a.id === 'forwarding_url');
  if (!fwd) return null;
  const targets = (pageRule?.targets || [])
    .map((t) => t?.constraint?.value)
    .filter(Boolean)
    .join(', ');
  const status = fwd?.value?.status_code ?? '(no status)';
  const to = fwd?.value?.url ?? '(no url)';
  const enabled = pageRule?.status === 'active' ? 'active' : String(pageRule?.status || 'unknown');
  return `[${enabled}] ${status} ${targets || '(no target)'} -> ${to}`;
}

// ---------------------------------------------------------------------------
// Live probes
// ---------------------------------------------------------------------------

async function walkChain(startUrl) {
  const hops = [];
  let url = startUrl;

  for (let i = 0; i < MAX_HOPS; i++) {
    const ac = new AbortController();
    const timer = setTimeout(() => ac.abort(), HOP_TIMEOUT_MS);
    try {
      // redirect: 'manual' is the whole point — see design rule 1 above.
      const res = await fetch(url, {
        redirect: 'manual',
        signal: ac.signal,
        headers: { 'user-agent': 'ffc-redirect-forensics/1.0' },
      });
      const headers = Object.fromEntries(res.headers.entries());
      const location = res.headers.get('location');
      hops.push({
        url,
        status: res.status,
        location,
        responder: identifyResponder(headers),
        headers,
      });
      if (!location) return hops;
      url = new URL(location, url).toString();
    } catch (e) {
      hops.push({
        url,
        error:
          e?.name === 'AbortError' ? `timeout after ${HOP_TIMEOUT_MS}ms` : String(e?.message || e),
      });
      return hops;
    } finally {
      clearTimeout(timer);
    }
  }
  return hops;
}

function printChain(label, hops, startHost) {
  const cls = classifyChain(hops, startHost);
  console.log(`  ${label}`);
  for (const [i, hop] of hops.entries()) {
    if (hop.error) {
      console.log(`    ${i + 1}. ${hop.url}  ERROR: ${hop.error}`);
      continue;
    }
    console.log(`    ${i + 1}. ${hop.url}`);
    console.log(`       HTTP ${hop.status}   responder: ${hop.responder}`);
    if (hop.location) console.log(`       location: ${hop.location}`);
    // cf-ray proves the response came through Cloudflare even when a later hop
    // rewrites `server`; cf-cache-status distinguishes an edge-cached redirect
    // (which survives an origin fix until purged) from a live one.
    for (const h of ['cf-ray', 'cf-cache-status', 'x-github-request-id']) {
      if (hop.headers?.[h]) console.log(`       ${h}: ${hop.headers[h]}`);
    }
  }
  const flag = cls.verdict === 'redirects-offsite' ? '  <== OFFSITE REDIRECT' : '';
  console.log(`    => ${cls.verdict}: ${cls.detail}${flag}`);
  return cls;
}

// ---------------------------------------------------------------------------
// Cloudflare reads (all GET)
// ---------------------------------------------------------------------------

async function cfGet(token, path) {
  const res = await fetch(`${CF_API}${path}`, { headers: { authorization: `Bearer ${token}` } });
  const body = await res.json().catch(() => ({}));
  if (!res.ok || body?.success === false) {
    const msg = (body?.errors || []).map((e) => e.message).join('; ') || `HTTP ${res.status}`;
    throw new Error(msg);
  }
  return body.result;
}

async function inspectZone(domain, accounts) {
  for (const { label, token } of accounts) {
    let zones;
    try {
      zones = await cfGet(token, `/zones?name=${encodeURIComponent(domain)}`);
    } catch (e) {
      console.log(`  [${label}] zone lookup failed: ${e.message}`);
      continue;
    }
    if (!zones?.length) {
      console.log(`  [${label}] no zone for ${domain}`);
      continue;
    }

    const zone = zones[0];
    console.log(`  [${label}] ZONE FOUND  id=${zone.id}  status=${zone.status}`);
    console.log(`  [${label}] name servers: ${(zone.name_servers || []).join(', ') || '(none)'}`);
    if (zone.original_name_servers?.length) {
      console.log(`  [${label}] original NS: ${zone.original_name_servers.join(', ')}`);
    }

    // --- DNS records: where does the traffic actually go? ---
    try {
      const recs = await cfGet(token, `/zones/${zone.id}/dns_records?per_page=200`);
      const interesting = recs.filter((r) => ['A', 'AAAA', 'CNAME'].includes(r.type));
      console.log(`  [${label}] DNS (${interesting.length} A/AAAA/CNAME of ${recs.length} total):`);
      for (const r of interesting) {
        console.log(`      ${r.type.padEnd(5)} ${r.name}  ->  ${r.content}   proxied=${r.proxied}`);
      }
    } catch (e) {
      console.log(`  [${label}] DNS read failed: ${e.message}`);
    }

    // --- Dynamic redirect rules (modern Rulesets) ---
    try {
      const rulesets = await cfGet(token, `/zones/${zone.id}/rulesets`);
      const redirectPhases = rulesets.filter((rs) => String(rs.phase || '').includes('redirect'));
      if (!redirectPhases.length) {
        console.log(`  [${label}] redirect rulesets: none`);
      }
      for (const rs of redirectPhases) {
        const full = await cfGet(token, `/zones/${zone.id}/rulesets/${rs.id}`);
        const rules = full?.rules || [];
        console.log(
          `  [${label}] ruleset "${rs.name || rs.id}" phase=${rs.phase} (${rules.length} rule(s)):`,
        );
        if (!rules.length) console.log('      (empty)');
        for (const rule of rules) console.log(`      ${describeRedirectRule(rule)}`);
      }
    } catch (e) {
      console.log(`  [${label}] ruleset read failed: ${e.message}`);
    }

    // --- Legacy Page Rules: the usual hiding place ---
    try {
      const pageRules = await cfGet(token, `/zones/${zone.id}/pagerules`);
      const forwarding = pageRules.map(describePageRuleForwarding).filter(Boolean);
      console.log(
        `  [${label}] page rules: ${pageRules.length} total, ${forwarding.length} forwarding`,
      );
      for (const line of forwarding) console.log(`      ${line}`);
    } catch (e) {
      console.log(`  [${label}] page rule read failed: ${e.message}`);
    }

    return true;
  }
  return false;
}

// ---------------------------------------------------------------------------
// Self-test (pure logic only — no network)
// ---------------------------------------------------------------------------

function selfTest() {
  let pass = 0;
  const fail = [];
  const check = (name, cond) => (cond ? pass++ : fail.push(name));

  check(
    'cloudflare edge identified',
    identifyResponder({ server: 'cloudflare' }).includes('Cloudflare'),
  );
  check(
    'github pages identified',
    identifyResponder({ 'x-github-request-id': 'abc' }).includes('GitHub Pages'),
  );
  check('cpanel host identified', identifyResponder({ server: 'LiteSpeed' }).includes('LiteSpeed'));
  check('missing server header is not a crash', identifyResponder({}) === '(no server header)');

  check('registrable domain of www', registrableDomain('www.example.org') === 'example.org');
  check('registrable domain of apex', registrableDomain('example.org') === 'example.org');

  const direct = [{ url: 'https://a.org/', status: 200, location: null }];
  check('direct 200 is no-redirect', classifyChain(direct, 'a.org').verdict === 'no-redirect');

  const onsite = [
    { url: 'http://a.org/', status: 301, location: 'https://a.org/' },
    { url: 'https://a.org/', status: 200, location: null },
  ];
  check('http->https is onsite', classifyChain(onsite, 'a.org').verdict === 'redirects-onsite');

  const offsite = [
    { url: 'https://a.org/', status: 301, location: 'https://b.org/' },
    { url: 'https://b.org/', status: 200, location: null },
  ];
  check('cross-domain is offsite', classifyChain(offsite, 'a.org').verdict === 'redirects-offsite');

  const loop = [
    { url: 'https://a.org/', status: 301, location: 'https://a.org/x' },
    { url: 'https://a.org/x', status: 301, location: 'https://a.org/' },
    { url: 'https://a.org/', status: 301, location: 'https://a.org/x' },
  ];
  check('loop detected', classifyChain(loop, 'a.org').verdict === 'loop');

  check(
    'error surfaces',
    classifyChain([{ url: 'https://a.org/', error: 'timeout' }], 'a.org').verdict === 'error',
  );
  check('empty chain is no-response', classifyChain([], 'a.org').verdict === 'no-response');

  const rule = {
    enabled: true,
    expression: '(http.host eq "a.org")',
    action_parameters: { from_value: { status_code: 301, target_url: { value: 'https://b.org' } } },
  };
  check(
    'redirect rule rendered',
    describeRedirectRule(rule).includes('301') &&
      describeRedirectRule(rule).includes('https://b.org'),
  );

  const pr = {
    status: 'active',
    targets: [{ constraint: { value: 'a.org/*' } }],
    actions: [{ id: 'forwarding_url', value: { status_code: 301, url: 'https://b.org/$1' } }],
  };
  check(
    'page rule forwarding rendered',
    describePageRuleForwarding(pr).includes('https://b.org/$1'),
  );
  check(
    'non-forwarding page rule ignored',
    describePageRuleForwarding({ actions: [{ id: 'cache_level' }] }) === null,
  );

  console.log(`self-test: ${pass} passed, ${fail.length} failed`);
  for (const f of fail) console.log(`  FAIL: ${f}`);
  return fail.length === 0;
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

function arg(name, fallback = '') {
  const hit = process.argv.find((a) => a.startsWith(`--${name}=`));
  return hit ? hit.slice(name.length + 3) : fallback;
}

async function main() {
  if (process.argv.includes('--self-test')) {
    process.exit(selfTest() ? 0 : 1);
  }

  const domains = arg('domains')
    .split(',')
    .map((d) =>
      d
        .trim()
        .replace(/^https?:\/\//, '')
        .replace(/\/.*$/, ''),
    )
    .filter(Boolean);

  if (!domains.length) {
    console.error('::error::--domains is required (comma-separated bare apex domains)');
    process.exit(1);
  }

  const skipCf = process.argv.includes('--skip-cloudflare');
  const accounts = [
    { label: 'FFC', token: process.env.CLOUDFLARE_API_TOKEN_FFC },
    { label: 'CM', token: process.env.CLOUDFLARE_API_TOKEN_CM },
  ].filter((a) => a.token);

  console.log(`Domain redirect forensics — ${domains.length} domain(s)`);
  console.log('Read-only. Redirects are walked hop by hop and never followed silently.');
  console.log('');

  const verdicts = [];

  for (const domain of domains) {
    console.log(`=== ${domain} ===`);
    console.log('— HTTP redirect chains —');
    for (const host of [domain, `www.${domain}`]) {
      for (const scheme of ['https', 'http']) {
        const hops = await walkChain(`${scheme}://${host}/`);
        const cls = printChain(`${scheme}://${host}/`, hops, host);
        verdicts.push({ domain, probe: `${scheme}://${host}/`, ...cls });
      }
    }

    console.log('— Cloudflare configuration —');
    if (skipCf) {
      console.log('  (skipped: --skip-cloudflare)');
    } else if (!accounts.length) {
      console.log(
        '  (skipped: no CLOUDFLARE_API_TOKEN_FFC / CLOUDFLARE_API_TOKEN_CM in the environment)',
      );
    } else {
      const found = await inspectZone(domain, accounts);
      if (!found) {
        console.log(`  ${domain} is in NEITHER FFC nor CM Cloudflare.`);
        console.log('  If the live probes above show a Cloudflare edge, the zone is in a');
        console.log('  THIRD-PARTY Cloudflare account and FFC cannot read or change its rules.');
      }
    }
    console.log('');
  }

  console.log('— Summary —');
  console.log('| Probe | Verdict | Detail |');
  console.log('| --- | --- | --- |');
  for (const v of verdicts) console.log(`| ${v.probe} | ${v.verdict} | ${v.detail} |`);

  const offsite = verdicts.filter((v) => v.verdict === 'redirects-offsite');
  if (offsite.length) {
    console.log('');
    console.log(
      `::warning::${offsite.length} probe(s) redirect off-site: ${offsite.map((v) => v.probe).join(', ')}`,
    );
  }
  // Always exit 0: this is a reporting tool, and an off-site redirect is
  // sometimes the intended end state. A non-zero exit here would make the
  // caller's shell treat "I found the answer you asked for" as a failure.
}

const invokedDirectly =
  process.argv[1] && import.meta.url.endsWith(process.argv[1].split('/').pop());
if (invokedDirectly) {
  main().catch((e) => {
    console.error(`::error::${e?.stack || e}`);
    process.exit(1);
  });
}
