#!/usr/bin/env python3
"""Report `agent-ready` issues whose premise has provably left the tree (#1193).

An `agent-ready` issue is a work order. #1077 stayed one for six days after
`761bfdf` fixed both causes it names, because that commit carried no
`Fixes #1077` and nothing else was watching. The cost is not the stale label:
it is that a sandboxed agent taking the top of the pickup queue gets dispatched
to fix code that is already fixed, and burns a whole run discovering that.

Why this keys on the ANCHOR and not on the file
-----------------------------------------------
The obvious detector -- "a file this issue cites has changed since it was
filed" -- was measured against the live backlog in #1193 and flagged **20 of 52
open `agent-ready` issues, 1 of them real**. Dependabot bumps and a single
comment-fix commit to `tests/workflow-logic/run_all.py` light up a third of the
backlog. At that precision the report is noise, and a report that is noise is
worse than no report, because it gets muted.

Issues in this repository quote the offending line. That quote is the premise,
and it is checkable. #1077 quotes

    .\\Update-CloudflareDns.ps1 -Zone $Domain -List -ErrorAction SilentlyContinue

which is no longer in `105-manage-record.yml` (the call is now `-ExportAll
-OutputFile`), so the premise is provably gone. #1060's cited file *did* change
-- `eeee163` edited `check-pwsh-workflow-invocations.py` -- but only to make the
guard state its blind spot; its anchor is still there, so this sweep is silent
on it. **A file changing is not the defect leaving.** That distinction is the
whole design, and `tests/workflow-logic/test_agent_ready_anchors.py` pins it as
the discriminating case.

What it reports
---------------
`PREMISE MAY BE GONE`  every cited path that still exists was searched, and the
                       anchor is in none of them. Named with the anchor, the
                       paths searched, and the commits touching those paths
                       since the issue was filed, so a human has the candidate
                       fix in hand.
`PATH GONE`            a cited path that git has history for no longer exists.
                       A different and usually more serious case.
`UNVERIFIABLE —        the issue was filed before the oldest commit this
HISTORY HORIZON`       checkout holds, so the was-it-ever-there check below
                       could not run for it at all. Reported with the remedy,
                       and counted apart from the backlog's own limits: this is
                       a fact about the CLONE, not about the issue.

MAY, deliberately. This reports and a human decides. It never closes, labels,
comments on, or edits anything -- an auto-closer that is wrong re-buries live
work, and #1077 was closed by a Conductor holding the evidence.

Why the history horizon is a finding and not a footnote
-------------------------------------------------------
Every verdict here rests on `content_at`, and `content_at` needs a commit older
than the issue. On a truncated checkout there is none, so it answers None for
every path -- and None means "say nothing". `_require_git` already refuses to
run when git cannot answer *at all*, for exactly this reason; a shallow clone
reaches the same unverifiable state while every individual answer still looks
like the ordinary "that was not there then". Measured on a cloud worker's
truncated checkout (horizon `2026-08-04`, backlog of 55): **24 of 55
`agent-ready` issues sat behind the horizon** -- the summary line read
`55 scanned ... 9 with no usable anchor`, which implies 46 were examined when 31
were.

The sharper half is that the one limit the sweep *did* publish named the wrong
cause. Of those 9 "no usable anchor" issues, **8 were horizon cases**: the sweep
had no history for them, reported the failure as a property of their prose, and
a reader improving those issues' wording would have changed nothing. Correct
attribution takes that count 9 -> 1. The 24 are also where the oldest and
least-revisited work orders live, which is where staleness concentrates.

So the horizon is surfaced, listed, and made non-zero. The sweep's contract is
that it never reports from an inability to look, and the count of issues it
could not look at belongs in the same sentence as the count it cleared.

The honest limits of the technique
----------------------------------
Anchor extraction is a heuristic and it is stated, not hidden. Every run prints
the count of issues from which **no usable anchor could be extracted**; those
issues are simply invisible to this sweep, and that number is the denominator a
reader needs to judge the rest. Three filters keep precision up, each of which
also costs recall:

  * an anchor must be >= 12 characters, and must not be a bare identifier or
    plain prose -- those false-positive the moment something is renamed;
  * fenced blocks tagged as a shell language are dropped whole, and so is an
    untagged fence whose first non-empty line opens with a command invocation
    (`python3 `, `gh `, `npx `, ...) -- block-level, so a wrapped command's
    continuation line goes with it rather than surviving as `--repo … --json`.
    Those are the issue's own *verification* steps, which were never in any
    file and would otherwise report on every well-written issue;
  * an anchor is reported only when it is absent from **every** cited path that
    exists. Present in any one of them means the premise is still live
    somewhere, and this stays quiet.

A cited path that does not exist and that git has no history for is a **proposed
new file** -- issues that ask for a new script name one in almost every case --
so it is skipped in silence rather than reported as `PATH GONE`.

Comparison is whitespace-normalised on both sides, so re-indenting a `run:` body
does not read as the line having been deleted.

Cost: one paginated issue listing (~1 call), plus one `git log` per cited path
that produced a finding. Bounded, local, and it does not loop.

Authentication is a single environment variable, ``GH_TOKEN`` (also accepts
``GITHUB_TOKEN``), matching ``scripts/audit-agentic-os-board.py``.

Examples:
  GH_TOKEN=... python3 scripts/audit-agent-ready-anchors.py
  GH_TOKEN=... python3 scripts/audit-agent-ready-anchors.py --json
  GH_TOKEN=... python3 scripts/audit-agent-ready-anchors.py --repo FreeForCharity/FFC-IN-ffcadmin.org --root ../FFC-IN-ffcadmin.org

Exit codes:
  0  nothing to report. The prose mode prints NOTHING on stdout in this case
     (`--json` always emits a document; a machine consumer asked for one). The
     denominators still go to stderr, so a clean run is quiet without being
     unaccountable.
  1  at least one finding, OR any API, auth or config error. **Never 0 on a
     failed enumeration**: a sweep that could not read the backlog has no
     verdict, and reporting "clean" would be the silence-read-as-green shape
     #966 is about. That covers the LOCAL half too -- a missing or broken git
     aborts, because with no history every anchor reads as unverifiable and the
     run would otherwise exit 0 having examined nothing.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

API_ROOT = "https://api.github.com"
USER_AGENT = "ffc-agent-ready-anchor-auditor"
HTTP_TIMEOUT = 30  # seconds; fail closed rather than hang a Conductor run.

DEFAULT_REPO = "FreeForCharity/FFC-Cloudflare-Automation"
LABELS = "agentic-os,agent-ready"

# An anchor shorter than this is a token, not a quote. `curl.exe` (8) names a
# thing; `-Zone $Domain -List` (20) names a call. Only the second can be checked
# for presence without false-positiving on every rename.
MIN_ANCHOR_LEN = 12

# Directories whose contents an issue in this repo cites as a repo-relative
# path. Anything outside them is prose that happens to contain a slash.
PATH_ROOTS = (
    r"\.github/workflows",
    r"\.github/actions",
    r"\.claude/hooks",
    r"\.claude/skills",
    r"tests/workflow-logic",
    r"scripts",
    r"docs",
)
PATH_RE = re.compile(
    r"(?<![\w/.-])(?:" + "|".join(PATH_ROOTS) + r")/[A-Za-z0-9._/-]*[A-Za-z0-9_]"
)

# Issues name a workflow by bare basename far more often than by full path
# ("228 dies at ...", "`105-manage-record.yml`"). Resolving those is what makes
# AC1 report the file the issue actually talks about.
WORKFLOW_BASENAME_RE = re.compile(r"(?<![\w/-])(\d{3}-[A-Za-z0-9._-]+\.ya?ml)(?![\w/-])")
WORKFLOW_DIR = ".github/workflows"

# Fenced blocks in these languages are things to RUN, not source that was
# quoted out of a file. Every issue in this repo has a verification block, and
# without this the sweep reports `PREMISE MAY BE GONE` on all of them.
SHELL_LANGS = {
    "bash",
    "sh",
    "shell",
    "shell-session",
    "console",
    "zsh",
    "terminal",
    "text",
}

# The same defence for inline spans and for untagged fences, which carry no
# language to filter on. A span that opens this way is an invocation.
COMMAND_PREFIXES = (
    "python3 ",
    "python ",
    "py ",
    "gh ",
    "git ",
    "npx ",
    "npm ",
    "pnpm ",
    "node ",
    "az ",
    "curl ",
    "pip ",
    "pwsh ",
    "bash ",
    "sh ",
    "cd ",
    "export ",
    "sudo ",
    "$ ",
    "GH_TOKEN=",
    "GITHUB_TOKEN=",
)

FENCE_RE = re.compile(r"^```([A-Za-z0-9._+-]*)\s*$")
INLINE_SPAN_RE = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")

BARE_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
PROSE_RE = re.compile(r"[A-Za-z][A-Za-z ]*")
WHITESPACE_RE = re.compile(r"\s+")


# --------------------------------------------------------------------------
# transport
# --------------------------------------------------------------------------


def _token():
    """The API token, or abort.

    Aborting is the point. An unauthenticated sweep enumerates zero issues and
    then reports zero findings -- a clean bill of health produced by having no
    access at all."""
    tok = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not tok:
        raise SystemExit(
            "error: set GH_TOKEN (or GITHUB_TOKEN) with repo read access. "
            "Refusing to run: an unauthenticated sweep reports an empty backlog as a clean one."
        )
    return tok


def _link_rel(link_header, rel):
    """Return the URL for a given ``rel`` from a GitHub Link header, or None."""
    if not link_header:
        return None
    target = f'rel="{rel}"'
    for part in link_header.split(","):
        segments = part.split(";")
        if len(segments) < 2:
            continue
        url = segments[0].strip().lstrip("<").rstrip(">")
        for seg in segments[1:]:
            if seg.strip() == target:
                return url
    return None


def _build_url(path_or_url, params=None):
    if path_or_url.startswith("http"):
        url = path_or_url
    else:
        url = f"{API_ROOT}/{path_or_url.lstrip('/')}"
    if params:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}{urllib.parse.urlencode(params)}"
    return url


def _request(path_or_url, token, params=None):
    """Perform ONE REST GET and return ``(payload, link_header)``.

    No soft-fail: the single call this script makes is the whole input, so
    there is no HTTP error whose correct handling is "carry on with less"."""
    url = _build_url(path_or_url, params)
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", USER_AGENT)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            return payload, resp.headers.get("Link")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace").strip()
        raise SystemExit(f"error: GitHub API {exc.code} for {url}: {detail}")
    except urllib.error.URLError as exc:
        raise SystemExit(f"error: could not reach GitHub API ({url}): {exc.reason}")


def rest_get_all(path_or_url, token, params=None, _request_fn=None):
    """GET a REST **list** endpoint, following Link ``rel="next"`` to the end.

    A non-list payload is a shape error rather than something to tolerate: an
    object where a list was expected is how a 200-with-an-error-body slips
    through as zero rows, which is exactly the falsely-clean report this sweep
    must never produce."""
    request_fn = _request_fn or _request
    url = _build_url(path_or_url, params)
    results = []
    while url:
        payload, link = request_fn(url, token)
        if not isinstance(payload, list):
            raise SystemExit(
                f"error: expected a JSON array from {url}, got {type(payload).__name__}. "
                "Refusing to treat an unexpected response shape as an empty page."
            )
        results.extend(payload)
        url = _link_rel(link, "next")
    return results


def list_agent_ready_issues(repo, token, _request_fn=None):
    """Open issues carrying BOTH `agentic-os` and `agent-ready`.

    `GET /issues` returns pull requests too; a PR is not a work order and its
    body quotes the fix rather than the defect, so they are dropped here."""
    rows = rest_get_all(
        f"repos/{repo}/issues",
        token,
        params={"state": "open", "labels": LABELS, "per_page": 100},
        _request_fn=_request_fn,
    )
    return [r for r in rows if isinstance(r, dict) and "pull_request" not in r]


# --------------------------------------------------------------------------
# the tree under test
# --------------------------------------------------------------------------


class Tree:
    """Read-only view of the working tree, injectable so tests need no fixtures
    on disk and do not decay when the real files are fixed."""

    def __init__(self, root=REPO_ROOT):
        self.root = pathlib.Path(root)
        self._git_ok = None

    def _require_git(self):
        """Abort unless `root` is a readable git checkout.

        Checked once, before the first history question, because every
        `PREMISE MAY BE GONE` depends on being able to read the past: if git
        cannot answer, `content_at` returns None for every anchor, nothing is
        reported, and the run exits 0. That is a clean bill of health produced
        by having no history at all -- the same shape as the unauthenticated
        sweep `_token()` refuses, one input over.

        Deliberately NOT a per-call check on the exit code: `git cat-file -t
        <rev>:<path>` exits non-zero exactly when the object is absent, which is
        the meaningful negative this sweep is built on. Failing on that would
        abort on the ordinary case."""
        if self._git_ok is None:
            self._git_ok = _run_git(["rev-parse", "--git-dir"], self.root).returncode == 0
            if not self._git_ok:
                raise SystemExit(
                    f"error: {self.root} is not a readable git checkout, so the "
                    "was-it-ever-there check cannot run. Refusing to continue: with no "
                    "history every anchor reads as unverifiable and the sweep would report "
                    "a clean backlog it never examined."
                )

    def _within(self, path):
        """`root / path` resolved, or None if it escapes the checkout.

        Every path this class touches was extracted from an ISSUE BODY, which
        anyone able to file an issue controls. The path pattern legitimately
        allows `.` and `/` (real citations contain both), so it also admits
        `scripts/../../etc/hostname`, and `root / path` resolves that happily.

        Containment is verified rather than `..` blacklisted, matching
        `d2cdef9` on this branch: a blacklist has to enumerate every spelling
        of an escape (`..`, an absolute path, a symlink out of the tree), while
        resolving and comparing answers all three at once -- `resolve()`
        follows symlinks, so a link inside the repo pointing out is caught too.

        The leak this closes is not just existence. `read` feeds the anchor
        comparison, and the attacker supplies the anchor as well as the path,
        so an uncontained read is a content ORACLE over any readable file on
        the host: quote a guess, see whether the issue is reported."""
        try:
            resolved = (self.root / path).resolve()
        except (OSError, RuntimeError):  # RuntimeError: symlink loop
            return None
        root = self.root.resolve()
        return resolved if resolved == root or resolved.is_relative_to(root) else None

    def exists(self, path):
        target = self._within(path)
        return bool(target) and target.is_file()

    def read(self, path):
        """`path`'s current content.

        An OSError here is an ENVIRONMENT failure, never an answer. `read` is
        only ever reached for a path `exists()` has already confirmed, so an
        unreadable file is a permission or filesystem fault rather than a
        premise that left the tree. Returning '' for it makes every anchor in
        that file read as absent, and the issue is then reported as
        PREMISE MAY BE GONE -- a finding manufactured by the sweep's own
        inability to look, pointing a reader at a premise that is still there.

        Note the direction, because it is the opposite of the two aborts above
        and of the review that found it: those failed toward a clean backlog,
        this one fails toward a false finding. Both are the same defect -- an
        inability to read reported as a fact about the tree -- and the same
        split applies: the environment aborts, while a real answer about one
        object (a path that is simply absent) stays a quiet negative.

        `errors="replace"` is deliberate and is NOT that same defect, though it
        reads like it. An anchor always arrives from an issue body over the API,
        so it is valid UTF-8 and can never contain U+FFFD; replacement can
        therefore only ever *remove* a match, never invent one. And the
        historical side decodes with the same policy (`_run_git`), so an
        undecodable byte reads identically in both halves of the comparison and
        cancels out. Aborting on a decode instead would turn one stray byte in
        any cited file into a total outage of the sweep.

        The symmetry is load-bearing: change either side's error policy alone
        and the two halves stop being comparable, silently.
        `test_an_undecodable_byte_cannot_fabricate_or_hide_a_finding` pins the
        behaviour in both directions and pins that the two policies still
        match."""
        target = self._within(path)
        if target is None:
            # Unreachable through `audit`, which only reads paths `exists()`
            # accepted -- so this cannot be tripped from an issue body and is
            # not a denial-of-service vector. It is here so the containment
            # rule holds at every filesystem entry point rather than only at
            # the one the current caller happens to use.
            raise SystemExit(
                f"error: refusing to read {path!r}: it resolves outside {self.root}."
            )
        try:
            return target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise SystemExit(
                f"error: cannot read {self.root / path} ({exc.strerror or exc}), "
                "though it exists. Refusing to continue: every anchor in an unreadable "
                "file reads as absent, so the issue citing it would be reported as a "
                "premise that is gone."
            )

    def content_at(self, path, when):
        """`path`'s content at the last commit before `when`, or None.

        This is the premise-was-once-true side of the check, and it is what
        separates a deleted file from one the issue is asking to have created.
        None means "cannot be established here" -- an unresolvable revision, a
        path that did not exist then, or a shallow clone whose horizon is later
        than the issue. Every caller treats None as "say nothing": a sweep that
        cannot read the past must not guess about it."""
        if not when:
            return None
        self._require_git()
        rev = _git(["rev-list", "-1", f"--before={when}", "HEAD"], self.root).strip()
        if not rev:
            return None
        # `git show <rev>:<dir>` prints a TREE LISTING, not an error, so a cited
        # directory ("the baseline lives under `docs/data`") comes back as
        # plausible content and reads as a deleted file. #859 was reported
        # exactly that way on the first live run. Ask for the object type first
        # and accept nothing but a blob.
        if _git(["cat-file", "-t", f"{rev}:{path}"], self.root).strip() != "blob":
            return None
        # `content or None` would be wrong here: a legitimately EMPTY file is a
        # zero-length blob, and folding it into None makes "existed but was
        # empty" indistinguishable from "never existed" -- so deleting an empty
        # file is never reported as PATH GONE. The `cat-file -t` check above has
        # already established that a blob exists at this rev, which is exactly
        # the existence question the sentinel answers, so "" can be returned
        # honestly as content.
        return _git(["show", f"{rev}:{path}"], self.root)

    def commits_since(self, path, since):
        """Commits touching `path` since `since` -- advisory, never load-bearing.

        The verdict comes from the tree; this is the candidate fix handed to a
        human. A shallow clone can only see back to its horizon, so an empty
        list means "none found here", not "none exist", and never changes
        whether something is reported."""
        if not since:
            return []
        self._require_git()
        out = _git(
            ["log", f"--since={since}", "--format=%h %ad %s", "--date=short", "--", path],
            self.root,
        )
        return [line.strip() for line in out.splitlines() if line.strip()]

    def history_horizon(self):
        """ISO-8601 commit date of the OLDEST commit this checkout can read.

        `content_at` needs a commit that predates the issue. `git rev-list -1
        --before=<t> HEAD` returns nothing when `t` is older than every commit
        present, so on a truncated checkout it returns None for every path --
        the same unverifiable state `_require_git` aborts on, reached by having
        *some* history rather than none, and silent because each individual
        answer looks like the ordinary "was not there then".

        The earliest reachable commit is necessarily parentless (anything with a
        parent has an older ancestor), so the boundary set is
        `--max-parents=0` -- the grafted shallow boundaries on a truncated
        clone, the real root commit on a complete one. Taking the MINIMUM is
        what makes this one query correct for both: a full clone's root predates
        every issue, so nothing is withheld and no special case is needed.

        Returns None only when git answers nothing at all, which
        `_require_git` has already ruled out."""
        self._require_git()
        boundaries = [
            line.strip()
            for line in _git(["rev-list", "--max-parents=0", "HEAD"], self.root).splitlines()
            if line.strip()
        ]
        dates = []
        for rev in boundaries:
            date = _git(["log", "-1", "--format=%cI", rev], self.root).strip()
            if date:
                dates.append(date)
        return earliest_instant(dates)

    def shallow(self):
        """True when this checkout is truncated. Advisory: it names the CAUSE in
        the report, never the verdict -- the horizon comparison stands on its
        own, so a `--depth` that happens to reach far enough is not reported."""
        self._require_git()
        return _git(["rev-parse", "--is-shallow-repository"], self.root).strip() == "true"


def _as_instant(iso):
    """An aware datetime from an ISO-8601 stamp, or None if it will not parse.

    The two clocks compared here are spelled differently and neither is ours:
    the API renders `created_at` as `2026-08-12T04:20:19Z`, and `git --format=%cI`
    renders the committer's own offset (`-04:00`). `fromisoformat` rejects the
    `Z` before Python 3.11, so it is normalised rather than assumed.

    None on anything unparseable, and every caller treats None as "cannot
    establish" -- the same fail-closed reading the rest of this sweep gives an
    unanswerable question, never a comparison against a guessed clock."""
    if not iso:
        return None
    text = iso.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    # A naive stamp would raise on comparison against an aware one, which is a
    # crash rather than a wrong answer -- but only at the moment two differently
    # -spelled inputs meet, which is not a case any fixture reaches by accident.
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


def earliest_instant(dates):
    """The earliest of several ISO stamps, compared as INSTANTS not as text.

    Split out of `history_horizon` so it can be pinned without a checkout: the
    hazard it guards is invisible on any real boundary set that happens to agree
    either way, which is the case on the checkout this was written against.

    `%cI` renders each committer's own zone, so one `rev-list` carries a mix
    (`+00:00` and `-04:00` boundaries, measured). A lexical min over those picks
    by wall clock and can select a LATER instant -- `17:30-04:00` sorts before
    `20:00+00:00` and is 90 minutes later. Choosing the later one understates
    the history available, so issues that COULD have been checked get reported
    as unread: noise, in the direction that erodes trust in the report.

    Unparseable stamps are dropped rather than ordered, and None is returned
    only when nothing is parseable -- which every caller reads as "cannot
    establish", never as "no limit"."""
    usable = [d for d in dates if _as_instant(d) is not None]
    if not usable:
        return None
    return min(usable, key=_as_instant)


def predates_horizon(created, horizon):
    """True when an issue filed at `created` is older than everything git holds.

    Separate from the sweep so a test can pin the comparison without a
    checkout, and so the fail-closed direction is stated in one place: an
    unparseable stamp on EITHER side answers False -- "not established" -- so a
    clock this function cannot read never silently converts a verified issue
    into an unverifiable one, nor the reverse. The caller's own checks still
    run either way; this only decides attribution."""
    left, right = _as_instant(created), _as_instant(horizon)
    if left is None or right is None:
        return False
    return left < right


def _git(args, cwd):
    """stdout of one read-only git command ('' when git answers non-zero)."""
    return _run_git(args, cwd).stdout or ""


def _run_git(args, cwd):
    """Run one read-only git command and return the CompletedProcess.

    `encoding=` is pinned because this repository's commit subjects carry
    em-dashes and arrows, and text-mode subprocess decodes with cp1252 on a
    Windows host (#945). git is not a Python child, so the parent's decode is
    the only side that needs pinning (#962).

    An ENVIRONMENT failure -- git absent from PATH, or a command that hangs --
    aborts the run rather than returning ''. Swallowing it would turn every
    history question into "cannot establish", which this sweep reads as "say
    nothing", so a host with no git would report a clean backlog and exit 0. A
    non-zero EXIT is a different thing and is NOT an error here: it is how git
    reports an absent object, which is the answer `content_at` needs.

    `MSYS_NO_PATHCONV=1` is not optional on the Conductor's git-bash host: MSYS
    rewrites a `<rev>:<path>` argument whose path starts with a dot, so
    `git show abc123:.github/workflows/x.yml` arrives as
    `abc123\\.github\\workflows\\x.yml` and git rejects it (CLAUDE.md, L42).
    Every workflow path this sweep resolves starts with that dot."""
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            env={**os.environ, "MSYS_NO_PATHCONV": "1"},
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            encoding="utf-8",
            errors="replace",
            timeout=HTTP_TIMEOUT,
            check=False,
        )
    except FileNotFoundError:
        raise SystemExit(
            "error: git is not on PATH, so the was-it-ever-there check cannot run. "
            "Refusing to continue: without it every anchor reads as unverifiable and "
            "the sweep would report a clean backlog it never examined."
        )
    except subprocess.TimeoutExpired:
        raise SystemExit(
            f"error: git timed out after {HTTP_TIMEOUT}s running `git {' '.join(args)}` in {cwd}. "
            "Refusing to continue on a partial history read."
        )
    except OSError as exc:
        raise SystemExit(f"error: could not run git in {cwd}: {exc}")


# --------------------------------------------------------------------------
# anchor + path extraction
# --------------------------------------------------------------------------


def _strip_fenced_blocks(body):
    """Split a body into (prose, fenced_blocks) where each block is
    ``(language, [lines])``. Fences are removed from the prose so an inline-span
    scan cannot re-harvest a shell block's contents."""
    prose, blocks = [], []
    lang, buf, inside = None, [], False
    for line in body.splitlines():
        fence = FENCE_RE.match(line.strip())
        if fence:
            if inside:
                blocks.append((lang, buf))
                lang, buf, inside = None, [], False
            else:
                lang, buf, inside = (fence.group(1) or "").lower(), [], True
            continue
        (buf if inside else prose).append(line)
    if inside:  # unterminated fence: keep what we saw
        blocks.append((lang, buf))
    return "\n".join(prose), blocks


def extract_paths(body):
    """Repo-relative paths named anywhere in the body, in first-seen order."""
    seen, out = set(), []
    for match in PATH_RE.finditer(body):
        path = match.group(0)
        if path not in seen:
            seen.add(path)
            out.append(path)
    for match in WORKFLOW_BASENAME_RE.finditer(body):
        path = f"{WORKFLOW_DIR}/{match.group(1)}"
        if path not in seen:
            seen.add(path)
            out.append(path)
    return out


def is_anchor(span):
    """Is this code span a checkable quote from a file?

    Everything rejected here is rejected because reporting it would produce a
    finding nobody can act on: a renamed identifier, a sentence, a path
    citation, or the issue's own verification command."""
    span = span.strip()
    if len(span) < MIN_ANCHOR_LEN or "\n" in span:
        return False
    if span.startswith("http://") or span.startswith("https://"):
        return False
    if BARE_IDENTIFIER_RE.fullmatch(span):  # a rename, not a deletion
        return False
    if PROSE_RE.fullmatch(span):  # a sentence in backticks
        return False
    if PATH_RE.fullmatch(span) or WORKFLOW_BASENAME_RE.fullmatch(span):
        return False  # a path citation is a path, not an anchor
    lowered = span.lower()
    if any(lowered.startswith(prefix.lower()) for prefix in COMMAND_PREFIXES):
        return False
    return True


def _is_command_block(lines):
    """Does this UNTAGGED fence hold shell commands?

    Classified by the FIRST non-empty line, and the classification then covers
    the whole block. Deciding line-by-line is what leaks a continuation:
    `--repo … --json` on the second line of a wrapped invocation opens with no
    command name, is well over 12 characters and is full of code punctuation,
    so every span-level filter accepts it.

    Line-level rejection cannot be tightened to fix that. An anchor legitimately
    starts with a flag — #1077's own premise is quoted inline as
    `-Zone $Domain -List -ErrorAction SilentlyContinue` — so a rule keyed on the
    leading `-` would throw away the case this sweep exists for. The block is
    the only level at which a continuation is distinguishable from a fragment."""
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        lowered = stripped.lower()
        return any(lowered.startswith(prefix.lower()) for prefix in COMMAND_PREFIXES)
    return False


def extract_anchors(body):
    """Checkable anchors from a body, in first-seen order.

    Inline spans, plus the lines of fenced blocks that are not tagged as a
    shell language -- a `bash` block is the issue telling you how to verify it,
    and its commands were never in any file."""
    prose, blocks = _strip_fenced_blocks(body)
    candidates = [m.group(1) for m in INLINE_SPAN_RE.finditer(prose)]
    for lang, lines in blocks:
        if lang in SHELL_LANGS:
            continue
        # An untagged fence carries no language to filter on, so it is
        # classified by what it opens with instead.
        if not lang and _is_command_block(lines):
            continue
        candidates.extend(lines)
    seen, out = set(), []
    for cand in candidates:
        cand = cand.strip()
        if cand in seen or not is_anchor(cand):
            continue
        seen.add(cand)
        out.append(cand)
    return out


def normalize(text):
    """Collapse whitespace so re-indenting a `run:` body does not read as the
    line having been deleted."""
    return WHITESPACE_RE.sub(" ", text).strip()


# --------------------------------------------------------------------------
# the sweep
# --------------------------------------------------------------------------


def audit_issue(issue, tree):
    """Classify ONE issue. Returns ``(premise_findings, path_findings, usable)``.

    `usable` is "this issue cited a path that exists AND quoted an anchor", i.e.
    the sweep had something it could check -- NOT "the sweep said something".
    The two come apart on the path-only branch, which is why `audit()` tests all
    three values before counting an issue as invisible rather than `usable`
    alone; see the comment at that call site."""
    body = issue.get("body") or ""
    number = issue.get("number")
    title = issue.get("title") or ""
    created = issue.get("created_at") or ""

    cited = extract_paths(body)
    present = [p for p in cited if tree.exists(p)]
    missing = [p for p in cited if not tree.exists(p)]

    # Cached: one revision resolution per (path, issue) rather than per anchor.
    was = {}

    def content_when_filed(path):
        if path not in was:
            snapshot = tree.content_at(path, created)
            # `if snapshot` again folds the empty file into "no history";
            # `is not None` keeps the two apart all the way to the caller.
            was[path] = normalize(snapshot) if snapshot is not None else None
        return was[path]

    path_findings = []
    for path in missing:
        # A path that held no content when the issue was filed was never in the
        # tree: the issue is asking for it to be CREATED, which nearly every
        # feature request does. It also rejects a cited directory, which has
        # git history but no content of its own.
        if content_when_filed(path) is not None:
            path_findings.append(
                {
                    "issue": number,
                    "title": title,
                    "url": issue.get("html_url", ""),
                    "kind": "PATH GONE",
                    "path": path,
                    "commits": tree.commits_since(path, created),
                }
            )

    premise_findings = []
    anchors = extract_anchors(body)
    usable = bool(present and anchors)
    if usable:
        contents = {p: normalize(tree.read(p)) for p in present}
        for anchor in anchors:
            needle = normalize(anchor)
            # Absent from EVERY cited path that exists. Present in any one of
            # them means the premise is still live somewhere.
            if any(needle in blob for blob in contents.values()):
                continue
            # ...and it has to have BEEN there. Without this, every version
            # range, log line, stack frame, line-range citation, search query
            # and block of proposed-new code in a body reads as a deletion.
            # Measured against this backlog: 408 rows over 45 of 53 issues
            # before the check, 5 rows over 3 issues after it.
            was_in = []
            for path in present:
                snapshot = content_when_filed(path)
                if snapshot and needle in snapshot:
                    was_in.append(path)
            if not was_in:
                continue
            commits = []
            for path in was_in:
                commits.extend(tree.commits_since(path, created))
            premise_findings.append(
                {
                    "issue": number,
                    "title": title,
                    "url": issue.get("html_url", ""),
                    "kind": "PREMISE MAY BE GONE",
                    "anchor": anchor,
                    "paths_searched": list(was_in),
                    "commits": commits,
                }
            )

    return premise_findings, path_findings, usable


def audit(issues, tree):
    """Sweep the backlog. Pure over its inputs -- no network, no globals."""
    premise, gone, no_anchor, unverifiable = [], [], [], []
    horizon = tree.history_horizon()
    # `shallow` is read once and only to explain the horizon in the report. The
    # verdict is the date comparison alone, so a complete clone whose root
    # predates the backlog withholds nothing and this stays empty.
    truncated = tree.shallow() if horizon else False
    for issue in issues:
        p, g, usable = audit_issue(issue, tree)
        premise.extend(p)
        gone.extend(g)
        if horizon and predates_horizon(issue.get("created_at") or "", horizon):
            # Attribution, not a second bucket for the same fact: every
            # `content_at` for this issue answered None because the revision is
            # not here, so "no usable anchor" would name the wrong cause and
            # `scanned` would imply it was examined. Findings still stand if the
            # path-only branch produced any -- that branch needs no history.
            unverifiable.append(
                {
                    "issue": issue.get("number"),
                    "title": issue.get("title") or "",
                    "url": issue.get("html_url", ""),
                    "created_at": issue.get("created_at") or "",
                }
            )
            continue
        # "Invisible to this sweep" has to mean the sweep said NOTHING about the
        # issue. `usable` alone is the wrong test: a PATH GONE finding comes from
        # the path-only branch, where every cited path is missing, so `present`
        # is empty and `usable` is False — and the issue would be reported and
        # simultaneously counted as one this sweep could not see into. That
        # inflates the one number #1193 asks to be kept honest, in the direction
        # that makes the technique look blinder than it is. Measured on the first
        # live run: #859 appeared in both buckets.
        if not usable and not p and not g:
            no_anchor.append(
                {
                    "issue": issue.get("number"),
                    "title": issue.get("title") or "",
                    "url": issue.get("html_url", ""),
                }
            )
    return {
        "scanned": len(issues),
        "premise_may_be_gone": premise,
        "path_gone": gone,
        "no_anchor_extracted": no_anchor,
        "unverifiable_history": unverifiable,
        "history_horizon": horizon,
        "shallow_checkout": truncated,
    }


def has_findings(result):
    """The exit-code decision, in one place so a test can assert it in-process.

    A subprocess run against a clean fixture only ever exercises the zero path,
    and would pass against a `main` that returned 0 unconditionally (#912/#927).

    `no_anchor_extracted` is deliberately NOT a finding: it is the technique's
    stated limit, not something wrong with the backlog.

    `unverifiable_history` IS one, and the asymmetry is the point. An issue with
    no usable anchor was examined and had nothing checkable in it; an issue
    behind the history horizon was never examined at all, and the sweep cannot
    tell a live premise from a dead one for it. `audit-agentic-os-board.py`
    settles this for the repo -- non-zero on any finding **or any enumeration it
    could not complete, never 0 on a read it could not finish** -- and
    `_require_git` already applies it to the total absence of history. This is
    the same condition reached with a truncated clone instead of no clone."""
    return bool(
        result["premise_may_be_gone"]
        or result["path_gone"]
        or result.get("unverifiable_history")
    )


def render(result, repo):
    """The prose report. Only ever called when there is something to say."""
    lines = [f"Agent-ready anchor sweep — {repo}", ""]
    for row in result["path_gone"]:
        lines.append(f"PATH GONE  #{row['issue']} — {row['title']}")
        lines.append(f"  path:    {row['path']} (cited, has git history, no longer in the tree)")
        lines.append(f"  url:     {row['url']}")
        for commit in row["commits"][:5]:
            lines.append(f"  commit:  {commit}")
        lines.append("")
    for row in result["premise_may_be_gone"]:
        lines.append(f"PREMISE MAY BE GONE  #{row['issue']} — {row['title']}")
        lines.append(f"  anchor:  {row['anchor']}")
        lines.append(f"  absent from: {', '.join(row['paths_searched'])}")
        lines.append(f"  url:     {row['url']}")
        for commit in row["commits"][:5]:
            lines.append(f"  commit:  {commit}")
        lines.append("")
    lines.extend(render_horizon(result))
    lines.append(summary_line(result))
    lines.append(
        "MAY, deliberately: this reports, a human closes. Confirm against the commits above "
        "before touching an issue."
    )
    return "\n".join(lines)


def render_horizon(result):
    """The horizon block: what could not be examined, and how to fix it.

    Names the remedy, because this is the one finding in the sweep that is about
    the CHECKOUT rather than about the backlog -- nobody reading it should have
    to work out that the issues are fine and the clone is short. Listed rather
    than merely counted, since which issues are hidden is the part a reader
    needs in order to go and check them by hand."""
    rows = result.get("unverifiable_history") or []
    if not rows:
        return []
    horizon = result.get("history_horizon") or "unknown"
    lines = [
        f"UNVERIFIABLE — HISTORY HORIZON  {len(rows)} issue(s) filed before this checkout begins",
        f"  oldest commit here: {horizon}"
        + ("  (shallow clone)" if result.get("shallow_checkout") else ""),
        "  For these the was-it-ever-there check cannot run, so a premise that HAS gone is",
        "  indistinguishable from one still live. They are not cleared -- they are unread.",
        "  Remedy: `git fetch --unshallow` here, or `fetch-depth: 0` on actions/checkout.",
    ]
    for row in rows:
        lines.append(f"  #{row['issue']} (filed {row['created_at']}) — {row['title']}")
    lines.append("")
    return lines


def summary_line(result):
    """The denominators. Printed on every run, findings or not -- the count of
    issues this sweep could not see into is the honest limit of the technique
    and must never be hidden."""
    unverifiable = len(result.get("unverifiable_history") or [])
    # `scanned` is what was listed, which is not what was read. Before this the
    # line reported `scanned` against one limit (`no usable anchor`) and so
    # implied every other issue had been examined -- on a shallow checkout that
    # overstated the examined set by roughly 3x here, in the reassuring
    # direction. `examined` is stated because it is the denominator the two
    # finding counts are actually out of.
    examined = result["scanned"] - unverifiable
    line = (
        f"{result['scanned']} agent-ready issues scanned, {examined} examined; "
        f"{len(result['premise_may_be_gone'])} premise-may-be-gone, "
        f"{len(result['path_gone'])} path-gone, "
        f"{len(result['no_anchor_extracted'])} with no usable anchor "
        f"(invisible to this sweep)"
    )
    if unverifiable:
        line += f", {unverifiable} UNREAD — filed before this checkout's history begins"
    return line + "."


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Report agent-ready issues whose quoted premise has left the tree (read-only)."
    )
    ap.add_argument("--repo", default=DEFAULT_REPO, help="owner/name (default: %(default)s)")
    ap.add_argument("--json", action="store_true", help="Emit the result as JSON instead of prose")
    ap.add_argument(
        "--root",
        default=str(REPO_ROOT),
        help=(
            "Working tree to check anchors against (default: this checkout). "
            "This does NOT follow --repo: pass both when sweeping another "
            "repository, or anchors are checked against the wrong tree."
        ),
    )
    args = ap.parse_args(argv)

    # --repo and --root describe ONE project but are set independently, so they
    # can disagree, and the failure is silent: every cited path resolves against
    # the wrong tree, so anchors read as absent or match by coincidence. The
    # example now shows both flags, but that only helps someone who reads it.
    # A sweep whose contract is "never report from an inability to look" should
    # not quietly look at the wrong thing. stderr, so stdout stays silent-when-clean.
    if args.repo != DEFAULT_REPO and pathlib.Path(args.root).resolve() == REPO_ROOT.resolve():
        print(
            f"warning: --repo is {args.repo} but --root is this checkout ({REPO_ROOT}). "
            "Anchors will be checked against the wrong project's files. Pass --root "
            "pointing at that repo's working tree.",
            file=sys.stderr,
        )

    token = _token()
    issues = list_agent_ready_issues(args.repo, token)
    result = audit(issues, Tree(args.root))

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    elif has_findings(result):
        print(render(result, args.repo))
    else:
        # Silent when clean: nothing on stdout, so this can be wired to a
        # schedule without becoming an alert nobody reads (#992).
        print(summary_line(result) + " Nothing to report.", file=sys.stderr)

    return 1 if has_findings(result) else 0


if __name__ == "__main__":
    sys.exit(main())
