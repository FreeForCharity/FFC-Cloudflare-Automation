"""Unit tests for scripts/charity_lane_lookup.py (epic #1618).

The lookup stops the charity lane from building a second site for a charity that
is already in flight or live. These tests pin the fuzzy matching on fixture data
(no network): the acronym slugs that catch real FFC repo names, the phrase filter
that drops GitHub search's tokenized false hits, and the verdict ladder.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("lookup", REPO_ROOT / "scripts" / "charity_lane_lookup.py")
lk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lk)

REPOS = [
    {"name": "FFC-EX-hsfreeclinic.org"},
    {"name": "FFC-EX-legioninthewoods.org"},
    {"name": "FFC-EX-SRRN.net"},
    {"name": "FFC-IN-ffcadmin.org"},
    {"name": "FFC-EX-oldsite.org", "archived": True},
]


def test_acronym_slug_catches_hsfreeclinic():
    assert "hsfreeclinic" in lk.slugs("Hollywood Sunset Free Clinic")


def test_legal_suffixes_are_dropped_for_core_slug():
    s = lk.slugs("The Good Neighbor, Inc")
    assert "goodneighbor" in s and "thegoodneighborinc" in s


def test_ampersand_entity_is_a_word_boundary():
    assert lk.words("United Carnales &amp; Community Coalition") == [
        "united", "carnales", "and", "community", "coalition",
    ]


def test_stems():
    assert lk.repo_stem("FFC-EX-hsfreeclinic.org") == "hsfreeclinic"
    assert lk.repo_stem("FFC-EX-SRRN.net") == "srrn"
    assert lk.domain_stem("https://www.Legion-In-The-Woods.org/") == "legioninthewoods"


def test_repo_match_strengths():
    hits = lk.match_repos(REPOS, lk.slugs("Hollywood Sunset Free Clinic"), None)
    assert [(h["name"], h["strength"]) for h in hits] == [("FFC-EX-hsfreeclinic.org", "strong")]
    exact = lk.match_repos(REPOS, [], "legioninthewoods")
    assert exact[0]["strength"] == "exact"


def test_short_slugs_never_match_alone():
    # "srrn" is 4 chars: below MIN_SLUG, so an org whose acronym is SRRN is not
    # declared a strong match on that alone.
    assert lk.match_repos(REPOS, ["srrn"], None) == []


def test_archived_flag_is_reported():
    hits = lk.match_repos(REPOS, ["oldsite"], None)
    assert hits and hits[0]["archived"] is True


def test_issue_phrase_filter_drops_tokenized_hits():
    # GitHub search matches "Galatians 62" against any PR #62; the filter does not.
    assert lk.issue_strength("Galatians 62", "npm(deps-dev): bump fast-uri", "") is None
    assert lk.issue_strength("hsfreeclinic.org", "[WEBSITE REQUEST] hsfreeclinic.org", "") == "strong"
    assert lk.issue_strength("Hollywood Sunset Free Clinic", "status", "...Hollywood Sunset Free Clinic...") == "weak"
    assert lk.issue_strength("abc", "abc", "") is None  # too short to mean anything


def test_sites_match_by_domain():
    rows = [{"Domain": "hsfreeclinic.org", "Status": "Planned", "Repo URL": "", "Server In Use": ""}]
    hits = lk.match_sites(rows, lk.slugs("Hollywood Sunset Free Clinic"), None)
    assert hits and hits[0]["strength"] == "strong"


def test_verdict_ladder():
    assert lk.summarize("x", None, [], [])["verdict"] == "NO_MATCH"
    weak = [{"source": "issues", "strength": "weak"}]
    assert lk.summarize("x", None, [], weak)["verdict"] == "POSSIBLE_MATCH"
    strong = weak + [{"source": "repos", "strength": "strong"}]
    res = lk.summarize("x", None, [], strong)
    assert res["verdict"] == "ALREADY_KNOWN" and res["matches"][0]["strength"] == "strong"


def test_web_query_uses_irs_city_when_known():
    irs = {"name": "Galatians 62", "city": "Rock Hill", "state": "SC", "ein": "833315573"}
    assert lk.web_query("Galatians 62", irs) == '"Galatians 62" Rock Hill SC website'
    assert lk.web_query("402 NonPAWfit", None) == '"402 NonPAWfit" nonprofit website'


def test_lookup_offline_with_fixtures():
    res = lk.lookup("Hollywood Sunset Free Clinic", None, {"repos", "sites"}, repos=REPOS, sites=[])
    assert res["verdict"] == "ALREADY_KNOWN"
    assert res["irs"] is None


def main():
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS {name}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"  FAIL {name}: {e!r}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
