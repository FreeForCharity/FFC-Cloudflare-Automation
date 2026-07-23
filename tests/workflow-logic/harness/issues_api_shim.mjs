// Harness for unit-testing actions/github-script bodies that drive the
// **issue-lifecycle API** (list/search open issues → comment / create / close)
// outside a runner — e.g. the 732 Google-workflow rolling failure alert and the
// 733 quarterly credential-rotation reminders. The sibling
// github_script_shim.mjs only stubs createComment (701's idempotency scan) and
// actions_run_shim.mjs covers the Actions runs API, so this driver adds the
// rolling-issue upsert/close surface:
//   - github.rest.issues.listForRepo  (returns a fixture set of open issues;
//     records the filter args so tests can assert state/labels)
//   - github.rest.search.issuesAndPullRequests (marker-based dedupe lookup;
//     records every query string and matches on quoted markers in the query)
//   - github.rest.issues.create       (records the created issue)
//   - github.rest.issues.createComment(records every comment posted)
//   - github.rest.issues.update       (records state transitions, e.g. close)
//
// Inputs (env):
//   TEST_SCRIPT_FILE          path to the extracted github-script body
//   TEST_CONTEXT_FILE         JSON: { repo:{owner,repo}, payload:{...} }
//   TEST_OPEN_ISSUES_FILE     JSON array of open issue objects [{number,body}]
//                             returned by listForRepo (default: [] = none open)
//   TEST_EXISTING_MARKERS_FILE JSON array of marker strings that already have an
//                             issue; the search mock returns one item when the
//                             query quotes a listed marker (default: none exist)
//   TEST_SEARCH_THROWS        when "1", the search mock rejects — proves the
//                             script's own .catch() fallback treats a failed
//                             lookup as "not found" rather than skipping work
//   TEST_NOW_MS               when set, freezes `new Date()`/`Date.now()` to this
//                             epoch-ms so date-derived logic (e.g. the quarter
//                             label) is deterministic
//
// Emits one JSON result line:
//   { failed, threw, notices, infos, listForRepoCalls, searchCalls, created,
//     comments, updates }

import { readFileSync } from 'node:fs';

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
const openIssues = process.env.TEST_OPEN_ISSUES_FILE
  ? JSON.parse(readFileSync(process.env.TEST_OPEN_ISSUES_FILE, 'utf8'))
  : [];
const existingMarkers = process.env.TEST_EXISTING_MARKERS_FILE
  ? JSON.parse(readFileSync(process.env.TEST_EXISTING_MARKERS_FILE, 'utf8'))
  : [];
const searchThrows = process.env.TEST_SEARCH_THROWS === '1';

const notices = [];
const infos = [];
const listForRepoCalls = [];
const searchCalls = [];
const created = [];
const comments = [];
const updates = [];
let failed = null;

const core = {
  setOutput: () => {},
  setFailed: (m) => {
    failed = String(m);
  },
  notice: (m) => notices.push(String(m)),
  warning: () => {},
  info: (m) => infos.push(String(m)),
  error: () => {},
  debug: () => {},
};

let nextNumber = 1000;
const github = {
  rest: {
    search: {
      issuesAndPullRequests: async (args) => {
        searchCalls.push({ q: args.q, per_page: args.per_page });
        if (searchThrows) throw new Error('simulated search API failure');
        // Match on the marker the script quotes in its query — dedupe must key
        // on the marker literal, not merely on any open labelled issue.
        const quoted = String(args.q).match(/"([^"]*)"/);
        const marker = quoted ? quoted[1] : null;
        const items =
          marker && existingMarkers.includes(marker)
            ? [{ number: nextNumber++, body: `${marker}\nexisting` }]
            : [];
        return { data: { items } };
      },
    },
    issues: {
      listForRepo: async (args) => {
        listForRepoCalls.push({
          state: args.state,
          labels: args.labels,
          per_page: args.per_page,
        });
        return { data: openIssues };
      },
      create: async (args) => {
        const number = nextNumber++;
        created.push({
          number,
          title: args.title,
          labels: args.labels,
          body: args.body,
        });
        return { data: { number } };
      },
      createComment: async (args) => {
        comments.push({ issue_number: args.issue_number, body: args.body });
        return { data: { id: comments.length } };
      },
      update: async (args) => {
        updates.push({ issue_number: args.issue_number, state: args.state });
        return { data: {} };
      },
    },
  },
};

const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const fn = new AsyncFunction('context', 'core', 'github', scriptBody);

let threw = null;
try {
  await fn(context, core, github);
} catch (e) {
  threw = String(e && e.stack ? e.stack : e);
}

console.log(
  JSON.stringify({
    failed,
    threw,
    notices,
    infos,
    listForRepoCalls,
    searchCalls,
    created,
    comments,
    updates,
  }),
);
