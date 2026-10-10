"""A two-shape waiting-run union is only a floor if one shape can fail alone.

L341 added a second, branch-qualified waiting-run query because `?status=waiting`
**under-reports rather than errors**, and L352 then spread that union to the two
consumers L341 had left bare. Both lessons reasoned entirely about a shape that
comes back *short*.

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

**No `pytest`.** `run_all.py` executes every module as `[sys.executable, path]`
and CI has no pytest installed, so an `import pytest` is a `ModuleNotFoundError`
that reads as "tests failed" with no failing assertion anywhere in the log. This
module was written against pytest first and CI caught it; 1 of 132 modules here
used pytest and it was this one. Hence the hand-rolled `_raises`, `_stderr_of`
and explicit case lists below — the house style is the portable one.

Refs #752, L341, L352.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
APPROVE = REPO_ROOT / "scripts" / "approve-waiting-runs.py"
STATUS = REPO_ROOT / "scripts" / "generate-agentic-os-status.py"

HUB = "FreeForCharity/FFC-Cloudflare-Automation"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _raises(exc_type, match, fn, *args, **kwargs):
    """Assert `fn` raises `exc_type` whose message contains `match`.

    The message check is not decoration: every refusal under test here is a
    `RuntimeError`, and so is the `gh` failure the fakes inject, so matching on
    the type alone would pass if the collection simply propagated the injected
    error instead of refusing on its own terms.
    """
    try:
        fn(*args, **kwargs)
    except exc_type as got:
        assert match in str(got), f"expected {match!r} in {str(got)!r}"
        return got
    except Exception as got:  # noqa: BLE001 - reported, not swallowed
        raise AssertionError(
            f"expected {exc_type.__name__} containing {match!r}, got "
            f"{type(got).__name__}: {got}"
        ) from None
    raise AssertionError(f"expected {exc_type.__name__} containing {match!r}, nothing raised")


def _stderr_of(fn, *args, **kwargs):
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        fn(*args, **kwargs)
    return buf.getvalue()


@contextlib.contextmanager
def _patched(mod, name, value):
    missing = object()
    old = getattr(mod, name, missing)
    setattr(mod, name, value)
    try:
        yield
    finally:
        if old is missing:
            delattr(mod, name)
        else:
            setattr(mod, name, old)


def _row(rid):
    return {"databaseId": rid, "workflowName": "703. Sites List", "displayTitle": "x"}


def _is_branch_shape(args):
    """Whether this `gh run list` argv carries the branch-qualified companion."""
    return "--branch" in args


# --------------------------------------------------------------------------
# scripts/approve-waiting-runs.py
# --------------------------------------------------------------------------


def test_approve_tool_keeps_the_surviving_shapes_rows():
    """Either shape may be the one that dies; the other still answers.

    Both positions are exercised deliberately. L334/L341 both measured that
    *which* shape goes bad is not stable, so killing only the unqualified one
    would pass against an implementation that tolerates failure in the first
    loop iteration alone.
    """
    for failing in ("unqualified", "branch"):
        mod = _load(APPROVE, "approve_waiting_runs")

        def fake_gh_json(args, _failing=failing):
            dead = (
                _is_branch_shape(args)
                if _failing == "branch"
                else not _is_branch_shape(args)
            )
            if dead:
                raise RuntimeError("gh run list failed: API rate limit exceeded")
            return [_row(37283881466)]

        with _patched(mod, "gh_json", fake_gh_json):
            runs = mod.collect_waiting_runs(HUB)

        got = [r["databaseId"] for r in runs]
        assert got == [37283881466], f"failing={failing}: got {got}"


def test_approve_tool_reports_the_failing_shape():
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

    with _patched(mod, "gh_json", fake_gh_json):
        err = _stderr_of(mod.collect_waiting_runs, HUB)

    assert "shape failed" in err, err
    assert "unqualified=err" in err, err


def test_approve_tool_refuses_when_every_shape_fails():
    """Zero surviving shapes must raise, not return `[]`.

    `[]` here reaches a `main()` that prints "No waiting runs found by either
    query shape" — the single most dangerous sentence an approval tool can
    emit, because it is also what a genuinely clean queue looks like.
    """
    mod = _load(APPROVE, "approve_waiting_runs")

    def fake_gh_json(args):
        raise RuntimeError("gh is down")

    with _patched(mod, "gh_json", fake_gh_json):
        _raises(
            RuntimeError,
            "every waiting-run query shape failed",
            mod.collect_waiting_runs,
            HUB,
        )


def test_approve_tool_still_reports_a_genuine_empty_queue():
    """Both shapes answering empty is a real answer and must stay non-fatal.

    The guard above has to key on *failure*, not on emptiness, or it converts
    every quiet day into a crash.
    """
    mod = _load(APPROVE, "approve_waiting_runs")
    with _patched(mod, "gh_json", lambda args: []):
        assert mod.collect_waiting_runs(HUB) == []


# --------------------------------------------------------------------------
# scripts/generate-agentic-os-status.py
# --------------------------------------------------------------------------


def test_status_page_keeps_the_surviving_shapes_rows():
    """The public gate panel survives one shape failing, in either position."""
    for failing_branch in (False, True):
        mod = _load(STATUS, "agentic_os_status")

        def fake_request(url, token, params=None, soft_fail=False, _fb=failing_branch):
            # `rest_get` bakes the params into the URL via `_build_url` and calls
            # `_request` with `params=None`, so the shape has to be read off the
            # URL. Keying on `params` here silently classifies BOTH shapes as
            # unqualified, and the test then fails against correct code.
            has_branch = "branch=" in url
            if has_branch == _fb:
                assert soft_fail, "the waiting-run shapes must be read with soft_fail"
                return None, None
            return {"workflow_runs": [{"id": 37384577096}]}, None

        with _patched(mod, "_request", fake_request):
            runs = mod.collect_waiting_runs(HUB, "tok")

        got = [r["id"] for r in runs]
        assert got == [37384577096], f"failing_branch={failing_branch}: got {got}"


def test_status_page_refuses_to_publish_when_every_shape_fails():
    """An empty gate panel hides every held gate at once, so it must not ship.

    This is the exact sentence L341 was filed about: the generator exits 0,
    writes valid JSON, and the page says "no approvals outstanding" while four
    runs sit waiting on a human.
    """
    mod = _load(STATUS, "agentic_os_status")

    def fake_request(url, token, params=None, soft_fail=False):
        return None, None

    with _patched(mod, "_request", fake_request):
        _raises(
            RuntimeError,
            "every waiting-run query shape failed",
            mod.collect_waiting_runs,
            HUB,
            "tok",
        )


def test_status_page_soft_fail_does_not_leak_into_other_reads():
    """`soft_fail` is opt-in per call and must not become the default.

    `rest_get` is the generator's only HTTP door. If the waiting-run fix had
    been applied by flipping that default, every other read in the feed would
    silently degrade to `None` on a 500 and the page would ship with sections
    missing rather than failing.
    """
    mod = _load(STATUS, "agentic_os_status")
    seen = []

    def fake_request(url, token, params=None, soft_fail=False):
        seen.append(soft_fail)
        return [], None

    with _patched(mod, "_request", fake_request):
        mod.rest_get("repos/x/y/issues", "tok")

    assert seen == [False], seen


def main():
    failures = []
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"  PASS {name}")
        except AssertionError as exc:
            failures.append(name)
            print(f"  FAIL {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            # Reported as a FAIL rather than allowed to propagate: an exception
            # escaping here kills the module mid-roster, and L194 is that a
            # truncated roster is scored as passing tests by any `FAIL` grep.
            failures.append(name)
            print(f"  FAIL {name}: unexpected {type(exc).__name__}: {exc}")
    print(f"\n{len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
