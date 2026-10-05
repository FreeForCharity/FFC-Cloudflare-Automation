"""Job-graph tests for 701 (Website - Provision).

`dns` is skipped by design for every zone Free For Charity does not control.
A job with no always() in its `if:` gets GitHub's implicit success(), which
treats a skipped ancestor as a failure. So any job downstream of `dns` without
always() is silently skipped for those zones. That is how `content` and
`maintainers` were skipped on the first live run for a zone FFC does not
control: the repo was created, but the charity's content was never applied
and the requester was never added, and the run still reported success.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import load_workflow  # noqa: E402

WF = load_workflow("701-website-provision.yml")
JOBS = WF["jobs"]


def needs(name: str) -> list[str]:
    n = JOBS[name].get("needs", [])
    return [n] if isinstance(n, str) else list(n)


def downstream_of(root: str) -> set[str]:
    out: set[str] = set()
    changed = True
    while changed:
        changed = False
        for name in JOBS:
            if name in out or name == root:
                continue
            if any(n == root or n in out for n in needs(name)):
                out.add(name)
                changed = True
    return out


def test_every_job_downstream_of_dns_opts_out_of_implicit_success():
    below = downstream_of("dns")
    assert {"repo", "content", "maintainers", "verify", "finalize"} <= below, below
    missing = sorted(j for j in below if "always()" not in str(JOBS[j].get("if", "")))
    assert not missing, (
        f"{missing} would be skipped whenever dns is skipped (every zone FFC does not "
        "control); start their if: with always() and state the real precondition"
    )


def test_content_and_maintainers_still_require_a_created_repo():
    for name in ("content", "maintainers"):
        cond = " ".join(str(JOBS[name]["if"]).split())
        assert "needs.repo.result == 'success'" in cond, (name, cond)
        assert "needs.resolve.outputs.skip != 'true'" in cond, (name, cond)


def test_maintainers_is_skipped_on_a_dry_run():
    # A dry run creates no repo, so there is nobody to add anyone to.
    cond = " ".join(str(JOBS["maintainers"]["if"]).split())
    assert "inputs.dry_run != true" in cond, cond


def test_content_rehearses_on_a_dry_run_against_the_template_when_no_repo_exists():
    # `content` is the content rehearsal (test_dry_run_skips_write_gates lists it
    # as REHEARSAL-INSIDE), so a dry run must reach it. A first-time dry run has
    # no repo to clone, so it clones the template the repo would come from.
    cond = " ".join(str(JOBS["content"]["if"]).split())
    assert "dry_run" not in cond, cond
    step = next(s for s in JOBS["content"]["steps"] if s.get("id") == "apply")
    assert step["env"]["TEMPLATE_REPO"] == "${{ needs.resolve.outputs.template_repo }}", step["env"]
    body = step["run"]
    guard = "if (([string]$env:DRY_RUN) -eq 'true') {"
    # Two dry-run branches: the clone-source fallback, then the write gate.
    assert body.count(guard) == 2, body.count(guard)
    clone_guard = body.index(guard)
    write_guard = body.index(guard, clone_guard + 1)
    anchors = {
        "view": "$viewOut = gh repo view $repoFull",
        "not_found": "-notmatch 'Could not resolve to a Repository|HTTP 404'",
        "fallback": "$cloneSource = [string]$env:TEMPLATE_REPO",
        "clone": "gh repo clone $cloneSource $cloneDir",
        "dry_notice": "content rendered and staged, not committed or pushed",
        "commit": 'git commit -m "chore: apply footer + leadership content"',
        "push": "git push origin HEAD:main",
    }
    for name, text in anchors.items():
        assert body.count(text) == 1, f"content step should contain its {name} anchor once: {text!r}"
    at = {name: body.index(text) for name, text in anchors.items()}
    # Only a genuine not-found falls back to the template; anything else throws.
    assert clone_guard < at["view"] < at["not_found"] < at["fallback"] < at["clone"], at
    assert at["clone"] < write_guard, (at, write_guard)
    # The only commit and push sit in the else of the write gate: the dry-run
    # branch prints its notice, then `} else {` hands off to commit and push.
    assert body.count("git push origin") == 1
    else_ = body.find("} else {", at["dry_notice"])
    assert write_guard < at["dry_notice"] < else_ < at["commit"] < at["push"], (write_guard, else_, at)
    assert guard not in body[write_guard + 1 : at["push"]], "another dry-run branch opened before the push"


def test_content_patch_needs_only_the_charity_name():
    # The gate used to demand every footer field and all four social links;
    # any gap skipped the patch, and a sparse charity (FFC-EX-iwilf.org#6)
    # went live showing Free For Charity's EIN, phone, offices, GuideStar seal
    # and staff. The script now empties what is missing and marks it pending,
    # so only the name gates it.
    step = next(s for s in JOBS["content"]["steps"] if s.get("id") == "apply")
    body = step["run"]
    start = body.index("$canApplyTemplate =")
    gate = body[start : body.index("\n", start)]
    assert gate.strip() == "$canApplyTemplate = -not [string]::IsNullOrWhiteSpace($charityName)", gate
    assert "Test-SocialLinksPresent" not in body
    assert body.count("$canApplyTemplate = $canApplyTemplate") == 0, "a later clause re-narrows the gate"
    # The pending list the script reports reaches the step's outputs.
    assert "-SummaryPath $summaryPath" in body, body
    assert "content_pending_fields=" in body, body


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {str(e)[:600]}")
    sys.exit(1 if failures else 0)
