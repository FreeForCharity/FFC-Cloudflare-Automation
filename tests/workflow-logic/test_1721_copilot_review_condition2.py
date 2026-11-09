"""A tier banner ahead of the marker is a real review, not a refusal (#1721).

AGENTS.md merge condition 2 used to require that a Copilot review's body **open
with** `<!-- ccr-overview-v2 -->`. Copilot began prefixing genuine, complete
reviews with a 119-character banner about a degraded model tier, which moves the
marker to offset 119. Under the literal rule those reviews read as refusals.

Measured: wrong on **2 of 12** open `agentic-os` PRs on 2026-11-06, both of them
`🟢 Approval recommended` with no open issues and every thread resolved -- the
exact PRs a live session should have landed. Re-measured 2026-11-09 on the same
cohort, the banner was on **10 of 10** reviewed PRs, so the defect went from
occasional to total in three days.

Every test below fails against a `startswith` implementation or against one that
reads refusal out of the prose while the check run says `success`. The fixtures
are the real bodies, byte-for-byte where they are quoted.

The failure direction is the point: a false refusal does not merely mislabel a
row. It holds a landable PR, it spends the #1645 re-request cap on a PR that
needs nothing, and a re-request can replace a real review with a refusal -- so
the mistake can destroy the asset it was trying to obtain.

Pure Python against the module's own helpers -- no `gh`, no network, no
`subprocess`, so this runs on the Conductor's Windows host too (#1119).
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

_SCRIPT = (
    pathlib.Path(__file__).resolve().parents[2]
    / "scripts"
    / "copilot_review_condition2.py"
)
_spec = importlib.util.spec_from_file_location("_cond2", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

classify = _mod.classify
body_is_real_review = _mod.body_is_real_review
body_is_tier_degraded = _mod.body_is_tier_degraded

SATISFIED = _mod.SATISFIED
WAIT = _mod.WAIT
NOT_ARMED = _mod.NOT_ARMED
REFUSED = _mod.REFUSED
NO_REVIEW = _mod.NO_REVIEW

# The exact banner Copilot prefixes, as read off #1586/#1611/#1720 heads.
TIER_BANNER = (
    "> [!NOTE]\n"
    "> Copilot was unable to complete this review at the selected tier due to "
    "model policy or plan restrictions.\n\n"
)

# A real, complete review -- the shape that used to be misread. Taken from
# #1586 head 3baabfe5 (2026-11-09T13:00:00Z).
TIER_DEGRADED_APPROVAL = (
    TIER_BANNER + "<!-- ccr-overview-v2 -->\n\n"
    "### 🟢 Approval recommended\n\n"
    "The review surfaced only minor documentation/comment nits with no active "
    "functional issues.\n\n**No open issues** 🎉\n\n<sub>Review effort: Balanced</sub>\n"
)

# The same thing without the banner: what the rule was written against.
PLAIN_APPROVAL = (
    "<!-- ccr-overview-v2 -->\n\n"
    "### 🟢 Approval recommended\n\n**No open issues** 🎉\n"
)

# A tier-degraded review that still files a finding -- #1647 head 9db4aee5.
TIER_DEGRADED_CHANGES = (
    TIER_BANNER + "<!-- ccr-overview-v2 -->\n\n"
    "### 🟡 Changes recommended\n\nA moderate active issue remains.\n\n"
    "<summary><strong>1 open issue</strong></summary>\n"
)

# The genuine refusals. Neither carries the marker.
CORPUS_REFUSAL = (
    "Copilot wasn't able to review this pull request because the custom "
    "instructions in this repository or organization exceed the maximum prompt size."
)
GENERIC_ERROR = (
    "Copilot encountered an error and was unable to review this pull request."
)


def test_the_tier_banner_does_not_make_a_real_review_a_refusal():
    """The #1721 defect itself: marker at offset 119, verdict 🟢."""
    assert TIER_DEGRADED_APPROVAL.index("<!-- ccr-overview-v2 -->") == 119, (
        "fixture drifted: the banner is meant to put the marker at offset 119, "
        "which is the whole reason a startswith test fails on it"
    )
    assert not TIER_DEGRADED_APPROVAL.startswith("<!-- ccr-overview-v2 -->"), (
        "fixture no longer exercises the defect"
    )
    assert body_is_real_review(TIER_DEGRADED_APPROVAL), (
        "a tier-degraded review body is a REAL review; asserting the marker's "
        "position reports it as a refusal (#1721)"
    )
    verdict, _ = classify("completed", "success", TIER_DEGRADED_APPROVAL)
    assert verdict == SATISFIED, (
        f"condition 2 must hold on a tier-degraded real review, got {verdict}"
    )


def test_a_tier_degraded_review_that_files_findings_is_still_a_review():
    """🟡 with an open issue is a review; it fails condition 3, not condition 2."""
    assert body_is_real_review(TIER_DEGRADED_CHANGES)
    verdict, _ = classify("completed", "success", TIER_DEGRADED_CHANGES)
    assert verdict == SATISFIED, (
        "a 'Changes recommended' body is a completed review -- its findings are "
        f"condition 3's business, not condition 2's; got {verdict}"
    )


def test_the_tier_notice_is_reported_without_being_treated_as_refusal():
    """The degradation is worth surfacing; it must not change the verdict."""
    assert body_is_tier_degraded(TIER_DEGRADED_APPROVAL)
    assert not body_is_tier_degraded(PLAIN_APPROVAL)
    plain, _ = classify("completed", "success", PLAIN_APPROVAL)
    degraded, _ = classify("completed", "success", TIER_DEGRADED_APPROVAL)
    assert plain == degraded == SATISFIED, (
        "the tier notice changed the verdict; it is a notice, not a refusal"
    )


def test_a_failure_check_run_is_a_refusal_whatever_the_body_says():
    """The check run is primary. #1596 is `failure` 18x and must never pass."""
    for body in (CORPUS_REFUSAL, TIER_DEGRADED_APPROVAL, PLAIN_APPROVAL, None):
        verdict, _ = classify("completed", "failure", body)
        assert verdict == REFUSED, (
            f"a failure check run must be REFUSED regardless of body, got {verdict}"
        )


def test_an_absent_check_run_is_not_armed_and_never_satisfies():
    """#1607's shape: no check run at all. Absence is not a review."""
    verdict, _ = classify(None, None, None)
    assert verdict == NOT_ARMED, verdict
    # Even if a stale body is lying around, no check run means no answer.
    verdict, _ = classify(None, None, TIER_DEGRADED_APPROVAL)
    assert verdict == NOT_ARMED, (
        f"a body with no check run on the head must not satisfy condition 2, got {verdict}"
    )


def test_a_queued_or_running_check_run_means_wait_not_pass_and_not_fail():
    """AGENTS.md: while the check is queued or running, wait."""
    for status in ("queued", "in_progress"):
        verdict, _ = classify(status, None, None)
        assert verdict == WAIT, f"{status} should be WAIT, got {verdict}"


def test_the_known_refusal_bodies_are_not_reviews():
    """Both documented error bodies, neither of which carries the marker."""
    for body in (CORPUS_REFUSAL, GENERIC_ERROR):
        assert not body_is_real_review(body), body[:40]


def test_a_refusal_body_under_a_success_check_run_is_still_refused():
    """Belt and braces: if the two signals disagree, do not enqueue."""
    verdict, reason = classify("completed", "success", CORPUS_REFUSAL)
    assert verdict == REFUSED, (
        f"a refusal body must not pass on a success check run, got {verdict}"
    )
    assert "refusal body" in reason, reason


def test_a_success_check_run_with_no_review_body_does_not_satisfy():
    """Green check, nothing to read -- that is not a review on this head."""
    for body in (None, "", "LGTM", "### 🟢 Approval recommended"):
        verdict, _ = classify("completed", "success", body)
        assert verdict == NO_REVIEW, (
            f"body {body!r} carries no marker and must not satisfy, got {verdict}"
        )


def test_the_implementation_does_not_assert_on_the_markers_POSITION():
    """The durable rule: position is the wrong thing to assert on.

    The banner's wording is Copilot's and may change again, so a test that only
    pins today's prefix would pass a reimplementation that hardcodes it. This
    pins the mechanism instead.
    """
    import ast
    import inspect
    import textwrap

    # Parse rather than grep: the function's own docstring says the word
    # "startswith" to explain why it is not used, and a text search cannot tell
    # prose from a call. An AST walk looks only at what executes.
    tree = ast.parse(textwrap.dedent(inspect.getsource(_mod.body_is_real_review)))
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "startswith" not in called, (
        "body_is_real_review() calls .startswith() -- it asserts on the marker's "
        "position again, which is the #1721 defect"
    )
    assert "index" not in called and "find" not in called, (
        "body_is_real_review() locates the marker by offset; condition 2 must not "
        "depend on where the marker sits"
    )
    # An arbitrary, unknown prefix must not change the answer.
    for prefix in ("", "x" * 500, "> [!WARNING]\n> something new entirely\n\n"):
        assert body_is_real_review(prefix + PLAIN_APPROVAL), (
            f"prefix {prefix[:20]!r} defeated the body test"
        )


def test_every_PR_of_the_2026_11_09_cohort_satisfies_condition_2():
    """The regression set, as measured. 10 reviewed PRs, 10 tier-degraded.

    Recorded as data because the cohort is what makes the defect total rather
    than occasional: on this reading every one of these is landable-on-review,
    and under `startswith` every one of them reads as unreviewed.
    """
    # (pr, check_status, check_conclusion, tier_degraded)
    cohort = [
        (1720, "completed", "success", True),
        (1706, "completed", "success", True),
        (1695, "completed", "success", True),
        (1647, "completed", "success", True),
        (1634, "completed", "success", True),
        (1611, "completed", "success", True),
        (1610, "completed", "success", True),
        (1600, "completed", "success", True),
        (1587, "completed", "success", True),
        (1586, "completed", "success", True),
    ]
    assert len(cohort) == 10, "the measured cohort was 10 reviewed PRs"
    for pr, status, conclusion, degraded in cohort:
        body = TIER_DEGRADED_APPROVAL if degraded else PLAIN_APPROVAL
        verdict, _ = classify(status, conclusion, body)
        assert verdict == SATISFIED, f"#{pr} read as {verdict}"

    # #1596 is the control: genuinely refused, 18 consecutive times.
    verdict, _ = classify("completed", "failure", CORPUS_REFUSAL)
    assert verdict == REFUSED, "#1596 must stay refused -- it is the control"


def test_AGENTS_md_no_longer_requires_the_marker_to_be_FIRST():
    """AC1. The rule text is the authority other tooling quotes."""
    raw = (pathlib.Path(__file__).resolve().parents[2] / "AGENTS.md").read_text(
        encoding="utf-8"
    )
    # Collapse whitespace before matching. `prettier@3.8.1` reflows this file at
    # a 100-column print width, so any phrase here may be split across lines by
    # a formatting pass that changes nothing about the rule. Asserting on the
    # raw text would make this test a line-wrap detector.
    agents = " ".join(raw.split())

    assert "body must open with `<!-- ccr-overview-v2 -->`" not in agents, (
        "AGENTS.md still requires the marker to be first (#1721 AC1)"
    )
    assert "must **contain** `<!-- ccr-overview-v2 -->`" in agents, (
        "AGENTS.md does not state the `contains` test (#1721 AC1)"
    )
    # AC1 also requires the check run be named as the primary signal.
    assert "check run on the head SHA is the PRIMARY signal" in agents, (
        "AGENTS.md does not name the check run as the primary signal (#1721 AC1)"
    )
    # And that the tier banner is called out as a notice rather than a refusal,
    # since that is the fact a reader needs to not re-derive the defect.
    assert "tier notice, not a refusal" in agents, (
        "AGENTS.md does not say the tier banner is a notice, not a refusal"
    )


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {str(e)[:500]}")
    sys.exit(1 if failures else 0)
