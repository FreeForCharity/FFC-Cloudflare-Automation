"""The script self-tests 706 gates on must also run in Validate Repository.

706's `resolve` job runs `node scripts/<name>.mjs --self-test` for fifteen
scripts and gates every later job on the result. Until 2026-09-28 that was the
ONLY place those self-tests ran: `722-ci.yml` never invoked them. A broken
self-test therefore merged green and surfaced at the next 706 dispatch -- as a
red migration rather than as a red PR, and at the moment it costs most.

#1411 was the live case. It added a `shrinkPdf` assertion that shells out to
ghostscript and asserts `ok === true`. Ghostscript is on most developer hosts
and in the agent sandbox, so it passed locally and in review. 706's `resolve`
job installs nothing -- ghostscript arrives in `convert`, the job `resolve`
gates -- so run 36417508156 died in `resolve` with two FAILs, blocking the
newheightseducation.org migration the PR existed to unblock.

Two guards here, and the second is the one that keeps the fix alive:

  * the 722 step exists and actually runs `--self-test`;
  * its script list is EXACTLY 706's. Without this, the next script added to
    706's gate is simply absent from 722 and that script re-opens the hole,
    silently, for itself. A drift check is cheap; noticing the drift by
    dispatching a migration is not.

Deliberately NOT asserted: that 722 installs sharp or ghostscript. A self-test
needing host tooling its gate does not have is the defect being caught. Install
the tooling in 722 and the two lists agree by accident -- 722 goes green on a
test 706 will still fail.
"""

from __future__ import annotations

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import find_step, load_workflow  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"

SELF_TEST = re.compile(r"scripts/([A-Za-z0-9._-]+)\.mjs[\"']?\s+--self-test")
# Matches the `for s in a b c; do ... node "scripts/$s.mjs" --self-test` form,
# where the names live in the loop header rather than beside the invocation.
LOOP_HEADER = re.compile(r"for\s+s\s+in\s+(.*?);\s*do", re.DOTALL)


def _names_from_706() -> set[str]:
    step = find_step(
        load_workflow("706-website-wordpress-to-pages.yml"), "resolve", "Offline self-tests"
    )
    return set(SELF_TEST.findall(step["run"]))


def _names_from_722() -> set[str]:
    step = find_step(load_workflow("722-ci.yml"), "validate", "Script self-tests")
    body = step["run"]
    names = set(SELF_TEST.findall(body))
    # Drop the loop variable itself: `scripts/$s.mjs` is not a script name.
    names.discard("$s")
    m = LOOP_HEADER.search(body)
    if m:
        names |= {w for w in m.group(1).replace("\\\n", " ").split() if w and w != "\\"}
    return names


def test_722_has_a_script_self_test_step():
    step = find_step(load_workflow("722-ci.yml"), "validate", "Script self-tests")
    assert "--self-test" in step["run"], "the step exists but runs no self-test"


def test_722_runs_exactly_the_scripts_706_gates_on():
    a, b = _names_from_706(), _names_from_722()
    assert a, "parsed no script names out of 706's gate -- the parser is wrong, not the workflow"
    assert a == b, (
        f"706 gates on scripts 722 does not run: {sorted(a - b)}; "
        f"722 runs scripts 706 does not gate on: {sorted(b - a)}"
    )


def test_every_named_script_exists_and_supports_self_test():
    # A name that no longer resolves would make both lists agree on a script
    # that cannot run, and the loop's `set -euo pipefail` would fail the whole
    # step with a bare "module not found".
    for name in sorted(_names_from_706()):
        path = SCRIPTS / f"{name}.mjs"
        assert path.is_file(), f"706 gates on {path.relative_to(REPO_ROOT)}, which does not exist"
        assert "--self-test" in path.read_text(encoding="utf-8"), (
            f"{name}.mjs is in the gate but never reads --self-test, "
            "so the gate runs it as a no-op"
        )


def test_722_does_not_install_the_tooling_that_would_mask_the_defect():
    # See the module docstring: installing ghostscript or sharp here makes 722
    # agree with 706 only by accident, and re-opens the exact hole #1411 fell
    # through.
    step = find_step(load_workflow("722-ci.yml"), "validate", "Script self-tests")
    body = step["run"].lower()
    for forbidden in ("ghostscript", "apt-get", "npm install sharp", "pnpm add sharp"):
        assert forbidden not in body, (
            f"722's self-test step installs {forbidden!r}; it must run on the same bare host "
            "706's resolve job does, or it cannot catch a host-dependent assertion"
        )


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]


if __name__ == "__main__":
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {e}")
    sys.exit(1 if failures else 0)
