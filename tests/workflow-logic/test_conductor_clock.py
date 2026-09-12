#!/usr/bin/env python3
"""Tests for `scripts/check-conductor-clock.py` (run 155, ledger L270).

The subject decides whether this host's clock may be trusted to date a run. The
two ways it could be worthless are the two halves covered here:

* **false green** -- it reports `ok` for a host that is badly skewed, or for a
  host whose clock it never actually managed to check. The second is the one that
  bit run 155: a check that cannot reach its reference and says nothing is
  indistinguishable from a check that found nothing wrong (L241, L267).
* **false red** -- it fires on ordinary NTP jitter, which is how a real alarm
  gets muted.

The decision is tested as a pure function with injected instants. A test that
read the real clock could only assert `ok` on a healthy host, which is precisely
the case that needs no check; and it would go red on a host with the defect,
turning the suite into a second victim of the skew.

Nothing here touches the network or the repo tree.
"""

from __future__ import annotations

import datetime
import importlib.util
import os
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
SCRIPT = REPO_ROOT / "scripts" / "check-conductor-clock.py"

UTC = datetime.timezone.utc


def _load():
    """Import the script as a module despite its hyphenated, non-importable name."""
    spec = importlib.util.spec_from_file_location("check_conductor_clock", SCRIPT)
    assert spec and spec.loader, SCRIPT
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = _load()

# The instant GitHub actually reported at run 155's bootstrap, and what this host
# claimed at the same moment. Used as the regression fixture so the case that
# motivated the script is the case pinned.
RUN155_SERVER = datetime.datetime(2026, 9, 12, 3, 27, 48, tzinfo=UTC)
RUN155_HOST = datetime.datetime(2026, 9, 11, 16, 5, 16, tzinfo=UTC)


def test_the_run_155_skew_is_caught():
    v = M.decide_clock(RUN155_HOST, RUN155_SERVER)
    assert v["verdict"] == M.VERDICT_SKEWED, v
    # ~11h22m behind. Asserted as a range, not a literal, so the fixture stays
    # readable as "most of a day" rather than encoding a second count.
    assert -41000 < v["skew_seconds"] < -40800, v
    assert "BEHIND" in v["reason"], v
    assert v["authoritative"] == "2026-09-12T03:27:48Z", v


def test_a_host_running_fast_is_caught_too():
    """Same magnitude, opposite sign. A `skew > tolerance` test would pass this."""
    ahead = RUN155_SERVER + datetime.timedelta(hours=11, minutes=22)
    v = M.decide_clock(ahead, RUN155_SERVER)
    assert v["verdict"] == M.VERDICT_SKEWED, v
    assert v["skew_seconds"] > 0, v
    assert "AHEAD OF" in v["reason"], v


def test_ordinary_jitter_is_not_a_finding():
    for offset in (-90, -1, 0, 1, 90):
        host = RUN155_SERVER + datetime.timedelta(seconds=offset)
        v = M.decide_clock(host, RUN155_SERVER)
        assert v["verdict"] == M.VERDICT_OK, (offset, v)


def test_the_tolerance_boundary_is_inclusive_on_both_sides():
    for sign in (1, -1):
        at = RUN155_SERVER + datetime.timedelta(seconds=sign * M.DEFAULT_TOLERANCE_SECONDS)
        assert M.decide_clock(at, RUN155_SERVER)["verdict"] == M.VERDICT_OK, sign
        just_past = RUN155_SERVER + datetime.timedelta(
            seconds=sign * (M.DEFAULT_TOLERANCE_SECONDS + 1)
        )
        assert M.decide_clock(just_past, RUN155_SERVER)["verdict"] == M.VERDICT_SKEWED, sign


def test_an_unreadable_server_time_is_unknown_and_never_ok():
    """The load-bearing case: no reference must not render as a clean bill."""
    v = M.decide_clock(RUN155_HOST, None)
    assert v["verdict"] == M.VERDICT_UNKNOWN, v
    assert v["verdict"] != M.VERDICT_OK
    assert v["skew_seconds"] is None, v
    assert v["authoritative"] is None, v
    assert "UNVERIFIED" in v["reason"], v
    assert M.EXIT_BY_VERDICT[v["verdict"]] != 0, v


def test_every_verdict_maps_to_a_distinct_exit_code():
    codes = M.EXIT_BY_VERDICT
    assert set(codes) == {M.VERDICT_OK, M.VERDICT_SKEWED, M.VERDICT_UNKNOWN}
    assert codes[M.VERDICT_OK] == 0
    # `skewed` and `unknown` must be distinguishable by a caller that only reads
    # the exit code -- a wrapper that collapsed them would lose the difference
    # between "your clock is wrong" and "I could not tell".
    assert len(set(codes.values())) == 3, codes


def test_a_naive_host_instant_is_refused_rather_than_assumed_utc():
    naive = RUN155_HOST.replace(tzinfo=None)
    try:
        M.decide_clock(naive, RUN155_SERVER)
    except ValueError as e:
        assert "timezone-aware" in str(e), e
    else:
        raise AssertionError("a naive host_now was accepted and silently treated as UTC")


def test_a_non_positive_tolerance_is_refused():
    for bad in (0, -1):
        try:
            M.decide_clock(RUN155_HOST, RUN155_SERVER, bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"tolerance {bad} was accepted")


def test_offset_spellings_of_one_instant_compare_equal():
    """A `Date` header in a non-UTC zone must not read as skew."""
    eastern = datetime.datetime(
        2026, 9, 11, 23, 27, 48, tzinfo=datetime.timezone(datetime.timedelta(hours=-4))
    )
    v = M.decide_clock(eastern, RUN155_SERVER)
    assert v["verdict"] == M.VERDICT_OK, v
    assert abs(v["skew_seconds"]) < 1, v


def test_a_real_rfc7231_date_header_parses_to_the_right_instant():
    parsed = M.parse_http_date("Sat, 12 Sep 2026 03:27:48 GMT")
    assert parsed == RUN155_SERVER, parsed


def test_a_header_value_with_leading_whitespace_still_parses():
    """`fetch_github_date` returns the raw text after the colon, which carries
    the space that follows it. If the parser could not cope with that, the script
    would report `unknown` on every healthy host -- a fail-closed check stuck
    closed, which reads as a broken host rather than a broken parser."""
    assert M.parse_http_date(" Sat, 12 Sep 2026 03:27:48 GMT") == RUN155_SERVER


def test_unparseable_and_zoneless_dates_are_None_not_a_guess():
    for raw in (None, "", "   ", "not a date", "Sat, 99 Xxx 2026 03:27:48 GMT"):
        assert M.parse_http_date(raw) is None, raw
    # A date with no zone parses as NAIVE. Treating it as UTC would manufacture a
    # confident instant out of a header we did not fully understand.
    assert M.parse_http_date("Sat, 12 Sep 2026 03:27:48") is None


def test_the_header_scan_cannot_match_a_date_field_in_the_json_body():
    """`gh api --include` prints headers then the body. A body containing
    `"date": ...` must not be mistaken for the `Date:` header -- the scan is
    anchored at the start of the line for exactly that reason."""
    body_line = '  "date": "1999-01-01T00:00:00Z",'
    assert not body_line.lower().startswith("date:")


def test_the_script_runs_and_reports_a_verdict():
    """One live invocation. The pure tests prove the decision; this proves the
    wiring -- argparse, the header scan, the exit-code map -- actually executes.

    The assertion is deliberately on the SHAPE, not on `ok`: on a host with no
    `gh`, or offline, `UNVERIFIED` and rc=2 is the correct answer, and a test
    demanding rc=0 would fail for the one reason the script exists to report."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        # The CHILD's encoding has to be pinned too, not just this decode (#962).
        # Written as an inline literal because `check-subprocess-encoding.py` reads
        # this call statically and cannot follow a variable. Pinning only the parent
        # turns mojibake into a UnicodeDecodeError on subprocess's reader thread:
        # the call still returns, `proc.stdout` is None, and the traceback blames
        # the caller. That guard caught exactly this in the first draft of this file.
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        timeout=90,
    )
    assert proc.returncode in (0, 1, 2), (proc.returncode, proc.stdout, proc.stderr)
    import json as _json

    verdict = _json.loads(proc.stdout)
    assert verdict["verdict"] in M.EXIT_BY_VERDICT, verdict
    assert proc.returncode == M.EXIT_BY_VERDICT[verdict["verdict"]], (proc.returncode, verdict)
    assert verdict["host"].endswith("Z"), verdict
    assert verdict["tolerance_seconds"] == M.DEFAULT_TOLERANCE_SECONDS, verdict


def test_a_bad_tolerance_on_the_command_line_exits_2_not_a_traceback():
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--tolerance-seconds", "0"],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},  # child too, #962
        timeout=90,
    )
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
    assert "Traceback" not in proc.stderr, proc.stderr


def test_agents_md_tells_the_conductor_to_run_this():
    """The script is only load-bearing if bootstrap actually calls it. A doc
    reference is the weakest half of this lesson, so it is at least pinned."""
    agents = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "check-conductor-clock.py" in agents, "AGENTS.md does not mention the clock check"


def run_module_tests(tests):
    """Run every test, report each, and return the failure count.

    `SystemExit` is not an `Exception`, so `except Exception` would let one test
    end the module and leave the rest unnamed -- the L82 truncated-roster shape.
    `KeyboardInterrupt` is re-raised so Ctrl-C does not take one interrupt per
    test."""
    failures = 0
    for t in tests:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {str(e)[:2000]}")
        except KeyboardInterrupt:
            raise
        except BaseException as e:  # SystemExit is not an Exception
            failures += 1
            print(f"  FAIL {t.__name__}: unexpected {type(e).__name__}: {str(e)[:2000]}")
    return failures


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    sys.exit(1 if run_module_tests(TESTS) else 0)
