"""Unit tests for scripts/charity_lane_ticket_batch.py.

The batch turns 232 reason codes into the gap list a charity reads, so these tests
pin that every completeness code has applicant wording (or is deliberately kept
from the applicant), that a full older-form gap list fits 205's extra_message
limit, and that an unknown code or a missing order refuses before anything is sent.
No network.
"""

from __future__ import annotations

import importlib.util
import pathlib
import re
import sys
import tempfile

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("batch", REPO_ROOT / "scripts" / "charity_lane_ticket_batch.py")
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)

CATALOG = (REPO_ROOT / "scripts" / "charity-lane-completeness.ps1").read_text(encoding="utf-8")


def completeness_codes():
    block = CATALOG.split("$script:LaneCompletenessCodes", 1)[1]
    return sorted(set(re.findall(r"^\s+([A-Z][A-Z0-9_]+)\s+= @\{ severity", block, re.M)))


def report(rows):
    return {"fraud_check": "screened", "verdicts": rows}


def test_every_completeness_code_has_wording_or_is_withheld():
    codes = completeness_codes()
    assert len(codes) > 30, codes
    missing = [c for c in codes if c not in batch.ITEMS and c not in batch.NOT_FOR_APPLICANT]
    assert not missing, missing


def test_a_full_gap_list_fits_the_205_limit():
    # Every gap at once, one code per item (the "(the one given ...)" variants
    # replace their base item, they never appear beside it).
    codes = [c for c, (_, item) in batch.ITEMS.items() if "(the one given" not in item]
    msg = batch.render(codes, ["A public URL that ties one name to the other."], ["Final notice: x."])
    assert len(msg) <= 1500, len(msg)


def test_render_groups_and_dedupes():
    msg = batch.render(["FACEBOOK_URL_INVALID", "FACEBOOK_PAGE_NOT_PUBLIC", "OFFICER_NAMES_MISSING", "LINK_UNVERIFIED"])
    assert msg.count("Facebook page") == 1
    assert "three different officers" in msg
    assert "UNVERIFIED" not in msg and "LINK_" not in msg


def test_unknown_code_refuses():
    try:
        batch.render(["SOMETHING_NEW"])
    except batch.BatchError:
        return
    raise AssertionError("an unknown code must stop the batch")


def test_build_validates_and_carries_client_and_preview():
    rows = [{"order_id": 753, "client_id": 432, "reason_codes": ["TAGLINE_MISSING"]}]
    plan = batch.build(report(rows), "onboarding_accepted_gaps", ["753"], [], {}, {"753": ["https://example.org/"]})
    assert plan[0]["inputs"]["client_id"] == "432"
    assert plan[0]["inputs"]["extra_message"].startswith("Preview: https://example.org/")


def test_build_sends_for_real():
    # 205 defaults to dry_run=true, so a batch that left it out would "send" nothing.
    rows = [{"order_id": 753, "client_id": 432, "reason_codes": ["TAGLINE_MISSING"]}]
    plan = batch.build(report(rows), "onboarding_accepted_gaps", ["753"], [], {}, {})
    assert plan[0]["inputs"]["dry_run"] == "false"


def test_build_refuses_a_missing_order_or_an_unscreened_report_or_a_bad_template():
    rows = [{"order_id": 1, "client_id": 2, "reason_codes": []}]
    for args in (
        (report(rows), "onboarding_still_interested", ["9"], [], {}, {}),
        ({"fraud_check": "not-run", "verdicts": rows}, "onboarding_still_interested", ["1"], ["x"], {}, {}),
        (report(rows), "international_techsoup", ["1"], ["x"], {}, {}),
    ):
        try:
            batch.build(*args)
        except (batch.BatchError, batch.gate.PolicyError):
            continue
        raise AssertionError(f"should refuse: {args[1]} {args[2]}")


class _Args:
    def __init__(self, head=(), verdicts_run=43428530582, template="onboarding_still_interested"):
        self.head, self.verdicts_run, self.template = list(head), verdicts_run, template


def test_batch_inputs_validate_against_rule_236():
    inputs = batch.batch_dispatch_inputs(_Args(head=["Final notice."]), ["688", "679"], {"688": ["a URL"]}, {})
    assert inputs["orders"] == "688,679" and inputs["dry_run"] == "false"
    assert batch.json.loads(inputs["specials_json"]) == {"688": ["a URL"]}
    assert "previews_json" not in inputs


def test_batch_refuses_over_fifty_orders_or_two_heads():
    for args, orders in ((_Args(), [str(i) for i in range(1, 52)]), (_Args(head=["a", "b"]), ["1"])):
        try:
            batch.batch_dispatch_inputs(args, orders, {}, {})
        except batch.BatchError:
            continue
        raise AssertionError("should refuse")


def test_json_specials_round_trip_and_refuse_junk():
    assert batch.from_json('{"800": "x", "861": ["y", "z"]}', "s") == {"800": ["x"], "861": ["y", "z"]}
    assert batch.from_json("", "s") == {}
    for raw in ("[1]", '{"abc": "x"}', '{"1": ""}', "{nope"):
        try:
            batch.from_json(raw, "s")
        except batch.BatchError:
            continue
        raise AssertionError(f"should refuse {raw!r}")


def test_summary_shows_every_ticket_text():
    rows = [{"order_id": 1, "client_id": 2, "reason_codes": ["TAGLINE_MISSING"]}, {"order_id": 3, "client_id": 4, "reason_codes": ["MISSION_MISSING"]}]
    plan = batch.build(report(rows), "onboarding_still_interested", ["1", "3"], [], {}, {})
    md = batch.summary_markdown(plan)
    assert "2 ticket(s)" in md and "tagline" in md and "mission statement" in md


def _plan(n_orders, tmp):
    """Run the `plan` action exactly the way workflow 236's plan job runs it."""
    rows = [{"order_id": 1000 + i, "client_id": 2000 + i, "reason_codes": ["TAGLINE_MISSING"]} for i in range(n_orders)]
    src = pathlib.Path(tmp, "verdicts.json")
    src.write_text(batch.json.dumps(report(rows)), encoding="utf-8")
    out = pathlib.Path(tmp, "plan.json")
    rc = batch.main(
        [
            "plan",
            "--verdicts-run",
            "43428530582",
            "--template",
            "onboarding_still_interested",
            "--orders",
            ",".join(str(r["order_id"]) for r in rows),
            "--report",
            str(src),
            "--plan-out",
            str(out),
        ]
    )
    rendered = len(batch.json.loads(out.read_text(encoding="utf-8"))) if out.exists() else 0
    return rc, rendered


def test_plan_binds_the_fifty_order_cap_that_236_documents():
    # 236's plan job runs `plan`, and only `batch` passed through rule 236-batch, so the
    # documented ceiling did not bind a direct workflow_dispatch: 200 orders rendered 200
    # tickets, and send's `plan.Count -ne EXPECTED_COUNT` compares the plan with itself.
    with tempfile.TemporaryDirectory() as d:
        assert _plan(batch.MAX_BATCH, d) == (0, batch.MAX_BATCH)
    with tempfile.TemporaryDirectory() as d:
        rc, rendered = _plan(batch.MAX_BATCH + 1, d)
        assert rc == 2 and rendered == 0, (rc, rendered)


def test_rule_236_policy_itself_refuses_more_than_fifty_orders():
    # The behavioural test above passes on the Python cap alone, so pin the policy's own
    # order pattern too: widening it to 98 is otherwise invisible to every test.
    rule = batch.gate.find_rule(batch.gate.load_policy(), batch.BATCH_RULE)
    base = {"verdicts_run": "43428530582", "template": "onboarding_still_interested", "dry_run": "false"}
    orders = lambda n: ",".join(str(1000 + i) for i in range(n))  # noqa: E731
    batch.gate.validate_inputs(rule, dict(base, orders=orders(batch.MAX_BATCH)))
    try:
        batch.gate.validate_inputs(rule, dict(base, orders=orders(batch.MAX_BATCH + 1)))
    except (batch.BatchError, batch.gate.PolicyError):
        return
    raise AssertionError(f"rule {batch.BATCH_RULE} should refuse {batch.MAX_BATCH + 1} orders")


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
