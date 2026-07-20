"""Unit tests for the 738 fleet-smoke engine-drift decision logic.

The workflow's github-script step `require`s scripts/fleet-smoke-drift-lib.js, so
exercising that module directly tests the shipped logic: which repos count as
drift (a deployed copy whose hash differs from canonical) versus the benign
buckets (not-yet-onboarded `missing`, `unreadable` fetch failures) that must NOT
raise the rolling issue. A shape test guards the workflow wiring itself.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import load_workflow

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
LIB = REPO_ROOT / "scripts" / "fleet-smoke-drift-lib.js"
WF_FILE = "738-fleet-smoke-engine-drift-audit.yml"


def _node(expr_body: str, *argv: str) -> object:
    code = f"const l=require({json.dumps(str(LIB))});{expr_body}"
    proc = subprocess.run(
        ["node", "-e", code, *argv],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed: {proc.stderr}")
    return json.loads(proc.stdout)


def analyze(canonical: str, entries: list) -> dict:
    return _node(
        "process.stdout.write(JSON.stringify("
        "l.analyze(process.argv[1], JSON.parse(process.argv[2]))));",
        canonical,
        json.dumps(entries),
    )


def render(analysis_canonical_ts: dict) -> str:
    return _node(
        "const a=JSON.parse(process.argv[1]);"
        "process.stdout.write(JSON.stringify("
        "l.renderBody(a.analysis, a.canonical, a.ts)));",
        json.dumps(analysis_canonical_ts),
    )


# --- classification --------------------------------------------------------

CANON = "a" * 64


def test_uniform_fleet_is_no_drift():
    r = analyze(CANON, [
        {"repo": "FreeForCharity/FFC-EX-one.org", "hash": CANON},
        {"repo": "FreeForCharity/FFC-EX-two.org", "hash": CANON},
    ])
    assert r["hasDrift"] is False, r
    assert sorted(r["matching"]) == [
        "FreeForCharity/FFC-EX-one.org",
        "FreeForCharity/FFC-EX-two.org",
    ], r
    assert r["divergent"] == [] and r["missing"] == [] and r["unreadable"] == [], r


def test_differing_hash_is_drift():
    r = analyze(CANON, [
        {"repo": "FreeForCharity/FFC-EX-good.org", "hash": CANON},
        {"repo": "FreeForCharity/FFC-EX-bad.org", "hash": "b" * 64},
    ])
    assert r["hasDrift"] is True, r
    assert [d["repo"] for d in r["divergent"]] == ["FreeForCharity/FFC-EX-bad.org"], r
    assert r["divergent"][0]["hash"] == "b" * 64, r


def test_absent_file_is_missing_not_drift():
    # Incremental rollout: a repo without the engine yet must not raise the issue.
    r = analyze(CANON, [
        {"repo": "FreeForCharity/FFC-EX-new.org", "hash": None},
        {"repo": "FreeForCharity/FFC-EX-old.org"},  # hash key absent entirely
    ])
    assert r["hasDrift"] is False, r
    assert sorted(r["missing"]) == [
        "FreeForCharity/FFC-EX-new.org",
        "FreeForCharity/FFC-EX-old.org",
    ], r


def test_fetch_error_is_unreadable_not_drift():
    r = analyze(CANON, [
        {"repo": "FreeForCharity/FFC-EX-priv.org", "error": "HTTP 403 rate limited"},
    ])
    assert r["hasDrift"] is False, r
    assert r["unreadable"] == [
        {"repo": "FreeForCharity/FFC-EX-priv.org", "error": "HTTP 403 rate limited"}
    ], r


def test_mixed_fleet_only_divergent_triggers():
    r = analyze(CANON, [
        {"repo": "FreeForCharity/FFC-EX-a.org", "hash": CANON},
        {"repo": "FreeForCharity/FFC-EX-b.org", "hash": "c" * 64},
        {"repo": "FreeForCharity/FFC-EX-c.org", "hash": None},
        {"repo": "FreeForCharity/FFC-EX-d.org", "error": "boom"},
    ])
    assert r["hasDrift"] is True, r
    assert len(r["divergent"]) == 1 and len(r["matching"]) == 1
    assert len(r["missing"]) == 1 and len(r["unreadable"]) == 1


def test_empty_entries_is_no_drift():
    r = analyze(CANON, [])
    assert r["hasDrift"] is False, r
    assert r["matching"] == [] and r["divergent"] == [], r


# --- report rendering ------------------------------------------------------

def test_render_contains_marker_and_divergent_row():
    analysis = analyze(CANON, [
        {"repo": "FreeForCharity/FFC-EX-good.org", "hash": CANON},
        {"repo": "FreeForCharity/FFC-EX-bad.org", "hash": "d" * 64},
    ])
    body = render({"analysis": analysis, "canonical": CANON, "ts": "2026-07-20T00:00:00Z"})
    assert "<!-- fleet-smoke-engine-drift-audit -->" in body, body
    assert "FFC-EX-bad.org" in body, body
    assert ("d" * 12) in body, body  # short hash rendered
    assert "Divergent (1)" in body, body
    assert "Matching (1)" in body, body


def test_render_missing_and_unreadable_sections():
    analysis = analyze(CANON, [
        {"repo": "FreeForCharity/FFC-EX-x.org", "hash": "e" * 64},
        {"repo": "FreeForCharity/FFC-EX-y.org", "hash": None},
        {"repo": "FreeForCharity/FFC-EX-z.org", "error": "nope"},
    ])
    body = render({"analysis": analysis, "canonical": CANON, "ts": "t"})
    assert "Not yet deployed (1)" in body, body
    assert "Unreadable (1)" in body, body
    assert "nope" in body, body


# --- workflow wiring shape -------------------------------------------------

def test_workflow_requires_lib_and_is_read_only():
    raw = (REPO_ROOT / ".github" / "workflows" / WF_FILE).read_text()
    assert "scripts/fleet-smoke-drift-lib.js" in raw, "workflow must require the shipped lib"
    wf = load_workflow(WF_FILE)
    perms = wf["permissions"]
    assert perms.get("contents") == "read", perms
    assert perms.get("issues") == "write", perms
    # No environment gate on any job (read-only, GITHUB_TOKEN).
    for name, job in wf["jobs"].items():
        assert "environment" not in job, f"{name} must not use an environment gate"


def test_workflow_has_schedule_and_dispatch():
    wf = load_workflow(WF_FILE)
    on = wf.get(True, wf.get("on"))
    assert "schedule" in on, on
    assert "workflow_dispatch" in on, on


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
