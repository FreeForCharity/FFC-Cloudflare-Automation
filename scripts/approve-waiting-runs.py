#!/usr/bin/env python3
"""Batch-approve workflow runs waiting at an environment approval gate.

An **operator tool**, not a workflow. GitHub's ambient `GITHUB_TOKEN` cannot
approve environment protection gates — only a human designated as an environment
reviewer can — so this runs under *your own* `gh` auth (whatever `gh auth status`
reports) and approves on your behalf. It productizes the manual approval loop used
to clear the 2026-07-07 waiting queue (issue #636).

Structural alternative (recommended, tracked in #636): split an ungated
`google-prod-provision` environment for the idempotent 505/503 provisioning writes
so they don't gate at all — mirroring `whmcs-prod-read`. This helper is the operator
fallback for the environments that legitimately stay gated.

Examples:
  # Preview every waiting run (default is dry-run):
  python3 scripts/approve-waiting-runs.py

  # Approve every run waiting on google-prod-write:
  python3 scripts/approve-waiting-runs.py --environment google-prod-write --approve

  # Approve everything you're allowed to approve, with a note:
  python3 scripts/approve-waiting-runs.py --approve --comment "batch approve for fleet rollout"

Exit codes: 0 on success (including nothing to do), 1 on any approval error.
"""
import argparse
import json
import subprocess
import sys

DEFAULT_REPO = "FreeForCharity/FFC-Cloudflare-Automation"


def gh_json(args):
    """Run a gh command and parse stdout as JSON (utf-8, tolerant)."""
    r = subprocess.run(
        ["gh", *args], capture_output=True, encoding="utf-8", errors="replace"
    )
    if r.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {r.stderr.strip()}")
    out = r.stdout.strip()
    return json.loads(out) if out else None


def gh_ok(args):
    r = subprocess.run(
        ["gh", *args], capture_output=True, encoding="utf-8", errors="replace"
    )
    return r.returncode == 0, (r.stderr or r.stdout).strip()


def _waiting_runs_one_shape(args):
    """The rows one query shape returns, ``[]`` if it answered nothing usable,
    or ``None`` if the shape itself failed.

    Deliberately total, and for the same reason as the sibling in
    `scripts/generate-agentic-os-status.py`: the union in `collect_waiting_runs`
    is what provides the floor, and it can only do that if one bad shape does
    not take the other down with it. `gh_json` raises on any non-zero `gh`
    exit, so without this a network blip, a rate-limit 403 or an auth hiccup on
    the FIRST shape discards a perfectly good answer from the second -- which
    is exactly the redundancy this union was added to provide (L341).

    Failure is `None` rather than `[]` so the caller can tell "this shape could
    not be read" from "this shape says the queue is empty". Collapsing the two
    would recreate the silent zero the union exists to defend against."""
    try:
        rows = gh_json(args)
    except (RuntimeError, ValueError) as exc:
        print(
            f"WARNING: waiting-run query shape failed ({' '.join(args)}): {exc}. "
            "Continuing with the remaining shape(s); the union is a floor, not a "
            "single source.",
            file=sys.stderr,
        )
        return None
    return rows if isinstance(rows, list) else []


def collect_waiting_runs(repo, default_branch="main"):
    """Waiting runs, as the **union of two redundant query shapes**.

    `--status waiting` is not reliable and its failure mode is to under-report
    rather than to error: measured on the hub 2026-10-07T13:14Z, the unqualified
    shape answered `total_count: 0` on two consecutive reads while four runs were
    provably waiting, and adding a branch filter to the same call returned all
    four (L341). The unqualified shape had been correct six minutes earlier, so
    the defect is transient — which is why the fix is redundancy rather than a
    replacement query.

    Unioned by run id, so the result can only ever be too long, never too short.
    For an approval tool that is the correct direction: an extra row is a line of
    preview output, a missing row is a gate the operator never sees.

    Client-side filtering over an unfiltered run list is **not** an alternative
    and was measured too — a held gate is routinely days old and falls off the
    newest-100 page entirely, which is exactly the population this tool exists
    to act on. `scripts/generate-agentic-os-status.py` and
    `.github/workflows/734-stale-waiting-run-janitor.yml` carry the same union
    for the same reason; keep the three in step.
    """
    fields = "databaseId,workflowName,displayTitle"
    shapes = [
        ["run", "list", "--repo", repo, "--status", "waiting",
         "--limit", "100", "--json", fields],
        ["run", "list", "--repo", repo, "--status", "waiting",
         "--branch", default_branch, "--limit", "100", "--json", fields],
    ]

    by_id = {}
    per_shape = []
    for shape in shapes:
        rows = _waiting_runs_one_shape(shape)
        per_shape.append(len(rows) if rows is not None else None)
        for row in rows or []:
            rid = row.get("databaseId")
            if rid is not None:
                by_id.setdefault(rid, row)

    # Every shape failing is NOT an empty queue, and must never be reported as
    # one: this is an approval tool, so "no gates waiting" is the single most
    # dangerous sentence it can print. One shape surviving is enough -- that is
    # the whole point of the union -- but zero is a read failure, and a read
    # failure has to be loud.
    if all(count is None for count in per_shape):
        raise RuntimeError(
            "every waiting-run query shape failed; refusing to report an empty "
            "queue, which is indistinguishable from a clean one. Re-run once "
            "`gh` is healthy."
        )

    # The two shapes are NOT symmetric, so raw inequality is the wrong trigger.
    # The unqualified shape returns waiting runs across every branch; the
    # branch-qualified one is a strict subset of it. `unqualified > branch` is
    # therefore the EXPECTED reading whenever a gate waits on a non-default
    # branch -- a dispatch of a gated workflow on a `claude/*` branch is routine
    # here -- and attributing that to L341 sends the next reader hunting a
    # defect that is not there. L341's signature is the other direction: the
    # unqualified shape answering SHORTER than a strict subset of itself, which
    # cannot happen without it.
    unqualified, branch_qualified = per_shape
    both_read = unqualified is not None and branch_qualified is not None
    if both_read and unqualified < branch_qualified:
        # Reported rather than silently repaired: the operator needs to know the
        # upstream defect is live, not just get the right answer this once.
        print(
            f"WARNING: the unqualified waiting-run shape returned FEWER runs "
            f"({unqualified}) than the branch-qualified subset "
            f"({default_branch}:{branch_qualified}); using the union of "
            f"{len(by_id)}. A superset cannot be short: this is the known "
            "upstream `--status waiting` defect (L341).",
            file=sys.stderr,
        )
    elif not both_read:
        shown = ["err" if c is None else str(c) for c in per_shape]
        print(
            f"WARNING: a waiting-run query shape could not be read "
            f"(unqualified={shown[0]}, branch={default_branch}:{shown[1]}); "
            f"using the union of {len(by_id)} from the shape(s) that answered. "
            "The per-shape failure is reported above.",
            file=sys.stderr,
        )
    # `unqualified > branch_qualified` is deliberately silent: it is the normal
    # superset relationship, and a warning that fires in normal operation is one
    # nobody reads when it finally matters.

    return list(by_id.values())


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default=DEFAULT_REPO, help="owner/repo (default: FFC automation)")
    ap.add_argument("--environment", help="Only approve runs waiting on this environment")
    ap.add_argument(
        "--approve",
        action="store_true",
        help="Actually approve. Without this flag the tool only previews (dry-run).",
    )
    ap.add_argument("--comment", default="Batch approval via approve-waiting-runs.py")
    args = ap.parse_args()

    runs = collect_waiting_runs(args.repo)
    if not runs:
        # Deliberately not "nothing to do": `--status waiting` can answer empty
        # while runs are provably waiting (L341), and this tool's whole job is
        # to find them. Both query shapes coming back empty is the best evidence
        # available, not proof, and the operator is the one who can tell.
        print(
            "No waiting runs found by either query shape. If you believe a gate "
            "is pending, confirm it directly:\n"
            f"  gh api repos/{args.repo}/actions/runs/<run-id> --jq .status"
        )
        return 0

    errors = 0
    acted = 0
    for run in runs:
        rid = run["databaseId"]
        pend = gh_json(
            ["api", f"repos/{args.repo}/actions/runs/{rid}/pending_deployments"]
        ) or []
        for dep in pend:
            env = dep.get("environment", {})
            env_name, env_id = env.get("name"), env.get("id")
            if args.environment and env_name != args.environment:
                continue
            can = dep.get("current_user_can_approve", False)
            label = f"run {rid} [{env_name}] {run.get('displayTitle', '')}"
            if not can:
                print(f"  SKIP (not an approver): {label}")
                continue
            if not args.approve:
                print(f"  would approve: {label}")
                acted += 1
                continue
            ok, msg = gh_ok(
                [
                    "api", "--method", "POST",
                    f"repos/{args.repo}/actions/runs/{rid}/pending_deployments",
                    "-F", f"environment_ids[]={env_id}",
                    "-f", "state=approved",
                    "-f", f"comment={args.comment}",
                ]
            )
            if ok:
                print(f"  APPROVED: {label}")
                acted += 1
            else:
                print(f"  ERROR approving {label}: {msg.splitlines()[-1] if msg else '?'}")
                errors += 1

    verb = "approved" if args.approve else "would approve"
    print(f"\n{acted} run(s) {verb}" + (" (dry-run; pass --approve to act)" if not args.approve else ""))
    if errors:
        print(f"{errors} approval error(s).")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
