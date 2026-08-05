// Harness for unit-testing actions/github-script bodies that **aggregate
// Actions-run data and write a JSON metrics file** outside a runner — e.g. the
// 731 30-day per-workflow run-metrics job. Neither existing driver fits: the
// sibling actions_run_shim.mjs mocks the runs *list/cancel* surface but has no
// `require('fs')` (731 writes its artifact via `require('fs').writeFileSync`),
// and issues_api_shim.mjs is the issue-lifecycle surface. This driver adds:
//   - github.rest.actions.listWorkflowRunsForRepo (paginated over a fixture set;
//     records the `created` filter + page so window/pagination are assertable)
//   - a chainable core.summary stub (addHeading/addRaw/write)
//   - a `require` that returns a capturing `fs` (writeFileSync -> `files` map)
//     for 'fs'/'node:fs' and delegates everything else to the real require, so
//     the metrics artifact can be inspected without touching the real disk
//   - an optional TEST_NOW_MS clock freeze so `since` (Date.now()-30d) and
//     `generatedAt` (new Date()) are deterministic while `new Date(x)` still
//     parses the fixture timestamps normally
//
// Inputs (env):
//   TEST_SCRIPT_FILE   path to the extracted github-script body
//   TEST_CONTEXT_FILE  JSON: { repo:{owner,repo} }
//   TEST_RUNS_FILE     JSON array of run objects
//                      [{name,path,conclusion,run_started_at,updated_at}]
//                      returned (paginated, 100/page) by listWorkflowRunsForRepo
//   TEST_NOW_MS        when set, freezes `new Date()`/`Date.now()` to this epoch-ms
//
// Emits one JSON result line:
//   { failed, threw, listCalls, files, summaryText }

import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';

// Freeze the clock BEFORE the script runs so `new Date()` (no args) and
// `Date.now()` are deterministic while `new Date(x)` still parses normally.
if (process.env.TEST_NOW_MS) {
  const FIXED = Number(process.env.TEST_NOW_MS);
  const RealDate = Date;
  class MockDate extends RealDate {
    constructor(...args) {
      if (args.length === 0) super(FIXED);
      else super(...args);
    }
    static now() {
      return FIXED;
    }
  }
  globalThis.Date = MockDate;
}

const scriptBody = readFileSync(process.env.TEST_SCRIPT_FILE, 'utf8');
const context = JSON.parse(readFileSync(process.env.TEST_CONTEXT_FILE, 'utf8'));
const runs = process.env.TEST_RUNS_FILE
  ? JSON.parse(readFileSync(process.env.TEST_RUNS_FILE, 'utf8'))
  : [];

const PER_PAGE = 100;
const listCalls = [];
const summaryText = [];
const files = {};
let failed = null;

const summary = {
  addHeading: (t) => {
    summaryText.push(String(t));
    return summary;
  },
  addRaw: (t) => {
    summaryText.push(String(t));
    return summary;
  },
  addTable: (rows) => {
    summaryText.push(`TABLE:${Array.isArray(rows) ? rows.length : 0}`);
    return summary;
  },
  write: async () => {},
};

const core = {
  setOutput: () => {},
  setFailed: (m) => {
    failed = String(m);
  },
  notice: () => {},
  warning: () => {},
  info: () => {},
  error: () => {},
  debug: () => {},
  summary,
};

const github = {
  rest: {
    actions: {
      listWorkflowRunsForRepo: async (args) => {
        listCalls.push({ created: args.created, per_page: args.per_page, page: args.page });
        // Honor the caller's requested page size so pagination tests stay
        // faithful if the script changes per_page.
        const perPage = Number(args.per_page) || PER_PAGE;
        const page = args.page || 1;
        const start = (page - 1) * perPage;
        const slice = runs.slice(start, start + perPage);
        return { data: { total_count: runs.length, workflow_runs: slice } };
      },
    },
  },
};

// Capturing `require`: 'fs' writes go to the in-memory `files` map instead of
// disk; anything else falls through to the real module loader.
const realRequire = createRequire(import.meta.url);
const mockFs = {
  writeFileSync: (p, data) => {
    files[String(p)] = String(data);
  },
};
const requireShim = (id) => {
  if (id === 'fs' || id === 'node:fs') return mockFs;
  return realRequire(id);
};

const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const fn = new AsyncFunction('context', 'core', 'github', 'require', scriptBody);

let threw = null;
try {
  await fn(context, core, github, requireShim);
} catch (e) {
  threw = String(e && e.stack ? e.stack : e);
}

console.log(JSON.stringify({ failed, threw, listCalls, files, summaryText }));
