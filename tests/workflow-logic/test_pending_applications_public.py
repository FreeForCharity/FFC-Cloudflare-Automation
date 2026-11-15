"""Guards for the public pending-applications list
(scripts/pending-applications-public.mjs -> sites-list/pending_applications.*).

What these protect:
  - every published score matches the gap list beside it ("14 of 20" means
    exactly 6 missing labels);
  - no score is published when 232 ran without the completeness review, since
    "no code raised" would then read as "done" for every item;
  - fraud, duplicate and EIN-identity codes never reach the public file;
  - ranking follows FFC priority (food, water or shelter; veterans; others);
  - 703 actually runs the generator and commits its output.

Run: python3 tests/workflow-logic/test_pending_applications_public.py
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import find_step, load_workflow, step_run

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
LIB = REPO_ROOT / "scripts" / "pending-applications-public.mjs"
NODE = shutil.which("node") or "node"

FULL_CATALOG = {k: {"severity": "NEED_INFO", "description": ""} for k in ("ORG_LEGAL_NAME_MISSING", "MISSION_MISSING")}


def _row(order_id, pid=16, verdict="NEED_INFO", codes=(), org="Example Charity", domain="", gate=1):
    return {
        "order_id": order_id,
        "client_id": order_id + 1000,
        "pid": pid,
        "gate": gate,
        "org_name": org,
        "desired_domain": domain,
        "verdict": verdict,
        "reason_codes": list(codes),
        "readiness_score": None,
        "fraud_check": "pass",
        "fraud_score": 1,
    }


def _report(rows, catalog=None):
    return {
        "schema": "charity-lane-verdicts/v2",
        "generated_at": "2026-11-13T00:00:00Z",
        "run_url": "https://example.invalid/run/1",
        "verdicts": rows,
        "reason_code_catalog": FULL_CATALOG if catalog is None else catalog,
    }


def _build(report, curated_csv="order_id,category,ffc_repo,note\n", repos=()):
    code = (
        f"import({json.dumps(LIB.as_uri())}).then((l)=>{{"
        "const a=JSON.parse(process.argv[1]);"
        "const doc=l.buildPendingApplications(a.report,{curated:l.loadCurated(a.curated),repos:new Set(a.repos),now:'2026-11-13T00:00:00Z'});"
        "process.stdout.write(JSON.stringify({doc,csv:l.toCsv(doc)}));"
        "}).catch((e)=>{console.error(e);process.exit(2);});"
    )
    payload = json.dumps({"report": report, "curated": curated_csv, "repos": list(repos)})
    proc = subprocess.run([NODE, "--input-type=module", "-e", code, payload], capture_output=True, text=True, encoding="utf-8", env=dict(os.environ))
    assert proc.returncode == 0, f"node exited {proc.returncode}: {proc.stderr[:600]}"
    return json.loads(proc.stdout)


def test_score_matches_the_missing_list():
    out = _build(_report([_row(1, codes=["MISSION_MISSING", "TAGLINE_MISSING", "FACEBOOK_PAGE_NOT_PUBLIC"])]))
    app = out["doc"]["applications"][0]
    assert app["items_total"] == 20, app
    assert app["items_done"] == 17, app
    assert app["score"] == "17 of 20", app
    assert app["missing"] == ["Mission", "Tagline", "Facebook Page"], app
    assert app["items_total"] - app["items_done"] == len(app["missing"])


def test_501c3_track_has_its_four_extra_items():
    out = _build(_report([_row(2, pid=33, codes=["DONATION_URL_MISSING"])]))
    app = out["doc"]["applications"][0]
    assert app["track"] == "501(c)(3)"
    assert app["score"] == "23 of 24", app
    assert app["missing"] == ["Zeffy donation link"]


def test_two_codes_for_one_item_count_once():
    out = _build(_report([_row(3, codes=["OFFICER_NAMES_MISSING", "OFFICERS_NOT_DISTINCT"])]))
    app = out["doc"]["applications"][0]
    assert app["missing"] == ["Three different officers"], app
    assert app["items_done"] == 19


def test_no_score_without_the_completeness_review():
    # A 232 report from before #1747 has no completeness codes in its catalog:
    # publishing a score from it would call every unchecked item done.
    out = _build(_report([_row(4, codes=["FACEBOOK_URL_MISSING"])], catalog={"FACEBOOK_URL_MISSING": {}}))
    app = out["doc"]["applications"][0]
    assert out["doc"]["completeness_review"] is False
    assert app["score"] == "Not yet checked" and app["coverage_percent"] is None and app["missing"] == [], app


def test_a_row_marked_not_checked_gets_no_score():
    out = _build(_report([_row(5, verdict="ESCALATE", codes=["COMPLETENESS_NOT_CHECKED"])]))
    assert out["doc"]["applications"][0]["score"] == "Not yet checked"


def test_sensitive_codes_never_reach_the_public_file():
    codes = ["FRAUD_FLAGGED", "DUPLICATE_ORG_OTHER_CLIENT", "EIN_NAME_MISMATCH", "CANDID_EIN_MISMATCH"]
    out = _build(_report([_row(6, pid=33, verdict="ESCALATE", codes=codes)]))
    blob = json.dumps(out["doc"]) + out["csv"]
    for c in codes + ["FRAUD", "fraud_score", "client_id", "reason_codes"]:
        assert c not in blob, f"{c!r} leaked into the public output"
    assert out["doc"]["applications"][0]["status"] == "Under FFC review"


def test_withheld_names_stay_withheld():
    out = _build(_report([_row(7, org="", codes=["ORG_NAME_WITHHELD"]), _row(8, org="")]))
    names = {a["order_id"]: a["charity"] for a in out["doc"]["applications"]}
    assert names == {7: "(name withheld)", 8: "(name not given)"}, names


def test_only_gate_1_onboarding_orders_are_listed():
    out = _build(_report([_row(9), _row(10, pid=40, gate=2)]))
    assert [a["order_id"] for a in out["doc"]["applications"]] == [9]


def test_ranking_is_priority_then_score():
    rows = [
        _row(20, codes=[]),
        _row(21, codes=["MISSION_MISSING", "TAGLINE_MISSING"]),
        _row(22, codes=["MISSION_MISSING", "TAGLINE_MISSING", "PUBLIC_PHONE_MISSING"]),
        _row(23, codes=["MISSION_MISSING"]),
    ]
    curated = "order_id,category,ffc_repo,note\n22,food_water_shelter,,\n21,veterans,,\n"
    out = _build(_report(rows), curated)
    order = [a["order_id"] for a in out["doc"]["applications"]]
    assert order == [22, 21, 20, 23], order
    assert [a["rank"] for a in out["doc"]["applications"]] == [1, 2, 3, 4]


def test_repo_link_from_curation_or_from_the_desired_domain():
    rows = [_row(30, domain="example.org"), _row(31, domain="other.org")]
    curated = "order_id,category,ffc_repo,note\n31,other,FFC-EX-renamed.org,Site built under a new domain\n"
    out = _build(_report(rows), curated, repos=["FFC-EX-example.org"])
    links = {a["order_id"]: a["ffc_repo"] for a in out["doc"]["applications"]}
    assert links[30] == "https://github.com/FreeForCharity/FFC-EX-example.org"
    assert links[31] == "https://github.com/FreeForCharity/FFC-EX-renamed.org"


def test_csv_quotes_commas_and_lists_every_gap():
    out = _build(_report([_row(40, org="Food, Water & More", codes=["MISSION_MISSING", "TAGLINE_MISSING"])]))
    lines = out["csv"].strip().split("\n")
    assert lines[0].startswith("Rank,Order,Charity,Domain,Track,Category,Status,Score,Coverage %,Missing")
    assert '"Food, Water & More"' in lines[1]
    assert "Mission; Tagline" in lines[1]


def test_curated_file_parses_and_names_only_known_categories():
    text = (REPO_ROOT / "sites-list" / "pending_applications_curated.csv").read_text(encoding="utf-8")
    header = text.splitlines()[0]
    assert header == "order_id,category,ffc_repo,note", header
    known = {"food_water_shelter", "veterans", "other", "unclassified"}
    for line in text.splitlines()[1:]:
        if not line.strip():
            continue
        cat = line.split(",")[1]
        assert cat in known, f"unknown category {cat!r} in: {line}"


def test_cli_writes_both_files():
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = pathlib.Path(tmp)
        rep = tmpdir / "verdicts.json"
        rep.write_text(json.dumps(_report([_row(50)])), encoding="utf-8")
        proc = subprocess.run(
            [NODE, str(LIB), "--verdicts", str(rep), "--out-dir", str(tmpdir / "out"), "--curated", str(tmpdir / "none.csv")],
            capture_output=True, text=True, encoding="utf-8", env=dict(os.environ),
        )
        assert proc.returncode == 0, proc.stderr
        assert "pending applications: 1" in proc.stdout, proc.stdout
        doc = json.loads((tmpdir / "out" / "pending_applications.json").read_text(encoding="utf-8"))
        assert doc["schema"] == "pending-applications/v1"
        assert (tmpdir / "out" / "pending_applications.csv").read_text(encoding="utf-8").startswith("Rank,")


def test_703_runs_the_generator_and_commits_its_output():
    wf_file = "703-sites-list-generate.yml"
    body = step_run(wf_file, "generate", "Generate pending applications")
    assert "scripts/pending-applications-public.mjs" in body
    assert "charity-lane-verdicts.json" in body
    collect = step_run(wf_file, "generate", "Collect export artifacts")
    assert "232-whmcs-charity-lane-verdicts.yml" in collect, "703 no longer dispatches/downloads 232"
    pr = find_step(load_workflow(wf_file), "generate", "Open data update PR")
    paths = (pr.get("with") or {}).get("add-paths", "")
    for f in ("sites-list/pending_applications.csv", "sites-list/pending_applications.json"):
        assert f in paths, f"703's data PR does not commit {f}"


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
