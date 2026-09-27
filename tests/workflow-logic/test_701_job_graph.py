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


def test_content_and_maintainers_still_require_a_created_repo_and_a_real_run():
    for name in ("content", "maintainers"):
        cond = " ".join(str(JOBS[name]["if"]).split())
        assert "needs.repo.result == 'success'" in cond, (name, cond)
        assert "needs.resolve.outputs.skip != 'true'" in cond, (name, cond)
        # A dry run creates no repo; neither job ever ran on one (dns is skipped then).
        assert "inputs.dry_run != true" in cond, (name, cond)


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
