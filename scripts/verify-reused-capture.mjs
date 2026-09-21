#!/usr/bin/env node
/**
 * verify-reused-capture.mjs — decide whether a capture downloaded from an
 * EARLIER run may be delivered as the result of THIS one.
 *
 * WHY THIS EXISTS. 706's `convert` job crawls a live charity's shared host for
 * 13–14 minutes, and its `deliver` job sits behind a human approval gate. A
 * failure anywhere after that gate spends the approval AND forces the whole
 * crawl again, for a result that is supposed to be byte-identical. The capture
 * that the failed run produced is still sitting in its artifacts. Reusing it
 * turns a retry from "14 minutes and a second approval" into "seconds and the
 * approval you already have", and it crawls the charity's server once per
 * migration rather than once per attempt.
 *
 * WHY IT IS A GUARD AND NOT A CONVENIENCE. The failure mode of getting this
 * wrong is the worst one this pipeline has: a run that reports success and
 * publishes SOMEBODY ELSE'S SITE, or yesterday's, or two of a charity's three
 * hostnames. Every one of those is invisible in the run log — the build passes,
 * the self-containment gate passes, the PR opens — because each of those checks
 * asks "is this tree a coherent site?" and not "is this tree the site that was
 * asked for". Nothing downstream can catch it, so it is caught here.
 *
 * WHAT IS CHECKED, and why each one is a real failure rather than a hypothesis:
 *
 *   DOMAIN      The apex report's `domain` must be the domain THIS run was
 *               dispatched with. A run id is eight digits an operator types by
 *               hand; the adjacent digit is another charity's migration.
 *
 *   HOST SET    Exactly the hosts this run asked for, no more and no fewer.
 *               Fewer means publishing a site with a section missing, which
 *               looks like a successful migration. MORE means publishing a
 *               subdomain nobody asked to publish — the same mistake, pointing
 *               the other way, and the one an "at least what I need" check
 *               would wave through.
 *
 *   MOUNT       Each host's pages must sit where this run says they go. A
 *               capture is mounted INSIDE the capture (relativePrefix derives
 *               each page's `../` count from its depth), so a reused capture
 *               mounted at `/school` cannot be re-pointed at `/courses` by
 *               moving files: every relative reference would be off by one, on
 *               every page, silently.
 *
 *   AGE         Reported always, refused past a threshold. A capture within the
 *               7-day artifact retention can still be days stale, and "the site
 *               has not changed" is an assumption the operator should make
 *               deliberately rather than inherit.
 *
 * Exit codes:
 *   0  the capture may be reused; the per-host report paths are on stdout
 *   1  refused, with every reason on stderr as a ::error:: line
 *   2  invalid usage / self-test failure
 */
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

/** The label `706` gives the apex capture's per-host report copy. */
export const APEX_LABEL = 'apex';

/** `wp-capture-report.<label>.json` — the per-host copies `706` writes. */
export function reportFileFor(label) {
  return `wp-capture-report.${label}.json`;
}

/**
 * Read `host=mount host=mount` as `706`'s `resolve` job emits it.
 *
 * Deliberately the same encoding the capture step consumes, parsed the same
 * way, so a mount that reaches the capture and a mount that reaches this
 * verifier cannot disagree about what was asked for.
 */
export function parseMountPairs(text) {
  const pairs = [];
  for (const token of String(text ?? '').split(/\s+/)) {
    if (!token) continue;
    const at = token.indexOf('=');
    // Not `split('=')`: a token with no `=` must be an ERROR, and both
    // `${t%%=*}` and `${t#*=}` return the whole token in that case, so a
    // split-based reader would silently produce host === mount.
    if (at <= 0 || at === token.length - 1) {
      pairs.push({ host: token, mount: '', malformed: true });
      continue;
    }
    pairs.push({ host: token.slice(0, at), mount: token.slice(at + 1), malformed: false });
  }
  return pairs;
}

/**
 * The whole decision, as a pure function over what was READ rather than over
 * the filesystem, so every branch below is reachable from a self-test.
 *
 * @param {object} args
 * @param {string} args.domain        apex domain this run was dispatched with
 * @param {Array}  args.expected      [{host, mount}] extra hosts this run wants
 * @param {Map}    args.reports       label -> parsed report JSON (or null if unreadable)
 * @param {number} args.maxAgeHours   refuse a capture older than this
 * @param {Date}   args.now
 * @returns {{ok: boolean, errors: string[], notes: string[], reportLabels: string[]}}
 */
export function verifyReusedCapture({ domain, expected, reports, maxAgeHours, now }) {
  const errors = [];
  const notes = [];

  const apex = reports.get(APEX_LABEL);
  if (!apex) {
    errors.push(
      `the reused capture has no ${reportFileFor(APEX_LABEL)} — it was not produced by a 706 run that completed its capture step`,
    );
    // Without the apex report there is nothing to compare anything against, so
    // every later check would report a second, derived failure. Stop here: one
    // true cause beats four symptoms.
    return { ok: false, errors, notes, reportLabels: [] };
  }
  if (apex.domain !== domain) {
    errors.push(
      `the reused capture is of '${apex.domain}', but this run was dispatched for '${domain}' — refusing to publish one charity's site as another's`,
    );
  }
  if (apex.mount) {
    errors.push(
      `the reused capture's apex is mounted at '/${apex.mount}', but the apex is always captured at '/' — this artifact does not have the shape 706 produces`,
    );
  }

  // The host SET, compared in both directions.
  const wanted = new Map(expected.map((e) => [e.host, e.mount]));
  const present = new Set([...reports.keys()].filter((k) => k !== APEX_LABEL));

  for (const [host, mount] of wanted) {
    const r = reports.get(host);
    if (!r) {
      errors.push(
        `this run asks for '${host}' at '/${mount}', but the reused capture has no ${reportFileFor(host)} — delivering it would publish the site with that section missing`,
      );
      continue;
    }
    if (r.domain !== host) {
      errors.push(
        `${reportFileFor(host)} reports domain '${r.domain}', not '${host}' — the artifact's labelling and its contents disagree`,
      );
    }
    if ((r.mount ?? '') !== mount) {
      errors.push(
        `'${host}' was captured mounted at '/${r.mount ?? ''}', but this run mounts it at '/${mount}'. A mount is baked into every relative link at capture time and cannot be changed by moving files — re-capture instead of reusing`,
      );
    }
  }

  for (const host of present) {
    if (!wanted.has(host)) {
      errors.push(
        `the reused capture also contains '${host}' (at '/${reports.get(host)?.mount ?? '?'}'), which this run did not ask for — delivering it would publish a hostname nobody requested. Pass it in extra_hosts, or re-capture`,
      );
    }
  }

  // Age: always reported, refused past the threshold.
  const capturedAt = apex.capturedAt ? new Date(apex.capturedAt) : null;
  if (!capturedAt || Number.isNaN(capturedAt.getTime())) {
    errors.push(
      `${reportFileFor(APEX_LABEL)} has no readable capturedAt — the age of a reused capture is not optional information`,
    );
  } else {
    const ageHours = (now.getTime() - capturedAt.getTime()) / 3_600_000;
    const pretty =
      ageHours < 1 ? `${Math.round(ageHours * 60)} minute(s)` : `${ageHours.toFixed(1)} hour(s)`;
    notes.push(`captured ${apex.capturedAt} — ${pretty} ago`);
    if (ageHours > maxAgeHours) {
      errors.push(
        `the reused capture is ${pretty} old, past the ${maxAgeHours}h limit. It may no longer be the live site; re-capture, or raise reuse_max_age_hours deliberately`,
      );
    }
    if (ageHours < 0) {
      errors.push(
        `${reportFileFor(APEX_LABEL)} is dated in the future (${apex.capturedAt}) — a clock this wrong makes the age check meaningless`,
      );
    }
  }

  const reportLabels = errors.length ? [] : [APEX_LABEL, ...wanted.keys()];
  return { ok: errors.length === 0, errors, notes, reportLabels };
}

/* ------------------------------------------------------------------ */

/** Read every `wp-capture-report.<label>.json` in `dir` into label -> JSON. */
export function readReports(dir) {
  const reports = new Map();
  const unreadable = [];
  if (!existsSync(dir)) return { reports, unreadable: [`${dir} does not exist`] };
  for (const name of readdirSync(dir)) {
    const m = /^wp-capture-report\.(.+)\.json$/.exec(name);
    if (!m) continue;
    try {
      reports.set(m[1], JSON.parse(readFileSync(join(dir, name), 'utf8')));
    } catch (err) {
      unreadable.push(`${name}: ${err.message}`);
    }
  }
  return { reports, unreadable };
}

/* ------------------------------------------------------------------ */

function selfTest() {
  let failures = 0;
  const eq = (name, actual, expected) => {
    const a = JSON.stringify(actual);
    const e = JSON.stringify(expected);
    const ok = a === e;
    console.log(`${ok ? 'ok  ' : 'FAIL'} ${name}`);
    if (!ok) {
      console.log(`  expected ${e}`);
      console.log(`  actual   ${a}`);
      failures++;
    }
  };

  const now = new Date('2026-09-21T12:00:00Z');
  const fresh = '2026-09-21T11:00:00Z';
  const apexReport = (over = {}) => ({
    domain: 'example.org',
    mount: '',
    capturedAt: fresh,
    ...over,
  });
  const hostReport = (host, mount, over = {}) => ({
    domain: host,
    mount,
    capturedAt: fresh,
    ...over,
  });

  const run = (reports, expected = [], maxAgeHours = 168) =>
    verifyReusedCapture({
      domain: 'example.org',
      expected,
      reports: new Map(reports),
      maxAgeHours,
      now,
    });

  eq('mount pairs parse', parseMountPairs('a.example.org=school b.example.org=news'), [
    { host: 'a.example.org', mount: 'school', malformed: false },
    { host: 'b.example.org', mount: 'news', malformed: false },
  ]);
  eq('mount pairs on empty input', parseMountPairs(''), []);
  eq(
    'mount pairs collapse repeated whitespace',
    parseMountPairs('  a.org=x   b.org=y  ').length,
    2,
  );
  // A token with no `=` is the exact shape `${pair%%=*}`/`${pair#*=}` produce
  // for a malformed pair, and it must not read as host === mount.
  eq(
    'a token with no equals is malformed, not a pair',
    parseMountPairs('a.org')[0].malformed,
    true,
  );
  eq('a token with an empty mount is malformed', parseMountPairs('a.org=')[0].malformed, true);
  eq('a token with an empty host is malformed', parseMountPairs('=x')[0].malformed, true);

  // --- the happy paths -----------------------------------------------------
  eq(
    'an apex-only capture reused for an apex-only run',
    run([[APEX_LABEL, apexReport()]]).ok,
    true,
  );
  eq('and it names the report to re-assess', run([[APEX_LABEL, apexReport()]]).reportLabels, [
    APEX_LABEL,
  ]);
  const threeHost = [
    [APEX_LABEL, apexReport()],
    ['school.example.org', hostReport('school.example.org', 'school')],
    ['news.example.org', hostReport('news.example.org', 'news')],
  ];
  const threeWanted = [
    { host: 'school.example.org', mount: 'school' },
    { host: 'news.example.org', mount: 'news' },
  ];
  eq('a three-host capture reused for the same three hosts', run(threeHost, threeWanted).ok, true);
  eq(
    'every host is re-assessed, not just the apex',
    run(threeHost, threeWanted).reportLabels.length,
    3,
  );

  // --- the refusals, each a failure that is otherwise invisible -------------
  eq(
    "another charity's capture is refused",
    run([[APEX_LABEL, apexReport({ domain: 'other.org' })]]).errors.length,
    1,
  );
  eq(
    'and the error names both domains, because the cause is a mistyped run id',
    /'other\.org'.*'example\.org'/.test(
      run([[APEX_LABEL, apexReport({ domain: 'other.org' })]]).errors[0],
    ),
    true,
  );
  eq(
    'a capture with no apex report is refused',
    run([['school.example.org', hostReport('school.example.org', 'school')]]).ok,
    false,
  );
  eq(
    'and that refusal reports ONE cause, not a derived cascade',
    run([['school.example.org', hostReport('school.example.org', 'school')]]).errors.length,
    1,
  );

  // A MISSING host publishes a site with a section silently absent.
  eq(
    'a capture missing a host this run wants is refused',
    run([[APEX_LABEL, apexReport()]], threeWanted).errors.length,
    2,
  );
  // An EXTRA host publishes a hostname nobody asked to publish. This is the
  // one an "at least what I need" check waves through.
  eq(
    'a capture with a host this run did NOT ask for is refused',
    run(threeHost, [{ host: 'school.example.org', mount: 'school' }]).errors.length,
    1,
  );
  eq(
    'and it says which one and where it would land',
    /news\.example\.org.*\/news/.test(
      run(threeHost, [{ host: 'school.example.org', mount: 'school' }]).errors[0],
    ),
    true,
  );

  // A mount mismatch cannot be repaired downstream: relativePrefix() derives
  // each page's `../` count from its depth AT CAPTURE TIME.
  eq(
    'a host captured at a different mount is refused',
    run(
      [
        [APEX_LABEL, apexReport()],
        ['school.example.org', hostReport('school.example.org', 'school')],
      ],
      [{ host: 'school.example.org', mount: 'courses' }],
    ).errors.length,
    1,
  );
  eq(
    'and it says WHY moving the files will not fix it',
    /cannot be changed by moving files/.test(
      run(
        [
          [APEX_LABEL, apexReport()],
          ['school.example.org', hostReport('school.example.org', 'school')],
        ],
        [{ host: 'school.example.org', mount: 'courses' }],
      ).errors[0],
    ),
    true,
  );
  // A report predating the `mount` field reads as unmounted, which is exactly
  // what it means for an apex-only capture and wrong for anything else — so an
  // absent mount must compare as '' rather than be skipped.
  eq(
    'a report with no mount field is treated as unmounted, not as a wildcard',
    run(
      [
        [APEX_LABEL, apexReport()],
        ['school.example.org', { domain: 'school.example.org', capturedAt: fresh }],
      ],
      [{ host: 'school.example.org', mount: 'school' }],
    ).errors.length,
    1,
  );

  eq(
    'an apex captured under a mount is refused as the wrong shape',
    run([[APEX_LABEL, apexReport({ mount: 'school' })]]).errors.length,
    1,
  );

  // --- age -----------------------------------------------------------------
  eq('a fresh capture reports its age', run([[APEX_LABEL, apexReport()]]).notes.length, 1);
  eq(
    'a capture older than the limit is refused',
    run([[APEX_LABEL, apexReport({ capturedAt: '2026-09-01T11:00:00Z' })]]).errors.length,
    1,
  );
  eq(
    'the limit is configurable, and raising it accepts the same capture',
    run([[APEX_LABEL, apexReport({ capturedAt: '2026-09-01T11:00:00Z' })]], [], 24 * 365).ok,
    true,
  );
  eq(
    'an unreadable capturedAt is refused rather than treated as fresh',
    run([[APEX_LABEL, apexReport({ capturedAt: 'whenever' })]]).errors.length,
    1,
  );
  eq(
    'a missing capturedAt is refused too',
    run([[APEX_LABEL, { domain: 'example.org', mount: '' }]]).errors.length,
    1,
  );
  eq(
    'a future-dated capture is refused',
    run([[APEX_LABEL, apexReport({ capturedAt: '2027-01-01T00:00:00Z' })]]).errors.length,
    1,
  );

  // Errors accumulate: an operator fixing a dispatch should see every reason.
  eq(
    'every reason is reported, not just the first',
    run(
      [
        [APEX_LABEL, apexReport({ domain: 'other.org', capturedAt: '2026-09-01T11:00:00Z' })],
        ['extra.example.org', hostReport('extra.example.org', 'extra')],
      ],
      [{ host: 'school.example.org', mount: 'school' }],
    ).errors.length,
    4,
  );
  // And nothing is offered for re-assessment when anything failed — a partial
  // "these ones were fine" list is how a refused capture gets half-used.
  eq(
    'a refused capture offers no reports to continue with',
    run([[APEX_LABEL, apexReport({ domain: 'other.org' })]]).reportLabels,
    [],
  );

  console.log(failures === 0 ? '\nall self-tests passed' : `\n${failures} self-test failure(s)`);
  return failures === 0 ? 0 : 1;
}

/* ------------------------------------------------------------------ */

const isMain = process.argv[1] ? import.meta.url === pathToFileURL(process.argv[1]).href : false;

if (isMain) {
  if (process.argv.includes('--self-test')) process.exit(selfTest());

  const arg = (name, def = '') => {
    const i = process.argv.indexOf(`--${name}`);
    return i > -1 && process.argv[i + 1] !== undefined ? process.argv[i + 1] : def;
  };
  const dir = arg('dir');
  const domain = arg('domain');
  const mountsText = arg('mounts', '');
  const maxAgeHours = Number(arg('max-age-hours', '168'));
  if (!dir || !domain || !Number.isFinite(maxAgeHours) || maxAgeHours <= 0) {
    console.error(
      'Usage: --dir <capture/site> --domain <apex> [--mounts "h=m h=m"] [--max-age-hours N]',
    );
    process.exit(2);
  }

  const pairs = parseMountPairs(mountsText);
  const malformed = pairs.filter((p) => p.malformed);
  if (malformed.length) {
    for (const p of malformed) {
      console.error(
        `::error::reused capture: malformed mount pair '${p.host}' — cannot tell what this run asked for`,
      );
    }
    process.exit(1);
  }

  const { reports, unreadable } = readReports(dir);
  for (const u of unreadable) console.error(`::error::reused capture: ${u}`);

  const result = verifyReusedCapture({
    domain,
    expected: pairs.map(({ host, mount }) => ({ host, mount })),
    reports,
    maxAgeHours,
    now: new Date(),
  });

  for (const n of result.notes) console.error(`reused capture: ${n}`);
  for (const e of result.errors) console.error(`::error::reused capture: ${e}`);
  if (!result.ok || unreadable.length) process.exit(1);

  // stdout is the machine-readable half: the per-host reports the caller must
  // re-assess against THIS run's min_percent. Printed only on success, so a
  // caller that ignores the exit code still gets nothing to work with.
  for (const label of result.reportLabels) console.log(join(dir, reportFileFor(label)));
  process.exit(0);
}
