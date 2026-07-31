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

const DAY_MS = 24 * 3600 * 1000;

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
  mean,
  ageDays,
  computeMetrics,
  extractPreviousMetrics,
  delta,
  renderReport,
};
