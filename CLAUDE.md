# Repo notes for Claude

> Agent-generic onboarding (catalog, numbering, safety model, add-a-workflow checklist) lives in
> **AGENTS.md** — read that first. This file covers Claude-specific environment notes.
>
> **Cross-repo context lives in `docs/ffc-repo-map.md`.** Read it before writing anything about FFC
> _process_, _standards_, or _how a charity works on their site_ — those are authoritative in
> `FreeForCharity/FFC-IN-ffcadmin.org` and in each `FFC-EX-<domain>` repo, not here. It is public,
> so a read-only clone needs no attachment:
> `GIT_LFS_SKIP_SMUDGE=1 git clone --depth 1 https://github.com/FreeForCharity/ffc-in-ffcadmin.org`

## Merging: queue etiquette (validated 2026-07-01, PRs #534–#538)

- `main` requires **Validate Repository** + **Phantom Revert Guard** (strict) and merges via the
  **merge queue**, which builds a merge group and re-runs those checks (722/723 have `merge_group:`
  triggers; 727 skips on merge groups = passing).
- **Resolve review threads before queueing.** Copilot auto-reviews every PR; fix the real findings
  first, then
  `gh api graphql -f query='mutation{resolveReviewThread(input:{threadId:"<id>"}){thread{isResolved}}}'`.
  List threads: query `pullRequest(number:N){reviewThreads(first:20){nodes{id isResolved …}}}`.
- **`gh pr merge --auto` can mask the real blocker** behind a GraphQL "rate limit" error. Use the
  direct mutation to see the truth (unresolved conversation / CodeQL pending):
  `gh api graphql -f query='mutation{enqueuePullRequest(input:{pullRequestId:"<node_id>"}){mergeQueueEntry{position state}}}'`
- GraphQL and REST have **separate rate pools** (5,000/hr each, shared account-wide). When GraphQL
  is exhausted, reads still work via REST; check with `gh api rate_limit`.
- Never `--admin`-merge; never push to `main` directly.
- **After `gh pr ready`, the first `enqueuePullRequest` usually fails — retry, don't diagnose.**
  Promoting a draft re-registers the branch checks, so the mutation returns
  `Required status check "Phantom Revert Guard" is expected` even when that exact SHA already
  carries a green run of it. It is a re-registration race, not a missing check. Poll the head SHA's
  `check-runs` via REST and retry; on 2026-07-30 (#938) the retry ~60s later enqueued at position 1.
- **`AWAITING_CHECKS` on a queue entry is not a stall.** `mergeQueueEntry.state` sat at
  `AWAITING_CHECKS` with a fixed `estimatedTimeToMerge` for several minutes _after_ every
  `merge_group` run had reported success (722 success, 723 success, 727 skipped — a skip is a pass
  here). The merge landed on its own. Do not dequeue, re-push, or "fix" the branch on the strength
  of that state; poll `pulls/N --jq .merged` via REST and let it finish.
- **Format with the CI-pinned prettier.** `722-ci.yml` checks with `npx --yes prettier@3.8.1`; plain
  `npx prettier` fetches the latest version, whose Markdown reflow differs — producing
  local-pass/CI-fail loops. Always run `npx --yes prettier@3.8.1 --write <files>`.
  - **Pass `--ignore-unknown`, and copy CI's whole invocation rather than just its version.** CI
    runs `npx --yes prettier@3.8.1 --check . --ignore-unknown`. Without that flag, prettier **aborts
    the entire invocation** on the first file it has no parser for — so
    `--check <file>.yml <file>.py <file>.md` dies on the Python file with
    `No parser could be inferred` and never examines the Markdown. On 2026-08-11 (run 149) that
    checked a one-character ledger edit, reported clean, and #1184 then failed CI at
    `Check formatting (Prettier)` naming the very file that had never been read. The exit code is
    non-zero, which is what makes it easy to misread: it looks like "the check ran and flagged the
    `.py`", not like "nothing you cared about was checked". A batched verification couples its
    inputs — one unparseable file voids the rest. Ledger **L240**.
  - **…and anchor the check at the repo root, because `.` follows the Bash tool's cwd, which
    persists between calls.** Same failure as L240, reached from the other side: there a missing
    flag aborted the run before the Markdown, here a drifted cwd left it out of the file set
    entirely. Verifying a ledger edit on #1242, the pre-commit `prettier --check . --ignore-unknown`
    printed `All matched files use Prettier code style!` and exited 0; CI then failed
    `Check formatting (Prettier)` naming `docs/lessons-ledger.md`. The cwd had moved to
    `tests/workflow-logic` **two Bash calls earlier**, in a command that ran a test module — the
    `cd` persists, the edit did not care, and `.` resolved to a directory whose only Markdown was an
    already-clean README. Measured: run from that subdirectory the invocation emits a
    **byte-identical** success line to a full-repo pass, so nothing in the output distinguishes "400
    files clean" from "you checked the wrong subtree". **Fix the cwd, not the argument** — `cd` to
    the root and run CI's own command:

    ```bash
    ( cd "$(git rev-parse --show-toplevel)" && npx --yes prettier@3.8.1 --check . --ignore-unknown )
    ```

    **Do NOT pass the root as the PATH argument instead** — the obvious one-liner
    (`prettier --check "$(git rev-parse --show-toplevel)"`) is wrong, and this note shipped it wrong
    for one commit before it was measured. `.prettierignore` patterns are resolved against the
    **cwd**, not against the target path, so from a subdirectory the ignore file stops applying:
    measured from `tests/workflow-logic`, that form reports **42 files** needing formatting — 31
    under `whmcs/`, 7 under `assets/`, the rest scattered — every one of them inside a path
    `.prettierignore` lists and CI considers clean. The `cd` form on the same tree exits 0. So the
    two spellings fail in opposite directions from the same wrong cwd: `.` under-reports silently (a
    clean-looking pass over the wrong subtree), an absolute path over-reports loudly (42 phantom
    failures). The second is the safer error and still costs a session chasing them.

    Not a ledger row: `docs/lessons-ledger.md` had 1,051 bytes of headroom under the 1 MiB
    large-blob guard when this was found, and the row that would have recorded it is what pushed the
    file 2,115 bytes over. Filed as its own issue instead.

## Verifying tests: CI is authoritative, local runs may be false-red

> **This section's premise expired on 2026-07-31 and the section outlived it.** The
> `harness crashed:` epidemic below was **#943**, and #943 is **closed**. Measured on the
> Conductor's Windows host on 2026-08-02 (run 77), a full `python tests/workflow-logic/run_all.py`:
> **`harness crashed` count = 0**, 895 `PASS`, 83 `FAIL`, 16 modules named with specific diagnoses.
> Read what follows as history, and read the corrected rule first.

- **A local red now carries signal, and discarding it costs real defects.** The 16 modules failing
  here fail for stated reasons — a `FileNotFoundError` on a temp path, a preflight refusing an
  unparseable response — not a node abort. Run 77 followed one of them (`test_729_add_collaborator`)
  to a live defect that CI cannot see at all: the module runs the step under test with
  `cwd=REPO_ROOT`, loses all five of its tests to the first one, and leaves a stray file in the
  working tree. Tracked on **#1023**.
- **CI is still authoritative for green, and is still the only cross-platform judge.** Do not "fix"
  a test or hold a PR on a local red without saying _which_ module and _what_ the failure text was —
  the standard is now the same one applied to any other measurement, not a blanket dismissal.
- **Historical (pre-#943, kept because the shape recurs):** the harness used to report most modules
  failing with `harness crashed:` and `Assertion failed: ncrypto::CSPRNG(nullptr, 0)`, because each
  module built a fresh minimal `env` dict for `subprocess.run` and Windows node cannot start without
  the inherited environment. Verified 2026-07-24 (PR #828): a tree failing ~17 modules locally
  passed `Validate Repository` in CI. If a _new_ wave of `harness crashed:` appears, that is the
  shape to suspect — and it is a regression to file, not a fact to route around.
- **But "the harness is broken here" is not a reason to review a guard by reading it.** When the
  harness cannot run, **test the module it wraps.** Only the harness-spawned node dies; `node`
  itself is fine, so a pure module under `scripts/` can be `require`d directly and exercised on the
  spot. Validated 2026-07-31 on PR #941: its test module reported **14 `harness crashed:` FAILs**
  locally while CI was green, so instead of trusting the PR's own reported output — which the #935
  rule forbids — the Conductor wrote a standalone 9-case node probe against
  `scripts/claim-sync-lib.js`, then mutated the source (`(${NWO})?#` → `()#`, and the repo
  comparison → `if (false)`) and re-ran it. Both mutations flipped exactly the expected cases and
  left the rest green, which is the whole point: the checks pass by **discrimination, not
  permissiveness**. Full mutation review was recovered on a host where the official harness is
  unusable.
  - Two mechanics that make this work: `require()` resolves relative paths against the **probe
    file's** directory, not the cwd — pass an absolute `C:/…` path; and assert the mutation's anchor
    text is present **before** substituting, so a refactor that moved the guard fails loudly instead
    of silently testing nothing.

### The harness crash is the scrubbed-`env` bug, and it is fixable (measured 2026-07-31)

The two facts above and the `subprocess.run` rule further down are **the same bug**, and reading
them as separate is what made "local red is no signal" feel permanent. On `main` (`d956a78d`),
`run_all.py` fails **26 modules**, all with a node abort in
`node::InitializeOncePerProcessInternal`. The cause is that each module builds a **fresh, minimal**
env dict for `subprocess.run`, e.g. `tests/workflow-logic/test_724_initialize_labels.py:61`:

```python
env = {"PATH": f"{pathlib.Path(NODE).parent}:/usr/bin:/bin:/usr/local/bin"}
```

On Linux that suffices; on Windows node cannot start without inherited variables (`SYSTEMROOT` above
all) — exactly what the "never pass a scrubbed `env=`" rule below already says about `bash`.

**The POSIX-looking `/usr/bin:/bin` is a red herring — falsify it before acting on it:**

| change to `test_724` / `test_228` | crashes              | pass        |
| --------------------------------- | -------------------- | ----------- |
| unmodified                        | 8 / 8                | 2 / 5       |
| `PATH` → `os.environ["PATH"]`     | **8 / 8, unchanged** | 2 / 5       |
| `env = dict(os.environ)`          | **0 / 0**            | **10 / 13** |

Rewriting `PATH` changes nothing; inheriting the whole environment fixes it outright. So the
standing advice is narrower than it looks: **local red is no signal only until the env dict is
fixed** — it is not an unfixable property of this host, and it is not entropy. Tracked as **#943**,
**which landed and closed on 2026-07-31**: run 77 measured `harness crashed` **0 times** in a full
local suite. This prediction came true, and the section above has been corrected to match. The probe
technique above was the workaround while it was open; it is now a technique for a module whose
harness is broken for its own reasons, not the standing answer to this host.

## Prefer the machine's claim to your own: a hand-written `claimed` label expires in 48h (validated 2026-07-31)

737 treats a claim bearing `<!-- claim-sync:linked-pr -->` (the sweep's own comment) as strictly
stronger than a hand-written `CLAIM:` comment. From the release path
(`.github/workflows/737-claim-sync.yml`):

```js
if (!searchOk && claimedByLinkedPR) continue;   // a MARKER holds through a search failure
```

A marker-bearing claim survives an unreadable org search; an unmarked one is deliberately given
**exactly 48h** and then released, so two agents cannot collide on a stale hand-label. Confirmed by
driving the sweep with a frozen clock and a 100h-idle issue: PR open + healthy search → held; no PR
anywhere → released (the backstop, correct); **PR open + search over the result cap → released, a
live claim lost.**

Consequence for the Conductor: hand-labelling `claimed` buys ~48 hours, not a claim, and the cost is
a standing "re-check these every run" thread — runs 54 and 55 each carried one. The durable move is
to **remove the hand label and dispatch 737** once the sweep can actually see the PR; it re-applies
the claim with a marker in seconds, and it finds referencing PRs a human enumerating by hand will
miss (on 2026-07-31 it added canary #21/#22, FOT #123 and hub #940 to three issues that had been
hand-labelled with one PR each).

## A test asserting a non-zero exit code must also assert on the output (validated 2026-07-29)

**Never pass a scrubbed `env=` to `subprocess.run`.** Writing
`subprocess.run([...], env={"PATH": os.environ["PATH"]})` to isolate a test broke `bash` on this box
— a bare `bash` here resolves to the WSL shim, which needs `SYSTEMROOT` and friends:

```
Catastrophic failure
Error code: Bash/Service/E_UNEXPECTED
```

That surfaced as **exit 1**. The system under test also returns exit 1, for "a violation was found".
Six tests in `test_722_large_blob_guard.py` failed honestly, but the seventh asserted only
`returncode == 1` and went **green for the wrong reason** — the harness was broken and the test read
it as a successful detection.

The rule generalizes past this box: **a test that asserts a failure exit code must also assert
something about stdout/stderr**, or it cannot distinguish the system under test from its harness.
Prefer `env = dict(os.environ)` plus the one or two overrides you actually need.

Related, same file: on `win32`, prefer Git-for-Windows bash explicitly —

```python
r"C:\Program Files\Git\bin\bash.exe"   # handles C:\... arguments
```

A bare `bash` strips drive letters out of Windows-style path arguments and exits 127 with
`No such file or directory` naming a path with every separator removed. No effect on
`ubuntu-latest`, so this is a local-run fact only.

**Which `bash` that is depends on PATH, and this line used to name only one of them — do not read
the attribution as a way to rule the rule out.** It said "the WSL shim", and from a git-bash-spawned
Python on 2026-08-09 (run 132) `shutil.which("bash")` is `C:\Program Files\Git\usr\bin\bash.EXE` —
the **MSYS** binary, not WSL. The symptom is byte-identical, because both fail the same way on the
same argument: MSYS eats the backslashes as escapes, so `C:\Users\…\step.sh` arrives as
`C:UsersclarkAppDataLocalTemptmpXXXXstep.sh`. So the remedy is right and its stated cause was only
ever one instance of it. Reproduced on #1139, where a new module ran the step from a **file**
(`["bash", str(script)]`) rather than the repo-wide `bash -c <text>` — which is why that module was
the only one of 61 affected, and why the fix is per-module rather than global.

The reason this matters beyond a red test: **`assert rc != 0` is satisfied by that 127**, so a
harness that cannot start is indistinguishable from a step correctly rejecting a payload. That is
the same rule as the section below, hit from the other direction — and on #1139 it made a security
test pass for the wrong reason on this host while CI was green.

## Reading `gh --format json` on the Windows Conductor box (validated 2026-07-25)

**Open the file as UTF-8 explicitly, or Python decodes it as cp1252 and dies.** Piping
`gh project item-list … --format json` (or any `gh` JSON output) to a file and reading it back with
plain `open(path)` fails on this box:

```
UnicodeDecodeError: 'charmap' codec can't decode byte 0x9d in position 20770
```

Nothing is wrong with the data — Python's default encoding on Windows is cp1252, and FFC issue
titles, board item titles and PR bodies routinely contain em-dashes, arrows and smart quotes.
Always:

```python
with open(path, encoding="utf-8") as fh:
    data = json.load(fh)
```

Same for `pathlib.Path(p).read_text(encoding="utf-8")` and any `write_text`. This costs one failed
call every time it is rediscovered, which has now happened more than once.

**It bites on the way out too — set `PYTHONIOENCODING=utf-8`.** Reading UTF-8 correctly and then
`print`ing what you read fails at the terminal instead:

```
UnicodeEncodeError: 'charmap' codec can't encode character '→' in position 78
```

On 2026-07-30 both halves fired inside one command while inspecting `agentic-os-status.json`,
because a live workflow really is named _Redirect Rule: trendylittlegeek.com → aprilhansen.com_. The
decode error names a byte offset and the encode error names a codepoint — worth knowing apart, since
the first means "reopen the file" and the second means "the file was fine, your stdout is cp1252".
This is not confined to `gh` output: any FFC JSON can carry an arrow, so treat **both** env-var and
`encoding=` as the default for feed work.

## Python on this host cannot open a git-bash `/c/...` path (validated 2026-07-30)

**Every shell builtin accepts `/c/Users/...`; Python does not.** The interpreter is native Windows,
so it never sees the MSYS mount table:

```
>>> io.open('/c/Users/clark/.../feed.json', encoding='utf-8')
FileNotFoundError: [Errno 2] No such file or directory: '/c/Users/clark/.../feed.json'
```

The path is real — `cat` on the very same string works. Write `C:/Users/...` in Python string
literals (forward slashes are fine; it is the `/c/` _prefix_ that fails). Same family as the
`MSYS_NO_PATHCONV` fact below: a path that is correct for the shell is not automatically correct for
the process the shell hands it to. Bites hardest when a heredoc script is copying files the
surrounding `cd`/`ls` already proved exist.

## A symptom that disappears on its own has not verified your fix (validated 2026-07-30)

**Check the cause was still present before claiming the fix suppressed it.** #924 filters GitHub's
platform agents out of the public gate panel. The first feed generated after it merged showed no
`copilot` row — which looks like proof and is not: that waiting run had resolved by itself minutes
earlier, so the filter had nothing to act on and was never exercised.

The mistake is cheap to avoid and expensive to make, because it retires a real verification task
while feeling like it completed one. Before reading an absence as evidence, re-read the _input_:

```
gh api "repos/OWNER/REPO/actions/runs?status=waiting&per_page=20" --jq '.workflow_runs[].name'
```

If the thing you filter is not in there, the run proves nothing about the filter.
Deliberately-injected input — a mutation test — is what actually verifies it, which is why #924
shipped with one.

## `git cat-file` / `git show` with a `.github/…` path needs `MSYS_NO_PATHCONV=1` (validated 2026-07-25)

**git-bash rewrites a `rev:path` argument when the path starts with a dot.** Reading a file out of a
branch without checking it out:

```
$ git cat-file -p "origin/claude/some-branch:.github/workflows/742-x.yml"
fatal: Not a valid object name origin\claude\some-branch;.github\workflows\742-x.yml
```

MSYS path conversion turned the `:` into `;` and every `/` into `\`. The tell is the mangled path in
the error — the ref is fine. It is **the leading dot in the path** that trips the heuristic, not the
slashes in the branch name: `origin/main:docs/foo.md` works on the same command line and
`origin/branch:.github/…` does not, so this looks like it works right up until you touch a workflow
file. Prefix the command:

```bash
MSYS_NO_PATHCONV=1 git cat-file -p "origin/<branch>:.github/workflows/<file>.yml"
```

## `gh api` list endpoints silently truncate at `per_page` — paginate before concluding "absent"

`per_page=100` returns 100 items and **no warning** that more exist. On 2026-07-25 this produced a
false alarm mid-run: a just-merged workflow appeared to be unregistered because the repo had
`total_count: 105` workflows and the 105th was on page 2. The near-miss is the shape to remember —
the conclusion "the alerter did not register" was about to be reported as a defect.

Two habits, both cheap:

- Add `--paginate` to any `gh api` list call whose result feeds a **negative** conclusion ("X is
  missing", "nothing is pending", "zero failures").
- When an endpoint returns `total_count`, compare it against the length of what you actually got
  before drawing a conclusion from the absence of something.

This is the same class as the `#719` log tail needing `--paginate` to find the true run number.

### …but `--paginate` concatenates pages, so the result is often not valid JSON

`--paginate` runs the jq expression **once per page** and concatenates the results. Which shape you
ask for decides whether that is correct:

- **Streaming** — `--jq '.[] | "\(.created_at) \(.body)"'` — emits one line per item, so the pages
  concatenate cleanly. This is the form AGENTS.md teaches for reading the #719 tail, and it is
  right.
- **Array-building** — `--jq '[.[] | {body, created_at}]'` — emits one **array per page**:
  `[…][…][…]`. That is not valid JSON. `gh` exits 0 and says nothing; the failure surfaces later in
  whatever parses the file, as an error pointing at a byte offset in the middle of page 2, nowhere
  near the command that caused it.

- **No `--jq` at all, on an endpoint that returns an object** —
  `--paginate "…/actions/workflows?per_page=100"` emits `{…}{…}` and is invalid for the same reason.
  This is the easiest one to miss, because there is no jq expression to inspect and the call looks
  like the textbook use of the flag. `json.loads` reports
  `JSONDecodeError: Extra data: line 1 column 67588`, a byte offset in the middle of page 1's
  closing brace, which reads as a corrupt response rather than as two concatenated ones.

The fix is not the obvious one: `--slurp` is the right flag but **cannot be combined with `--jq`**
(`gh` rejects it outright). Fetch raw, then flatten:

```bash
gh api --paginate --slurp repos/OWNER/REPO/issues/719/comments > pages.json
# pages.json is an array OF PAGES — flatten it before use
```

Hit on 2026-08-01 (run 70) doing a whole-thread read of #719 for the #988 review. A mechanical guard
is filed as #989; until it lands this is prose, because `.claude/hooks/` changes are reviewed by
Clarke by standing rule.

## On a merge-queue repo, a `null` `autoMergeRequest` does not mean the enqueue failed — `mergeQueueEntry` is the proof

`main` here is governed by a merge queue, and that changes which field records an enqueue.
`gh pr merge --auto` succeeds, prints only the advisory

```
! The merge strategy for main is set by the merge queue
```

and then **`autoMergeRequest` ends up `null`** — the PR is in the queue, not in auto-merge. Reading
that `null` as "the enqueue failed" is wrong, and on 2026-07-29 it nearly caused a duplicate merge
attempt; the second `gh pr merge` answered `Pull request … is already queued to merge`, which is
what revealed the mistake.

**The field is not constant, so reading it once tells you nothing.** Observed on 2026-08-02 (run
71): immediately after `gh pr merge --auto` on #991, while its checks were still running,
`autoMergeRequest` read **non-null** (`enabledAt`, `mergeMethod: MERGE`); once the checks went green
and the queue took the PR, the same field read `null`. So a non-null value means "enabled, not yet
queued" and `null` means either "never enabled" **or** "already queued" — the two states you most
need to tell apart map to the same reading. The near-miss repeated here: `null` on #990 looked like
a failed enable, and the disambiguator was the mutation, which answered
`Pull request is already in the queue`. Never re-push to a branch on the strength of a `null`.

Confirm with `mergeQueueEntry` instead:

```bash
gh api graphql -f query='{repository(owner:"FreeForCharity",name:"FFC-Cloudflare-Automation"){
  pullRequest(number:905){ mergeQueueEntry{ position state enqueuedAt } }}}'
# → {"position":1,"state":"AWAITING_CHECKS","enqueuedAt":"2026-07-29T13:10:06Z"}
```

Note the advisory goes to **stderr and the command still exits 0**, so a `>/dev/null` wrapper hides
the one hint that the queue — not auto-merge — took the request.

This is the third instance of the same underlying rule already in this file: **confirm a GitHub
write by re-reading the state it should have changed, and make sure you re-read the _right_ field.**
The gate-approval note above says don't trust the POST body; this says don't trust the field that
would have been correct on a non-queue repo. Both fail the same way — a truthful-looking negative.

**The `null` is not even stable, so a second reading is not a second opinion.** On 2026-07-30, #904
read `autoMergeRequest` non-null with `mergeStateStatus=BLOCKED` immediately after `--auto`, then
`null` with `CLEAN` a few minutes later — two different-looking states for one unchanged fact. Only
the mutation settled it:

```bash
gh api graphql -f query="mutation{enqueuePullRequest(input:{pullRequestId:\"$NID\"}){mergeQueueEntry{position state}}}"
# → errors[0].message: "Pull request is already in the queue"
```

`enqueuePullRequest` is safe to use as a _probe_ precisely because it is idempotent — an
already-queued PR is rejected rather than double-enqueued, so the error message is the answer.

**`--auto` can also print nothing at all, and REST `.auto_merge` can read `false`, while the enqueue
took.** On 2026-07-30 this happened on three PRs in one run (#923, #887, ffcadmin #744): no
advisory, no error, exit 0, `.auto_merge == false` on the very next call — and `enqueuePullRequest`
answered `"already in the queue"` for two of them and `"Pull request is closed"` for the third,
which had already merged. So the absence of the stderr advisory is **not** the signal that the queue
declined the request. Empty output is not evidence; only the probe is.

### Promoting a draft can hard-block the enqueue — a different case from the one AGENTS.md covers

AGENTS.md says a branch-level check failure does not dequeue an **already-queued** PR, so leave the
branch alone. The inverse case has the opposite remedy. #887 sat `clean` and all-green **as a
draft**; `gh pr ready` re-ran branch CI and Phantom Revert Guard failed:

```
Phantom-revert risk: branch has untouched updates in critical paths and is 11 commits
behind main (threshold: 5). Update the branch (merge main in or rebase) before merging.
```

Because the PR was not yet queued, this was a real block — `enqueuePullRequest` returned
`"has failing required statuses"`, not `"already in the queue"`. Fix it with

```bash
gh api -X PUT repos/FreeForCharity/FFC-Cloudflare-Automation/pulls/<n>/update-branch
```

and note the ordering that makes this safe: **the enqueue rejection is what proved the PR was out of
the queue.** Probing first, then updating, satisfies AGENTS.md's "only merge `main` into a branch
that is genuinely out of the queue" — doing it in the other order risks a 422 against a queued
branch. Also expect a **draft that has been green for days to fail on promotion for staleness
alone**: the guard's 5-commit threshold is measured at run time, so age accrues silently while the
PR waits.

**But do not pre-emptively update the branch on every promotion — being behind is the trigger, not
promoting.** After three consecutive cases it was tempting to read this as "promotion breaks the
guard". It does not. On 2026-07-30 #931 sat ~40 minutes as a draft, was **0 behind / 2 ahead**, and
`gh pr ready` re-ran branch CI with the guard **passing**. Promotion only re-runs the check; the
5-commit threshold decides the outcome. Check first and touch the branch only if the number says to
— it is a worker's branch, and an unnecessary merge commit on it is not free:

```bash
gh api repos/FreeForCharity/FFC-Cloudflare-Automation/compare/main...<branch> --jq '{ahead:.ahead_by,behind:.behind_by}'
```

## Code scanning can block the merge queue even when every required check is green

The ruleset is not the whole story. On 2026-07-30 #931 had both required contexts green — ruleset
`16768928` requires exactly `Validate Repository` and `Phantom Revert Guard` — and the CodeQL
workflow itself was `success` with `Perform CodeQL Analysis=success`. The enqueue still refused:

```
UNPROCESSABLE: Code scanning is waiting for results from CodeQL for the commits 3b73753 or 387c28d.
```

That is **code scanning merge protection**, a separate gate from required status checks, so reading
`rulesets/<id>` `required_status_checks` will never show it and its absence there proves nothing.

The cause is upload targeting, not analysis failure. Merge protection wants an analysis for the
current **test-merge** commit, and a second push to the branch moves that sha:

```bash
gh api "repos/FreeForCharity/FFC-Cloudflare-Automation/code-scanning/analyses?ref=refs/pull/931/merge" \
  --jq '.[]|"\(.commit_sha[0:7]) \(.created_at)"'
# → 4e81727 2026-07-30T12:24:10Z     ... the PREVIOUS head's merge sha; neither sha in the error
```

Re-running the CodeQL workflow uploads for the current merge commit, and the enqueue then succeeds:

```bash
gh run rerun <codeql-run-id> --repo FreeForCharity/FFC-Cloudflare-Automation
# ~3 min later the analyses list carries 3b73753, and enqueuePullRequest returns
# "already in the queue" — the earlier silent --auto had been queued behind this all along
```

Diagnostic order: the enqueue-probe error **names the two shas it will accept** — start there, list
the analyses on `refs/pull/N/merge`, compare, then re-run CodeQL. Do not push an empty commit to
force it; the analysis, not the branch, is what is missing.

## Narrowing a workflow to a read lane surfaces the writes that were riding on the old credential

**Before moving a workflow to a `*-prod-read` environment, enumerate what it writes.** Validated the
hard way on 2026-07-29 (#834): `726` was moved off the gated `github-prod` onto `github-prod-read`,
and its Key Vault step exports the **read-scoped** PAT to `GITHUB_ENV` as `GH_TOKEN` — which then
applies to every later step, including the one that **updates the rolling tracking issue**. The
org-wide audit passed and the run died on the last step:

```
Updating issue #667
failed to update .../issues/667: GraphQL: Resource not accessible by
personal access token (updateIssue)
```

Searching an issue and editing one are different permissions, so the failure only appears at the
write. That write had worked for months purely because the gated lane happened to hand it a
**writer** PAT — the narrowing did not break it so much as reveal it.

The fix is the pattern `737` already uses: an own-repo issue write belongs on the **ambient**
`GITHUB_TOKEN`, not on a Key Vault credential. A step-level `env:` beats `GITHUB_ENV`, so the read
step keeps the PAT and only the write falls back:

```yaml
- name: Open / update tracking issue
  env:
    GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
  run: |
    gh issue edit "$existing" --repo "$repo" --body "$body"
```

The job must declare `issues: write` (726 already did). Corollary worth keeping: **a workflow's own
error message states what its author assumed, not what the repo can do.** 726's preflight asserted
"These OIDC identifiers are ENVIRONMENT secrets — a repo Variable does not satisfy a `secrets.*`
reference." True about `secrets.*`, and a false diagnosis: the identifiers were already repo
Variables and the correct fix was to reference them as `vars.*`. That message was escalated to a
human as a provisioning request before anyone checked `gh api repos/<r>/actions/variables`.

## The Conductor cannot `--request-changes` on a cloud-worker PR

Every cloud-worker PR in this org is authored by `clarkemoyer`, and the Conductor is authenticated
as the same account, so GitHub refuses the review:

```
failed to create review: GraphQL: Review Can not request changes on your own
pull request (addPullRequestReview)
```

This is not a scope problem and adding permissions will not fix it. Post blocking feedback with
`gh pr comment <n> --body-file <f>` instead, and simply do not promote the PR out of draft until the
finding is addressed — leaving it draft is what actually holds the merge, not the review state.

## Windows host facts (validated 2026-07-31, conductor run 57)

The scheduled Conductor runs on Windows 11 + git-bash. These cost real time to rediscover.

- **Native Python cannot open a git-bash path.** `open('/c/Users/…')` raises `FileNotFoundError`
  while `ls /c/Users/…` works — bash resolves the redirect, native Python does not. The tell is a
  command that writes a file successfully and a reader that insists it is missing. Use `C:/Users/…`
  for anything a Windows binary will open, and keep `/c/…` for the shell's own redirects.
- **`gh pr merge --delete-branch` is rejected where a merge queue is enabled**:
  `Cannot use -d or --delete-branch when merge queue enabled`. Same family as the existing
  no-strategy-flag rule — enqueue with `gh pr merge --auto` or the `enqueuePullRequest` mutation and
  let the queue delete the branch.
- **`scripts/generate-agentic-os-status.py` needs `PYTHONUTF8=1` on Windows** until #945 lands. It
  writes issue titles to stdout and dies with
  `UnicodeEncodeError: 'charmap' codec can't encode character '\U0001f6a8'` on the 🚨 in #921's
  title. It also needs `GH_TOKEN` exported (`export GH_TOKEN=$(gh auth token)`) — it does not read
  `gh`'s keyring.
- **The generator's `--output` used to write CRLF on Windows — fixed at the source (run 74).**
  `scripts/generate-agentic-os-status.py` now writes through `write_feed()`, which pins
  `newline="\n"` beside the `encoding="utf-8"` that was already there, so both hosts emit identical
  bytes. Two things had been masking it and neither was a fix: ffcadmin's `.gitattributes`
  (`* text=auto eol=lf`) normalized the _committed_ blob, and this repo's CI is Linux, where text
  mode never translates — so no test could observe it by running the script.
  `test_502_agentic_os_status.py` therefore asserts on the **`open()` call's keywords**, not on the
  bytes written; a round-trip check would pass on `ubuntu-latest` with or without the fix.
  - Verifying the staged blob (`git cat-file -p :<path> | tr -cd '\r' | wc -c` → `0`) is still the
    right habit for any hand-delivered feed — it just no longer has anything to catch here.
  - **But `FFC-IN-ffcadmin.org` rewrites staged JSON in a pre-commit hook, so verify `HEAD:<path>` —
    and only ever _after_ the commit.** That repo runs `lint-staged` → `prettier --write` over
    `*.{json,md,css}`, which fires **after** the staged-blob check and re-stages whatever it
    changes. So the staged check is measuring a blob the commit may not keep, and the two copies
    plus the generated source can all still agree while the delivered bytes differ from every one of
    them.

    **`HEAD:` has its own trap, and it fails in the reassuring direction: run it before committing
    and it reads the _previous_ commit's blob and reports a confident pass.** So this is a
    post-commit step, not a substitute for the staged one. Hash **every delivered copy** against the
    generated file — CR-counting is not enough on its own, because a `prettier` reformat that
    preserves LF changes the bytes without adding a single `\r`:

    ```bash
    GEN=/path/to/generated/agentic-os-status.json
    git rev-parse --verify HEAD >/dev/null          # you are AFTER the commit, not before it
    sha256sum "$GEN"
    git cat-file -p HEAD:public/data/agentic-os-status.json | sha256sum
    # both must print the same digest; then, separately, the CR check:
    git cat-file -p HEAD:public/data/agentic-os-status.json | tr -cd '\r' | wc -c   # → 0
    ```

    Equal digests subsume the CR check (identical bytes cannot differ in `\r`); it is kept because
    it names the specific failure this repo has actually had, and because it still applies to a
    delivery where a copy legitimately differs from the source.

    **There is one delivered copy as of 2026-08-11.** This recipe hashed two until
    FFC-IN-ffcadmin.org#904 deleted the dead `src/data/agentic-os-status.json` — the page fetches
    `assetPath('/data/agentic-os-status.json')` and nothing imported the other. That PR grepped its
    own repository and so missed both this recipe and `scripts/public-feed-freshness-lib.js`'s
    `FEED_PATHS`, which is what #1179 reported. If a second copy is published again, add it to the
    hash list and to `FEED_PATHS` **in the same PR that starts writing it**.

    On 2026-08-05 (run 98, delivery 38) prettier left both blobs byte-identical to the generator's
    output — it already emits compatible formatting — so nothing was wrong and the pre-commit check
    was not misleading. It was simply **not evidence**: it described a state that a later step was
    free to change, which is the same shape as L62/#924 (an absence proves nothing about a step that
    had no input).

    The first draft of this very note repeated the mistake it describes — it hashed `src/` and only
    CR-checked `public/`, which a LF-preserving reformat passes. Caught in review on #1076. A
    procedure that checks two artifacts by two different standards is only as strong as the weaker
    one, and it reads as thorough precisely because it is two commands.

  - **And do not run that check in Python text mode - it reports `0` whether or not the CRs are
    there.** `io.open(p, encoding='utf-8').read().count('\r')` applies universal-newline
    translation, so CRLF is silently rewritten on the way IN. On 2026-08-02 (run 72) that returned a
    confident `CR bytes: 0` for a freshly generated feed that in fact carried **1264** CRs, which
    `tr -cd '\r' | wc -c` found immediately. Read `'rb'` (or use `tr`). A check whose failure mode
    is to print the answer you were hoping for is worse than no check.
  - **And `grep` cannot answer it either — it is wrong in both directions, and which way depends on
    where you put it.** Reaching for `grep -c $'\r'` instead of the `tr` above is the natural
    substitution, and on this host it never returns the truth. Measured on run 112 against a file
    holding exactly **1** CR, with `tr` and `od` agreeing on 1:

    ```bash
    printf 'a\r\nb\nc\n' > /tmp/crtest.txt
    grep -c $'\r' /tmp/crtest.txt                    # → 0   direct: false NEGATIVE
    echo "n=$(grep -c $'\r' /tmp/crtest.txt)"        # → 3   in command substitution: every line, false POSITIVE
    tr -cd '\r' < /tmp/crtest.txt | wc -c            # → 1   correct
    ```

    The pattern really is byte `0d` (`printf '%s' $'\r' | od -An -tx1` → `0d`), and `-a` does not
    rescue the **direct** form — `grep -ac $'\r'` is still `0`. That is not "grep always returns 0":
    the two forms above disagree with each other, which is the whole point. The result tracks the
    probe's **syntactic position** rather than the file's contents, and that is a property no one
    checks a one-liner for. The false-negative form is the dangerous one, because it is the form a
    delivery check is written in and it prints the `0` the author is hoping to see — the same
    failure shape as the Python text-mode bullet above, one tool over, and easier to trust because
    `grep` reads as a byte-level instrument. Use `tr -cd '\r' | wc -c`, which is what the recipe
    already prescribes; in this environment nothing else has been shown to be trustworthy for
    counting CRs.

  - **The same translation runs on the way OUT, which breaks restore-after-mutation.**
    `pathlib.write_text()` uses text mode, so writing a file back converts every `\n` to `\r\n` on
    this host. Reviewing #1015 (run 76) the Conductor mutated `run_all.py` in place six times and
    asserted `sha256(path.read_text().encode())` matched the original after each restore. It matched
    every time and proved nothing — the read side translated the CRLFs back out, so a file rewritten
    end to end hashed identical to the LF original. The only tell was `git status` listing the file
    as modified with an **empty** `git diff`. Pass `newline=""` when writing, and verify restores
    with `tr`/`'rb'`, never `read_text`. (`newline=""` and `newline="\n"` are byte-identical on
    **write** — measured here on both LF and CRLF content — so the choice is intent, not behaviour:
    `""` for "restore verbatim", `"\n"` for "force LF" as the generators in `scripts/` do. Only the
    default is wrong, and it is worse than "adds CR": a string that already holds `\r\n` comes out
    `\r\r\n`.)
  - **Better: mutate a copy and leave the original alone.** The repo's own mutation sites already do
    this — `test_737_claim_sync.py:206` and `test_powershell_command_resolution.py:138` copy into a
    `tempfile.TemporaryDirectory()` and mutate there, so restore fidelity is never in question. The
    in-place-and-restore pattern is the ad-hoc reviewer's habit, and it is the one that needs the
    binary check.
  - **If the restore is `git checkout -- <file>`, commit first — otherwise the harness reverts to
    HEAD, deletes the very work it is testing, and every later mutation reports a clean tree.** Run
    120 proved a new ledger guard this way. Mutation 1 was detected correctly; `restore()` then ran
    `git checkout --` over both edited files, which were still **uncommitted**; mutations 2, 3 and 4
    all came back `rc=0 fails=[]`, and so did the harness's own closing "restored, baseline clean"
    check. The obvious reading — _these mutations do not discriminate, the guard is weak_ — was
    wrong, and so was the reassuring one: the guard was gone, and a tree with no guard in it has
    nothing to fail. Note the asymmetry with the fidelity rule above: CRLF translation makes a
    restore **inexact**, and this makes it exact against **the wrong baseline**, which is harder to
    see because `git status` afterwards is genuinely clean. Commit before mutating, or snapshot the
    bytes and restore from the snapshot — a restore must be defined against the state you started
    from, not against whatever the tool treats as canonical. Ledger **L182**.
  - **And do not build the mutating script through a Bash-tool command string. The heredoc is not
    the surface — `\\` collapses to `\` everywhere in the command, including inside single quotes.**
    Six instances across runs 73-96 were each recorded as "heredoc mangling"; run 96 measured the
    mechanism and the heredoc turns out to be incidental. Every doubled backslash in a Bash tool
    command is halved before the shell ever sees it:

    | written                         | reaches the shell |
    | ------------------------------- | ----------------- |
    | `printf '%s' 'a\b'`             | `a\b`             |
    | `printf '%s' 'a\\b'`            | `a\b`             |
    | `printf '%s' 'a\\\b'`           | `a\\b`            |
    | `printf '%s' 'a\\\\b'`          | `a\\b`            |
    | `printf '%s' 'a\tb'` / `'a\$b'` | `a\tb` / `a\$b`   |

    Read the first two rows together: those are **single-quoted** arguments, where POSIX guarantees
    the shell performs no processing at all, so a shell cannot be responsible. Only `\\` is
    affected; `\t`, `\$` and a lone `\` pass through, so this is one round of doubled-backslash
    collapsing, not C-style unescaping. A quoted heredoc (`<<'EOF'`) is documented to pass bytes
    verbatim and is hit exactly as hard — run 96 lost `(?<!\\)"\s*"` to `(?<!\)"\s*"` inside one,
    which is why five earlier runs blamed the heredoc.

    Consequences that the "heredoc" framing hid: a `python3 -c "…\\…"` is hit (run 95 recorded this
    and still filed it under heredocs), a `--jq` filter with an escaped literal is hit, and a
    `sed 's/\\//'` is hit. Anything you can write with the file-editing tool is safe, because that
    path never passes through a command string — which is why the standing remedy works, and it
    works for a reason nobody had measured. Write files with the editing tool; if a script must
    transform one, put the script in a file and run it by path. Ledger **L88**, corrected by
    **L135**.

    The failure is loud only by luck. Run 77's cost was a committed file with **0 LF and 169 CR**
    that prettier called unchanged and `git status` called clean; run 96's was caught only because
    the mutation harness asserts its anchor is present before substituting, and the assert fired.

- **The full workflow-logic suite takes ~8 minutes once it actually runs.** It used to finish in
  seconds only because the modules were aborting; any short command timeout now reads as a hang.
  Measured run 142 (2026-08-10) on clean `main` at `7183417`: **480s wall, `rc=1`, 1177 PASS / 83
  FAIL across 16 failing modules** — that failure count is the _expected_ local baseline, not a
  regression (these modules need credentials or a Linux runner). Run 141 had to background it
  against a 10-minute foreground cap. **The figure this line carried until run 142 was ">2 minutes",
  which was true and four times too small** — it was written when the suite was a quarter its
  current size, and a bound that is merely _not false_ stops being a budget you can plan with.
  Re-measure it, do not re-copy it.
- **One module — `test_729_add_collaborator.py` — leaves a zero-byte `U+F022 U+F022` file in the
  repo root.** Reproducible, untracked; it is suite output, not a checkout artifact. `ls -b` renders
  the name `""`, which is what Windows maps `"` to. Bisected in run 77 by running all 50 modules in
  order: it is that one module, not "each full suite run" as this line used to claim, and it is
  there because the test passes `cwd=REPO_ROOT`. Tracked on **#1023** — #945, which this line named
  until run 77, closed on 2026-07-31 while the artifact went on reproducing.
- **When a PR declines to tick a platform-specific criterion, supply the platform.** Agents working
  from a Linux sandbox correctly refuse to claim a Windows result they cannot measure (#944 did
  exactly this). The move is neither to merge on trust nor to bounce the PR — it is to run that
  criterion here, because this is the only host that can. Doing so on #944 turned an unverifiable
  claim into a measured `129 → 0`, and surfaced a scope correction (the cause explained 8 of 26
  modules, not 21) that the author had no way to see.

## Windows git-bash: five ways a command lies instead of failing (runs 60–61, 2026-07-31)

Every one of these produces a confident wrong answer rather than an error, which is what makes them
worth writing down. Two are now blocked by `.claude/hooks/guard_bash.py` — `grep -P` (rule 7) and
the leading-slash `gh api` endpoint (rule 8); the other three are not mechanically detectable from
the command text alone and stay here as judgment.

- **`grep -P` matches nothing and exits non-zero.** PCRE is not compiled into this environment's
  git-bash, so `grep -P` never matches — and because it _fails_ rather than returning "no match",
  `if grep -qP …` silently takes the **else** branch and `grep -P … || echo <default>` prints the
  default. Run 60 used it to audit the public board and was told all 10 open PRs were missing from
  it; all 10 were present. Use a POSIX ERE (`grep -E`) or python. Blocked by `guard_bash.py` rule 7.
- **`/tmp` is not one directory.** git-bash resolves `/tmp` to its own MSYS mount, while a Windows
  `python3` in the same pipeline resolves it to `C:\tmp` — so `cmd > /tmp/x.txt` followed by
  `python3 … open('/tmp/x.txt')` fails with `FileNotFoundError` on a file that bash just wrote and
  can still read. Do not hand paths between bash and Windows python via `/tmp`; use an absolute
  Windows-style path (the session scratchpad) for anything that crosses that boundary.
- **Ad-hoc `python3 -c` printing non-ASCII dies on cp1252.** `print()` encodes with the console
  codepage, so echoing API data that contains an em dash, an arrow or an emoji raises
  `UnicodeEncodeError: 'charmap' codec can't encode character '\u2192'` — mid-way through, so you
  get partial output that looks like a truncated result rather than an encoding fault. Run 60 hit
  this printing a workflow name (`… → aprilhansen.com`) while verifying the public status feed.
  Prefix with `PYTHONIOENCODING=utf-8`. This is the same cp1252 root cause as #945/L35, but a
  _different vector_: `scripts/check-subprocess-encoding.py` guards `subprocess(text=True)` inside
  committed files and cannot see a one-off command's stdout.
- **A console `?` is not proof of a mangled file.** The same codepage that breaks `print()` also
  renders a correctly-stored em dash as `?` in captured output. Before "fixing" an encoding, decode
  the file with `errors='strict'` and test for `'\ufffd'` — run 60 nearly re-encoded a clean
  `docs/lessons-ledger.md` on the strength of terminal rendering alone. Re-confirmed run 61 on
  #963's docstrings: strict-decoded clean, zero replacement characters, no fix needed.
- **A leading slash in a `gh api` endpoint is rewritten into a filesystem path.** `gh api /markdown`
  becomes `gh api "C:/Program Files/Git/markdown"` and fails with `invalid API endpoint` — the error
  blames the endpoint, not the shell, which is what costs the time. Same MSYS argument-mangling
  class as L42's `origin\main;…`, but on a `gh` endpoint rather than a git ref. Drop the slash:
  `gh api markdown`. Blocked by `guard_bash.py` rule 8 (run 61).

## Measuring health: a run count is not a time window (run 61, 2026-07-31)

`gh api ".../actions/runs?per_page=N"` returns the newest N runs, so on a busy repo the **time span
it covers shrinks as activity rises**. On 2026-07-31 the hub's newest 40 runs spanned **51
minutes**. "0 failures in the last 40 runs" was therefore a statement about the last hour, and it
read as a clean day — while `228. WHMCS - Fraud Review` and `502. Google - Analytics Report` had
both failed on schedule that morning and every morning before it. Run 60 published "1 failure/30"
from the same mistake.

- For a health verdict, ask each **scheduled** workflow for its own recent runs
  (`actions/workflows/<file>.yml/runs?per_page=N`) rather than sampling the repo-wide feed. A daily
  cron produces one run a day; it cannot compete with PR churn for a slot in the newest N.
- `?branch=main` does **not** rescue this — scheduled runs are on `main` too. They are simply older
  than the window.
- Separate the two populations before reporting: branch-CI failures on PR head SHAs are normal churn
  (a promoted draft re-runs Phantom Revert Guard and can fail there legitimately — see AGENTS.md),
  whereas a failing scheduled workflow is a standing outage.
- **The workflow file name is not derivable from the number.** `739-process-health.yml` returns a
  bare `404` that reads like "this workflow does not exist"; the real file is
  `739-process-health-metrics.yml`. List `.github/workflows/` and match the numeric prefix rather
  than guessing the slug.

**Run 142 recurrence — this exact trap, with these exact workflows, caught the Conductor again.** A
60-run sweep returned zero failures and would have been published as a clean fleet; those 60 runs
spanned **2h13m** (19:53→22:06Z), and all three standing outages are _daily_, so every one of them
sat outside the window. The per-workflow remedy above works but costs one call per workflow, which
is why it keeps getting skipped under budget pressure. **The one-call form that does not have the
defect** — the filter is applied server-side, before the page is cut, so the page spans days:

```bash
gh api 'repos/OWNER/REPO/actions/runs?status=failure&per_page=30' \
  --jq '.workflow_runs[] | "wf\(.workflow_id) | \(.created_at) | \(.name) | run \(.id)"'
```

⚠️ **…and that output is NOT searchable by FFC workflow number, which is what turned the recurrence
into a near-miss.** The runs API returns the **run's** display title, not the workflow's name: 502
renders as `Google Analytics Report` and 735 as `Dependabot Affected Repos`, with the `502. ` /
`735. ` prefix — the key the entire FFC taxonomy is organised around — absent. Grepping that list
for `502` finds nothing while 502 is failing, and the natural reading of "not in the failure list"
is **recovered**. Run 142 drew exactly that conclusion, and additionally flagged 502 as a _new,
unreported_ outage under its display name. Both errors point the flattering way.

Resolve `workflow_id` against the workflow list; never match on `.name`:

```bash
gh api 'repos/OWNER/REPO/actions/workflows?per_page=100' \
  --jq '.workflows[] | "\(.id) | \(.path) | \(.name)"'
```

`740-scheduled-workflow-failure-alert.yml:185` already carries this rule for the alert marker, with
the reason spelled out — the gap was never in the automation, only in what an operator reading a
failure list by hand had in front of them.

## `gh search` is index-backed and lags — never audit completeness with it (run 62, 2026-07-31)

`gh search issues` / `gh search prs` read GitHub's **search index**, which trails the write APIs by
an interval nobody controls. So they are fine for "find me something" and **wrong for "is anything
missing"** — the items they omit are the newest ones, which are exactly the items a completeness
audit is looking for.

Run 62 built the "what should be on the public board" set from
`gh search issues --owner FreeForCharity --label agentic-os --state open`: it returned **52** items
and the audit reported **0 missing**. The authoritative REST enumeration
(`repos/{owner}/{repo}/issues?state=open&labels=agentic-os`, paginated, per repo) returned **53**,
and the omitted one — PR #965, created minutes earlier — was genuinely absent from the board. The
search-based audit was not merely incomplete; it returned a **clean bill of health** for a board
that had a hole in it.

- For any "everything of kind X" question, enumerate with **REST per repo** and paginate.
- This is a sibling of the stale-read rule below, but **not** the same thing and the remedy differs:
  a post-write read is stale for seconds and re-polling cures it; a search index is stale on its own
  schedule and re-polling is just a slower wrong answer. Change endpoint, don't retry.
- The tell is a **denominator that looks plausible**. 52 vs 53 raises no alarm on its own — so print
  both sides' counts (`expected=N board=M`) and treat the comparison, not the empty result set, as
  the finding. Scripted in issue #966.

## Board & PR-creation env facts (validated 2026-07-24)

- **The public "Agentic OS" board (org project #9) has NO automation.** Its only enabled built-in
  workflow is `Auto-add sub-issues to project`; label/item auto-add is absent, and `Item closed` and
  `Pull request merged` are **disabled**. So **neither issues nor PRs auto-add, and statuses never
  self-update** — every item and every status is placed by hand. Do not assume a new `agentic-os`
  issue reached the board. Check:

  ```bash
  gh api graphql -f query='query{organization(login:"FreeForCharity"){projectV2(number:9){workflows(first:20){nodes{name enabled}}}}}'
  ```

  - **Audit the board with `scripts/audit-agentic-os-board.py`, not by hand.** Because nothing
    auto-adds, every run re-derives the same three sets — missing from the board, on the board with
    no Status, closed/merged but not `Done` — and hand-rolling it was wrong twice in two runs, in
    two different ways (#966). Run 61 read `items(first:100)` and got 100 of 208. Run 62 built the
    _expected_ half from `gh search issues` and got 52 where REST returned 53: the **search index
    lags writes**, so it under-reports precisely for the newest items, which are the ones most
    likely to be missing from a hand-maintained board. It failed silently and in the reassuring
    direction. The script reads both sides from authoritative endpoints (REST per repo; GraphQL
    paginated on `$endCursor`), prints `expected=N board=M` so a short denominator is visible rather
    than invisible, and exits non-zero on any finding **or** any failed enumeration — never 0 on a
    read it could not complete. Read-only; it adds nothing and sets no status.
    ```bash
    GH_TOKEN=… python3 scripts/audit-agentic-os-board.py          # prose report
    GH_TOKEN=… python3 scripts/audit-agentic-os-board.py --json   # machine-readable
    ```

- **A negative read straight after a successful GitHub write means "unknown", not "failed".** The
  write APIs are strongly consistent; the _list/read_ endpoints behind them are not, and the lag is
  seconds. Three instances so far: `mergeQueueEntry` reads `null` for a few seconds after a good
  `--auto` enqueue (2026-07-24); `gh project item-list` read back the full board **without** an
  issue that `gh project item-add` had just added successfully (2026-07-25, org project #9); and the
  `POST /pulls` HTTP-500 episode below, where the branch had in fact been pushed. Re-poll before
  concluding anything failed, and **never let an immediate negative read trigger a retry loop** —
  run 41 burned ~51 attempts that way. Where the write is idempotent (`item-add` is: two calls
  produced one row) a retry is merely wasteful; where it is not, it is destructive.
  - **For board membership specifically, stop re-polling and change endpoint: the item's own
    `projectItems` connection is authoritative and does not lag.** On 2026-08-05 (run 99) two
    `gh project item-add` calls returned exit 0 and **no output**, and
    `gh project item-list 9 --limit 400` then returned exactly `320` items twice in a row, minutes
    apart, with neither new card in it. That is the 2026-07-25 instance above repeating — but
    re-polling the project's item list is the slow way to find out, and on a 320-item board each
    attempt is an expensive GraphQL page walk (run 71 drained the points budget to 0/5000 doing
    exactly this as a lookup). Ask the issue instead:

    ```bash
    # Replace 1077 with the issue you are checking; use pullRequest(number:N) for a PR — the
    # projectItems connection is on both types and reads the same way.
    gh api graphql -f query='{repository(owner:"FreeForCharity",name:"FFC-Cloudflare-Automation"){
      issue(number:1077){ projectItems(first:5){ nodes{ id project{ number title } } } } }}'
    # → {"id":"PVTI_…","project":{"number":9,"title":"Agentic OS"}}   — immediately, while the
    #    project's own item-list was still serving a page without it
    ```

    It answers the question you actually have ("did the card land, and what is its id?"), returns
    the item id you need for `item-edit` anyway, and costs one small query instead of 320 rows. Same
    distinction as `.auto_merge` vs `enqueuePullRequest` below: re-polling cures a stale read, but
    the cheaper move is usually to ask the endpoint that is authoritative for the state.

  - **Separately: some fields are not lagging, they simply never carry the state you want.** On
    2026-07-31 (run 60) `gh pr merge --auto` printed nothing for #914/#958/#959 and `.auto_merge`
    read `null` for all three — but `enqueuePullRequest` answered "already in the queue" for every
    one. That `null` was not a few-second lag that re-polling would clear; `.auto_merge` describes
    the _auto-merge flag_, and a PR that went straight into the merge queue never sets it. So
    distinguish the two failure shapes: re-poll cures a stale read, but no amount of re-polling
    cures a field that does not model the thing. When a read looks negative, confirm against the
    endpoint that is **authoritative for that state** (here, the `enqueuePullRequest` mutation)
    before either retrying or concluding anything — and note that silence from `gh pr merge --auto`
    is success, not a no-op.

- **Push the branch before opening the PR.** On 2026-07-24, `POST /repos/…/pulls` returned **HTTP
  500 with an empty body** for >20 minutes, across **multiple FFC repos**
  (`FFC-IN-freeforcharity.org` and `FFC-Cloudflare-Automation`) and all three clients (`gh`, REST,
  GitHub MCP), with full and minimal bodies alike. Not auth, not rate limit, and githubstatus.com
  reported all-operational throughout — so treat "GitHub is green" as no guarantee that PR creation
  works. Because the work was committed and pushed first, nothing was lost: the branches simply wait
  for their PRs. **Adopt commit-and-push-first as the default order.**
- **A per-item success log is not evidence of completeness — assert `processed == expected`.** On
  2026-07-30 a conductor run fed 22 board items to `while read … done < missing.txt` and added
  **21**. The file had no trailing newline (`'\n'.join(...)` from Python), so `read` discarded the
  final line. Every line that _did_ run printed `ADDED`, so the log looked perfect; the miss was
  visible only by counting. This generalizes past shell: whenever a loop reports success per item,
  the loop's own output cannot tell you whether an item was skipped before it ever started. Print a
  count and compare it, or terminate the file with a newline and still compare the count.

## Windows host facts (the Conductor's git-bash environment, validated 2026-07-31)

The scheduled Conductor runs on Windows 11 under git-bash, which is **not** the environment CI uses.
These are the ways that difference has actually bitten, each found the expensive way.

- **A `--jq` expression containing `//` is mangled the same way** (ledger L51). MSYS rewrites
  `(.conclusion//"null")` into a Windows path, and jq then fails with a **type** error —
  `cannot add: string ("completedC:/Program File …")` — which reads like a malformed filter and
  sends you to rewrite the query instead of the quoting. Use `\(.field)` interpolation rather than
  jq's `//` default operator, or prefix with `MSYS_NO_PATHCONV=1`.
- **`git show <rev>:<path>` is mangled by MSYS path conversion — but only when the path starts with
  a dot.** `git show origin/main:.github/…` reaches git as `origin\main;.github\workflows\…` and
  aborts with `fatal: ambiguous argument`; `git show origin/main:docs/foo.md` works untouched on the
  same command line. It is the leading dot that trips the heuristic, not the colon or the slashes —
  the full validation is in the section above. Prefix dot-paths with `MSYS_NO_PATHCONV=1` (and quote
  the argument). Do **not** read this as "always prefix `git show`": stating the rule without its
  qualifier is what made an automated reviewer file a false finding against a correct command on
  #981. The failure is loud — but see the next point for how it gets swallowed.
- **Never let `||` supply a benign default for a command that can crash** (ledger L42). A
  `cmd | grep … || echo "none found"` prints the reassuring branch when `cmd` _fails_, not only when
  the match is empty. That silently turned a failed supersession check into a pass. Check the exit
  status, or make the fallback read `CHECK FAILED`.
- **Never read an exit code through a pipe** (ledger L50). `cmd | tail; echo $?` reports `tail`'s
  status, not `cmd`'s. `scripts/audit-agentic-os-board.py | tail -45; echo "EXIT=$?"` printed
  `EXIT=0` while the script had exited **1** with six real findings — the script's entire contract
  is to exit non-zero on any finding or any enumeration it could not complete, and the pipe threw
  exactly that away. Redirect to a file and read `$?` before piping, or `set -o pipefail`.
- **`gh` inside a `while read` loop eats the loop's stdin**, losing one input line per invocation.
  Read from a dedicated descriptor (`while read … <&3; done 3< file`) or redirect the child
  (`gh … </dev/null`). A board audit silently processed 73 of 74 rows this way.
- **Space-delimited `awk` columns break on human labels.** Project board statuses include
  `In Progress` and `In Review`, so `$6` reads an item id instead of a state. Emit tab-delimited
  rows (`IFS=$'\t'`) for anything carrying a human-authored field.
- **Native Windows Python cannot open a git-bash path.** `open('/tmp/x')` raises `FileNotFoundError`
  while `ls /tmp/x` works, so a bash heredoc that writes to `/tmp` and a Python reader disagree
  about the same filename. Use `C:/…` paths (the session scratch dir) whenever Python is on either
  end.
- **Text-mode `subprocess` decodes with cp1252 here, not UTF-8** — pass `PYTHONUTF8=1` or an
  explicit `encoding=`. `scripts/generate-agentic-os-status.py` is a production script that dies on
  an emoji in an issue title without it, and the public status feed now routinely contains 🚨 alert
  titles. Enumerated and tracked in #945.
- **The full `tests/workflow-logic/` suite takes ~8 minutes here** once it actually runs (480s
  measured run 142; see the fuller note above for the pass/fail baseline). Budget for it rather than
  treating it as a quick check, and background it — run 141 hit a 10-minute foreground cap. (Crashes
  used to make it finish fast — see ledger L35.)
- **You cannot approve your own PR, and every agent authenticates as `clarkemoyer`.**
  `gh pr review --approve` returns `Can not approve your own pull request`. A conductor review is
  therefore a **comment**, and the merge queue's required checks — not an approval — are the gate.

## "The workflow is blocked" is not "the outcome is blocked" (validated 2026-07-30)

**Before escalating a reporting or visibility outcome to a human, check whether the artifact can be
produced by any path you already control.**

The public `/agentic-os` status feed sat frozen for ~12 hours and was recorded as un-completable,
blocked on Clarke rotating a revoked Key Vault PAT. What was actually blocked was workflow **502's
`deliver` job** — which 401s at its `Generate Agentic OS status feed` step. The generator itself
states its auth contract in its own docstring:

> REST only (no `gh` CLI, no GraphQL), so it runs anywhere a token is present. Authentication is a
> single environment variable, `GH_TOKEN`.

So the Conductor ran it locally and delivered the result by the same branch + PR route `deliver`
would have used:

```bash
GH_TOKEN=$(gh auth token) python3 scripts/generate-agentic-os-status.py --output feed.json
# then: cp to src/data/ AND public/data/ on a branch, PR, merge queue
```

A stale public dashboard is bad; a public dashboard that **misreports which approvals are
outstanding** is worse, and that was the actual state. The dead credential was a real finding and
still needs rotating — but it gated one delivery path, not the outcome. Read the script's auth
contract before concluding a human is the blocker.

## When something claims a mechanism is dead, look for an artifact that mechanism produced (validated 2026-07-30)

An issue asserted that the whole failure-alerting layer was dead, on solid evidence:
`event=workflow_run` in this repo totals **0**, and both alerter workflows had never produced a run.
That evidence was still literally true — and the conclusion was wrong by six days, because 740 had
since been converted from `workflow_run` to a `schedule` poll (`cron: '9,39 * * * *'`) and had **115
successful runs**.

The decisive evidence was not the run list. It was a _rolling alert issue that existed_, whose
footer read `Managed by 740. Repo - Scheduled Workflow Failure Alert.` — created **7 seconds** after
740's run started. An artifact the mechanism emitted outranks any inference about whether the
mechanism works, and it is usually cheaper to find. Two issues closed on that single observation.

Corollary: an issue's stated premise can decay while every fact quoted in it stays true. Re-derive
the premise, not the facts.

## The local Conductor workspace is not the state — the log issue is (validated 2026-07-30)

`state\CONDUCTOR.md` is a convenience cache, exactly as the routine says, and it will silently fall
behind: it was last written at run 45 while runs **46 and 47 both completed** without updating it.
Two runs of gate decisions, merges and open threads existed only in the #719 thread.

**Derive run state (including the run number) from the newest entries in the log issue first**, and
treat the local file as corroboration. A local file that is _usually_ current is more dangerous than
one that is obviously absent, because nothing about reading it feels like a risk.

## Any session whose project root is not this repo runs with NONE of its hooks loaded (validated 2026-08-09 run 134; corrected 2026-09-08, #1237)

`.claude/hooks/` protects a Claude Code session whose **project root is this repository**. That is
the whole condition, and the population failing it is **not** the Conductor alone:

- the scheduled **Conductor**, whose project root is its own workspace
  (`C:\ClaudeCodeDesktop\Claude_AI_OS_Routine`), carrying a `permissions` block and **no `hooks`
  block at all**. It `cd`s into the clone to work; that does not load the clone's hooks. So the
  session with the most write authority in the system — the one that approves gates, merges PRs and
  hand-delivers the public feed — runs with none of the enforcement every other agent gets.
- the scheduled **multi-repo cloud worker**, which clones five FFC repos side by side and runs with
  its project root set to their **parent**. Measured 2026-09-08: project root `/home/user`, **no
  `/home/user/.claude` at all**, and the hub's `.claude/settings.json` present with all four hook
  events and never loaded.

> **This line used to name the sandboxed agents as the protected class, and that is what expired.**
> The cloud worker is the population that does the issue→PR work, and it is exempt for exactly the
> Conductor's reason. So the triage rule below — "we put a hook on it" does not close a finding —
> applies to **any** finding an unrooted session can hit, not only a Conductor-side one. The
> Conductor's half is the harder one (its config lives on an operator workstation, in no
> repository); the worker's half is **FFC-controlled**, because the session's project root is chosen
> by the environment definition. Ledger **L261**.

This is not theoretical, and the demonstration is worth repeating rather than summarising. Run 134
ran a board audit as `python3 scripts/audit-agentic-os-board.py 2>&1 | tail -25; echo "AUDIT rc=$?"`
and read **rc=0** — from `tail`. The audit had refused to run (no `GH_TOKEN`) and said so, in output
that happened to be visible; had the refusal been quieter it would have scored as a clean board.
Replaying that exact command string into `guard_bash.py` on stdin **blocks it, exit 2**, naming
ledger L50 verbatim. The rule was already there, already correct, and already promoted out of prose
_because_ it kept being violated — and it could not fire.

Two things follow, and they are easy to collapse into one:

1. **"We put a hook on it" does not close a finding for an unrooted session.** When triaging a
   lesson in step 7, a hook is the strongest tier _for a session rooted at the repo_ and no tier at
   all for the Conductor or a multi-repo worker. If the mistake is one either can make, prose is the
   real ceiling until that session loads hooks — so write it as prose that expects to be re-read,
   and say in the ledger's tier column why prose is the ceiling.
2. **Every future hook inherits this hole**, silently. For the Conductor nothing in the repo can
   detect it, because the file that would fix it is on the operator's workstation and in no
   repository. For the cloud worker the fix _is_ reachable — the session definition, or a
   `/home/user/.claude/settings.json` rendered from the tracked template at clone time.

The fix is a `hooks` block in the Conductor workspace's own settings pointing at a clone's
`.claude/hooks/`. Ledger **L218**.

### The hub now ships that block, and a way to prove it loaded (#1042)

The config is no longer an untracked file on one workstation. Two artifacts, both reviewable:

- **`.claude/conductor/settings.template.json`** — the workspace's `hooks` block, with the clone's
  path as a `__HUB_CLONE__` placeholder. It is deliberately **absolute**, not `$CLAUDE_PROJECT_DIR`:
  that variable is the _session's_ project root, which for the Conductor is the workspace, so
  copying the hub's own `settings.json` across produces valid JSON with a real `hooks` block that
  resolves to nothing. That near-miss is the one to remember — it passes every check that stops at
  config presence.
- **`scripts/verify-conductor-hooks.py`** — renders the template (`--render`) and, every bootstrap,
  **probes the guard the config points at**: one command that must be blocked (the run-134 L50
  shape) and one that must be allowed (`git status --porcelain`). Both verdicts have to land, so
  neither an `exit 0` stub nor an `exit 2` stub passes; any other exit code is reported as
  _crashed_, never as a detection. Exit 0/1, and it prints a `HOOKS: …` line in **both** directions
  for the run's START comment — a verifier that only speaks on failure leaves "guarded" and "the
  check never ran" identical in the record.

```bash
python3 scripts/verify-conductor-hooks.py --workspace "$PWD"          # bootstrap check
python3 scripts/verify-conductor-hooks.py --render --workspace <ws>   # one-time wiring
```

⚠️ **State `--workspace` when you KNOW the session's project root — and never pass `$PWD` as a
stand-in for knowing.** The Conductor's bootstrap line above is correct because that session's cwd
_is_ its project root; the same spelling in any other session is a laundered guess. An agent whose
cwd is a clone should run the script **bare**, which uses `$CLAUDE_PROJECT_DIR` when the session
exports it and refuses rather than certifying when it must guess.

The blunt version of this rule ("always state it") shipped in `AGENTS.md` for one commit as
`--workspace "${CLAUDE_PROJECT_DIR:-$PWD}"`, and in a worker session — where that variable is unset
— it returned `HOOKS: wired, exit 0` for a session loading nothing: the advice reintroduced the
defect it was written to prevent. Dropping the flag entirely is what made this script certify itself
in the first place (#1237), so the two failures bracket the same mistake from opposite sides — the
flag is not the point, **knowing** is. Run with no arguments from inside the clone, it used to print
`HOOKS: wired … exit 0` in the five-repo cloud-worker session, whose project root is `/home/user`
and which loads nothing at all. Nothing about that was dishonest: the guard it found was real, the
two-sided probe genuinely passed, and every check resolved — against the clone, which was not the
session. A verifier written to make L218's false green impossible had its own, reached through its
default argument, and the reassuring direction is the default's.

Since #1237 that case is refused rather than certified: an **inferred** workspace (the cwd fallback,
never `--workspace` or `$CLAUDE_PROJECT_DIR`) that itself ships `.claude/hooks/` reports
`HOOKS: UNVERIFIED … exit 1` and names the sibling-clone parent it thinks the real root is. So a
third wording now exists and there are still only two exit codes — "I could not tell" and "not
wired" are the same instruction to the run about to start, and only the record needs them apart.

`$PWD` is safe here even though git-bash spells it `/c/...`: the script translates an MSYS
`/<drive>/` prefix to `C:/` **on Windows only**. That translation is not cosmetic — native Windows
Python reads a leading `/c/` as _drive-relative_ and resolves `/c/Users/x` to `C:\c\Users\x`, so
without it a correctly-wired workspace reports NOT WIRED, and `--render` writes the settings into a
directory the real session never reads while reporting success. Same trap as the `/c/...` note
above, reached through an operator's `$PWD` rather than through a heredoc.

Full reasoning, and why option (c) — moving the Conductor's project root to the hub clone — was left
to @clarkemoyer rather than taken: `docs/runbooks/conductor-hook-wiring.md`. That option changes how
the privileged session is launched, and a misconfigured guard there blocks the Conductor mid-run
rather than an agent mid-task.

## Review threads are GraphQL-only — `--json reviewThreads` is not a field (validated 2026-07-30)

`gh pr view <n> --json reviewThreads` errors and dumps the full valid-field list. AGENTS.md already
requires GraphQL to _resolve_ a thread; the **read** side is equally GraphQL-only, which is easy to
assume otherwise when every other PR attribute is available over REST.

```bash
gh api graphql -f query='{repository(owner:"FreeForCharity",name:"FFC-Cloudflare-Automation"){
  pullRequest(number:904){reviewThreads(first:50){nodes{isResolved path}}}}}'
```

## Running & authorizing GitHub Actions workflows (IMPORTANT)

In a self-hosted/local remote environment the `gh` CLI is typically pre-authenticated — run
`gh auth status` to confirm (and `gh auth login` if not). When available it acts as a real user
(e.g. `clarkemoyer`) with `workflow` + `repo` scopes. **Prefer `gh` for anything Actions-related.**

**Update (validated 2026-07-06):** the MCP GitHub App installation **now has `actions: write`** —
`actions_run_trigger` with `method: run_workflow` successfully dispatched 101/113/209/210 from the
web sandbox (`204` queued). Two gotchas: (a) **all dispatch inputs must be strings** — a numeric
value (e.g. `issue_number: 609`) fails with `422 Invalid value for input`; pass `"609"`. (b) MCP
still **cannot approve environment deployment gates** (no `pending_deployments` tool, and direct
REST stays 403 in the sandbox), so gated jobs sit at `status: waiting` until a human reviewer
(`clarkemoyer`) approves. The paragraph below is retained as history in case scopes regress:

> Previously (pre-2026-07): the App installation lacked `actions: write`, so
> `actions_run_trigger`/`run_workflow` returned `403 Resource not accessible by integration`, and
> the only sandbox trigger path was `issues`-event workflows. (MCP has always been fine for PRs,
> issues, comments, reviews.)

### Claude Code on the web (sandbox) — `gh` web-flow auth does NOT work here (IMPORTANT)

When running as **Claude Code on the web**, do not waste time trying to `gh auth login` (web/device
flow) to get "full org" access — it cannot work in this sandbox, and here is the proof so a future
session doesn't rediscover it the hard way:

- All outbound HTTPS goes through the agent egress proxy. The proxy **intercepts `api.github.com`
  and injects its own auth**, ignoring whatever token `gh`/`curl` sends. A request to
  `https://api.github.com/user` with a **bogus** `Authorization` header — or **no** header at all —
  still returns `200` as `clarkemoyer`. So no token a web/device flow obtains is ever used.
- Direct repo/Actions calls via that proxy auth return
  `403 "GitHub access is not enabled for this session…"` for this org, so `gh`/`curl` cannot
  dispatch workflows or approve deployments from the sandbox either.
- The **MCP** GitHub tools are the working channel in the sandbox (scoped to this repo). As of
  2026-07-06 they **can dispatch `workflow_dispatch` workflows** (see the update above) and
  create/assign issues, open PRs, push files, comment, and read Actions runs/logs.

Net effect in the web sandbox: you **can dispatch workflows via MCP** (string inputs only) and
trigger any `issues`-event workflow (e.g. Website Provision) by creating + assigning an issue via
MCP — but you still **cannot approve an environment gate**; a human reviewer (`clarkemoyer`)
approves those. See the next section.

### Provision a website repo + add a maintainer (primary workflow)

This is the canonical way to "establish the repo for `<domain>` and add a GitHub user as
maintainer". It runs **`701. Website - Provision`** (`.github/workflows/701-website-provision.yml`),
which on `issues: [assigned]` creates `FFC-EX-<domain>` from the FFC template, enables GitHub Pages,
adds the Technical POC as a `maintain` collaborator, and (only if the zone is controlled in FFC
Cloudflare) enforces apex + `www` GitHub Pages DNS. All privileged steps run inside Actions with
`secrets.CBM_TOKEN`, so this path needs **no** `actions: write` from the caller.

From the web sandbox (works today), using the admin-minimal template
(`.github/ISSUE_TEMPLATE/07-adminonly-provision-website.yml`) — create the issue **with an
assignee** via MCP so the `assigned` event fires:

- Title: `[WEBSITE REQUEST] <domain>` (apex, no `https://`, no `www`)
- Labels: `website-request`, `admin-provision`, `github-pages`, `cloudflare`
- Body sections (issue-form headings are parsed verbatim):
  - `### Website Domain (no http://)` → `<domain>`
  - `### Technical POC GitHub Username` → the maintainer's GitHub login (omit/blank to skip)
- Assignee: any user (e.g. `clarkemoyer`) — assignment is what triggers the run.

> **Gotcha — keep all prose ABOVE the `###` sections.** `extractSection` captures everything from a
> heading to the next `###` _or end of body_, so any explanatory text placed **after** the last
> section (e.g. a `---` note after `### Technical POC GitHub Username`) is slurped into that field's
> value. A maintainer login then fails validation and is silently skipped
> (`Skipping invalid GitHub username for maintainer`), and the repo is created without the
> maintainer. Put any narrative at the top of the body, before `### Website Domain`.

Then watch the run via MCP (`actions_list` / `get_job_logs`). **If the zone is controlled in FFC
Cloudflare**, the `dns` (`cloudflare-prod-write`) and `repo` (`github-prod`) jobs sit at
`status: waiting` on environment approval, and `repo` is chained behind `dns` — i.e. the repo is
**only** created once the DNS repoint is approved. The sandbox cannot approve; ask `clarkemoyer` to
approve both gates (UI → _Review deployments_, or the `gh api … pending_deployments` flow below).

From a `gh`-authed environment you can instead dispatch directly:
`gh workflow run 701-website-provision.yml --ref main -f domain=<domain> -f technical_poc_github_username=<login>`.

### Dispatch a workflow

```bash
gh workflow run <workflow-file>.yml --ref <branch>
# e.g.
gh workflow run 202-whmcs-export-products.yml --ref main
```

`git push` also triggers `push`-event workflows, but environment-gated jobs still wait for approval
(see below).

### Environment approval gate (`whmcs-prod`)

Workflows that use `environment: whmcs-prod` (all WHMCS jobs) require a deployment approval; the run
sits at `status: waiting`. Reviewer is `clarkemoyer`, and `gh` is authed as them, so approve it
directly:

```bash
RUN_ID=<run id>
# find the environment id + confirm you can approve
gh api repos/FreeForCharity/FFC-Cloudflare-Automation/actions/runs/$RUN_ID/pending_deployments \
  --jq '.[] | {env: .environment.name, env_id: .environment.id, current_user_can_approve}'
# approve
gh api -X POST repos/FreeForCharity/FFC-Cloudflare-Automation/actions/runs/$RUN_ID/pending_deployments \
  -F "environment_ids[]=<env_id>" -f state=approved -f comment="approved"
```

**Do not try to confirm the approval from the POST's own output — confirm by re-reading the run.**
The response shape does not match the `pending_deployments` GET, so even an array-aware filter
errors. On 2026-07-25 this exact command:

```bash
gh api -X POST repos/FreeForCharity/FFC-Cloudflare-Automation/actions/runs/$RUN_ID/pending_deployments \
  -F "environment_ids[]=$ENV_ID" -f state=approved \
  --jq '.[0] | "\(.status) \(.environment.name)"'
```

printed `expected an object but got: string ("github-prod")` — while the approval had **succeeded**
and the run moved `waiting → in_progress`. Same trap as the read-after-write note above: the failure
was in the confirmation, not the action, and reacting to it would mean re-approving an
already-approved gate. Drop the `--jq` on the POST and verify with:

```bash
gh api repos/FreeForCharity/FFC-Cloudflare-Automation/actions/runs/$RUN_ID --jq '.status'
```

**"Drop the `--jq`" does not mean "discard the output" — read it for errors, just never for
confirmation.** A conductor run on 2026-07-25 took the rule above one step too far and sent the
approval with `> /dev/null 2>&1`, then reported it approved on the strength of the rule. It had not
been: the command used `-f "environment_ids[]=$ENV_ID"` instead of `-F`, and `-f` sends `["<id>"]` —
an array of **strings** — where the API requires integers. The POST rejected it, the run stayed at
`status: waiting`, and the one place that said so had been routed to `/dev/null`.

So the two halves are not interchangeable:

- the **POST output** is the only place a _rejected_ approval reports itself;
- the **run's `status`** is the only trustworthy sign an _accepted_ one took effect.

Use `-F` for `environment_ids[]` (typed — `-f` is string-only and fails this endpoint silently from
the caller's point of view), keep the POST's stderr, and still confirm from the run.

### Watch a run / read results

```bash
gh run view <run id>                 # summary
gh run view <run id> --log           # full logs (read-only export catalogs print here)
gh api repos/FreeForCharity/FFC-Cloudflare-Automation/actions/runs/<id>/jobs --jq '.jobs[]|{name,status,conclusion}'
```

To wait for completion, poll in a background Bash task with an `until`/loop on
`gh api .../runs/<id> --jq '.status'` (do not foreground-sleep).

## WHMCS API (Key Vault + APIM architecture)

WHMCS automation is **fully Key-Vault-backed and IP-stable**. The end-to-end path is:

> **Validation status (2026-06-28):** the hardened path is proven in production. A keyless call to
> the APIM gateway returns `401` (the `whmcs` API is `subscriptionRequired: true`), and a real
> `windows-latest` runner dispatch of **`202. WHMCS - Export Products`**
> (`202-whmcs-export-products.yml`) on `main` completed `success` — the `whmcs-secrets-from-kv`
> action loaded `WHMCS_APIM_SUBSCRIPTION_KEY` (masked) and the export returned live data (30
> products, 535 client products) through OIDC → KV → APIM → Cloudflare → WHMCS.

```
GitHub runner ──OIDC──► Azure (ffc-admin-kv-writer) ──► Key Vault (creds + APIM key)
runner ──POST + Ocp-Apim-Subscription-Key──► APIM apim-ffc-gateway-prod (egress 20.231.116.111)
        ──► Cloudflare ──► WHMCS origin (freeforcharity.org/hub/includes/api.php)
```

**WHMCS admin UI paths** (for direct links in issue comments/replies — the admin directory is
renamed): `https://freeforcharity.org/hub/globaladmin/` — e.g. `clientssummary.php?userid=<id>`,
`clientsprofile.php?userid=<id>`, `clientsservices.php?userid=<id>`,
`orders.php?action=view&id=<orderid>`.

**Where application answers live (validated 2026-07-07):** the charity-onboarding application's
answers (org name, requested domain, mission, contacts) are **product custom fields** on the
onboarding service — NOT client-level fields. Client `companyname` stays empty and
`GetClientsDetails` returns client custom fields without names; use `GetClientsProducts` (workflow
219 exports it) to read the application with field names.

**Identify an application by DOMAIN, not by the triage name (validated 2026-07-07).** The masked
triage tables (209/210) show the **applicant's personal first name**, not the org — the org name is
only inside the mission text. Matching on a name-initial guessed from the org name will find the
wrong charity. To find "the application for `<domain>`" use **workflow 221 (WHMCS Application
Search)** — it sweeps `GetClientsProducts` and returns the matching client id + readable fields.
Fastest confirm from the sandbox once `az` is authed (see below): read `GetClientsProducts` for a
`clientid` directly via APIM. See `docs/restored-radiance-first-fullchain-retro.md`.

### Azure CLI from the sandbox (device-auth) — direct WHMCS reads + Azure inspection

`az` is **not preinstalled**, but you can install it into a venv and device-auth as the admin
(`clarkemoyer@freeforcharity.org`), which unblocks direct WHMCS queries (KV creds → APIM) and Azure
AD reads:

```bash
python3 -m venv azvenv && ./azvenv/bin/pip install -q azure-cli
export AZURE_CONFIG_DIR="$PWD/azconfig"
./azvenv/bin/az login --use-device-code --allow-no-subscriptions   # give the code to the user
```

- **Reads work:** `az ad app federated-credential list`, `az keyvault secret show`, and querying
  live WHMCS by fetching the `read-all-ffc-whmcs-*` secrets and POSTing to the APIM gateway with the
  `Ocp-Apim-Subscription-Key` header (no gate needed — this is how client 419 was confirmed).
- **Azure AD IAM writes are BLOCKED by the harness auto-mode classifier** (creating/updating a
  federated credential is high-severity). Provide the exact `az` command for a human to run, or ask
  for a Bash allow-rule. Full identity inventory + repair commands:
  **`docs/azure-oidc-federated-credentials.md`** — including the **`m365-prod` credential-subject
  typo** (`FFC-Cloudflare-Automation-`, trailing hyphen) that breaks every M365 job with
  `AADSTS700213`, and the `whmcs-prod-read` setup.

### Credentials come from Key Vault via OIDC (KV is master — never a GH secret copy)

- Composite action **`.github/actions/whmcs-secrets-from-kv`**: `azure/login@v3` (OIDC, no Azure
  password in GitHub) → `az keyvault secret show` from `kv-ffc-admin-prod-cbm` → masks → exports
  `WHMCS_API_IDENTIFIER`, `WHMCS_API_SECRET`, and `WHMCS_APIM_SUBSCRIPTION_KEY` to `GITHUB_ENV`
  (heredoc-delimited). Mirrors `cloudflare-tokens-from-kv`.
- **Scoped KV secret names** (like the Cloudflare tokens):
  `{wr-all,read-all}-ffc-whmcs-api-identifier`, `…-ffc-whmcs-api-secret`,
  `…-ffc-apim-whmcs-subscription-key` (+ a `…-ffc-whmcs-api-url`). WHMCS is a single credential, so
  `read-all-*` and `wr-all-*` hold identical values; `scope` (default `write`) only selects which
  identity/copy is used. The action defaults to `write`.
- **OIDC identifiers are repository Variables** (not env secrets — they are non-secret GUIDs):
  `vars.WR_ALL_FFC_AZURE_KV_CLIENT_ID` / `vars.WR_ALL_FFC_AZURE_TENANT_ID`. So `whmcs-prod` holds
  **no** secrets; the per-environment **federated credential**
  (`repo:FreeForCharity/FFC-Cloudflare-Automation:environment:whmcs-prod` on `ffc-admin-kv-writer`)
  is what authorizes the OIDC exchange. Each WHMCS job sets `permissions: id-token: write`.
- Scripts resolve creds from those env vars via `Resolve-WhmcsCredentials` in
  `whmcs-api-common.ps1`, so the action is a drop-in — no per-script credential wiring.

### Calls route through APIM for a static egress IP

- `WHMCS_API_URL` in every WHMCS workflow points at the APIM gateway
  `https://apim-ffc-gateway-prod.azure-api.net/whmcs/api.php` (not the origin). The `whmcs` API
  proxies to `freeforcharity.org/hub/includes` and **requires the `Ocp-Apim-Subscription-Key`**
  (subscription `whmcs-ops`). `Invoke-WhmcsApi` and the self-contained export scripts add that
  header from `WHMCS_APIM_SUBSCRIPTION_KEY` when set (unset ⇒ they call WHMCS directly).
- **WHMCS-side config (one-time):** in System Settings → General Settings → Security, allowlist
  `20.231.116.111` under **API IP Access Restriction** and set **Proxy IP Header** to
  `CF-Connecting-IP`. The latter is essential: WHMCS is behind Cloudflare, and APIM appends the
  dynamic runner IP to `X-Forwarded-For`; reading `CF-Connecting-IP` makes WHMCS use APIM's stable
  IP instead. See `docs/whmcs-apim-routing.md`.
- Sandbox testing: you CAN hit the live WHMCS API from this sandbox via the APIM gateway (fetch the
  identifier/secret/`whmcs-ops` key from KV with `az`, POST with the `Ocp-Apim-Subscription-Key`
  header). `whmcs-prod` no longer holds the credential.

### Scripts

- Onboarding: `whmcs-client-add.ps1` (AddClient), `whmcs-contact-add.ps1` (AddContact),
  `whmcs-order-add.ps1` (AddOrder); shared helpers in `whmcs-api-common.ps1`. Product/custom-field
  discovery via `whmcs-products-export.ps1` (prints a catalog to the job log).

### Architectural memory

- **Key Vault is the single source of truth** for the WHMCS credential AND the APIM subscription
  key; GitHub consumes them at runtime via OIDC. Never reintroduce a GH-environment copy of the
  secret (that drift is exactly what broke the Cloudflare token for 4 months). The legacy GH secret
  `ZBBEPFQ5W7RCSIME0NOQOYRQIDGTKBPU` / `WHMCS_API_ACCESS_KEY` is **deprecated** (nothing reads it)
  and can be deleted from `whmcs-prod`. The `whmcs-secrets-from-kv` action no longer fetches or
  exports a WHMCS access key at all (the WHMCS API does not use one); the per-script `-AccessKey`
  parameter remains as a generic, inert WHMCS API option.
- **Rotate** the WHMCS secret or the APIM key by adding a new version of the relevant
  `*-ffc-whmcs-*` / `*-ffc-apim-whmcs-subscription-key` KV secret — no GitHub change needed.

## Candid (GuideStar) — MCP + API workflows

- **Interactive:** the repo `.mcp.json` registers Candid's official remote MCP server
  (`https://mcp.candid.org/mcp`, OAuth with a Candid account — run `/mcp` to connect). Tools: org
  search (name/EIN/seal level), org identification, knowledge search, PCS taxonomy matching. Note:
  Claude Code on the web only sees org-level connectors, so this entry helps local/desktop sessions.
- **Workflows:** `801-candid-charity-check.yml` (validate 501(c)(3)/Pub78/BMF/OFAC by EIN) and
  `802-candid-essentials-search.yml` (find profile + transparency-seal level). Both read-only,
  environment `candid-prod-read` (no approval gate), keys from KV via
  `.github/actions/candid-keys-from-kv` (`Subscription-Key` header, host allowlist
  `api.candid.org`).
- **Provisioning status:** scaffolding is inert until the one-time setup in
  `docs/candid-api-and-mcp.md` is done (Candid developer keys → KV secrets
  `read-all-ffc-candid-{charity-check,essentials}-key`, an ungated `candid-prod-read` environment,
  and a federated credential for `ffc-admin-kv-reader`). **The environment needs no secrets** —
  since #912 both workflows read the OIDC identifiers from the repo Variables
  (`vars.READ_ALL_FFC_AZURE_*`), and `scripts/check-env-secret-references.py` fails CI if a workflow
  goes back to the `secrets.*` form against an environment that does not carry copies.
- **No write API:** the annual Candid Platinum profile update stays a manual web form — the
  paste-sheet automation is issue #493.
