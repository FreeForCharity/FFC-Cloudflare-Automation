// Harness for unit-testing the 737 claim-sync **sweep** github-script outside a
// runner. The sibling issues_api_shim.mjs drives rolling-issue alerters
// (list/search/create/close); this one drives the label-reconciliation surface
// the sweep actually touches, which that shim does not model:
//   - github.rest.pulls.list                    (hub open PRs, paged)
//   - github.rest.search.issuesAndPullRequests  (org-wide open PRs, paged, with
//     total_count so a truncated read is expressible)
//   - github.rest.issues.listForRepo            (open issues carrying `claimed`)
//   - github.rest.issues.get                    (is the referenced issue real,
//     open, and unlabeled — rejects for a number absent from the fixture)
//   - github.rest.issues.listComments           (marker lookup, paged)
//   - github.rest.issues.addLabels / removeLabel / createComment (the writes)
//   - core.summary                              (chainable no-op recorder)
//
// The script `require`s scripts/claim-sync-lib.js from GITHUB_WORKSPACE, so
// pointing GITHUB_WORKSPACE at a temp tree holding a MUTATED copy of the lib is
// how the mutation tests reintroduce a defect and prove a case fails without the
// fix (#935).
//
// Inputs (env):
//   TEST_SCRIPT_FILE        path to the extracted github-script body
//   TEST_CONTEXT_FILE       JSON: { repo:{owner,repo}, payload:{...} }
//   GITHUB_WORKSPACE        tree whose scripts/claim-sync-lib.js is required
//   TEST_HUB_PRS_FILE       JSON array of hub PRs [{number, body}]
//   TEST_ORG_PRS_FILE       JSON array of org-wide search items
//                           [{number, body, repository_url|html_url, author?}]
//                           `author` is matched by `-author:` terms in the query
//   TEST_SEARCH_TOTAL       override total_count to model the 1,000-result cap
//                           (a complete-LOOKING response that is missing rows).
//                           Without it, total_count is the number of rows that
//                           MATCHED the query while at most 1,000 are served —
//                           the cap reproduced from the fixture population.
//   TEST_SEARCH_INCOMPLETE  "1" -> incomplete_results on the first page
//   TEST_SEARCH_THROWS      "1" -> the search mock rejects
//   TEST_CLAIMED_ISSUES_FILE JSON array of open `claimed` issues
//                           [{number, updated_at, labels, pull_request?}]
//   TEST_ISSUES_FILE        JSON map issue number -> issue object served by
//                           issues.get; an absent number rejects (404)
//   TEST_COMMENTS_FILE      JSON map issue number -> [{body}]
//   TEST_NOW_MS             freezes Date.now()/new Date() for the expiry maths
//   DRY_RUN                 passed through to the script unchanged
//
// Emits one JSON result line:
//   { failed, threw, warnings, infos, logs, searchCalls, pullsListCalls,
//     listForRepoCalls, getCalls, listCommentsCalls, addedLabels,
//     removedLabels, comments, summary }

import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';

// Freeze the clock BEFORE the script runs (see issues_api_shim.mjs): the sweep
// reads Date.now() once and compares it against each issue's updated_at.
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

const readJson = (envVar, fallback) =>
  process.env[envVar] ? JSON.parse(readFileSync(process.env[envVar], 'utf8')) : fallback;

const scriptBody = readFileSync(process.env.TEST_SCRIPT_FILE, 'utf8');
const context = JSON.parse(readFileSync(process.env.TEST_CONTEXT_FILE, 'utf8'));
const hubPrs = readJson('TEST_HUB_PRS_FILE', []);
const orgPrs = readJson('TEST_ORG_PRS_FILE', []);
const claimedIssues = readJson('TEST_CLAIMED_ISSUES_FILE', []);
const issuesById = readJson('TEST_ISSUES_FILE', {});
const commentsById = readJson('TEST_COMMENTS_FILE', {});
const searchThrows = process.env.TEST_SEARCH_THROWS === '1';
const searchIncomplete = process.env.TEST_SEARCH_INCOMPLETE === '1';

const warnings = [];
const infos = [];
const logs = [];
const searchCalls = [];
const pullsListCalls = [];
const listForRepoCalls = [];
const getCalls = [];
const listCommentsCalls = [];
const addedLabels = [];
const removedLabels = [];
const comments = [];
const summary = [];
let failed = null;

// Paged exactly like the real endpoints: a caller that reads only page 1
// provably cannot see fixture rows past it.
// GitHub's Search API never serves more than this many results for one query,
// however many match (`total_count` still reports the true figure).
const SEARCH_CAP = 1000;

const page = (rows, args) => {
  const perPage = Math.min(args.per_page || 30, 100);
  const p = args.page || 1;
  return rows.slice((p - 1) * perPage, p * perPage);
};

const core = {
  setOutput: () => {},
  setFailed: (m) => {
    failed = String(m);
  },
  notice: () => {},
  warning: (m) => warnings.push(String(m)),
  info: (m) => infos.push(String(m)),
  error: () => {},
  debug: () => {},
  summary: {
    addHeading: (m) => {
      summary.push(String(m));
      return core.summary;
    },
    addRaw: (m) => {
      summary.push(String(m));
      return core.summary;
    },
    write: async () => core.summary,
  },
};

const github = {
  rest: {
    pulls: {
      list: async (args) => {
        pullsListCalls.push({
          owner: args.owner,
          repo: args.repo,
          state: args.state,
          page: args.page,
        });
        return { data: page(hubPrs, args) };
      },
    },
    search: {
      issuesAndPullRequests: async (args) => {
        searchCalls.push({ q: args.q, per_page: args.per_page, page: args.page });
        if (searchThrows) throw new Error('simulated search API failure');
        // Honor `-author:` exclusions and the hard 1,000-result cap, because the
        // sweep's remaining defect was a QUERY-SCOPE one: too broad a query is
        // served short, with incomplete_results FALSE, and the cross-repo pass
        // skips itself (#939). A mock that ignores `q` cannot tell the fixed
        // query from the broken one, so the fix would be untestable. A fixture
        // row's optional `author` is what an exclusion term matches.
        const excluded = [...String(args.q || '').matchAll(/-author:(\S+)/g)].map((m) =>
          m[1].toLowerCase(),
        );
        const matched = orgPrs.filter(
          (it) => !excluded.includes(String(it.author || '').toLowerCase()),
        );
        // total_count counts what matched; at most SEARCH_CAP rows are ever
        // served. That gap IS the silently-short read.
        const items = page(matched.slice(0, SEARCH_CAP), args);
        return {
          data: {
            total_count: process.env.TEST_SEARCH_TOTAL
              ? Number(process.env.TEST_SEARCH_TOTAL)
              : matched.length,
            incomplete_results: searchIncomplete && (args.page || 1) === 1,
            items,
          },
        };
      },
    },
    issues: {
      listForRepo: async (args) => {
        listForRepoCalls.push({ state: args.state, labels: args.labels, page: args.page });
        return { data: page(claimedIssues, args) };
      },
      get: async (args) => {
        getCalls.push(args.issue_number);
        const issue = issuesById[String(args.issue_number)];
        // Reject rather than return null: the script's `.catch` on a missing or
        // unreadable issue is part of what is under test.
        if (!issue) throw new Error(`404 issue #${args.issue_number}`);
        return { data: issue };
      },
      listComments: async (args) => {
        listCommentsCalls.push({ issue_number: args.issue_number, page: args.page });
        return { data: page(commentsById[String(args.issue_number)] || [], args) };
      },
      addLabels: async (args) => {
        addedLabels.push({ issue_number: args.issue_number, labels: args.labels });
        return { data: {} };
      },
      removeLabel: async (args) => {
        removedLabels.push({ issue_number: args.issue_number, name: args.name });
        return { data: {} };
      },
      createComment: async (args) => {
        comments.push({ issue_number: args.issue_number, body: args.body });
        return { data: { id: comments.length } };
      },
    },
  },
};

const realLog = console.log;
console.log = (...a) => logs.push(a.map(String).join(' '));

const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
// `require` is ambient inside actions/github-script (a CommonJS step); this file
// is ESM, so it is injected explicitly and resolves from the harness directory —
// the script passes an absolute GITHUB_WORKSPACE path, so the base is irrelevant.
const fn = new AsyncFunction('context', 'core', 'github', 'require', scriptBody);

let threw = null;
try {
  await fn(context, core, github, createRequire(import.meta.url));
} catch (e) {
  threw = String(e && e.stack ? e.stack : e);
}

console.log = realLog;
console.log(
  JSON.stringify({
    failed,
    threw,
    warnings,
    infos,
    logs,
    searchCalls,
    pullsListCalls,
    listForRepoCalls,
    getCalls,
    listCommentsCalls,
    addedLabels,
    removedLabels,
    comments,
    summary,
  }),
);
