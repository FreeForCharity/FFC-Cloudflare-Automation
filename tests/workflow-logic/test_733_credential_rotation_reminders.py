"""Unit tests for the 733 quarterly credential-rotation-reminder github-script.

733 is a "seed one issue per credential family, once per quarter" monitor: on
its quarterly schedule it opens (or refreshes) exactly one rotation-reminder
issue per credential family, each stamped with a `<!-- rotation-<family>-<Q> -->`
marker so a re-run in the same quarter is idempotent. The whole value of the
pattern is that its create/dedupe branch never drifts — a lost or mis-computed
marker would re-open the same four issues every run (quarterly noise), an
inverted "exists" check would never open them at all, and a broken quarter label
would make the marker collide across quarters. These lock down each branch of
that machine:

  - no existing marker  -> create exactly one issue per family (marker, the
    `security` label, runbook + Key Vault refs, rotation-is-a-new-KV-version copy);
  - existing marker      -> skip that family (no duplicate issue this quarter);
  - all four present     -> a clean no-op re-run;
  - the exists lookup keys on the per-family+quarter marker, not any open issue;
  - a failed search API call falls through to "not found" (the script's own
    `.catch`) and still opens the reminders rather than silently skipping them;
  - the quarter label derives from the UTC date (frozen clock -> a known Q).

733 opens `security`-labelled issues but performs no rotation itself — the
rotations stay human/gated — so it is issues-write/contents-read only and must
never reach a deployment-gate approval surface.

Refs #752 (process assurance — verify the Agentic OS's own monitors keep
working), AGENTS.md section "Adding or changing a workflow" (embedded logic gets
a unit test). Follows the 734 (PR #806) and 732 (PR #811) monitor coverage;
#811's LESSON flagged 731/732/733 as the remaining untested github-script
monitors and noted this one can reuse `harness/issues_api_shim.mjs`.
"""

from __future__ import annotations

import datetime
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import find_step, load_workflow, step_github_script  # noqa: E402

WORKFLOW = "733-credential-rotation-reminders.yml"
JOB = "remind"
STEP = "Upsert quarterly rotation issues"
HARNESS = pathlib.Path(__file__).resolve().parent / "harness" / "issues_api_shim.mjs"
NODE = shutil.which("node") or "node"

# The four credential families the reminder covers, keyed as in the script.
FAMILY_KEYS = ["cloudflare", "whmcs", "google", "zeffy"]

# Freeze the clock to a fixed UTC instant so the quarter label is deterministic.
# 2026-07-15 is in Q3 (months 7-9 -> floor(6/3)+1 = 3).
NOW = datetime.datetime(2026, 7, 15, 12, 0, 0, tzinfo=datetime.timezone.utc)
NOW_MS = int(NOW.timestamp() * 1000)
QUARTER = f"{NOW.year}-Q{(NOW.month - 1) // 3 + 1}"  # -> "2026-Q3"


def _marker(key: str) -> str:
    return f"<!-- rotation-{key}-{QUARTER} -->"


def _run(*, existing_markers=None, search_throws=False):
    """Drive the reminder step with the clock frozen to NOW."""
    script = step_github_script(WORKFLOW, JOB, STEP)
    context = {
        "repo": {"owner": "FreeForCharity", "repo": "FFC-Cloudflare-Automation"},
        "payload": {},
    }
    env = {
        "PATH": f"{pathlib.Path(NODE).parent}:/usr/bin:/bin:/usr/local/bin",
        "TEST_NOW_MS": str(NOW_MS),
    }
    if search_throws:
        env["TEST_SEARCH_THROWS"] = "1"
    with tempfile.TemporaryDirectory() as td:
        tdp = pathlib.Path(td)
        (tdp / "script.js").write_text(script)
        (tdp / "context.json").write_text(json.dumps(context))
        (tdp / "existing.json").write_text(json.dumps(existing_markers or []))
        env["TEST_SCRIPT_FILE"] = str(tdp / "script.js")
        env["TEST_CONTEXT_FILE"] = str(tdp / "context.json")
        env["TEST_EXISTING_MARKERS_FILE"] = str(tdp / "existing.json")
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


# --- create / dedupe branch ------------------------------------------------


def test_no_existing_creates_one_issue_per_family():
    r = _run(existing_markers=[])
    assert r["threw"] is None, r
    assert len(r["created"]) == len(FAMILY_KEYS), r
    markers = {_marker(k) for k in FAMILY_KEYS}
    got = set()
    for issue in r["created"]:
        # each carries exactly one family marker, the security label, and the
        # quarter in its title
        assert issue["labels"] == ["security"], issue
        assert QUARTER in issue["title"], issue
        fam = next((m for m in markers if m in issue["body"]), None)
        assert fam is not None, issue
        got.add(fam)
    assert got == markers, got  # one per family, no duplicates


def test_existing_marker_is_skipped():
    # cloudflare already has its Q3 issue -> only the other three are opened.
    r = _run(existing_markers=[_marker("cloudflare")])
    assert r["threw"] is None, r
    assert len(r["created"]) == 3, r
    bodies = "\n".join(i["body"] for i in r["created"])
    assert _marker("cloudflare") not in bodies, r
    for k in ("whmcs", "google", "zeffy"):
        assert _marker(k) in bodies, k


def test_all_present_is_a_clean_noop():
    r = _run(existing_markers=[_marker(k) for k in FAMILY_KEYS])
    assert r["threw"] is None, r
    assert r["created"] == [], r  # idempotent re-run within the quarter


# --- dedupe keys on the marker, via a scoped search ------------------------


def test_search_query_is_scoped_to_repo_body_and_the_family_marker():
    r = _run(existing_markers=[])
    assert r["threw"] is None, r
    # one lookup per family, each quoting that family's marker in the body scope
    assert len(r["searchCalls"]) == len(FAMILY_KEYS), r
    queried = {c["q"] for c in r["searchCalls"]}
    for k in FAMILY_KEYS:
        match = next((q for q in queried if _marker(k) in q), None)
        assert match is not None, (k, queried)
        assert "repo:FreeForCharity/FFC-Cloudflare-Automation" in match, match
        assert "in:body" in match, match
    for c in r["searchCalls"]:
        assert c["per_page"] == 1, c


def test_search_failure_falls_through_to_create_not_skip():
    # A failed dedupe lookup must not silently skip the reminders: the script's
    # own `.catch(() => ({ data: { items: [] } }))` treats it as "not found".
    r = _run(existing_markers=[_marker(k) for k in FAMILY_KEYS], search_throws=True)
    assert r["threw"] is None, r
    assert len(r["created"]) == len(FAMILY_KEYS), r


# --- issue body content ----------------------------------------------------


def test_reminder_body_carries_runbook_kv_and_rotation_guidance():
    r = _run(existing_markers=[])
    cf = next(i for i in r["created"] if _marker("cloudflare") in i["body"])
    body = cf["body"]
    assert "Runbook:" in body, body
    assert "Key Vault" in body, body
    # rotation = add a new KV version, no GitHub change (the architectural rule)
    assert "new KV secret version" in body, body


# --- YAML-level contract guards (no node needed) ---------------------------


def test_family_and_marker_prefix_present_in_the_engine():
    # Assert on the *stable* marker prefix (`<!-- rotation-`) and each family
    # key, not the `${q}` variable name: a non-semantic rename (e.g. q ->
    # quarter) must not break this. A real change to the marker *format* is
    # already caught by the functional tests above, whose `_marker()` helper
    # would no longer match the created issue bodies.
    script = step_github_script(WORKFLOW, JOB, STEP)
    assert "<!-- rotation-" in script, "marker prefix drifted"
    for k in FAMILY_KEYS:
        assert f"'{k}'" in script or f'"{k}"' in script, f"family {k} missing"


def test_labels_are_security_only():
    # The label value is already validated functionally above (created issues
    # carry exactly ['security']); this guard keeps the shipped script honest and
    # tolerates either quote style so a non-semantic reformat doesn't trip it.
    script = step_github_script(WORKFLOW, JOB, STEP)
    assert re.search(
        r"""labels:\s*\[\s*['"]security['"]\s*\]""", script
    ), "rotation issues must be security-labelled"


def test_permissions_are_issue_write_contents_read_only():
    wf = load_workflow(WORKFLOW)
    perms = wf.get("permissions", {})
    assert perms.get("issues") == "write", perms
    assert perms.get("contents") == "read", perms
    # a reminder-only monitor needs nothing more
    assert "deployments" not in perms, perms
    assert "pull-requests" not in perms, perms


def test_triggers_are_the_quarterly_schedule_and_manual_dispatch():
    wf = load_workflow(WORKFLOW)
    # PyYAML parses the bare `on:` key as the boolean True.
    on = wf.get("on", wf.get(True, {}))
    assert "workflow_dispatch" in on, on
    crons = [s["cron"] for s in on["schedule"]]
    # 1st of Jan/Apr/Jul/Oct — the quarter boundaries
    assert any("1,4,7,10" in c for c in crons), crons


def test_step_never_touches_gate_approval_surfaces():
    script = step_github_script(WORKFLOW, JOB, STEP)
    for forbidden in ("pending_deployments", "reviewCustomProtectionRule", "approve"):
        assert forbidden not in script, f"reminder must not touch gates: {forbidden}"
    step = find_step(load_workflow(WORKFLOW), JOB, STEP)
    assert "github-script" in step.get("uses", ""), step


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
