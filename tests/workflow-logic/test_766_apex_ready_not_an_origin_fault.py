"""Tests for the cutover preflight's apex-ready blocker (#766).

`scripts/preflight-cutover.mjs` is the read-only go/no-go gate in front of every
staging->apex cutover, and until this file existed NOTHING under
tests/workflow-logic referenced it. Its 932 lines carry an internal `--self-test`
battery over the pure functions; nothing in CI ran it and nothing guarded the
imperative call sites that feed those functions. This file closes both halves.

THE REGRESSION BEING PINNED
    The apex-ready check (#766) reads the exported HTML and fails the domain when
    it still carries root-relative `/FFC-EX-<domain>/` href/src refs, which would
    404 every asset once the site answers at the apex. That check was correct.
    What it did with the result was not: it assigned

        if (bp.mismatch) originHealthy = false;

    and `originHealthy` is two things at once -- the short-circuit at the top of
    `computeVerdict` (returning "NOT READY -- Pages origin unhealthy") and the
    value rendered in the verdict table's "Pages origin" column.

    Measured on run 37244478280 (tamkeensports.org, 2026-10-04), the operator was
    handed a report that said, four lines apart:

        [ok] Pages origin healthy (https://freeforcharity.github.io/FFC-EX-tamkeensports.org/) -- HTTP 200
        Verdict: NOT READY -- Pages origin unhealthy

    ...with the table column reading UNHEALTHY. Both statements came from the same
    run about the same origin, and the origin was fine: the artifact was not. The
    fix is to leave `originHealthy` alone, because `record(false, ...)` already
    counts the artifact failure in `blockerCount` -- so the domain is still a
    no-go, now labelled "NOT READY -- N blocker(s)", which points at the artifact
    instead of at a Pages deployment nobody needs to touch.

    This is the ledger's "name where the fix actually is" property applied to a
    verdict line: a no-go that blames the wrong subsystem costs an operator the
    same hour as a wrong answer.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "preflight-cutover.mjs"


def _source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_the_script_exists():
    assert SCRIPT.is_file(), f"{SCRIPT} is missing -- every cutover preflights through it"


def test_the_scripts_own_self_test_passes():
    """Behavioural: the pure classification + verdict battery, offline.

    Asserts on stdout as well as the exit code. A check that asserts only a
    status cannot tell a passing suite from one that never ran.
    """
    proc = subprocess.run(
        ["node", str(SCRIPT), "--self-test"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=REPO_ROOT,
    )
    combined = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0, f"self-test exited {proc.returncode}\n{combined}"
    assert "self-test OK" in combined, combined


def test_the_apex_ready_blocker_does_not_mark_the_origin_unhealthy():
    """Structural: the #766 result must not be folded into `originHealthy`.

    This is the assertion that fails on the shipped version. It is deliberately
    structural: the alternative is an end-to-end run with a mocked origin
    serving basePath-dirty HTML, and the gap this closes is one line of
    assignment, not a behaviour that needs a live fixture to observe.
    """
    src = _source()
    assert "originHealthy = false" not in src.replace(
        "originHealthy: false", ""
    ), (
        "the apex-ready (or any other) blocker is being folded into "
        "`originHealthy`, which makes computeVerdict blame the Pages origin and "
        "makes the verdict table's 'Pages origin' column read UNHEALTHY for an "
        "origin that answered 200 -- see this module's docstring, run 37244478280"
    )


def test_the_apex_ready_check_itself_is_still_wired():
    """The guard above must not be satisfiable by deleting the check."""
    src = _source()
    assert "basePathMismatch(" in src, "the apex-ready comparison is gone"
    assert re.search(
        r"record\(\s*!bp\.mismatch", src
    ), "the apex-ready result is no longer recorded, so it no longer counts as a blocker"


def test_a_blocker_driven_verdict_names_blockers_not_the_origin():
    """Behavioural: drive the exported `computeVerdict` across both phases.

    The module guards its own entrypoint, so importing it runs no probes.
    """
    program = (
        f"import {{ computeVerdict }} from {SCRIPT.as_uri()!r};"
        "for (const pointedAtPages of [false, true]) {"
        "  const v = computeVerdict({ originHealthy: true, pointedAtPages, blockerCount: 1 });"
        "  console.log(JSON.stringify({ pointedAtPages, ok: v.ok, label: v.label }));"
        "}"
    ).replace("'", '"')
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", program],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=REPO_ROOT,
    )
    assert proc.returncode == 0, (proc.stdout or "") + (proc.stderr or "")
    lines = [line for line in proc.stdout.splitlines() if line.strip().startswith("{")]
    assert len(lines) == 2, f"expected both phases, got: {proc.stdout!r}"
    for line in lines:
        assert '"ok":false' in line.replace(" ", ""), f"a blocker must stay a no-go: {line}"
        assert "blocker" in line, f"the label must name the blockers: {line}"
        assert not re.search(r"origin", line, re.I), (
            f"a healthy origin must not be blamed for a blocker: {line}"
        )


# Keep this roster BELOW every test: the comprehension reads `globals()`, so a
# test defined after this line is never collected (run_all.py guards for it).
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
