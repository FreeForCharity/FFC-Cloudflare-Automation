"""Unit tests for check_provisioned_build.py, the built-page check workflow 748
runs after each provisioned site's build (FreeForCharity/FFC-Cloudflare-Automation#1391).

Each case writes a tiny fake static export and asserts the checker's verdict,
so the rule is pinned without a template checkout, a pnpm install or a build.
"""

from __future__ import annotations

import pathlib
import shutil
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from check_provisioned_build import findings  # noqa: E402
from test_sample_charities import load  # noqa: E402

CHARITIES = {c["id"]: c for c in load()}
FFC_ZEFFY = "https://www.zeffy.com/embed/donation-form/free-for-charity-endowment-fund"


def run(charity_id: str, pages: dict[str, str]) -> list[str]:
    td = pathlib.Path(tempfile.mkdtemp())
    try:
        for rel, body in pages.items():
            f = td / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(body, encoding="utf-8")
        return findings(CHARITIES[charity_id], td)
    finally:
        shutil.rmtree(td)


def own_links(charity_id: str) -> str:
    i = CHARITIES[charity_id]["inputs"]
    return f'<a href="{i["donation_url"]}">Donate</a><a href="{i["volunteer_url"]}">Volunteer</a>'


def test_the_charitys_own_links_pass():
    assert run("riverbend-pantry", {"index.html": own_links("riverbend-pantry")}) == []


def test_mailto_fallbacks_pass_when_the_charity_gave_no_pages():
    email = CHARITIES["st-marys-shelter"]["inputs"]["footer_email"]
    body = (
        f'<a href="mailto:{email}?subject=Donating%20to%20St.%20Mary&#x27;s">Donate</a>'
        f'<a href="mailto:{email}?subject=Volunteering%20with%20St.%20Mary&#x27;s">Volunteer</a>'
    )
    assert run("st-marys-shelter", {"index.html": body}) == []


def test_ffcs_donation_form_on_a_charity_page_fails():
    body = own_links("riverbend-pantry") + f'<iframe src="{FFC_ZEFFY}"></iframe>'
    problems = run("riverbend-pantry", {"index.html": body})
    assert len(problems) == 1 and "free-for-charity-endowment-fund" in problems[0], problems


def test_ffcs_own_donation_policy_page_is_exempt():
    pages = {
        "index.html": own_links("riverbend-pantry"),
        "free-for-charity-donation-policy.html": f'<a href="{FFC_ZEFFY}">FFC</a>',
        "free-for-charity-donation-policy/index.html": f'<a href="{FFC_ZEFFY}">FFC</a>',
    }
    assert run("riverbend-pantry", pages) == []


def test_a_missing_own_donation_url_fails():
    i = CHARITIES["riverbend-pantry"]["inputs"]
    problems = run("riverbend-pantry", {"index.html": f'<a href="{i["volunteer_url"]}">V</a>'})
    assert problems == [f"no page links the charity's own donate URL {i['donation_url']}"], problems


def test_a_mailto_needs_the_right_address_and_subject():
    email = CHARITIES["st-marys-shelter"]["inputs"]["footer_email"]
    body = (
        f'<a href="mailto:{email}">plain</a>'
        '<a href="mailto:someone@else.example?subject=Donating%20to%20X">wrong address</a>'
        f'<a href="mailto:{email}?subject=Volunteering%20with%20X">volunteer</a>'
    )
    problems = run("st-marys-shelter", {"index.html": body})
    assert len(problems) == 1 and "donate mailto" in problems[0], problems


def test_entity_encoded_urls_are_decoded_before_matching():
    donate = CHARITIES["cafe-eclair-arts"]["inputs"]["donation_url"]
    email = CHARITIES["cafe-eclair-arts"]["inputs"]["footer_email"]
    body = (
        f'<a href="{donate.replace("?", "&#x3F;")}">Donate</a>'
        f'<a href="mailto:{email}?subject=Volunteering%20with%20Caf%C3%A9&amp;x=1">V</a>'
    )
    assert run("cafe-eclair-arts", {"index.html": body}) == []


def test_an_empty_export_is_a_finding_not_a_pass():
    problems = run("riverbend-pantry", {"notes.txt": "no html"})
    assert problems and "no built HTML pages" in problems[0], problems


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {str(e)[:600]}")
    sys.exit(1 if failures else 0)
