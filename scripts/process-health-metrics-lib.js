'use strict';

// Pure aggregation + trend + render logic for workflow 739 (Process Health
// Metrics Report). The workflow's github-script step `require`s this module and
// keeps all I/O (paginated REST reads, posting the #719 comment, writing the
// artifact) in the YAML; only the deterministic math and Markdown live here so
// tests/workflow-logic/test_739_process_health.py exercises exactly what ships.
//
// Design (see #756, child of the process-assurance epic #752):
//   - Each weekly run posts a NEW comment on the Conductor log (#719) so the
//     thread itself is the visible trend history (no committed file, no push to
//     `main`, no environment gate).
//   - Every comment embeds its metrics as a hidden data block; the next run reads
//     the most recent such block to render deltas ("trends over absolute
//     numbers", per the issue). extractPreviousMetrics() is that reader.
//   - Times are passed in (`nowIso`) rather than read from the clock so the
//     functions stay pure and unit-testable.

const MARKER = '<!-- process-health-metrics-report -->';
const DATA_OPEN = '<!-- phm-data:';
const DATA_CLOSE = ' -->';
const LOG_ISSUE = 719;
const THROUGHPUT_WINDOW_DAYS = 30;
const PIPELINE_WINDOW_DAYS = 7;

// A conductor run that has posted a START and no END for longer than this is
// treated as dead (#970). Runs 60/61/62 took 20-30 minutes START->END, so two
// hours is several times the observed worst case — deliberately loose, because
// the cost of a false "dead" is a wrong entry in a weekly report and the cost of
// a false "alive" is the failure this metric exists to catch going unseen.
const DEAD_RUN_THRESHOLD_HOURS = 2;

// The Conductor's own run headers on #719, e.g. `## Run 63 — START (…)`.
// Matched against the FIRST line of a comment only (see findDeadConductorRuns):
// the header is the first line by construction, and anchoring there means prose
// in a later comment that quotes a header cannot forge a START or — worse —
// clear a real one with a forged END.
const RUN_HEADER = /^##[ \t]+Run[ \t]+(\d+)[ \t]+[—–-][ \t]+(START|END)\b/;

const DAY_MS = 24 * 3600 * 1000;
const HOUR_MS = 3600 * 1000;

function round1(n) {
  return Math.round(n * 10) / 10;
}

// Mean of a numeric array, rounded to one decimal; null for an empty set so
// "no data" renders as "—" instead of a misleading 0.
function mean(nums) {
  if (!nums || nums.length === 0) return null;
  return round1(nums.reduce((a, b) => a + b, 0) / nums.length);
}

function ageDays(fromIso, nowIso) {
  return Math.max(0, (new Date(nowIso) - new Date(fromIso)) / DAY_MS);
}

/**
 * Aggregate gathered REST data into the weekly metrics object. All inputs are
 * plain arrays/numbers the workflow assembled from paginated reads:
 *
 * @param {object} input
 *   nowIso               ISO timestamp of the run (report time; trend anchor).
 *   smokeOpen            [{created_at}]           open `smoke-failure` issues.
 *   smokeClosedRecent    [{created_at, closed_at}] `smoke-failure` issues closed
 *                        within the throughput window.
 *   claimedOpen          [{created_at}]           open `claimed` issues.
 *   agenticOpen          number                   open `agentic-os` issues.
 *   agenticClosedRecent  number                   `agentic-os` issues closed in
 *                        the throughput window.
 *   readyWaiting         [{number, readySinceIso, parked}] PRs that are green,
 *                        clean and still in draft (#900). See buildReadyWaiting.
 *   pipelineRuns         [{name, conclusion}]     data-pipeline Actions runs in
 *                        the pipeline window (which workflows count is the
 *                        workflow's policy, not the lib's).
 *   logComments          [{created_at, body}]     #719's comments across ALL
 *                        fetched pages, for the dead-run scan (#970). Omit /
 *                        non-array ⇒ `conductorRuns.assessed: false` rather
 *                        than a reassuring zero.
 *   deadRunThresholdHours  optional override of DEAD_RUN_THRESHOLD_HOURS.
 */
function computeMetrics(input) {
  const nowIso = input && input.nowIso;
  // Fail fast on a missing/invalid report time rather than silently emitting
  // NaN ages and a "Generated: undefined" report — the workflow always passes a
  // valid ISO stamp, so a bad value means a caller bug worth surfacing loudly.
  if (!nowIso || Number.isNaN(new Date(nowIso).getTime())) {
    throw new Error('computeMetrics: input.nowIso must be a valid ISO timestamp');
  }
  const smokeOpen = input.smokeOpen || [];
  const smokeClosedRecent = input.smokeClosedRecent || [];
  const claimedOpen = input.claimedOpen || [];
  const pipelineRuns = input.pipelineRuns || [];

  const smokeAges = smokeOpen.map((i) => ageDays(i.created_at, nowIso));
  const ttc = smokeClosedRecent.map((i) => ageDays(i.created_at, i.closed_at));
  const claimAges = claimedOpen.map((i) => ageDays(i.created_at, nowIso));

  // Group pipeline runs by workflow name, plus an overall roll-up.
  const per = new Map();
  let runs = 0;
  let success = 0;
  for (const r of pipelineRuns) {
    runs++;
    const ok = r.conclusion === 'success';
    if (ok) success++;
    const k = r.name || '(unnamed)';
    if (!per.has(k)) per.set(k, { runs: 0, success: 0 });
    const s = per.get(k);
    s.runs++;
    if (ok) s.success++;
  }
  const byWorkflow = [...per.entries()]
    .map(([name, s]) => ({
      name,
      runs: s.runs,
      successRate: s.runs ? round3(s.success / s.runs) : null,
    }))
    .sort((a, b) => a.name.localeCompare(b.name));

  return {
    generatedAt: nowIso,
    windowDays: { throughput: THROUGHPUT_WINDOW_DAYS, pipeline: PIPELINE_WINDOW_DAYS },
    smokeFailures: {
      open: smokeOpen.length,
      meanAgeDays: mean(smokeAges),
      closed: smokeClosedRecent.length,
      meanTimeToCloseDays: mean(ttc),
    },
    claims: {
      open: claimedOpen.length,
      meanAgeDays: mean(claimAges),
    },
    agenticOs: {
      open: Number(input.agenticOpen || 0),
      closed: Number(input.agenticClosedRecent || 0),
      // The band the Conductor routine actually means (#922). `open` counts the
      // whole `agentic-os` topic label — epics, machine-managed rolling issues,
      // human-blocked items and durable findings included — so it can never sit
      // in "5-15 issues a sandboxed agent can execute", and eight consecutive
      // runs of trimming failed to move it. `ready` counts `agent-ready`, which
      // is that sentence's actual population. Both are reported: `open` is still
      // the honest size of the programme, `ready` is the one to steer by.
      ready: Number(input.readyOpen || 0),
    },
    readyWaiting: buildReadyWaiting(input.readyWaiting, nowIso),
    // Conductor runs that started and never ended (#970). Weekly cadence means
    // this reports the PATTERN ("we lost 2 runs this week"), not the incident —
    // a stated limitation, not an oversight.
    conductorRuns: buildConductorRuns(input, nowIso),
    dataPipeline: {
      runs,
      success,
      successRate: runs ? round3(success / runs) : null,
      byWorkflow,
    },
  };
}

/**
 * "Green, clean, and stuck in draft" (#900).
 *
 * Every health signal the Agentic OS emits reads GREEN while finished agent work
 * sits unlanded. 739 counts backlog and open claims — both of which look
 * *healthier* as work moves into finished-but-unmerged PRs. 740 alerts on
 * failures; nothing here fails. Validate Repository, Phantom Revert Guard and
 * Copilot all pass — passing is the state being complained about. And a draft PR
 * is indistinguishable from genuine work-in-progress everywhere it is displayed.
 *
 * Four consecutive landing runs rediscovered this and wrote it up as a prose
 * LESSON rather than as a metric. This is the metric.
 *
 * `candidates` is [{ number, readySinceIso, parked }] — the workflow decides
 * which PRs qualify (draft AND mergeable_state clean AND required checks green);
 * the lib stays pure. Age runs from the moment a PR became LANDABLE, not from
 * `created_at`: a PR is only "waiting" once there is nothing left to do to it.
 *
 * PARKED is the load-bearing exclusion. Two different things look identical
 * here: a PR parked for a stated reason (its own description sequences external
 * provisioning before merge) and one that is simply forgotten. Counting the
 * first alerts forever and trains everyone to ignore the metric — the failure
 * mode of every alert that cannot express "known, and correct". Parked PRs are
 * still LISTED, just not counted.
 */
function buildReadyWaiting(candidates, nowIso) {
  const all = candidates || [];
  const counted = all.filter((c) => !c.parked);
  const parked = all.filter((c) => c.parked);
  const ages = counted.map((c) => round1(ageDays(c.readySinceIso, nowIso)));

  let oldest = null;
  if (counted.length) {
    let idx = 0;
    for (let i = 1; i < counted.length; i++) if (ages[i] > ages[idx]) idx = i;
    oldest = { number: counted[idx].number, ageDays: ages[idx] };
  }

  return {
    count: counted.length,
    meanAgeDays: mean(ages),
    // null (renders "—") rather than 0 for an empty set: "nothing is waiting"
    // and "something has been waiting zero days" are different facts.
    maxAgeDays: ages.length ? Math.max(...ages) : null,
    oldest,
    numbers: counted.map((c) => c.number),
    parked: parked.map((c) => c.number),
  };
}

function round3(n) {
  return Math.round(n * 1000) / 1000;
}

/**
 * Conductor runs that started and never ended (#970).
 *
 * A conductor run is the privileged supervisor: it approves gates, merges PRs,
 * refreshes the public status feed and grooms the backlog. Run 63 posted a START
 * on #719 at 2026-08-01T01:05:45Z and died — no END, no commit, no PR, no label
 * change — and nothing noticed for three hours. The visible artifacts (a START
 * comment and a plan) make the thread *look* like the run happened, which is why
 * every other signal stayed green.
 *
 * Both halves are already published by the Conductor in a fixed format, so this
 * needs no new instrumentation, only a reader. Two deliberate choices:
 *
 *  - **Age comes from the comment's `created_at`, never from the timestamp in
 *    the header.** The Conductor fuzzes that one to the minute-tens digit
 *    (`## Run 63 — START (2026-08-01T01:0xZ)`), so it is not a parseable
 *    instant; `created_at` is exact and set by GitHub.
 *  - **Only the first line of a comment is examined**, so a post-mortem quoting
 *    `## Run 63 — END` in its prose cannot retire a run that never ended.
 *
 * @param {Array}  comments  [{created_at, body}] — #719's comments, oldest→newest,
 *                           ACROSS ALL PAGES. A START and its END routinely land
 *                           on different pages (run 61, 2026-07-31); pairing per
 *                           page reports every boundary-split run as dead.
 * @param {string} nowIso    report time.
 * @param {number} [thresholdHours]  override for DEAD_RUN_THRESHOLD_HOURS.
 */
function findDeadConductorRuns(comments, nowIso, thresholdHours) {
  // Exported, so this is a public entry point and cannot lean on
  // computeMetrics having already validated the clock. An invalid `nowIso`
  // makes every age NaN, and `NaN < threshold` is false — so every unmatched
  // START would be reported dead, with `ageHours` serialising to null. A false
  // alarm about the supervisor is worse than the silence this replaces, so
  // fail fast exactly as computeMetrics does.
  if (!nowIso || Number.isNaN(new Date(nowIso).getTime())) {
    throw new Error('findDeadConductorRuns: nowIso must be a valid ISO timestamp');
  }
  // An unusable override falls back to the default rather than throwing: this
  // is an optional knob on a health report, and killing the weekly run over it
  // would cost more than it saves. `Number('')` is 0 (every in-flight run reads
  // dead) and `Number('2h')` is NaN (the `< threshold` test never fires, so
  // every unmatched START reads dead) — both fail in the alarming direction,
  // and neither is loud. The effective value is returned as `thresholdHours`
  // and rendered in the report, so a fallback announces itself.
  //
  // `Number()` alone is not enough to validate with, which is the trap: it maps
  // '', '   ', null, false and [] all to 0 — a *finite, non-negative* number
  // that passes a naive `Number.isFinite(n) && n >= 0` check and then reports
  // every in-flight run dead. Narrow the accepted TYPES first, then the value.
  let threshold = DEAD_RUN_THRESHOLD_HOURS;
  if (
    typeof thresholdHours === 'number' ||
    (typeof thresholdHours === 'string' && thresholdHours.trim() !== '')
  ) {
    const requested = Number(thresholdHours);
    if (Number.isFinite(requested) && requested >= 0) threshold = requested;
  }
  const started = new Map(); // run number -> earliest START created_at
  const ended = new Set();

  for (const c of comments || []) {
    const body = c && c.body;
    if (typeof body !== 'string') continue;
    const firstLine = body.split('\n', 1)[0].trim();
    const m = RUN_HEADER.exec(firstLine);
    if (!m) continue;
    const run = Number(m[1]);
    if (m[2] === 'END') {
      ended.add(run);
      continue;
    }
    // A re-posted START keeps the earliest one: the run began when it said it
    // began, and taking the latest would let a repost reset the clock.
    const at = c.created_at;
    if (!at || Number.isNaN(new Date(at).getTime())) continue;
    const prevStart = started.get(run);
    if (!prevStart || new Date(at) < new Date(prevStart)) started.set(run, at);
  }

  const now = new Date(nowIso).getTime();
  const dead = [];
  for (const [run, startedAt] of started) {
    if (ended.has(run)) continue;
    const ageHours = Math.max(0, (now - new Date(startedAt).getTime()) / HOUR_MS);
    // Younger than the threshold is a run still in flight, not a dead one.
    if (ageHours < threshold) continue;
    dead.push({ run, startedAt, ageHours: round1(ageHours) });
  }
  dead.sort((a, b) => a.run - b.run);

  return {
    assessed: true,
    thresholdHours: threshold,
    // The denominator, printed rather than implied: "0 dead out of 0 runs seen"
    // is an unread log, not a healthy week, and the two must not look alike.
    observed: started.size,
    count: dead.length,
    dead,
  };
}

// `assessed: false` is the honest reading when #719 could not be read at all —
// the comment fetch is wrapped in a try/catch so a transient API failure cannot
// wedge the weekly report, and a swallowed read must not surface as "0 dead".
// That is exactly the "presence mistaken for validity" shape the ledger is about.
function buildConductorRuns(input, nowIso) {
  if (!Array.isArray(input.logComments)) {
    return {
      assessed: false,
      thresholdHours: DEAD_RUN_THRESHOLD_HOURS,
      observed: 0,
      count: null,
      dead: [],
    };
  }
  return findDeadConductorRuns(input.logComments, nowIso, input.deadRunThresholdHours);
}

// Scan issue-comment bodies (oldest→newest as returned by the REST list) and
// return the metrics object from the most recent hidden data block, or null on
// the first run / when none is parseable. Malformed blocks are skipped, never
// thrown, so one bad comment can't wedge every future run.
function extractPreviousMetrics(comments) {
  let latest = null;
  for (const c of comments || []) {
    const body = c && c.body;
    if (!body || body.indexOf(DATA_OPEN) === -1) continue;
    const start = body.indexOf(DATA_OPEN) + DATA_OPEN.length;
    const end = body.indexOf(DATA_CLOSE, start);
    if (end === -1) continue;
    const raw = body.slice(start, end).trim();
    try {
      latest = JSON.parse(raw);
    } catch (_e) {
      // skip malformed block; keep the last good one
    }
  }
  return latest;
}

// Trend cell for a scalar: arrow reflects the numeric direction of change only
// (up/down/flat) — not "good/bad", which depends on the metric — plus the signed
// delta. "—" when there is no prior value to compare against.
function delta(cur, prev, digits) {
  if (cur === null || cur === undefined || prev === null || prev === undefined) return '—';
  const d = cur - prev;
  const shown = digits ? round(d, digits) : d;
  if (d > 0) return `▲ +${shown}`;
  if (d < 0) return `▼ ${shown}`;
  return '▬ 0';
}

function round(n, digits) {
  const f = Math.pow(10, digits);
  return Math.round(n * f) / f;
}

function fmt(n) {
  return n === null || n === undefined ? '—' : String(n);
}

function pct(rate) {
  return rate === null || rate === undefined ? '—' : `${round(rate * 100, 1)}%`;
}

/**
 * Render the weekly Markdown report for #719. Deterministic given its inputs so
 * a test can assert exact content. `prev` is the previous run's metrics (or null
 * on the first run); trend columns compare against it. The report ends with a
 * hidden data block carrying `metrics` for the next run to diff.
 */
function renderReport(metrics, prev, opts) {
  const o = opts || {};
  const m = metrics;
  const p = prev || {};
  const sf = m.smokeFailures;
  const psf = p.smokeFailures || {};
  const cl = m.claims;
  const pcl = p.claims || {};
  const ao = m.agenticOs;
  const pao = p.agenticOs || {};
  // A report written before #900 has no readyWaiting block; default it so the
  // delta reads '—' instead of throwing (extractPreviousMetrics tolerates
  // missing keys and this must too).
  const rw = m.readyWaiting || {
    count: 0,
    numbers: [],
    parked: [],
    maxAgeDays: null,
    oldest: null,
  };
  const prw = p.readyWaiting || {};
  // A report predating #970 has no conductorRuns block; default it so the delta
  // reads '—' instead of throwing.
  const cr = m.conductorRuns || { assessed: false, count: null, dead: [], observed: 0 };
  const pcr = p.conductorRuns || {};
  const dp = m.dataPipeline;
  const pdp = p.dataPipeline || {};

  const lines = [];
  lines.push(MARKER);
  lines.push('');
  lines.push('## 📈 Process health — weekly report');
  lines.push('');
  lines.push(`- Generated: ${m.generatedAt}`);
  lines.push(
    `- Windows: ${m.windowDays.throughput}d throughput/close, ${m.windowDays.pipeline}d data-pipeline`,
  );
  lines.push(
    prev
      ? '- Trend column compares against the previous weekly report.'
      : '- First report — no trend baseline yet.',
  );
  lines.push('');
  lines.push('| Metric | Value | Trend |');
  lines.push('| --- | --- | --- |');
  lines.push(`| Open smoke failures | ${fmt(sf.open)} | ${delta(sf.open, psf.open)} |`);
  lines.push(
    `| Mean open smoke-failure age (d) | ${fmt(sf.meanAgeDays)} | ${delta(sf.meanAgeDays, psf.meanAgeDays, 1)} |`,
  );
  lines.push(
    `| Smoke failures closed (${m.windowDays.throughput}d) | ${fmt(sf.closed)} | ${delta(sf.closed, psf.closed)} |`,
  );
  lines.push(
    `| Mean time-to-close (d) | ${fmt(sf.meanTimeToCloseDays)} | ${delta(sf.meanTimeToCloseDays, psf.meanTimeToCloseDays, 1)} |`,
  );
  lines.push(`| Open claims | ${fmt(cl.open)} | ${delta(cl.open, pcl.open)} |`);
  lines.push(
    `| Mean open-claim age (d) | ${fmt(cl.meanAgeDays)} | ${delta(cl.meanAgeDays, pcl.meanAgeDays, 1)} |`,
  );
  lines.push(`| Open agentic-os backlog | ${fmt(ao.open)} | ${delta(ao.open, pao.open)} |`);
  lines.push(
    `| **Ready queue (agent-ready, band 5-15)** | ${fmt(ao.ready)} | ${delta(ao.ready, pao.ready)} |`,
  );
  lines.push(
    `| **PRs ready but unlanded** | ${fmt(rw.count)}${rw.oldest ? ` (oldest ${rw.oldest.ageDays}d)` : ''} | ${delta(rw.count, prw.count)} |`,
  );
  lines.push(
    `| **Conductor runs that died (START, no END)** | ${
      cr.assessed ? `${fmt(cr.count)} of ${cr.observed} seen` : 'not assessed'
    } | ${delta(cr.count, pcr.count)} |`,
  );
  lines.push(
    `| agentic-os closed (${m.windowDays.throughput}d) | ${fmt(ao.closed)} | ${delta(ao.closed, pao.closed)} |`,
  );
  lines.push(
    `| Data-pipeline success (${m.windowDays.pipeline}d) | ${pct(dp.successRate)} (${dp.success}/${dp.runs}) | ${delta(dp.successRate === null ? null : round(dp.successRate * 100, 1), pdp.successRate === null || pdp.successRate === undefined ? null : round(pdp.successRate * 100, 1), 1)} |`,
  );
  lines.push('');

  // "Ready but unlanded" (#900). Rendered as its own callout rather than only a
  // table cell: a count nobody can act on is what the previous four landing runs
  // already had. Naming the PR numbers is the whole point.
  if (rw.count) {
    const oldestBit = rw.oldest
      ? ` — oldest **#${rw.oldest.number}** at ${rw.oldest.ageDays}d`
      : '';
    lines.push(
      `> **${rw.count} PR${rw.count === 1 ? '' : 's'} green, clean and waiting on a human` +
        `${oldestBit}.** ${rw.numbers.map((n) => `#${n}`).join(', ')}`,
    );
    if (rw.maxAgeDays !== null && rw.maxAgeDays >= 1) {
      lines.push('>');
      lines.push(
        '> Nothing is failing and nothing is blocked — these need promoting out of draft. ' +
          'Every other signal in this report reads green while they sit.',
      );
    }
    if (rw.parked.length) {
      lines.push('>');
      lines.push(`> Excluded as deliberately parked: ${rw.parked.map((n) => `#${n}`).join(', ')}.`);
    }
    lines.push('');
  }

  // Dead conductor runs (#970). Named, dated and aged — "2 runs died" that does
  // not say WHICH is a number nobody can investigate a week later.
  if (cr.assessed && cr.count) {
    lines.push(
      `> **${cr.count} conductor run${cr.count === 1 ? '' : 's'} started and never ended** ` +
        `(no END comment ${cr.thresholdHours}h+ after START): ` +
        `${cr.dead.map((d) => `run ${d.run} (START ${d.startedAt}, ${d.ageHours}h ago)`).join('; ')}.`,
    );
    lines.push('>');
    lines.push(
      '> A dead run approves no gates, merges no PRs and refreshes no feed — while its START ' +
        'comment leaves the thread looking like it ran. This report is weekly, so it detects the ' +
        'pattern, not the incident.',
    );
    lines.push('');
  } else if (!cr.assessed) {
    lines.push(
      `> Conductor run START/END pairing was **not assessed** this week — #${LOG_ISSUE}'s comments ` +
        'could not be read. Absence of a finding here is not evidence of a healthy week.',
    );
    lines.push('');
  }

  if (dp.byWorkflow.length) {
    lines.push('<details><summary>Data-pipeline runs by workflow</summary>');
    lines.push('');
    lines.push('| Workflow | Runs | Success rate |');
    lines.push('| --- | --- | --- |');
    for (const w of dp.byWorkflow) {
      lines.push(`| ${w.name} | ${w.runs} | ${pct(w.successRate)} |`);
    }
    lines.push('');
    lines.push('</details>');
    lines.push('');
  }
  lines.push(
    '> Arrows show the numeric direction of change only, not good/bad (lower age/backlog and higher ' +
      'success rate are the healthy directions).',
  );
  lines.push('');
  lines.push(
    '_Deferred (dependency-gated): gate-wait durations for 726/502 (after #749), richer claim ages ' +
      '(once #751 lands), and ffcadmin rendering of this feed (#723)._',
  );
  if (o.runUrl) {
    lines.push('');
    lines.push(`[run](${o.runUrl})`);
  }
  lines.push('');
  lines.push('_Managed by 739. Repo - Process Health Metrics Report._');
  lines.push('');
  lines.push(`${DATA_OPEN}${JSON.stringify(metrics)}${DATA_CLOSE}`);
  return lines.join('\n');
}

module.exports = {
  MARKER,
  LOG_ISSUE,
  THROUGHPUT_WINDOW_DAYS,
  PIPELINE_WINDOW_DAYS,
  DEAD_RUN_THRESHOLD_HOURS,
  mean,
  ageDays,
  computeMetrics,
  findDeadConductorRuns,
  extractPreviousMetrics,
  delta,
  renderReport,
};
