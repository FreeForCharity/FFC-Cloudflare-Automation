// Public "pending applications" companion to the Sites List
// (sites-list/pending_applications.{csv,json}).
//
// The Sites List is keyed by domain, so a charity whose application has no
// domain yet never appears on it, and nothing public says how close any pending
// application is to being accepted. Clarke, 2026-11-13: show each pending
// application with its org name, its score and its gaps, so every number is
// publicly tied to the list of what is missing.
//
// Source: workflow 232's charity-lane-verdicts.json (schema charity-lane-verdicts/v2),
// which is already PII-free by design (order id, client id, pid, gate, org name,
// desired domain, verdict, reason codes). This script publishes a SUBSET of it:
//
//   - only Gate 1 onboarding orders (pid 16 pre-501(c)(3), pid 33 501(c)(3));
//   - org name and desired domain as 232 emits them (232 already blanks a name
//     that looks like a person's name or carries contact data);
//   - the score, computed from 232's reason codes against the fixed checklist
//     below, and the plain-English labels of the items that are not done;
//   - a public status in place of the verdict. Fraud screening, duplicate and
//     EIN-identity codes are never published: they only move a row to
//     "Under FFC review".
//
// The score counts checklist ITEMS, not reason codes, so "14 of 20" always
// matches the 6 labels listed beside it. An item is done when none of its
// codes is on the row. That is only true once 232 runs the pre-acceptance
// completeness review (#1747); a report without it, or a row carrying
// COMPLETENESS_NOT_CHECKED, gets no score at all rather than a flattering one.
//
// Curated inputs (sites-list/pending_applications_curated.csv, edited by hand):
// the charity's category (FFC priority: food, water or shelter first, then
// veterans, then all others), an FFC-EX repo when one exists under another
// domain, and a short public note.
//
// Usage:
//   node scripts/pending-applications-public.mjs --verdicts <charity-lane-verdicts.json>
//     [--curated sites-list/pending_applications_curated.csv] [--repos <file of repo names>]
//     [--out-dir sites-list] [--now <ISO time>]

import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

export const ORG = 'FreeForCharity';

// Checklist items. `pids` = which tracks the item applies to; `codes` = the 232
// reason codes that mean the item is NOT done. Labels are public.
export const CHECKLIST = [
  {
    key: 'legal_name',
    label: 'Legal name',
    pids: [16, 33],
    codes: ['ORG_LEGAL_NAME_MISSING', 'ORG_NAME_MISSING', 'ORG_NAME_IS_APPLICANT'],
  },
  { key: 'mission', label: 'Mission', pids: [16, 33], codes: ['MISSION_MISSING'] },
  {
    key: 'founding_year',
    label: 'Founding year',
    pids: [16, 33],
    codes: ['FOUNDING_YEAR_MISSING', 'FOUNDING_YEAR_INVALID'],
  },
  {
    key: 'public_email',
    label: 'Public email',
    pids: [16, 33],
    codes: ['PUBLIC_EMAIL_MISSING', 'PUBLIC_EMAIL_INVALID'],
  },
  {
    key: 'public_phone',
    label: 'Public phone',
    pids: [16, 33],
    codes: ['PUBLIC_PHONE_MISSING', 'PUBLIC_PHONE_INVALID'],
  },
  {
    key: 'city_state',
    label: 'City and state',
    pids: [16, 33],
    codes: ['FOOTER_LOCATION_MISSING', 'FOOTER_LOCATION_INVALID'],
  },
  {
    key: 'legal_status',
    label: 'Legal status',
    pids: [16, 33],
    codes: ['LEGAL_STATUS_MISSING', 'LEGAL_STATUS_UNRECOGNIZED', 'TRACK_MISMATCH'],
  },
  {
    key: 'attestations',
    label: 'Terms and US attestations',
    pids: [16, 33],
    codes: ['TOS_NOT_ATTESTED', 'US_NOT_ATTESTED'],
  },
  {
    key: 'template',
    label: 'Website starting point',
    pids: [16, 33],
    codes: ['TEMPLATE_CHOICE_MISSING'],
  },
  {
    key: 'domain_answer',
    label: 'Domain answer',
    pids: [16, 33],
    codes: ['DOMAIN_OWNERSHIP_MISSING'],
  },
  { key: 'tagline', label: 'Tagline', pids: [16, 33], codes: ['TAGLINE_MISSING'] },
  {
    key: 'site_description',
    label: 'Site description',
    pids: [16, 33],
    codes: ['SITE_DESCRIPTION_MISSING'],
  },
  {
    key: 'short_description',
    label: 'Short description',
    pids: [16, 33],
    codes: ['SHORT_DESCRIPTION_MISSING'],
  },
  { key: 'main_program', label: 'Main program', pids: [16, 33], codes: ['PROGRAM_1_MISSING'] },
  {
    key: 'facebook',
    label: 'Facebook Page',
    pids: [16, 33],
    codes: ['FACEBOOK_URL_MISSING', 'FACEBOOK_URL_INVALID', 'FACEBOOK_PAGE_NOT_PUBLIC'],
  },
  {
    key: 'linkedin',
    label: 'LinkedIn Page',
    pids: [16, 33],
    codes: ['LINKEDIN_URL_MISSING', 'LINKEDIN_URL_NOT_ORG_PAGE', 'LINKEDIN_PAGE_NOT_PUBLIC'],
  },
  {
    key: 'github',
    label: 'GitHub maintainer',
    pids: [16, 33],
    codes: ['GITHUB_USER_MISSING', 'GITHUB_USER_INVALID', 'GITHUB_USER_NOT_FOUND'],
  },
  {
    key: 'officers',
    label: 'Three different officers',
    pids: [16, 33],
    codes: ['OFFICER_NAMES_MISSING', 'OFFICERS_NOT_DISTINCT'],
  },
  {
    key: 'officer_linkedin',
    label: 'Officer LinkedIn profiles',
    pids: [16, 33],
    codes: [
      'OFFICER_LINKEDIN_MISSING',
      'OFFICER_LINKEDIN_NOT_PROFILE',
      'OFFICER_LINKEDIN_NOT_DISTINCT',
    ],
  },
  {
    key: 'officer_consent',
    label: 'Officer consent',
    pids: [16, 33],
    codes: ['OFFICERS_CONSENT_MISSING'],
  },
  { key: 'ein', label: 'EIN', pids: [33], codes: ['EIN_MISSING'] },
  {
    key: 'donation',
    label: 'Zeffy donation link',
    pids: [33],
    codes: ['DONATION_URL_MISSING', 'DONATION_URL_NOT_PUBLIC', 'DONATION_URL_NOT_FOUND'],
  },
  {
    key: 'volunteer',
    label: 'Idealist volunteer link',
    pids: [33],
    codes: ['VOLUNTEER_URL_MISSING', 'VOLUNTEER_URL_NOT_PUBLIC', 'VOLUNTEER_URL_NOT_FOUND'],
  },
  {
    key: 'candid',
    label: 'Candid profile',
    pids: [33],
    codes: ['CANDID_URL_MISSING', 'CANDID_URL_NOT_PUBLIC', 'CANDID_URL_NOT_FOUND'],
  },
];

// Present in 232's code catalog only once the completeness review (#1747) is in.
export const COMPLETENESS_MARKER = 'ORG_LEGAL_NAME_MISSING';

export const CATEGORIES = {
  food_water_shelter: { label: 'Food, water or shelter', priority: 1 },
  veterans: { label: 'Veterans', priority: 2 },
  other: { label: 'Other', priority: 3 },
  unclassified: { label: 'Not yet classified', priority: 3 },
};

export const TRACKS = { 16: 'Pre-501(c)(3)', 33: '501(c)(3)' };

export function checklistFor(pid) {
  return CHECKLIST.filter((c) => c.pids.includes(Number(pid)));
}

export function publicStatus(verdict) {
  if (verdict === 'ACCEPT') return 'Ready for FFC review';
  if (verdict === 'NEED_INFO') return 'Waiting on the charity';
  return 'Under FFC review';
}

// Score one row. Returns { done, total, missing: [labels] } or null when the
// completeness review did not run, so no number can be published honestly.
export function scoreRow(row, completenessAvailable) {
  const codes = new Set((row.reason_codes || []).map(String));
  if (!completenessAvailable || codes.has('COMPLETENESS_NOT_CHECKED')) return null;
  const items = checklistFor(row.pid);
  const missing = items.filter((c) => c.codes.some((code) => codes.has(code))).map((c) => c.label);
  return { done: items.length - missing.length, total: items.length, missing };
}

export function parseCsv(text) {
  // Small RFC 4180 reader: quoted fields, doubled quotes, CRLF.
  const rows = [];
  let row = [];
  let field = '';
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') {
        field += '"';
        i++;
      } else if (ch === '"') quoted = false;
      else field += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === ',') {
      row.push(field);
      field = '';
    } else if (ch === '\n' || ch === '\r') {
      if (ch === '\r' && text[i + 1] === '\n') i++;
      row.push(field);
      field = '';
      if (row.some((v) => v !== '')) rows.push(row);
      row = [];
    } else field += ch;
  }
  row.push(field);
  if (row.some((v) => v !== '')) rows.push(row);
  if (rows.length === 0) return [];
  const [header, ...body] = rows;
  return body.map((r) => Object.fromEntries(header.map((h, i) => [h.trim(), (r[i] ?? '').trim()])));
}

export function csvCell(v) {
  const s = v === null || v === undefined ? '' : String(v);
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function loadCurated(text) {
  const map = new Map();
  for (const r of parseCsv(text || '')) {
    if (!/^\d+$/.test(r.order_id || '')) continue;
    const category = CATEGORIES[r.category] ? r.category : 'unclassified';
    map.set(String(r.order_id), { category, ffc_repo: r.ffc_repo || '', note: r.note || '' });
  }
  return map;
}

function daysBetween(fromIso, nowIso) {
  const from = Date.parse(fromIso);
  const now = Date.parse(nowIso);
  if (Number.isNaN(from) || Number.isNaN(now)) return null;
  return Math.max(0, Math.floor((now - from) / 86400000));
}

export function buildPendingApplications(
  report,
  { curated = new Map(), repos = new Set(), now = new Date().toISOString() } = {},
) {
  if (!report || !Array.isArray(report.verdicts))
    throw new Error('not a charity-lane verdicts report: no `verdicts` array');
  const catalog = report.reason_code_catalog || {};
  const completenessAvailable = Object.prototype.hasOwnProperty.call(catalog, COMPLETENESS_MARKER);
  const repoNames = new Set([...repos].map((r) => String(r).toLowerCase()));

  const apps = [];
  for (const row of report.verdicts) {
    if (Number(row.gate) !== 1 || !TRACKS[Number(row.pid)]) continue;
    const orderId = String(row.order_id);
    const cur = curated.get(orderId) || { category: 'unclassified', ffc_repo: '', note: '' };
    const cat = CATEGORIES[cur.category];
    const domain = String(row.desired_domain || '').toLowerCase();
    let repo = cur.ffc_repo;
    if (!repo && domain && repoNames.has(`ffc-ex-${domain}`)) repo = `FFC-EX-${domain}`;
    const score = scoreRow(row, completenessAvailable);
    const codes = new Set((row.reason_codes || []).map(String));
    const name = String(row.org_name || '').trim();
    apps.push({
      order_id: Number(orderId) || orderId,
      charity:
        name ||
        (codes.has('ORG_NAME_WITHHELD') || codes.has('ORG_NAME_IS_APPLICANT')
          ? '(name withheld)'
          : '(name not given)'),
      domain,
      track: TRACKS[Number(row.pid)],
      category: cat.label,
      priority: cat.priority,
      status: publicStatus(String(row.verdict)),
      items_done: score ? score.done : null,
      items_total: score ? score.total : null,
      score: score ? `${score.done} of ${score.total}` : 'Not yet checked',
      coverage_percent: score ? Math.round((100 * score.done) / score.total) : null,
      missing: score ? score.missing : [],
      ffc_repo: repo ? `https://github.com/${ORG}/${repo}` : '',
      days_waiting: row.order_date ? daysBetween(row.order_date, now) : null,
      note: cur.note,
    });
  }

  apps.sort(
    (a, b) =>
      a.priority - b.priority ||
      (b.coverage_percent ?? -1) - (a.coverage_percent ?? -1) ||
      Number(a.order_id) - Number(b.order_id),
  );
  apps.forEach((a, i) => (a.rank = i + 1));

  return {
    schema: 'pending-applications/v1',
    generated_at: now,
    source: {
      workflow: '232',
      generated_at: report.generated_at || '',
      run_url: report.run_url || '',
    },
    completeness_review: completenessAvailable,
    scoring_note:
      'Score = checklist items done out of the items required for the track. An item is done when the gate (workflow 232) raised none of its reason codes. Missing lists exactly the items not done. Ranking: FFC priority (food, water or shelter; then veterans; then all others), then score, then order number.',
    checklist: {
      'Pre-501(c)(3)': checklistFor(16).map((c) => c.label),
      '501(c)(3)': checklistFor(33).map((c) => c.label),
    },
    counts: {
      total: apps.length,
      ready: apps.filter((a) => a.status === 'Ready for FFC review').length,
      waiting_on_charity: apps.filter((a) => a.status === 'Waiting on the charity').length,
      under_review: apps.filter((a) => a.status === 'Under FFC review').length,
    },
    applications: apps.map(({ rank, ...rest }) => ({ rank, ...rest })),
  };
}

export const CSV_COLUMNS = [
  ['Rank', (a) => a.rank],
  ['Order', (a) => a.order_id],
  ['Charity', (a) => a.charity],
  ['Domain', (a) => a.domain],
  ['Track', (a) => a.track],
  ['Category', (a) => a.category],
  ['Status', (a) => a.status],
  ['Score', (a) => a.score],
  ['Coverage %', (a) => a.coverage_percent],
  ['Missing', (a) => a.missing.join('; ')],
  ['FFC Repo', (a) => a.ffc_repo],
  ['Days Waiting', (a) => a.days_waiting],
  ['Note', (a) => a.note],
];

export function toCsv(doc) {
  const lines = [CSV_COLUMNS.map(([h]) => h).join(',')];
  for (const a of doc.applications) lines.push(CSV_COLUMNS.map(([, f]) => csvCell(f(a))).join(','));
  return lines.join('\n') + '\n';
}

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith('--')) throw new Error(`unexpected argument: ${a}`);
    out[a.slice(2)] = argv[++i];
  }
  return out;
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  if (!args.verdicts) throw new Error('--verdicts <charity-lane-verdicts.json> is required');
  const report = JSON.parse(fs.readFileSync(args.verdicts, 'utf8'));
  const curatedPath = args.curated || 'sites-list/pending_applications_curated.csv';
  const curated = fs.existsSync(curatedPath)
    ? loadCurated(fs.readFileSync(curatedPath, 'utf8'))
    : new Map();
  const repos = args.repos
    ? new Set(
        fs
          .readFileSync(args.repos, 'utf8')
          .split(/\r?\n/)
          .map((s) => s.trim())
          .filter(Boolean),
      )
    : new Set();
  const doc = buildPendingApplications(report, {
    curated,
    repos,
    now: args.now || new Date().toISOString(),
  });
  const outDir = args['out-dir'] || 'sites-list';
  fs.mkdirSync(outDir, { recursive: true });
  fs.writeFileSync(
    path.join(outDir, 'pending_applications.json'),
    JSON.stringify(doc, null, 2) + '\n',
    'utf8',
  );
  fs.writeFileSync(path.join(outDir, 'pending_applications.csv'), toCsv(doc), 'utf8');
  console.log(
    `pending applications: ${doc.counts.total} (completeness review ${doc.completeness_review ? 'present' : 'ABSENT: scores withheld'})`,
  );
  if (!doc.completeness_review) {
    console.log(
      '::warning::232 report has no completeness review codes, so no scores were published. Scores appear once the pre-acceptance completeness review (#1747) is in 232.',
    );
  }
}

if (import.meta.url === pathToFileURL(process.argv[1] || '').href) {
  try {
    main();
  } catch (e) {
    console.error(`::error::${e.message}`);
    process.exit(1);
  }
}
