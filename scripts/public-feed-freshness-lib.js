'use strict';

// Pure classification + report logic for workflow 744 (Repo - Public Feed
// Freshness). The workflow's github-script step `require`s this module and only
// performs I/O — fetching the two published files and opening/closing the
// rolling issue — so the logic tested under tests/workflow-logic/ is the logic
// that ships.
//
// Why this exists (#977). Three mechanisms are supposed to keep the public
// /agentic-os status page current, and none of them checks whether the page's
// data is actually fresh:
//
//   - #921 (via 740) watches whether 502 SUCCEEDS — not whether its PR merged.
//   - #908 watches data-refresh PRs hanging behind `main` — not the publisher
//     never producing a PR at all.
//   - #771 watches DRIFT between the two copies — not both being equally stale.
//
// Every one of those can be green while the published page is days old, which
// is exactly what happened for three days from 2026-07-29 when 502's `deliver`
// job started failing on a dead PAT (#848). This module asserts the OUTCOME —
// the timestamp the public page is actually serving — which catches all three
// causes plus the next unknown one.
//
// Two rules the classification exists to hold:
//
//   1. **Unknown is never fresh.** A file that is missing, unreadable, or whose
//      `generated_at` is absent/empty/unparseable is a FINDING, never a clean
//      pass. That fail-open shape — "could not check" collapsing into "checked,
//      fine" — is the whole subject of #977, and the repo has been bitten by it
//      before (#726/#855, ledger L02).
//   2. **A registered path with no observation is a finding too.** If the
//      workflow hands back fewer results than FEED_PATHS, the missing ones are
//      reported rather than dropped, so a step that silently skips a path
//      cannot produce a green run.
//
// This monitor deliberately uses the ambient GITHUB_TOKEN and NO Key Vault
// credential. The files are public. A freshness monitor that depended on
// `read-all-cbm-github-pat` — the credential whose death it exists to reveal —
// would go dark at precisely the moment it was needed.

const FEED_OWNER = 'FreeForCharity';
const FEED_REPO = 'FFC-IN-ffcadmin.org';

// Both copies are published, and #771 exists because they are written
// separately and can disagree. Checking each by path means "the site build is
// fresh but the public/ copy is stale" is reported as the one stale path it is,
// rather than averaged away.
const FEED_PATHS = ['public/data/agentic-os-status.json', 'src/data/agentic-os-status.json'];

const TIMESTAMP_FIELD = 'generated_at';

// 502 publishes daily at 07:17 UTC. 36h means one missed tick does not page,
// but two consecutive ones do — the three-day outage in #977 would have been
// reported on its first morning.
const STALE_AFTER_HOURS = 36;

// A timestamp ahead of now by more than this is treated as invalid rather than
// as very fresh. Without it, a bad clock or a hand-edited date reads as fresh
// forever, which is the same fail-open this workflow exists to close. One hour
// absorbs ordinary skew between the publisher and this runner.
const FUTURE_TOLERANCE_HOURS = 1;

// Date.parse accepts far more than ISO-8601 (`Date.parse('2026')` is a valid
// number), so a shape check runs first. A date with no time of day cannot
// express freshness to the hour and is rejected here rather than silently
// treated as midnight.
const ISO_8601 = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})$/;

const MARKER = '<!-- public-feed-freshness -->';
const ISSUE_TITLE = 'Public Agentic OS status feed is stale or unreadable';
const ISSUE_LABELS = ['bug', 'agentic-os'];

/**
 * Classify one fetched feed file.
 *
 * @param {{path:string, status?:number|null, error?:string|null, text?:string|null}} obs
 *   What the workflow observed: the HTTP status, a transport/API error message,
 *   and the raw file text when one was returned.
 * @param {string} nowIso generation timestamp of this run
 * @returns {{path:string, verdict:'FRESH'|'STALE'|'MISSING'|'UNREADABLE'|'INVALID',
 *            generatedAt:string|null, ageHours:number|null, detail:string}}
 *
 * MISSING    — the file is not there (404). The publisher never wrote it, or a
 *              path moved without this registry being updated.
 * UNREADABLE — the fetch failed for any other reason (5xx, rate limit,
 *              transport). Says nothing about the feed, so it must not read as
 *              healthy.
 * INVALID    — fetched, but the timestamp cannot be believed: unparseable JSON,
 *              no `generated_at`, an empty or non-ISO value, or a value in the
 *              future.
 * STALE      — a good timestamp, older than STALE_AFTER_HOURS.
 * FRESH      — a good timestamp within the window. The only non-finding.
 */
function classifyFeed(obs, nowIso) {
  const path = (obs && obs.path) || '(unknown path)';
  const out = (verdict, detail, extra) =>
    Object.assign({ path, verdict, generatedAt: null, ageHours: null, detail }, extra || {});

  if (!obs) return out('UNREADABLE', 'no observation produced for this path');

  const status = Number.isFinite(Number(obs.status)) ? Number(obs.status) : null;
  if (status === 404) return out('MISSING', 'file not found in the published repository');
  if (obs.error) return out('UNREADABLE', String(obs.error));
  if (status !== null && status !== 200) return out('UNREADABLE', `HTTP ${status}`);
  if (typeof obs.text !== 'string' || obs.text.trim() === '') {
    return out('UNREADABLE', 'no content returned');
  }

  let parsed;
  try {
    parsed = JSON.parse(obs.text);
  } catch (e) {
    return out('INVALID', `feed did not parse as JSON: ${String(e.message || e).slice(0, 120)}`);
  }
  if (!parsed || typeof parsed !== 'object') {
    return out('INVALID', 'feed is not a JSON object');
  }

  const raw = parsed[TIMESTAMP_FIELD];
  if (typeof raw !== 'string' || raw.trim() === '') {
    return out(
      'INVALID',
      raw === undefined
        ? `\`${TIMESTAMP_FIELD}\` is absent`
        : `\`${TIMESTAMP_FIELD}\` is empty or not a string`,
    );
  }
  const value = raw.trim();
  if (!ISO_8601.test(value)) {
    return out('INVALID', `\`${TIMESTAMP_FIELD}\` is not an ISO-8601 timestamp: \`${value}\``, {
      generatedAt: value,
    });
  }
  const then = Date.parse(value);
  if (!Number.isFinite(then)) {
    return out('INVALID', `\`${TIMESTAMP_FIELD}\` did not parse: \`${value}\``, {
      generatedAt: value,
    });
  }

  const now = Date.parse(nowIso);
  const ageHours = Math.round(((now - then) / 3600000) * 10) / 10;
  if (ageHours < -FUTURE_TOLERANCE_HOURS) {
    return out('INVALID', `\`${TIMESTAMP_FIELD}\` is ${Math.abs(ageHours)}h in the future`, {
      generatedAt: value,
      ageHours,
    });
  }
  if (ageHours > STALE_AFTER_HOURS) {
    return out('STALE', `last published ${ageHours}h ago`, { generatedAt: value, ageHours });
  }
  return out('FRESH', `published ${ageHours}h ago`, { generatedAt: value, ageHours });
}

/**
 * Fold every observation into one verdict.
 *
 * A registered path the workflow returned nothing for is classified as
 * UNREADABLE rather than skipped — a monitor that reports green over a path it
 * never checked is the defect this workflow exists to catch, one level up.
 *
 * @param {{observations?:Array<object>, now?:string}} input
 */
function analyze(input) {
  const nowIso = (input && input.now) || new Date().toISOString();
  const observations = (input && input.observations) || [];
  const byPath = new Map();
  for (const o of observations) {
    if (o && o.path) byPath.set(o.path, o);
  }

  const results = FEED_PATHS.map((path) =>
    classifyFeed(
      byPath.has(path)
        ? byPath.get(path)
        : // Synthesised so the finding still carries its path — a report that
          // cannot say WHICH copy went unchecked is barely a report.
          { path, status: null, error: 'no observation produced for this path', text: null },
      nowIso,
    ),
  );
  // Anything the workflow reported for a path not in the registry is still
  // classified — an unexpected extra is visible rather than dropped.
  for (const o of observations) {
    if (o && o.path && !FEED_PATHS.includes(o.path)) results.push(classifyFeed(o, nowIso));
  }

  const pick = (v) => results.filter((r) => r.verdict === v);
  const stale = pick('STALE');
  const missing = pick('MISSING');
  const unreadable = pick('UNREADABLE');
  const invalid = pick('INVALID');
  const fresh = pick('FRESH');

  return {
    feedRepo: `${FEED_OWNER}/${FEED_REPO}`,
    checked: results.length,
    registered: FEED_PATHS.length,
    thresholdHours: STALE_AFTER_HOURS,
    results,
    stale,
    missing,
    unreadable,
    invalid,
    fresh,
    hasFinding: Boolean(stale.length || missing.length || unreadable.length || invalid.length),
  };
}

/** One-line summary for `core.notice` and the recovery comment. */
function summary(a) {
  return (
    `feed=${a.feedRepo} checked=${a.checked} of ${a.registered} fresh=${a.fresh.length} ` +
    `stale=${a.stale.length} missing=${a.missing.length} invalid=${a.invalid.length} ` +
    `unreadable=${a.unreadable.length} threshold=${a.thresholdHours}h`
  );
}

function _table(header, rows) {
  return [`| ${header.join(' | ')} |`, `| ${header.map(() => '---').join(' | ')} |`, ...rows];
}

function _row(r) {
  return `| \`${r.path}\` | ${r.generatedAt || '—'} | ${r.ageHours === null ? '—' : `${r.ageHours}h`} | ${r.detail} |`;
}

/**
 * Render the rolling issue body.
 *
 * @param {object} a analysis from analyze()
 * @param {string} iso generation timestamp
 */
function renderBody(a, iso) {
  const lines = [MARKER, ''];
  lines.push(
    `Freshness of the **published** Agentic OS status feed in \`${a.feedRepo}\` — the data behind ` +
      '<https://ffcadmin.org/agentic-os/>. This checks the outcome (how old the served timestamp ' +
      'is), not whether any particular publishing step succeeded.',
    '',
    `- Generated: ${iso}`,
    `- Paths checked: ${a.checked} of ${a.registered} registered`,
    `- Stale after: ${a.thresholdHours}h (502 publishes daily, so one missed tick does not report)`,
    '',
    'This rolling issue auto-closes on the next run where every registered path is present, ' +
      'readable, carries a parseable `' +
      TIMESTAMP_FIELD +
      '`, and is inside the window. Anything less keeps it open — a path that could not be ' +
      'checked is a finding, never a pass.',
    '',
  );

  if (a.stale.length) {
    lines.push(
      `## 🕰️ Stale — older than ${a.thresholdHours}h (${a.stale.length})`,
      '',
      'The public page is serving old data. Likely causes, in the order they have actually ' +
        'happened: `502. Google - Analytics Report`’s `deliver` job failing (#921/#848), a ' +
        'data-refresh PR stuck behind `main` (#908), or the publisher never opening a PR at all.',
      '',
      ..._table(['Path', TIMESTAMP_FIELD, 'Age', 'Detail'], a.stale.map(_row)),
      '',
    );
  }

  if (a.missing.length) {
    lines.push(
      `## 🔍 Missing from the published repository (${a.missing.length})`,
      '',
      'Either the publisher has never written this path, or it moved without ' +
        '`scripts/public-feed-freshness-lib.js` being updated. Until it is resolved this copy is ' +
        'not being checked at all.',
      '',
      ..._table(['Path', TIMESTAMP_FIELD, 'Age', 'Detail'], a.missing.map(_row)),
      '',
    );
  }

  if (a.invalid.length) {
    lines.push(
      `## ⛔ Unbelievable timestamp (${a.invalid.length})`,
      '',
      `A feed whose \`${TIMESTAMP_FIELD}\` is absent, empty, not ISO-8601, or in the future ` +
        'cannot be shown to be fresh, so it is reported rather than assumed good.',
      '',
      ..._table(['Path', TIMESTAMP_FIELD, 'Age', 'Detail'], a.invalid.map(_row)),
      '',
    );
  }

  if (a.unreadable.length) {
    lines.push(
      `## ❓ Could not be read (${a.unreadable.length})`,
      '',
      'Not evidence of a stale feed, and **not** evidence of a fresh one. A transport failure, a ' +
        'provider 5xx, or a path the run never got to.',
      '',
      ..._table(['Path', TIMESTAMP_FIELD, 'Age', 'Detail'], a.unreadable.map(_row)),
      '',
    );
  }

  if (a.fresh.length) {
    lines.push(
      `## ✅ Fresh (${a.fresh.length})`,
      '',
      ..._table(['Path', TIMESTAMP_FIELD, 'Age', 'Detail'], a.fresh.map(_row)),
      '',
    );
  }

  lines.push(
    '---',
    '',
    '_Managed by 744. Repo - Public Feed Freshness. Refs #977. Reads two public files with the ' +
      'ambient `GITHUB_TOKEN` — deliberately no Key Vault credential, so this monitor cannot go ' +
      'dark for the same reason the feed does._',
  );
  return lines.join('\n');
}

module.exports = {
  FEED_OWNER,
  FEED_REPO,
  FEED_PATHS,
  TIMESTAMP_FIELD,
  STALE_AFTER_HOURS,
  FUTURE_TOLERANCE_HOURS,
  ISO_8601,
  MARKER,
  ISSUE_TITLE,
  ISSUE_LABELS,
  classifyFeed,
  analyze,
  summary,
  renderBody,
};
