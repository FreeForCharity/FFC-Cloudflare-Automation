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
