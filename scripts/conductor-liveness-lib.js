'use strict';

// Pure classification + report logic for workflow 747 (Repo - Conductor
// Liveness). The workflow's github-script step `require`s this module and only
// performs I/O — reading the Conductor Log's comments, the merged/open
// `agentic-os` PR sets, and opening/closing the rolling issue — so the logic
// tested under tests/workflow-logic/ is the logic that ships.
//
// Why this exists (#1339). The Conductor is the only actor with promotion and
// merge authority, and it is a LOCALLY-run routine on an operator workstation.
// Its only GitHub footprint is the comments it posts to the Conductor Log
// (#719). This repo has 17 monitoring workflows (730-746) and every one of them
// watches something that happens INSIDE GitHub Actions — waiting runs (734),
// scheduled-workflow failures (740), board drift (745), feed freshness (744).
// So the supervisor's absence is invisible to the entire monitoring fleet by
// construction, and on 2026-09-14 it went quiet mid-run for ~5 days with all 17
// green: 0 `agentic-os` PRs merged, the open pile growing 4 -> 13, and ~39
// consecutive cloud-worker runs each correctly computing "0 worker-actionable
// PRs" and filing it as a normal terminal state.
//
// 744's docstring already makes this argument one level down — #921 watches
// whether 502 SUCCEEDS, not whether its PR merged; #908 watches PRs hanging
// behind `main`, not the publisher never opening one. Same shape, one level up:
// seventeen PROCESS checks were green for five days while the process they exist
// to serve produced nothing. This module asserts the OUTCOME instead.
//
// Three rules the classification exists to hold:
//
//   1. **Unknown is never alive.** An unreadable log, an empty comment page, or
//      a timestamp that is absent/empty/non-ISO/in the future is a FINDING,
//      never "fresh". "Could not check" and "checked, fine" must not collapse
//      (ledger L02, and 744's whole subject).
//   2. **Every signal is reported BY NAME with its measured value**, never
//      averaged into one verdict. Two independent signals were flat for five
//      days here and either alone would have caught it; an average of four
//      would have diluted both.
//   3. **A terminal state reached N times in a row is not a terminal state.**
//      The open-PR pile's LEVEL is not a finding — 4 open PRs is an ordinary
//      healthy day, above the cap of 3 or not. Sustained monotonic GROWTH
//      across consecutive runs is, because that is the shape a stalled
//      supervisor makes and the shape 39 worker runs could not see.
//
// This monitor deliberately uses the ambient GITHUB_TOKEN and NO Key Vault
// credential, for 744's reason carried up a level: a liveness check must not
// depend on a credential whose death it might need to report. No Key Vault
// access, no environment, no approval gate — so nothing it watches can also
// block it.

const LOG_OWNER = 'FreeForCharity';
const LOG_REPO = 'FFC-Cloudflare-Automation';

// The pinned Conductor Log. Every Conductor run posts a START and an END here;
// those comments are the routine's ONLY footprint in GitHub, which is what
// makes their arrival the signal and their absence the outage.
const LOG_ISSUE = 719;

// Observed healthy cadence is ~3h, so 6h is one missed run and 12h is at least
// three. #1339's discrimination bar is that the real window
// (2026-09-14T10:20Z -> 2026-09-19T09:20Z) reports on DAY 1, not day 5 — a
// monitor that only fires at day 5 reproduces the bug it was filed about.
const SILENCE_WARN_HOURS = 6;
const SILENCE_ALERT_HOURS = 12;

// The independent second signal. Healthy cadence was 5-8 `agentic-os` PRs/day,
// so a full day with none is notable and two is an outage. Kept deliberately
// looser than the silence thresholds: this one can go quiet for legitimate
// reasons (an empty queue, a day spent on one large PR), so it corroborates the
// log signal rather than racing it.
const MERGE_WARN_HOURS = 24;
const MERGE_ALERT_HOURS = 48;

// The worker's landing-sweep cap, from AGENTS.md. Reported as context on every
// run; see GROWTH_SAMPLES for why the level alone is not a finding.
const PR_CAP = 3;

// Consecutive samples that must each be strictly greater than the one before
// for growth to be a finding. Three samples means two rises, which no single
// busy afternoon produces but a stalled supervisor produces every time.
const GROWTH_SAMPLES = 3;

// How many samples the rolling issue carries forward. Enough to see a trend and
// to survive a few runs of a transient, without turning the issue body into a
// log file.
const HISTORY_LIMIT = 12;

// A timestamp ahead of now by more than this is INVALID rather than very fresh.
// Without it a bad clock or a hand-edited date reads as alive forever — the same
// fail-open this workflow exists to close. One hour absorbs ordinary skew.
const FUTURE_TOLERANCE_HOURS = 1;

// Date.parse accepts far more than ISO-8601 (`Date.parse('2026')` is a valid
// number), so a shape check runs first. A date with no time of day cannot
// express liveness to the hour and is rejected rather than read as midnight.
const ISO_8601 = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})$/;

// Matches every START/END spelling the log has actually carried. This pattern is
// the one piece of this module most likely to rot, and ledger L215 is exactly
// that rot: a run derived its own run number with /RUN [0-9]+ START/ and got
// "Run 86" against an actual 132, because every run since ~87 writes
// `## Run N — START` with an EM DASH. Nothing errored; the filter honestly
// reported the newest thing it could see, and the two bugs composed into one
// well-formed, in-range, 46-runs-stale answer.
//
// **This module shipped with that exact defect, and the live log caught it.**
// The first revision allowed leading `#` and `>` but not emphasis, and the log
// changed format again at run 167 to a BOLD line rather than a heading:
//
//     ## Run 166 — END                       <- matched
//     **Conductor run 167 — START** (…)      <- did NOT match
//
// Measured over all 871 comments on #719 (2026-09-20): the first pattern found
// 260 matches ending at run 166 (2026-09-13T10:24:41Z); this one finds 296
// ending at run 174 (2026-09-14T10:20:04Z) — **36 comments missed**, and the
// newest now agrees with the figure #1339 quotes independently.
//
// What makes it worth this much comment is WHY it was invisible: the Conductor
// was genuinely down when the defect was introduced, so both patterns said
// ALERT and the monitor looked correct. The stale one was right for the wrong
// reason. Its real failure arrives on RECOVERY — it would never see the new
// comments, so it would report a growing silence forever, which is how a
// monitor becomes noise and stops being read.
//
// So the leading class admits whitespace, `>`, `#`, `*` and `_` (horizontal
// whitespace only, so `^` keeps anchoring per line), the `Conductor` word is
// optional, and the separator may be an em dash, an en dash, a hyphen, or
// absent.
//
// The phase ends on `(?![A-Za-z0-9])` rather than `\b`, and that is not
// stylistic: `_` IS a word character, so `\b` does not match between `D` and
// `_`, and `_Conductor run 200 - END_` failed while `**…END**` passed. The
// leading class already admits `_`, so accepting one emphasis marker and
// rejecting the other was an asymmetry inside the very fix for a
// too-narrow pattern. The negative lookahead still refuses `ENDED` and
// `STARTING`, which is all `\b` was doing here.
//
// `parseConductorComments` additionally reports how many
// comments matched, so a pattern that has stopped matching is visible as a
// collapsed count rather than as a confident wrong timestamp — and
// `test_the_pattern_matches_every_format_the_log_has_carried` pins all four
// spellings, including the one that got past the first revision.
//
// The separator class holds LITERAL em dash, en dash and ASCII hyphen. Those two
// non-ASCII bytes are the one fragile thing in this module: an encoding
// round-trip that mangles them — this repo has hit cp1252 corruption more than
// once — breaks the class by making it match nothing, which is the same silent
// L215 failure the pattern exists to resist. What defends against that is not a
// comment but `test_the_pattern_matches_hyphen_and_en_dash_separators`, which
// exercises all four separator spellings and fails loudly if these bytes change.
// Do not "simplify" the class to ASCII-only: the log's current format uses the em
// dash, so that edit silently stops matching every recent comment.
const CONDUCTOR_RE =
  /^[>#*_ \t]*(?:conductor[ \t]+)?run[ \t]+(\d+)[ \t]*[—–-]?[ \t]*(START|END)(?![A-Za-z0-9])/im;

const MARKER = '<!-- conductor-liveness -->';
// The history the next run reads back. Kept as one HTML-comment line so the
// rendered issue stays readable, and parsed defensively: a corrupt or absent
// block yields an empty history, which suppresses only the growth signal rather
// than failing the run.
const HISTORY_PREFIX = '<!-- conductor-liveness:history ';
const HISTORY_SUFFIX = ' -->';

const ISSUE_TITLE = 'Conductor liveness: the supervising routine may have stopped';
const ISSUE_LABELS = ['bug', 'agentic-os'];

/**
 * Hours between two timestamps, with every way the arithmetic can lie made
 * explicit rather than folded into a number.
 *
 * Returns `{ageHours: null, error}` when either side cannot be believed. The
 * `now` check is the one that matters most: an unparseable `now` makes every
 * comparison NaN, and NaN fails BOTH `< -tolerance` and `> threshold`, so a
 * silence of any length would fall through to OK. That is the fail-open this
 * module exists to close, in the only place it is not otherwise checked.
 *
 * @param {string|null|undefined} iso the measured timestamp
 * @param {string} nowIso this run's timestamp
 * @param {string} label what `iso` is, for the error text
 */
function ageInHours(iso, nowIso, label) {
  const bad = (error) => ({ ageHours: null, error });
  if (typeof iso !== 'string' || iso.trim() === '') {
    return bad(`${label} is absent or empty`);
  }
  const value = iso.trim();
  if (!ISO_8601.test(value)) {
    return bad(`${label} is not an ISO-8601 timestamp: \`${value}\``);
  }
  const then = Date.parse(value);
  if (!Number.isFinite(then)) {
    return bad(`${label} did not parse: \`${value}\``);
  }
  const now = Date.parse(nowIso);
  if (!Number.isFinite(now)) {
    return bad(`cannot establish age: run timestamp \`${nowIso}\` did not parse`);
  }
  const ageHours = Math.round(((now - then) / 3600000) * 10) / 10;
  if (ageHours < -FUTURE_TOLERANCE_HOURS) {
    return bad(`${label} is ${Math.abs(ageHours)}h in the future: \`${value}\``);
  }
  return { ageHours, error: null };
}

/**
 * Find the newest Conductor START/END comment in a comment listing.
 *
 * @param {Array<{body?:string, created_at?:string}>} comments
 * @returns {{total:number, matched:number, newest:object|null}}
 *
 * `matched` is returned so the caller can tell "the Conductor is silent" from
 * "my pattern no longer matches the log's format" — ledger L215. A run that read
 * hundreds of comments and matched none is reporting on its own regex, not on
 * the Conductor, and the two must not look alike.
 */
function parseConductorComments(comments) {
  const list = Array.isArray(comments) ? comments : [];
  let matched = 0;
  let newest = null;
  for (const c of list) {
    if (!c || typeof c.body !== 'string') continue;
    const m = CONDUCTOR_RE.exec(c.body);
    if (!m) continue;
    matched += 1;
    const at = typeof c.created_at === 'string' ? c.created_at : null;
    const candidate = {
      run: Number(m[1]),
      phase: m[2].toUpperCase(),
      at,
      url: typeof c.html_url === 'string' ? c.html_url : null,
    };
    // Order by timestamp rather than by position: the comments endpoint returns
    // oldest-first, but a caller that paginated wrongly (or sliced a page) can
    // hand over any order, and "the last element" would silently inherit that.
    const t = at === null ? NaN : Date.parse(at);
    const best = newest && newest.at !== null ? Date.parse(newest.at) : NaN;
    if (!Number.isFinite(best) || (Number.isFinite(t) && t > best)) newest = candidate;
  }
  return { total: list.length, matched, newest };
}

/** Shape one signal result. */
function _signal(name, verdict, measured, detail) {
  return { name, verdict, measured, detail };
}

/**
 * How long the Conductor Log has been silent.
 *
 * UNKNOWN (a finding) when the log could not be read, when it returned no
 * comments at all, when no comment matched the START/END pattern, or when the
 * newest match carries a timestamp that cannot be believed.
 */
function classifySilence(input, nowIso) {
  const name = 'conductor-silence';
  const readError = input && input.commentsError;
  if (readError) {
    return _signal(name, 'UNKNOWN', null, `could not read the Conductor Log: ${readError}`);
  }
  const parsed = parseConductorComments(input && input.comments);
  if (parsed.total === 0) {
    return _signal(name, 'UNKNOWN', null, 'the Conductor Log returned no comments at all');
  }
  if (parsed.matched === 0) {
    return _signal(
      name,
      'UNKNOWN',
      null,
      `no START/END comment matched in ${parsed.total} comments — the Conductor is silent, or ` +
        'the log changed format and this pattern no longer matches it (ledger L215)',
    );
  }
  const { ageHours, error } = ageInHours(
    parsed.newest.at,
    nowIso,
    'the newest Conductor comment timestamp',
  );
  if (error) return _signal(name, 'UNKNOWN', null, error);

  const where = `run ${parsed.newest.run} ${parsed.newest.phase}`;
  if (ageHours > SILENCE_ALERT_HOURS) {
    return _signal(
      name,
      'ALERT',
      ageHours,
      `${ageHours}h since ${where} — past the ${SILENCE_ALERT_HOURS}h alert threshold`,
    );
  }
  if (ageHours > SILENCE_WARN_HOURS) {
    return _signal(
      name,
      'WARN',
      ageHours,
      `${ageHours}h since ${where} — past the ${SILENCE_WARN_HOURS}h warn threshold`,
    );
  }
  return _signal(name, 'OK', ageHours, `${ageHours}h since ${where}`);
}

/**
 * How long since an `agentic-os` PR last merged — the independent second signal.
 *
 * An empty merged set is UNKNOWN rather than OK: it means the query returned
 * nothing, which says nothing about the pipeline.
 */
function classifyMergeSilence(input, nowIso) {
  const name = 'merge-silence';
  const readError = input && input.mergedError;
  if (readError) {
    return _signal(name, 'UNKNOWN', null, `could not read merged agentic-os PRs: ${readError}`);
  }
  const merged = Array.isArray(input && input.mergedPRs) ? input.mergedPRs : [];
  if (merged.length === 0) {
    return _signal(name, 'UNKNOWN', null, 'no merged agentic-os PR was returned for the window');
  }
  let newest = null;
  let newestT = NaN;
  for (const pr of merged) {
    if (!pr || typeof pr.merged_at !== 'string') continue;
    const t = Date.parse(pr.merged_at);
    if (Number.isFinite(t) && (!Number.isFinite(newestT) || t > newestT)) {
      newestT = t;
      newest = pr;
    }
  }
  if (!newest) {
    return _signal(name, 'UNKNOWN', null, 'no merged agentic-os PR carried a usable `merged_at`');
  }
  const { ageHours, error } = ageInHours(newest.merged_at, nowIso, 'the newest `merged_at`');
  if (error) return _signal(name, 'UNKNOWN', null, error);

  const where = `#${newest.number}`;
  if (ageHours > MERGE_ALERT_HOURS) {
    return _signal(
      name,
      'ALERT',
      ageHours,
      `${ageHours}h since ${where} merged — past the ${MERGE_ALERT_HOURS}h alert threshold`,
    );
  }
  if (ageHours > MERGE_WARN_HOURS) {
    return _signal(
      name,
      'WARN',
      ageHours,
      `${ageHours}h since ${where} merged — past the ${MERGE_WARN_HOURS}h warn threshold`,
    );
  }
  return _signal(name, 'OK', ageHours, `${ageHours}h since ${where} merged`);
}

/**
 * The open `agentic-os` PR count, against the worker's cap.
 *
 * Deliberately **never a finding on its own.** The pile stood at 4 — above the
 * cap of 3 — throughout the healthy window that preceded the #1339 outage, so a
 * level-triggered rule reports every ordinary day and teaches its reader to
 * ignore it. The level is context; `pr-growth` below is the finding.
 */
function classifyPrCap(input) {
  const name = 'open-pr-cap';
  const readError = input && input.openError;
  if (readError) {
    return _signal(name, 'UNKNOWN', null, `could not read open agentic-os PRs: ${readError}`);
  }
  const open = input && input.openPRs;
  if (!Number.isFinite(Number(open))) {
    return _signal(name, 'UNKNOWN', null, `open PR count is not a number: \`${String(open)}\``);
  }
  const n = Number(open);
  return _signal(
    name,
    'OK',
    n,
    n > PR_CAP
      ? `${n} open, above the landing-sweep cap of ${PR_CAP} — context only, see \`pr-growth\``
      : `${n} open, at or below the cap of ${PR_CAP}`,
  );
}

/**
 * Sustained monotonic growth in the open `agentic-os` PR pile.
 *
 * This is the signal that models the actual #1339 failure: the pile grew 4 -> 13
 * across five days and ~39 worker runs without one alert, because nobody was
 * looking at the SHAPE of the sequence. A strictly increasing run of
 * GROWTH_SAMPLES samples that ends above the cap is a finding; anything shorter,
 * flat, or falling is not.
 *
 * @param {Array<{at?:string, openPRs?:number}>} history oldest-first, current sample last
 */
function classifyGrowth(history) {
  const name = 'pr-growth';
  const samples = (Array.isArray(history) ? history : []).filter(
    (h) => h && Number.isFinite(Number(h.openPRs)),
  );
  if (samples.length < GROWTH_SAMPLES) {
    return _signal(
      name,
      'OK',
      samples.length,
      `only ${samples.length} of ${GROWTH_SAMPLES} samples needed to judge a trend — not enough ` +
        'history yet, which is not the same as no growth',
    );
  }
  const tail = samples.slice(-GROWTH_SAMPLES).map((h) => Number(h.openPRs));
  let rising = true;
  for (let i = 1; i < tail.length; i += 1) {
    if (tail[i] <= tail[i - 1]) rising = false;
  }
  const seq = tail.join(' -> ');
  if (rising && tail[tail.length - 1] > PR_CAP) {
    return _signal(
      name,
      'ALERT',
      tail[tail.length - 1],
      `open agentic-os PRs rose on every one of the last ${GROWTH_SAMPLES} runs (${seq}) and are ` +
        `above the cap of ${PR_CAP} — nothing is draining the queue`,
    );
  }
  if (rising) {
    return _signal(
      name,
      'WARN',
      tail[tail.length - 1],
      `rising on the last ${GROWTH_SAMPLES} runs (${seq}) but still at or below the cap of ${PR_CAP}`,
    );
  }
  return _signal(name, 'OK', tail[tail.length - 1], `not monotonically rising (${seq})`);
}

/**
 * Read the history block back out of the rolling issue body.
 *
 * Fails soft on purpose: a body with no block, a truncated block, or a payload
 * that is not an array of samples yields `[]`. That suppresses only the growth
 * signal — which then says so, rather than claiming no growth — and never fails
 * the run. The three fail-CLOSED signals above are what carry the outage.
 *
 * @param {string|null|undefined} body
 */
function parseHistory(body) {
  if (typeof body !== 'string') return [];
  const start = body.indexOf(HISTORY_PREFIX);
  if (start === -1) return [];
  const from = start + HISTORY_PREFIX.length;
  const end = body.indexOf(HISTORY_SUFFIX, from);
  if (end === -1) return [];
  let parsed;
  try {
    parsed = JSON.parse(body.slice(from, end));
  } catch (e) {
    return [];
  }
  if (!Array.isArray(parsed)) return [];
  return parsed
    .filter((h) => h && typeof h === 'object' && Number.isFinite(Number(h.openPRs)))
    .map((h) => ({ at: typeof h.at === 'string' ? h.at : null, openPRs: Number(h.openPRs) }))
    .slice(-HISTORY_LIMIT);
}

/** Render the history block the next run will read. */
function renderHistory(samples) {
  const trimmed = (Array.isArray(samples) ? samples : []).slice(-HISTORY_LIMIT);
  return `${HISTORY_PREFIX}${JSON.stringify(trimmed)}${HISTORY_SUFFIX}`;
}

/**
 * Fold every signal into one analysis.
 *
 * @param {{comments?:Array, commentsError?:string|null, mergedPRs?:Array,
 *          mergedError?:string|null, openPRs?:number, openError?:string|null,
 *          priorBody?:string|null, now?:string}} input
 */
function analyze(input) {
  const nowIso = (input && input.now) || new Date().toISOString();
  const prior = parseHistory(input && input.priorBody);

  const openSignal = classifyPrCap(input);
  // Only a believable count joins the history; appending an UNKNOWN would let a
  // failed read masquerade as a data point and could manufacture a rise.
  const history =
    openSignal.verdict === 'UNKNOWN'
      ? prior
      : prior.concat([{ at: nowIso, openPRs: openSignal.measured }]);

  const signals = [
    classifySilence(input, nowIso),
    classifyMergeSilence(input, nowIso),
    openSignal,
    classifyGrowth(history),
  ];

  const pick = (v) => signals.filter((s) => s.verdict === v);
  const unknown = pick('UNKNOWN');
  const alert = pick('ALERT');
  const warn = pick('WARN');

  return {
    logRef: `${LOG_OWNER}/${LOG_REPO}#${LOG_ISSUE}`,
    signals,
    unknown,
    alert,
    warn,
    ok: pick('OK'),
    history,
    // WARN counts. A supervisor six hours past a three-hour cadence is the
    // report #1339 asks for on day 1; holding out for ALERT would reproduce the
    // day-5 detection it was filed about.
    hasFinding: Boolean(unknown.length || alert.length || warn.length),
  };
}

/**
 * Pick this monitor's rolling issue out of an open-issue listing.
 *
 * `issues.listForRepo` returns **pull requests as well as issues** — they are the
 * same object in the REST model, distinguished only by a `pull_request` key.
 * Without that filter, any open PR whose body happened to contain the marker
 * would be selected as "the rolling issue", and the clean-run path would comment
 * on it and then `issues.update({state: 'closed'})` — this workflow would close
 * somebody's pull request.
 *
 * That is not a remote hazard: the marker is an HTML comment, so it is INVISIBLE
 * in a rendered PR body. A PR that documents or quotes the marker (a lessons
 * entry, a change to this file) carries it without its author ever seeing it.
 * 739 already guards this and 744 inherited the fix; the rule is repeated here
 * because the defect is in the pattern being copied, not in either instance.
 *
 * @param {Array<{body?:string, pull_request?:object}>} items an open-issue listing
 * @returns {object|null} the rolling issue, or null when there is none
 */
function findRollingIssue(items) {
  return (
    (items || []).find(
      (i) => i && !i.pull_request && typeof i.body === 'string' && i.body.includes(MARKER),
    ) || null
  );
}

/** One-line summary for `core.notice` and the recovery comment. */
function summary(a) {
  const parts = a.signals.map((s) => `${s.name}=${s.verdict}`);
  return (
    `log=${a.logRef} ${parts.join(' ')} ` +
    `unknown=${a.unknown.length} alert=${a.alert.length} warn=${a.warn.length}`
  );
}

const _ICON = { OK: '✅', WARN: '⚠️', ALERT: '🚨', UNKNOWN: '❓' };

function _row(s) {
  return `| \`${s.name}\` | ${_ICON[s.verdict] || ''} ${s.verdict} | ${s.measured === null ? '—' : s.measured} | ${s.detail} |`;
}

/**
 * Render the rolling issue body.
 *
 * @param {object} a analysis from analyze()
 * @param {string} iso generation timestamp
 */
function renderBody(a, iso) {
  const lines = [MARKER, renderHistory(a.history), ''];
  lines.push(
    'Liveness of the **Conductor** — the locally-run routine that is the only actor with ' +
      `promotion and merge authority. Its only footprint in GitHub is the comments it posts to ` +
      `${a.logRef}, so this workflow asserts the OUTCOME (are they still arriving, and is work ` +
      'still landing) rather than the health of any Actions workflow.',
    '',
    `- Generated: ${iso}`,
    `- Silence thresholds: warn ${SILENCE_WARN_HOURS}h, alert ${SILENCE_ALERT_HOURS}h ` +
      '(observed healthy cadence ~3h)',
    `- Merge thresholds: warn ${MERGE_WARN_HOURS}h, alert ${MERGE_ALERT_HOURS}h`,
    '',
    'Every signal is reported by name with its measured value. They are **not** averaged: two of ' +
      'them were flat for five days in #1339 and either alone would have caught it.',
    '',
    ..._table(['Signal', 'Verdict', 'Measured', 'Detail'], a.signals.map(_row)),
    '',
  );

  if (a.unknown.length) {
    lines.push(
      `## ❓ Could not be established (${a.unknown.length})`,
      '',
      'Not evidence that the Conductor is dead, and **not** evidence that it is alive. An ' +
        'unreadable log, an empty comment page, a pattern that no longer matches the log format ' +
        '(ledger L215), or a timestamp that cannot be believed. Reported rather than assumed ' +
        'healthy — collapsing "could not check" into "checked, fine" is the fail-open this ' +
        'workflow exists to close (ledger L02).',
      '',
    );
  }

  if (a.alert.length || a.warn.length) {
    lines.push(
      '## What to do',
      '',
      'The Conductor runs on an operator workstation and is reachable only by @clarkemoyer — ' +
        'nothing in this repository can restart it. If `conductor-silence` is past its threshold:',
      '',
      '1. Check whether the routine is still running on that host, and restart it if not.',
      `2. Until it is back, every green, resolved, 0-behind draft PR is unpromotable: ` +
        "`gh pr ready` plus an enqueue is the Conductor's alone, so cloud workers cannot drain " +
        'the queue however many runs they spend on it.',
      '3. A worker run that finds 0 actionable PRs **and** this issue open should escalate the ' +
        "supervisor's absence rather than re-verify an unchanged cohort (AGENTS.md § the landing " +
        'sweep).',
      '',
    );
  }

  lines.push(
    '---',
    '',
    '_Managed by 747. Repo - Conductor Liveness. Refs #1339. Reads one public issue and this ' +
      "repository's own PRs with the ambient `GITHUB_TOKEN` — deliberately no Key Vault " +
      'credential and no environment, so a liveness check cannot be blocked by a gate or go dark ' +
      'on a dead PAT it might need to report._',
  );
  return lines.join('\n');
}

function _table(header, rows) {
  return [`| ${header.join(' | ')} |`, `| ${header.map(() => '---').join(' | ')} |`, ...rows];
}

module.exports = {
  LOG_OWNER,
  LOG_REPO,
  LOG_ISSUE,
  SILENCE_WARN_HOURS,
  SILENCE_ALERT_HOURS,
  MERGE_WARN_HOURS,
  MERGE_ALERT_HOURS,
  PR_CAP,
  GROWTH_SAMPLES,
  HISTORY_LIMIT,
  FUTURE_TOLERANCE_HOURS,
  ISO_8601,
  CONDUCTOR_RE,
  MARKER,
  HISTORY_PREFIX,
  ISSUE_TITLE,
  ISSUE_LABELS,
  ageInHours,
  parseConductorComments,
  classifySilence,
  classifyMergeSilence,
  classifyPrCap,
  classifyGrowth,
  parseHistory,
  renderHistory,
  analyze,
  findRollingIssue,
  summary,
  renderBody,
};
