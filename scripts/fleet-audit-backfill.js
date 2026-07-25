#!/usr/bin/env node
'use strict';

// CLI front-end for workflow 742 (Fleet Security Audit Backfill). All decisions
// live in scripts/fleet-audit-backfill-lib.js; this file is argument handling
// and file I/O only.
//
// It exists as a file rather than a set of inline `node -e` snippets in the
// workflow for two reasons: a JS template literal inside a single-quoted shell
// argument reads to shellcheck as an unexpanded shell expansion (SC2016, which
// broke 741's CI), and a real file can be exercised end-to-end by the unit
// tests instead of only through the YAML.
//
// Subcommands:
//   probe <package.json>                     -> "true" | "false" (audit:high present)
//   validate-template <security-audit.yml>   -> exit 1 + reasons if unusable
//   plan <candidates.json>                   -> plan JSON on stdout
//   summary <plan.json> <dryRun> <limit>     -> markdown on stdout
//   pr-body <target.json>                    -> markdown on stdout
//   apply <repoDir> <repo> <cron> <template> -> result JSON on stdout
//
// Exit codes for `apply` are load-bearing: 0 = wrote (or already done),
// 2 = BLOCKED, skip this repo and report it, 1 = unexpected error. The caller
// must never treat 2 as a reason to write anything.

const fs = require('fs');
const path = require('path');
const lib = require('./fleet-audit-backfill-lib.js');
const coverage = require('./fleet-audit-coverage-lib.js');

const BLOCKED = 2;

function read(file) {
  return fs.readFileSync(file, 'utf8');
}

function readJson(file) {
  return JSON.parse(read(file));
}

function fail(message, code) {
  process.stderr.write(`${message}\n`);
  process.exit(code === undefined ? 1 : code);
}

/**
 * Add both halves of the audit to an already-cloned repo.
 *
 * Ordering is deliberate: package.json is edited FIRST and in memory, and
 * nothing is written to disk until both halves are known to be producible. A
 * repo whose package.json cannot be edited must not be left holding a workflow
 * file that fails on every run — that is the `partial` state #840's review
 * called out as worse than uncovered.
 */
function apply(repoDir, repoFullName, cron, templateFile) {
  const template = read(templateFile);
  const rendered = lib.renderAuditWorkflow(template, cron);
  if (rendered.error) {
    fail(`${repoFullName}: ${rendered.error}`, BLOCKED);
  }

  const pkgPath = path.join(repoDir, 'package.json');
  if (!fs.existsSync(pkgPath)) {
    fail(`${repoFullName}: no package.json in the clone`, BLOCKED);
  }
  const pkgRaw = read(pkgPath);
  const edit = lib.addAuditScript(pkgRaw);
  if (!edit.changed && edit.reason !== 'already-present') {
    fail(`${repoFullName}: ${edit.reason}`, BLOCKED);
  }

  const wfPath = path.join(repoDir, lib.AUDIT_WORKFLOW_PATH);
  const workflowExisted = fs.existsSync(wfPath);

  // Both halves are producible — now write.
  if (edit.changed) {
    fs.writeFileSync(pkgPath, edit.content);
  }
  fs.mkdirSync(path.dirname(wfPath), { recursive: true });
  if (!workflowExisted) {
    fs.writeFileSync(wfPath, rendered.content);
  }

  // Read back and re-derive coverage with 741's own parsers. The audit that
  // decides whether this repo is covered must agree with what was just written,
  // or the next sweep re-lists a repo this run reported as done.
  const finalPkg = read(pkgPath);
  const finalWf = read(wfPath);
  const covered = coverage.hasAuditScript(finalPkg) && Boolean(finalWf.trim());
  if (!covered) {
    fail(`${repoFullName}: post-write coverage check failed — refusing to report success`, 1);
  }

  return {
    repo: repoFullName,
    wroteWorkflow: !workflowExisted,
    wroteScript: edit.changed,
    workflowAlreadyPresent: workflowExisted,
    scriptAlreadyPresent: edit.reason === 'already-present',
    cron: coverage.extractCron(finalWf),
  };
}

function main(argv) {
  const [cmd, ...args] = argv;
  switch (cmd) {
    case 'probe': {
      process.stdout.write(String(coverage.hasAuditScript(read(args[0]))));
      return;
    }
    case 'validate-template': {
      const problems = lib.validateTemplate(read(args[0]));
      if (problems.length) {
        fail(
          `canonical ${lib.AUDIT_WORKFLOW_PATH} from ${lib.CANONICAL_REPO} is unusable:\n` +
            problems.map((p) => `  - ${p}`).join('\n'),
        );
      }
      process.stdout.write('ok');
      return;
    }
    case 'plan': {
      process.stdout.write(JSON.stringify(lib.planTargets(readJson(args[0]))));
      return;
    }
    case 'summary': {
      const plan = readJson(args[0]);
      const limit = Number(args[2]);
      process.stdout.write(
        lib.renderPlanSummary(plan, {
          dryRun: args[1] === 'true',
          limit: Number.isFinite(limit) && limit > 0 ? limit : 0,
        }),
      );
      return;
    }
    case 'pr-body': {
      process.stdout.write(lib.renderPrBody(readJson(args[0])));
      return;
    }
    case 'apply': {
      const result = apply(args[0], args[1], args[2], args[3]);
      process.stdout.write(JSON.stringify(result));
      return;
    }
    default:
      fail(`unknown subcommand ${JSON.stringify(cmd)}`);
  }
}

if (require.main === module) {
  main(process.argv.slice(2));
}

module.exports = { apply, main };
