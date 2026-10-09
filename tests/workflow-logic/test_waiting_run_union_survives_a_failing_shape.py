"""A two-shape waiting-run union is only a floor if one shape can fail alone.

L341 added a second, branch-qualified query to every waiting-run read because
`?status=waiting` **under-reports rather than errors**, and L352 then spread that
union to the two consumers L341 had left bare. Both lessons reasoned entirely
about a shape that comes back *short*.

They missed how these calls actually fail. `gh_json` raises on any non-zero `gh`
exit and `rest_get` aborts the run on an HTTP or transport error, so a rate-limit
403, a timeout or an auth hiccup on the **first** shape discarded a perfectly
good answer from the second and took the whole collection down with it. The
redundancy was real only for malformed JSON — not for the failure mode it was
written to survive. Two shapes iterated inside one exception scope are not
redundant; they are two chances to fail.

The direction each consumer must fail in is not symmetric, and that asymmetry is
the point:

* **One shape surviving is enough.** The union is a floor, so the survivor's rows
  are the answer and the dead shape contributes nothing.
* **Every shape failing is not an empty queue.** It is a read failure, and all
  three of these callers do something destructive-by-omission with an empty list
  — the janitor cancels nothing and suppresses the pre-reap warning a human
  relies on, the status page publishes "no approvals outstanding", and the
  approval tool prints "no waiting runs found". Each must refuse instead.

The sibling cases for the janitor's inline JS live in
`test_734_stale_run_janitor.py`; the repo-wide guard that a new consumer cannot
be added bare is in `test_waiting_run_reads_are_unioned.py`.

Refs #752, L341, L352.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
APPROVE = REPO_ROOT / "scripts" / "approve-waiting-runs.py"
STATUS = REPO_ROOT / "scripts" / "generate-agentic-os-status.py"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _row(rid):
    return {"databaseId": rid, "workflowName": "703. Sites List", "displayTitle": "x"}


def _is_branch_shape(args):
    """Whether this `gh run list` argv carries the branch-qualified companion."""
    return "--branch" in args


# --------------------------------------------------------------------------
# scripts/approve-waiting-runs.py
# --------------------------------------------------------------------------


@pytest.mark.parametrize("failing", ["unqualified", "branch"])
def test_approve_tool_keeps_the_surviving_shapes_rows(monkeypatch, failing):
    """Either shape may be the one that dies; the other still answers.

    Parametrised deliberately. L334/L341 both measured that *which* shape goes
    bad is not stable, so a test that only ever kills the unqualified one would
    pass against an implementation that happens to tolerate failure in the first
    iteration only.
    """
    mod = _load(APPROVE, "approve_waiting_runs")

    def fake_gh_json(args):
        dead = _is_branch_shape(args) if failing == "branch" else not _is_branch_shape(args)
        if dead:
            raise RuntimeError("gh run list failed: API rate limit exceeded")
        return [_row(37283881466)]

    monkeypatch.setattr(mod, "gh_json", fake_gh_json)

    runs = mod.collect_waiting_runs("FreeForCharity/FFC-Cloudflare-Automation")

    assert [r["databaseId"] for r in runs] == [37283881466]


def test_approve_tool_reports_the_failing_shape(monkeypatch, capsys):
    """A shape that failed must be visible, and distinguishable from a zero.

    `unqualified=0` says the upstream `?status=waiting` defect is live;
    `unqualified=err` says the call never came back. Collapsing them sends the
    next person diagnosing a short count to the wrong endpoint entirely.
    """
    mod = _load(APPROVE, "approve_waiting_runs")

    def fake_gh_json(args):
        if not _is_branch_shape(args):
            raise RuntimeError("gh run list failed: timeout")
        return [_row(1)]

    monkeypatch.setattr(mod, "gh_json", fake_gh_json)
    mod.collect_waiting_runs("FreeForCharity/FFC-Cloudflare-Automation")

    err = capsys.readouterr().err
    assert "shape failed" in err, err
    assert "unqualified=err" in err, err


def test_approve_tool_refuses_when_every_shape_fails(monkeypatch):
    """Zero surviving shapes must raise, not return `[]`.

    `[]` here reaches a `main()` that prints "No waiting runs found by either
    query shape" — the single most dangerous sentence an approval tool can emit,
    because it is also what a genuinely clean queue looks like.
    """
    mod = _load(APPROVE, "approve_waiting_runs")
    monkeypatch.setattr(
        mod, "gh_json", lambda args: (_ for _ in ()).throw(RuntimeError("gh is down"))
    )

    with pytest.raises(RuntimeError, match="every waiting-run query shape failed"):
        mod.collect_waiting_runs("FreeForCharity/FFC-Cloudflare-Automation")


def test_approve_tool_still_reports_a_genuine_empty_queue(monkeypatch):
    """Both shapes answering empty is a real answer and must stay non-fatal.

    The guard above must key on *failure*, not on emptiness, or it would convert
    every quiet day into a crash.
    """
    mod = _load(APPROVE, "approve_waiting_runs")
    monkeypatch.setattr(mod, "gh_json", lambda args: [])

    assert mod.collect_waiting_runs("FreeForCharity/FFC-Cloudflare-Automation") == []


# --------------------------------------------------------------------------
# scripts/generate-agentic-os-status.py
# --------------------------------------------------------------------------


@pytest.mark.parametrize("failing_branch", [False, True])
def test_status_page_keeps_the_surviving_shapes_rows(monkeypatch, failing_branch):
    """The public gate panel survives one shape failing, in either position."""
    mod = _load(STATUS, "agentic_os_status")

    def fake_request(url, token, params=None, soft_fail=False):
        # `rest_get` bakes the params into the URL via `_build_url` and calls
        # `_request` with `params=None`, so the shape has to be read off the URL.
        # Asserting on `params` here silently classifies BOTH shapes as
        # unqualified and the test fails against correct code.
        has_branch = "branch=" in url
        if has_branch == failing_branch:
            assert soft_fail, "the waiting-run shapes must be read with soft_fail"
            return None, None
        return {"workflow_runs": [{"id": 37384577096}]}, None

    monkeypatch.setattr(mod, "_request", fake_request)

    runs = mod.collect_waiting_runs("FreeForCharity/FFC-Cloudflare-Automation", "tok")

    assert [r["id"] for r in runs] == [37384577096]


def test_status_page_refuses_to_publish_when_every_shape_fails(monkeypatch):
    """An empty gate panel hides every held gate at once, so it must not ship.

    This is the exact sentence L341 was filed about: the generator exits 0,
    writes valid JSON, and the page says "no approvals outstanding" while four
    runs sit waiting on a human.
    """
    mod = _load(STATUS, "agentic_os_status")
    monkeypatch.setattr(
        mod, "_request", lambda url, token, params=None, soft_fail=False: (None, None)
    )

    with pytest.raises(RuntimeError, match="every waiting-run query shape failed"):
        mod.collect_waiting_runs("FreeForCharity/FFC-Cloudflare-Automation", "tok")


def test_status_page_soft_fail_does_not_leak_into_other_reads(monkeypatch):
    """`soft_fail` is opt-in per call and must not become the default.

    `rest_get` is the generator's only HTTP door. If the waiting-run fix had been
    applied by flipping the default, every other read in the feed would silently
    degrade to `None` on a 500 and the page would ship with sections missing
    rather than failing.
    """
    mod = _load(STATUS, "agentic_os_status")
    seen = []

    def fake_request(url, token, params=None, soft_fail=False):
        seen.append(soft_fail)
        return [], None

    monkeypatch.setattr(mod, "_request", fake_request)
    mod.rest_get("repos/x/y/issues", "tok")

    assert seen == [False], seen
