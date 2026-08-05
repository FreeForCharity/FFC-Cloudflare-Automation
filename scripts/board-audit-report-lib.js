'use strict';

// Pure classification + report logic for workflow 745 (Repo - Agentic OS Board
// Audit). The workflow's github-script step `require`s this module and only
// performs I/O — reading the audit script's captured output and posting the
// Conductor Log comment — so the logic tested under tests/workflow-logic/ is
// the logic that ships.
//
// Why this exists (#969). `scripts/audit-agentic-os-board.py` landed in #968 and
// works; nothing invoked it. A compensating control on a manual process (the
// board has no auto-add — CLAUDE.md) has to be automatic, or it inherits the
// failure mode it exists to cover. It had already failed twice in two runs by
// the time #969 was filed, and run 62's mess was only found ~6 hours later by
// accident.
//
// The classification's whole job is that the audit has THREE outcomes and its
// exit code only distinguishes two. The script documents this itself:
//
//     0  board is consistent with the backlog — all three sets empty
//     1  at least one set is non-empty (the audit found something), OR any API,
//        auth or config error. **Never 0 on a failed enumeration**
//
// So a bare `exit != 0` cannot tell "the board has four defects" from "the PAT
// is dead and I read nothing". Both need a comment, but they need DIFFERENT
// comments: the first is a worklist, the second is an admission that the run has
// no verdict. Collapsing them is the shape 321 was bitten by (#848) — an
// enumeration failure rendered as findings manufactures work that does not
// exist, and rendered as clean is the silence-read-as-green this whole audit is
// about (#966).
//
// Hence `classify` returns one of three outcomes and never infers a verdict it
// cannot corroborate:
//
//   'clean'      exit 0 AND a parseable report AND that report has no findings
//   'findings'   exit != 0 AND a parseable report AND that report has findings
//   'incomplete' everything else, including the two CONTRADICTIONS — a zero exit
//                whose report carries findings, and a non-zero exit whose report
//                carries none. A tool disagreeing with itself is not evidence of
//                a clean board; #722's large-blob guard went green for the wrong
//                reason on exactly that kind of unexamined exit code.
//
// Only 'clean' is silent. A daily "board is clean" comment on #719 trains
// everyone to stop reading the thread, which is why 734 and 967 established the
// silence-when-clean pattern this follows — but silence is reserved for the one
// outcome that actually established something.

// The Conductor Log. Every report lands here rather than in a rolling issue:
// the audit's output is a per-run worklist for whoever is conducting, not a
// condition that opens and closes.
const LOG_ISSUE = 719;

// Findings render as a bounded list. A board that has drifted badly can produce
// a set of a hundred; the comment stays readable and says how many it dropped.
const MAX_ROWS_PER_SECTION = 25;

// The sets that fail the run. Mirrors FAILING_SECTIONS in
// scripts/audit-agentic-os-board.py.
const SECTIONS = [
  {
    key: 'missing_from_board',
    heading: 'Missing from the board',
    blurb: 'open `agentic-os` items with no card — add them to org project #9',
  },
  {
    key: 'statusless',
    heading: 'On the board with no Status',
    blurb: 'cards that exist but were never placed in a column',
  },
  {
    key: 'closed_not_done',
    heading: 'Closed or merged but not `Done`',
    blurb: 'finished work whose card still reads as live',
  },
];

// Reported, never failing (#992). Mirrors DEFERRED_SECTIONS in the script.
//
// These are rendered but excluded from `hasFindings`, so a run whose only
// content is deferred rows classifies `clean` and stays silent. They are still
// PARSED as strictly as the failing sections — an absent key is a malformed
// report either way — and they are still printed whenever a comment is posted
// for another reason, because a deferral nobody can see is indistinguishable
// from a check that was quietly deleted.
const DEFERRED_SECTIONS = [
  {
    key: 'recently_opened_not_yet_carded',
    heading: 'Recently opened, not yet carded',
    blurb:
      'younger than the grace window — latency, not drift. Listed so the deferral is ' +
      'visible; these do **not** fail the run and need no action unless they are still ' +
      'here tomorrow.',
  },
  {
    key: 'self_referential_alerts',
    heading: "This audit's own failure alert",
    blurb:
      "740's rolling alert for 745 itself. Counting it as a finding keeps 745 red, which " +
      'keeps the alert open, which keeps 745 red — a monitor must not be able to ' +
      'manufacture its own findings.',
  },
];

const ALL_SECTIONS = [...SECTIONS, ...DEFERRED_SECTIONS];

/** Parse the audit script's `--json` stdout. Never throws. */
function parseReport(stdout) {
  if (typeof stdout !== 'string' || !stdout.trim()) {
    return { report: null, parseError: 'no output' };
  }
  let parsed;
  try {
    parsed = JSON.parse(stdout);
  } catch (e) {
    return { report: null, parseError: String((e && e.message) || e).slice(0, 200) };
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return { report: null, parseError: 'output is not a JSON object' };
  }
  // A report missing a section is not a report with an empty section. Treating
  // an absent key as `[]` would let a truncated or half-written document read as
  // a clean board — the same fail-open direction the audit exists to close.
  for (const s of ALL_SECTIONS) {
    if (!Array.isArray(parsed[s.key])) {
      return { report: null, parseError: `missing or non-array section: ${s.key}` };
    }
  }
  return { report: parsed, parseError: null };
}

/**
 * True when any of the three FAILING sets is non-empty. Mirrors the script's
 * `has_findings`, deferred sections and all — if this counted them, the run
 * would go red for a PR opened twenty minutes ago and #992 would be unfixed.
 */
function hasFindings(report) {
  if (!report) return false;
  return SECTIONS.some((s) => Array.isArray(report[s.key]) && report[s.key].length > 0);
}

function countIn(report, sections) {
  if (!report) return 0;
  return sections.reduce(
    (n, s) => n + (Array.isArray(report[s.key]) ? report[s.key].length : 0),
    0,
  );
}

function findingCount(report) {
  return countIn(report, SECTIONS);
}

/** How many rows were deferred. Never contributes to the outcome; always shown. */
function deferredCount(report) {
  return countIn(report, DEFERRED_SECTIONS);
}

/**
 * Decide what the run established.
 *
 * @param {{exitCode:*, stdout:*, stderr:*}} run
 * @returns {{outcome:'clean'|'findings'|'incomplete', report:object|null,
 *            parseError:string|null, reason:string|null, count:number}}
 */
function classify(run) {
  const r = run || {};
  // A non-numeric or absent exit code is itself an incomplete run: the step that
  // was supposed to report it did not. `Number('')` is 0, so an empty string
  // would otherwise pass for success.
  const raw = r.exitCode;
  const exitCode =
    typeof raw === 'number' && Number.isFinite(raw)
      ? raw
      : /^-?\d+$/.test(String(raw ?? '').trim())
        ? Number(String(raw).trim())
        : null;
  const { report, parseError } = parseReport(r.stdout);
  const found = hasFindings(report);
  const count = findingCount(report);
  const base = { report, parseError, count };

  if (exitCode === null) {
    return { ...base, outcome: 'incomplete', reason: `unusable exit code: ${JSON.stringify(raw)}` };
  }
  if (!report) {
    return {
      ...base,
      outcome: 'incomplete',
      reason: `the audit produced no readable report (${parseError})`,
    };
  }
  if (exitCode === 0 && !found) {
    return { ...base, outcome: 'clean', reason: null };
  }
  if (exitCode !== 0 && found) {
    return { ...base, outcome: 'findings', reason: null };
  }
  // Both remaining combinations are the tool contradicting itself.
  return {
    ...base,
    outcome: 'incomplete',
    reason:
      exitCode === 0
        ? `exit 0 but the report carries ${count} finding(s) — the audit contradicts its own exit code`
        : `exit ${exitCode} but the report carries no findings — the audit contradicts its own exit code`,
  };
}

/** Silence is for 'clean' only. */
function shouldComment(classification) {
  return !!classification && classification.outcome !== 'clean';
}

/**
 * Whether the job should end red. Findings fail the job because #969 asks for it
 * ("let a real finding fail the job rather than swallowing the exit code"), and
 * an incomplete run fails because a monitor that cannot read one of its two
 * sides has no verdict — reporting green there is the exact defect.
 */
function shouldFail(classification) {
  return shouldComment(classification);
}

function truncate(text, max) {
  const s = String(text == null ? '' : text).trim();
  return s.length > max ? `${s.slice(0, max - 3)}...` : s;
}

/**
 * One report line for a row, matching the script's `_describe` distinctions: a
 * real item, a draft card, and a card whose content is unreadable each need a
 * different remedy, so they must not share a label.
 */
function describeRow(row) {
  const r = row && typeof row === 'object' ? row : {};
  let label;
  if (r.repo && r.number != null) {
    label = r.url ? `[${r.repo}#${r.number}](${r.url})` : `${r.repo}#${r.number}`;
  } else if (r.type === 'DraftIssue') {
    label = '(draft card)';
  } else {
    label = '(no readable content: deleted, or in a repo this token cannot see)';
  }
  const status = r.status ? `\`${r.status}\`` : '`-`';
  const title = truncate(r.title, 70);
  const age = formatAge(r.age_minutes);
  const suffix = age ? ` — ${age} old` : '';
  return `- ${label} ${status} ${title}${suffix}`.trimEnd();
}

/**
 * A short human age, or '' when unknown. Mirrors the script's `format_age`.
 * Rendered on every row that carries one, which in practice is the deferred
 * sections: a deferral a reader cannot date is a deferral they cannot check.
 */
function formatAge(minutes) {
  if (typeof minutes !== 'number' || !Number.isFinite(minutes)) return '';
  if (minutes < 0) return '0m';
  if (minutes < 90) return `${Math.round(minutes)}m`;
  if (minutes < 48 * 60) return `${(minutes / 60).toFixed(1)}h`;
  return `${(minutes / 1440).toFixed(1)}d`;
}

/**
 * One-line console/notice summary. Always safe to log.
 *
 * Deferred counts are on this line even on a clean run, which is the only place
 * they surface when the board is otherwise fine — `renderBody` returns null for
 * `clean`, by design (#967). So `core.notice` is where "we deferred 3 things
 * today" is auditable without opening the JSON.
 */
function summary(classification) {
  const c = classification || {};
  const rep = c.report || {};
  const denominators =
    rep.expected_count != null && rep.board_count != null
      ? ` expected=${rep.expected_count} board=${rep.board_count}`
      : '';
  const counts = ALL_SECTIONS.map(
    (s) => `${s.key}=${Array.isArray(rep[s.key]) ? rep[s.key].length : '?'}`,
  ).join(' ');
  return `board audit: ${c.outcome}${denominators} ${counts}`.trim();
}

/**
 * The Conductor Log comment. Returns null for a clean run so a caller that
 * forgets `shouldComment` still cannot post one.
 */
function renderBody(classification, ctx) {
  const c = classification || {};
  if (!shouldComment(c)) return null;
  const { runUrl = null } = ctx || {};
  const rep = c.report || {};
  const lines = [];

  if (c.outcome === 'incomplete') {
    lines.push('### 745 Agentic OS board audit — ⚠️ INCOMPLETE, no verdict');
    lines.push('');
    lines.push(
      `The audit did not establish whether the board is consistent: **${c.reason}**. ` +
        'This is reported rather than skipped because a run that cannot read one of its two ' +
        'sides has no verdict, and "no verdict" rendered as "clean" is the exact silence this ' +
        'audit exists to end (#966).',
    );
    if (c.parseError) {
      lines.push('');
      lines.push(`Parse detail: \`${truncate(c.parseError, 200)}\``);
    }
    lines.push('');
    lines.push(
      'Most likely causes, in order: the Key Vault PAT is dead or lacks project read ' +
        '(#848 killed `read-all-cbm-github-pat` this way), the org project number moved, or the ' +
        'GitHub API refused an enumeration. **The board state is unknown until this run is ' +
        'repeated successfully — do not read this comment as "the board was fine".**',
    );
  } else {
    const title = rep.board_title ? ` (${rep.board_title})` : '';
    lines.push(`### 745 Agentic OS board audit — ${c.count} finding(s)`);
    lines.push('');
    lines.push(
      `Board: ${rep.org || 'FreeForCharity'} project #${rep.project == null ? 9 : rep.project}${title} · ` +
        `\`expected=${rep.expected_count == null ? '?' : rep.expected_count}\` ` +
        `\`board=${rep.board_count == null ? '?' : rep.board_count}\` · ` +
        `repos swept: ${Array.isArray(rep.repos_swept) ? rep.repos_swept.length : '?'}`,
    );
    lines.push('');
    lines.push(
      'Both denominators are printed deliberately: run 62 reported "0 missing" off an expected ' +
        'set that was one item short, and a short denominator was the only thing a reader could ' +
        'have found suspicious (#966).',
    );
    for (const s of SECTIONS) {
      const rows = Array.isArray(rep[s.key]) ? rep[s.key] : [];
      lines.push('');
      lines.push(`#### ${s.heading}: ${rows.length}`);
      lines.push('');
      lines.push(`_${s.blurb}_`);
      lines.push('');
      if (!rows.length) {
        lines.push('- (none)');
        continue;
      }
      for (const row of rows.slice(0, MAX_ROWS_PER_SECTION)) {
        lines.push(describeRow(row));
      }
      if (rows.length > MAX_ROWS_PER_SECTION) {
        lines.push(`- …and ${rows.length - MAX_ROWS_PER_SECTION} more (list truncated for length)`);
      }
    }
    lines.push('');
    lines.push(
      'The board has **no auto-add automation** (org project #9 has only ' +
        '`Auto-add sub-issues`; `Item closed` and `Pull request merged` are disabled), so every ' +
        'one of these is placed by hand and nothing else will place them.',
    );

    const deferred = deferredCount(rep);
    if (deferred > 0) {
      lines.push('');
      lines.push(
        `<details><summary>Deferred, not counted as findings: ${deferred} ` +
          `(grace window: ${rep.grace_minutes == null ? '?' : rep.grace_minutes} minutes)</summary>`,
      );
      for (const s of DEFERRED_SECTIONS) {
        const rows = Array.isArray(rep[s.key]) ? rep[s.key] : [];
        if (!rows.length) continue;
        lines.push('');
        lines.push(`##### ${s.heading}: ${rows.length}`);
        lines.push('');
        lines.push(`_${s.blurb}_`);
        lines.push('');
        for (const row of rows.slice(0, MAX_ROWS_PER_SECTION)) {
          lines.push(describeRow(row));
        }
        if (rows.length > MAX_ROWS_PER_SECTION) {
          lines.push(
            `- …and ${rows.length - MAX_ROWS_PER_SECTION} more (list truncated for length)`,
          );
        }
      }
      lines.push('');
      lines.push('</details>');
    }
  }

  if (runUrl) {
    lines.push('');
    lines.push(`Run: ${runUrl}`);
  }
  lines.push('');
  lines.push(
    '_Managed by 745. Repo - Agentic OS Board Audit. Refs #969. Read-only: this workflow adds ' +
      'no card, sets no Status, and closes nothing — it reports and sets an exit code. Silent ' +
      'when the board is clean._',
  );
  return lines.join('\n');
}

module.exports = {
  LOG_ISSUE,
  MAX_ROWS_PER_SECTION,
  SECTIONS,
  DEFERRED_SECTIONS,
  ALL_SECTIONS,
  parseReport,
  hasFindings,
  findingCount,
  deferredCount,
  classify,
  shouldComment,
  shouldFail,
  describeRow,
  formatAge,
  summary,
  renderBody,
};
