"""Guard: an `always()` step that authenticates must also check what ran (#899).

726's tracking-issue step was gated on a bare `if: always()`. When the preflight
or the Azure login bailed, the audit never ran, `GH_TOKEN` was never populated,
and the step still executed — dying on `gh issue list` with

    Could not search for an existing tracking issue — refusing to risk opening a
    duplicate.

That message is a true statement about a situation that did not exist. Worse, it
is emitted **last**, so it is the first line a reader sees and the one a failure
notification surfaces — burying the real one-line cause (two missing environment
secrets) under a second error that looks like an API or permissions fault.
Observed on run 30334346136; fixed on `main` in 6994830 by adding
`&& steps.audit.conclusion != 'skipped'`.

WHAT THIS MODULE ADDS
    #899 asked for the *class*, not the instance: "any step that reports results
    with `if: always()` while depending on a credential loaded by an earlier step
    has it." Sweeping the tree found **no** second instance — every other
    `always()` step is either already guarded on a `steps.*.conclusion` /
    `needs.*.result`, or is an `upload-artifact` / cleanup step that authenticates
    nothing and degrades cleanly (`if-no-files-found: warn`).

    A clean sweep is exactly when to install the guard, because the next instance
    will be written by someone who never read #899. A bare `always()` is correct
    and common for cleanup and artifact upload, so this does NOT ban it — it bans
    the specific combination that produced the defect: **runs unconditionally,
    and authenticates with a credential an earlier step had to load.**

HOW A STEP PASSES
    * it does not use `always()`; or
    * its `if` also constrains an earlier step or job — any `steps.<id>.outcome`,
      `steps.<id>.conclusion`, or `needs.<job>.result` reference; or
    * it authenticates nothing (no `gh`/`az` call, no credential env var).
"""

from __future__ import annotations

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import WORKFLOWS, load_workflow  # noqa: E402

# A guard is any reference to what an earlier step or job actually did.
GUARD_RE = re.compile(r"steps\.[A-Za-z0-9_-]+\.(outcome|conclusion)|needs\.[A-Za-z0-9_-]+\.result")

# Commands that need a credential somebody had to load first.
AUTHED_CMD_RE = re.compile(r"(?<![\w./-])(gh|az)\s+[a-z]", re.MULTILINE)

# Credential env vars. A step naming one of these is authenticating with it.
CREDENTIAL_TOKENS = (
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "CBM_TOKEN",
    "AZURE_",
    "WHMCS_",
    "CLOUDFLARE_",
    "Ocp-Apim-Subscription-Key",
    "secrets.",
)


def _uses_always(cond) -> bool:
    return "always()" in str(cond or "")


def _is_guarded(cond) -> bool:
    return bool(GUARD_RE.search(str(cond or "")))


def _authenticates(step: dict) -> bool:
    """Does this step need a credential an earlier step had to populate?"""
    run = str(step.get("run") or "")
    if AUTHED_CMD_RE.search(run):
        return True
    blob = run + "\n" + str(step.get("env") or "") + "\n" + str(step.get("with") or "")
    return any(tok in blob for tok in CREDENTIAL_TOKENS)


def _offending_steps():
    out = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        wf_name = path.name
        wf = load_workflow(wf_name)
        for job_name, job in (wf.get("jobs") or {}).items():
            if not isinstance(job, dict):
                continue
            for step in job.get("steps") or []:
                if not isinstance(step, dict):
                    continue
                cond = step.get("if")
                if not _uses_always(cond) or _is_guarded(cond):
                    continue
                if not _authenticates(step):
                    continue  # cleanup / artifact upload: nothing to get wrong
                out.append(
                    f"{wf_name} :: {job_name} :: "
                    f"{step.get('name') or step.get('uses') or '<unnamed>'}"
                )
    return out


def test_no_unguarded_always_step_authenticates():
    bad = _offending_steps()
    assert bad == [], (
        "these steps run on a bare always() AND authenticate, so they execute even when the "
        "step that loads their credential never ran — and report a credential failure as an "
        "API failure (#899):\n" + "\n".join(f"  {b}" for b in bad) + "\n\nAdd a "
        "`steps.<id>.conclusion` / `needs.<job>.result` term to the `if:`."
    )


def test_726_still_carries_its_guard():
    """The instance that produced this class — pin it so it cannot regress."""
    wf = load_workflow("726-repo-rulesets-drift-audit.yml")
    steps = wf["jobs"]["audit"]["steps"]
    issue_step = next(
        (s for s in steps if "tracking issue" in str(s.get("name", "")).lower()), None
    )
    assert issue_step is not None, "726's tracking-issue step is gone or was renamed"
    cond = str(issue_step.get("if") or "")
    assert "always()" in cond, "726 must still deliver a report when the audit FAILED (#854)"
    assert _is_guarded(cond), (
        "726's tracking-issue step must stay guarded on the audit's conclusion — a bare "
        f"always() reports a missing credential as a failed issue search (#899). Got: {cond!r}"
    )


def test_the_detector_sees_the_pre_fix_shape():
    """Load-bearing: without this, a detector that finds nothing passes vacuously.

    Feeds the checker 726's exact pre-fix condition and requires both halves of
    the verdict — unguarded, and authenticating.
    """
    pre_fix = "${{ always() }}"
    assert _uses_always(pre_fix), "must recognize always()"
    assert not _is_guarded(pre_fix), "a bare always() must not read as guarded"
    assert _authenticates({"run": 'existing=$(gh issue list --search "drift")'}), (
        "a `gh issue list` call must read as authenticating"
    )
    # ...and the post-fix condition must clear it.
    post_fix = "${{ always() && steps.audit.conclusion != 'skipped' }}"
    assert _is_guarded(post_fix), "the shipped fix must read as guarded"


def test_benign_always_steps_are_not_flagged():
    """A bare always() is correct for cleanup and artifact upload — don't ban it."""
    assert not _authenticates(
        {"uses": "actions/upload-artifact@v7", "with": {"name": "report", "path": "r.json"}}
    ), "upload-artifact authenticates nothing and must stay allowed on a bare always()"
    assert not _authenticates(
        {"run": "if (Test-Path $env:WORKSPACE_KEY_PATH) { Remove-Item $env:WORKSPACE_KEY_PATH }"}
    ), "a cleanup step must stay allowed on a bare always()"


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {str(e)[:500]}")
    sys.exit(1 if failures else 0)
