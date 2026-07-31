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
const LOCKFILE_PATH = 'package-lock.json';
// Lockfiles for the other package managers. Their presence is why "no
// package-lock.json" must not read as "broken": a pnpm repo is correctly
// without one (#771), and the npm-shaped audit is the thing that is wrong there.
const ALT_LOCKFILES = ['pnpm-lock.yaml', 'yarn.lock'];
const MARKER = '<!-- fleet-security-audit-coverage -->';
const ISSUE_TITLE = 'Fleet security-audit coverage gap: Node repos with no vulnerability detection';
const ISSUE_LABELS = ['security', 'priority: high', 'agentic-os'];

// Lockfile states, in the order the report presents them.
// Broken — `npm ci` cannot run, so the audit runs nothing and reports clean:
const LOCK_BROKEN_STATES = ['out-of-sync', 'unresolvable', 'missing'];
// Not a finding — either nothing to audit, or nothing was measured:
const LOCK_BENIGN_STATES = ['resolves', 'no-dependencies', 'other-lockfile', 'unverified'];

/**
 * Classify fleet repos by whether dependency-vulnerability detection is wired.
 *
 * Detection needs BOTH halves and they are not interchangeable:
 *   - `.github/workflows/security-audit.yml` — the thing that runs.
 *   - an `audit:high` entry in package.json `scripts` — the thing it runs.
 *
 * @param {Array<{repo:string, hasPackageJson?:boolean, hasWorkflow?:boolean,
 *                hasScript?:boolean, cron?:string|null, error?:string|null,
 *                lockfile?:{state:string, detail?:string}|null}>} entries
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
 *   - lockfiles: the L06 half (#889) — see classifyLockResolution. `broken`
 *                repos install nothing when their audit runs, so a covered repo
 *                can still be reporting on an empty tree.
 *
 * `hasGap` is true for partial + uncovered + lockfiles.broken, so the rolling
 * issue still means one thing precisely: "a repo with dependencies is not
 * actually being scanned." A broken lockfile belongs in that sentence and a
 * missing one does not sit outside it — both end with `npm ci` failing and the
 * audit reporting clean. What stays out of `hasGap` is everything not measured
 * (unverified lockfiles, unreadable repos) and everything with nothing to
 * measure (no dependencies, a pnpm/yarn repo), because a red issue that cannot
 * be acted on is one nobody reads. Cron distribution likewise reports alongside
 * (see summarizeCrons) without setting hasGap — scheduling hygiene and missing
 * detection are different problems.
 */
function analyze(entries) {
  const covered = [];
  const partial = [];
  const uncovered = [];
  const notNode = [];
  const unreadable = [];
  const lockfiles = { resolves: [], broken: [], unverified: [], skipped: [] };
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
    _bucketLockfile(lockfiles, e);
  }
  covered.sort((a, b) => a.repo.localeCompare(b.repo));
  partial.sort((a, b) => a.repo.localeCompare(b.repo));
  uncovered.sort();
  notNode.sort();
  unreadable.sort((a, b) => a.repo.localeCompare(b.repo));
  lockfiles.resolves.sort();
  for (const key of ['broken', 'unverified', 'skipped']) {
    lockfiles[key].sort((a, b) => a.repo.localeCompare(b.repo));
  }

  const nodeRepos = covered.length + partial.length + uncovered.length;
  return {
    covered,
    partial,
    uncovered,
    notNode,
    unreadable,
    lockfiles,
    nodeRepos,
    crons: summarizeCrons(covered),
    hasGap: partial.length + uncovered.length + lockfiles.broken.length > 0,
  };
}

/**
 * Sort one Node repo's lockfile verdict into the reporting buckets.
 *
 * An entry with no `lockfile` field lands in `unverified`, never in `resolves`:
 * a run that did not measure must not read as a run that measured and found
 * nothing wrong. That is the same substitution — not-measured for
 * measured-and-fine — that ledger L06 is about.
 */
function _bucketLockfile(lockfiles, e) {
  const lock = e.lockfile;
  const state = lock && lock.state ? String(lock.state) : '';
  const detail = lock && lock.detail ? String(lock.detail) : '';
  if (!state) {
    lockfiles.unverified.push({ repo: e.repo, state: 'not-measured', detail: 'not measured' });
    return;
  }
  if (state === 'resolves') {
    lockfiles.resolves.push(e.repo);
    return;
  }
  if (LOCK_BROKEN_STATES.includes(state)) {
    lockfiles.broken.push({ repo: e.repo, state, detail });
    return;
  }
  if (state === 'no-dependencies' || state === 'other-lockfile') {
    lockfiles.skipped.push({ repo: e.repo, state, detail });
    return;
  }
  // `unverified`, and any state a future workflow revision introduces before
  // this module learns about it. Unknown must fail benign — the alternative is
  // a repo appearing on a remediation list because of a spelling.
  lockfiles.unverified.push({
    repo: e.repo,
    state: LOCK_BENIGN_STATES.includes(state) ? state : 'unverified',
    detail: detail || (LOCK_BENIGN_STATES.includes(state) ? '' : `unrecognized state '${state}'`),
  });
}

/**
 * Group covered repos by the cron expression their audit runs on.
 *
 * #838 asks that the backfill stagger the schedule rather than pointing 34 more
 * repos at `17 6 * * *`. This surfaces the distribution so that is checkable at
 * a glance. It is informational: at fleet scale some repos sharing a minute is
 * unavoidable, so "no two repos share a cron" would be a rule that never goes
 * green — a permanently-red alert is one nobody reads. `staggered` reports the
 * one unambiguous case: two or more repos whose cron is actually KNOWN, all on
 * a single expression, means staggering was never applied at all.
 *
 * The known-cron qualifier is load-bearing. Repos whose audit is dispatch-only
 * (or whose schedule could not be parsed) land in the `unknown` group, and
 * `distinct` deliberately does not count that group — so testing the whole
 * covered set instead would read a fleet of entirely-unknown crons as "all on
 * one cron" and warn that staggering was never applied, when in truth nothing
 * is known about the schedule at all. Not-measured must never render as
 * measured-and-bad; that is the same error this workflow exists to catch, one
 * level down.
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
  const knownCount = groups.filter((g) => g.cron !== 'unknown').reduce((n, g) => n + g.count, 0);
  return {
    groups,
    distinct,
    knownCount,
    staggered: !(knownCount >= 2 && distinct <= 1),
  };
}

/**
 * Classify what `npm ci --dry-run` said about a repo's lockfile (ledger L06).
 *
 * The question this answers is NOT "does a lockfile exist" — 741 and 742 already
 * answered that, and it is the wrong question. A `package-lock.json` that exists
 * but is out of sync with package.json makes `npm ci` exit non-zero on every
 * nightly audit run, so the audit installs nothing, scans nothing, and reports
 * clean. Presence read as validity, the ledger's L06.
 *
 * Deciding it by running the real `npm ci --dry-run` rather than re-implementing
 * npm's sync check is deliberate: the check would otherwise be a second, subtly
 * different opinion about semver satisfaction, and the audit would trust the
 * copy while the fleet runs the original. Only the classification of npm's
 * verdict lives here, and every string it keys on was observed from npm 10.9.7
 * rather than read from documentation.
 *
 * @param {number} exitCode  npm's exit status (124 = the step's `timeout`).
 * @param {string} output    npm's combined stdout+stderr.
 * @returns {{state: string, detail: string}}
 *
 * States:
 *   - `resolves`     exit 0 — the lockfile installs, so the nightly audit runs.
 *   - `out-of-sync`  EUSAGE + "in sync" — npm names the drift (`Missing: x from
 *                    lock file`, `Invalid: lock file's x@1.3.0 does not satisfy
 *                    x@1.1.3`). Definite: this repo's audit fails every night.
 *   - `unresolvable` the lockfile is in sync but the tree cannot be installed at
 *                    all (ETARGET — a requested version does not exist). Same
 *                    consequence, different cause, so it is not folded into
 *                    out-of-sync: "run npm install" is not the fix here.
 *   - `missing`      npm found no lockfile (the caller normally detects this
 *                    before spending an npm run; kept so the mapping is total).
 *   - `unverified`   registry/network/timeout failure. NOT a finding — the
 *                    lockfile may be perfect. A transient 502 rendering as
 *                    "broken lockfile" would be exactly the not-measured-as-
 *                    measured error this audit exists to catch, one level up.
 */
function classifyLockResolution(exitCode, output) {
  const text = String(output || '');
  const code = Number(exitCode);
  if (code === 0) return { state: 'resolves', detail: '' };
  if (code === 124) {
    return { state: 'unverified', detail: 'npm ci --dry-run timed out' };
  }

  // Registry/transport trouble is checked FIRST: npm reports these with the same
  // exit code as a real sync failure, and a rate-limited run must never put a
  // repo on a remediation list.
  // ENOTCACHED belongs here and is not obvious: `npm ci` consults the registry
  // to build the ideal tree whenever the lockfile does NOT already satisfy
  // package.json, so a network-less runner turns every genuinely out-of-sync
  // repo into ENOTCACHED. Reading that as `out-of-sync` would be right by
  // accident; reading it as unverified is right on purpose.
  const transport =
    /\b(ENOTFOUND|EAI_AGAIN|ETIMEDOUT|ECONNRESET|ECONNREFUSED|ERR_SOCKET_TIMEOUT|ENOTCACHED|EOFFLINE|EAGAIN|E429|E5\d\d)\b/.exec(
      text,
    );
  if (transport) {
    return { state: 'unverified', detail: `registry/network failure (${transport[1]})` };
  }

  if (/can only install with an existing package-lock\.json/i.test(text)) {
    return { state: 'missing', detail: 'npm found no package-lock.json' };
  }

  if (/can only install packages when your package\.json and package-lock\.json/i.test(text)) {
    // npm lists the offending packages on `Missing:` / `Invalid:` lines; carry a
    // few so the report says WHICH dependency drifted, not merely that one did.
    const detail = _npmDriftLines(text);
    return {
      state: 'out-of-sync',
      detail: detail || 'package.json and package-lock.json disagree',
    };
  }

  const target = /No matching version found for (\S+?)\.?$/m.exec(text);
  if (/\bETARGET\b/.test(text) || target) {
    return {
      state: 'unresolvable',
      detail: target ? `no published version matches ${target[1]}` : 'requested version not found',
    };
  }
  if (/\bE404\b/.test(text)) {
    return { state: 'unresolvable', detail: 'a dependency is not published (404)' };
  }

  return { state: 'unverified', detail: _firstNpmError(text) || `npm exited ${code}` };
}

/**
 * Decide what "no package-lock.json" means, given the alt-lockfile probes.
 *
 * A repo without `package-lock.json` is only a finding if it is an npm repo. So
 * the workflow probes for the other package managers' lockfiles — and those
 * probes can themselves fail. Copilot caught this on #910: treating a
 * rate-limited probe as "no pnpm-lock.yaml either" reports the repo as
 * `missing`, a remediation finding, on evidence that was never collected.
 * That is the same not-measured-as-measured substitution this audit exists to
 * catch, so it gets the same answer: `unverified`.
 *
 * @param {Array<{path:string, status:string}>} probes  status is `OK`, `ABSENT`,
 *   or `ERROR:<msg>` — the three outcomes the workflow's `fetch_raw` returns.
 *
 * A found lockfile wins over a failed probe: if pnpm-lock.yaml is confirmed
 * present, a failed yarn.lock probe changes nothing about the answer.
 */
function classifyAbsentLockfile(probes) {
  const list = Array.isArray(probes) ? probes : [];
  const found = list.find((p) => p && p.status === 'OK');
  if (found) return { state: 'other-lockfile', detail: String(found.path || '') };
  const failed = list.filter((p) => p && String(p.status || '').startsWith('ERROR:'));
  if (failed.length) {
    const which = failed.map((p) => p.path).join(', ');
    return {
      state: 'unverified',
      detail: `no package-lock.json, and the ${which} probe failed — package manager not determined`,
    };
  }
  return { state: 'missing', detail: 'no package-lock.json in the repo' };
}

/** The `Missing:` / `Invalid:` lines npm prints under an EUSAGE sync failure. */
function _npmDriftLines(text) {
  const lines = String(text)
    .split(/\r?\n/)
    .map((l) => l.replace(/^npm (?:ERR!|error)\s*/, '').trim())
    .filter((l) => /^(Missing|Invalid):/.test(l));
  if (!lines.length) return '';
  const shown = lines.slice(0, 3).join('; ');
  return lines.length > 3 ? `${shown} (+${lines.length - 3} more)` : shown;
}

/** First substantive npm error line, for an outcome this module does not name. */
function _firstNpmError(text) {
  for (const raw of String(text).split(/\r?\n/)) {
    const line = raw.replace(/^npm (?:ERR!|error)\s*/, '').trim();
    if (!line || /^A complete log of this run/.test(line)) continue;
    return line.slice(0, 160);
  }
  return '';
}

/**
 * Does this package.json declare any dependency at all?
 *
 * A Node repo with an empty dependency tree has nothing to audit and cannot
 * carry a meaningful lockfile, so reporting it as a broken lockfile would be a
 * finding nobody can act on. Peer/optional/bundled deps count: any of them means
 * `npm ci` has work to do.
 */
function hasDependencies(rawPackageJson) {
  let pkg;
  try {
    pkg = JSON.parse(String(rawPackageJson || ''));
  } catch {
    return false;
  }
  if (!pkg || typeof pkg !== 'object') return false;
  const fields = ['dependencies', 'devDependencies', 'optionalDependencies', 'peerDependencies'];
  return fields.some((f) => pkg[f] && typeof pkg[f] === 'object' && Object.keys(pkg[f]).length > 0);
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
  const locks = analysis.lockfiles || { resolves: [], broken: [], unverified: [], skipped: [] };
  lines.push(
    `- Lockfiles: ${locks.resolves.length} resolve, **${locks.broken.length} do not**, ` +
      `${locks.unverified.length} unverified, ${locks.skipped.length} n/a`,
  );
  lines.push('');
  lines.push(
    'This rolling issue auto-closes on the next run where every Node repo in the fleet is ' +
      'covered and no lockfile is broken. **Unverified lockfiles do not hold it open** — a ' +
      'registry failure is not evidence of a gap, so a run can close this issue with lockfiles ' +
      'it could not measure. Check the unverified count below before reading a close as ' +
      '"the fleet is clean".',
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
  lines.push(`## Lockfile does not resolve — the audit scans nothing (${locks.broken.length})`);
  lines.push('');
  lines.push(
    '_`npm ci` exits non-zero in these repos, so the nightly audit installs nothing and reports ' +
      'clean. A repo can sit in **Covered** above and still be here — coverage is the workflow ' +
      'being wired, this is the workflow being able to run (ledger L06)._',
  );
  lines.push('');
  if (locks.broken.length) {
    lines.push('| Repo | State | What `npm ci --dry-run` said | Fix |');
    lines.push('| --- | --- | --- | --- |');
    for (const b of locks.broken) {
      lines.push(`| ${b.repo} | \`${b.state}\` | ${b.detail || '—'} | ${_lockFix(b.state)} |`);
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
  if (locks.unverified.length) {
    lines.push(`## Lockfile unverified (${locks.unverified.length})`);
    lines.push('');
    lines.push(
      '_The resolution check did not complete (registry failure, timeout, or not run). ' +
        'These lockfiles are NOT asserted to be either good or broken._',
    );
    lines.push('');
    for (const u of locks.unverified) {
      lines.push(`- ${u.repo}: ${u.detail || u.state}`);
    }
    lines.push('');
  }
  if (locks.skipped.length) {
    lines.push(`## Lockfile n/a (${locks.skipped.length})`);
    lines.push('');
    lines.push('_Nothing for `npm ci` to do, or the repo is not on npm._');
    lines.push('');
    for (const s of locks.skipped) {
      const why =
        s.state === 'other-lockfile'
          ? `uses ${s.detail || 'another package manager'} — the npm-shaped audit does not fit (#771)`
          : 'package.json declares no dependencies';
      lines.push(`- ${s.repo}: ${why}`);
    }
    lines.push('');
  }
  lines.push('_Managed by 741. Repo - Fleet Security Audit Coverage. Refs #838, #889._');
  return lines.join('\n');
}

/** The remediation that matches a broken-lockfile state. */
function _lockFix(state) {
  if (state === 'out-of-sync') return '`npm install` and commit the lockfile';
  if (state === 'missing') return '`npm install` to generate `package-lock.json`';
  return 'correct the dependency range in package.json';
}

module.exports = {
  AUDIT_WORKFLOW_PATH,
  AUDIT_SCRIPT,
  LOCKFILE_PATH,
  ALT_LOCKFILES,
  LOCK_BROKEN_STATES,
  LOCK_BENIGN_STATES,
  MARKER,
  ISSUE_TITLE,
  ISSUE_LABELS,
  analyze,
  summarizeCrons,
  extractCron,
  hasAuditScript,
  hasDependencies,
  classifyLockResolution,
  classifyAbsentLockfile,
  renderBody,
};
