#!/usr/bin/env python3
"""Compare this host's clock against GitHub's, and refuse to guess when it cannot.

Run 155 (2026-09-12) opened with `date -u` reporting `2026-09-11T16:05:16Z` while
GitHub's own `Date` response header said `Sat, 12 Sep 2026 03:27:48 GMT` -- 11h22m
of skew, most of a day, across a date boundary. The Conductor came within one
comment of logging the run as a same-day follow-on to run 154 and of scoring a
scheduled workflow's next tick as "not yet due" when it had in fact not yet
happened for a different reason.

**Why a wrong clock is worse than a wrong number.** Every other stale reading the
ledger records (L130's counts, L135's capabilities) is a fact the Conductor looks
up. The clock is the frame it dates every other reading *in*. A stale clock does
not produce one wrong answer; it silently re-dates the whole run -- which #719
comment is newest, whether a scheduled run has ticked since an intervention
(L267), how old a pending gate is, whether a feed is fresh. And it fails in the
reassuring direction: a plausible timestamp, printed confidently, with nothing in
the output marked as uncertain.

**Two sources agreed and both were wrong.** The session's own date reminder said
`2026-09-11`, matching the host. That is L242 exactly -- they are not independent,
because the reminder is derived from the same host clock. The only reading that
does not share that surface is a timestamp minted by a remote server.

This script asks GitHub via `gh api rate_limit --include` and reads the `Date`
response header. The `rate_limit` endpoint is the right probe because it does not
consume the PRIMARY REST quota the Conductor is required to conserve. It is not
unconditionally free -- GitHub's secondary rate limits still apply to it, so a
caller that polled it in a loop could be throttled. That is not this caller: the
check runs **once, at bootstrap**, which is what keeps the cheapness real.

**There is a second probe because one of them is structurally unavailable to half
the fleet (#1335).** The scheduled multi-repo cloud worker has no `gh` CLI at all
-- it reaches GitHub through MCP by design -- so for that session class the `gh`
probe could never run, and the verdict was `UNVERIFIED` on every run since this
check was written. Not a lie, but never a pass: the bootstrap step AGENTS.md
instructs every agent to run had exactly one probe, and it was the one that
session class does not have. That is the clock half of the hole #1237/#1042 found
in the hooks half, one section further down the same list. So `gh` is tried
first, and `curl` against the same endpoint second. Failing closed survives:
`unknown` still requires BOTH probes to come back empty.

The fallback's timestamp has to be GitHub's, not the local egress proxy's -- a
proxy-minted `Date` shares this host's surface and would be worth exactly nothing
(L242), which is the whole reason a remote reference is used. Two things enforce
that: the `Date` is read from the LAST response block in the transcript (the
proxy's `HTTP/1.1 200 Connection Established` is the first), and that block must
identify itself as GitHub's.

It fails CLOSED. An unreachable API, a missing header or an unparseable one all
report `unknown` and exit non-zero, because "I could not check your clock" and
"your clock is fine" must never render identically (L241, L267).

Exit codes: 0 = within tolerance, 1 = skewed beyond tolerance, 2 = could not
determine (fail closed).
"""

from __future__ import annotations

import argparse
import datetime
import email.utils
import json
import os
import subprocess
import sys
from collections.abc import Callable

# Reads one probe's transcript and returns the raw `Date` header value, or None.
DateReader = Callable[[str], "str | None"]

# 120s is chosen to be well inside anything that changes a decision (the finest
# grain the Conductor reasons about is a workflow tick, minutes apart) while
# staying outside ordinary NTP jitter, so this does not cry wolf on a healthy
# host. It is deliberately NOT tight: the failure this exists to catch was eleven
# hours, and a check that fires on two seconds of drift gets muted.
DEFAULT_TOLERANCE_SECONDS = 120

VERDICT_OK = "ok"
VERDICT_SKEWED = "skewed"
VERDICT_UNKNOWN = "unknown"

# The endpoint, pinned BY NAME rather than left to whoever edits next, because
# the cloud worker reaches `api.github.com` through an egress proxy that
# allowlists it PER PATH -- and a path that is not allowlisted comes back with no
# `Date` header at all rather than a wrong one. Measured from a worker sandbox on
# 2026-09-17 and again on 2026-09-18:
#
#     /rate_limit   200 OK          Date PRESENT
#     /             200 OK          Date ABSENT
#     /zen          403 Forbidden   Date ABSENT
#     /meta         403 Forbidden   Date ABSENT
#
# `/zen` is GitHub's canonical liveness endpoint and `/` is the obvious "cheapest
# possible probe"; either is a natural later simplification of a call that exists
# only to read a header. Both exit 0 through `curl`, one of them with a `200 OK`,
# and both would leave this check with no timestamp source and nothing in the
# output to say so. The 403 body compounds it by reading as a general policy
# ("sessions are bound to their configured repositories") when `/rate_limit` is
# itself a non-repo-scoped path that is allowed. Do not swap this endpoint
# without re-measuring all four.
GITHUB_DATE_PROBE_URL = "https://api.github.com/rate_limit"

# Headers that only the GitHub origin sets, used to prove the block a `Date` was
# read from is GitHub's response and not the proxy's. Matched lowercased and
# anchored at the start of the line, like the `Date` scan itself.
GITHUB_ORIGIN_MARKERS = ("server: github.com", "x-github-request-id:")

EXIT_BY_VERDICT = {VERDICT_OK: 0, VERDICT_SKEWED: 1, VERDICT_UNKNOWN: 2}


def parse_http_date(raw: str | None) -> datetime.datetime | None:
    """Parse an RFC 7231 `Date` header into an aware UTC datetime, or None.

    Returns None rather than raising on anything unparseable, so the caller
    reaches the fail-closed `unknown` path instead of dying with a traceback that
    a wrapper could mistake for an unrelated crash.

    `parsedate_to_datetime` raises on some malformed inputs and returns a NAIVE
    datetime for a date with no zone. A naive value is rejected here rather than
    assumed to be UTC: assuming would turn a header we did not understand into a
    confident instant, which is the exact substitution this script exists to
    prevent (and the L57 shape -- a timestamp with no offset is local time).
    """
    if not raw:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(raw.strip())
    except (TypeError, ValueError):
        return None
    if parsed is None or parsed.tzinfo is None:
        return None
    return parsed.astimezone(datetime.timezone.utc)


def decide_clock(
    host_now: datetime.datetime,
    server_now: datetime.datetime | None,
    tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
) -> dict:
    """Compare two instants and return a verdict dict. Pure; no I/O.

    Split out from the fetch so the decision is testable without a network: the
    fetch can only be smoke-tested, and the decision is where a fail-open bug
    would actually live.

    Raises on a naive `host_now` or a non-positive tolerance -- those are
    programming errors in the caller, not conditions of the host, and quietly
    coercing them would mean reporting a comparison that was never made.
    """
    if host_now.tzinfo is None:
        raise ValueError("host_now must be timezone-aware; a naive datetime is local time (L57)")
    if tolerance_seconds <= 0:
        raise ValueError("tolerance_seconds must be positive")

    host_utc = host_now.astimezone(datetime.timezone.utc)

    if server_now is None:
        return {
            "verdict": VERDICT_UNKNOWN,
            "skew_seconds": None,
            "host": host_utc.isoformat().replace("+00:00", "Z"),
            "authoritative": None,
            "tolerance_seconds": tolerance_seconds,
            "reason": (
                "could not read a server timestamp, so this host's clock is UNVERIFIED. "
                "Do not date a run from it: derive `now` from a GitHub timestamp instead"
            ),
        }
    if server_now.tzinfo is None:
        raise ValueError("server_now must be timezone-aware")

    server_utc = server_now.astimezone(datetime.timezone.utc)
    skew = (host_utc - server_utc).total_seconds()

    # Compared on the ABSOLUTE value: a host running fast and a host running slow
    # are both disqualifying, and an earlier draft that tested `skew >` only
    # would have passed the run-155 host if its clock had been ahead instead of
    # behind. Same defect, opposite sign, invisible to the case that motivated it.
    if abs(skew) <= tolerance_seconds:
        verdict, reason = VERDICT_OK, "host clock agrees with GitHub within tolerance"
    else:
        direction = "BEHIND" if skew < 0 else "AHEAD OF"
        reason = (
            f"host clock is {abs(skew):.0f}s ({abs(skew) / 3600:.2f}h) {direction} GitHub. "
            "Date this run from the `authoritative` value below, not from the host"
        )
        verdict = VERDICT_SKEWED

    return {
        "verdict": verdict,
        "skew_seconds": skew,
        "host": host_utc.isoformat().replace("+00:00", "Z"),
        "authoritative": server_utc.isoformat().replace("+00:00", "Z"),
        "tolerance_seconds": tolerance_seconds,
        "reason": reason,
    }


def split_response_blocks(transcript: str) -> list[list[str]]:
    """Split an HTTP transcript into one list of lines per response.

    A new block starts at every status line (`HTTP/...`). Anything before the
    first status line is discarded: it belongs to no response.

    This exists because the fallback's transcript carries TWO responses -- the
    proxy's `HTTP/1.1 200 Connection Established` for the CONNECT, then GitHub's
    own. Reading "the first `Date` in the stream" happens to work today only
    because the CONNECT block has none; a proxy that added one would silently
    substitute a local timestamp for the remote reference this check exists to
    obtain.
    """
    blocks: list[list[str]] = []
    current: list[str] | None = None
    for line in transcript.splitlines():
        if line.upper().startswith("HTTP/"):
            current = []
            blocks.append(current)
        elif current is not None:
            current.append(line)
    return blocks


def _date_header_in(lines: list[str]) -> str | None:
    """Return the raw value of the `Date:` header in one response block, or None.

    Matched case-insensitively on the header NAME only, anchored at the start of
    the line, so a `"date": ...` field inside a JSON body -- which `gh api
    --include` prints after the headers, indented -- cannot be mistaken for it.
    """
    for line in lines:
        if line.lower().startswith("date:"):
            return line.split(":", 1)[1]
    return None


def date_from_gh_transcript(transcript: str) -> str | None:
    """Read the `Date` header out of `gh api --include` output."""
    blocks = split_response_blocks(transcript)
    return _date_header_in(blocks[-1]) if blocks else None


def date_from_curl_transcript(transcript: str) -> str | None:
    """Read GitHub's `Date` header out of a `curl -D -` transcript, or None.

    Two conditions beyond "there is a `Date` somewhere", both aimed at the same
    failure -- a timestamp that did not come from GitHub, or no timestamp at all
    reported as though it were one:

    * the LAST response block is the one read, so the proxy's CONNECT cannot
      supply it;
    * that block must carry a GitHub origin header. The proxy answers a
      non-allowlisted path itself, with `curl` exiting 0, so "the command
      succeeded" says nothing about who replied.

    No status-code check: a `Date` minted by GitHub is a usable instant whatever
    the status, and the refusals this actually sees carry no `Date` at all.
    """
    blocks = split_response_blocks(transcript)
    if not blocks:
        return None
    last = blocks[-1]
    if not any(line.lower().startswith(GITHUB_ORIGIN_MARKERS) for line in last):
        return None
    return _date_header_in(last)


def _run_probe(argv: list[str], timeout_seconds: int) -> str | None:
    """Run one probe command and return its stdout, or None if it could not run.

    Every failure mode collapses to None on purpose -- the binary absent, not
    logged in, offline, a non-zero exit. The caller's job is to try the next
    probe and ultimately report `unknown`; distinguishing "no gh" from "no
    network" here would only tempt a future edit into treating one of them as
    benign.
    """
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def probe_commands(timeout_seconds: int) -> list[tuple[list[str], DateReader]]:
    """The probes to try, in order, each paired with its transcript reader.

    `gh` stays first: it is authenticated, so it does not spend the
    unauthenticated per-IP allowance, and on a host that has it the answer costs
    one call. `curl` is the fallback for the session class that has no `gh`.
    """
    return [
        (["gh", "api", "rate_limit", "--include"], date_from_gh_transcript),
        (
            [
                "curl",
                "-sS",
                "--max-time",
                str(timeout_seconds),
                "-o",
                os.devnull,
                "-D",
                "-",
                GITHUB_DATE_PROBE_URL,
            ],
            date_from_curl_transcript,
        ),
    ]


def fetch_github_date(timeout_seconds: int = 20) -> str | None:
    """Return GitHub's `Date` response header, or None if no probe can read it.

    Tries each probe in order and returns the first `Date` any of them yields. A
    probe that runs but produces no usable timestamp is not treated as an answer
    -- the next one is still tried -- and None is returned only when every probe
    has been exhausted, which is what keeps the fail-closed contract intact.
    """
    for argv, read_date in probe_commands(timeout_seconds):
        transcript = _run_probe(argv, timeout_seconds)
        if transcript is None:
            continue
        raw = read_date(transcript)
        if raw is not None:
            return raw
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Compare this host's clock against GitHub's and fail closed if it cannot be checked. "
            "Run at Conductor bootstrap, before anything is dated from the host."
        )
    )
    ap.add_argument(
        "--tolerance-seconds",
        type=int,
        default=DEFAULT_TOLERANCE_SECONDS,
        help=f"allowed absolute skew (default {DEFAULT_TOLERANCE_SECONDS})",
    )
    ap.add_argument("--json", action="store_true", help="emit the verdict dict as JSON")
    args = ap.parse_args(argv)

    if args.tolerance_seconds <= 0:
        print("error: --tolerance-seconds must be positive", file=sys.stderr)
        return 2

    verdict = decide_clock(
        datetime.datetime.now(datetime.timezone.utc),
        parse_http_date(fetch_github_date()),
        args.tolerance_seconds,
    )

    if args.json:
        print(json.dumps(verdict, indent=2, sort_keys=True))
    else:
        label = {
            VERDICT_OK: "CLOCK: ok",
            VERDICT_SKEWED: "CLOCK: SKEWED",
            VERDICT_UNKNOWN: "CLOCK: UNVERIFIED",
        }[verdict["verdict"]]
        print(f"{label} -- {verdict['reason']}")
        print(f"  host          {verdict['host']}")
        print(f"  authoritative {verdict['authoritative'] or '(unreadable)'}")

    return EXIT_BY_VERDICT[verdict["verdict"]]


if __name__ == "__main__":
    sys.exit(main())
