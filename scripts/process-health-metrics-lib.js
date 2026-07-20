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
 *   pipelineRuns         [{name, conclusion}]     data-pipeline Actions runs in
 *                        the pipeline window (which workflows count is the
 *                        workflow's policy, not the lib's).
 */
function computeMetrics(input) {
  const nowIso = input.nowIso;
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
    },
    dataPipeline: {
      runs,
      success,
      successRate: runs ? round3(success / runs) : null,
      byWorkflow,
    },
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
    `| agentic-os closed (${m.windowDays.throughput}d) | ${fmt(ao.closed)} | ${delta(ao.closed, pao.closed)} |`,
  );
  lines.push(
    `| Data-pipeline success (${m.windowDays.pipeline}d) | ${pct(dp.successRate)} (${dp.success}/${dp.runs}) | ${delta(dp.successRate === null ? null : round(dp.successRate * 100, 1), pdp.successRate === null || pdp.successRate === undefined ? null : round(pdp.successRate * 100, 1), 1)} |`,
  );
  lines.push('');
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
