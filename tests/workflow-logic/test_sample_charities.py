"""Reusable fictional-charity dataset: tests/fixtures/sample-charities.json.

The dataset is the shared input for testing website provisioning end to end
against EITHER template. This module keeps it honest and runs every charity
through the parts of 701 that can run offline:

- the dataset itself: every `inputs` key is a real 701 workflow_dispatch input
  (so it can be dispatched verbatim), every value is a string (MCP dispatch
  rejects anything else), and every value is visibly fictional so a test run
  can never provision, email or link a real organization;
- 701's real `resolve` parse script (extracted from the YAML), which must
  accept every charity and pass its fields through unchanged;
- scripts/Apply-WebsiteReactTemplate.ps1, fed from those parse outputs the way
  701's content job feeds it, against the config-driven fixture repo from
  test_701_apply_website_template.py; each charity's `expect` block is checked.

Workflow 747 applies the same dataset to the REAL templates and runs each
template's own CI; this module is the fast, offline half that runs on every PR.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_701_apply_website_template import make_repo, read, run_apply  # noqa: E402
from test_701_parse import ctx, run_parse  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
DATASET = ROOT / "tests" / "fixtures" / "sample-charities.json"
WORKFLOW = ROOT / ".github" / "workflows" / "701-website-provision.yml"


def load() -> list[dict]:
    return json.loads(DATASET.read_text(encoding="utf-8"))["charities"]


def dispatch_inputs() -> dict:
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return doc[True]["workflow_dispatch"]["inputs"]


def parse(charity: dict) -> dict:
    r = run_parse(ctx("workflow_dispatch", {"inputs": charity["inputs"]}))
    assert r["failed"] is None and r["threw"] is None, (charity["id"], r)
    return r["outputs"]


def test_the_dataset_is_not_empty_and_ids_are_unique():
    charities = load()
    assert len(charities) >= 3, len(charities)
    ids = [c["id"] for c in charities]
    assert len(ids) == len(set(ids)), ids
    domains = [c["inputs"]["domain"] for c in charities]
    assert len(domains) == len(set(domains)), domains


def test_every_input_is_a_real_701_dispatch_input_given_as_a_string():
    declared = dispatch_inputs()
    for c in load():
        for key, value in c["inputs"].items():
            assert key in declared, f"{c['id']}: {key!r} is not a 701 workflow_dispatch input"
            assert isinstance(value, str), f"{c['id']}: {key} must be a string for dispatch"
            options = declared[key].get("options")
            if options and value:
                assert value in options, f"{c['id']}: {key}={value!r} is not one of {options}"


def test_every_charity_is_visibly_fictional():
    # A test run creates real repos and Pages sites. Nothing in the dataset may
    # be able to reach, or be mistaken for, a real organization.
    for c in load():
        i = c["inputs"]
        assert i["domain"].startswith("ffc-test-"), (c["id"], i["domain"])
        assert i["footer_email"].endswith(".example"), (c["id"], i["footer_email"])
        digits = re.sub(r"\D", "", i["footer_phone"])[-10:]
        assert digits[3:6] == "010" and digits.startswith("555"), (c["id"], i["footer_phone"])
        assert re.fullmatch(r"(00|99)-\d{7}", i["footer_ein"]), (c["id"], i["footer_ein"])
        for url in re.findall(r"https://\S+", " ".join(i.values())):
            assert "ffc-test" in url.lower() or "ffctest" in url.lower() or url.endswith(
                i["footer_ein"]
            ), (c["id"], url)


def test_the_dataset_covers_both_link_outcomes_and_both_candid_paths():
    expects = [c["expect"] for c in load()]
    assert {e["donate"] for e in expects} == {"url", "mailto"}, expects
    assert {e["volunteer"] for e in expects} == {"url", "mailto"}, expects
    assert {e["candid"] for e in expects} == {"given", "derived-from-ein"}, expects


def test_701_resolve_accepts_every_charity_and_passes_the_footer_fields_through():
    for c in load():
        out = parse(c)
        i = c["inputs"]
        assert out["domain"] == i["domain"], (c["id"], out)
        assert out["template_repo"] == "FreeForCharity/FFC-IN-Footer_Only_Template", out
        assert out["charity_name"] == i["charity_name"], (c["id"], out)
        # A multi-line mission reaches the footer as one line.
        assert out["mission"] == re.sub(r"\s*\r?\n\s*", " ", i["mission"].strip()), (c["id"], out)
        assert out["donation_url"] == i["donation_url"], (c["id"], out)
        assert out["volunteer_url"] == i["volunteer_url"], (c["id"], out)


def script_args(c: dict) -> dict:
    """The content script's arguments, built from 701's parse outputs the way
    701's content job builds them (JSON outputs decoded, as ConvertFrom-Json does)."""
    out = parse(c)
    return {
        "Domain": out["domain"],
        "CharityName": out["charity_name"],
        "FooterEmail": out["footer_email"],
        "FooterPhone": out["footer_phone"],
        "FooterAddress": json.loads(out["footer_address_json"]),
        "FooterEin": out["footer_ein"],
        "GuideStarProfileUrl": out.get("guidestar_profile_url", ""),
        "GuideStarDirectProfileUrl": out.get("guidestar_direct_profile_url", ""),
        "FooterSocial": json.loads(out["footer_social_json"]),
        "LeadershipLines": json.loads(out["leadership_json"]),
        "Mission": out["mission"],
        "DonationUrl": out["donation_url"],
        "VolunteerUrl": out["volunteer_url"],
        "IrsStatus": out.get("irs_status", ""),
    }


def apply_charity(c: dict):
    td = pathlib.Path(__import__("tempfile").mkdtemp())
    repo = make_repo(td)
    return td, repo, run_apply(repo, script_args(c))


def test_the_content_script_renders_every_charity_as_expected():
    for c in load():
        td, repo, proc = apply_charity(c)
        try:
            i, e = c["inputs"], c["expect"]
            assert proc.returncode == 0, (c["id"], proc.stdout + proc.stderr)
            cfg = read(repo, "src/lib/site.config.ts")
            assert "46-2471893" not in cfg and "clarkemoyer@" not in cfg, (c["id"], cfg)
            assert f"contactEmail: '{i['footer_email']}'," in cfg, (c["id"], cfg)

            donation = re.search(r"donationUrl: '([^']*)',", cfg).group(1)
            volunteer = re.search(r"volunteerUrl: '([^']*)',", cfg).group(1)
            assert (donation != "") == (e["donate"] == "url"), (c["id"], donation)
            assert (volunteer != "") == (e["volunteer"] == "url"), (c["id"], volunteer)

            assert f"twitterHandle: '{e['twitter_handle']}'," in cfg, (c["id"], cfg)
            assert f"taxStatusLabel: '{e['tax_status_label']}'," in cfg, (c["id"], cfg)

            profile = re.search(r"profileUrl: '([^']*)',", cfg).group(1)
            if e["candid"] == "derived-from-ein":
                assert profile == f"https://www.guidestar.org/profile/{i['footer_ein']}", profile
            else:
                assert profile == i["guidestar_profile_url"], profile

            members = sorted((repo / "src" / "data" / "team").glob("*.json"))
            assert len(members) == e["team_size"], (c["id"], [m.name for m in members])
            for m in members:
                data = json.loads(m.read_text(encoding="utf-8"))
                assert set(data) <= {"name", "role", "linkedinUrl"} and data["role"], data
        finally:
            shutil.rmtree(td)


NEEDS_PWSH = {"test_the_content_script_renders_every_charity_as_expected"}

TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    have_pwsh = shutil.which("pwsh") is not None
    failures = 0
    for t in TESTS:
        if t.__name__ in NEEDS_PWSH and not have_pwsh:
            print(f"  SKIP {t.__name__} (pwsh not installed; runs in CI)")
            continue
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {str(e)[:600]}")
    sys.exit(1 if failures else 0)
