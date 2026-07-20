'use strict';

// Pure comparison + report logic for workflow 738 (Fleet Smoke Engine Drift
// Audit). The workflow's github-script step `require`s this module, so testing
// these functions (tests/workflow-logic/test_738_fleet_smoke_drift.py) tests the
// exact logic that ships. I/O — listing repos, fetching file contents, hashing,
// opening/closing the rolling issue — stays in the workflow; only the decision
// (which repos have drifted) and the issue body live here.

const CANONICAL_REPO = 'FreeForCharity/FFC-IN-FFC_Single_Page_Template';
const SMOKE_PATH = '.github/workflows/post-deploy-smoke.yml';
const MARKER = '<!-- fleet-smoke-engine-drift-audit -->';
const ISSUE_TITLE = 'Fleet smoke engine drift detected';
const ISSUE_LABELS = ['smoke-failure', 'priority: high', 'agentic-os'];

/**
 * Classify fleet repos by whether their post-deploy-smoke.yml matches the
 * canonical hash.
 *
 * @param {string} canonicalHash  sha256 hex of the canonical smoke workflow.
 * @param {Array<{repo:string, hash?:string|null, error?:string|null}>} entries
 *   One record per fleet repo:
 *     - hash: sha256 hex string when the file was fetched, else null/absent.
 *     - error: set when the fetch itself failed (private/rate-limit/transient).
 *
 * Buckets:
 *   - divergent: HAS the engine but at a different byte-identity  → real drift.
 *   - matching:  byte-identical to canonical.
 *   - missing:   no post-deploy-smoke.yml (fetch ok, file absent) → NOT drift;
 *                the rollout is deliberately incremental (see #738/#743), so a
 *                repo without the engine yet is informational, not a failure.
 *   - unreadable: fetch errored → NOT drift (can't assert identity either way).
 *
 * `hasDrift` is true ONLY when a deployed copy differs from canonical, so the
 * rolling issue fires on the racing-variant class this audit exists to catch
 * (2026-07-19) without false-alarming on not-yet-onboarded repos.
 */
function analyze(canonicalHash, entries) {
  const matching = [];
  const divergent = [];
  const missing = [];
  const unreadable = [];
  for (const e of entries || []) {
    if (e.error) {
      unreadable.push({ repo: e.repo, error: String(e.error) });
    } else if (!e.hash) {
      missing.push(e.repo);
    } else if (e.hash === canonicalHash) {
      matching.push(e.repo);
    } else {
      divergent.push({ repo: e.repo, hash: e.hash });
    }
  }
  matching.sort();
  missing.sort();
  divergent.sort((a, b) => a.repo.localeCompare(b.repo));
  unreadable.sort((a, b) => a.repo.localeCompare(b.repo));
  return { matching, divergent, missing, unreadable, hasDrift: divergent.length > 0 };
}

function shortHash(h) {
  return h ? String(h).slice(0, 12) : 'none';
}

/**
 * Render the rolling-issue body (Markdown). Deterministic given its inputs so a
 * test can assert exact content; the marker comment lets the workflow find the
 * issue again on the next run.
 */
function renderBody(analysis, canonicalHash, timestamp) {
  const canonicalUrl = `https://github.com/${CANONICAL_REPO}/blob/main/${SMOKE_PATH}`;
  const lines = [];
  lines.push(MARKER);
  lines.push('');
  lines.push(
    `Byte-identity audit of \`${SMOKE_PATH}\` across the FFC fleet against the ` +
      `canonical copy in [\`${CANONICAL_REPO}\`](${canonicalUrl}) (\`main\`).`,
  );
  lines.push('');
  lines.push(`- Generated: ${timestamp}`);
  lines.push(`- Canonical SHA-256: \`${canonicalHash}\``);
  lines.push('');
  lines.push(
    'This rolling issue auto-closes on the next run where every deployed copy is byte-identical.',
  );
  lines.push('');
  lines.push(`## Divergent (${analysis.divergent.length})`);
  lines.push('');
  if (analysis.divergent.length) {
    lines.push('| Repo | SHA-256 (short) |');
    lines.push('| --- | --- |');
    for (const d of analysis.divergent) {
      lines.push(`| ${d.repo} | \`${shortHash(d.hash)}\` |`);
    }
  } else {
    lines.push('_none_');
  }
  lines.push('');
  lines.push(`## Matching (${analysis.matching.length})`);
  lines.push('');
  lines.push(analysis.matching.length ? analysis.matching.join(', ') : '_none_');
  lines.push('');
  if (analysis.missing.length) {
    lines.push(`## Not yet deployed (${analysis.missing.length})`);
    lines.push('');
    lines.push(
      '_Informational — no `post-deploy-smoke.yml` yet (incremental rollout); not counted as drift._',
    );
    lines.push('');
    lines.push(analysis.missing.join(', '));
    lines.push('');
  }
  if (analysis.unreadable.length) {
    lines.push(`## Unreadable (${analysis.unreadable.length})`);
    lines.push('');
    lines.push('_Fetch failed (private/rate-limit/transient); not counted as drift._');
    lines.push('');
    for (const u of analysis.unreadable) {
      lines.push(`- ${u.repo}: ${u.error}`);
    }
    lines.push('');
  }
  lines.push('_Managed by 738. Repo - Fleet Smoke Engine Drift Audit._');
  return lines.join('\n');
}

module.exports = {
  CANONICAL_REPO,
  SMOKE_PATH,
  MARKER,
  ISSUE_TITLE,
  ISSUE_LABELS,
  analyze,
  renderBody,
  shortHash,
};
