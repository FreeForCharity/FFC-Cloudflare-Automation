// Harness for unit-testing actions/github-script bodies that drive the
// **issue-lifecycle API** (list open issues → comment / create / close) outside
// a runner — e.g. the 732 Google-workflow rolling failure alert. The sibling
// github_script_shim.mjs only stubs createComment (701's idempotency scan) and
// actions_run_shim.mjs covers the Actions runs API, so this driver adds the
// rolling-issue upsert/close surface:
//   - github.rest.issues.listForRepo  (returns a fixture set of open issues;
//     records the filter args so tests can assert state/labels)
//   - github.rest.issues.create       (records the created issue)
//   - github.rest.issues.createComment(records every comment posted)
//   - github.rest.issues.update       (records state transitions, e.g. close)
//
// Inputs (env):
//   TEST_SCRIPT_FILE       path to the extracted github-script body
//   TEST_CONTEXT_FILE      JSON: { repo:{owner,repo}, payload:{ workflow_run } }
//   TEST_OPEN_ISSUES_FILE  JSON array of open issue objects [{number,body,...}]
//                          returned by listForRepo (default: [] = none open)
//
// Emits one JSON result line:
//   { failed, threw, notices, listForRepoCalls, created, comments, updates }

import { readFileSync } from 'node:fs';

const scriptBody = readFileSync(process.env.TEST_SCRIPT_FILE, 'utf8');
const context = JSON.parse(readFileSync(process.env.TEST_CONTEXT_FILE, 'utf8'));
const openIssues = process.env.TEST_OPEN_ISSUES_FILE
  ? JSON.parse(readFileSync(process.env.TEST_OPEN_ISSUES_FILE, 'utf8'))
  : [];

const notices = [];
const listForRepoCalls = [];
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
  info: () => {},
  error: () => {},
  debug: () => {},
};

let nextNumber = 1000;
const github = {
  rest: {
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
    listForRepoCalls,
    created,
    comments,
    updates,
  }),
);
