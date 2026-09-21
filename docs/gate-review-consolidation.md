# Making approval gates cheaper without removing them

Written 2026-09-21, from the New Heights migration
([#1342](https://github.com/FreeForCharity/FFC-Cloudflare-Automation/issues/1342)), where a two-week
hosting deadline collided with a reviewer who could not sit next to the Actions tab. Every
observation below is something that happened in that one session.

The goal is **not** fewer gates. It is gates that cost a reviewer one informed click instead of
several uninformed ones spread over hours.

## What actually went wrong

### 1. The gate is at the END of a 15-minute run

`706`'s `deliver` job is correctly the only gated one — `convert` is read-only and needs no
approval, which is already the right split. But `convert` takes **13–14 minutes** (the capture
crawls a live shared host at a 250 ms politeness delay), so the gate does not appear until
minute 15. A reviewer told "approve this and you can go" has to wait a quarter of an hour first, for
a decision that takes ten seconds.

Worse, it is not pre-approvable. GitHub environment approvals are per-run and per-pending-
deployment; there is no way to say "approve the next one".

### 2. A failure after the gate destroys the approval

If `deliver` fails, the approval is spent. Recovery is a fresh dispatch: another 14-minute crawl of
the charity's origin, and another approval. The crawl is the expensive part and it is **entirely
redundant** — the capture that the failed run already produced is sitting in that run's artifacts,
uploaded by the step literally named `Upload the neutralized capture (handoff to deliver)`.

### 3. Approvals did not compose

New Heights is three hostnames. Before this session, delivering them meant three dispatches, three
crawls and **three approvals** — and they could not even run in parallel, because
`integrate-clone-into-nextjs.mjs` does `rmSync(public/)` before copying, so the second run deletes
the first site. Serial, hours apart, one human click each.

Fixed by `extra_hosts` (this PR): one run, one approval, per-host completeness gates.

### 4. The reviewer is shown the environment name, not the decision

GitHub's _Review deployments_ dialog says `github-prod` and nothing else. It does not say which
charity, which repo, how many pages, or what the PR will contain. `706` computes exactly that — it
has a `Report what would be written` step — but the output lands in the run log, which the approver
has to go and find.

`706` already mitigates this in the one place it can: `run-name` carries
`<domain> -> <repo> (<mode>)`, added for this reason (see the comment above it). That is the right
instinct and it stops at the run title.

## Recommendations, in value order

### A. Let a `deliver` reuse a previous run's capture — `reuse_capture_from_run`

The single highest-value change. Add an input naming a prior run id; `convert` downloads that run's
`neutralized capture` artifact instead of crawling.

Consequences:

- A post-gate failure costs **seconds and one approval**, not 14 minutes and one approval.
- The gate appears **immediately** on a retry, so a reviewer can approve and leave.
- A charity's small shared host is crawled **once per migration**, not once per attempt. On this
  session alone the same origin was crawled four times; three of those were re-work.

Guard it: refuse a capture from a run whose `domain` input differs, or the artifact is expired, and
say which — a silently stale capture would publish yesterday's site.

### B. Split `convert` and `deliver` into separately dispatchable runs

With (A) in place this becomes natural. `convert` runs ungated and publishes its report; a human
reads it; `deliver` is then a **cheap, fast** dispatch that hits the gate within a minute.

This inverts the current cost model. Today the reviewer waits for the machine. Then the machine
waits for the reviewer — which is the correct direction, because the reviewer is the scarce
resource.

### C. Put the decision in front of the approver

Have `convert` post its summary where the decision is made — a comment on the tracking issue, or the
job summary, with the numbers that matter: pages captured per host, completeness percentage, forms
neutralized, self-containment result, and the routes that will change.

An approval is only meaningful if the approver can see what they are approving. Right now "approve"
means "I trust that the run did the right thing", which is not a review.

### D. Batch by scope, not by run

`extra_hosts` is the instance; the pattern generalizes. **Any workflow dispatched N times for N
related objects should take a list**, because the approval count follows the dispatch count. Worth
auditing: `102` (one domain), `211` (one order), `230` (one field) are all one-object-per-dispatch
with a gate each.

The counter-rule matters just as much: `211`'s header says there is deliberately **no** bulk
accept/cancel, because each live state change should be an explicit human decision. So batch where
the objects are **parts of one decision** (three hostnames of one charity's site) and never where
they are **separate decisions** (three charities' orders).

### E. Audit for jobs gated only by inheritance

`github-prod` currently protects both "this job holds a credential" and "this job writes somewhere".
Those deserve the same gate, but a job that does **neither** and merely sits in a workflow that has
an environment somewhere is paying for a gate it does not need.

`706` gets this right — `convert` builds the whole site, runs Playwright over it and needs no
approval. `730. Repo - Audit Environment Gates` already exists; pointing it at this specific
question (which gated jobs neither read a credential nor write outside the runner?) would find the
rest.

## One mechanism worth documenting rather than changing

**A `workflow_dispatch` can name a branch ref, and the checked-out scripts come from that ref.**

During this session `706` was dispatched with `ref: claude/new-heights-education-app-kr7yq0` so it
ran with an unmerged fix (#1344). The conversion then passed the self-containment gate it had been
failing, in production, against the real site — **before** the fix merged.

That is a genuinely useful capability and it is not written down anywhere: a fix to this pipeline
can be validated on a real site without first landing on `main`. It also means a reviewer approving
a gated run should check which **ref** the run is on, because a branch dispatch runs unreviewed code
with the environment's credentials. Both halves belong in `AGENTS.md`.

## Not recommended

**Removing the reviewer from `github-prod` for a working session.** It was considered here and
declined by @clarkemoyer, correctly: that environment guards repo creation and repo writes across
the whole automation, not just the job in front of you, and the blast radius of an unattended window
is every workflow that uses it — not the one you were trying to unblock.
