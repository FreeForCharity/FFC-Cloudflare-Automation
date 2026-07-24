"""Unit tests for the 724 "Initialize Labels" github-script.

724 seeds/repairs the repo's label set from `.github/labels.yml` — the labels the
whole Agentic OS keys on (`agentic-os`, the domain/website request labels, the
dependency labels). Its embedded logic is an **upsert loop with a specific
404 branch and a per-label error swallow**:

  for each configured label:
    getLabel(name)                      # exists?
      -> updateLabel(...)               #   yes: refresh description/color
    catch (err)
      if err.status === 404 -> createLabel(...)   # no: create it
      else throw                        # real API error -> outer catch
  outer catch -> console.error("Failed to process label ...")  # log, continue

A silent drift here would quietly break label setup fleet-wide: dropping the
`=== 404` guard would make a transient error *create a duplicate* (or abort);
letting a per-label failure escape the outer catch would abort the whole run so
later labels never get seeded. These lock down every branch:

  - an existing label is UPDATED, never re-created;
  - a missing label (getLabel 404) is CREATED;
  - every configured label is processed (the loop covers the whole config);
  - a NON-404 getLabel error is logged and that label skipped, while the rest of
    the sweep still runs (the error does not abort);
  - a create/update failure is swallowed the same way (sweep continues, no crash);
  - the config is read from `.github/labels.yml`;
  - the workflow only writes labels — it never touches a deployment gate.

Refs #752 (process assurance — verify the Agentic OS's own processes keep
working), AGENTS.md §"Adding or changing a workflow" (embedded logic gets a unit
test).
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import find_step, load_workflow, step_github_script  # noqa: E402

WORKFLOW = "724-initialize-labels.yml"
JOB = "create-labels"
STEP = "Create labels from configuration"
HARNESS = pathlib.Path(__file__).resolve().parent / "harness" / "labels_api_shim.mjs"
NODE = shutil.which("node") or "node"

CTX = {"repo": {"owner": "FreeForCharity", "repo": "FFC-Cloudflare-Automation"}}


def _label(name):
    return {"name": name, "description": f"desc {name}", "color": "ededed"}


def _run(labels, *, existing=None, getlabel_error=None, write_fail=None):
    script = step_github_script(WORKFLOW, JOB, STEP)
    env = {"PATH": f"{pathlib.Path(NODE).parent}:/usr/bin:/bin:/usr/local/bin"}
    with tempfile.TemporaryDirectory() as td:
        tdp = pathlib.Path(td)
        (tdp / "script.js").write_text(script)
        (tdp / "context.json").write_text(json.dumps(CTX))
        (tdp / "labels.json").write_text(json.dumps(labels))
        env["TEST_SCRIPT_FILE"] = str(tdp / "script.js")
        env["TEST_CONTEXT_FILE"] = str(tdp / "context.json")
        env["TEST_LABELS_FILE"] = str(tdp / "labels.json")
        if existing is not None:
            (tdp / "existing.json").write_text(json.dumps(existing))
            env["TEST_EXISTING_LABELS_FILE"] = str(tdp / "existing.json")
        if getlabel_error is not None:
            env["TEST_GETLABEL_ERROR"] = json.dumps(getlabel_error)
        if write_fail is not None:
            env["TEST_WRITE_FAIL"] = write_fail
        proc = subprocess.run(
            [NODE, str(HARNESS)],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
    if proc.returncode != 0:
        raise AssertionError(f"harness crashed: {proc.stderr}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_existing_label_is_updated_not_created():
    r = _run([_label("agentic-os")], existing=["agentic-os"])
    assert r["threw"] is None, r
    assert r["updateCalls"] == ["agentic-os"], r
    assert r["createCalls"] == [], r


def test_missing_label_is_created_on_404():
    r = _run([_label("agentic-os")], existing=[])
    assert r["threw"] is None, r
    assert r["createCalls"] == ["agentic-os"], r
    assert r["updateCalls"] == [], r


def test_every_configured_label_is_processed():
    # Mixed: 'a' exists (update), 'b'/'c' missing (create). Loop must cover all.
    r = _run([_label("a"), _label("b"), _label("c")], existing=["a"])
    assert r["threw"] is None, r
    assert r["getLabelCalls"] == ["a", "b", "c"], r
    assert r["updateCalls"] == ["a"], r
    assert sorted(r["createCalls"]) == ["b", "c"], r


def test_non_404_getlabel_error_is_logged_and_sweep_continues():
    # 'a' hits a 500 on getLabel -> inner catch rethrows -> outer catch logs it,
    # NO create for 'a'; 'b' (404) is still created. The error must not abort.
    r = _run(
        [_label("a"), _label("b")],
        existing=[],
        getlabel_error={"name": "a", "status": 500},
    )
    assert r["threw"] is None, r  # a non-404 error must not crash the step
    assert r["createCalls"] == ["b"], r  # 'a' skipped, 'b' still created
    assert r["updateCalls"] == [], r
    assert any("Failed to process label a" in e for e in r["errors"]), r


def test_write_failure_is_swallowed_and_sweep_continues():
    # 'a' exists but its updateLabel throws -> outer catch logs, continues; 'b'
    # exists and updates cleanly. One label's write failure must not abort.
    r = _run([_label("a"), _label("b")], existing=["a", "b"], write_fail="a")
    assert r["threw"] is None, r
    assert r["updateCalls"] == ["b"], r  # 'a' failed; 'b' still updated
    assert any("Failed to process label a" in e for e in r["errors"]), r


def test_create_failure_is_swallowed_and_sweep_continues():
    # 404 -> create path, and the create itself throws for 'a'; 'b' still created.
    r = _run([_label("a"), _label("b")], existing=[], write_fail="a")
    assert r["threw"] is None, r
    assert r["createCalls"] == ["b"], r
    assert any("Failed to process label a" in e for e in r["errors"]), r


def test_config_is_read_from_labels_yml():
    r = _run([_label("agentic-os")], existing=["agentic-os"])
    assert r["threw"] is None, r
    assert any(p.endswith(".github/labels.yml") for p in r["fsReads"]), r


def test_empty_config_is_a_clean_noop():
    r = _run([], existing=[])
    assert r["threw"] is None, r
    assert r["getLabelCalls"] == [], r
    assert r["createCalls"] == [] and r["updateCalls"] == [], r


# --- YAML-level contract guards (no node needed) ---------------------------


def test_workflow_writes_labels_only_never_touches_a_gate():
    wf = load_workflow(WORKFLOW)
    perms = wf.get("permissions", {})
    assert perms.get("issues") == "write", perms
    # A label initializer runs no deployment and must never gate-approve.
    job = wf["jobs"][JOB]
    assert "environment" not in job, job
    script = step_github_script(WORKFLOW, JOB, STEP)
    assert "getLabel" in script and "createLabel" in script and "updateLabel" in script, script
    for forbidden in ("pending_deployments", "reviewCustomProtectionRule", "approve"):
        assert forbidden not in script, f"label init must not touch gate approvals: {forbidden}"


def test_config_path_matches_the_tested_literal():
    # The fs-read assertion above pins '.github/labels.yml'; keep the workflow's
    # literal in sync so the two can't drift apart.
    script = step_github_script(WORKFLOW, JOB, STEP)
    assert ".github/labels.yml" in script, script


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
