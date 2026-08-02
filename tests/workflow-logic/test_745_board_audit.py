"""Unit tests for the 745 Agentic-OS-board-audit decision logic.

The workflow's github-script step `require`s scripts/board-audit-report-lib.js,
so exercising that module directly tests the shipped logic.

WHAT ACTUALLY NEEDS PINNING (#969)

The arithmetic is trivial — three sets, comment if any is non-empty. What is not
trivial, and what this module is mostly about, is that the audit has **three**
outcomes and its exit code distinguishes only **two**. `audit-agentic-os-board.py`
documents this in its own docstring: exit 1 means "found something" OR "any API,
auth or config error", because it must never exit 0 on an enumeration it could
not complete. So `exit != 0 ⇒ post the findings` is wrong in a way that is
invisible until the day the PAT dies, and then it manufactures a finding list out
of an auth failure — or, with the branches the other way round, reports a board it
never read as clean.

Hence the outcome triple, and hence the two CONTRADICTION cases: a zero exit whose
report carries findings, and a non-zero exit whose report carries none. Neither is
evidence of anything, and both are classified `incomplete`. That is the same
defect shape as #722's large-blob guard going green for the wrong reason on an
unexamined exit code, and as the fail-open collapse (#977, ledger L02) where
"could not check" became "checked, fine".

The two criteria #969 names explicitly are `test_a_clean_board_is_silent` and
`test_findings_are_reported` — and the issue is right that the first is the one
that regresses silently, because a workflow that comments unconditionally still
looks like it works.

Shape tests cover the wiring the library cannot see: the schedule, the ungated
environment (#834), the captured-not-propagated exit code that lets the report be
published before the job goes red, membership of 740's watch list, and — a live
regression given #848 — that the Key Vault secret is not the dead one #969's text
asked for.

`test_the_library_matches_the_script` is the drift guard: it drives the real
`audit()` out of scripts/audit-agentic-os-board.py, serializes the envelope the
workflow serializes, and feeds it to the JS. Nothing here touches the network.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import child_env, load_workflow, step_run

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
LIB = REPO_ROOT / "scripts" / "board-audit-report-lib.js"
AUDIT_SCRIPT = REPO_ROOT / "scripts" / "audit-agentic-os-board.py"
WF_FILE = "745-agentic-os-board-audit.yml"
WF_PATH = REPO_ROOT / ".github" / "workflows" / WF_FILE
WF_RAW = WF_PATH.read_text(encoding="utf-8")

# The secret #969 asked for, and the reason this module asserts on a string
# rather than trusting the reviewer to remember: it has been dead since
# 2026-07-29T22:26Z (#848), and a new daily cron wired to it is red on its first
# tick.
DEAD_SECRET = "read-all-cbm-github-pat"
LIVE_SECRET = "read-all-cbm-ffc-copilot-mcp-github-pat"


def _node(expr_body: str, *argv: str):
    code = f"const l=require({json.dumps(str(LIB))});{expr_body}"
    proc = subprocess.run(
        ["node", "-e", code, *argv], capture_output=True, text=True, encoding="utf-8", timeout=60
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed: {proc.stderr}")
    return json.loads(proc.stdout)


def classify(exit_code, stdout, stderr: str = "") -> dict:
    return _node(
        "const a=JSON.parse(process.argv[1]);"
        "process.stdout.write(JSON.stringify(l.classify(a)));",
        json.dumps({"exitCode": exit_code, "stdout": stdout, "stderr": stderr}),
    )


def should_comment(classification: dict) -> bool:
    return _node(
        "process.stdout.write(JSON.stringify(l.shouldComment(JSON.parse(process.argv[1]))));",
        json.dumps(classification),
    )


def should_fail(classification: dict) -> bool:
    return _node(
        "process.stdout.write(JSON.stringify(l.shouldFail(JSON.parse(process.argv[1]))));",
        json.dumps(classification),
    )


def render(classification: dict, run_url: str = "https://example.invalid/run/1"):
    return _node(
        "process.stdout.write(JSON.stringify("
        "l.renderBody(JSON.parse(process.argv[1]),{runUrl:process.argv[2]})));",
        json.dumps(classification),
        run_url,
    )


def summary(classification: dict) -> str:
    return _node(
        "process.stdout.write(JSON.stringify(l.summary(JSON.parse(process.argv[1]))));",
        json.dumps(classification),
    )


def const(name: str):
    return _node(f"process.stdout.write(JSON.stringify(l.{name}));")


def _report(missing=None, statusless=None, closed=None, **extra) -> dict:
    payload = {
        "org": "FreeForCharity",
        "project": 9,
        "board_title": "Agentic OS",
        "repos_swept": ["FreeForCharity/FFC-Cloudflare-Automation"],
        "expected_count": 53,
        "board_count": 208,
        "missing_from_board": missing or [],
        "statusless": statusless or [],
        "closed_not_done": closed or [],
    }
    payload.update(extra)
    return payload


def _item(number: int, repo: str = "FreeForCharity/FFC-Cloudflare-Automation", **extra) -> dict:
    row = {
        "repo": repo,
        "number": number,
        "title": f"item {number}",
        "url": f"https://github.com/{repo}/issues/{number}",
        "status": None,
    }
    row.update(extra)
    return row


def _clean_run():
    return classify("0", json.dumps(_report()))


def _findings_run():
    return classify("1", json.dumps(_report(missing=[_item(969)])))


# --------------------------------------------------------------------------
# The two criteria #969 names
# --------------------------------------------------------------------------


def test_a_clean_board_is_silent():
    # The one that regresses silently: a workflow that comments unconditionally
    # still looks like it works, and a daily "board is clean" comment on #719 is
    # how a thread stops being read.
    c = _clean_run()
    assert c["outcome"] == "clean", c
    assert should_comment(c) is False
    assert should_fail(c) is False
    assert render(c) is None, "a clean run must render no body at all"


def test_findings_are_reported():
    c = _findings_run()
    assert c["outcome"] == "findings", c
    assert c["count"] == 1
    assert should_comment(c) is True
    assert should_fail(c) is True, "#969: let a real finding fail the job"
    body = render(c)
    assert "745 Agentic OS board audit" in body
    assert "1 finding(s)" in body
    assert "FFC-Cloudflare-Automation#969" in body


# --------------------------------------------------------------------------
# The third outcome: the run that established nothing
# --------------------------------------------------------------------------


def test_an_unreadable_report_is_incomplete_and_never_clean():
    # The audit script exits 1 for an auth or enumeration failure and prints no
    # JSON. Classifying that as `findings` invents a worklist; classifying it as
    # `clean` is the silence-read-as-green shape (#966).
    for stdout in ("", "   ", "error: set GH_TOKEN", "{", "[]", '"a string"'):
        c = classify("1", stdout)
        assert c["outcome"] == "incomplete", (stdout, c)
        assert should_comment(c) is True
        assert should_fail(c) is True
        body = render(c)
        assert "INCOMPLETE" in body
        assert "no verdict" in body


def test_a_zero_exit_with_no_readable_report_is_still_incomplete():
    # A success we cannot corroborate is not a success. The script always prints
    # a JSON document on its zero path, so an empty stdout here means something
    # else ran — or nothing did.
    c = classify("0", "")
    assert c["outcome"] == "incomplete", c
    assert should_comment(c) is True


def test_a_missing_section_is_not_an_empty_section():
    # Treating an absent key as [] would let a truncated or half-written
    # document read as a clean board.
    for key in ("missing_from_board", "statusless", "closed_not_done"):
        payload = _report()
        del payload[key]
        c = classify("0", json.dumps(payload))
        assert c["outcome"] == "incomplete", (key, c)
        assert key in (c["parseError"] or ""), c


def test_a_non_array_section_is_incomplete():
    c = classify("0", json.dumps(_report(statusless="three")))
    assert c["outcome"] == "incomplete", c


# --------------------------------------------------------------------------
# The contradictions — the tool disagreeing with itself
# --------------------------------------------------------------------------


def test_exit_zero_with_findings_is_a_contradiction_not_a_clean_board():
    c = classify("0", json.dumps(_report(statusless=[_item(1, type="DraftIssue")])))
    assert c["outcome"] == "incomplete", c
    assert "contradicts its own exit code" in c["reason"]
    assert should_fail(c) is True


def test_exit_nonzero_with_no_findings_is_a_contradiction():
    c = classify("1", json.dumps(_report()))
    assert c["outcome"] == "incomplete", c
    assert "contradicts its own exit code" in c["reason"]


# --------------------------------------------------------------------------
# The exit code arrives as a STRING from GITHUB_OUTPUT
# --------------------------------------------------------------------------


def test_the_exit_code_is_read_as_a_string_because_that_is_what_actions_hands_over():
    # steps.<id>.outputs.* is always a string. A `=== 0` comparison against '0'
    # would classify every clean run as incomplete and comment daily; a
    # `Number(x) || fallback` would read '' as 0 and every broken run as clean.
    assert classify("0", json.dumps(_report()))["outcome"] == "clean"
    assert classify(0, json.dumps(_report()))["outcome"] == "clean"
    assert classify("1", json.dumps(_report(missing=[_item(9)])))["outcome"] == "findings"
    assert classify(" 1 ", json.dumps(_report(missing=[_item(9)])))["outcome"] == "findings"


def test_an_unusable_exit_code_is_incomplete_and_never_success():
    # An absent output means the step that should have reported it did not run
    # as expected. `Number('')` is 0, so the empty string is the dangerous one.
    for bad in ("", None, "abc", "0.5", [], {}):
        c = classify(bad, json.dumps(_report()))
        assert c["outcome"] == "incomplete", (bad, c)
        assert "exit code" in (c["reason"] or ""), c


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def test_the_report_prints_both_denominators():
    # Run 62 reported "0 missing" off an expected set that was one item short,
    # and a short denominator was the only thing a reader could have found
    # suspicious (#966). The script prints them; so must the comment.
    body = render(_findings_run())
    assert "expected=53" in body
    assert "board=208" in body


def test_the_three_card_kinds_are_labelled_differently():
    body = render(
        classify(
            "1",
            json.dumps(
                _report(
                    statusless=[
                        _item(5),
                        {"repo": None, "number": None, "type": "DraftIssue", "title": "a draft"},
                        {"repo": None, "number": None, "type": None, "title": ""},
                    ]
                )
            ),
        )
    )
    assert "(draft card)" in body
    assert "no readable content" in body
    assert "FFC-Cloudflare-Automation#5" in body


def test_a_long_finding_list_is_truncated_and_says_so():
    cap = const("MAX_ROWS_PER_SECTION")
    rows = [_item(n) for n in range(1, cap + 6)]
    body = render(classify("1", json.dumps(_report(missing=rows))))
    assert f"…and {len(rows) - cap} more" in body
    assert body.count("\n- [FreeForCharity") <= cap + 1


def test_every_report_names_the_workflow_and_says_it_writes_nothing():
    for c in (_findings_run(), classify("1", "")):
        body = render(c)
        assert "745. Repo - Agentic OS Board Audit" in body
        assert "Refs #969" in body
        # The read-only promise travels with the report: whoever reads it must
        # not go looking for the card this workflow supposedly moved.
        assert "no card" in body and "sets no Status" in body


def test_the_incomplete_report_names_the_dead_pat_as_a_likely_cause():
    # Whoever reads this at 08:00 should not have to rediscover #848.
    body = render(classify("1", "error: 401"))
    assert "#848" in body
    assert DEAD_SECRET in body


def test_the_run_url_is_included_when_known_and_omitted_when_not():
    assert "https://example.invalid/run/1" in render(_findings_run())
    body = _node(
        "process.stdout.write(JSON.stringify(l.renderBody(JSON.parse(process.argv[1]),{})));",
        json.dumps(_findings_run()),
    )
    assert "Run: " not in body


def test_summary_survives_every_shape_it_can_be_handed():
    for c in (_clean_run(), _findings_run(), classify("1", ""), classify(None, "")):
        line = summary(c)
        assert line.startswith("board audit: ")


# --------------------------------------------------------------------------
# Drift guard: the library and the script must agree on the envelope
# --------------------------------------------------------------------------


def _load_audit_script():
    spec = importlib.util.spec_from_file_location("audit_agentic_os_board", AUDIT_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # stdlib-only; no I/O at import time
    return mod


def test_the_library_matches_the_script():
    # Drives the REAL audit() and serializes the envelope the workflow's --json
    # path serializes, so a rename in either file fails here rather than in
    # production at 07:47 UTC.
    mod = _load_audit_script()

    empty = mod.audit({}, [])
    assert mod.has_findings(empty) is False
    envelope = {"org": "FreeForCharity", "project": 9, "board_title": "x", "repos_swept": [], **empty}
    assert classify("0", json.dumps(envelope))["outcome"] == "clean"

    expected = {("FreeForCharity/FFC-Cloudflare-Automation", 969): {
        "repo": "FreeForCharity/FFC-Cloudflare-Automation",
        "number": 969,
        "title": "Schedule the Agentic OS board audit",
        "url": "https://github.com/FreeForCharity/FFC-Cloudflare-Automation/issues/969",
        "is_pr": False,
    }}
    dirty = mod.audit(expected, [])
    assert mod.has_findings(dirty) is True
    envelope = {"org": "FreeForCharity", "project": 9, "board_title": "x", "repos_swept": [], **dirty}
    c = classify("1", json.dumps(envelope))
    assert c["outcome"] == "findings", c
    assert "FFC-Cloudflare-Automation#969" in render(c)


def test_the_section_keys_are_exactly_the_scripts_sets():
    mod = _load_audit_script()
    script_keys = {k for k, v in mod.audit({}, []).items() if isinstance(v, list)}
    lib_keys = {s["key"] for s in const("SECTIONS")}
    assert lib_keys == script_keys, (lib_keys, script_keys)


# --------------------------------------------------------------------------
# Shape: the wiring the library cannot see
# --------------------------------------------------------------------------


def _wf():
    return load_workflow(WF_FILE)


def _job():
    return _wf()["jobs"]["audit"]


def test_it_is_scheduled_and_dispatchable():
    on = _wf()[True] if True in _wf() else _wf()["on"]
    assert "schedule" in on, "a script nothing invokes is the whole point of #969"
    assert "workflow_dispatch" in on, "dispatch is what makes the audit provable on demand"


def test_the_environment_is_ungated():
    # #834: a daily read-only audit on a reviewer-gated environment parks at a
    # gate nobody is watching and is reaped by 734.
    assert _job()["environment"] == "github-prod-read"


def test_the_sweep_is_not_cancel_in_progress():
    assert _wf()["concurrency"]["cancel-in-progress"] is False


def test_it_does_not_load_the_dead_pat():
    # #969 asked for read-all-cbm-github-pat. It has been 401 since
    # 2026-07-29 (#848) and 502's deliver has failed daily on it ever since.
    assert LIVE_SECRET in WF_RAW
    assert f"--name {DEAD_SECRET}" not in WF_RAW


def test_the_audit_step_reads_structured_output():
    step = next(s for s in _job()["steps"] if s.get("id") == "audit")
    assert "exit-code=" in step["run"], "the exit code must reach GITHUB_OUTPUT"
    assert "--json" in step["run"], "the classifier parses structured output, not prose"


def _run_audit_step(python_exit: int, stdout: str = "", stderr: str = ""):
    """Execute the REAL audit step under the runner's exact shell.

    Returns (returncode, exit_code_output, stdout+stderr).

    Run 1 of this workflow failed here and the previous version of this test
    could not have caught it. That test asserted `"set -e" not in step["run"]`,
    which was true and irrelevant: the `-e` is not in the script body, it is in
    the shell the runner INVOKES the body with —

        shell: /usr/bin/bash --noprofile --norc -e -o pipefail {0}

    and `set -uo pipefail` does not clear an inherited `-e`. So a textual check
    over the body was reading the one place the flag could never appear. The
    only way to know is to run the thing the way the runner runs it, which is
    why this drives `bash -e -o pipefail` explicitly rather than `bash -c`.
    """
    script = step_run(WF_FILE, "audit", "Run the board audit")
    # `mkdtemp` + an ignore_errors teardown rather than TemporaryDirectory().
    # The child's cwd is INSIDE this directory — it has to be, because the step
    # writes board-audit.json/.err relative to cwd and pointing that at
    # REPO_ROOT would litter the tree on every suite run (#945). On Windows a
    # process's working directory is an open handle on that directory, so the
    # context manager's rmtree races the just-exited bash and raises
    # `PermissionError: [WinError 32]` — reported from the Conductor's host,
    # where it failed the whole module. This is the only helper in the suite
    # that sets cwd into its temp dir (test_729 and friends run the child in
    # REPO_ROOT), which is why nothing else hits it. A cleanup race must never
    # decide whether the assertions passed.
    td = pathlib.Path(tempfile.mkdtemp(prefix="wf745-"))
    try:
        # A stub python3 ahead of the real one: the point is the exit code, and
        # the step must never reach the network in a unit test.
        stub = td / "bin"
        stub.mkdir()
        (stub / "python3").write_text(
            "#!/usr/bin/env bash\n"
            f"printf '%s' {json.dumps(stdout)}\n"
            f"printf '%s' {json.dumps(stderr)} >&2\n"
            f"exit {python_exit}\n",
            encoding="utf-8",
        )
        (stub / "python3").chmod(0o755)
        out_file = td / "step_output"
        out_file.touch()
        env = child_env(stub, GITHUB_OUTPUT=str(out_file), GH_TOKEN="stub")
        proc = subprocess.run(
            # Exactly the runner's shell, `-e` included. Using plain `bash -c`
            # here would silently make every case pass.
            ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", script],
            env=env,
            cwd=str(td),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        return proc.returncode, out_file.read_text(encoding="utf-8"), proc.stdout + proc.stderr
    finally:
        # Attempt a STRICT removal and report what stops it, rather than
        # `ignore_errors=True`. Both forms keep a teardown race from deciding
        # whether the assertions passed, but the blanket one is unconditional
        # and silent on every platform — so a genuine leak on Linux, where this
        # race has no known cause, would look exactly like the expected Windows
        # case and accumulate directories with nothing to say so. L72 is the
        # same shape: a fail-open is a sound trade, and being silent about it
        # is the defect.
        #
        # Deliberately NOT re-raising on non-Windows, tempting as it is: this
        # runs in a `finally`, so an exception raised here would replace an
        # in-flight assertion failure with a teardown error and hide the thing
        # the test was actually for.
        try:
            shutil.rmtree(td)
        except OSError as exc:
            print(f"  NOTE: temp dir not removed on {sys.platform}: {td}: {exc}")


def test_a_findings_exit_does_not_kill_the_step_before_it_can_report():
    # THE run-1 REGRESSION. A board with findings makes the audit exit 1, which
    # is the normal case; if that ends the step, the report step is skipped and
    # the findings never reach #719 — the workflow's entire purpose, undone by
    # its own success condition.
    rc, output, log = _run_audit_step(1, stdout='{"missing_from_board": []}')
    assert rc == 0, f"the step must survive a non-zero audit exit\n{log}"
    assert "exit-code=1" in output, f"the real exit code must reach GITHUB_OUTPUT\n{output}"


def test_a_clean_exit_is_reported_as_zero():
    rc, output, log = _run_audit_step(0, stdout='{"missing_from_board": []}')
    assert rc == 0, log
    assert "exit-code=0" in output, output


def test_an_auth_failure_exit_also_survives_to_be_classified():
    # The dead-PAT path: no stdout at all, non-zero exit. It must reach the
    # classifier as `incomplete` rather than killing the step.
    rc, output, log = _run_audit_step(1, stdout="", stderr="error: 401")
    assert rc == 0, log
    assert "exit-code=1" in output, output


def test_the_conductor_log_comment_uses_the_ambient_token():
    # An own-repo issue write belongs on GITHUB_TOKEN, never on a Key Vault
    # read-scoped PAT — 726 lost a whole run to that (#834).
    step = next(s for s in _job()["steps"] if "github-script" in str(s.get("uses", "")))
    assert "BOARD_AUDIT_TOKEN" not in json.dumps(step), (
        "the reporting step must not carry the Key Vault PAT"
    )
    assert "github-token" not in step.get("with", {}), "default ambient token is correct here"


def test_the_job_can_never_advance_a_deployment_gate():
    perms = _wf()["permissions"]
    assert perms.get("contents") == "read"
    assert perms.get("issues") == "write"
    assert "deployments" not in perms
    assert "actions" not in perms


def test_the_workflow_is_watched_by_740():
    alerter = (
        REPO_ROOT / ".github" / "workflows" / "740-scheduled-workflow-failure-alert.yml"
    ).read_text(encoding="utf-8")
    assert _wf()["name"].strip("'\"") in alerter, (
        "a scheduled monitor that is not itself monitored reproduces #848 one level up"
    )


def test_the_workflow_has_a_safety_table_row():
    doc = (REPO_ROOT / "docs" / "workflow-safety-and-approvals.md").read_text(encoding="utf-8")
    assert "| 745 " in doc, "add a row to docs/workflow-safety-and-approvals.md"


def test_the_safety_row_names_the_environment_the_job_actually_declares():
    # The row shipped with `—` in the Approval env column, copied from 743/744 —
    # which are correct for THEM, because they declare no environment at all
    # (744 deliberately holds no Key Vault credential so it cannot go dark with
    # the feed it watches). 745 does declare one, and the read-lane precedent
    # 726/735 both name it. Caught by review, not by any guard.
    #
    # It matters past tidiness because the column is the SOURCE the catalog
    # generator reads: `parse_safety_table()` sets `approvalEnv` from this cell
    # and nothing cross-checks it against the YAML, so one wrong cell propagates
    # silently into docs/workflow-catalog.json and the workflows README — three
    # files disagreeing with the workflow, from one edit.
    doc = (REPO_ROOT / "docs" / "workflow-safety-and-approvals.md").read_text(encoding="utf-8")
    row = next(ln for ln in doc.split("\n") if ln.startswith("| 745 "))
    approval_env = row.split("|")[4].strip()
    assert approval_env == _job()["environment"], (
        f"safety-table Approval env is {approval_env!r} but the job declares "
        f"{_job()['environment']!r}"
    )


def test_the_catalog_agrees_with_the_workflow():
    # The generated end of the same contract: whatever the doc says has to
    # survive regeneration into the catalog, which is what tooling reads.
    catalog = json.loads((REPO_ROOT / "docs" / "workflow-catalog.json").read_text(encoding="utf-8"))
    rows = catalog["workflows"] if isinstance(catalog, dict) else catalog
    entry = next(w for w in rows if str(w.get("number")) == "745")
    assert entry["approvalEnv"] == _job()["environment"], entry
    assert entry["environments"] == [_job()["environment"]], entry


def test_the_generated_readme_row_agrees_too():
    # One generator run writes BOTH docs/workflow-catalog.json and the README
    # catalog section, so the two can disagree only if someone regenerated and
    # then reverted a subset. That is not hypothetical: restoring after a
    # mutation test with hand-listed file copies put the catalog back and left
    # the README carrying the mutated value, which reached CI as
    # `Catalog out of date; regenerate` naming the README alone. Asserting the
    # third file here makes the trio fail together in one module rather than
    # only in the repo-wide `--check`.
    readme = (REPO_ROOT / ".github" / "workflows" / "README.md").read_text(encoding="utf-8")
    row = next(ln for ln in readme.split("\n") if ln.startswith("| 745 "))
    assert f"| {_job()['environment']} |" in row, row


def test_the_header_never_wraps_mid_hyphenated_word():
    # The catalog generator joins header comment lines with a space, so a word
    # split across the wrap ships to the public catalog as `read- only` (#840).
    offenders = [
        ln for ln in WF_RAW.splitlines() if ln.startswith("#") and ln.rstrip().endswith("-")
    ]
    assert not offenders, offenders


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
