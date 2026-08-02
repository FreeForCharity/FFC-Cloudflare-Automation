# Stale branch review — 2026-08

Disposition for the six branches on `FreeForCharity/FFC-Cloudflare-Automation` that have **no open
PR** and are not reachable from `main`. Filed against **#986**, which exists because every Conductor
run since run 64 counted these branches and none decided them.

Measured at `main` = `617f079` (2026-08-02). Branch count at review time: **14** (13 + `main`).

**Verdict: all six are safe to delete.** Five were superseded — each by a named PR or commit — and
one was never intended for `main` at all. Exactly one piece of unlanded value was found, and it is
preserved as **#1016** before its branch goes.

## Read this before deciding any future branch

#986 already warns that **ancestry is worthless here**: this repo merges through a merge queue, so
`git branch -r --merged` reports a landed branch as unmerged and `git diff main...branch` returns a
non-empty file list for content that is fully present. Both were confirmed useless again in this
review.

**The second trap cost more time and is not in #986: a symbol-name grep gives a false "absent"
reading, because a superseding implementation almost always renames.** Three of the six branches
below returned zero hits for their own function names against `main` and are nonetheless
_completely_ superseded:

| Branch                             | Grep that said "absent"                       | What `main` actually carries                                          |
| ---------------------------------- | --------------------------------------------- | --------------------------------------------------------------------- |
| `claude/nice-edison-yth9bw`        | `basePathArtifactRefs`, `basePathOutcome` → 0 | `basePathMismatch`, **8 occurrences**                                 |
| `conductor/948-claim-vs-citation`  | `prClaimNotice`, `draftHeldClaims` → 0        | `prNoticeComment`, `noticedIssues`, `draftOnlyClaim`, `decideRelease` |
| `fix/865-getgooglerows-strictmode` | `return ,@()` → 0                             | the same StrictMode/`$null`/`.Count` fix, rewritten (lines 143–159)   |

Taken at face value those greps say "unmerged work worth keeping" for all three, and the branches
would have been kept — or worse, re-proposed. **Ask what capability the branch adds and whether
`main` has that capability, then confirm against the closing comment.** The closing comment is the
cheapest reliable source here: every one of the five closed PRs names its superseder explicitly.

A third, smaller trap, hit while writing this: `grep -c` **exits 1 when the count is zero**, so
`grep -c pattern file || echo "file missing"` prints the fallback for a file that exists and simply
has no match. That conflates "absent" with "unreadable" in exactly the direction that makes a branch
look live. Terminate the pipeline (`$(grep -c … ; true)`) or check the file separately — the same
family as ledger **L42**.

## The six

### 1. `claude/applications-feed` — superseded architecturally → **delete**

- 4 commits, 2026-06-27. Adds `.github/workflows/38-publish-applications-feed.yml`,
  `applications/README.md`, `applications/applications.json`, `scripts/whmcs-applications-feed.ps1`
  (417 insertions, no deletions).
- All four paths are **absent from `main`**, and intentionally so.
- **PR #454, closed 2026-06-28** — not superseded by other code but by a change of architecture:
  FFCadmin now runs WHMCS intake **locally**, querying WHMCS directly with Key Vault credentials via
  OIDC through the APIM gateway, so this repo no longer publishes `applications.json` at all.
- Nothing to preserve: the feature's _consumer_ went away. Keeping the branch preserves a publisher
  for a feed nobody reads.

### 2. `claude/nice-edison-yth9bw` — already in `main` under another name → **delete**

- 1 commit, 2026-07-20, `89ee468`. Adds `basePathArtifactRefs` / `basePathOutcome` to
  `scripts/preflight-cutover.mjs` plus 25 lines of Pester tests.
- **PR #772, closed 2026-07-20**, superseded by **#773** (commit `972c0f0`), which implemented #766
  directly.
- `main`'s `scripts/preflight-cutover.mjs` carries **`basePathMismatch(html, repoName)`, 8
  occurrences**, wired into the 121 apex-readiness probe with self-tests. Same capability — detect
  an export built with the project basePath, which 404s on the apex — under a different name.

### 3. `claude/sites-list-add-garrison-and-sort` — data landed, ordering redirected → **delete**, value preserved as **#1016**

- 1 commit, 2026-06-14, `b1fcc0e`. Two separable halves: add `garrisonareacaregivers.org`, and group
  stable GitHub Pages sites at the top of the published list.
- **Data half is in `main`**: `garrisonareacaregivers.org` is present in `sites-list/sites_list.csv`
  (`Unknown,garrisonareacaregivers.org,Active,Yes,Yes,No,…185.199.111.153;…`). `main` carries
  **392** rows against the branch's 378.
- **Ordering half is not** (`onGitHubPages` → 0 in `main`'s `scripts/update-sites-data.mjs`), and
  **PR #425's closing comment redirects rather than rejects it**: these files are now regenerated by
  workflow 703, so the ordering belongs in the generator, never in a hand-edit of generated output.
- This is the review's only unlanded value. Captured as **#1016**, which quotes the recovery command
  and states plainly that its wanted-ness is **unconfirmed** — closing #1016 as `not planned` is a
  fine outcome.

### 4. `claude/transfer-eligibility-analysis` — never intended for `main` → **delete**

- 1 commit, 2026-06-20, `5f8a38d`, adding `.github/workflows/26-transfer-eligibility-analysis.yml`
  (95 lines).
- **No PR has ever existed**, so this commit has never been reviewed by anyone — the one branch here
  where that is true.
- It does not need review: **its own commit message says so** —
  `chore: one-off transfer eligibility analysis workflow (not for main)`. A deliberate scratch
  branch for a one-time question, left behind.
- Path absent from `main`. Deleting it discards a one-off whose author already declared it
  non-durable.

### 5. `conductor/948-claim-vs-citation` — already in `main` under other names → **delete**

- 1 commit, 2026-07-31, `3d1ab66`. Adds `prClaimNoticeMarker`, `prClaimNotice`, `hasPrClaimNotice`,
  `draftHeldClaims` across `737-claim-sync.yml`, `scripts/claim-sync-lib.js` and
  `tests/workflow-logic/test_737_claim_sync.py`.
- **PR #954, closed 2026-07-31**, superseded by **#951/#952** (`19d581f`, `81c9bec`). The closing
  comment records that another agent implemented #948 concurrently, theirs merged first, and an
  AGENTS.md supersession check judged theirs the better implementation.
- `main`'s `scripts/claim-sync-lib.js` carries `prNoticeComment`, `noticedIssues`, `draftOnlyClaim`
  and `decideRelease` — the same two capabilities (tell a PR author what their reference claimed;
  report draft-held claims).
- Note for the record: **#986 guessed this branch was "very likely already-applied" via #939, and
  the conclusion is right for the wrong reason.** The superseder is #951/#952, not #939.

### 6. `fix/865-getgooglerows-strictmode` — already in `main`, rewritten → **delete**

- 1 commit, 2026-07-26, `36f9f38`. Fixes two StrictMode hazards in `Get-GoogleRows` so a GA4
  property with genuinely zero sessions reports zero instead of failing with
  `The property 'Count' cannot be found on this object`.
- **PR #885, closed 2026-07-29**, superseded by **#898** (`d4b0fe4`, "an empty GA4 result is zero
  traffic, not a failed probe"), which landed after this branch was cut. The closing run recorded
  the branch as `CONFLICTING/DIRTY` against that fix.
- `main`'s `scripts/google-api-common.ps1` carries the same analysis at lines 143–159 — the
  empty-array unroll yielding `$null`, `$rows.Count` then throwing, and `502`'s
  `if ($respRows.Count)` as the live caller that failed. Same defect, same reasoning, different
  code.

## Out of scope — do not touch

Six branches each have an open PR against them, and are **not** part of this review. **This mapping
was read from `gh pr list --json headRefName`, not inferred from branch names** — an earlier draft
of this document guessed it from the branch naming pattern and got two of six wrong in a way that
would have proposed a live branch for deletion:

| Branch                                    | Open PR |
| ----------------------------------------- | ------- |
| `conductor/run73-hooks-exit-and-encoding` | #1007   |
| `claude/sweet-hawking-u3rcqm`             | #1005   |
| `claude/sweet-hawking-5ejszc`             | #1015   |
| `conductor/lessons-r60`                   | #961    |
| `conductor/lessons-r54`                   | #940    |
| `feat/229-client-field-populate`          | #825    |

The `conductor/lessons-*` names look like abandoned Conductor scratch branches and are not — they
carry Clarke's two open hook PRs. The `claude/sweet-hawking-*` names look interchangeable and are
not: two back open PRs, one does not.

## A seventh orphan, found by this review — `claude/sweet-hawking-i8uawy`

Not one of #986's six; surfaced only because the branch/PR mapping above was re-derived from the
API. Recorded here rather than folded in silently, and **not** included in the deletion block below.

- Remaining commit `db3fa8d` (2026-08-02): "a failed git command must not read as 'nothing staged'",
  touching `.githooks/scan_staged.py` and its test.
- Two PRs: **#1000 MERGED** (04:34) and **#1002 CLOSED** (04:55).
- **Superseded**: `main` carries **`GitFailed` twice** in `.githooks/scan_staged.py`, landed by
  `d8e1535` — "adopt #1002's named `GitFailed` and its fixture-setup hardening" (#1004), refined by
  `ac599d2`. The branch's own copy has no `GitFailed` at all; #1004 adopted the _idea_ under a
  cleaner implementation. Same pattern as branches 2, 5 and 6 above.
- **Verdict: safe to delete**, on the same evidence standard as the six. Left out of the block below
  only because it is outside #986's stated scope — add it deliberately, or in a follow-up.

`conductor/lessons-r54` and `conductor/lessons-r60` are **not** orphans, per the table above.

## Deletion commands — NOT run by this PR

Per #986, a branch delete is not revertible through the PR flow, so these are listed for a human to
run after review. Nothing in this PR executes them.

```bash
gh api -X DELETE repos/FreeForCharity/FFC-Cloudflare-Automation/git/refs/heads/claude/applications-feed
gh api -X DELETE repos/FreeForCharity/FFC-Cloudflare-Automation/git/refs/heads/claude/nice-edison-yth9bw
gh api -X DELETE repos/FreeForCharity/FFC-Cloudflare-Automation/git/refs/heads/claude/sites-list-add-garrison-and-sort
gh api -X DELETE repos/FreeForCharity/FFC-Cloudflare-Automation/git/refs/heads/claude/transfer-eligibility-analysis
gh api -X DELETE repos/FreeForCharity/FFC-Cloudflare-Automation/git/refs/heads/conductor/948-claim-vs-citation
gh api -X DELETE repos/FreeForCharity/FFC-Cloudflare-Automation/git/refs/heads/fix/865-getgooglerows-strictmode
```

Expected result: branch count **14 → 8**.

**Before running them**, confirm #1016 is either open or deliberately closed — it is the only place
the `claude/sites-list-add-garrison-and-sort` ordering code is described, and `b1fcc0e` becomes
harder to reach once the ref is gone.
