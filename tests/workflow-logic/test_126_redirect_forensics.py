"""Tests for 126 — domain redirect forensics.

Regression anchors from the 2026-09-04 incident, where a live charity site was
forwarding to a replacement domain that served nothing yet, and neither
existing tool could locate the redirect:

  - 121 follows redirects and reports only the FINAL status, so
    `301 -> elsewhere -> 200` was reported as a healthy `200`.
  - 101 writes its Cloudflare audit to an ARTIFACT, which an agent session
    restricted to api.github.com cannot download.

Both properties this workflow exists to guarantee are therefore pinned here:
the chain must be walked hop by hop (never silently followed), and the output
must reach the JOB LOG rather than an artifact. A future "cleanup" that moves
the report into an artifact would restore the exact blindness this replaced.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import find_step, load_workflow, step_run

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "domain-redirect-forensics.mjs"
WORKFLOW = "126-domain-redirect-forensics.yml"


def test_the_script_exists():
    assert SCRIPT.is_file(), f"{SCRIPT} is missing — the workflow wraps it"


def test_the_scripts_own_self_test_passes():
    """Behavioural: exercises classification, chain walking and rule rendering."""
    proc = subprocess.run(
        ["node", str(SCRIPT), "--self-test"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "0 failed" in proc.stdout, proc.stdout


def test_redirects_are_walked_manually_not_followed():
    """The whole point: a followed redirect destroys the evidence."""
    src = SCRIPT.read_text(encoding="utf-8")
    assert "redirect: 'manual'" in src, (
        "the chain walker must use fetch(redirect: 'manual') — following "
        "redirects reduces the chain to its endpoint, which is the 121 defect "
        "this tool exists to avoid"
    )


def test_the_report_goes_to_the_job_log_not_an_artifact():
    """An artifact is unreadable to an egress-restricted caller — see the docstring."""
    wf = load_workflow(WORKFLOW)
    text = (REPO_ROOT / ".github" / "workflows" / WORKFLOW).read_text(encoding="utf-8")
    # Anchor first, so this cannot pass vacuously against a renamed workflow.
    assert "forensics" in wf["jobs"], f"expected a 'forensics' job, got {list(wf['jobs'])}"
    assert "upload-artifact" not in text, (
        "126 must not upload an artifact: artifact blob storage is a different "
        "host from api.github.com, so an agent diagnosing an outage cannot read it"
    )


def test_it_is_read_only_and_ungated():
    """Diagnosing an outage must never wait on a deployment approval."""
    wf = load_workflow(WORKFLOW)
    job = wf["jobs"]["forensics"]
    assert job.get("environment") == "cloudflare-prod-read", job.get("environment")
    perms = wf.get("permissions", {})
    assert perms.get("contents") == "read", perms
    # id-token is required for the OIDC hop to Key Vault; anything broader is not.
    assert set(perms) <= {"contents", "id-token"}, perms


def test_inputs_reach_the_script_through_the_environment():
    """Ledger L28: an input interpolated into `run:` text is executed as code."""
    run = step_run(WORKFLOW, "forensics", "Run redirect forensics")
    assert "INPUT_DOMAINS" in run, run
    assert "${{" not in run, f"inputs must not be interpolated into the script text: {run}"


def test_an_empty_domain_list_is_refused():
    """Ledger L214: a deleted env: mapping must fail closed, not run on nothing."""
    run = step_run(WORKFLOW, "forensics", "Run redirect forensics")
    assert "refusing to run with no target" in run, run

    proc = subprocess.run(
        ["node", str(SCRIPT), "--domains="],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(REPO_ROOT),
    )
    assert proc.returncode != 0, "an empty --domains must be refused"
    assert "--domains is required" in (proc.stdout + proc.stderr)


def test_the_cloudflare_hop_degrades_instead_of_losing_the_chain():
    """A Key Vault failure must not cost us the HTTP half, which matters most."""
    wf = load_workflow(WORKFLOW)
    step = find_step(wf, "forensics", "cloudflare-tokens-from-kv")
    assert step.get("continue-on-error") is True, step


def test_the_dns_read_pages_instead_of_asking_for_one_big_page():
    """An over-cap page size is clamped silently, truncating the collection.

    CI caught the first revision of this script asking for a page of 200
    against the 100 cap, via tests/workflow-logic/test_api_page_size_cap.py.
    (That value is spelled out in prose rather than as a literal query
    parameter, because the guard scans the whole tree as text and would flag
    this docstring itself — it did, on the first attempt at this test.) The
    failure mode is the one this whole tool exists to expose: a report that
    under-reports while looking complete. Pinned here as well as in the
    repo-wide guard, because the guard checks a number and this checks the
    shape — a future edit could satisfy one and not the other.
    """
    src = SCRIPT.read_text(encoding="utf-8")
    assert "cfGetAll(token, `/zones/${zone.id}/dns_records`)" in src, (
        "the DNS read must paginate via cfGetAll, not request a single large page"
    )
    assert "hasMorePages" in src, "the paging stop condition must be a named, testable predicate"


def test_a_failed_zone_lookup_is_not_reported_as_absent():
    """Copilot finding: an errored lookup must not become a confident negative.

    `inspectZone()` returning a bare false conflated "no zone in either
    account" with "both lookups failed". The first sends a responder to hunt
    for a third-party Cloudflare account; the second means we simply do not
    know. Turning an unknown into a confident absence is the exact failure
    class this tool was written to stop committing.
    """
    src = SCRIPT.read_text(encoding="utf-8")
    assert "'unknown'" in src and "'absent'" in src, (
        "inspectZone must distinguish absent from unknown, not return a boolean"
    )
    assert "anyLookupFailed" in src, "a failed lookup must be tracked, not swallowed by `continue`"
    assert "zoneVerdict === 'absent'" in src, (
        "the NEITHER-account message must be gated on 'absent', never on a falsy value"
    )


def test_multi_label_public_suffixes_are_handled():
    """Copilot finding, verified against sites_list.csv: jsbt.org.au is real.

    Taking the last two labels unconditionally maps every Australian domain to
    `org.au`, so a redirect between two different orgs compares equal and is
    reported as a benign same-site hop — failing in the flattering direction on
    the one signal this tool exists to raise.
    """
    proc = subprocess.run(
        ["node", "--input-type=module", "-e",
         "import {registrableDomain} from './scripts/domain-redirect-forensics.mjs';"
         "console.log(registrableDomain('jsbt.org.au'), registrableDomain('other.org.au'));"],
        capture_output=True, text=True, encoding="utf-8", cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    a, b = proc.stdout.split()
    assert a == "jsbt.org.au", a
    assert a != b, f"two different .org.au orgs must not compare equal ({a} vs {b})"


def test_absence_is_scoped_to_the_accounts_actually_queried():
    """Copilot finding #3, same root cause as #2 one step further out.

    A token missing from the environment means that account was never CHECKED.
    Reporting "NEITHER FFC nor CM" after querying one of them is a confident
    claim about evidence never gathered, and the third-party-account guidance
    that follows would send a responder hunting for a zone that may sit in the
    account we simply could not read.
    """
    proc = subprocess.run(
        ["node", "--input-type=module", "-e",
         "import {describeAbsence} from './scripts/domain-redirect-forensics.mjs';"
         "console.log(JSON.stringify({both:describeAbsence(['FFC','CM']),one:describeAbsence(['FFC'])}));"],
        capture_output=True, text=True, encoding="utf-8", cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    import json as _json
    out = _json.loads(proc.stdout)
    both, one = " ".join(out["both"]), " ".join(out["one"])
    assert "THIRD-PARTY" in both, both
    assert "THIRD-PARTY" not in one, one
    assert "NEITHER" not in one, one
    assert "CM" in one, "the unchecked account must be named"


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {str(e)[:400]}")
    sys.exit(1 if failures else 0)
