#!/usr/bin/env node
'use strict';

// Decision logic for workflow 751 (Website - Security Overrides Sync). The
// workflow brings an FFC-EX site's dependency security floor up to the template
// it was made from: the template's `pnpm.overrides` block plus the direct
// dependencies whose old versions carry advisories. Dependabot cannot do this,
// because the advisories it leaves open are transitive (`source-map-js` through
// `postcss`, #1640).
//
// Subcommands:
//   detect <scripts/check-drift.mjs>             -> "footer" | "single" (exit 2 if neither)
//   merge <template package.json> <package.json> -> rewrites the target, prints JSON
//                                                   {"changes": [...]} on stdout
//
// Exit codes are load-bearing: 0 = done, 2 = BLOCKED (report the repo, write
// nothing), 1 = unexpected error.

const fs = require('fs');

const BLOCKED = 2;

// Each template's drift guard opens with its own banner. A repo that carries
// neither banner has drifted from both templates, and guessing its template is
// how the first manual run copied the Single Page block into two Footer-Only
// sites (#1640).
const TEMPLATE_MARKERS = {
  footer: 'FFC footer-only drift guard.',
  single: 'FFC drift guard — runs in CI',
};

// Direct dependencies raised to the template's floor. `next` carries the
// critical advisories; `postcss` matters because the templates override
// postcss with `$postcss` (the direct version), so an old direct postcss keeps
// its advisory however the overrides read.
const FLOOR_DEPS = ['next', 'postcss'];

function detectTemplate(driftSource) {
  const hits = Object.entries(TEMPLATE_MARKERS)
    .filter(([, marker]) => driftSource.includes(marker))
    .map(([name]) => name);
  return hits.length === 1 ? hits[0] : null;
}

function versionFloor(spec) {
  const m = String(spec).match(/(\d+)\.(\d+)\.(\d+)/);
  return m ? m.slice(1).map(Number) : null;
}

function isLower(a, b) {
  for (let i = 0; i < 3; i++) {
    if (a[i] !== b[i]) return a[i] < b[i];
  }
  return false;
}

// A scoped override such as `nanoid@<3.3.18` is dead once the same block pins
// bare `nanoid`: every version is already forced, so the selector never
// matches anything the bare key does not cover.
function scopedBase(key) {
  const at = key.indexOf('@', key.startsWith('@') ? 1 : 0);
  return at > 0 ? key.slice(0, at) : null;
}

function mergePackage(template, target) {
  const out = JSON.parse(JSON.stringify(target));
  const changes = [];

  for (const dep of FLOOR_DEPS) {
    for (const section of ['dependencies', 'devDependencies']) {
      const want = template[section] && template[section][dep];
      const have = out[section] && out[section][dep];
      if (!want || !have) continue;
      const w = versionFloor(want);
      const h = versionFloor(have);
      if (w && h && isLower(h, w)) {
        out[section][dep] = want;
        changes.push(`${dep} ${have} -> ${want}`);
      }
    }
  }

  const tplOverrides = (template.pnpm && template.pnpm.overrides) || {};
  out.pnpm = out.pnpm || {};
  const overrides = (out.pnpm.overrides = out.pnpm.overrides || {});
  for (const [key, value] of Object.entries(tplOverrides)) {
    if (overrides[key] !== value) {
      changes.push(
        `override ${key}: ${overrides[key] === undefined ? '(none)' : overrides[key]} -> ${value}`,
      );
      overrides[key] = value;
    }
  }
  for (const key of Object.keys(overrides)) {
    const base = scopedBase(key);
    if (base && !(key in tplOverrides) && base in tplOverrides) {
      changes.push(`override ${key}: dropped (bare ${base} is pinned)`);
      delete overrides[key];
    }
  }
  return { pkg: out, changes };
}

// Keep the file's own indent and final newline. The indent is read from
// spaces and tabs only: `\s` also matches CR and LF, so on a CRLF file it
// swallowed the line break and wrote a blank line after every key (seen on the
// first manual run, #1640).
function serialize(pkg, raw) {
  const m = raw.match(/^([ \t]+)"/m);
  const indent = m ? m[1] : '  ';
  const eol = raw.includes('\r\n') ? '\r\n' : '\n';
  let text = JSON.stringify(pkg, null, indent);
  if (eol === '\r\n') text = text.replace(/\n/g, '\r\n');
  return raw.endsWith('\n') ? text + eol : text;
}

function main(argv) {
  const [cmd, ...args] = argv;
  if (cmd === 'detect') {
    const t = detectTemplate(fs.readFileSync(args[0], 'utf8'));
    if (!t) {
      process.stderr.write('drift guard matches neither template banner\n');
      return BLOCKED;
    }
    process.stdout.write(t + '\n');
    return 0;
  }
  if (cmd === 'merge') {
    const template = JSON.parse(fs.readFileSync(args[0], 'utf8'));
    const raw = fs.readFileSync(args[1], 'utf8');
    let target;
    try {
      target = JSON.parse(raw);
    } catch (e) {
      process.stderr.write(`package.json does not parse: ${e.message}\n`);
      return BLOCKED;
    }
    const { pkg, changes } = mergePackage(template, target);
    if (changes.length) fs.writeFileSync(args[1], serialize(pkg, raw));
    process.stdout.write(JSON.stringify({ changes }) + '\n');
    return 0;
  }
  process.stderr.write(
    'usage: fleet-overrides-sync.js detect <file> | merge <template> <target>\n',
  );
  return 1;
}

module.exports = { detectTemplate, mergePackage, serialize, TEMPLATE_MARKERS, FLOOR_DEPS };

if (require.main === module) {
  process.exitCode = main(process.argv.slice(2));
}
