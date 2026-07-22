// Harness for unit-testing actions/github-script bodies that drive the
// **Actions runs API** (list / cancel workflow runs) outside a runner —
// e.g. the 734 stale-waiting-run janitor. The sibling github_script_shim.mjs
// only mocks the issues API (701's path), so this driver adds:
//   - github.rest.actions.listWorkflowRunsForRepo (paginated over a fixture set)
//   - github.rest.actions.cancelWorkflowRun (records every attempt; can be told
//     to fail specific ids so error handling is exercised)
//   - a chainable core.summary stub (addHeading/addRaw/addTable/write)
//
// Inputs (env):
//   TEST_SCRIPT_FILE   path to the extracted github-script body
//   TEST_CONTEXT_FILE  JSON: { repo:{owner,repo}, eventName, payload }
//   TEST_RUNS_FILE     JSON array of run objects [{id,name,created_at,html_url}]
//                      returned (paginated, 100/page) by listWorkflowRunsForRepo
//   TEST_CANCEL_FAIL_IDS  comma-separated run ids whose cancel call throws (409)
//   plus whatever env the step itself reads (MAX_AGE_DAYS, DRY_RUN, …)
//
// Emits one JSON result line:
//   { failed, threw, notices, logs, listCalls, cancelAttempts, cancelledIds, summaryText }

import { readFileSync } from 'node:fs';

const scriptBody = readFileSync(process.env.TEST_SCRIPT_FILE, 'utf8');
const context = JSON.parse(readFileSync(process.env.TEST_CONTEXT_FILE, 'utf8'));
const runs = process.env.TEST_RUNS_FILE
  ? JSON.parse(readFileSync(process.env.TEST_RUNS_FILE, 'utf8'))
  : [];
const cancelFailIds = new Set(
  (process.env.TEST_CANCEL_FAIL_IDS || '').split(',').filter(Boolean).map(Number),
);

const PER_PAGE = 100;
const notices = [];
const logs = [];
const listCalls = [];
const cancelAttempts = [];
const cancelledIds = [];
const summaryText = [];
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
  notice: (m) => notices.push(String(m)),
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
        listCalls.push({ status: args.status, per_page: args.per_page, page: args.page });
        const page = args.page || 1;
        const start = (page - 1) * PER_PAGE;
        const slice = runs.slice(start, start + PER_PAGE);
        return { data: { total_count: runs.length, workflow_runs: slice } };
      },
      cancelWorkflowRun: async (args) => {
        cancelAttempts.push(args.run_id);
        if (cancelFailIds.has(Number(args.run_id))) {
          const e = new Error('run is not cancellable');
          e.status = 409;
          throw e;
        }
        cancelledIds.push(args.run_id);
        return { data: {} };
      },
    },
  },
};

// Capture the janitor's console.log lines without polluting the result line.
const origLog = console.log;
console.log = (...a) => logs.push(a.map(String).join(' '));

const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const fn = new AsyncFunction('context', 'core', 'github', scriptBody);

let threw = null;
try {
  await fn(context, core, github);
} catch (e) {
  threw = String(e && e.stack ? e.stack : e);
}

console.log = origLog;
console.log(
  JSON.stringify({
    failed,
    threw,
    notices,
    logs,
    listCalls,
    cancelAttempts,
    cancelledIds,
    summaryText,
  }),
);
