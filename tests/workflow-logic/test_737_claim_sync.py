"""Unit tests for the 737 claim-sync decision logic (scripts/claim-sync-lib.js)
and for the sweep github-script that drives it.

The workflow's github-script steps `require` this exact module, so testing the
library functions directly tests the shipped logic — link extraction (which
issues a PR body claims) and the expiry decision (when a stale claim is
released). A shape test also guards the workflow wiring itself.

The second half drives the **sweep** step end to end through
`harness/claim_sync_shim.mjs`. The backlog is org-wide (`AGENTS.md`: available =
`org:FreeForCharity label:agentic-os is:open -label:claimed`) while the label
mechanism was repo-local, so a hub issue implemented by a template/site-repo PR
read as unclaimed and invited a duplicate PR against a file a finished PR was
already rewriting (#939). These lock down the repo-keyed reconciliation:

  - a qualified `Refs FreeForCharity/FFC-Cloudflare-Automation#N` from ANY org
    repo claims hub issue N;
  - a bare `#N` in a non-hub PR claims nothing here (it means that repo's #N);
  - a reference qualified with a THIRD repo claims nothing here;
  - the claim is released once the last open PR referencing it — in any repo —
    is closed or merged, while a hand-written CLAIM comment still gets its full
    48h (releasing those on sight would let two agents collide within the hour);
  - an unreadable OR truncated org search never reads as "no PR claims this",
    and never releases a live claim on that silence;
  - the org search costs one call per sweep, never one per issue.

Per #935 each new guard is also proved by reintroducing the defect it catches:
the mutation tests copy the lib into a temp workspace with the fix reverted and
assert the corresponding case flips.
"""

from __future__ import annotations

import datetime
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import load_workflow, step_github_script

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
LIB = REPO_ROOT / "scripts" / "claim-sync-lib.js"
WF_FILE = "737-claim-sync.yml"
SHIM = pathlib.Path(__file__).resolve().parent / "harness" / "claim_sync_shim.mjs"
NODE = shutil.which("node") or "node"

HUB = "FreeForCharity/FFC-Cloudflare-Automation"
TEMPLATE = "FreeForCharity/FFC-IN-FFC_Single_Page_Template"
THIRD = "FreeForCharity/FFC-EX-canary"


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


def extract_scoped(body: str, opts: dict) -> dict:
    return _node(
        "process.stdout.write(JSON.stringify("
        "l.extractLinkedIssues(process.argv[1]||'',JSON.parse(process.argv[2]))));",
        body,
        json.dumps(opts),
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
    raw = (REPO_ROOT / ".github" / "workflows" / WF_FILE).read_text(encoding="utf-8")
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
    raw = (REPO_ROOT / ".github" / "workflows" / WF_FILE).read_text(encoding="utf-8")
    assert "FFC-Cloudflare-Automation" in raw, raw
    assert "secrets.CBM_TOKEN" not in raw, (
        "sweep must not reference CBM_TOKEN: it is an environment secret in "
        "gated github-prod and evaluates empty in an ungated scheduled job"
    )
    wf = load_workflow(WF_FILE)
    sweep = wf["jobs"]["sweep"]
    assert sweep.get("environment") is None, "daily sweep must stay ungated"
    assert sweep["permissions"].get("issues") == "write", sweep["permissions"]


# --- repo-keyed extraction (#939 criteria 1-3) -----------------------------

HUB_SCOPE = {"sourceRepo": HUB, "targetRepo": HUB}
FROM_TEMPLATE = {"sourceRepo": TEMPLATE, "targetRepo": HUB}


def test_qualified_hub_reference_from_another_repo_is_a_hub_claim():
    # Criterion 1: the form three finished PRs were actually using while their
    # hub issues sat in the pickup query looking unclaimed.
    r = extract_scoped(f"Refs {HUB}#934", FROM_TEMPLATE)
    assert r["all"] == [934], r
    assert r["refs"] == [934] and r["closing"] == [], r


def test_bare_reference_in_a_non_hub_pr_is_not_a_hub_claim():
    # Criterion 2: a bare `#N` means THAT repo's #N, not the hub's.
    assert extract_scoped("Closes #934", FROM_TEMPLATE)["all"] == []


def test_qualified_reference_to_a_third_repo_claims_nothing_here():
    # Criterion 3.
    assert extract_scoped(f"Refs {THIRD}#934", FROM_TEMPLATE)["all"] == []
    assert extract_scoped(f"Fixes {THIRD}#934", HUB_SCOPE)["all"] == []


def test_bare_reference_in_a_hub_pr_still_claims():
    # Criterion 5: the same-repo path is unchanged by the scoping.
    assert extract_scoped("Closes #12", HUB_SCOPE)["all"] == [12]


def test_hub_qualified_reference_in_a_hub_pr_claims():
    # A hub PR may qualify its own repo; that is still this repo's issue.
    assert extract_scoped(f"Closes {HUB}#12", HUB_SCOPE)["all"] == [12]


def test_repo_matching_is_case_insensitive():
    assert extract_scoped(f"Refs {HUB.lower()}#934", FROM_TEMPLATE)["all"] == [934]


def test_closing_and_refs_split_survives_scoping():
    r = extract_scoped(f"Closes {HUB}#7 and refs {HUB}#8", FROM_TEMPLATE)
    assert sorted(r["all"]) == [7, 8], r
    assert r["closing"] == [7], r
    assert r["refs"] == [8], r


def test_unscoped_call_ignores_qualified_references():
    # Legacy shape: callers that pass no scope get bare same-repo refs only, so
    # a qualified reference to somewhere else can never leak into their claim.
    assert extract(f"Refs {THIRD}#934")["all"] == []
    assert extract("Refs #934")["all"] == [934]


def test_scoped_call_with_an_unparseable_target_matches_nothing():
    # Fail closed: a scoped call that cannot say which repo it means must not
    # fall back to labeling on bare references.
    assert extract_scoped("Closes #12", {"sourceRepo": HUB, "targetRepo": "not-a-repo"})["all"] == []


def test_no_false_match_on_a_path_like_reference():
    # A path in prose resolves to a "repo" nothing matches, rather than being
    # read as a bare reference to the hub.
    assert extract_scoped("Fixes scripts/claim-sync-lib.js#3", HUB_SCOPE)["all"] == []


# --- claim provenance + release decision (#939 criterion 4) ----------------


def test_marker_round_trips_from_claim_comment_to_detection():
    assert _node(
        "const c=l.linkedClaimComment('o/r#1');"
        "process.stdout.write(JSON.stringify(l.hasLinkedClaimMarker([{body:c}])));"
    ) is True
    assert _node(
        "process.stdout.write(JSON.stringify("
        "l.hasLinkedClaimMarker([{body:'CLAIM: live-session branch 2026-07-30'}])));"
    ) is False
    assert _node("process.stdout.write(JSON.stringify(l.hasLinkedClaimMarker(null)));") is False


def test_linked_pr_claim_releases_immediately_once_the_pr_is_gone():
    # Criterion 4: the sweep gets no `closed` event for a cross-repo PR, so a
    # finished PR-derived claim releases on the next sweep, not 48h later.
    assert decide(
        {
            "hasOpenLinkedPR": False,
            "claimedByLinkedPR": True,
            "lastActivityMs": 10 * DAY,
            "nowMs": 10 * DAY,
            "thresholdMs": EXPIRY,
        }
    ) is True


def test_linked_pr_claim_never_releases_while_a_pr_is_still_open():
    assert decide(
        {
            "hasOpenLinkedPR": True,
            "claimedByLinkedPR": True,
            "lastActivityMs": 0,
            "nowMs": 10 * DAY,
            "thresholdMs": EXPIRY,
        }
    ) is False


def test_hand_claim_still_gets_the_full_window():
    # The default matters: releasing an unmarked (hand-written) claim on sight
    # would let a second agent pick up work already in flight.
    now = 10 * DAY
    assert decide(
        {"hasOpenLinkedPR": False, "lastActivityMs": now - 3600_000, "nowMs": now}
    ) is False


# --- sweep step, end to end ------------------------------------------------

SWEEP_JOB = "sweep"
SWEEP_STEP = "Sync cross-repo claims"

NOW = datetime.datetime(2026, 7, 30, 12, 0, 0, tzinfo=datetime.timezone.utc)
NOW_MS = int(NOW.timestamp() * 1000)
MARKER = _node("process.stdout.write(JSON.stringify(l.LINKED_CLAIM_MARKER));")


def _ago(**kwargs) -> str:
    return (NOW - datetime.timedelta(**kwargs)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _search_item(nwo: str, number: int, body: str) -> dict:
    return {
        "number": number,
        "body": body,
        "repository_url": f"https://api.github.com/repos/{nwo}",
    }


def _claimed(number: int, updated_at: str) -> dict:
    return {"number": number, "updated_at": updated_at, "labels": [{"name": "claimed"}]}


def _open_issue(number: int, labels=()) -> dict:
    return {
        "number": number,
        "state": "open",
        "labels": [{"name": n} for n in labels],
        "updated_at": _ago(hours=1),
    }


# Mutations for the #935 "prove the guard by reintroducing the defect" pass.
# Each anchor is asserted before replacement, so a refactor that moves the guard
# fails these loudly instead of silently testing nothing.
MUT_BARE_ONLY = (
    "const linkPattern = (keywords) => "
    "new RegExp(`\\\\b${keywords}\\\\b[:\\\\s]+(${NWO})?#(\\\\d+)`, 'gi');",
    "const linkPattern = (keywords) => "
    "new RegExp(`\\\\b${keywords}\\\\b[:\\\\s]+()#(\\\\d+)`, 'gi');",
)
MUT_NO_REPO_KEY = (
    "      if ((qualified || sourceRepo) !== targetRepo) continue;",
    "      if (false) continue;",
)


def _workspace(td: pathlib.Path, mutation: tuple[str, str] | None) -> pathlib.Path:
    """A GITHUB_WORKSPACE whose claim-sync-lib.js is the shipped one, or a
    mutated copy with one guard reverted."""
    if mutation is None:
        return REPO_ROOT
    src = LIB.read_text(encoding="utf-8")
    old, new = mutation
    assert old in src, f"mutation anchor no longer present in claim-sync-lib.js: {old!r}"
    ws = td / "ws"
    (ws / "scripts").mkdir(parents=True)
    (ws / "scripts" / "claim-sync-lib.js").write_text(src.replace(old, new), encoding="utf-8")
    return ws


def run_sweep(
    *,
    hub_prs=None,
    org_prs=None,
    claimed_issues=None,
    issues=None,
    comments=None,
    dry_run=False,
    search_throws=False,
    search_incomplete=False,
    search_total=None,
    mutation=None,
) -> dict:
    script = step_github_script(WF_FILE, SWEEP_JOB, SWEEP_STEP)
    context = {
        "repo": {"owner": "FreeForCharity", "repo": "FFC-Cloudflare-Automation"},
        "payload": {},
    }
    with tempfile.TemporaryDirectory() as td:
        tdp = pathlib.Path(td)
        files = {
            "TEST_SCRIPT_FILE": ("script.js", script),
            "TEST_CONTEXT_FILE": ("context.json", json.dumps(context)),
            "TEST_HUB_PRS_FILE": ("hub_prs.json", json.dumps(hub_prs or [])),
            "TEST_ORG_PRS_FILE": ("org_prs.json", json.dumps(org_prs or [])),
            "TEST_CLAIMED_ISSUES_FILE": ("claimed.json", json.dumps(claimed_issues or [])),
            "TEST_ISSUES_FILE": ("issues.json", json.dumps(issues or {})),
            "TEST_COMMENTS_FILE": ("comments.json", json.dumps(comments or {})),
        }
        env = {
            "PATH": f"{pathlib.Path(NODE).parent}:/usr/bin:/bin:/usr/local/bin",
            "TEST_NOW_MS": str(NOW_MS),
            "GITHUB_WORKSPACE": str(_workspace(tdp, mutation)),
            "DRY_RUN": "true" if dry_run else "false",
        }
        for var, (name, content) in files.items():
            (tdp / name).write_text(content, encoding="utf-8")
            env[var] = str(tdp / name)
        if search_throws:
            env["TEST_SEARCH_THROWS"] = "1"
        if search_incomplete:
            env["TEST_SEARCH_INCOMPLETE"] = "1"
        if search_total is not None:
            env["TEST_SEARCH_TOTAL"] = str(search_total)
        proc = subprocess.run(
            [NODE, str(SHIM)],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
    if proc.returncode != 0:
        raise AssertionError(f"harness crashed: {proc.stderr[-2000:]}")
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    assert not result["threw"], result["threw"]
    assert not result["failed"], result["failed"]
    return result


def _labeled(result: dict) -> list[int]:
    return [a["issue_number"] for a in result["addedLabels"]]


def _released(result: dict) -> list[int]:
    return [r["issue_number"] for r in result["removedLabels"]]


def test_sweep_claims_a_hub_issue_from_a_cross_repo_pr():
    r = run_sweep(
        org_prs=[_search_item(TEMPLATE, 433, f"Fixes the www probe.\n\nRefs {HUB}#934")],
        issues={"934": _open_issue(934)},
    )
    assert _labeled(r) == [934], r
    body = r["comments"][0]["body"]
    assert MARKER in body, body
    assert f"{TEMPLATE}#433" in body, body


def test_sweep_ignores_a_bare_reference_in_a_foreign_pr():
    r = run_sweep(
        org_prs=[_search_item(TEMPLATE, 433, "Closes #934")],
        issues={"934": _open_issue(934)},
    )
    assert _labeled(r) == [], r
    assert r["getCalls"] == [], "an unclaimed number must not even be looked up"


def test_sweep_ignores_a_reference_qualified_with_a_third_repo():
    r = run_sweep(
        org_prs=[_search_item(TEMPLATE, 433, f"Refs {THIRD}#934")],
        issues={"934": _open_issue(934)},
    )
    assert _labeled(r) == [], r


def test_sweep_still_claims_from_a_bare_reference_in_a_hub_pr():
    r = run_sweep(
        hub_prs=[{"number": 940, "body": "Closes #934"}],
        issues={"934": _open_issue(934)},
    )
    assert _labeled(r) == [934], r


def test_sweep_skips_an_issue_that_is_closed_already_labeled_or_missing():
    r = run_sweep(
        org_prs=[
            _search_item(TEMPLATE, 1, f"Refs {HUB}#100"),
            _search_item(TEMPLATE, 2, f"Refs {HUB}#200"),
            _search_item(TEMPLATE, 3, f"Refs {HUB}#300"),
        ],
        issues={
            "100": {"number": 100, "state": "closed", "labels": []},
            "200": _open_issue(200, labels=["claimed"]),
            # 300 is absent -> issues.get rejects (deleted/transferred/404)
        },
    )
    assert _labeled(r) == [], r


def test_sweep_keeps_a_claim_while_the_cross_repo_pr_is_open():
    r = run_sweep(
        org_prs=[_search_item(TEMPLATE, 433, f"Refs {HUB}#934")],
        claimed_issues=[_claimed(934, _ago(days=5))],
        comments={"934": [{"body": f"claimed\n{MARKER}"}]},
    )
    assert _released(r) == [], "an open PR anywhere in the org holds the claim"


def test_sweep_releases_once_the_last_cross_repo_pr_is_closed():
    # Criterion 4, inside the 48h window: the marker is what tells the sweep the
    # claim came from a PR rather than from a human's CLAIM comment.
    r = run_sweep(
        claimed_issues=[_claimed(934, _ago(hours=1))],
        comments={"934": [{"body": f"claimed\n{MARKER}"}]},
    )
    assert _released(r) == [934], r
    assert "released" in r["comments"][0]["body"], r["comments"]


def test_sweep_holds_a_hand_written_claim_inside_the_window():
    r = run_sweep(
        claimed_issues=[_claimed(934, _ago(hours=1))],
        comments={"934": [{"body": "CLAIM: live-session feat/x 2026-07-30T11:00Z"}]},
    )
    assert _released(r) == [], "a hand claim must keep its full 48h"


def test_sweep_expires_a_hand_written_claim_past_the_window():
    r = run_sweep(claimed_issues=[_claimed(934, _ago(days=3))])
    assert _released(r) == [934], r
    assert "expired" in r["comments"][0]["body"], r["comments"]
    assert r["listCommentsCalls"] == [], (
        "past the expiry the backstop releases either way — provenance is not "
        "worth a REST call"
    )


def test_sweep_dry_run_writes_nothing():
    r = run_sweep(
        org_prs=[_search_item(TEMPLATE, 433, f"Refs {HUB}#934")],
        issues={"934": _open_issue(934)},
        claimed_issues=[_claimed(880, _ago(days=3))],
        dry_run=True,
    )
    assert r["addedLabels"] == [] and r["removedLabels"] == [] and r["comments"] == [], r
    assert "1 claimed, 1 released (dry-run)" in " ".join(r["summary"]), r["summary"]


def test_sweep_treats_an_unreadable_search_as_unknown_not_as_no_claims():
    r = run_sweep(
        org_prs=[_search_item(TEMPLATE, 433, f"Refs {HUB}#934")],
        issues={"934": _open_issue(934)},
        claimed_issues=[_claimed(893, _ago(hours=1))],
        comments={"893": [{"body": f"claimed\n{MARKER}"}]},
        search_throws=True,
    )
    assert _labeled(r) == [], "no cross-repo claim can be trusted from a failed search"
    assert _released(r) == [], (
        "a PR-derived claim must not be released on a search that never answered"
    )
    assert any("search unusable" in w for w in r["warnings"]), r["warnings"]


def test_sweep_still_expires_hand_claims_when_the_search_fails():
    # The backstop is independent of the org search: hub PRs remain authoritative.
    r = run_sweep(claimed_issues=[_claimed(880, _ago(days=3))], search_throws=True)
    assert _released(r) == [880], r


def test_sweep_rejects_a_truncated_search_that_looks_complete():
    # The Search API caps a query at 1,000 results and stops paginating with
    # incomplete_results FALSE (#925). Fewer rows than total_count means the open
    # PR set is unknown, which must never read as "nothing claims this".
    r = run_sweep(
        org_prs=[_search_item(TEMPLATE, 433, f"Refs {HUB}#934")],
        issues={"934": _open_issue(934)},
        search_total=1000,
    )
    assert _labeled(r) == [], r
    assert any("truncated" in w for w in r["warnings"]), r["warnings"]


def test_sweep_rejects_an_incomplete_results_search():
    r = run_sweep(
        org_prs=[_search_item(TEMPLATE, 433, f"Refs {HUB}#934")],
        issues={"934": _open_issue(934)},
        search_incomplete=True,
    )
    assert _labeled(r) == [], r
    assert any("incomplete_results" in w for w in r["warnings"]), r["warnings"]


def test_sweep_spends_one_org_search_per_run_not_one_per_issue():
    # Criterion 6 — the shared 5,000/hr budget (AGENTS.md).
    r = run_sweep(
        org_prs=[_search_item(TEMPLATE, i, f"Refs {HUB}#{900 + i}") for i in range(1, 6)],
        claimed_issues=[_claimed(880, _ago(days=3)), _claimed(881, _ago(days=3))],
        issues={str(900 + i): _open_issue(900 + i) for i in range(1, 6)},
    )
    assert len(r["searchCalls"]) == 1, r["searchCalls"]
    assert r["searchCalls"][0]["q"] == "org:FreeForCharity is:pr is:open", r["searchCalls"]


# --- the same cases with the fix reverted (#935) ---------------------------


def test_mutation_bare_only_regex_loses_the_cross_repo_claim():
    # Revert LINK_RE to the bare-`#` form: criterion 1 fails — which is exactly
    # the state #939 found, with three finished PRs claiming nothing.
    r = run_sweep(
        org_prs=[_search_item(TEMPLATE, 433, f"Refs {HUB}#934")],
        issues={"934": _open_issue(934)},
        mutation=MUT_BARE_ONLY,
    )
    assert _labeled(r) == [], "the mutation must break criterion 1, or it proves nothing"


def test_mutation_dropping_the_repo_comparison_claims_foreign_issues():
    # Make the repo check unconditional: criteria 2 and 3 fail — a bare `#934`
    # in a template PR, and a reference to a third repo, both label hub #934.
    for body in ("Closes #934", f"Refs {THIRD}#934"):
        r = run_sweep(
            org_prs=[_search_item(TEMPLATE, 433, body)],
            issues={"934": _open_issue(934)},
            mutation=MUT_NO_REPO_KEY,
        )
        assert _labeled(r) == [934], f"mutation must break the repo keying for {body!r}"


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
