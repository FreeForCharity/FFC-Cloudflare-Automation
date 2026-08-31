"""Unit tests for scripts/audit-agent-ready-anchors.py (#1193).

#1193 measured two detectors against the live backlog and only one survived:

  * "a file this issue cites changed since it was filed" flagged **20 of 52**
    open `agent-ready` issues, **1 real** -- Dependabot bumps and one
    comment-fix commit to `run_all.py` light up a third of the backlog;
  * "the anchor the issue quotes is gone from the file it quotes it from"
    agreed with the manual verdict on all three issues it was hand-checked
    against.

So the tests here are mostly about the ways this decays back into the first
detector, or into noise. The discriminating one is
`test_ac4_a_changed_file_whose_anchor_survives_reports_nothing`: #1060's cited
guard file *did* change, and the anchor is still in it, and a detector that
flags on file-change alone passes AC1-AC3 and fails only that. It is written
first for that reason.

No network and no fixtures on disk. The tree is injected (`FakeTree`), so these
tests do not decay when the real `105-manage-record.yml` changes again, and the
transport is injected at `_request`, so the real Link-header pagination and the
real shape-check are the code under test rather than a mock of them.

Locked down here:
  * the #1077 shape reports, and the same body against PRE-fix content does not
    -- the guard proved to fail without its fix (L47);
  * an anchor still present anywhere it is cited stays quiet, whatever else
    moved in the file;
  * an issue's own `bash` verification block is never an anchor, and neither is
    a bare identifier, a sentence, a path citation, or a span under 12 chars --
    each of those is a false positive that would land on nearly every issue;
  * a cited path that git has no history for is a **proposed** file and is
    silent, not `PATH GONE`: issues asking for a new script name one, and this
    sweep's own issue (#1193) is one of them;
  * every failed enumeration exits NON-ZERO -- no token, a non-list payload. A
    sweep with no verdict must never report a clean one;
  * the exit-code decision is asserted in-process via `has_findings`, because a
    clean-fixture subprocess run only ever exercises the zero path and would
    pass against a `main` that returned 0 unconditionally (#912/#927);
  * `no_anchor_extracted` is counted and printed but is NOT a finding -- it is
    the technique's stated limit, and burying it would make the rest unreadable;
  * the script issues no write, which no fixture can demonstrate.

Run: python3 tests/workflow-logic/test_agent_ready_anchors.py
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import pathlib
import subprocess
import sys
import tempfile

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "audit-agent-ready-anchors.py"

HUB = "FreeForCharity/FFC-Cloudflare-Automation"
WF105 = ".github/workflows/105-manage-record.yml"
WF111 = ".github/workflows/111-dns-create-redirect-rule.yml"
GUARD = "scripts/check-pwsh-workflow-invocations.py"


def load_module():
    spec = importlib.util.spec_from_file_location("agent_ready_anchors", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = load_module()

SOURCE = SCRIPT.read_text(encoding="utf-8")


class FakeTree:
    """The working tree, as data, at two points in time.

    `files` is the tree NOW; `at` is what each path held when the issue was
    filed. Two timestamps is the whole point -- "the premise is gone" is a
    statement about a change, and a fake with only one of them can express
    "absent" but not "was there and now is not", which is the only condition
    that should ever be reported. `at` defaults to `files` (nothing changed),
    so a test only spells it out when it means something moved."""

    def __init__(self, files, at=None, commits=None):
        self.files = dict(files)
        self.at = dict(files) if at is None else dict(at)
        self._commits = dict(commits or {})

    def exists(self, path):
        return path in self.files

    def read(self, path):
        return self.files.get(path, "")

    def content_at(self, path, when):
        return self.at.get(path)

    def commits_since(self, path, since):
        return list(self._commits.get(path, []))


def issue(number, body, title="t", created="2026-08-01T00:00:00Z"):
    return {
        "number": number,
        "title": title,
        "body": body,
        "created_at": created,
        "html_url": f"https://github.com/{HUB}/issues/{number}",
    }


# --- the three real shapes #1193 hand-checked -----------------------------

# #1077 quotes the call it says is wrong, and names the workflow by basename.
BODY_1077 = (
    "The preflight in `105-manage-record.yml` runs\n"
    "`.\\Update-CloudflareDns.ps1 -Zone $Domain -List -ErrorAction SilentlyContinue`\n"
    "and swallows the miss.\n\n"
    "## Verification\n\n"
    "```bash\n"
    "python3 tests/workflow-logic/run_all.py\n"
    "gh workflow run 105-manage-record.yml\n"
    "```\n"
)
ANCHOR_1077 = r".\Update-CloudflareDns.ps1 -Zone $Domain -List -ErrorAction SilentlyContinue"
PRE_FIX_105 = "steps:\n  - run: |\n      " + ANCHOR_1077 + "\n"
POST_FIX_105 = "steps:\n  - run: |\n      .\\Update-CloudflareDns.ps1 -ExportAll -OutputFile out.json\n"

# #1037's anchor is still live in 111.
BODY_1037 = (
    "`111-dns-create-redirect-rule.yml` still shells out with\n"
    "`curl.exe -sS -X POST --data-binary @payload.json` instead of the module.\n"
)
LIVE_111 = "run: |\n  curl.exe -sS -X POST --data-binary @payload.json\n"

# #1060: the cited guard file CHANGED, and the anchor is still in it.
BODY_1060 = (
    "`scripts/check-pwsh-workflow-invocations.py` skips a variable command:\n"
    "`if not command or command.startswith(\"$\"): continue`\n"
)
GUARD_SRC = 'for command in commands:\n    if not command or command.startswith("$"): continue\n'


# --------------------------------------------------------------------------
# AC4 first: the case that separates this from a file-changed sweep
# --------------------------------------------------------------------------


def test_ac4_a_changed_file_whose_anchor_survives_reports_nothing():
    # eeee163 edited the guard only to make it STATE its blind spot; its own
    # message says "the guard itself is unchanged". A file-changed detector
    # flags this. An anchor detector must not.
    tree = FakeTree(
        {GUARD: GUARD_SRC},
        commits={GUARD: ["eeee163 2026-08-07 docs: state the guard's blind spot"]},
    )
    result = M.audit([issue(1060, BODY_1060)], tree)
    assert result["premise_may_be_gone"] == [], result["premise_may_be_gone"]
    assert not M.has_findings(result), "a changed file with a live anchor is not a finding"


def test_ac1_the_1077_shape_reports_premise_may_be_gone_naming_the_workflow():
    tree = FakeTree(
        {WF105: POST_FIX_105},
        at={WF105: PRE_FIX_105},
        commits={WF105: ["761bfdf 2026-08-06 fix(105): export all records"]},
    )
    result = M.audit([issue(1077, BODY_1077)], tree)
    rows = result["premise_may_be_gone"]
    assert len(rows) == 1, rows
    assert rows[0]["issue"] == 1077
    assert rows[0]["kind"] == "PREMISE MAY BE GONE"
    assert WF105 in rows[0]["paths_searched"], rows[0]
    assert "Update-CloudflareDns.ps1" in rows[0]["anchor"], rows[0]
    assert M.has_findings(result)


def test_ac2_the_same_body_against_pre_fix_content_reports_nothing():
    # L47: the guard must be shown to fail without its fix. Only the FILE
    # differs between this and AC1 -- same issue body, same paths, same code.
    tree = FakeTree({WF105: PRE_FIX_105})
    result = M.audit([issue(1077, BODY_1077)], tree)
    assert result["premise_may_be_gone"] == [], result["premise_may_be_gone"]
    assert not M.has_findings(result)


def test_ac3_an_anchor_still_present_reports_nothing():
    tree = FakeTree({WF111: LIVE_111})
    result = M.audit([issue(1037, BODY_1037)], tree)
    assert not M.has_findings(result), result["premise_may_be_gone"]


# --------------------------------------------------------------------------
# refusal and silence
# --------------------------------------------------------------------------


def test_ac5_an_unauthenticated_run_refuses_and_emits_no_partial_report():
    saved = {k: os.environ.pop(k) for k in ("GH_TOKEN", "GITHUB_TOKEN") if k in os.environ}
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                M.main(["--repo", HUB])
            except SystemExit as exc:
                code = exc.code
            else:  # pragma: no cover - reaching here IS the failure
                code = 0
    finally:
        os.environ.update(saved)
    assert code != 0, "an unauthenticated sweep must not exit 0"
    assert isinstance(code, str) and "GH_TOKEN" in code, code
    assert out.getvalue() == "", f"no partial report may be printed: {out.getvalue()!r}"


def test_ac6_a_clean_backlog_prints_nothing_on_stdout_and_exits_zero():
    saved = os.environ.get("GH_TOKEN")
    os.environ["GH_TOKEN"] = "x"
    real_list, real_tree = M.list_agent_ready_issues, M.Tree
    M.list_agent_ready_issues = lambda repo, token, _request_fn=None: [issue(1, "no anchors here")]
    M.Tree = lambda root: FakeTree({})
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = M.main(["--repo", HUB])
    finally:
        M.list_agent_ready_issues, M.Tree = real_list, real_tree
        if saved is None:
            os.environ.pop("GH_TOKEN", None)
        else:
            os.environ["GH_TOKEN"] = saved
    assert code == 0, code
    assert out.getvalue() == "", f"clean must be silent on stdout: {out.getvalue()!r}"
    assert "scanned" in err.getvalue(), "the denominators must still be accountable on stderr"


# --------------------------------------------------------------------------
# PATH GONE: deleted vs never-existed
# --------------------------------------------------------------------------


def test_a_cited_path_git_never_saw_is_a_proposed_file_and_stays_silent():
    # #1193 itself names `scripts/audit-agent-ready-anchors.py` in its Scope
    # section before it exists. Reporting that as PATH GONE would fire on
    # nearly every feature request.
    body = "Add **`scripts/audit-agent-ready-anchors.py`** *(new)* — the sweep."
    tree = FakeTree({}, at={})
    result = M.audit([issue(1193, body)], tree)
    assert result["path_gone"] == [], result["path_gone"]
    assert not M.has_findings(result)


def test_a_cited_path_with_git_history_that_is_gone_is_reported_as_path_gone():
    body = "The check lives in `scripts/check-deleted-thing.py` and is wrong."
    tree = FakeTree(
        {},
        at={"scripts/check-deleted-thing.py": "def check():\n    return True\n"},
        commits={"scripts/check-deleted-thing.py": ["abc1234 2026-08-09 chore: drop the check"]},
    )
    result = M.audit([issue(9, body)], tree)
    assert len(result["path_gone"]) == 1, result["path_gone"]
    row = result["path_gone"][0]
    assert row["kind"] == "PATH GONE"
    assert row["path"] == "scripts/check-deleted-thing.py"
    assert M.has_findings(result)


# --------------------------------------------------------------------------
# anchor extraction: every filter is a false positive that would otherwise land
# --------------------------------------------------------------------------


def test_a_shell_fenced_verification_block_is_never_an_anchor():
    # Every well-written issue in this repo ends with one. Without this filter
    # the sweep reports PREMISE MAY BE GONE on all of them.
    #
    # The CONTINUATION line is the load-bearing case and the reason this test
    # is not redundant with the command-prefix filter: `--repo Free... --json`
    # opens with no command name, is well over 12 characters, and is full of
    # code punctuation, so the fence's language tag is the ONLY thing that can
    # reject it. A first draft of this test used two lines that both began with
    # a command, and it passed with the fence filter deleted.
    body = (
        "See `scripts/foo.py`.\n\n"
        "```bash\n"
        "GH_TOKEN=... python3 scripts/foo.py \\\n"
        "  --repo FreeForCharity/FFC-Cloudflare-Automation --json\n"
        "```\n"
    )
    assert M.extract_anchors(body) == [], M.extract_anchors(body)


def test_a_command_invocation_in_an_untagged_fence_is_never_an_anchor():
    # An untagged fence carries no language to filter on, so the prefix list is
    # the only defence left.
    body = "See `scripts/foo.py`.\n\n```\nGH_TOKEN=... python3 scripts/foo.py\ngh pr view 1\n```\n"
    assert M.extract_anchors(body) == [], M.extract_anchors(body)


def test_an_untagged_fence_does_not_leak_a_wrapped_commands_continuation_line():
    # Copilot's finding on #1224, reproduced before it was fixed: an untagged
    # fence has no language to filter on, so only the per-span command-prefix
    # check applied -- and a continuation line opens with no command name.
    # `--repo FreeForCharity/... --json` is 54 characters of code punctuation
    # and passed every span-level filter.
    #
    # The harm is to the honest denominator rather than to the findings (the
    # was-it-ever-there gate rejects a CLI flag that was never in the file), and
    # that is precisely why it is worth fixing: it inflates `usable`, so the
    # sweep counts an issue as one it could see into when it could not, and the
    # one number #1193 insists must not be hidden quietly gets smaller.
    body = (
        "See `scripts/foo.py`.\n\n"
        "```\n"
        "GH_TOKEN=... python3 scripts/foo.py \\\n"
        "  --repo FreeForCharity/FFC-Cloudflare-Automation --json\n"
        "```\n"
    )
    assert M.extract_anchors(body) == [], M.extract_anchors(body)
    # ...and the issue must land in the no-anchor bucket, not read as usable.
    result = M.audit([issue(11, body)], FakeTree({"scripts/foo.py": "pass\n"}))
    assert len(result["no_anchor_extracted"]) == 1, result


def test_an_inline_command_span_in_prose_is_not_an_anchor():
    # A command quoted INLINE in a sentence belongs to no block, so the
    # per-span prefix filter in `is_anchor` is the only thing that can reject
    # it. This case exists because the block-level fix above silently took over
    # every case that used to cover that filter: with the per-span check
    # disabled, the whole suite stayed green until this test was added. A fix
    # that subsumes another guard's only coverage leaves it untested rather
    # than redundant, and nothing says so.
    body = "Reproduce with `gh run view 33268448034 --log-failed` against `scripts/foo.py`."
    assert M.extract_anchors(body) == [], M.extract_anchors(body)
    assert not M.is_anchor("gh run view 33268448034 --log-failed")


def test_a_flag_leading_fragment_is_still_a_valid_anchor():
    # The polarity control for the fix above, and the reason it is block-level.
    # #1077's premise is quoted inline as a bare flag fragment; a rule that
    # rejected spans opening with `-` would pass the test above by discarding
    # the case this sweep exists to catch.
    assert M.is_anchor(r"-Zone $Domain -List -ErrorAction SilentlyContinue")
    body = "`105-manage-record.yml` calls `-Zone $Domain -List -ErrorAction SilentlyContinue`."
    tree = FakeTree({WF105: POST_FIX_105}, at={WF105: PRE_FIX_105})
    assert len(M.audit([issue(12, body)], tree)["premise_may_be_gone"]) == 1


def test_short_spans_bare_identifiers_prose_and_paths_are_not_anchors():
    for span in (
        "curl.exe",  # 8 chars: names a thing, cannot be checked for presence
        "SELF_ALERT_MARKER",  # bare identifier: a rename, not a deletion
        "the merge queue is strict",  # a sentence in backticks
        "scripts/audit-agentic-os-board.py",  # a path citation is a path
        "105-manage-record.yml",  # ditto, by basename
        "https://github.com/FreeForCharity/x",  # a link
    ):
        assert not M.is_anchor(span), span
    assert M.is_anchor(r"-Zone $Domain -List -ErrorAction SilentlyContinue")


def test_an_anchor_present_in_any_one_cited_path_stays_quiet():
    # Two files cited, anchor lives in the second. "Gone from the first" is not
    # "gone" -- the premise is still live somewhere.
    body = "`scripts/a.py` and `scripts/b.py` both call `helper(timeout=30, retries=2)`."
    tree = FakeTree({"scripts/a.py": "pass\n", "scripts/b.py": "helper(timeout=30, retries=2)\n"})
    result = M.audit([issue(5, body)], tree)
    assert not M.has_findings(result), result["premise_may_be_gone"]


def test_reindenting_a_run_body_is_not_a_deletion():
    body = "`scripts/a.py` runs `helper(timeout=30, retries=2)` on every tick."
    tree = FakeTree(
        {"scripts/a.py": "if x:\n        helper(timeout=30,\n               retries=2)\n"},
        at={"scripts/a.py": "helper(timeout=30, retries=2)\n"},
    )
    result = M.audit([issue(6, body)], tree)
    assert not M.has_findings(result), "whitespace must be normalised on both sides"


# --------------------------------------------------------------------------
# enumeration: never a falsely-clean report
# --------------------------------------------------------------------------


def test_pull_requests_are_not_part_of_the_backlog():
    rows = [
        {"number": 1, "title": "an issue", "body": "b"},
        {"number": 2, "title": "a pr", "body": "b", "pull_request": {"url": "..."}},
    ]
    got = M.list_agent_ready_issues(HUB, "tok", _request_fn=lambda url, token, params=None: (rows, None))
    assert [r["number"] for r in got] == [1], got


def test_a_non_list_payload_aborts_rather_than_reading_as_zero_issues():
    def fake(url, token, params=None):
        return {"message": "Bad credentials"}, None

    try:
        M.list_agent_ready_issues(HUB, "tok", _request_fn=fake)
    except SystemExit as exc:
        assert "expected a JSON array" in str(exc), exc
    else:
        raise AssertionError("a 200-with-an-error-body must not read as an empty backlog")


def test_the_listing_follows_the_link_header_to_the_last_page():
    pages = {
        None: ([{"number": 1, "body": "a"}], '<https://api.github.com/x?page=2>; rel="next"'),
        "https://api.github.com/x?page=2": ([{"number": 2, "body": "b"}], None),
    }

    def fake(url, token, params=None):
        return pages.get(url if url in pages else None)

    got = M.list_agent_ready_issues(HUB, "tok", _request_fn=fake)
    assert [r["number"] for r in got] == [1, 2], got


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------


def test_an_anchor_that_was_never_in_the_cited_file_is_not_reported():
    # THE lesson of the first live run: without a was-it-ever-there check this
    # sweep reported 408 rows across 45 of 53 issues -- worse than the
    # file-changed detector #1193 rejected at 20 of 52. Bodies are full of
    # strings that were never in the file they cite: version ranges, log lines,
    # `path.py:243-254` citations, search queries, proposed new code. Every one
    # of them is "absent from the file" and none of them is a premise.
    body = (
        "`105-manage-record.yml` breaks when the advisory names "
        "`fast-uri 3.0.0 - 3.1.4` and the run logs `status=pending-approval`.\n"
    )
    tree = FakeTree({WF105: POST_FIX_105})  # unchanged since filing
    result = M.audit([issue(7, body)], tree)
    assert not M.has_findings(result), result["premise_may_be_gone"]


def test_content_at_accepts_a_blob_and_refuses_a_directory_in_the_REAL_tree():
    # FakeTree cannot express this one: the defect is a git behaviour, not a
    # logic error. `git show <rev>:docs` prints a tree LISTING rather than
    # failing, so a cited directory came back as plausible content and #859 was
    # reported as PATH GONE on the first live run.
    #
    # The two halves are asserted TOGETHER on purpose. Alone, the directory
    # assertion passes when git is missing, the repo is unreadable, or the
    # revision will not resolve -- every one of which also returns None. Pairing
    # it with a blob that MUST return content means a broken environment fails
    # this test loudly instead of scoring it green (#1182).
    tree = M.Tree(REPO_ROOT)
    now = "2099-01-01T00:00:00Z"  # after any horizon: resolves to HEAD
    blob = tree.content_at("tests/workflow-logic/run_all.py", now)
    assert blob and "def main" in blob, "a real blob must come back as content"
    assert tree.content_at("tests/workflow-logic", now) is None, "a tree is not content"
    assert tree.content_at("scripts", now) is None, "a tree is not content"


def test_a_missing_git_aborts_instead_of_reporting_a_clean_backlog():
    # Copilot's second finding. `_git` used to swallow OSError and return "",
    # so `content_at` answered None for every anchor, nothing was reported, and
    # the run exited 0. Measured before the fix: the same issue that yields a
    # finding against the real tree yielded has_findings=False on a host with no
    # git. That is the unauthenticated-sweep failure one input over — a clean
    # bill of health produced by having no access — and the module docstring
    # promises never to do it.
    real = subprocess.run

    def no_git(*a, **k):
        raise FileNotFoundError("git")

    subprocess.run = no_git
    try:
        M.Tree(REPO_ROOT).content_at("scripts/audit-agent-ready-anchors.py", "2026-08-01T00:00:00Z")
    except SystemExit as exc:
        assert "git is not on PATH" in str(exc), exc
    else:
        raise AssertionError("a missing git must abort, not read as 'no history'")
    finally:
        subprocess.run = real


def test_audit_issue_documents_the_third_return_value_it_actually_returns():
    # Copilot round 5: the docstring said `had_anchor` while the value is
    # `usable` (a cited path that EXISTS *and* an anchor). The two differ
    # exactly where the denominator is decided, so the wrong name invites a
    # future change that overstates what the sweep can see.
    doc = M.audit_issue.__doc__ or ""
    assert "usable" in doc, doc
    assert "had_anchor" not in doc, "the docstring still names a value this does not return"
    # And the contract itself: an anchor with no existing cited path is NOT usable.
    body = "`scripts/gone.py` calls `helper(timeout=30, retries=2)` on every tick."
    _, _, usable = M.audit_issue(issue(3, body), FakeTree({}, at={}))
    assert usable is False, "an anchor with no existing path must not count as usable"


def test_a_root_that_is_not_a_git_checkout_aborts():
    with tempfile.TemporaryDirectory() as td:
        try:
            M.Tree(td).content_at("scripts/whatever.py", "2026-08-01T00:00:00Z")
        except SystemExit as exc:
            assert "not a readable git checkout" in str(exc), exc
        else:
            raise AssertionError("a non-repo root must abort rather than report nothing")


def test_an_absent_object_is_a_quiet_negative_and_never_an_abort():
    # The polarity control for the two aborts above, and the reason the check is
    # one-time rather than per-call: `git cat-file -t <rev>:<path>` exits
    # non-zero precisely when the object is absent, which is the answer this
    # sweep is built on. A fix that keyed on the exit code would pass both tests
    # above by aborting on the ordinary case.
    tree = M.Tree(REPO_ROOT)
    now = "2099-01-01T00:00:00Z"
    assert tree.content_at("scripts/this-path-has-never-existed.py", now) is None
    assert tree.commits_since("scripts/this-path-has-never-existed.py", now) == []
    # ...and a real blob still reads, so the tree is genuinely being consulted.
    assert tree.content_at("tests/workflow-logic/run_all.py", now)


def test_an_unreadable_file_aborts_instead_of_reporting_its_premise_as_gone():
    # Copilot round 4. `Tree.read` used to swallow OSError and return "", and
    # `read` is only reached for a path `exists()` already confirmed -- so an
    # unreadable file is an environment fault, and every anchor in it read as
    # absent. Same defect as the two aborts above, opposite direction: those
    # manufactured a clean backlog, this manufactures a finding.
    with tempfile.TemporaryDirectory() as td:
        target = pathlib.Path(td) / "unreadable.py"
        target.write_text("the anchor string is right here\n", encoding="utf-8")
        tree = M.Tree(td)
        assert tree.exists("unreadable.py"), "precondition: exists() must say yes"

        real = pathlib.Path.read_text

        def denied(self, *a, **k):
            raise PermissionError(13, "Permission denied")

        pathlib.Path.read_text = denied
        try:
            tree.read("unreadable.py")
        except SystemExit as exc:
            assert "cannot read" in str(exc), exc
        else:
            raise AssertionError(
                "an unreadable file that exists must abort, not read as empty content"
            )
        finally:
            pathlib.Path.read_text = real


def test_a_readable_file_is_still_read_verbatim():
    # The polarity control. A "fix" that aborted on every read, or that stopped
    # reading the working tree at all, satisfies the test above and destroys the
    # sweep -- every anchor would then be absent for a different reason.
    #
    # It ASSERTS the abort rather than letting it propagate: `SystemExit` is not
    # an `AssertionError`, so an over-broad fix would escape the module runner
    # and end the module with no named FAIL at all -- non-zero, but reading as a
    # crash rather than a detection (L82, and the reason L203 says to match on
    # the finding rather than on the exit code). Measured: before this catch, the
    # over-broad mutant flipped an empty set.
    with tempfile.TemporaryDirectory() as td:
        (pathlib.Path(td) / "readable.py").write_text("anchor here\n", encoding="utf-8")
        try:
            got = M.Tree(td).read("readable.py")
        except SystemExit as exc:
            raise AssertionError(f"a readable file must not abort, but did: {exc}")
        assert got == "anchor here\n", got


def test_an_empty_file_that_existed_is_not_confused_with_one_that_never_did():
    # Copilot round 6. `content_at` returned `content or None`, folding a
    # zero-length blob into the same answer as "no such object" — so deleting a
    # legitimately EMPTY file was never reported as PATH GONE.
    #
    # A real git fixture, because the defect is in the sentinel that git's own
    # output feeds; FakeTree cannot express "a blob exists and its content is
    # the empty string". The `cat-file -t` blob check already answers the
    # existence question, which is what lets "" be returned honestly.
    #
    # Both directions in one fixture: the empty file must read as "", and a path
    # git never saw must still read as None. A fix that returned "" for
    # everything would satisfy the first assertion and destroy PATH GONE's only
    # guard against reporting proposed-new files.
    import os as _os
    import subprocess as _sp
    import tempfile as _tf

    with _tf.TemporaryDirectory() as td:
        root = pathlib.Path(td)

        def git(*args, when=None):
            env = dict(_os.environ)
            if when:
                env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = when
            _sp.run(["git", "-C", str(root), *args], check=True, env=env,
                    stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)

        git("init", "-q")
        git("config", "user.email", "t@t")
        git("config", "user.name", "t")
        (root / "scripts").mkdir()
        (root / "scripts" / "empty.py").write_text("", encoding="utf-8")
        git("add", "-A")
        git("commit", "-q", "-m", "add an empty file", when="2026-08-01T00:00:00")
        (root / "scripts" / "empty.py").unlink()
        git("add", "-A")
        git("commit", "-q", "-m", "delete it", when="2026-08-20T00:00:00")

        tree = M.Tree(root)
        filed = "2026-08-10T00:00:00Z"  # after the add, before the delete
        assert tree.content_at("scripts/empty.py", filed) == "", "an empty blob existed; say so"
        assert tree.content_at("scripts/never.py", filed) is None, "a path git never saw stays None"

        body = "The check lives in `scripts/empty.py` and is wrong."
        result = M.audit([issue(1, body, created=filed)], tree)
        assert [r["path"] for r in result["path_gone"]] == ["scripts/empty.py"], result["path_gone"]


def test_a_cited_directory_is_not_reported_as_path_gone():
    # `docs/data` has git history (its children do) but no content of its own.
    # The first live run reported it as PATH GONE against issue #859.
    body = "The baseline lives under `docs/data` and is never refreshed."
    tree = FakeTree({}, at={})
    result = M.audit([issue(859, body)], tree)
    assert result["path_gone"] == [], result["path_gone"]


def test_the_report_names_only_the_paths_the_anchor_was_actually_in():
    # An issue citing four files must not be reported against all four; the
    # finding points at the one the premise actually left.
    body = "`scripts/a.py` and `scripts/b.py` — see `helper(timeout=30, retries=2)`."
    tree = FakeTree(
        {"scripts/a.py": "pass\n", "scripts/b.py": "pass\n"},
        at={"scripts/a.py": "helper(timeout=30, retries=2)\n", "scripts/b.py": "pass\n"},
    )
    rows = M.audit([issue(8, body)], tree)["premise_may_be_gone"]
    assert len(rows) == 1, rows
    assert rows[0]["paths_searched"] == ["scripts/a.py"], rows[0]["paths_searched"]


def test_no_anchor_extracted_is_counted_but_is_not_a_finding():
    # The technique's limit is not a defect in the backlog. If this counted as
    # a finding the sweep would be red permanently and mean nothing.
    result = M.audit([issue(1, "prose only, nothing quoted")], FakeTree({}))
    assert len(result["no_anchor_extracted"]) == 1, result
    assert not M.has_findings(result)
    assert "no usable anchor" in M.summary_line(result)


def test_a_reported_issue_is_never_also_counted_as_invisible():
    # Copilot's third finding. A PATH GONE comes from the path-only branch,
    # where every cited path is missing -- so `present` is empty, `usable` is
    # False, and the issue was reported AND counted under "no usable anchor
    # (invisible to this sweep)" at the same time. The two buckets contradicted
    # each other, and the error inflated the honest-limit denominator, making
    # the technique look blinder than it is. #859 hit this on the first live run.
    body = "The check lives in `scripts/check-deleted-thing.py` and is wrong."
    tree = FakeTree({}, at={"scripts/check-deleted-thing.py": "def check():\n    return True\n"})
    result = M.audit([issue(859, body)], tree)
    assert [r["issue"] for r in result["path_gone"]] == [859], result["path_gone"]
    assert result["no_anchor_extracted"] == [], result["no_anchor_extracted"]


def test_an_issue_the_sweep_said_nothing_about_is_still_counted_as_invisible():
    # The polarity control: the fix above must not empty the bucket entirely.
    # A bucket that is always zero would satisfy the assertion above while
    # destroying the number #1193 exists to keep visible.
    result = M.audit([issue(1, "prose only, nothing quoted")], FakeTree({}))
    assert [r["issue"] for r in result["no_anchor_extracted"]] == [1], result
    assert not M.has_findings(result)


def test_the_summary_line_always_states_both_denominators():
    result = M.audit([issue(1, "prose only"), issue(2, "also prose")], FakeTree({}))
    line = M.summary_line(result)
    assert "2 agent-ready issues scanned" in line, line
    assert "2 with no usable anchor" in line, line


def test_the_report_names_the_anchor_the_paths_and_the_candidate_commits():
    tree = FakeTree(
        {WF105: POST_FIX_105},
        at={WF105: PRE_FIX_105},
        commits={WF105: ["761bfdf 2026-08-06 fix(105): export all records"]},
    )
    text = M.render(M.audit([issue(1077, BODY_1077)], tree), HUB)
    assert "PREMISE MAY BE GONE" in text
    assert WF105 in text
    assert "761bfdf" in text, "a human needs the candidate fix in hand"
    assert "MAY, deliberately" in text, "the report must not read as a verdict"


def test_the_script_issues_no_write_of_any_kind():
    # No fixture can demonstrate this; the source is the only place to assert it.
    for forbidden in ('method="POST"', "method='POST'", "mutation", "issue_write", "--patch"):
        assert forbidden not in SOURCE, forbidden
    assert "urlopen" in SOURCE, "sanity: the transport is still urllib"


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {str(e)[:2000]}")
        except Exception as e:  # never truncate the module: report and continue
            failures += 1
            print(f"  FAIL {t.__name__}: unexpected {type(e).__name__}: {str(e)[:2000]}")
    sys.exit(1 if failures else 0)
