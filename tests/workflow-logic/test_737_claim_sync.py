"""Unit tests for the 737 claim-sync decision logic (scripts/claim-sync-lib.js).

The workflow's github-script steps `require` this exact module, so testing the
library functions directly tests the shipped logic — link extraction (which
issues a PR body claims) and the expiry decision (when a stale claim is
released). A shape test also guards the workflow wiring itself.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import load_workflow

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
LIB = REPO_ROOT / "scripts" / "claim-sync-lib.js"
WF_FILE = "737-claim-sync.yml"


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


def extract(body: str) -> dict:
    return _node(
        "process.stdout.write(JSON.stringify(l.extractLinkedIssues(process.argv[1]||'')));",
        body,
    )


def decide(args: dict) -> bool:
    return _node(
        "process.stdout.write(JSON.stringify(l.decideRelease(JSON.parse(process.argv[1]))));",
        json.dumps(args),
    )


# --- link extraction -------------------------------------------------------

def test_closing_keywords_extracted():
    r = extract("Closes #12\nFixes #3\nResolves #4")
    assert sorted(r["all"]) == [3, 4, 12], r
    assert sorted(r["closing"]) == [3, 4, 12], r
    assert r["refs"] == [], r


def test_refs_counts_as_claim_but_not_closing():
    r = extract("Refs #45 for context; refs #46 too")
    assert sorted(r["all"]) == [45, 46], r
    assert r["closing"] == [], r
    assert sorted(r["refs"]) == [45, 46], r


def test_case_insensitive_and_colon_forms():
    r = extract("FIXED: #7  closed:#9  Resolve #11")
    # "closed:#9" has no separator space but a colon separator -> matches.
    assert sorted(r["all"]) == [7, 9, 11], r


def test_dedup_across_keywords():
    r = extract("Fixes #5 and later Closes #5 again, refs #5")
    assert r["all"] == [5], r
    assert r["closing"] == [5], r
    assert r["refs"] == [], r  # a closing ref outranks a bare ref for the same #


def test_no_false_match_on_word_boundary_or_missing_separator():
    r = extract("The prefix #3 and closes#4 should not match; see hotfix #5.")
    # "prefix"/"hotfix" fail \b; "closes#4" has no separator.
    assert r["all"] == [], r


def test_empty_and_none_body():
    assert extract("")["all"] == []
    assert _node(
        "process.stdout.write(JSON.stringify(l.extractLinkedIssues(null).all));"
    ) == []


# --- expiry decision -------------------------------------------------------

DAY = 24 * 60 * 60 * 1000
EXPIRY = 48 * 60 * 60 * 1000


def test_open_linked_pr_never_releases_even_when_old():
    assert decide(
        {"hasOpenLinkedPR": True, "lastActivityMs": 0, "nowMs": 10 * DAY, "thresholdMs": EXPIRY}
    ) is False


def test_no_pr_and_idle_past_threshold_releases():
    assert decide(
        {"hasOpenLinkedPR": False, "lastActivityMs": 0, "nowMs": 3 * DAY, "thresholdMs": EXPIRY}
    ) is True


def test_no_pr_but_recent_activity_holds():
    now = 10 * DAY
    assert decide(
        {"hasOpenLinkedPR": False, "lastActivityMs": now - DAY, "nowMs": now, "thresholdMs": EXPIRY}
    ) is False


def test_exactly_threshold_releases():
    now = 10 * DAY
    assert decide(
        {"hasOpenLinkedPR": False, "lastActivityMs": now - EXPIRY, "nowMs": now, "thresholdMs": EXPIRY}
    ) is True


def test_nan_activity_is_safe_no_release():
    # A missing/garbage updated_at (NaN) must not spuriously release a claim.
    assert _node(
        "process.stdout.write(JSON.stringify("
        "l.decideRelease({hasOpenLinkedPR:false,lastActivityMs:NaN,nowMs:1e12,thresholdMs:1})"
        "));"
    ) is False


def test_default_threshold_is_48h():
    assert _node("process.stdout.write(JSON.stringify(l.EXPIRY_MS));") == EXPIRY


# --- workflow wiring shape -------------------------------------------------

def test_workflow_requires_lib_and_has_both_triggers():
    raw = (REPO_ROOT / ".github" / "workflows" / WF_FILE).read_text()
    assert "scripts/claim-sync-lib.js" in raw, "workflow must require the shipped lib"
    # yaml maps `on:` to the boolean True key; assert triggers via the parsed map.
    wf = load_workflow(WF_FILE)
    on = wf.get(True, wf.get("on"))
    assert "pull_request" in on, on
    assert "schedule" in on, on
    assert "workflow_dispatch" in on, on
    assert set(on["pull_request"]["types"]) >= {
        "opened", "reopened", "edited", "closed", "converted_to_draft", "ready_for_review",
    }, on["pull_request"]
    jobs = wf["jobs"]
    assert "label-sync" in jobs and "sweep" in jobs, list(jobs)


def test_sweep_uses_ambient_token_hub_only():
    # CBM_TOKEN lives only in the gated github-prod environment, so it is empty
    # on schedule events — the sweep must run on the ambient GITHUB_TOKEN and
    # therefore can only mutate this repo (2026-07-20 first-fire failure).
    raw = (REPO_ROOT / ".github" / "workflows" / WF_FILE).read_text()
    assert "FFC-Cloudflare-Automation" in raw, raw
    assert "secrets.CBM_TOKEN" not in raw, (
        "sweep must not reference CBM_TOKEN: it is an environment secret in "
        "gated github-prod and evaluates empty in an ungated scheduled job"
    )
    wf = load_workflow(WF_FILE)
    sweep = wf["jobs"]["sweep"]
    assert sweep.get("environment") is None, "daily sweep must stay ungated"
    assert sweep["permissions"].get("issues") == "write", sweep["permissions"]


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
