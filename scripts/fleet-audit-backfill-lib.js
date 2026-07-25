'use strict';

// Pure planning + file-editing logic for workflow 742 (Fleet Security Audit
// Backfill). The workflow's steps `require` this module, so testing these
// functions (tests/workflow-logic/test_742_fleet_audit_backfill.py) tests the
// exact logic that ships. I/O — listing repos, fetching files, cloning, opening
// PRs — stays in the workflow; the decisions (which repos are actionable, what
// cron each gets, and the exact bytes written into a charity's package.json)
// live here.
//
// Background (#838): 34 of the 44 Node apps in the FFC-EX fleet had no
// dependency-vulnerability detection at all. Workflow 741 measures that gap;
// this one closes it. The issue asks for a hub workflow rather than 34
// hand-made PRs specifically so the 35th repo provisioned next month is covered
// without anyone remembering.
//
// Two constraints from #840's review bind everything here:
//
//   1. NEVER create a `partial` repo. `security-audit.yml` without the
//      `audit:high` script fails every run with "Missing script: audit:high";
//      the script without the workflow never runs at all. Both halves land in
//      one commit, or the repo is skipped and reported.
//   2. Stagger the cron. All 10 covered repos sit on `17 6 * * *`, so pointing
//      34 more at that minute is the wrong default.
//
// The governing bias throughout: REFUSE RATHER THAN MANGLE. Every edit is
// verified after the fact, and any repo whose files do not match a shape this
// module fully understands is reported as blocked instead of guessed at. A
// missed repo shows up red on 741's next sweep; a corrupted package.json in a
// charity's repo does not announce itself.

const AUDIT_WORKFLOW_PATH = '.github/workflows/security-audit.yml';
const AUDIT_SCRIPT = 'audit:high';
const AUDIT_SCRIPT_CMD = 'npm audit --omit=dev --audit-level=high';
const LOCKFILE_PATH = 'package-lock.json';

// Source of truth for the workflow file, per #838: "do not write a new one".
// Fetched at run time rather than vendored into this repo so the fleet cannot
// drift from the canonical copy the way a checked-in duplicate would.
const CANONICAL_REPO = 'FreeForCharity/FFC-IN-FFC_Single_Page_Template';

// The one minute already carrying every covered repo in the fleet. Excluded
// from the stagger so a backfill never deepens the pile it was asked to avoid.
const CROWDED_MINUTE = 17;

const BRANCH = 'chore/security-audit-backfill';
const COMMIT_MESSAGE = 'ci: add security audit (npm audit high/critical, daily)';

/**
 * Deterministic minute-of-hour for a repo's audit schedule.
 *
 * Deterministic is the load-bearing property, not uniformity: re-dispatching
 * the backfill must compute the SAME cron for a repo, otherwise every re-run
 * produces a fresh diff on repos that are already done and the workflow stops
 * being idempotent. So this is a hash of the repo name, never a counter, a
 * random draw, or an index into the target list (all three of which shift when
 * the fleet's membership changes).
 *
 * The owner prefix is stripped so `FreeForCharity/FFC-EX-x` and `FFC-EX-x`
 * agree — the callers disagree about which form they hold.
 *
 * Collisions are expected and fine: 44 repos across 59 usable minutes will
 * share some. #838 asks for staggering, not for uniqueness, and 741 reports the
 * distribution.
 */
function stableMinute(repoName) {
  const name = String(repoName || '').replace(/^.*\//, '');
  // FNV-1a, 32-bit. Chosen for being short, dependency-free and stable across
  // Node versions — a hash whose value could change between runtimes would
  // silently break the idempotency above.
  let h = 0x811c9dc5;
  for (let i = 0; i < name.length; i++) {
    h ^= name.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  const slots = [];
  for (let m = 0; m < 60; m++) {
    if (m !== CROWDED_MINUTE) slots.push(m);
  }
  return slots[h % slots.length];
}

/** Cron expression for a repo: daily in the 06:00 UTC hour, staggered by name. */
function cronFor(repoName) {
  return `${stableMinute(repoName)} 6 * * *`;
}

/**
 * Sanity-check the canonical workflow before copying it to 34 repos.
 *
 * The rollout amplifies whatever it is handed, so a template that has drifted
 * out of the shape this backfill assumes must stop the run rather than ship. In
 * particular, if SPT ever moves to pnpm (#771) the npm-specific steps stop
 * transferring, and a blind copy would leave the fleet with an audit that fails
 * on every run — indistinguishable at a glance from the vulnerability it exists
 * to report.
 *
 * Returns an array of problem strings; empty means usable.
 */
function validateTemplate(raw) {
  const text = String(raw || '');
  const problems = [];
  if (!text.trim()) {
    return ['canonical security-audit.yml is empty'];
  }
  const crons = text.match(/^\s*-?\s*cron:/gm) || [];
  if (crons.length === 0) {
    problems.push('no `cron:` schedule — a dispatch-only copy would never report on its own');
  } else if (crons.length > 1) {
    problems.push(`${crons.length} \`cron:\` lines — cannot tell which one to stagger`);
  }
  // The leading boundary is load-bearing: `pnpm` CONTAINS `npm`, so an
  // unanchored /npm\s+run/ happily matches `pnpm run audit:high` and the pnpm
  // migration (#771) this check exists to catch would sail through. Excluding a
  // preceding word character is what makes the check mean "npm".
  if (!/(?:^|[^\w-])npm\s+run\s+audit:high/m.test(text)) {
    problems.push('does not run `npm run audit:high` — the script half would not be exercised');
  }
  if (!/(?:^|[^\w-])npm\s+ci\b/m.test(text)) {
    problems.push('does not run `npm ci` — no dependency tree would be installed to audit');
  }
  if (!/permissions:\s*\n\s*contents:\s*read/.test(text)) {
    problems.push('missing `permissions: contents: read` — refusing to grant a fleet audit more');
  }
  return problems;
}

/**
 * Produce the per-repo workflow file: the canonical text with its schedule
 * replaced by this repo's staggered cron.
 *
 * Only the schedule is rewritten — everything else is copied byte-for-byte, so
 * 738-style byte-identity drift audits stay meaningful and a future change to
 * the canonical file needs no change here.
 *
 * Returns { content } or { error } — never a half-substituted file.
 */
function renderAuditWorkflow(templateRaw, cron) {
  const problems = validateTemplate(templateRaw);
  if (problems.length) {
    return { error: `canonical template unusable: ${problems.join('; ')}` };
  }
  if (!/^\d{1,2} \d{1,2} \* \* \*$/.test(String(cron || ''))) {
    return { error: `refusing to write malformed cron ${JSON.stringify(cron)}` };
  }
  const minute = String(cron).split(' ')[0];
  const hour = String(cron).split(' ')[1];
  const stamp = `${hour.padStart(2, '0')}:${minute.padStart(2, '0')}`;

  let replaced = 0;
  const content = String(templateRaw).replace(/^(\s*-?\s*)cron:.*$/gm, (_m, lead) => {
    replaced += 1;
    return `${lead}cron: '${cron}' # daily, ${stamp} UTC`;
  });
  if (replaced !== 1) {
    return { error: `expected exactly 1 cron line to rewrite, rewrote ${replaced}` };
  }
  // Self-check with the SAME parser 741 uses to read coverage back, so a repo
  // this workflow just wrote cannot report a cron the audit disagrees about.
  const readBack = /^\s*-?\s*cron:\s*['"]?([^'"#\n]+?)['"]?\s*(?:#.*)?$/m.exec(content);
  if (!readBack || readBack[1].trim() !== cron) {
    return { error: `cron read-back mismatch: wrote ${cron}, parsed ${readBack && readBack[1]}` };
  }
  return { content };
}

/**
 * Add the `audit:high` script to a package.json, preserving the file's existing
 * formatting.
 *
 * Deliberately a surgical text insertion rather than JSON.parse →
 * JSON.stringify: a reserialize would reformat the whole file, turning a
 * two-line change into a whole-file diff in a charity's repo and fighting
 * whatever key order and indentation the template established.
 *
 * The entry is inserted FIRST inside `scripts` (not last) because the following
 * line already exists, so the comma placement is unambiguous — appending would
 * have to find and amend the previous entry's line ending.
 *
 * Returns { changed: true, content } on success, or { changed: false, reason }
 * for every shape this function will not touch. Callers must treat a reason as
 * "skip and report this repo", never as "write it anyway".
 */
function addAuditScript(rawPackageJson, cmd) {
  const command = cmd || AUDIT_SCRIPT_CMD;
  const text = String(rawPackageJson || '');
  let pkg;
  try {
    pkg = JSON.parse(text);
  } catch (e) {
    return { changed: false, reason: `package.json does not parse: ${e.message}` };
  }
  if (!pkg || typeof pkg !== 'object' || Array.isArray(pkg)) {
    return { changed: false, reason: 'package.json is not a JSON object' };
  }
  if (pkg.scripts && typeof pkg.scripts[AUDIT_SCRIPT] === 'string') {
    return { changed: false, reason: 'already-present' };
  }
  if (!pkg.scripts || typeof pkg.scripts !== 'object' || Array.isArray(pkg.scripts)) {
    // Every FFC template repo has a scripts block; a repo without one is
    // unusual enough that a human should look rather than have a block invented
    // for it at a guessed location.
    return { changed: false, reason: 'no `scripts` object to extend' };
  }

  const open = /(^|\n)([ \t]*)"scripts"[ \t]*:[ \t]*\{/.exec(text);
  if (!open) {
    // Parsed as having scripts, but the literal key is not locatable — e.g. an
    // escaped or unusually encoded key. Refuse rather than edit blind.
    return { changed: false, reason: 'could not locate the `"scripts": {` block in the raw text' };
  }
  const insertAt = open.index + open[0].length;
  const baseIndent = open[2];

  // Match the siblings' indentation so the result stays prettier-stable in the
  // target repo (whose CI may check formatting); fall back to base + 2 when the
  // block is empty and there are no siblings to copy.
  const after = text.slice(insertAt);
  const isEmptyBlock = /^\s*\}/.test(after);
  if (!isEmptyBlock && !/^[\r\n]/.test(after)) {
    // `"scripts": { "build": "…" }` — the first entry shares the opening
    // brace's line. Inserting here yields valid JSON but re-flows a line this
    // change does not own, which can fail the target repo's own format check
    // and make a working backfill look like it broke the repo. Preserving
    // formatting is an invariant of this function, so report instead. No repo
    // in the fleet is written this way today.
    return {
      changed: false,
      reason:
        'first `scripts` entry shares the opening brace line — cannot insert without re-flowing a line this change does not own',
    };
  }
  const sibling = /^[\r\n]+([ \t]+)"/.exec(after);
  const indent = sibling ? sibling[1] : `${baseIndent}  `;

  const entry = `${JSON.stringify(AUDIT_SCRIPT)}: ${JSON.stringify(command)}`;
  const insertion = isEmptyBlock ? `\n${indent}${entry}\n${baseIndent}` : `\n${indent}${entry},`;
  const rebuilt = isEmptyBlock
    ? text.slice(0, insertAt) + insertion + after.replace(/^\s*\}/, '}')
    : text.slice(0, insertAt) + insertion + after;

  const verdict = verifyOnlyScriptAdded(text, rebuilt, command);
  if (!verdict.ok) {
    return { changed: false, reason: verdict.reason };
  }
  return { changed: true, content: rebuilt };
}

/**
 * Confirm an edited package.json differs from the original in exactly one way:
 * the `audit:high` script was added.
 *
 * This is the guard that makes the surgical text edit above safe to run
 * unattended across 34 repos. A text insertion at a mislocated offset can still
 * produce valid JSON (dropping a sibling, nesting the entry one level too deep,
 * clobbering a value), so parsing the result is not sufficient — the whole
 * document has to be compared. Reads as belt-and-braces precisely because it is:
 * the cost of a false pass is a broken build in a charity's repo.
 */
function verifyOnlyScriptAdded(beforeRaw, afterRaw, cmd) {
  const command = cmd || AUDIT_SCRIPT_CMD;
  let before;
  let after;
  try {
    before = JSON.parse(String(beforeRaw));
  } catch (e) {
    return { ok: false, reason: `original package.json does not parse: ${e.message}` };
  }
  try {
    after = JSON.parse(String(afterRaw));
  } catch (e) {
    return { ok: false, reason: `edited package.json does not parse: ${e.message}` };
  }
  if (after.scripts?.[AUDIT_SCRIPT] !== command) {
    return { ok: false, reason: `edited package.json lacks the ${AUDIT_SCRIPT} script` };
  }
  // Strip the one intended addition, then require byte-equal canonical JSON.
  const stripped = JSON.parse(JSON.stringify(after));
  delete stripped.scripts[AUDIT_SCRIPT];
  if (!before.scripts) {
    if (Object.keys(stripped.scripts).length === 0) delete stripped.scripts;
  }
  if (stableStringify(stripped) !== stableStringify(before)) {
    return { ok: false, reason: 'edit changed more than the audit:high script' };
  }
  return { ok: true };
}

/** Key-order-independent JSON serialization, for comparing two documents. */
function stableStringify(value) {
  if (Array.isArray(value)) {
    return `[${value.map(stableStringify).join(',')}]`;
  }
  if (value && typeof value === 'object') {
    return `{${Object.keys(value)
      .sort()
      .map((k) => `${JSON.stringify(k)}:${stableStringify(value[k])}`)
      .join(',')}}`;
  }
  return JSON.stringify(value === undefined ? null : value);
}

/**
 * Decide which fleet repos this backfill will touch, and what it will do to
 * each.
 *
 * @param {Array<{repo:string, hasPackageJson?:boolean, hasWorkflow?:boolean,
 *                hasScript?:boolean, hasLockfile?:boolean,
 *                defaultBranch?:string|null, error?:string|null}>} candidates
 *
 * Buckets:
 *   - targets: actionable. `needs` names the missing halves, so a `partial`
 *     repo gets only the half it lacks and an uncovered one gets both.
 *   - skipped: already covered, or not a Node app — correct no-ops, listed so a
 *     dry run shows the whole fleet was considered rather than silently
 *     narrowed.
 *   - blocked: a gap this workflow will not close unattended. Reported so it
 *     stays visible on 741's sweep instead of looking done.
 *
 * The `no package-lock.json` block matters more than it looks. The canonical
 * workflow runs `npm ci`, which EXITS NONZERO without a lockfile — so wiring
 * such a repo would produce a permanently red Security Audit that reports
 * nothing about vulnerabilities while looking exactly like a repo that has
 * them. That is a worse state than uncovered, and it is the same
 * looks-monitored-but-isn't failure #838 was opened about. pnpm/yarn repos need
 * a different audit shape (#771), which is a separate piece of work.
 */
function planTargets(candidates) {
  const targets = [];
  const skipped = [];
  const blocked = [];

  for (const c of candidates || []) {
    const repo = c.repo;
    if (c.error) {
      blocked.push({ repo, reason: `unreadable: ${c.error}` });
      continue;
    }
    if (!c.hasPackageJson) {
      skipped.push({ repo, reason: 'no package.json — no dependency tree to audit' });
      continue;
    }
    const hasWorkflow = Boolean(c.hasWorkflow);
    const hasScript = Boolean(c.hasScript);
    if (hasWorkflow && hasScript) {
      skipped.push({ repo, reason: 'already covered' });
      continue;
    }
    if (!c.hasLockfile) {
      blocked.push({
        repo,
        reason: `no ${LOCKFILE_PATH} — \`npm ci\` would fail on every run, making the audit red for the wrong reason (pnpm/yarn needs a different shape, #771)`,
      });
      continue;
    }
    const needs = [];
    if (!hasWorkflow) needs.push('workflow');
    if (!hasScript) needs.push('script');
    const warnings = [];
    const branch = c.defaultBranch || null;
    if (branch && branch !== 'main') {
      // Non-blocking: the daily schedule still fires. Only the canonical file's
      // `pull_request: branches: ['main']` trigger goes dormant, so package
      // changes are audited nightly instead of at review time.
      warnings.push(
        `default branch is \`${branch}\`, not \`main\` — the canonical \`pull_request\` trigger will not match; the daily cron still runs`,
      );
    }
    targets.push({ repo, needs, cron: cronFor(repo), defaultBranch: branch, warnings });
  }

  targets.sort((a, b) => a.repo.localeCompare(b.repo));
  skipped.sort((a, b) => a.repo.localeCompare(b.repo));
  blocked.sort((a, b) => a.repo.localeCompare(b.repo));
  return { targets, skipped, blocked };
}

/** Markdown step-summary for a planned or completed run. */
function renderPlanSummary(plan, opts) {
  const o = opts || {};
  const lines = [];
  lines.push(`## 742 Fleet Security Audit Backfill — ${o.dryRun ? 'plan (dry run)' : 'apply'}`);
  lines.push('');
  lines.push(
    `- Targets: **${plan.targets.length}** · blocked: ${plan.blocked.length} · skipped: ${plan.skipped.length}`,
  );
  if (o.limit && plan.targets.length > o.limit) {
    lines.push(
      `- ⚠️ Batch limit \`${o.limit}\` — only the first ${o.limit} targets are in this run; re-dispatch for the rest.`,
    );
  }
  lines.push('');
  if (plan.targets.length) {
    lines.push('### Targets');
    lines.push('');
    lines.push('| Repo | Adds | Cron | Notes |');
    lines.push('| --- | --- | --- | --- |');
    for (const t of plan.targets) {
      lines.push(
        `| ${t.repo} | ${t.needs.join(' + ')} | \`${t.cron}\` | ${t.warnings.join('; ') || '—'} |`,
      );
    }
    lines.push('');
  }
  if (plan.blocked.length) {
    lines.push(`### Blocked — reported, not written (${plan.blocked.length})`);
    lines.push('');
    for (const b of plan.blocked) {
      lines.push(`- **${b.repo}**: ${b.reason}`);
    }
    lines.push('');
  }
  if (plan.skipped.length) {
    lines.push(`### Skipped (${plan.skipped.length})`);
    lines.push('');
    lines.push('<details><summary>Correct no-ops</summary>');
    lines.push('');
    for (const s of plan.skipped) {
      lines.push(`- ${s.repo}: ${s.reason}`);
    }
    lines.push('');
    lines.push('</details>');
    lines.push('');
  }
  return lines.join('\n');
}

/** Body for the per-repo pull request. */
function renderPrBody(target) {
  const lines = [];
  lines.push(
    'Adds dependency-vulnerability detection to this site. Opened by Free For Charity automation.',
  );
  lines.push('');
  lines.push(
    `- \`${AUDIT_WORKFLOW_PATH}\` — daily \`npm audit\` on production dependencies, copied verbatim from [the FFC template](https://github.com/${CANONICAL_REPO}/blob/main/${AUDIT_WORKFLOW_PATH}) apart from the schedule.`,
  );
  lines.push(
    `- \`package.json\` — an \`${AUDIT_SCRIPT}\` script (\`${AUDIT_SCRIPT_CMD}\`), which is what that workflow runs.`,
  );
  lines.push('');
  lines.push(
    `Scheduled at \`${target.cron}\` (${String(target.cron).split(' ')[0].padStart(2, '0')} past 06:00 UTC). ` +
      "The minute is derived from this repo's name so the fleet spreads across the hour instead of every site starting at once.",
  );
  lines.push('');
  lines.push(
    'Both files land together on purpose: the workflow without the script fails on every run, ' +
      'and the script without the workflow never runs at all.',
  );
  lines.push('');
  lines.push(
    "**If this PR's first Security Audit run goes red, that is the workflow working** — it means " +
      'this site already depends on a package with a published high or critical advisory. The ' +
      'upgrade is tracked separately in ' +
      '[FFC-Cloudflare-Automation#822](https://github.com/FreeForCharity/FFC-Cloudflare-Automation/issues/822); ' +
      'this change only makes it visible.',
  );
  lines.push('');
  lines.push(
    'Generated by FFC workflow **742. Repo - Fleet Security Audit Backfill**. ' +
      'Refs [FreeForCharity/FFC-Cloudflare-Automation#838](https://github.com/FreeForCharity/FFC-Cloudflare-Automation/issues/838).',
  );
  return lines.join('\n');
}

module.exports = {
  AUDIT_WORKFLOW_PATH,
  AUDIT_SCRIPT,
  AUDIT_SCRIPT_CMD,
  LOCKFILE_PATH,
  CANONICAL_REPO,
  CROWDED_MINUTE,
  BRANCH,
  COMMIT_MESSAGE,
  stableMinute,
  cronFor,
  validateTemplate,
  renderAuditWorkflow,
  addAuditScript,
  verifyOnlyScriptAdded,
  stableStringify,
  planTargets,
  renderPlanSummary,
  renderPrBody,
};
