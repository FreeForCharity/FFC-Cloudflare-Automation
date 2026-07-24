// Harness for unit-testing actions/github-script bodies that drive the
// **repo labels API** (read a label config → get/update-or-create each label)
// outside a runner — e.g. the 724 "Initialize Labels" workflow. Neither existing
// shim fits: github_script_shim.mjs / issues_api_shim.mjs / actions_run_shim.mjs
// expose the issue-lifecycle and Actions-runs surfaces, not the label surface,
// and 724 also reads its config through `require('fs')` + `require('js-yaml')`
// (an ESM .mjs has no global `require`, so it must be passed in). This driver:
//   - passes `require` as a 4th AsyncFunction param bound to a capturing shim:
//       * require('fs')/'node:fs'  -> an in-memory readFileSync that records the
//         path read (so a test can assert the config path) and returns a stub
//         string; the js-yaml stub ignores it, so no real YAML text is needed.
//       * require('js-yaml')       -> { load: () => <labels fixture> }, injecting
//         the parsed label array directly. The point of these tests is the
//         get/update/create decision loop, NOT js-yaml's parsing (js-yaml is not
//         installed in the plain-node CI harness), so the fixture is the parse.
//       * anything else            -> delegated to the real createRequire.
//   - github.rest.issues.getLabel   resolves for names in the "existing" set and
//     throws { status: 404 } otherwise (or a configurable non-404 for one name).
//   - github.rest.issues.updateLabel / createLabel record the names they touch
//     (and can be made to throw for one name to prove the outer catch swallows a
//     per-label failure and the sweep continues).
//   - console.log / console.error are captured (not printed) so the only stdout
//     line is the JSON result.
//
// Inputs (env):
//   TEST_SCRIPT_FILE          path to the extracted github-script body
//   TEST_CONTEXT_FILE         JSON: { repo: { owner, repo } }
//   TEST_LABELS_FILE          JSON array [{ name, description, color }] returned
//                             by the js-yaml stub (default: [])
//   TEST_EXISTING_LABELS_FILE JSON array of label names for which getLabel
//                             resolves (all others 404) (default: none exist)
//   TEST_GETLABEL_ERROR       JSON { name, status } — getLabel throws this status
//                             (non-404) for that one label instead of resolving
//   TEST_WRITE_FAIL           a label name whose create/update throws, to prove a
//                             per-label write failure is swallowed
//
// Emits one JSON result line:
//   { threw, getLabelCalls, updateCalls, createCalls, fsReads, logs, errors }

import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';

const realRequire = createRequire(import.meta.url);

const scriptBody = readFileSync(process.env.TEST_SCRIPT_FILE, 'utf8');
const context = JSON.parse(readFileSync(process.env.TEST_CONTEXT_FILE, 'utf8'));
const labelsFixture = process.env.TEST_LABELS_FILE
  ? JSON.parse(readFileSync(process.env.TEST_LABELS_FILE, 'utf8'))
  : [];
const existing = process.env.TEST_EXISTING_LABELS_FILE
  ? JSON.parse(readFileSync(process.env.TEST_EXISTING_LABELS_FILE, 'utf8'))
  : [];
const getLabelError = process.env.TEST_GETLABEL_ERROR
  ? JSON.parse(process.env.TEST_GETLABEL_ERROR)
  : null;
const writeFail = process.env.TEST_WRITE_FAIL || null;

const fsReads = [];
const getLabelCalls = [];
const updateCalls = [];
const createCalls = [];
const logs = [];
const errors = [];

// Capture console output so the only stdout line is the final JSON result.
const emit = process.stdout.write.bind(process.stdout);
globalThis.console = {
  log: (...a) => logs.push(a.map(String).join(' ')),
  error: (...a) => errors.push(a.map(String).join(' ')),
  warn: () => {},
  info: () => {},
  debug: () => {},
};

const requireShim = (name) => {
  if (name === 'fs' || name === 'node:fs') {
    return {
      readFileSync: (p) => {
        fsReads.push(String(p));
        return ''; // js-yaml stub ignores this; the fixture is the "parse".
      },
    };
  }
  if (name === 'js-yaml') {
    return { load: () => labelsFixture };
  }
  return realRequire(name);
};

const core = {
  setOutput: () => {},
  setFailed: () => {},
  notice: () => {},
  warning: () => {},
  info: () => {},
  error: () => {},
  debug: () => {},
};

const github = {
  rest: {
    issues: {
      getLabel: async (args) => {
        getLabelCalls.push(args.name);
        if (getLabelError && getLabelError.name === args.name) {
          const e = new Error(`simulated getLabel failure (${getLabelError.status})`);
          e.status = getLabelError.status;
          throw e;
        }
        if (existing.includes(args.name)) {
          return { data: { name: args.name } };
        }
        const e = new Error('Not Found');
        e.status = 404;
        throw e;
      },
      updateLabel: async (args) => {
        if (writeFail === args.name) throw new Error('simulated updateLabel failure');
        updateCalls.push(args.name);
        return { data: {} };
      },
      createLabel: async (args) => {
        if (writeFail === args.name) throw new Error('simulated createLabel failure');
        createCalls.push(args.name);
        return { data: {} };
      },
    },
  },
};

const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const fn = new AsyncFunction('context', 'core', 'github', 'require', scriptBody);

let threw = null;
try {
  await fn(context, core, github, requireShim);
} catch (e) {
  threw = String(e && e.stack ? e.stack : e);
}

emit(
  JSON.stringify({
    threw,
    getLabelCalls,
    updateCalls,
    createCalls,
    fsReads,
    logs,
    errors,
  }) + '\n',
);
