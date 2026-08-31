#!/usr/bin/env node
/**
 * assess-capture-completeness.mjs — decide whether a capture is complete enough
 * to convert, separately from whether the captured site has dead links.
 *
 * `capture-wordpress-api.mjs` exits 1 for ANY unmet gate, which conflates two
 * things that deserve opposite treatment:
 *
 *   COMPLETENESS — did we get the site? Losing pages is a migration defect and
 *   must stop the run.
 *
 *   ASSET HEALTH — are there dead references? A live site's own broken links
 *   are reproduced FAITHFULLY, not introduced. Failing on them would mean FFC
 *   can never migrate a site with one dead image, which is most real charity
 *   sites.
 *
 * Measured on the first live delivery of 706: the capture reached 589 of 590
 * inventory entries with ZERO page-fetch failures and 1838 assets localized,
 * and was rejected over 15 assets that return 404/410 on the live site too —
 * 11 WordPress.com leftovers the site still references and does not serve, and
 * 4 that answer 410 Gone.
 *
 * So this enforces completeness and REPORTS asset health. `verify-no-legacy`
 * remains the hard gate on whether the exported site actually stands alone,
 * which is the property the asset count was standing in for.
 *
 * This lives in a file rather than inline in the workflow for a reason beyond
 * tidiness: the inline form was a `node -e '…'` single-quoted shell string, and
 * every JS template literal in it read to shellcheck as an unexpanded shell
 * variable (SC2016). Quoting around that is how a script acquires the kind of
 * escaping nobody can safely edit later.
 *
 * Exit codes:
 *   0  complete enough to continue (asset problems may still be reported)
 *   1  incomplete, unreadable, or an empty inventory
 *   2  invalid usage / self-test failure
 */
import { readFileSync, appendFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

/**
 * Decide, from a capture report, whether the conversion may continue.
 *
 * Pure: takes the parsed report and the floor, returns a verdict plus the
 * lines to report. Nothing here reads the filesystem or exits, so the whole
 * decision is exercised by --self-test.
 */
export function assessCapture(report, minPercent) {
  const captured = report?.captured?.total ?? 0;
  const expected = Array.isArray(report?.entries) ? report.entries.length : 0;
  const problems = report?.verdict?.problems ?? [];
  const lines = [];

  // 0 of 0 is the case worth naming. As a ratio it is either NaN or 1.0
  // depending on how it is written, and one of those reads as a PERFECT
  // capture of a site with no pages — a green run over nothing at all.
  if (expected === 0) {
    return {
      ok: false,
      captured,
      expected,
      percent: 0,
      lines,
      error:
        'The capture report lists no inventory entries at all, so there is nothing to be complete about.',
    };
  }

  const percent = (captured / expected) * 100;
  lines.push(
    `- Captured **${captured} of ${expected}** inventory entries (${percent.toFixed(1)}%); completeness floor is ${minPercent}%`,
  );
  if (report?.declaredSelfHost) {
    lines.push(
      `- ⚠️ The live site declares its home as **${report.declaredSelfHost}** — its own URLs were rewritten onto the serving host for this capture. The LIVE site still has this bug.`,
    );
  }
  for (const p of problems) lines.push(`- capture verdict: ${p}`);

  if (percent < minPercent) {
    return {
      ok: false,
      captured,
      expected,
      percent,
      lines,
      error:
        `Captured ${captured} of ${expected} inventory entries (${percent.toFixed(1)}%), below the ` +
        `${minPercent}% completeness floor. The migration would lose content — refusing to continue.`,
    };
  }
  return { ok: true, captured, expected, percent, lines, error: null, problems };
}

function selfTest() {
  let failed = 0;
  const eq = (name, actual, expected) => {
    const a = JSON.stringify(actual);
    const b = JSON.stringify(expected);
    if (a === b) console.log(`ok   ${name}`);
    else {
      failed++;
      console.error(`FAIL ${name}\n  expected ${b}\n  actual   ${a}`);
    }
  };
  const report = (captured, expected, problems = []) => ({
    captured: { total: captured },
    entries: Array.from({ length: expected }, (_, i) => ({ i })),
    verdict: { ok: problems.length === 0, problems },
  });

  // The live failure this exists for: 589/590 with 15 assets that are dead on
  // the source site too.
  const REAL = [
    'captured 589 of 590 inventory entries (REST collections + sitemap union)',
    'unlocalized asset hosts: myviewletstalkaboutjesus.files.wordpress.com',
    'assets: 15 failed (11×HTTP 404, 4×HTTP 410); 11 of them on viewpointministriesinternational.org',
  ];
  eq(
    "a site's own dead links do not block a complete capture",
    assessCapture(report(589, 590, REAL), 98).ok,
    true,
  );
  eq(
    'losing content does block it',
    assessCapture(report(2, 587, ['captured 2 of 587']), 98).ok,
    false,
  );
  eq(
    'the floor is enforced at the boundary, not near it',
    [assessCapture(report(566, 590, REAL), 98).ok, assessCapture(report(589, 590, REAL), 98).ok],
    [false, true],
  );
  eq(
    'an empty inventory fails rather than reading as a perfect capture',
    (() => {
      const r = assessCapture(report(0, 0, []), 98);
      return [r.ok, /no inventory entries at all/.test(r.error)];
    })(),
    [false, true],
  );
  eq('the floor is operator-controllable', assessCapture(report(589, 590, REAL), 100).ok, false);
  eq('a clean capture passes', assessCapture(report(590, 590, []), 98).ok, true);
  eq(
    'the failure message names both numbers and the floor',
    (() => {
      const e = assessCapture(report(566, 590, REAL), 98).error;
      return [e.includes('566 of 590'), e.includes('95.9%'), e.includes('98%')];
    })(),
    [true, true, true],
  );
  eq(
    'every capture problem is reported even when the run continues',
    assessCapture(report(589, 590, REAL), 98).lines.filter((l) => l.startsWith('- capture verdict'))
      .length,
    3,
  );
  eq(
    "a stale self-reference is surfaced as the live site's bug",
    assessCapture({ ...report(589, 590, []), declaredSelfHost: 'vpmin.org' }, 98).lines.some((l) =>
      /declares its home as \*\*vpmin\.org\*\*/.test(l),
    ),
    true,
  );
  eq(
    'a missing captured block counts as zero rather than throwing',
    assessCapture({ entries: [{}, {}] }, 98).ok,
    false,
  );

  if (failed) {
    console.error(`\n${failed} self-test failure(s)`);
    process.exit(2);
  }
  console.log('\nall self-tests passed');
}

const isMain = process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href;

function arg(name, def = '') {
  const i = process.argv.indexOf(`--${name}`);
  return i > -1 && process.argv[i + 1] ? process.argv[i + 1] : def;
}

if (isMain) {
  if (process.argv.includes('--self-test')) {
    selfTest();
  } else {
    const reportPath = arg('report');
    const minRaw = arg('min-percent', '98');
    if (!reportPath) {
      console.error(
        'Usage: node scripts/assess-capture-completeness.mjs --report <wp-capture-report.json> [--min-percent 98]',
      );
      process.exit(2);
    }
    const minPercent = Number(minRaw);
    if (!Number.isFinite(minPercent) || minPercent <= 0 || minPercent > 100) {
      console.error(`::error::--min-percent '${minRaw}' must be a number between 1 and 100.`);
      process.exit(2);
    }

    let parsed;
    try {
      parsed = JSON.parse(readFileSync(reportPath, 'utf8'));
    } catch (err) {
      // An unreadable or unparseable report is NOT "no problems found". The
      // capture may have died mid-write, and continuing on that would convert
      // whatever fragment it left behind.
      console.error(`::error::Cannot read the capture report at ${reportPath}: ${err.message}`);
      process.exit(1);
    }

    const verdict = assessCapture(parsed, minPercent);
    for (const l of verdict.lines) console.log(l);
    if (process.env.GITHUB_STEP_SUMMARY) {
      appendFileSync(
        process.env.GITHUB_STEP_SUMMARY,
        `\n### Capture\n\n${verdict.lines.join('\n')}\n`,
      );
    }
    if (!verdict.ok) {
      console.error(`::error::${verdict.error}`);
      process.exit(1);
    }
    if ((verdict.problems ?? []).length) {
      console.error(
        '::warning::The capture reported problems that are NOT completeness failures (see the job summary). ' +
          "A live site's own dead references are reproduced faithfully; verify-no-legacy is the gate on whether the export stands alone.",
      );
    }
  }
}
