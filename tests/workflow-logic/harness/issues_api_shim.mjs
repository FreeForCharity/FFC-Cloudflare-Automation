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
//   TEST_RUN_JOBS_FILE        JSON array of job objects [{name,conclusion}] returned by
//                             actions.listJobsForWorkflowRun (default: [] = none), so a
//                             script can tell a declined gate from a real job failure
//   TEST_JOBS_THROW           when "1", the jobs mock rejects — proves the caller treats an
//                             unreadable job list as "alert anyway", never as "stay quiet"
//   TEST_REPO_WORKFLOWS_FILE  JSON array of workflow objects [{id,name}] returned by
//                             actions.listRepoWorkflows. Served PAGED, honouring page/per_page,
//                             so a caller that forgets to paginate provably sees only the first
//                             page — the 105-workflow truncation from #843
//   TEST_WORKFLOW_RUNS_FILE   JSON map of workflow id (as a string) -> array of run objects
//                             returned by actions.listWorkflowRuns; a missing id yields none
//
//   --- multi-repo fixtures (#1296) ------------------------------------------------------
//   The two files above are repo-BLIND: every repo sees the same inventory and runs. That is
//   fine for a hub-only caller and useless for testing one that sweeps several repos — a
//   script that ignored `owner`/`repo` entirely would pass. These serve per-repo data and
//   record the repo on every call, so "it asked the satellite repo" is assertable:
//   TEST_REPO_WORKFLOWS_BY_REPO_FILE  JSON map "owner/repo" -> array of workflow objects.
//                             Takes precedence over TEST_REPO_WORKFLOWS_FILE for a listed
//                             repo; an UNLISTED repo falls back to the flat fixture, so an
//                             existing single-repo test keeps working untouched
//   TEST_WORKFLOW_RUNS_BY_REPO_FILE   JSON map "owner/repo" -> { workflow id -> runs }, same
//                             precedence and fallback
//   TEST_DEFAULT_BRANCHES_FILE JSON map "owner/repo" -> default branch returned by repos.get
//                             (default "main"), so a satellite on another default branch can
//                             be modelled — the shape that silently unwatches a whole repo
//   TEST_REPO_WORKFLOWS_THROW JSON array of "owner/repo" whose listRepoWorkflows rejects, to
//                             prove a repo the sweep cannot read is reported as a fault of
//                             the alerter rather than counted as a clean sweep
//   TEST_REPOS_GET_THROW      JSON array of "owner/repo" whose repos.get rejects
//   TEST_SUCCESS_RUNS_THROW   JSON array of workflow ids (as strings) whose listWorkflowRuns
//                             throws ONLY for the `status: 'success'` query, leaving the
//                             ordinary poll readable
//   TEST_RUNS_THROW           JSON array of workflow ids (as strings) whose listWorkflowRuns
//                             call rejects, to prove an unreadable run list is not read as green
//   TEST_SEARCH_THROWS        when "1", the search mock rejects — proves the
//                             script's own .catch() fallback treats a failed
//                             lookup as "not found" rather than skipping work
//   TEST_NOW_MS               when set, freezes `new Date()`/`Date.now()` to this
//                             epoch-ms so date-derived logic (e.g. the quarter
//                             label) is deterministic
//
// Emits one JSON result line:
//   { failed, threw, notices, warnings, infos, listForRepoCalls, searchCalls,
//     listJobsCalls, listRepoWorkflowsCalls, listWorkflowRunsCalls, created,
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
// Job list returned by actions.listJobsForWorkflowRun — lets a script tell a
// declined approval gate (run `failure`, jobs `cancelled`) from a real fault
// (some job `failure`), which the run-level conclusion alone cannot express.
const runJobs = process.env.TEST_RUN_JOBS_FILE
  ? JSON.parse(readFileSync(process.env.TEST_RUN_JOBS_FILE, 'utf8'))
  : null;
const jobsThrow = process.env.TEST_JOBS_THROW === '1';
// Workflow inventory + latest-run fixtures for POLL-style alerters (740): the script
// resolves watched names against listRepoWorkflows, then reads each one's latest run.
const repoWorkflows = process.env.TEST_REPO_WORKFLOWS_FILE
  ? JSON.parse(readFileSync(process.env.TEST_REPO_WORKFLOWS_FILE, 'utf8'))
  : [];
const workflowRuns = process.env.TEST_WORKFLOW_RUNS_FILE
  ? JSON.parse(readFileSync(process.env.TEST_WORKFLOW_RUNS_FILE, 'utf8'))
  : {};
const successRunsThrow = process.env.TEST_SUCCESS_RUNS_THROW
  ? JSON.parse(process.env.TEST_SUCCESS_RUNS_THROW).map(String)
  : [];
const runsThrow = process.env.TEST_RUNS_THROW
  ? JSON.parse(process.env.TEST_RUNS_THROW).map(String)
  : [];
// Per-repo overlays. `slug(args)` is how every mock below decides which repo it is being
// asked about; a caller that never varies owner/repo therefore only ever sees one entry.
const repoWorkflowsByRepo = process.env.TEST_REPO_WORKFLOWS_BY_REPO_FILE
  ? JSON.parse(readFileSync(process.env.TEST_REPO_WORKFLOWS_BY_REPO_FILE, 'utf8'))
  : null;
const workflowRunsByRepo = process.env.TEST_WORKFLOW_RUNS_BY_REPO_FILE
  ? JSON.parse(readFileSync(process.env.TEST_WORKFLOW_RUNS_BY_REPO_FILE, 'utf8'))
  : null;
const defaultBranches = process.env.TEST_DEFAULT_BRANCHES_FILE
  ? JSON.parse(readFileSync(process.env.TEST_DEFAULT_BRANCHES_FILE, 'utf8'))
  : {};
const repoWorkflowsThrow = process.env.TEST_REPO_WORKFLOWS_THROW
  ? JSON.parse(process.env.TEST_REPO_WORKFLOWS_THROW)
  : [];
const reposGetThrow = process.env.TEST_REPOS_GET_THROW
  ? JSON.parse(process.env.TEST_REPOS_GET_THROW)
  : [];
const slug = (args) => `${(args || {}).owner}/${(args || {}).repo}`;

const notices = [];
const warnings = [];
const infos = [];
const listForRepoCalls = [];
const searchCalls = [];
const listJobsCalls = [];
const listRepoWorkflowsCalls = [];
const reposGetCalls = [];
const listWorkflowRunsCalls = [];
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
  warning: (m) => warnings.push(String(m)),
  info: (m) => infos.push(String(m)),
  error: () => {},
  debug: () => {},
};

let nextNumber = 1000;
const github = {
  // Real octokit `paginate`: walks pages until a short page, unwrapping the single
  // array property of list responses (`workflows`, `workflow_runs`, `items`). Modelled
  // faithfully rather than stubbed to "return everything", because the bug under test
  // IS the missing pagination — a mock that ignores paging could not fail on it.
  paginate: async (route, params) => {
    const perPage = (params && params.per_page) || 30;
    const out = [];
    for (let page = 1; page <= 100; page += 1) {
      const res = await route({ ...params, page });
      const data = (res || {}).data || {};
      const items = Array.isArray(data)
        ? data
        : data.workflows || data.workflow_runs || data.items || [];
      out.push(...items);
      if (items.length < perPage) break;
    }
    return out;
  },
  rest: {
    actions: {
      listJobsForWorkflowRun: async (args) => {
        listJobsCalls.push({
          repo: slug(args),
          run_id: args.run_id,
          per_page: args.per_page,
          filter: args.filter,
        });
        if (jobsThrow) throw new Error('simulated jobs API failure');
        return { data: { jobs: runJobs || [] } };
      },
      // Paged on purpose. `per_page` caps at 100 on the real endpoint, so a caller that
      // reads one page silently loses every workflow past the first 100 — the truncation
      // that produced a false "740 is not registered" reading in #843. Only a caller that
      // paginates sees the tail of the fixture.
      listRepoWorkflows: async (args) => {
        const perPage = Math.min(args.per_page || 30, 100);
        const page = args.page || 1;
        const where = slug(args);
        listRepoWorkflowsCalls.push({ repo: where, per_page: args.per_page, page });
        if (repoWorkflowsThrow.includes(where)) {
          throw new Error(`simulated workflow-list API failure for ${where}`);
        }
        const all =
          repoWorkflowsByRepo && repoWorkflowsByRepo[where]
            ? repoWorkflowsByRepo[where]
            : repoWorkflows;
        const slice = all.slice((page - 1) * perPage, page * perPage);
        return { data: { total_count: all.length, workflows: slice } };
      },
      listWorkflowRuns: async (args) => {
        const where = slug(args);
        listWorkflowRunsCalls.push({
          repo: where,
          workflow_id: args.workflow_id,
          branch: args.branch,
          status: args.status,
          per_page: args.per_page,
        });
        if (runsThrow.includes(String(args.workflow_id))) {
          throw new Error('simulated runs API failure');
        }
        // Throwing ONLY on the success query. Without this a test that means "the
        // success-history lookup is unreadable" can only make the whole runs API throw,
        // which kills the poll first and never reaches the lookup at all — a test that
        // passes while exercising nothing.
        if (args.status === 'success' && successRunsThrow.includes(String(args.workflow_id))) {
          throw new Error('simulated success-history API failure');
        }
        // The real endpoint filters server-side, and `status: 'success'` filters on the
        // CONCLUSION. Honouring it here lets one fixture list carry both a red latest run
        // and an older green one, so a caller asking "what is the newest run?" and one
        // asking "when did this last go green?" each see what GitHub would return.
        const byId =
          workflowRunsByRepo && workflowRunsByRepo[where]
            ? workflowRunsByRepo[where]
            : workflowRuns;
        const all = byId[String(args.workflow_id)] || [];
        const runs =
          args.status === 'success' ? all.filter((r) => r.conclusion === 'success') : all;
        return {
          data: {
            total_count: runs.length,
            workflow_runs: runs.slice(0, args.per_page || 30),
          },
        };
      },
    },
    repos: {
      // Default-branch lookup for a repo the caller does not own. Deliberately NOT
      // defaulted to the calling repo's branch: a sweep that assumes one branch name
      // across repos filters out every run in a repo that uses another, and reports it
      // as permanently green — silence dressed as health (#1296).
      get: async (args) => {
        const where = slug(args);
        reposGetCalls.push({ repo: where });
        if (reposGetThrow.includes(where)) {
          throw new Error(`simulated repos.get failure for ${where}`);
        }
        return { data: { default_branch: defaultBranches[where] || 'main' } };
      },
    },
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
      // Paged like the real endpoint when the caller asks for a page, so a script that
      // reads only page 1 provably cannot see an alert sitting deeper in the backlog.
      // Callers that pass no `page` still get the whole fixture, keeping older
      // single-page tests (732-era shape) working unchanged.
      listForRepo: async (args) => {
        listForRepoCalls.push({
          repo: slug(args),
          state: args.state,
          labels: args.labels,
          per_page: args.per_page,
          sort: args.sort,
          direction: args.direction,
          page: args.page,
        });
        if (!args.page) return { data: openIssues };
        const perPage = Math.min(args.per_page || 30, 100);
        const start = (args.page - 1) * perPage;
        return { data: openIssues.slice(start, start + perPage) };
      },
      create: async (args) => {
        const number = nextNumber++;
        created.push({
          number,
          repo: slug(args),
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
        // `body` is recorded too: the alerter re-stamps the last-recorded run id via
        // issues.update, so tests must be able to tell a body stamp from a close.
        // JSON.stringify drops undefined keys, so a pure close still serialises as
        // {issue_number, state} exactly as before.
        updates.push({
          issue_number: args.issue_number,
          state: args.state,
          body: args.body,
        });
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
    warnings,
    infos,
    listForRepoCalls,
    searchCalls,
    listJobsCalls,
    listRepoWorkflowsCalls,
    listWorkflowRunsCalls,
    reposGetCalls,
    created,
    comments,
    updates,
  }),
);
