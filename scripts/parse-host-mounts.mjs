#!/usr/bin/env node
/**
 * parse-host-mounts.mjs — read 706's `extra_hosts` input into a validated list
 * of (hostname, mount path) pairs.
 *
 * A charity's site is frequently more than one hostname: New Heights serves its
 * main site on `newheightseducation.org`, its 110-page course catalog on
 * `school.newheightseducation.org` and a third section on
 * `publications.newheightseducation.org`, all on one shared hosting plan. The
 * migration target is ONE FFC-EX repo publishing one static site, so each extra
 * hostname is folded in under a URL path — `/school/…`, `/publications/…` — and
 * the subdomains become redirects at cutover.
 *
 * WHY THIS IS A FILE AND NOT SHELL IN THE WORKFLOW. Every value here decides
 * where a charity's pages get written and which host gets crawled, and a
 * mis-parse is not loud: a mount that silently resolves to `''` merges a
 * subdomain's 110 pages INTO the apex site's routes, where they collide with
 * it slug for slug. That is a data-loss bug discovered after a 15-minute crawl
 * and a human approval, which is the most expensive place in this pipeline to
 * discover anything. Pure, self-tested logic instead.
 *
 * Usage:
 *   node scripts/parse-host-mounts.mjs --text "<input>" --apex <domain> [--json]
 *   node scripts/parse-host-mounts.mjs --self-test
 */

/** Lowercase, strip a scheme, a leading `www.`, any path and any trailing dot. */
export function normalizeHost(raw) {
  if (typeof raw !== 'string') return '';
  let h = raw.trim().toLowerCase();
  h = h.replace(/^[a-z][a-z0-9+.-]*:\/\//, '');
  h = h.split('/')[0].split('?')[0].split('#')[0];
  h = h.replace(/\.+$/, '');
  h = h.replace(/^www\./, '');
  return h;
}

/** Is this a plausible DNS hostname? Deliberately strict; see the header. */
export function isHostname(h) {
  if (typeof h !== 'string' || h.length === 0 || h.length > 253) return false;
  if (!h.includes('.')) return false;
  return h.split('.').every((label) => /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/.test(label));
}

/**
 * A mount as a URL path prefix: no slashes at either end, no traversal, no
 * empty segments. Mirrors `normalizeMount` in capture-wordpress-api.mjs — the
 * two must agree, because this decides what gets PASSED to `--mount` and that
 * one decides where the files land.
 */
export function normalizeMountPath(raw) {
  if (typeof raw !== 'string') return '';
  return raw
    .trim()
    .replace(/^\/+/, '')
    .replace(/\/+$/, '')
    .split('/')
    .map((s) => s.trim())
    .filter((s) => s && s !== '.' && s !== '..')
    .join('/');
}

/**
 * Parse the whole input.
 *
 * Returns `{ mounts, errors }` and NEVER throws: the caller reports every
 * problem at once rather than making an operator re-dispatch a 15-minute run to
 * discover the second typo after fixing the first.
 */
export function parseHostMounts(text, apexDomain) {
  const mounts = [];
  const errors = [];
  const apex = normalizeHost(apexDomain);
  const seenHosts = new Set();
  const seenMounts = new Set();

  if (typeof text !== 'string' || text.trim() === '') return { mounts, errors };

  const lines = text.split(/\r?\n/);
  for (let i = 0; i < lines.length; i++) {
    const raw = lines[i];
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    const at = `line ${i + 1}`;

    // `=>` is the separator. A bare hostname is REFUSED rather than defaulted
    // to a mount derived from its subdomain label: deriving it would mean the
    // published URL of a charity's section is chosen by a guess nobody typed.
    if (!line.includes('=>')) {
      errors.push(`${at}: expected '<hostname> => <path>', got '${line}'`);
      continue;
    }
    const [hostPart, ...rest] = line.split('=>');
    if (rest.length > 1) {
      errors.push(`${at}: more than one '=>' in '${line}'`);
      continue;
    }
    const host = normalizeHost(hostPart);
    const mount = normalizeMountPath(rest[0]);

    if (!isHostname(host)) {
      errors.push(`${at}: '${hostPart.trim()}' is not a hostname`);
      continue;
    }
    if (!mount) {
      errors.push(
        `${at}: '${host}' has an empty mount path — it would merge into the apex site and collide with it`,
      );
      continue;
    }
    if (host === apex) {
      errors.push(
        `${at}: '${host}' is the apex domain, which is already captured unmounted at '/'`,
      );
      continue;
    }
    if (seenHosts.has(host)) {
      errors.push(`${at}: '${host}' appears more than once`);
      continue;
    }
    // Two hosts sharing a mount would interleave two sites in one directory,
    // and the second capture would overwrite the first's index.html.
    if (seenMounts.has(mount)) {
      errors.push(`${at}: mount '/${mount}' is already used by another host`);
      continue;
    }
    // A mount nested inside another ('/a' and '/a/b') puts one site inside the
    // other's route tree. Refused rather than ordered, because which one wins
    // depends on capture order — a property no one reading the input can see.
    const nested = [...seenMounts].find(
      (m) => m === mount || m.startsWith(`${mount}/`) || mount.startsWith(`${m}/`),
    );
    if (nested) {
      errors.push(`${at}: mount '/${mount}' overlaps '/${nested}'`);
      continue;
    }

    seenHosts.add(host);
    seenMounts.add(mount);
    mounts.push({ host, mount });
  }

  return { mounts, errors };
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

  eq(
    'normalizeHost strips scheme, www and trailing dot',
    normalizeHost('HTTPS://WWW.School.Example.ORG./x'),
    'school.example.org',
  );
  eq('normalizeHost on junk', normalizeHost(null), '');
  eq('isHostname needs a dot', isHostname('localhost'), false);
  eq('isHostname accepts a subdomain', isHostname('school.example.org'), true);
  eq('isHostname rejects a leading hyphen', isHostname('-bad.example.org'), false);
  eq('normalizeMountPath strips slashes', normalizeMountPath('/school/'), 'school');
  eq('normalizeMountPath strips traversal', normalizeMountPath('../../school'), 'school');
  eq('normalizeMountPath keeps nesting', normalizeMountPath('a/b'), 'a/b');

  const apex = 'example.org';

  eq('empty input is not an error', parseHostMounts('', apex), { mounts: [], errors: [] });
  eq('blank and comment lines are skipped', parseHostMounts('\n  \n# note\n', apex), {
    mounts: [],
    errors: [],
  });

  eq('a good pair parses', parseHostMounts('school.example.org => /school', apex), {
    mounts: [{ host: 'school.example.org', mount: 'school' }],
    errors: [],
  });

  eq(
    'two good pairs parse in order',
    parseHostMounts(
      'school.example.org => /school\npublications.example.org => publications',
      apex,
    ),
    {
      mounts: [
        { host: 'school.example.org', mount: 'school' },
        { host: 'publications.example.org', mount: 'publications' },
      ],
      errors: [],
    },
  );

  // Each of these would silently damage a migration if it were tolerated.
  //
  // Asserted on the MESSAGE, not the count. Removing the `=>` check entirely
  // still leaves a bare hostname refused — it falls through to the empty-mount
  // branch — so a count-only assertion passes with the separator check deleted,
  // and the operator is then told their mount path is empty when what they
  // actually omitted is the separator. Measured: mutation M5 was invisible to
  // `errors.length === 1`.
  eq(
    'a bare hostname is refused, not guessed',
    parseHostMounts('school.example.org', apex).errors.length,
    1,
  );
  eq(
    'and it is refused for the RIGHT reason — the missing separator',
    /expected '<hostname> => <path>'/.test(parseHostMounts('school.example.org', apex).errors[0]),
    true,
  );
  eq(
    'an empty mount is refused',
    parseHostMounts('school.example.org =>   ', apex).errors.length,
    1,
  );
  eq(
    'an empty mount says WHY it matters',
    /collide/.test(parseHostMounts('school.example.org => /', apex).errors[0]),
    true,
  );
  eq('the apex is refused', parseHostMounts('example.org => /main', apex).errors.length, 1);
  eq(
    'the apex is refused via www too',
    parseHostMounts('www.example.org => /main', apex).errors.length,
    1,
  );
  eq(
    'a duplicate host is refused',
    parseHostMounts('a.example.org => /x\na.example.org => /y', apex).errors.length,
    1,
  );
  eq(
    'a duplicate mount is refused',
    parseHostMounts('a.example.org => /x\nb.example.org => /x', apex).errors.length,
    1,
  );
  eq(
    'an overlapping mount is refused',
    parseHostMounts('a.example.org => /x\nb.example.org => /x/y', apex).errors.length,
    1,
  );
  eq(
    'and the reverse nesting order too',
    parseHostMounts('a.example.org => /x/y\nb.example.org => /x', apex).errors.length,
    1,
  );
  eq('a non-hostname is refused', parseHostMounts('not a host => /x', apex).errors.length, 1);
  eq('two arrows are refused', parseHostMounts('a.example.org => /x => /y', apex).errors.length, 1);

  // Errors are collected, not thrown on the first one: an operator fixing a
  // dispatch should see every problem in one pass.
  eq(
    'every bad line is reported, not just the first',
    parseHostMounts('bad one\nalso bad\nstill.bad', apex).errors.length,
    3,
  );

  // A good line alongside a bad one still parses — the caller decides to abort
  // on errors, but the report has to show what WOULD have run.
  const mixed = parseHostMounts('school.example.org => /school\nbroken', apex);
  eq('a valid line survives an invalid neighbour', mixed.mounts, [
    { host: 'school.example.org', mount: 'school' },
  ]);
  eq('and the invalid one is still reported', mixed.errors.length, 1);

  console.log(failures === 0 ? '\nall self-tests passed' : `\n${failures} self-test failure(s)`);
  return failures === 0 ? 0 : 1;
}

/* ------------------------------------------------------------------ */

import { pathToFileURL } from 'node:url';

const isMain = process.argv[1] ? import.meta.url === pathToFileURL(process.argv[1]).href : false;

if (isMain) {
  if (process.argv.includes('--self-test')) process.exit(selfTest());

  const arg = (name, def = '') => {
    const i = process.argv.indexOf(`--${name}`);
    return i > -1 && process.argv[i + 1] !== undefined ? process.argv[i + 1] : def;
  };
  const text = arg('text', '');
  const apex = arg('apex', '');
  if (!apex) {
    console.error('Usage: --text "<input>" --apex <domain> [--json]');
    process.exit(2);
  }
  const { mounts, errors } = parseHostMounts(text, apex);
  for (const e of errors) console.error(`::error::extra_hosts ${e}`);
  if (process.argv.includes('--json')) {
    console.log(JSON.stringify({ mounts, errors }));
  } else {
    for (const m of mounts) console.log(`${m.host}\t${m.mount}`);
  }
  process.exit(errors.length ? 1 : 0);
}
