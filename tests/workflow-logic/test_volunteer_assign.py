"""Unit tests for scripts/volunteer_assign.py (docs/volunteer-site-builders.md).

The script is what keeps two builders off one charity, so these tests pin its
refusals: a charity someone else holds, the automated lane's items, a volunteer
over their limit, an item that would start 701 if assigned. No network.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("va", REPO_ROOT / "scripts" / "volunteer_assign.py")
va = importlib.util.module_from_spec(spec)
spec.loader.exec_module(va)

NOW = dt.datetime(2026, 11, 21, 3, 0, 0, tzinfo=dt.timezone.utc)


def item(n, stage="lane:build-ready", extra=(), title=None, body="", assignees=()):
    return {"number": n, "state": "open", "title": title or f"[CHARITY-LANE] charity{n}.org",
            "body": body, "labels": [{"name": "charity-lane"}, {"name": stage}] + [{"name": e} for e in extra],
            "assignees": [{"login": a} for a in assignees]}


def claim(holder, until, at="2026-11-20T00:00:00Z"):
    return {"created_at": at, "body": f"<!-- charity-lane:claim holder={holder} until={until} -->"}


def refused(fn, *a, **k):
    try:
        fn(*a, **k)
    except va.Refused:
        return True
    return False


# --- holder ------------------------------------------------------------------


def test_build_ready_with_no_claim_is_open():
    assert va.holder_of(item(1), [], NOW)["kind"] == "open"


def test_worker_lease_in_date_holds_a_build_ready_item():
    c = [claim("https://claude.ai/code/session_x", "2026-11-21T05:00:00Z")]
    assert va.holder_of(item(1), c, NOW)["kind"] == "automated"
    expired = [claim("https://claude.ai/code/session_x", "2026-11-21T01:00:00Z")]
    assert va.holder_of(item(1), expired, NOW)["kind"] == "open"


def test_volunteer_label_and_claim_name_the_volunteer():
    i = item(1, "lane:building", extra=[va.VOLUNTEER_LABEL])
    h = va.holder_of(i, [claim("volunteer:yojoe26", "2026-12-01T00:00:00Z")], NOW)
    assert h["kind"] == "volunteer" and h["login"] == "yojoe26"
    # An expired volunteer claim is still a hold: only a release ends it.
    h = va.holder_of(i, [claim("volunteer:yojoe26", "2026-11-01T00:00:00Z")], NOW)
    assert h["kind"] == "volunteer" and h["login"] == "yojoe26"


def test_a_release_after_the_claim_ends_the_hold_record():
    c = [claim("volunteer:a", "2026-12-01T00:00:00Z"),
         {"created_at": "2026-11-20T01:00:00Z", "body": "<!-- charity-lane:release holder=volunteer:a -->"}]
    assert va.latest_claim(c)["kind"] == "release"


# --- claim -------------------------------------------------------------------


def test_claim_of_an_open_item_moves_it_to_building_and_marks_it():
    p = va.plan_claim(item(5), [], "yojoe26", [], NOW)
    assert p["add_labels"] == [va.VOLUNTEER_LABEL, "lane:building"]
    assert p["remove_labels"] == ["lane:build-ready"]
    assert "holder=volunteer:yojoe26 until=2026-12-05T03:00:00Z" in p["marker"]
    assert p["repo"] == "FFC-EX-charity5.org"


def test_claim_refuses_an_item_another_volunteer_holds():
    i = item(5, "lane:building", extra=[va.VOLUNTEER_LABEL])
    c = [claim("volunteer:other", "2026-12-01T00:00:00Z")]
    assert refused(va.plan_claim, i, c, "yojoe26", [], NOW)
    assert refused(va.plan_claim, i, c, "yojoe26", [], NOW, adopt=True)


def test_claim_refuses_the_automated_lanes_items_unless_adopting():
    for stage in ("lane:building", "lane:built", "lane:validate", "lane:charity-review"):
        assert refused(va.plan_claim, item(5, stage), [], "yojoe26", [], NOW), stage
    p = va.plan_claim(item(5, "lane:validate"), [], "yojoe26", [], NOW, adopt=True)
    assert p["remove_labels"] == [] and p["stage_after"] == "lane:validate"


def test_claim_refuses_a_website_request_item():
    assert refused(va.plan_claim, item(5, extra=["website-request"]), [], "yojoe26", [], NOW)


def test_claim_refuses_a_closed_or_non_lane_issue_and_a_bad_login():
    closed = item(5)
    closed["state"] = "closed"
    assert refused(va.plan_claim, closed, [], "yojoe26", [], NOW)
    plain = {"number": 6, "state": "open", "title": "x", "labels": [{"name": "bug"}]}
    assert refused(va.plan_claim, plain, [], "yojoe26", [], NOW)
    assert refused(va.plan_claim, item(5), [], "bad login!", [], NOW)


def test_first_charity_limit_then_three_after_a_charity_review():
    held = [item(1, "lane:building", extra=[va.VOLUNTEER_LABEL])]
    assert refused(va.plan_claim, item(5), [], "v", held, NOW)
    held = [item(1, "lane:charity-review", extra=[va.VOLUNTEER_LABEL])]
    assert not refused(va.plan_claim, item(5), [], "v", held, NOW)
    held += [item(2, "lane:building", extra=[va.VOLUNTEER_LABEL]),
             item(3, "lane:building", extra=[va.VOLUNTEER_LABEL])]
    assert refused(va.plan_claim, item(5), [], "v", held, NOW)


def test_reclaiming_ones_own_recorded_hold_is_a_no_op_refusal():
    i = item(5, "lane:building", extra=[va.VOLUNTEER_LABEL])
    assert refused(va.plan_claim, i, [claim("volunteer:v", "2026-12-01T00:00:00Z")], "v", [i], NOW)


# --- release -----------------------------------------------------------------


def test_release_returns_a_building_item_to_the_queue():
    i = item(5, "lane:building", extra=[va.VOLUNTEER_LABEL])
    p = va.plan_release(i, [claim("volunteer:v", "2026-12-01T00:00:00Z")], "v", NOW)
    assert p["add_labels"] == ["lane:build-ready"]
    assert set(p["remove_labels"]) == {va.VOLUNTEER_LABEL, "lane:building"}


def test_release_refuses_someone_elses_hold():
    i = item(5, "lane:building", extra=[va.VOLUNTEER_LABEL])
    assert refused(va.plan_release, i, [claim("volunteer:v", "2026-12-01T00:00:00Z")], "w", NOW)


# --- build queue -------------------------------------------------------------


def test_stale_is_fourteen_days_and_unknown_is_never_stale():
    assert va.is_stale("2026-11-01T00:00:00Z", NOW) == "yes"
    assert va.is_stale("2026-11-15T00:00:00Z", NOW) == "no"
    assert va.is_stale("", NOW) == "unknown"


def test_queue_puts_open_items_first_in_priority_order():
    rows = [
        va.queue_row(item(3, body="- Category: other"), [], "", NOW),
        va.queue_row(item(4, body="- Category: food_water_shelter"), [], "", NOW),
        va.queue_row(item(2, "lane:validate"), [], "", NOW),
        va.queue_row(item(9, body="Category: veterans"), [], "", NOW),
    ]
    assert [r["item"] for r in va.sort_rows(rows)] == [4, 9, 3, 2]
    assert va.sort_rows(rows)[-1]["holder"] == "Automated builder"


def test_queue_row_carries_repo_preview_and_stale_only_for_volunteers():
    i = item(7, "lane:building", extra=[va.VOLUNTEER_LABEL])
    r = va.queue_row(i, [claim("volunteer:v", "2026-12-01T00:00:00Z")], "2026-11-01T00:00:00Z", NOW)
    assert r["holder"] == "Volunteer @v" and r["stale"] == "yes"
    assert r["preview_url"] == "https://freeforcharity.github.io/FFC-EX-charity7.org/"
    assert va.queue_row(item(8), [], "2026-11-01T00:00:00Z", NOW)["stale"] == ""
    assert va.to_csv([r]).splitlines()[0] == ",".join(va.QUEUE_COLUMNS)


def test_repo_named_in_the_body_wins_over_the_title():
    i = item(1, title="[CHARITY-LANE] Cactus Wren Cooperative Preschool",
             body="Repo: https://github.com/FreeForCharity/FFC-EX-cactuswrenpreschool.com.")
    assert va.repo_of(i) == "FFC-EX-cactuswrenpreschool.com"
    assert va.repo_of(item(1, title="[CHARITY-LANE] Some Org Name")) == ""


def test_site_builder_brief_skips_volunteer_holds():
    text = (REPO_ROOT / ".claude" / "routines" / "site-builder.md").read_text(encoding="utf-8")
    assert "builder:volunteer" in text


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
