'use strict';

// Pure classification + report logic for workflow 741 (Fleet Security Audit
// Coverage). The workflow's github-script step `require`s this module, so
// testing these functions (tests/workflow-logic/test_741_fleet_audit_coverage.py)
// tests the exact logic that ships. I/O — listing repos, fetching package.json
// and the workflow file, opening/closing the rolling issue — stays in the
// workflow; only the decision (which repos are unmonitored) and the issue body
// live here.
//
// Background (#838): 34 of the 44 Node apps in the FFC-EX fleet had no
// dependency-vulnerability detection at all, and nothing was measuring that —
// which is why #822's scope was estimated at 13 repos when the real number was
// 38. This audit re-derives the fleet every run instead of trusting a number
// carried between sessions.

const AUDIT_WORKFLOW_PATH = '.github/workflows/security-audit.yml';
const AUDIT_SCRIPT = 'audit:high';
const MARKER = '<!-- fleet-security-audit-coverage -->';
const ISSUE_TITLE = 'Fleet security-audit coverage gap: Node repos with no vulnerability detection';
const ISSUE_LABELS = ['security', 'priority: high', 'agentic-os'];

/**
 * Classify fleet repos by whether dependency-vulnerability detection is wired.
 *
 * Detection needs BOTH halves and they are not interchangeable:
 *   - `.github/workflows/security-audit.yml` — the thing that runs.
 *   - an `audit:high` entry in package.json `scripts` — the thing it runs.
 *
 * @param {Array<{repo:string, hasPackageJson?:boolean, hasWorkflow?:boolean,
 *                hasScript?:boolean, cron?:string|null, error?:string|null}>} entries
 *   One record per fleet repo.
 *
 * Buckets:
 *   - covered:   Node app with workflow AND script → monitored.
 *   - partial:   Node app with exactly one half → WORSE than uncovered, because
 *                the repo looks wired. A workflow without the script fails on
 *                every run ("Missing script: audit:high"); a script with no
 *                workflow never runs at all and reports nothing. Both are gaps.
 *   - uncovered: Node app with neither → silent, the #838 population.
 *   - notNode:   no package.json → no dependency tree, correctly out of scope.
 *                Adding an audit there would only manufacture red (#838).
 *   - unreadable: fetch errored → NOT a gap; coverage can't be asserted either
 *                way, so guessing would be worse than reporting the failure.
 *
 * `hasGap` is true only for partial+uncovered, so the rolling issue means one
 * thing precisely: "a repo with dependencies is unmonitored." Cron distribution
 * is reported alongside (see summarizeCrons) but deliberately does NOT set
 * hasGap — scheduling hygiene and missing detection are different problems, and
 * folding them together would blur what a red issue is telling you.
 */
function analyze(entries) {
  const covered = [];
  const partial = [];
  const uncovered = [];
  const notNode = [];
  const unreadable = [];
  for (const e of entries || []) {
    if (e.error) {
      unreadable.push({ repo: e.repo, error: String(e.error) });
      continue;
    }
    if (!e.hasPackageJson) {
      notNode.push(e.repo);
      continue;
    }
    const wf = Boolean(e.hasWorkflow);
    const sc = Boolean(e.hasScript);
    if (wf && sc) {
      covered.push({ repo: e.repo, cron: e.cron || null });
    } else if (wf || sc) {
      partial.push({ repo: e.repo, hasWorkflow: wf, hasScript: sc });
    } else {
      uncovered.push(e.repo);
    }
  }
  covered.sort((a, b) => a.repo.localeCompare(b.repo));
  partial.sort((a, b) => a.repo.localeCompare(b.repo));
  uncovered.sort();
  notNode.sort();
  unreadable.sort((a, b) => a.repo.localeCompare(b.repo));

  const nodeRepos = covered.length + partial.length + uncovered.length;
  return {
    covered,
    partial,
    uncovered,
    notNode,
    unreadable,
    nodeRepos,
    crons: summarizeCrons(covered),
    hasGap: partial.length + uncovered.length > 0,
  };
}

/**
 * Group covered repos by the cron expression their audit runs on.
 *
 * #838 asks that the backfill stagger the schedule rather than pointing 34 more
 * repos at `17 6 * * *`. This surfaces the distribution so that is checkable at
 * a glance. It is informational: at fleet scale some repos sharing a minute is
 * unavoidable, so "no two repos share a cron" would be a rule that never goes
 * green — a permanently-red alert is one nobody reads. `staggered` reports the
 * one unambiguous case: more than one audited repo, all on a single cron, means
 * staggering was never applied at all.
 *
 * Returns groups sorted by size (largest pile-up first), then cron.
 */
function summarizeCrons(covered) {
  const byCron = new Map();
  for (const c of covered || []) {
    const key = c.cron || 'unknown';
    if (!byCron.has(key)) byCron.set(key, []);
    byCron.get(key).push(c.repo);
  }
  const groups = [...byCron.entries()]
    .map(([cron, repos]) => ({ cron, repos: repos.slice().sort(), count: repos.length }))
    .sort((a, b) => b.count - a.count || a.cron.localeCompare(b.cron));
  const distinct = groups.filter((g) => g.cron !== 'unknown').length;
  return {
    groups,
    distinct,
    staggered: !((covered || []).length >= 2 && distinct <= 1),
  };
}

/**
 * Extract the first `cron:` expression from a workflow file's raw text.
 *
 * Deliberately a regex and not a YAML parse: the workflow step already has the
 * raw bytes in hand, and the only field needed is the schedule. Returns null
 * when the file has no schedule (dispatch-only copies exist in the fleet).
 */
function extractCron(raw) {
  if (!raw) return null;
  const m = /^\s*-?\s*cron:\s*['"]?([^'"#\n]+?)['"]?\s*(?:#.*)?$/m.exec(String(raw));
  return m ? m[1].trim() : null;
}

/**
 * Does a package.json (raw text) declare the `audit:high` script?
 *
 * Parses rather than greps: a bare `"audit:high"` substring can appear in a
 * comment-like string or another field, and a repo that merely mentions the
 * name must not read as covered.
 */
function hasAuditScript(rawPackageJson) {
  if (!rawPackageJson) return false;
  let pkg;
  try {
    pkg = JSON.parse(String(rawPackageJson));
  } catch {
    return false;
  }
  return Boolean(pkg && pkg.scripts && typeof pkg.scripts[AUDIT_SCRIPT] === 'string');
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
    `Coverage audit of dependency-vulnerability detection across the FFC-EX fleet. ` +
      `A repo counts as covered only when it has BOTH \`${AUDIT_WORKFLOW_PATH}\` and an ` +
      `\`${AUDIT_SCRIPT}\` entry in its package.json \`scripts\`.`,
  );
  lines.push('');
  lines.push(`- Generated: ${timestamp}`);
  lines.push(
    `- Node repos audited: ${analysis.nodeRepos} ` +
      `(covered ${analysis.covered.length}, partial ${analysis.partial.length}, ` +
      `uncovered ${analysis.uncovered.length})`,
  );
  lines.push('');
  lines.push(
    'This rolling issue auto-closes on the next run where every Node repo in the fleet is covered.',
  );
  lines.push('');
  lines.push(`## Uncovered — no detection at all (${analysis.uncovered.length})`);
  lines.push('');
  lines.push(
    analysis.uncovered.length ? analysis.uncovered.map((r) => `- [ ] ${r}`).join('\n') : '_none_',
  );
  lines.push('');
  lines.push(`## Partial — looks wired but does not report (${analysis.partial.length})`);
  lines.push('');
  if (analysis.partial.length) {
    lines.push('| Repo | `security-audit.yml` | `audit:high` script | Effect |');
    lines.push('| --- | --- | --- | --- |');
    for (const p of analysis.partial) {
      const effect = p.hasWorkflow
        ? 'workflow fails every run (missing script)'
        : 'script never runs (no workflow)';
      lines.push(
        `| ${p.repo} | ${p.hasWorkflow ? 'yes' : 'no'} | ${p.hasScript ? 'yes' : 'no'} | ${effect} |`,
      );
    }
  } else {
    lines.push('_none_');
  }
  lines.push('');
  lines.push(`## Covered (${analysis.covered.length})`);
  lines.push('');
  lines.push(analysis.covered.length ? analysis.covered.map((c) => c.repo).join(', ') : '_none_');
  lines.push('');
  if (analysis.covered.length) {
    lines.push('### Schedule distribution');
    lines.push('');
    if (!analysis.crons.staggered) {
      lines.push(
        '> ⚠️ Every audited repo runs on the same cron — the schedule was never staggered (#838).',
      );
      lines.push('');
    }
    lines.push('| Cron | Repos |');
    lines.push('| --- | --- |');
    for (const g of analysis.crons.groups) {
      lines.push(`| \`${g.cron}\` | ${g.count} |`);
    }
    lines.push('');
  }
  if (analysis.notNode.length) {
    lines.push(`## No package.json (${analysis.notNode.length})`);
    lines.push('');
    lines.push('_Out of scope — no dependency tree to audit._');
    lines.push('');
    lines.push(analysis.notNode.join(', '));
    lines.push('');
  }
  if (analysis.unreadable.length) {
    lines.push(`## Unreadable (${analysis.unreadable.length})`);
    lines.push('');
    lines.push('_Fetch failed (private/rate-limit/transient); coverage not asserted either way._');
    lines.push('');
    for (const u of analysis.unreadable) {
      lines.push(`- ${u.repo}: ${u.error}`);
    }
    lines.push('');
  }
  lines.push('_Managed by 741. Repo - Fleet Security Audit Coverage. Refs #838._');
  return lines.join('\n');
}

module.exports = {
  AUDIT_WORKFLOW_PATH,
  AUDIT_SCRIPT,
  MARKER,
  ISSUE_TITLE,
  ISSUE_LABELS,
  analyze,
  summarizeCrons,
  extractCron,
  hasAuditScript,
  renderBody,
};
