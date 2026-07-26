'use strict';

// Pure parsing + classification logic for workflow 743 (Fleet Security Header
// Audit). The workflow's github-script step `require`s this module, so testing
// these functions (tests/workflow-logic/test_743_security_headers.py) tests the
// exact logic that ships. I/O — reading the sites list, curling each domain,
// upserting the rolling issue — stays in the workflow; only the decisions (which
// domains are in scope, which headers count as served) and the issue body live
// here.
//
// Background (#884): every FFC template ships `public/_headers`, and the file is
// inert on this stack. `_headers` is a Cloudflare Pages / Netlify BUILD feature;
// these sites are a GitHub Pages origin behind a Cloudflare proxy, and nothing
// in that path reads it. The convergence plan was about to copy the file to the
// remaining sites and mark the gap closed — which would have produced a fleet of
// green checklists and zero security headers.
//
// The rule this module encodes: assert on the RESPONSE, never on the presence of
// a source file. A source-file audit cannot tell the difference between a header
// that is served and one that is merely written down.

const SITES_LIST_PATH = 'sites-list/sites_list.json';
const MARKER = '<!-- fleet-security-header-audit -->';
const ISSUE_TITLE = 'Fleet security headers: which FFC sites actually serve them';
const ISSUE_LABELS = ['security', 'priority: high', 'agentic-os'];

// Scope = every domain whose zone FFC controls in Cloudflare. That is exactly
// the population the recommended remediation reaches: a response-header
// Transform Rule is configured per ZONE and applies to whatever that zone
// serves, regardless of where the origin lives.
//
// The gate is `Is In Cloudflare`, which 703 derives from the live Cloudflare
// zone export — NOT the curated `Host Category`, and that distinction is
// load-bearing. An earlier draft of this file selected on `Host Category` and
// silently dropped freeforcharity.org, the one site already serving the full
// header set, because its curated row still reads `InterServer cPanel` and
// `Status: Transferred Away`. A curated column that has gone stale (see #886)
// must never be able to shrink an audit's scope without saying so: a domain
// that is never probed reports as no finding, which reads identically to a
// domain that passed.
//
// `Status` is deliberately not a gate either — it describes registration and
// billing, not what the zone serves. A domain that does not answer lands in the
// `unmeasured` bucket, which is reported and never counted as compliant.
const CLOUDFLARE_FIELDS = ['Is In Cloudflare', 'In Cloudflare'];

/**
 * The headers a static export CANNOT set for itself.
 *
 * `<meta http-equiv>` is honored for Content-Security-Policy only; it is ignored
 * for every other name here. So these six are exactly the set that requires a
 * real response-header mechanism (Cloudflare Transform Rule, Worker, or an
 * origin that emits them) — which is why the gap is invisible to any check that
 * reads the repo instead of the wire.
 */
const REQUIRED_HEADERS = [
  'strict-transport-security',
  'x-content-type-options',
  'x-frame-options',
  'referrer-policy',
  'permissions-policy',
  'content-security-policy',
];

/**
 * Parse the FINAL header block out of raw `curl -sSIL` output.
 *
 * Two things make this less trivial than splitting on newlines:
 *   - a redirect chain emits one block per hop, and only the last one describes
 *     what a browser ends up honoring;
 *   - an egress proxy prepends its own `HTTP/1.1 200 Connection Established`
 *     block (observed in the agent sandbox), which carries no site headers at
 *     all. Taking the first block would report every proxied probe as bare.
 *
 * Returns { status, headers } with lowercased header names. Duplicate names take
 * the LAST value, matching how a browser treats a repeated single-value header.
 */
function parseHeaderBlocks(raw) {
  const text = String(raw || '').replace(/\r/g, '');
  const blocks = [];
  let current = null;
  for (const line of text.split('\n')) {
    if (/^HTTP\/[\d.]+\s+(\d{3})/i.test(line)) {
      const status = Number(/^HTTP\/[\d.]+\s+(\d{3})/i.exec(line)[1]);
      current = { status, headers: new Map() };
      blocks.push(current);
      continue;
    }
    if (!current) continue;
    const m = /^([A-Za-z0-9-]+):\s*(.*)$/.exec(line);
    if (m) current.headers.set(m[1].toLowerCase(), m[2].trim());
  }
  // A CONNECT tunnel block has a status and no headers of its own; it is never
  // the answer when a real response followed it.
  const real = blocks.filter((b) => b.headers.size > 0);
  const chosen = real.length ? real[real.length - 1] : blocks[blocks.length - 1];
  if (!chosen) return { status: 0, headers: {} };
  return { status: chosen.status, headers: Object.fromEntries(chosen.headers) };
}

/**
 * Does the response carry an ENFORCING Content-Security-Policy?
 *
 * `content-security-policy-report-only` deliberately does not count. It reports
 * violations and blocks nothing, so treating it as coverage would mark a site
 * protected on the strength of a header whose entire purpose is to not protect
 * it. freeforcharity.org is the live example: full header set, CSP in
 * report-only mode.
 */
function enforcingCsp(headers) {
  const v = headers['content-security-policy'];
  return typeof v === 'string' && v.trim() ? v : null;
}

/**
 * Evaluate one probe against the required set.
 *
 * `x-frame-options` is satisfied by an enforcing CSP with `frame-ancestors`,
 * which supersedes it — flagging a site that uses the modern directive would be
 * a false finding.
 *
 * A `<meta http-equiv="Content-Security-Policy">` tag is recorded but satisfies
 * NOTHING here: it is the thing this audit exists to stop counting as coverage
 * for the other five names. It is reported so the issue can state what is
 * actually protecting each site today.
 */
function classify(entry) {
  const domain = entry.domain;
  const base = {
    domain,
    hostCategory: entry.hostCategory || null,
    reachable: false,
    status: entry.status || 0,
    servedBy: null,
    proxied: false,
    present: [],
    missing: [],
    reportOnlyCsp: false,
    metaCsp: Boolean(entry.metaCsp),
    error: entry.error || null,
  };
  if (entry.error || !entry.status || entry.status >= 400 || entry.status === 0) {
    base.missing = [];
    return base;
  }

  const headers = entry.headers || {};
  const csp = enforcingCsp(headers);
  const present = [];
  for (const name of REQUIRED_HEADERS) {
    if (typeof headers[name] === 'string' && headers[name].trim()) {
      present.push(name);
      continue;
    }
    if (name === 'x-frame-options' && csp && /frame-ancestors/i.test(csp)) {
      present.push(name);
    }
  }
  const servedBy = headers['server'] || null;
  return {
    ...base,
    reachable: true,
    servedBy,
    // Only a Cloudflare-proxied response can be fixed by a zone Transform Rule.
    // `Server: GitHub.com` means the domain resolves straight to GitHub Pages
    // with no proxy in front, so the zone-level remediation cannot reach it.
    proxied: /cloudflare/i.test(servedBy || ''),
    present,
    missing: REQUIRED_HEADERS.filter((h) => !present.includes(h)),
    reportOnlyCsp: !csp && Boolean(headers['content-security-policy-report-only']),
  };
}

/**
 * Which domains from the sites list are worth probing.
 *
 * Skips are returned with a reason and reported as COUNTS, not as 350 lines of
 * checklist — an audit nobody scrolls through is an audit nobody reads.
 */
function selectDomains(records) {
  const targets = [];
  const skipped = [];
  for (const r of records || []) {
    const domain = String(r.Domain || '').trim();
    if (!domain) continue;
    const hostCategory = String(r['Host Category'] || '').trim();
    const status = String(r.Status || '').trim();
    const inCloudflare = CLOUDFLARE_FIELDS.some(
      (f) =>
        String(r[f] || '')
          .trim()
          .toLowerCase() === 'yes',
    );
    if (String(r['Left FFC'] || '').trim() === 'Yes') {
      skipped.push({ domain, reason: 'left FFC' });
    } else if (!inCloudflare) {
      skipped.push({ domain, reason: 'zone not in FFC Cloudflare' });
    } else {
      // hostCategory rides along for the report only — it never decides scope.
      targets.push({ domain, hostCategory, status });
    }
  }
  targets.sort((a, b) => a.domain.localeCompare(b.domain));
  skipped.sort((a, b) => a.domain.localeCompare(b.domain));
  return { targets, skipped };
}

/** Group skip reasons into counts for the report. */
function summarizeSkips(skipped) {
  const byReason = new Map();
  for (const s of skipped || []) {
    byReason.set(s.reason, (byReason.get(s.reason) || 0) + 1);
  }
  return [...byReason.entries()]
    .map(([reason, count]) => ({ reason, count }))
    .sort((a, b) => b.count - a.count || a.reason.localeCompare(b.reason));
}

/**
 * Bucket every probed domain.
 *
 * `unmeasured` is its own bucket and never counts as a gap: a domain with no
 * usable 2xx/3xx response has not been measured, and not-measured must never
 * render as measured-and-bad. That is the same error #884 caught one level up.
 *
 * Named `unmeasured`, not `unreachable`, because the bucket holds two different
 * things: a connection that failed outright, and a server that answered with an
 * error status. A 503 is emphatically reachable — calling it unreachable sends
 * triage looking for a DNS or TLS fault that is not there.
 */
function analyze(entries, skipped) {
  const results = (entries || []).map(classify);
  const compliant = [];
  const partial = [];
  const bare = [];
  const unmeasured = [];
  for (const r of results) {
    if (!r.reachable) unmeasured.push(r);
    else if (!r.missing.length) compliant.push(r);
    else if (r.present.length) partial.push(r);
    else bare.push(r);
  }
  const measured = compliant.length + partial.length + bare.length;
  const unproxied = results.filter((r) => r.reachable && !r.proxied);
  return {
    compliant,
    partial,
    bare,
    unmeasured,
    unproxied,
    measured,
    probed: results.length,
    skips: summarizeSkips(skipped),
    skippedCount: (skipped || []).length,
    hasGap: partial.length + bare.length > 0,
  };
}

function headerCell(r) {
  return REQUIRED_HEADERS.map((h) => (r.present.includes(h) ? '✅' : '❌')).join(' ');
}

/**
 * Render the rolling-issue body (Markdown). Deterministic given its inputs so a
 * test can assert exact content; the marker comment lets the workflow find the
 * issue again on the next run.
 */
function renderBody(analysis, timestamp) {
  const lines = [];
  lines.push(MARKER);
  lines.push('');
  lines.push(
    'What each FFC-delivered site **actually serves** on the wire, for the six security ' +
      'headers a static export cannot set for itself. Measured by fetching the live response — ' +
      'never by looking for a file in a repo (#884: `public/_headers` is inert on this stack, ' +
      'so a source-file audit reports coverage that does not exist).',
  );
  lines.push('');
  lines.push(`- Generated: ${timestamp}`);
  lines.push(
    `- Domains probed: ${analysis.probed} ` +
      `(compliant ${analysis.compliant.length}, partial ${analysis.partial.length}, ` +
      `none ${analysis.bare.length}, unmeasured ${analysis.unmeasured.length})`,
  );
  lines.push(`- Header order in the columns below: ${REQUIRED_HEADERS.join(', ')}`);
  lines.push('');
  lines.push(
    'This rolling issue auto-closes on the next run where every reachable FFC-delivered ' +
      'domain serves the full set.',
  );
  lines.push('');

  lines.push(`## Serving nothing (${analysis.bare.length})`);
  lines.push('');
  if (analysis.bare.length) {
    lines.push('| Domain | Served by | Proxied | CSP meta tag |');
    lines.push('| --- | --- | --- | --- |');
    for (const r of analysis.bare) {
      lines.push(
        `| ${r.domain} | ${r.servedBy || '—'} | ${r.proxied ? 'yes' : 'no'} | ` +
          `${r.metaCsp ? 'yes (CSP only, nothing else)' : 'no'} |`,
      );
    }
  } else {
    lines.push('_none_');
  }
  lines.push('');

  lines.push(`## Partial (${analysis.partial.length})`);
  lines.push('');
  if (analysis.partial.length) {
    lines.push('| Domain | Headers | Missing |');
    lines.push('| --- | --- | --- |');
    for (const r of analysis.partial) {
      const note = r.reportOnlyCsp ? ' _(CSP is report-only — enforces nothing)_' : '';
      lines.push(`| ${r.domain} | ${headerCell(r)} | ${r.missing.join(', ')}${note} |`);
    }
  } else {
    lines.push('_none_');
  }
  lines.push('');

  lines.push(`## Compliant (${analysis.compliant.length})`);
  lines.push('');
  lines.push(
    analysis.compliant.length ? analysis.compliant.map((r) => r.domain).join(', ') : '_none_',
  );
  lines.push('');

  if (analysis.unproxied.length) {
    lines.push(`## Not behind the Cloudflare proxy (${analysis.unproxied.length})`);
    lines.push('');
    lines.push(
      'A zone Transform Rule — the recommended remediation in #884 — cannot reach these: they ' +
        'resolve straight to the origin with no proxy in front, so they must be proxied first.',
    );
    lines.push('');
    lines.push(analysis.unproxied.map((r) => `${r.domain} (${r.servedBy || '—'})`).join(', '));
    lines.push('');
  }

  if (analysis.unmeasured.length) {
    lines.push(`## Unmeasured (${analysis.unmeasured.length})`);
    lines.push('');
    lines.push(
      '_No usable 2xx/3xx response — either the request failed or the server answered with ' +
        'an error status. Header posture NOT asserted either way._',
    );
    lines.push('');
    for (const r of analysis.unmeasured) {
      lines.push(`- ${r.domain}: ${r.error || `HTTP ${r.status || 'no response'}`}`);
    }
    lines.push('');
  }

  if (analysis.skips.length) {
    lines.push(`## Out of scope (${analysis.skippedCount})`);
    lines.push('');
    lines.push(
      "_Outside this audit's scope, for the reason given. A domain whose zone FFC does not " +
        'control cannot be fixed by a zone-level Transform Rule; a domain that has left FFC is ' +
        'no longer ours to serve. Neither is a statement about its header posture._',
    );
    lines.push('');
    lines.push('| Reason | Domains |');
    lines.push('| --- | --- |');
    for (const s of analysis.skips) {
      lines.push(`| ${s.reason} | ${s.count} |`);
    }
    lines.push('');
  }

  lines.push('_Managed by 743. Website - Fleet Security Header Audit. Refs #884._');
  return lines.join('\n');
}

module.exports = {
  SITES_LIST_PATH,
  MARKER,
  ISSUE_TITLE,
  ISSUE_LABELS,
  REQUIRED_HEADERS,
  CLOUDFLARE_FIELDS,
  parseHeaderBlocks,
  enforcingCsp,
  classify,
  selectDomains,
  summarizeSkips,
  analyze,
  renderBody,
};
