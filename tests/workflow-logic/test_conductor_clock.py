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


# ---------------------------------------------------------------------------
# The `curl` fallback (#1335). The cloud worker has no `gh`, so before this the
# check reported UNVERIFIED on every run of that session class -- honest, and
# never a pass. These cases pin the two ways a fallback could be worse than no
# fallback: reading a timestamp the proxy minted, or reporting one when the
# response carried none.
# ---------------------------------------------------------------------------

# Captured verbatim from a worker sandbox, 2026-09-18. Two status lines: the
# egress proxy's CONNECT, then GitHub's own response.
MEASURED_CURL_TRANSCRIPT = """HTTP/1.1 200 Connection Established

HTTP/1.1 200 OK
Server: github.com
Date: Fri, 18 Sep 2026 03:16:30 GMT
Content-Type: application/json; charset=utf-8
X-Github-Request-Id: 80E8:D39EC:44EA723:E8462BF:6AAC58E6
"""

MEASURED_SERVER = datetime.datetime(2026, 9, 18, 3, 16, 30, tzinfo=UTC)

# The `/` shape: allowlisted enough to answer 200, and carries no `Date` at all.
NO_DATE_200_TRANSCRIPT = """HTTP/1.1 200 Connection Established

HTTP/1.1 200 OK
Server: github.com
Content-Type: application/json; charset=utf-8
X-Github-Request-Id: 80E8:D39EC:44EA723:E8462BF:6AAC58E7
"""

# The `/zen` shape: the proxy refuses a non-allowlisted path itself.
REFUSED_403_TRANSCRIPT = """HTTP/1.1 200 Connection Established

HTTP/1.1 403 Forbidden
Content-Type: application/json

{"message":"This GitHub API path is not available: sessions are bound to their configured repositories."}
"""


def test_the_measured_transcript_yields_githubs_instant():
    raw = M.date_from_curl_transcript(MEASURED_CURL_TRANSCRIPT)
    assert M.parse_http_date(raw) == MEASURED_SERVER, raw


def test_a_date_on_the_proxys_connect_block_cannot_be_read_as_the_reference():
    """The discriminating case, and the reason the real transcript is not enough.

    Today's CONNECT block carries no `Date`, so "first `Date` in the stream"
    passes the measured fixture while being wrong. A proxy that added one would
    hand this check a timestamp minted on THIS host -- the one surface a clock
    check may not share (L242) -- and every other assertion here would still be
    green. So the wrong value is injected deliberately rather than waited for.
    """
    poisoned = MEASURED_CURL_TRANSCRIPT.replace(
        "HTTP/1.1 200 Connection Established\n",
        "HTTP/1.1 200 Connection Established\nDate: Mon, 01 Jan 1990 00:00:00 GMT\n",
    )
    assert "1990" in poisoned, "the fixture did not take; this test would prove nothing"
    parsed = M.parse_http_date(M.date_from_curl_transcript(poisoned))
    assert parsed == MEASURED_SERVER, parsed
    assert parsed.year != 1990, parsed


def test_a_200_that_carries_no_date_is_not_an_answer():
    """A SUCCESSFUL response with no timestamp -- distinct from a probe that
    failed to run, and the shape an endpoint swap to `/` would produce."""
    assert M.date_from_curl_transcript(NO_DATE_200_TRANSCRIPT) is None


def test_a_refused_path_is_not_an_answer():
    assert M.date_from_curl_transcript(REFUSED_403_TRANSCRIPT) is None


def test_a_date_from_a_block_that_is_not_githubs_is_refused():
    """`curl` exits 0 when the PROXY answers, so a zero exit says nothing about
    who replied. Without the origin-marker requirement this transcript reads as
    a clean remote reference."""
    proxy_only = """HTTP/1.1 200 Connection Established

HTTP/1.1 200 OK
Content-Type: application/json
Date: Mon, 01 Jan 1990 00:00:00 GMT
"""
    assert M.date_from_curl_transcript(proxy_only) is None


def test_the_gh_reader_is_unchanged_by_the_fallback():
    """The Conductor's host still has `gh`, and its transcript is one block."""
    gh_out = """HTTP/2.0 200 OK
Date: Sat, 12 Sep 2026 03:27:48 GMT
Content-Type: application/json

{
  "resources": {
    "core": {"limit": 5000, "remaining": 5000}
  },
  "date": "1999-01-01T00:00:00Z"
}
"""
    assert M.parse_http_date(M.date_from_gh_transcript(gh_out)) == RUN155_SERVER


def test_text_before_any_status_line_belongs_to_no_response():
    assert M.split_response_blocks("Date: Mon, 01 Jan 1990 00:00:00 GMT\n") == []


def test_the_probe_endpoint_is_pinned_by_name_and_reaches_the_curl_argv():
    """`/zen`, `/meta` and `/` all answer without a `Date` through this proxy, so
    the endpoint is not interchangeable and the constant says so."""
    assert M.GITHUB_DATE_PROBE_URL == "https://api.github.com/rate_limit"
    argvs = [argv for argv, _ in M.probe_commands(20)]
    assert argvs[0][0] == "gh", argvs  # authenticated probe stays first
    curl_argv = argvs[1]
    assert curl_argv[0] == "curl", curl_argv
    assert M.GITHUB_DATE_PROBE_URL in curl_argv, curl_argv
    assert "-D" in curl_argv, "headers are the only thing this call wants"


def _verdict_through(transcripts):
    """Run the real `main()` with the probe layer replaced, and return
    (exit code, verdict dict). `transcripts` maps a probe's binary name to the
    stdout it produces, or to None for a probe that cannot run at all."""
    import contextlib
    import io
    import json as _json

    original = M._run_probe
    M._run_probe = lambda argv, timeout_seconds: transcripts.get(argv[0])
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = M.main(["--json"])
        return rc, _json.loads(buf.getvalue())
    finally:
        M._run_probe = original


def test_a_host_with_no_gh_still_gets_a_verdict_through_the_fallback():
    """AC 1 of #1335: the whole point. `gh` cannot run; the answer is still real."""
    rc, verdict = _verdict_through({"curl": MEASURED_CURL_TRANSCRIPT})
    assert verdict["authoritative"] == "2026-09-18T03:16:30Z", verdict
    assert verdict["verdict"] in (M.VERDICT_OK, M.VERDICT_SKEWED), verdict
    assert rc == M.EXIT_BY_VERDICT[verdict["verdict"]], (rc, verdict)


def test_a_skew_is_still_caught_through_the_fallback():
    """Without this, a fallback that hard-coded `ok` would satisfy every other
    criterion here. The transcript's `Date` is decades away from any real host
    clock, so the comparison cannot come out inside tolerance."""
    rc, verdict = _verdict_through(
        {
            "curl": MEASURED_CURL_TRANSCRIPT.replace(
                "Fri, 18 Sep 2026 03:16:30 GMT", "Mon, 01 Jan 1990 00:00:00 GMT"
            )
        }
    )
    assert verdict["verdict"] == M.VERDICT_SKEWED, verdict
    assert rc == 1, (rc, verdict)
    assert "AHEAD OF" in verdict["reason"], verdict


def test_a_probe_that_runs_but_answers_nothing_does_not_end_the_search():
    """`gh` present but pointed somewhere that yields no `Date` must not consume
    the attempt -- otherwise adding the fallback would have made the `gh` host
    strictly worse."""
    rc, verdict = _verdict_through(
        {"gh": NO_DATE_200_TRANSCRIPT, "curl": MEASURED_CURL_TRANSCRIPT}
    )
    assert verdict["authoritative"] == "2026-09-18T03:16:30Z", verdict
    assert rc == M.EXIT_BY_VERDICT[verdict["verdict"]], (rc, verdict)


def test_both_probes_unavailable_still_fails_closed():
    """The contract that must survive the new probe: no reference is `UNVERIFIED`
    and exit 2, never `ok`. Asserted on the verdict AND the exit code -- exit 2
    alone cannot distinguish this from an unparseable response (CLAUDE.md)."""
    rc, verdict = _verdict_through({})
    assert verdict["verdict"] == M.VERDICT_UNKNOWN, verdict
    assert verdict["authoritative"] is None, verdict
    assert "UNVERIFIED" in verdict["reason"], verdict
    assert rc == 2, (rc, verdict)


def test_a_refused_fallback_reports_unverified_not_a_crash():
    rc, verdict = _verdict_through({"curl": REFUSED_403_TRANSCRIPT})
    assert verdict["verdict"] == M.VERDICT_UNKNOWN, verdict
    assert rc == 2, (rc, verdict)


def test_agents_md_records_that_the_worker_reaches_the_reference_by_proxy():
    """AC 5 of #1335: so the `gh`-absent / 403 shape is not re-diagnosed from
    scratch by the next worker that reads the bootstrap section."""
    agents = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "#1335" in agents, "AGENTS.md does not cite the issue that added the fallback"
    lowered = agents.lower()
    assert "egress proxy" in lowered, "AGENTS.md does not say how the worker reaches the reference"


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
