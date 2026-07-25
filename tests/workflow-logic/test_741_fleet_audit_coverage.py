"""Unit tests for the 741 fleet security-audit coverage decision logic.

The workflow's github-script step `require`s scripts/fleet-audit-coverage-lib.js,
so exercising that module directly tests the shipped logic: which repos count as
a coverage gap (a Node app missing either half of the detection pair) versus the
benign buckets (`notNode` repos with no dependency tree, `unreadable` fetch
failures) that must NOT raise the rolling issue.

The parsing helpers are tested against the *real* canonical files rather than
hand-written fixtures where possible, because the failure this guards against is
a fleet repo silently reading as covered. A shape test guards the workflow
wiring itself.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import load_workflow

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
LIB = REPO_ROOT / "scripts" / "fleet-audit-coverage-lib.js"
WF_FILE = "741-fleet-security-audit-coverage.yml"


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


def analyze(entries: list) -> dict:
    return _node(
        "process.stdout.write(JSON.stringify(l.analyze(JSON.parse(process.argv[1]))));",
        json.dumps(entries),
    )


def render(analysis: dict, ts: str) -> str:
    return _node(
        "process.stdout.write(JSON.stringify("
        "l.renderBody(JSON.parse(process.argv[1]), process.argv[2])));",
        json.dumps(analysis),
        ts,
    )


def extract_cron(raw: str):
    return _node(
        "process.stdout.write(JSON.stringify(l.extractCron(process.argv[1])));", raw
    )


def has_audit_script(raw: str) -> bool:
    return _node(
        "process.stdout.write(JSON.stringify(l.hasAuditScript(process.argv[1])));", raw
    )


def _covered(repo: str, cron: str = "17 6 * * *") -> dict:
    return {"repo": repo, "hasPackageJson": True, "hasWorkflow": True,
            "hasScript": True, "cron": cron}


# --- classification --------------------------------------------------------

def test_fully_covered_fleet_is_no_gap():
    r = analyze([_covered("FreeForCharity/FFC-EX-one.org", "17 6 * * *"),
                 _covered("FreeForCharity/FFC-EX-two.org", "23 6 * * *")])
    assert r["hasGap"] is False, r
    assert [c["repo"] for c in r["covered"]] == [
        "FreeForCharity/FFC-EX-one.org",
        "FreeForCharity/FFC-EX-two.org",
    ], r
    assert r["nodeRepos"] == 2, r


def test_neither_half_is_uncovered():
    # The #838 population: a Node app with no detection of any kind.
    r = analyze([{"repo": "FreeForCharity/FFC-EX-silent.org", "hasPackageJson": True,
                  "hasWorkflow": False, "hasScript": False}])
    assert r["hasGap"] is True, r
    assert r["uncovered"] == ["FreeForCharity/FFC-EX-silent.org"], r
    assert r["partial"] == [] and r["covered"] == [], r


def test_workflow_without_script_is_partial_not_covered():
    # Fails on every run with "Missing script: audit:high" — looks wired, reports
    # nothing useful. Must not be counted as covered.
    r = analyze([{"repo": "FreeForCharity/FFC-EX-half.org", "hasPackageJson": True,
                  "hasWorkflow": True, "hasScript": False}])
    assert r["hasGap"] is True, r
    assert r["partial"] == [{"repo": "FreeForCharity/FFC-EX-half.org",
                             "hasWorkflow": True, "hasScript": False}], r
    assert r["covered"] == [], r


def test_script_without_workflow_is_partial_not_covered():
    # Never runs at all — the more dangerous half, since the script's presence is
    # what a hand sweep tends to grep for.
    r = analyze([{"repo": "FreeForCharity/FFC-EX-half2.org", "hasPackageJson": True,
                  "hasWorkflow": False, "hasScript": True}])
    assert r["hasGap"] is True, r
    assert r["partial"] == [{"repo": "FreeForCharity/FFC-EX-half2.org",
                             "hasWorkflow": False, "hasScript": True}], r


def test_no_package_json_is_out_of_scope_not_a_gap():
    # The 18 static/placeholder FFC-EX repos have no dependency tree; auditing
    # them would only manufacture red (#838).
    r = analyze([{"repo": "FreeForCharity/FFC-EX-static.org", "hasPackageJson": False},
                 {"repo": "FreeForCharity/FFC-EX-static2.org"}])  # key absent entirely
    assert r["hasGap"] is False, r
    assert sorted(r["notNode"]) == [
        "FreeForCharity/FFC-EX-static.org",
        "FreeForCharity/FFC-EX-static2.org",
    ], r
    assert r["nodeRepos"] == 0, r


def test_fetch_error_is_unreadable_not_a_gap():
    # Coverage cannot be asserted either way, so guessing "uncovered" would put a
    # repo on a remediation list it may not belong on.
    r = analyze([{"repo": "FreeForCharity/FFC-EX-priv.org", "error": "HTTP 403 rate limited"}])
    assert r["hasGap"] is False, r
    assert r["unreadable"] == [
        {"repo": "FreeForCharity/FFC-EX-priv.org", "error": "HTTP 403 rate limited"}
    ], r
    assert r["nodeRepos"] == 0, r


def test_error_wins_over_partial_flags():
    # A record carrying both an error and stale flags must be reported as
    # unreadable — the flags are meaningless if the fetch failed.
    r = analyze([{"repo": "FreeForCharity/FFC-EX-x.org", "hasPackageJson": True,
                  "hasWorkflow": True, "hasScript": False, "error": "boom"}])
    assert r["hasGap"] is False, r
    assert len(r["unreadable"]) == 1 and r["partial"] == [], r


def test_mixed_fleet_counts_every_bucket():
    r = analyze([
        _covered("FreeForCharity/FFC-EX-a.org"),
        {"repo": "FreeForCharity/FFC-EX-b.org", "hasPackageJson": True,
         "hasWorkflow": True, "hasScript": False},
        {"repo": "FreeForCharity/FFC-EX-c.org", "hasPackageJson": True,
         "hasWorkflow": False, "hasScript": False},
        {"repo": "FreeForCharity/FFC-EX-d.org", "hasPackageJson": False},
        {"repo": "FreeForCharity/FFC-EX-e.org", "error": "nope"},
    ])
    assert r["hasGap"] is True, r
    assert len(r["covered"]) == 1 and len(r["partial"]) == 1
    assert len(r["uncovered"]) == 1 and len(r["notNode"]) == 1
    assert len(r["unreadable"]) == 1
    assert r["nodeRepos"] == 3, r  # covered + partial + uncovered only


def test_empty_fleet_is_no_gap():
    r = analyze([])
    assert r["hasGap"] is False, r
    assert r["nodeRepos"] == 0 and r["covered"] == [], r


# --- cron distribution -----------------------------------------------------

def test_single_cron_across_fleet_reads_as_not_staggered():
    # The state #838 warns about: 34 more repos pointed at `17 6 * * *`.
    r = analyze([_covered("FreeForCharity/FFC-EX-a.org", "17 6 * * *"),
                 _covered("FreeForCharity/FFC-EX-b.org", "17 6 * * *")])
    assert r["crons"]["staggered"] is False, r["crons"]
    assert r["crons"]["distinct"] == 1, r["crons"]
    assert r["crons"]["groups"][0]["count"] == 2, r["crons"]


def test_distinct_crons_read_as_staggered():
    r = analyze([_covered("FreeForCharity/FFC-EX-a.org", "17 6 * * *"),
                 _covered("FreeForCharity/FFC-EX-b.org", "42 6 * * *")])
    assert r["crons"]["staggered"] is True, r["crons"]
    assert r["crons"]["distinct"] == 2, r["crons"]


def test_a_single_covered_repo_is_not_reported_as_unstaggered():
    # One repo cannot collide with itself; flagging it would be noise.
    r = analyze([_covered("FreeForCharity/FFC-EX-only.org", "17 6 * * *")])
    assert r["crons"]["staggered"] is True, r["crons"]


def test_cron_groups_sorted_largest_pile_up_first():
    r = analyze([_covered("FreeForCharity/FFC-EX-a.org", "17 6 * * *"),
                 _covered("FreeForCharity/FFC-EX-b.org", "17 6 * * *"),
                 _covered("FreeForCharity/FFC-EX-c.org", "42 6 * * *")])
    groups = r["crons"]["groups"]
    assert groups[0]["cron"] == "17 6 * * *" and groups[0]["count"] == 2, groups
    assert groups[1]["cron"] == "42 6 * * *", groups


def test_cron_distribution_never_sets_has_gap():
    # Scheduling hygiene and missing detection are different problems; folding
    # them together would blur what a red rolling issue means, and "no two repos
    # share a minute" is a rule 44 repos could never satisfy.
    r = analyze([_covered("FreeForCharity/FFC-EX-a.org", "17 6 * * *"),
                 _covered("FreeForCharity/FFC-EX-b.org", "17 6 * * *")])
    assert r["hasGap"] is False, r


def test_dispatch_only_audit_copy_has_unknown_cron():
    r = analyze([{"repo": "FreeForCharity/FFC-EX-a.org", "hasPackageJson": True,
                  "hasWorkflow": True, "hasScript": True, "cron": None}])
    assert r["hasGap"] is False, r
    assert r["crons"]["groups"][0]["cron"] == "unknown", r["crons"]
    assert r["crons"]["distinct"] == 0, r["crons"]


# --- parsing helpers -------------------------------------------------------

def test_extract_cron_from_the_canonical_workflow_shape():
    raw = "on:\n  schedule:\n    - cron: '17 6 * * *' # daily, 06:17 UTC\n"
    assert extract_cron(raw) == "17 6 * * *"


def test_extract_cron_handles_double_quotes_and_no_quotes():
    assert extract_cron('    - cron: "5 4 * * 0"\n') == "5 4 * * 0"
    assert extract_cron("    - cron: 5 4 * * 0\n") == "5 4 * * 0"


def test_extract_cron_returns_none_without_a_schedule():
    assert extract_cron("on:\n  workflow_dispatch:\n") is None
    assert extract_cron("") is None


def test_has_audit_script_requires_a_real_script_entry():
    assert has_audit_script(
        '{"scripts": {"audit:high": "npm audit --omit=dev --audit-level=high"}}'
    ) is True
    assert has_audit_script('{"scripts": {"build": "next build"}}') is False
    assert has_audit_script('{}') is False


def test_has_audit_script_does_not_match_a_bare_mention():
    # A grep would call this covered; it is not. This is the false-negative that
    # would leave a repo unmonitored while reading as done.
    assert has_audit_script(
        '{"description": "run audit:high before release", "scripts": {"build": "x"}}'
    ) is False


def test_has_audit_script_survives_malformed_json():
    assert has_audit_script("{not json") is False
    assert has_audit_script("") is False


def test_has_audit_script_rejects_a_non_string_value():
    assert has_audit_script('{"scripts": {"audit:high": null}}') is False


# --- report rendering ------------------------------------------------------

def test_render_contains_marker_and_actionable_checklist():
    analysis = analyze([
        _covered("FreeForCharity/FFC-EX-good.org"),
        {"repo": "FreeForCharity/FFC-EX-silent.org", "hasPackageJson": True,
         "hasWorkflow": False, "hasScript": False},
    ])
    body = render(analysis, "2026-07-25T00:00:00Z")
    assert "<!-- fleet-security-audit-coverage -->" in body, body
    assert "- [ ] FreeForCharity/FFC-EX-silent.org" in body, body
    assert "Uncovered — no detection at all (1)" in body, body
    assert "Covered (1)" in body, body
    assert "Refs #838" in body, body


def test_render_partial_table_explains_the_effect():
    analysis = analyze([
        {"repo": "FreeForCharity/FFC-EX-wf.org", "hasPackageJson": True,
         "hasWorkflow": True, "hasScript": False},
        {"repo": "FreeForCharity/FFC-EX-sc.org", "hasPackageJson": True,
         "hasWorkflow": False, "hasScript": True},
    ])
    body = render(analysis, "t")
    assert "workflow fails every run (missing script)" in body, body
    assert "script never runs (no workflow)" in body, body


def test_render_flags_an_unstaggered_fleet():
    analysis = analyze([_covered("FreeForCharity/FFC-EX-a.org", "17 6 * * *"),
                        _covered("FreeForCharity/FFC-EX-b.org", "17 6 * * *")])
    body = render(analysis, "t")
    assert "never staggered" in body, body
    assert "Schedule distribution" in body, body


def test_render_omits_the_stagger_warning_when_distributed():
    analysis = analyze([_covered("FreeForCharity/FFC-EX-a.org", "17 6 * * *"),
                        _covered("FreeForCharity/FFC-EX-b.org", "42 6 * * *")])
    body = render(analysis, "t")
    assert "never staggered" not in body, body


def test_render_out_of_scope_and_unreadable_sections():
    analysis = analyze([
        {"repo": "FreeForCharity/FFC-EX-static.org", "hasPackageJson": False},
        {"repo": "FreeForCharity/FFC-EX-priv.org", "error": "HTTP 403"},
    ])
    body = render(analysis, "t")
    assert "No package.json (1)" in body, body
    assert "Unreadable (1)" in body, body
    assert "HTTP 403" in body, body


# --- workflow wiring shape -------------------------------------------------

def test_workflow_requires_lib_and_is_read_only():
    raw = (REPO_ROOT / ".github" / "workflows" / WF_FILE).read_text()
    assert "scripts/fleet-audit-coverage-lib.js" in raw, "workflow must require the shipped lib"
    wf = load_workflow(WF_FILE)
    perms = wf["permissions"]
    assert perms.get("contents") == "read", perms
    assert perms.get("issues") == "write", perms
    # No environment gate on any job — an alerter must never be blocked by a gate.
    for name, job in wf["jobs"].items():
        assert "environment" not in job, f"{name} must not use an environment gate"


def test_workflow_has_schedule_and_dispatch():
    wf = load_workflow(WF_FILE)
    on = wf.get(True, wf.get("on"))
    assert "schedule" in on, on
    assert "workflow_dispatch" in on, on


def test_workflow_does_not_collide_with_738_fleet_sweep():
    # Both read every fleet repo from the same shared REST budget; running them
    # at the same minute is the one avoidable contention.
    def cron_of(f):
        wf = load_workflow(f)
        on = wf.get(True, wf.get("on"))
        return [s["cron"] for s in on["schedule"]]

    mine = cron_of(WF_FILE)
    theirs = cron_of("738-fleet-smoke-engine-drift-audit.yml")
    assert set(mine).isdisjoint(set(theirs)), (mine, theirs)


def test_workflow_never_writes_to_a_fleet_repo():
    # Remediation belongs to #822; this workflow only measures. The single write
    # is the rolling issue in this repo.
    raw = (REPO_ROOT / ".github" / "workflows" / WF_FILE).read_text()
    for forbidden in ["create_or_update_file", "git push", "peter-evans/create-pull-request"]:
        assert forbidden not in raw, f"741 must not write to fleet repos ({forbidden})"


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
