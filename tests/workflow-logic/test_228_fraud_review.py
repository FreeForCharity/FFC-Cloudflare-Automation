"""Unit tests for the 228 fraud-review decision function (pwsh).

Exercises Get-FraudReviewRecommendation in scripts/fraudlabspro-api-common.ps1 — the pure policy
that turns a WHMCS order status + FraudLabs Pro verdict into a recommended action (issue #813). The
function is the one place a false positive is distinguished from real fraud, so its table is locked
down here rather than left to surface as a mis-cleared (or mis-held) charity order in production.

Skipped when pwsh is unavailable (local sandboxes); always runs in CI (ubuntu-latest ships
PowerShell). Mirrors test_720_owner_parse.py.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
COMMON = REPO_ROOT / "scripts" / "fraudlabspro-api-common.ps1"


def recommend(whmcs_status: str, fraudlabs_status: str, amount: str) -> str:
    """Dot-source the shipped script and return the Recommendation for one scenario."""
    script = (
        f". '{COMMON}'; "
        f"$r = Get-FraudReviewRecommendation "
        f"-WhmcsStatus '{whmcs_status}' -FraudLabsStatus '{fraudlabs_status}' -Amount {amount}; "
        f"Write-Output \"REC=$($r.Recommendation)\""
    )
    proc = subprocess.run(
        ["pwsh", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        raise AssertionError(f"pwsh exited {proc.returncode}: {proc.stderr}")
    for line in proc.stdout.splitlines():
        if line.startswith("REC="):
            return line[len("REC=") :].strip()
    raise AssertionError(f"no REC= line in output: {proc.stdout!r}")


def test_fraud_approve_zero_dollar_recommends_clear():
    # The order-793 case: FraudLabs APPROVE on a $0 onboarding order held in WHMCS Fraud.
    assert recommend("Fraud", "APPROVE", "0") == "clear-recommended"


def test_approve_status_is_case_insensitive():
    assert recommend("Fraud", "approve", "0") == "clear-recommended"


def test_fraud_approve_nonzero_amount_needs_manual_review():
    # A non-$0 order is not the free onboarding pattern — never auto-cleared.
    assert recommend("Fraud", "APPROVE", "49.99") == "review-manually"


def test_fraud_reject_holds_for_human():
    assert recommend("Fraud", "REJECT", "0") == "hold-for-human"


def test_fraud_review_verdict_needs_manual_review():
    assert recommend("Fraud", "REVIEW", "0") == "review-manually"


def test_fraud_no_verdict_needs_manual_review():
    assert recommend("Fraud", "", "0") == "review-manually"


def test_non_fraud_status_is_no_action():
    # Only orders WHMCS is actually holding in Fraud status are in scope for a clear.
    assert recommend("Pending", "APPROVE", "0") == "no-action"
    assert recommend("Active", "APPROVE", "0") == "no-action"


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    if shutil.which("pwsh") is None:
        print("  SKIP all (pwsh not installed in this environment; runs in CI)")
        sys.exit(0)
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {str(e)[:400]}")
    sys.exit(1 if failures else 0)
