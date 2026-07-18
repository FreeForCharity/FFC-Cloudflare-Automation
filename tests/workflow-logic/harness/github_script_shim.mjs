// Harness for unit-testing actions/github-script step bodies outside a runner.
//
// Reads the extracted script from the file named by TEST_SCRIPT_FILE and the
// simulated event context (JSON) from TEST_CONTEXT_FILE. Mirrors how
// actions/github-script executes the body: wrapped in an async function with
// `context`, `core`, and `github` in scope. Prints a single JSON result line:
//   { outputs: {..}, failed: string|null, notices: [..], threw: string|null }

import { readFileSync } from 'node:fs';

const scriptBody = readFileSync(process.env.TEST_SCRIPT_FILE, 'utf8');
const context = JSON.parse(readFileSync(process.env.TEST_CONTEXT_FILE, 'utf8'));

const outputs = {};
const notices = [];
let failed = null;

const core = {
  setOutput: (k, v) => {
    outputs[k] = String(v);
  },
  setFailed: (m) => {
    failed = String(m);
  },
  notice: (m) => notices.push(String(m)),
  warning: () => {},
  info: () => {},
  error: () => {},
  debug: () => {},
};

// Only the issues-event path talks to the API (idempotency comment scan);
// the manual/dispatch paths never touch `github`. An empty paginate result
// is the correct simulation for "no prior comments".
const github = {
  paginate: async () => [],
  rest: { issues: { listComments: 'listComments' } },
};

const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const fn = new AsyncFunction('context', 'core', 'github', scriptBody);

let threw = null;
try {
  await fn(context, core, github);
} catch (e) {
  threw = String(e && e.stack ? e.stack : e);
}

console.log(JSON.stringify({ outputs, failed, notices, threw }));
