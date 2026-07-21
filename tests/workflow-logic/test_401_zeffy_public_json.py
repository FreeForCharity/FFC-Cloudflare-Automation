"""Unit tests for the 401 Zeffy public-JSON transform (scripts/zeffy-public-json-lib.js).

The workflow's `deliver` job runs `scripts/zeffy-campaigns-to-public-json.js`,
which is a thin wrapper over this library, so testing the library functions
directly tests the shipped transform: CSV parsing, the strict title/url/status
allowlist (no financial/date/PII leakage), URL dedupe/sort, and deterministic
serialization. A shape test also guards the workflow wiring itself.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import find_step, load_workflow

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
LIB = REPO_ROOT / "scripts" / "zeffy-public-json-lib.js"
WF_FILE = "401-zeffy-campaigns-export.yml"
OUTPUT_PATH = "docs/data/ffc-zeffy-campaigns.json"

# A CSV in the exact shape emitted by scripts/zeffy-campaigns-export.ps1:
# header plus financial (target_cents, volume, ...) and date columns that MUST
# NOT survive into the public file. Rows are deliberately out of URL order and
# include a duplicate URL, an empty-URL row, and a title containing a comma.
FIXTURE_CSV = (
    "id,type,category,status,title,created,updated,url,currency,target_cents,"
    "goal_cents,volume_cents,volume,is_archived,start_date,end_date,occurrence_count\r\n"
    'c2,ticketing,event,active,"Gala, Annual",1700000000,1700000001,'
    "https://www.zeffy.com/ticketing/free-for-charity-annual-gala,USD,500000,"
    '500000,120000,1200.00,False,,,0\r\n'
    "c1,donation-form,donation,active,Endowment Fund,1700000000,1700000001,"
    "https://www.zeffy.com/donation-form/free-for-charity-endowment-fund,USD,0,"
    "0,0,0,False,,,0\r\n"
    "c3,donation-form,donation,archived,Old Form,1700000000,1700000001,"
    "https://www.zeffy.com/donation-form/free-for-charity-endowment-fund,USD,0,"
    "0,0,0,True,,,0\r\n"
    "c4,donation-form,donation,draft,No URL Yet,1700000000,1700000001,"
    ",USD,0,0,0,0,False,,,0\r\n"
)


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


def build(csv_text: str) -> list:
    return _node(
        "process.stdout.write(JSON.stringify(l.buildPublicCampaigns(process.argv[1]||'')));",
        csv_text,
    )


def serialized(csv_text: str) -> str:
    return _node(
        "process.stdout.write(JSON.stringify(l.serialize(l.buildPublicCampaigns(process.argv[1]||''))));",
        csv_text,
    )


# --- transform correctness --------------------------------------------------


def test_only_title_url_status_survive():
    rows = build(FIXTURE_CSV)
    assert rows, "expected at least one campaign"
    for entry in rows:
        assert set(entry.keys()) == {"title", "url", "status"}, entry


def test_financial_and_date_columns_never_leak():
    text = serialized(FIXTURE_CSV)
    for banned in (
        "target_cents",
        "goal_cents",
        "volume_cents",
        '"volume"',
        "occurrence_count",
        "created",
        "is_archived",
        "currency",
    ):
        assert banned not in text, f"{banned!r} leaked into public JSON: {text}"


def test_dedupe_by_url_first_wins():
    rows = build(FIXTURE_CSV)
    urls = [r["url"] for r in rows]
    assert len(urls) == len(set(urls)), urls
    # The endowment form appears twice (active then archived); first (active) wins.
    endowment = [
        r for r in rows if r["url"].endswith("free-for-charity-endowment-fund")
    ]
    assert len(endowment) == 1
    assert endowment[0]["status"] == "active"


def test_empty_url_rows_dropped():
    rows = build(FIXTURE_CSV)
    assert all(r["url"] for r in rows)
    assert not any(r["title"] == "No URL Yet" for r in rows)


def test_sorted_by_url():
    rows = build(FIXTURE_CSV)
    urls = [r["url"] for r in rows]
    assert urls == sorted(urls), urls


def test_quoted_comma_in_title_preserved():
    rows = build(FIXTURE_CSV)
    gala = [r for r in rows if "annual-gala" in r["url"]]
    assert len(gala) == 1
    assert gala[0]["title"] == "Gala, Annual"


def test_lf_line_endings_also_parse():
    lf = FIXTURE_CSV.replace("\r\n", "\n")
    assert build(lf) == build(FIXTURE_CSV)


def test_serialize_is_deterministic_with_trailing_newline():
    once = serialized(FIXTURE_CSV)
    twice = serialized(FIXTURE_CSV)
    assert once == twice
    assert once.endswith("\n")


def test_empty_input_yields_empty_array():
    assert build("") == []
    assert build("id,url,status,title\r\n") == []


# --- workflow wiring shape ---------------------------------------------------


def test_deliver_job_wired():
    wf = load_workflow(WF_FILE)
    jobs = wf.get("jobs", {})
    assert "deliver" in jobs, list(jobs)
    deliver = jobs["deliver"]
    assert deliver.get("environment") == "github-prod"
    assert "zeffy_campaigns_export" in deliver.get("needs", [])


def test_deliver_runs_the_transform_to_the_public_path():
    step = find_step(load_workflow(WF_FILE), "deliver", "Generate public campaign list")
    run = step["run"]
    assert "scripts/zeffy-campaigns-to-public-json.js" in run
    assert OUTPUT_PATH in run


def test_deliver_opens_pr_scoped_to_the_public_file():
    step = find_step(load_workflow(WF_FILE), "deliver", "Open data update PR")
    assert "peter-evans/create-pull-request" in step.get("uses", "")
    assert OUTPUT_PATH in str(step["with"]["add-paths"])


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
