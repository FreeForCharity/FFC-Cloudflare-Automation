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

MAY, deliberately. This reports and a human decides. It never closes, labels,
comments on, or edits anything -- an auto-closer that is wrong re-buries live
work, and #1077 was closed by a Conductor holding the evidence.

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
  GH_TOKEN=... python3 scripts/audit-agent-ready-anchors.py --repo FreeForCharity/FFC-IN-ffcadmin.org

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

    def exists(self, path):
        return (self.root / path).is_file()

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
        object (a path that is simply absent) stays a quiet negative."""
        try:
            return (self.root / path).read_text(encoding="utf-8", errors="replace")
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
    premise, gone, no_anchor = [], [], []
    for issue in issues:
        p, g, usable = audit_issue(issue, tree)
        premise.extend(p)
        gone.extend(g)
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
    }


def has_findings(result):
    """The exit-code decision, in one place so a test can assert it in-process.

    A subprocess run against a clean fixture only ever exercises the zero path,
    and would pass against a `main` that returned 0 unconditionally (#912/#927).

    `no_anchor_extracted` is deliberately NOT a finding: it is the technique's
    stated limit, not something wrong with the backlog."""
    return bool(result["premise_may_be_gone"] or result["path_gone"])


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
    lines.append(summary_line(result))
    lines.append(
        "MAY, deliberately: this reports, a human closes. Confirm against the commits above "
        "before touching an issue."
    )
    return "\n".join(lines)


def summary_line(result):
    """The denominators. Printed on every run, findings or not -- the count of
    issues this sweep could not see into is the honest limit of the technique
    and must never be hidden."""
    return (
        f"{result['scanned']} agent-ready issues scanned; "
        f"{len(result['premise_may_be_gone'])} premise-may-be-gone, "
        f"{len(result['path_gone'])} path-gone, "
        f"{len(result['no_anchor_extracted'])} with no usable anchor "
        f"(invisible to this sweep)."
    )


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Report agent-ready issues whose quoted premise has left the tree (read-only)."
    )
    ap.add_argument("--repo", default=DEFAULT_REPO, help="owner/name (default: %(default)s)")
    ap.add_argument("--json", action="store_true", help="Emit the result as JSON instead of prose")
    ap.add_argument(
        "--root",
        default=str(REPO_ROOT),
        help="Working tree to check anchors against (default: this checkout)",
    )
    args = ap.parse_args(argv)

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
