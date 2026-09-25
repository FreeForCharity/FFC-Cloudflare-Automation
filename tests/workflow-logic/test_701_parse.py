"""Unit tests for the 701 website-provision resolve/parse script.

Covers the three event paths (workflow_dispatch, repository_dispatch,
issues) end to end at the parse level — the layer where every prior
silent failure in this workflow started (refs issue #419).
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import child_env, step_github_script

HARNESS = pathlib.Path(__file__).resolve().parent / "harness" / "github_script_shim.mjs"


def run_parse(context: dict) -> dict:
    script = step_github_script("701-website-provision.yml", "resolve", "Parse Website Request")
    with tempfile.TemporaryDirectory() as td:
        script_file = pathlib.Path(td) / "script.js"
        context_file = pathlib.Path(td) / "context.json"
        script_file.write_text(script, encoding="utf-8")
        context_file.write_text(json.dumps(context), encoding="utf-8")
        proc = subprocess.run(
            ["node", str(HARNESS)],
            env=child_env(
                TEST_SCRIPT_FILE=str(script_file), TEST_CONTEXT_FILE=str(context_file)
            ),
            capture_output=True,
            text=True, encoding="utf-8",
            timeout=60,
        )
    if proc.returncode != 0:
        raise AssertionError(f"harness crashed: {proc.stderr}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def ctx(event_name: str, payload: dict, actor: str = "test-actor") -> dict:
    return {
        "eventName": event_name,
        "actor": actor,
        "payload": payload,
        "repo": {"owner": "FreeForCharity", "repo": "FFC-Cloudflare-Automation"},
    }


def test_manual_dispatch_valid_domain():
    r = run_parse(ctx("workflow_dispatch", {"inputs": {"domain": "example.org"}}))
    assert r["failed"] is None, r
    assert r["threw"] is None, r
    assert r["outputs"]["domain"] == "example.org", r
    assert r["outputs"]["repo_name"] == "FFC-EX-example.org", r
    assert r["outputs"].get("skip") != "true", r
    # Manual runs must never try to comment on a (nonexistent) issue.
    assert r["outputs"]["post_comments"] == "false", r


def test_manual_dispatch_strips_scheme_and_www():
    r = run_parse(ctx("workflow_dispatch", {"inputs": {"domain": "https://www.example.org/"}}))
    assert r["failed"] is None, r
    assert r["outputs"]["domain"] == "example.org", r


def test_repository_dispatch_reads_client_payload():
    payload = {
        "action": "ffcadmin-website-provision",
        "client_payload": {
            "domain": "charity.org",
            "technical_poc_github_username": "@some-user",
            "charity_title": "Some Charity",  # extra keys must be ignored, not fatal
            "ffcadmin_issue": 123,  # non-string values must coerce safely
        },
    }
    r = run_parse(ctx("repository_dispatch", payload))
    assert r["failed"] is None, r
    assert r["threw"] is None, r
    assert r["outputs"]["domain"] == "charity.org", r
    assert r["outputs"]["repo_name"] == "FFC-EX-charity.org", r
    assert r["outputs"]["technical_poc_github_username"] == "some-user", r
    assert r["outputs"]["post_comments"] == "false", r


def test_repository_dispatch_missing_domain_fails_loudly():
    r = run_parse(ctx("repository_dispatch", {"client_payload": {"sponsor": "someone"}}))
    assert r["failed"] is not None and "Missing required field: domain" in r["failed"], r


def test_repository_dispatch_empty_payload_fails_loudly():
    r = run_parse(ctx("repository_dispatch", {}))
    assert r["failed"] is not None and "Missing required field: domain" in r["failed"], r
    assert r["threw"] is None, r


def test_issues_event_non_website_request_skips():
    payload = {
        "issue": {
            "number": 42,
            "title": "Unrelated bug report",
            "body": "something else",
            "labels": [],
            "user": {"login": "reporter"},
        }
    }
    r = run_parse(ctx("issues", payload))
    assert r["outputs"].get("skip") == "true", r
    assert r["outputs"].get("post_comments") == "false", r
    assert r["failed"] is None, r


def test_manual_dispatch_carries_domain_registrar():
    r = run_parse(
        ctx(
            "workflow_dispatch",
            {"inputs": {"domain": "example.org", "domain_registrar": "Wix"}},
        )
    )
    assert r["failed"] is None, r
    assert r["outputs"]["domain_registrar"] == "Wix", r


def test_domain_registrar_defaults_to_empty_when_absent():
    # The field is optional; omitting it must not fail the parse.
    r = run_parse(ctx("workflow_dispatch", {"inputs": {"domain": "example.org"}}))
    assert r["failed"] is None, r
    assert r["outputs"]["domain_registrar"] == "", r


def test_domain_registrar_label_matches_the_issue_template():
    # The issue path reads this field by its rendered label. If the template
    # label and the extractSection() argument ever drift apart the field
    # silently parses as empty -- which is exactly how a Wix domain would slip
    # through intake unnoticed, the failure this field exists to prevent.
    import re

    import yaml

    root = pathlib.Path(__file__).resolve().parents[2]
    label = "Domain Registrar (who holds the domain today)"

    workflow = (root / ".github" / "workflows" / "701-website-provision.yml").read_text(
        encoding="utf-8"
    )
    assert f"extractSection('{label}')" in workflow, "parser does not read the registrar label"

    template = yaml.safe_load(
        (root / ".github" / "ISSUE_TEMPLATE" / "02-website-request.yml").read_text(
            encoding="utf-8"
        )
    )
    fields = [
        b for b in template["body"] if b.get("type") in ("input", "dropdown", "textarea")
    ]
    field = next(
        (f for f in fields if f["attributes"].get("label") == label), None
    )
    assert field is not None, f"issue template is missing the {label!r} field"

    # The option list must stay in sync with the registrar families the inbound
    # preflight knows how to classify.
    options = " ".join(field["attributes"]["options"]).lower()
    for family in ("wix", "squarespace", "godaddy"):
        assert family in options, f"registrar dropdown lost the {family} option"
    assert re.search(r"enom|cloudflare", options), "dropdown lost the already-FFC option"


def test_footer_mission_and_links_from_manual_dispatch():
    r = run_parse(
        ctx(
            "workflow_dispatch",
            {
                "inputs": {
                    "domain": "example.org",
                    "mission": "We feed families.\nEvery week.",
                    "donation_url": "https://www.zeffy.com/donate/example",
                    "volunteer_url": "",
                }
            },
        )
    )
    assert r["failed"] is None, r
    # A pasted multi-line mission collapses to the single footer line.
    assert r["outputs"]["mission"] == "We feed families. Every week.", r
    assert r["outputs"]["donation_url"] == "https://www.zeffy.com/donate/example", r
    # Blank is valid: the footer link then emails the charity.
    assert r["outputs"]["volunteer_url"] == "", r


def test_footer_link_must_be_https():
    r = run_parse(
        ctx(
            "workflow_dispatch",
            {"inputs": {"domain": "example.org", "volunteer_url": "http://example.org/help"}},
        )
    )
    assert r["failed"] and "Volunteer Page URL must start with https://" in r["failed"], r


def test_footer_fields_from_repository_dispatch_payload():
    payload = {
        "action": "ffcadmin-website-provision",
        "client_payload": {"domain": "charity.org", "mission": "We shelter families."},
    }
    r = run_parse(ctx("repository_dispatch", payload))
    assert r["failed"] is None, r
    assert r["outputs"]["mission"] == "We shelter families.", r
    assert r["outputs"]["donation_url"] == "", r


def test_footer_fields_from_issue_form():
    body = "\n\n".join(
        [
            "### Website Domain (no http://)\n\nexample.org",
            "### Mission Statement (one sentence)\n\nWe feed families.",
            "### Donation Page URL (optional)\n\nhttps://www.zeffy.com/donate/example",
            "### Volunteer Page URL (optional)\n\n_No response_",
        ]
    )
    payload = {
        "action": "assigned",
        "issue": {
            "number": 7,
            "title": "[WEBSITE REQUEST] example.org",
            "body": body,
            "labels": [{"name": "website-request"}, {"name": "admin-provision"}],
            "user": {"login": "reporter"},
        },
    }
    r = run_parse(ctx("issues", payload))
    assert r["failed"] is None, r
    assert r["outputs"]["mission"] == "We feed families.", r
    assert r["outputs"]["donation_url"] == "https://www.zeffy.com/donate/example", r
    assert r["outputs"]["volunteer_url"] == "", r


def test_footer_field_labels_match_the_issue_template():
    # The issue path reads these by rendered label; a drift between template
    # and parser would silently drop the charity's mission and links.
    import yaml

    root = pathlib.Path(__file__).resolve().parents[2]
    workflow = (root / ".github" / "workflows" / "701-website-provision.yml").read_text(
        encoding="utf-8"
    )
    template = yaml.safe_load(
        (root / ".github" / "ISSUE_TEMPLATE" / "02-website-request.yml").read_text(
            encoding="utf-8"
        )
    )
    labels = {b["attributes"].get("label") for b in template["body"] if b.get("type") == "input"}
    for label in (
        "Mission Statement (one sentence)",
        "Donation Page URL (optional)",
        "Volunteer Page URL (optional)",
    ):
        assert f"extractSection('{label}')" in workflow, f"parser does not read {label!r}"
        assert label in labels, f"issue template is missing the {label!r} field"


def test_default_template_is_footer_only():
    r = run_parse(ctx("workflow_dispatch", {"inputs": {"domain": "example.org"}}))
    assert r["outputs"]["template_repo"] == "FreeForCharity/FFC-IN-Footer_Only_Template", r


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
