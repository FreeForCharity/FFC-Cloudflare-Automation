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
response header. `GET /rate_limit` is the right probe because it does not count
against the REST budget the Conductor is required to conserve -- the clock check
is free.

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
import subprocess
import sys

# 120s is chosen to be well inside anything that changes a decision (the finest
# grain the Conductor reasons about is a workflow tick, minutes apart) while
# staying outside ordinary NTP jitter, so this does not cry wolf on a healthy
# host. It is deliberately NOT tight: the failure this exists to catch was eleven
# hours, and a check that fires on two seconds of drift gets muted.
DEFAULT_TOLERANCE_SECONDS = 120

VERDICT_OK = "ok"
VERDICT_SKEWED = "skewed"
VERDICT_UNKNOWN = "unknown"

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


def fetch_github_date(timeout_seconds: int = 20) -> str | None:
    """Return GitHub's `Date` response header, or None if it cannot be read.

    Uses `gh api rate_limit --include`: authenticated (so it does not burn the
    unauthenticated per-IP allowance) and free (`GET /rate_limit` does not count
    against the REST budget).

    Every failure mode collapses to None on purpose -- `gh` absent, not logged
    in, offline, a proxy returning a body with no `Date`. The caller's job is to
    report `unknown`, and distinguishing "no gh" from "no network" here would
    only tempt a future edit into treating one of them as benign.
    """
    try:
        proc = subprocess.run(
            ["gh", "api", "rate_limit", "--include"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    for line in proc.stdout.splitlines():
        # Headers precede the JSON body and are `Name: value`. Matched
        # case-insensitively on the header NAME only, anchored at the start of
        # the line, so a `"date": ...` field inside the JSON body cannot be
        # mistaken for the header.
        if line.lower().startswith("date:"):
            return line.split(":", 1)[1]
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
