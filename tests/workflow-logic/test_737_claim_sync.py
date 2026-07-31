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

The third half drives the **label-sync** step the same way, for the feedback
loop #948 added. `Refs #N` is both the claiming form and how people write a
citation, and 737 cannot tell them apart — it comments on the ISSUE, which the
PR author has no reason to be watching, so a citation written as a claim removes
a live backlog pickup with nobody aware it happened (#945, hidden six hours
behind a docs draft). These pin the notice that closes the loop:

  - a PR that claims something gets exactly one comment naming every issue it
    claimed, with the cite-with-a-link escape hatch;
  - a PR that claims nothing, or whose issues were already claimed, gets none;
  - the notice never repeats, and idempotency is keyed per ISSUE so a PR that
    later claims a second issue is still told about that one;
  - unreadable PR comments skip the notice rather than risk a duplicate;
  - and the sweep REPORTS, never releases, claims whose only claimant is a draft
    PR older than 48h — a draft is not active work, but it suppresses a pickup
    exactly as hard as a ready PR.

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
from wf_extract import child_env, load_workflow, step_github_script

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
LIB = REPO_ROOT / "scripts" / "claim-sync-lib.js"
WF_FILE = "737-claim-sync.yml"
SHIM = pathlib.Path(__file__).resolve().parent / "harness" / "claim_sync_shim.mjs"
NODE = shutil.which("node") or "node"

HUB = "FreeForCharity/FFC-Cloudflare-Automation"
TEMPLATE = "FreeForCharity/FFC-IN-FFC_Single_Page_Template"
THIRD = "FreeForCharity/FFC-EX-canary"


# encoding="utf-8" is required, not cosmetic. `text=True` alone decodes with the
# platform's ANSI codepage, which on Windows is cp1252 — and every payload here
# now carries emoji (🔒 in the claim notice, ⏳ in the draft report), so the
# decode raises UnicodeDecodeError and the whole module crashes rather than
# failing a test. Same class as the cp1252 decode tracked in #945, and the
# Conductor runs on exactly that host.
def _node(expr_body: str, *argv: str) -> object:
    code = f"const l=require({json.dumps(str(LIB))});{expr_body}"
    proc = subprocess.run(
        [NODE, "-e", code, *argv],
        capture_output=True,
        text=True,
        encoding="utf-8",
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


# --- code spans are documentation, not claims (#948) ------------------------


def test_a_keyword_inside_an_inline_code_span_is_not_a_claim():
    """Prose ABOUT the protocol must not execute the protocol.

    The live failure: PR #958 cited an issue by URL and added a sentence
    explaining not to use the keyword form — with the keyword in backticks.
    That sentence claimed the issue and pulled it off the pickup query, which
    is the exact defect the sentence was warning about.
    """
    r = extract("Cite with a URL, not `Refs #834` — see the protocol note.")
    assert r["all"] == [], f"a keyword in backticks is documentation, got {r}"


def test_a_keyword_inside_a_fenced_block_is_not_a_claim():
    r = extract("Example of the claiming form:\n\n```\nCloses #12\n```\n\nDo not use it.")
    assert r["all"] == [], f"a fenced example must not claim, got {r}"


def test_a_multi_backtick_span_is_still_a_span():
    r = extract("Written ``Refs #99`` when you need a literal backtick inside.")
    assert r["all"] == [], f"``…`` is still a code span, got {r}"


def test_a_real_claim_beside_documented_prose_still_claims():
    """The exclusion must not become a way to hide a claim.

    A body that genuinely claims #12 and also *discusses* #834 claims exactly
    one of them. Dropping both would trade this bug for the worse one.
    """
    r = extract("Closes #12. Unlike a citation, do not write `Refs #834` here.")
    assert r["all"] == [12], f"the real claim must survive, got {r}"
    assert r["closing"] == [12], r


def test_an_unterminated_fence_does_not_swallow_a_later_claim():
    """A stray fence must not blank the rest of the body.

    The failure mode to avoid is a typo'd fence silently hiding a real claim —
    that would turn a formatting slip into a lost backlog pickup, trading this
    bug for a quieter one. Verified rather than assumed: the fence pattern's
    trailing `$` is line-anchored under /m, so an unterminated fence consumes
    only up to a line end, and a claim written afterwards still registers.
    """
    r = extract("```\nunterminated\n\nCloses #12")
    assert r["all"] == [12], f"a claim after a stray fence must survive, got {r}"


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


def test_label_sync_can_write_to_the_pull_request_it_comments_on():
    """The claim notice is a comment on a PULL REQUEST.

    It is posted through the issues endpoint, but GitHub scopes that call by the
    target's real type: with `pull-requests: read` the POST returns 403
    "Resource not accessible by integration" and answers
    `x-accepted-github-permissions: issues=write; pull_requests=write`. Measured
    live on run 30631950400, where the labels applied and the job then died on
    the comment — i.e. the notice shipped in a job that could not post it.

    A permission is not exercised by any unit test, so this is the only place
    the requirement can be pinned.
    """
    perms = load_workflow(WF_FILE)["jobs"]["label-sync"]["permissions"]
    assert perms.get("issues") == "write", perms
    assert perms.get("pull-requests") == "write", (
        "label-sync comments on a PR, which needs pull-requests: write; "
        f"`read` 403s at the createComment call (got {perms.get('pull-requests')!r})"
    )


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


def test_a_citation_written_as_a_link_claims_nothing():
    # The escape hatch AGENTS.md § "Work claiming" now documents (#948): cite
    # with a URL or an unkeyworded `repo#N`, claim with `Refs`/`Closes`. Advice
    # that the extractor does not actually honour would be a trap, and a guard's
    # prose is exactly where its own premise goes to get contradicted (#912).
    for body in (
        f"Background: https://github.com/{HUB}/issues/945 explains why.",
        f"Refs https://github.com/{HUB}/issues/945",
        f"The enforceable half lives in {HUB}#945.",
        "See #945 for the measurement.",
    ):
        assert extract_scoped(body, HUB_SCOPE)["all"] == [], body


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
LABEL_SYNC_JOB = "label-sync"
LABEL_SYNC_STEP = "Sync claimed label from linked PR"

NOW = datetime.datetime(2026, 7, 30, 12, 0, 0, tzinfo=datetime.timezone.utc)
NOW_MS = int(NOW.timestamp() * 1000)
MARKER = _node("process.stdout.write(JSON.stringify(l.LINKED_CLAIM_MARKER));")


def _ago(**kwargs) -> str:
    return (NOW - datetime.timedelta(**kwargs)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _search_item(nwo: str, number: int, body: str, author: str = "clarkemoyer", **extra) -> dict:
    # `draft`/`created_at` are optional: most fixtures do not care, and their
    # ABSENCE is itself under test (an unknown draft flag must not be reported
    # as a stale draft claim).
    return {
        "number": number,
        "body": body,
        "repository_url": f"https://api.github.com/repos/{nwo}",
        "author": author,
        **extra,
    }


def _draft(nwo: str, number: int, body: str, **age) -> dict:
    return _search_item(nwo, number, body, draft=True, created_at=_ago(**age))


def _ready(nwo: str, number: int, body: str, **age) -> dict:
    return _search_item(nwo, number, body, draft=False, created_at=_ago(**age))


def _fleet_population(dependabot: int, other: int) -> list[dict]:
    """An org PR population shaped like the live one that broke the sweep: mostly
    dependabot, plus a handful of real PRs — one of which claims a hub issue."""
    rows = [
        _search_item(TEMPLATE, 1000 + i, "Bump some dep", author="app/dependabot")
        for i in range(dependabot)
    ]
    rows.append(_search_item(TEMPLATE, 433, f"Refs {HUB}#934"))
    rows += [_search_item(THIRD, 2000 + i, "unrelated") for i in range(other - 1)]
    return rows


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

# This one reverts the WORKFLOW's query rather than the lib, so it is applied to
# the extracted script (see run_sweep's script_mutation) — the defect it restores
# is the un-narrowed org search that the first live sweep ran into.
MUT_UNNARROWED_QUERY = (
    "...NON_CLAIMING_AUTHORS.map((a) => `-author:${a}`),",
    "",
)
MUT_STRICT_HEADROOM = (
    "if (total >= SEARCH_CAP_HEADROOM) {",
    "if (total > SEARCH_CAP_HEADROOM) {",
)

# #948 Part A: drop the PR-side notice entirely (the pre-fix state).
MUT_NO_PR_NOTICE = ("if (fresh.length) {", "if (false) {")
# #948 Part A: keep the notice but blind it to what it has already said, which
# is the difference between "tell the author once" and "tell them every sync".
MUT_NOTICE_BLIND = ("  for (const c of comments || []) {", "  for (const c of []) {")
# #951 review: read only the FIRST page of an oldest-first comment list, the
# prefix mistake the backwards walk exists to avoid.
MUT_FIRST_PAGE_ONLY = (
    "for (let p = last, reads = 0; p > 1 && reads < backPages; p--, reads++) {",
    "for (let p = last, reads = 0; false; p--, reads++) {",
)
# #948 Part B: report a claim held by ANY draft, however fresh — a draft opened
# an hour ago is a PR being written, not a stuck claim.
MUT_DRAFT_IGNORES_AGE = (
    "    if (!Number.isFinite(age) || age < thresholdMs) return null;",
    "    if (!Number.isFinite(age)) return null;",
)
# #948 Part B: report a claim held by a READY PR — i.e. flag work that is in
# review, which is the report crying wolf on healthy activity.
MUT_DRAFT_IGNORES_READY = (
    "    if (!r || r.draft !== true) return null;",
    "    if (!r) return null;",
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


def _invoke(
    script: str,
    context: dict,
    *,
    hub_prs=None,
    org_prs=None,
    claimed_issues=None,
    issues=None,
    comments=None,
    mutation=None,
    env_extra=None,
) -> dict:
    """Run one extracted github-script step against the shim."""
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
        env = child_env(
            pathlib.Path(NODE).parent,
            TEST_NOW_MS=str(NOW_MS),
            GITHUB_WORKSPACE=str(_workspace(tdp, mutation)),
        )
        env.update(env_extra or {})
        for var, (name, content) in files.items():
            (tdp / name).write_text(content, encoding="utf-8")
            env[var] = str(tdp / name)
        proc = subprocess.run(
            [NODE, str(SHIM)],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",  # see _node: the shim's JSON carries 🔒/⏳
            timeout=120,
        )
    if proc.returncode != 0:
        raise AssertionError(f"harness crashed: {proc.stderr[-2000:]}")
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    assert not result["threw"], result["threw"]
    assert not result["failed"], result["failed"]
    return result


def _mutate(script: str, script_mutation, what: str) -> str:
    if script_mutation is None:
        return script
    old, new = script_mutation
    assert old in script, f"mutation anchor no longer present in the {what} script: {old!r}"
    return script.replace(old, new)


HUB_CONTEXT = {"owner": "FreeForCharity", "repo": "FFC-Cloudflare-Automation"}


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
    script_mutation=None,
) -> dict:
    env_extra = {"DRY_RUN": "true" if dry_run else "false"}
    if search_throws:
        env_extra["TEST_SEARCH_THROWS"] = "1"
    if search_incomplete:
        env_extra["TEST_SEARCH_INCOMPLETE"] = "1"
    if search_total is not None:
        env_extra["TEST_SEARCH_TOTAL"] = str(search_total)
    return _invoke(
        _mutate(step_github_script(WF_FILE, SWEEP_JOB, SWEEP_STEP), script_mutation, "sweep"),
        {"repo": HUB_CONTEXT, "payload": {}},
        hub_prs=hub_prs,
        org_prs=org_prs,
        claimed_issues=claimed_issues,
        issues=issues,
        comments=comments,
        mutation=mutation,
        env_extra=env_extra,
    )


def run_label_sync(
    *,
    pr,
    action="opened",
    issues=None,
    comments=None,
    hub_prs=None,
    list_comments_throws=False,
    mutation=None,
    script_mutation=None,
) -> dict:
    """Drive the event-driven label-sync step for one pull_request event.

    `comments` is keyed by number as usual — for this step the PR's own number
    is the interesting key, since a PR's comments live on the issues endpoint.
    """
    env_extra = {"PR_ACTION": action}
    if list_comments_throws:
        env_extra["TEST_LIST_COMMENTS_THROWS"] = "1"
    return _invoke(
        _mutate(
            step_github_script(WF_FILE, LABEL_SYNC_JOB, LABEL_SYNC_STEP),
            script_mutation,
            "label-sync",
        ),
        {"repo": HUB_CONTEXT, "payload": {"pull_request": pr}},
        hub_prs=hub_prs,
        issues=issues,
        comments=comments,
        mutation=mutation,
        env_extra=env_extra,
    )


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


def test_sweep_holds_a_pr_derived_claim_past_the_window_when_the_search_fails():
    # Copilot review on #941: the 48h backstop was still firing on a
    # marker-stamped claim when the search was unusable, so a cross-repo PR that
    # stays open longer than the expiry lost its claim to a transient search
    # failure — releasing a live claim on silence, the one thing this must not do.
    r = run_sweep(
        claimed_issues=[_claimed(934, _ago(days=3))],
        comments={"934": [{"body": f"claimed\n{MARKER}"}]},
        search_throws=True,
    )
    assert _released(r) == [], r
    # The provenance read is what makes the hold possible, so it must happen here
    # even though the issue is past the expiry.
    assert [c["issue_number"] for c in r["listCommentsCalls"]] == [934], r["listCommentsCalls"]


def test_sweep_still_claims_from_hub_prs_when_the_search_fails():
    # Copilot review on #941: hub PRs come from pulls.list and are authoritative
    # whether or not the search worked, so skipping the whole claim pass left a
    # hub issue unclaimed with an open hub PR referencing it (e.g. a hand-removed
    # label, with no PR event coming to re-add it).
    r = run_sweep(
        hub_prs=[{"number": 940, "body": "Closes #934"}],
        org_prs=[_search_item(TEMPLATE, 433, f"Refs {HUB}#893")],
        issues={"934": _open_issue(934), "893": _open_issue(893)},
        search_throws=True,
    )
    assert _labeled(r) == [934], "hub claims survive a failed search; cross-repo ones wait"


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
    assert (
        r["searchCalls"][0]["q"] == "org:FreeForCharity is:pr is:open -author:app/dependabot"
    ), r["searchCalls"]


# --- the query has to stay under the Search API's 1,000-result cap ----------


def test_sweep_excludes_non_claiming_authors_from_the_org_search():
    # The exclusion is the only thing keeping the population under the cap, so
    # it is asserted on the query itself and not just on the outcome.
    r = run_sweep(org_prs=[_search_item(TEMPLATE, 433, f"Refs {HUB}#934")], issues={})
    assert "-author:app/dependabot" in r["searchCalls"][0]["q"], r["searchCalls"]


def test_sweep_claims_across_a_fleet_sized_population():
    # The live shape on 2026-07-31: 1,009 open org PRs, 849 of them dependabot's.
    # Narrowed, the query matches 160 — a complete read, so the cross-repo claim
    # actually happens instead of being skipped.
    r = run_sweep(
        org_prs=_fleet_population(dependabot=849, other=160),
        issues={"934": _open_issue(934)},
    )
    assert _labeled(r) == [934], r["warnings"]
    assert r["warnings"] == [], "a healthy, complete read must warn about nothing"
    assert r["logs"], "the claim count is the acceptance signal, not the exit code"


def test_sweep_warns_before_the_narrowed_query_reaches_the_cap():
    # Still complete, still claiming — but the next growth step disables the pass
    # entirely, and that has to be visible before it happens rather than after.
    r = run_sweep(
        org_prs=_fleet_population(dependabot=0, other=850),
        issues={"934": _open_issue(934)},
    )
    assert _labeled(r) == [934], r
    assert any("of the 1000-result cap" in w for w in r["warnings"]), r["warnings"]
    assert not any("unusable" in w for w in r["warnings"]), r["warnings"]


def test_sweep_warns_at_exactly_the_headroom_boundary():
    # Pin the boundary: "warn at 800" means 800 warns, not 801. An off-by-one
    # here costs nothing today and one silent run when the fleet lands on it.
    r = run_sweep(
        org_prs=_fleet_population(dependabot=0, other=800),
        issues={"934": _open_issue(934)},
    )
    assert any("at 800 of the 1000-result cap" in w for w in r["warnings"]), r["warnings"]
    # ...and the boundary is load-bearing: a strict `>` goes quiet at exactly 800.
    strict = run_sweep(
        org_prs=_fleet_population(dependabot=0, other=800),
        issues={"934": _open_issue(934)},
        script_mutation=MUT_STRICT_HEADROOM,
    )
    assert strict["warnings"] == [], "the mutation must silence the boundary case"


def test_sweep_pages_the_org_search_at_the_declared_page_size():
    # The loop bound, the request and the last-page test all read one constant;
    # a fixture larger than one page proves the pager actually walks pages
    # rather than reading page 1 and calling it complete.
    r = run_sweep(
        org_prs=_fleet_population(dependabot=0, other=250),
        issues={"934": _open_issue(934)},
    )
    assert [c["per_page"] for c in r["searchCalls"]] == [100, 100, 100], r["searchCalls"]
    assert [c["page"] for c in r["searchCalls"]] == [1, 2, 3], r["searchCalls"]
    assert _labeled(r) == [934], r


def test_mutation_unnarrowed_query_is_truncated_and_claims_nothing():
    # Reintroduce the defect (#935): drop the author exclusion and the same fleet
    # population goes over the cap, the read comes back short, and the sweep
    # degrades to the no-op that #941's first live run produced.
    r = run_sweep(
        org_prs=_fleet_population(dependabot=849, other=160),
        issues={"934": _open_issue(934)},
        script_mutation=MUT_UNNARROWED_QUERY,
    )
    assert _labeled(r) == [], "the mutation must break the claim, or it proves nothing"
    assert any("truncated: 1000 of 1009" in w for w in r["warnings"]), r["warnings"]


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


# --- Part A: the PR-side claim notice (#948) -------------------------------
#
# `Refs #N` is both the claiming form and how people write a citation, and 737
# cannot tell them apart. It comments on the ISSUE, which the PR author is not
# watching — so a citation written as a claim hides a backlog pickup with nobody
# aware it happened. #945 was invisible for six hours behind a docs draft.

PR_NOTICE_MARKER = _node("process.stdout.write(JSON.stringify(l.PR_NOTICE_MARKER));")
NOTICE_945 = _node("process.stdout.write(JSON.stringify(l.prNoticeComment([945])));")


def _pr(number: int, body: str, **kw) -> dict:
    return {"number": number, "body": body, **kw}


def _notices(result: dict, pr_number: int) -> list[str]:
    return [c["body"] for c in result["comments"] if c["issue_number"] == pr_number]


def test_label_sync_tells_the_pr_what_it_just_claimed():
    # Criterion 1: exactly one comment, naming every issue claimed, carrying the
    # escape hatch.
    r = run_label_sync(
        pr=_pr(946, f"Refs #945\n\nAlso Closes {HUB}#900"),
        issues={"945": _open_issue(945), "900": _open_issue(900)},
    )
    assert sorted(_labeled(r)) == [900, 945], r
    notices = _notices(r, 946)
    assert len(notices) == 1, r["comments"]
    body = notices[0]
    assert "#945" in body and "#900" in body, body
    assert "instead of `Refs`/`Closes`" in body, body
    assert PR_NOTICE_MARKER in body, body


def test_label_sync_posts_nothing_when_the_pr_claims_nothing():
    # Criterion 3.
    r = run_label_sync(pr=_pr(946, "Fixes a typo. See the discussion in the ledger."))
    assert _labeled(r) == [] and r["comments"] == [], r


def test_label_sync_posts_nothing_when_the_issue_was_already_claimed():
    # This PR did not remove anything from the pickup query, so telling its
    # author it did would be false.
    r = run_label_sync(
        pr=_pr(946, "Refs #945"),
        issues={"945": _open_issue(945, labels=["claimed"])},
    )
    assert _labeled(r) == [] and r["comments"] == [], r


def test_label_sync_does_not_repeat_a_notice_it_already_posted():
    # Criterion 2. The realistic re-entry: someone removed the label by hand, so
    # the claim is re-applied on the next event — but the author has been told.
    r = run_label_sync(
        pr=_pr(946, "Refs #945"),
        action="edited",
        issues={"945": _open_issue(945)},
        comments={"946": [{"body": NOTICE_945}]},
    )
    assert _labeled(r) == [945], "the label itself is still restored"
    assert r["comments"] == [], "but the notice is not repeated"


def test_label_sync_announces_a_newly_added_claim_on_an_already_noticed_pr():
    # Idempotency is per ISSUE, not per comment: a PR that later claims a second
    # issue must still be told about that one.
    r = run_label_sync(
        pr=_pr(946, "Refs #945\nCloses #900"),
        action="edited",
        issues={"945": _open_issue(945, labels=["claimed"]), "900": _open_issue(900)},
        comments={"946": [{"body": NOTICE_945}]},
    )
    assert _labeled(r) == [900], r
    body = _notices(r, 946)[0]
    assert "#900" in body, body
    assert "945" not in body, "the already-announced issue must not be repeated"


def test_label_sync_skips_the_notice_when_the_pr_comments_cannot_be_read():
    # "Have I already said this?" is unanswerable, and a duplicate on every
    # future sync is worse than a missing convenience comment.
    r = run_label_sync(
        pr=_pr(946, "Refs #945"),
        issues={"945": _open_issue(945)},
        list_comments_throws=True,
    )
    assert _labeled(r) == [945], "the claim itself must still happen"
    assert r["comments"] == [], r["comments"]
    assert any("skipping the claim notice" in w for w in r["warnings"]), r["warnings"]


def test_label_sync_release_path_posts_no_pr_notice():
    # The notice belongs to the claim path only; closing still comments on the
    # ISSUE, exactly as before.
    r = run_label_sync(
        pr=_pr(946, "Refs #945", merged=True),
        action="closed",
        issues={"945": _open_issue(945, labels=["claimed"])},
    )
    assert _released(r) == [945], r
    assert [c["issue_number"] for c in r["comments"]] == [945], r["comments"]


def _long_thread(total: int, marker_at: str) -> list[dict]:
    """`total` comments on a PR, with the claim notice at one end.

    `marker_at` is "start" or "end" — the two places a notice can be. The first
    claim on a PR lands early in the thread; a claim added by a later edit lands
    last. A bounded read from page 1 only ever covers the first.
    """
    filler = [{"body": f"routine comment {i}"} for i in range(total - 1)]
    return [{"body": NOTICE_945}, *filler] if marker_at == "start" else [*filler, {"body": NOTICE_945}]


def test_label_sync_finds_a_notice_at_the_end_of_a_long_thread():
    # Copilot on #951: comments are served OLDEST-first, so pages 1..N read the
    # START of the thread. A notice posted after 300 comments sat beyond that
    # window and was re-announced on every later sync. Same prefix mistake
    # AGENTS.md records for long issues.
    r = run_label_sync(
        pr=_pr(946, "Refs #945"),
        action="edited",
        issues={"945": _open_issue(945)},
        comments={"946": _long_thread(350, marker_at="end")},
    )
    assert _labeled(r) == [945], r
    assert r["comments"] == [], "the notice is already at the end of the thread"
    # Bounded: page 1, then backwards from the last page. Never the whole thread.
    assert len(r["listCommentsCalls"]) <= 3, r["listCommentsCalls"]


def test_label_sync_still_finds_a_notice_at_the_start_of_a_long_thread():
    # The other direction, and why page 1 stays in the read: the FIRST claim on
    # a PR is announced early, and a backwards-only walk would miss it.
    r = run_label_sync(
        pr=_pr(946, "Refs #945"),
        action="edited",
        issues={"945": _open_issue(945)},
        comments={"946": _long_thread(350, marker_at="start")},
    )
    assert r["comments"] == [], "a notice at the head of the thread still counts"


def test_mutation_reading_only_the_first_page_re_announces_on_a_long_thread():
    r = run_label_sync(
        pr=_pr(946, "Refs #945"),
        action="edited",
        issues={"945": _open_issue(945)},
        comments={"946": _long_thread(350, marker_at="end")},
        script_mutation=MUT_FIRST_PAGE_ONLY,
    )
    assert _notices(r, 946), "the mutation must reintroduce the duplicate notice"


def test_mutation_dropping_the_notice_leaves_the_author_uninformed():
    r = run_label_sync(
        pr=_pr(946, "Refs #945"),
        issues={"945": _open_issue(945)},
        script_mutation=MUT_NO_PR_NOTICE,
    )
    assert _labeled(r) == [945], r
    assert r["comments"] == [], "the mutation must silence the notice, or it proves nothing"


def test_mutation_blinding_the_marker_check_repeats_the_notice():
    r = run_label_sync(
        pr=_pr(946, "Refs #945"),
        action="edited",
        issues={"945": _open_issue(945)},
        comments={"946": [{"body": NOTICE_945}]},
        mutation=MUT_NOTICE_BLIND,
    )
    assert _notices(r, 946), "the mutation must re-announce an issue already noticed"


# --- Part B: claims held only by a stale draft (#948) ----------------------
#
# Report only. A draft is not active work, but it suppresses a pickup exactly as
# hard as a ready PR — several of the drafts measured in #948 had held one for
# four to six days.

DRAFT_HEADING = "Claims held only by a draft PR older than 48h"


def _summary(result: dict) -> str:
    return "\n".join(result["summary"])


def test_sweep_reports_a_claim_held_only_by_an_old_draft():
    # Criterion 4: listed, with the age, and nothing released.
    r = run_sweep(
        org_prs=[_draft(TEMPLATE, 946, f"Refs {HUB}#945", days=4)],
        claimed_issues=[_claimed(945, _ago(hours=1))],
    )
    assert _released(r) == [], "report only — a long-lived draft is sometimes real work"
    assert r["comments"] == [], "and reporting must not comment on the issue"
    s = _summary(r)
    assert f"{DRAFT_HEADING} — 1" in s, s
    assert f"#945 — held only by draft {TEMPLATE}#946, 4d 0h old" in s, s


def test_sweep_reports_a_draft_claim_it_applied_in_the_same_run():
    # An issue claimed by this very sweep is suppressed from the pickup query
    # just as much as one that was already labeled.
    r = run_sweep(
        org_prs=[_draft(TEMPLATE, 946, f"Refs {HUB}#945", days=6)],
        issues={"945": _open_issue(945)},
    )
    assert _labeled(r) == [945], r
    assert "#945 — held only by draft" in _summary(r), _summary(r)


def test_sweep_does_not_report_a_claim_held_by_a_ready_pr():
    r = run_sweep(
        org_prs=[_ready(TEMPLATE, 946, f"Refs {HUB}#945", days=4)],
        claimed_issues=[_claimed(945, _ago(hours=1))],
    )
    assert f"{DRAFT_HEADING} — 0" in _summary(r), _summary(r)
    assert "None. Every claim is held by a ready PR" in _summary(r), _summary(r)


def test_sweep_does_not_report_a_young_draft():
    r = run_sweep(
        org_prs=[_draft(TEMPLATE, 946, f"Refs {HUB}#945", hours=5)],
        claimed_issues=[_claimed(945, _ago(hours=1))],
    )
    assert f"{DRAFT_HEADING} — 0" in _summary(r), _summary(r)


def test_sweep_does_not_report_an_issue_a_ready_pr_also_claims():
    # "ONLY claimant" is the whole rule: one ready PR alongside the old draft
    # means the work is in review.
    r = run_sweep(
        org_prs=[
            _draft(TEMPLATE, 946, f"Refs {HUB}#945", days=4),
            _ready(THIRD, 21, f"Closes {HUB}#945", days=4),
        ],
        claimed_issues=[_claimed(945, _ago(hours=1))],
    )
    assert f"{DRAFT_HEADING} — 0" in _summary(r), _summary(r)


def test_sweep_does_not_report_a_pr_with_an_unknown_draft_flag():
    # Every pre-existing fixture omits `draft`/`created_at`; unknown must read as
    # "not reportable" rather than defaulting either way.
    r = run_sweep(
        org_prs=[_search_item(TEMPLATE, 946, f"Refs {HUB}#945")],
        claimed_issues=[_claimed(945, _ago(hours=1))],
    )
    assert f"{DRAFT_HEADING} — 0" in _summary(r), _summary(r)


def test_sweep_skips_the_draft_report_when_the_org_search_is_unusable():
    # With the search down, `claimants` is hub PRs only — so "its ONLY claimant
    # is a draft" is a statement about a PR set this run could not read.
    r = run_sweep(
        hub_prs=[
            {"number": 946, "body": "Refs #945", "draft": True, "created_at": _ago(days=4)}
        ],
        claimed_issues=[_claimed(945, _ago(hours=1))],
        search_throws=True,
    )
    assert "Not reported this run" in _summary(r), _summary(r)
    assert _released(r) == [], r


def test_mutation_reporting_any_draft_flags_a_pr_opened_this_morning():
    r = run_sweep(
        org_prs=[_draft(TEMPLATE, 946, f"Refs {HUB}#945", hours=5)],
        claimed_issues=[_claimed(945, _ago(hours=1))],
        mutation=MUT_DRAFT_IGNORES_AGE,
    )
    assert f"{DRAFT_HEADING} — 1" in _summary(r), _summary(r)


def test_mutation_ignoring_the_draft_flag_flags_work_in_review():
    r = run_sweep(
        org_prs=[_ready(TEMPLATE, 946, f"Refs {HUB}#945", days=4)],
        claimed_issues=[_claimed(945, _ago(hours=1))],
        mutation=MUT_DRAFT_IGNORES_READY,
    )
    assert f"{DRAFT_HEADING} — 1" in _summary(r), _summary(r)


# --- the pure helpers behind both halves -----------------------------------


def test_notice_body_is_a_function_of_the_issue_set_not_its_order():
    a = _node("process.stdout.write(JSON.stringify(l.prNoticeComment([900,945,900])));")
    b = _node("process.stdout.write(JSON.stringify(l.prNoticeComment([945,900])));")
    assert a == b, (a, b)
    assert "#900, #945" in a, a


def test_noticed_issues_reads_back_every_marker():
    assert _node(
        "const c=l.prNoticeComment([945,900]);"
        "process.stdout.write(JSON.stringify(l.noticedIssues([{body:c}]).sort((x,y)=>x-y)));"
    ) == [900, 945]
    assert _node("process.stdout.write(JSON.stringify(l.noticedIssues(null)));") == []
    assert _node(
        "process.stdout.write(JSON.stringify("
        "l.noticedIssues([{body:'🔒 This PR claimed #945'}])));"
    ) == [], "prose naming an issue is not a marker"


def test_format_age_rounds_to_whole_hours_and_days():
    fmt = lambda ms: _node(  # noqa: E731
        f"process.stdout.write(JSON.stringify(l.formatAge({ms})));"
    )
    assert fmt(5 * 60 * 60 * 1000 + 59 * 60 * 1000) == "5h"
    assert fmt(4 * DAY) == "4d 0h"
    assert fmt(4 * DAY + 6 * 60 * 60 * 1000) == "4d 6h"
    assert _node("process.stdout.write(JSON.stringify(l.formatAge(NaN)));") == "unknown age"


def test_draft_only_claim_needs_a_usable_clock_and_a_non_empty_ref_set():
    call = lambda args: _node(  # noqa: E731
        "process.stdout.write(JSON.stringify(l.draftOnlyClaim("
        "JSON.parse(process.argv[1]).rows, JSON.parse(process.argv[1]).opts)));",
        json.dumps(args),
    )
    old = {"ref": "o/r#1", "draft": True, "createdAtMs": 0}
    assert call({"rows": [old], "opts": {"nowMs": 10 * DAY}})["refs"] == ["o/r#1"]
    assert call({"rows": [], "opts": {"nowMs": 10 * DAY}}) is None
    assert call({"rows": [old], "opts": {}}) is None, "no clock -> nothing to report"
    assert call({"rows": [{"ref": "o/r#1", "draft": True}], "opts": {"nowMs": 10 * DAY}}) is None


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
