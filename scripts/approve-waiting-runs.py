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
        rows = gh_json(shape) or []
        per_shape.append(len(rows))
        for row in rows:
            rid = row.get("databaseId")
            if rid is not None:
                by_id.setdefault(rid, row)

    if len(set(per_shape)) > 1:
        # Reported rather than silently repaired: the operator needs to know the
        # upstream defect is live, not just get the right answer this once.
        print(
            f"WARNING: waiting-run query shapes disagreed (unqualified={per_shape[0]}, "
            f"branch={default_branch}:{per_shape[1]}); using the union of {len(by_id)}. "
            "A short count from --status waiting is a known upstream defect (L341).",
            file=sys.stderr,
        )

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
