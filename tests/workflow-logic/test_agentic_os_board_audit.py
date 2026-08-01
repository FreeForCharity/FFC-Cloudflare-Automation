"""Unit tests for scripts/audit-agentic-os-board.py (#966).

The board audit compares two enumerations, and #966 is the record of each half
being wrong once, in two consecutive Conductor runs:

  * run 61 read the board with `items(first:100)` and no cursor loop, got 100 of
    208 items, and nearly published "0 statusless" off a third of the board;
  * run 62 built the *expected* half from `gh search issues`, got 52 where REST
    returned 53, and reported "0 missing" — the item the search index had not
    caught up to was PR #965, created minutes earlier.

Both failures are silent and both point the reassuring way. So the tests here
are mostly about the two directions that produce a **falsely clean** report:
a truncated enumeration, and a comparison that matches things it should not.

No network. The two transport primitives are injected — `_request` (REST, which
returns `(payload, link_header)`, so patching it exercises the real Link-header
pagination) and the `_graphql` parameter of `fetch_board_items` (which exercises
the real `$endCursor` loop).

Locked down here:
  * an item present in REST but absent from the board is REPORTED, not swallowed
    — the case that motivated the issue;
  * items are keyed `(repo, number)`, never the bare integer: `#744` names a
    different item in each repo, and an integer-keyed comparison would mark a
    real gap as present because another repo has that number on the board;
  * both sides paginate — an item at board position 101+ is not dropped, and a
    REST second page is followed;
  * pull requests are part of the expected set (run 62's missing item was one);
  * every failed enumeration exits NON-ZERO: no token, GraphQL `errors`, a
    missing project, an unexpected payload shape. An audit with no verdict must
    never report a clean one;
  * the exit-code decision is asserted in-process via `has_findings`, because a
    subprocess run against a clean fixture only ever exercises the zero path and
    would pass against a `main` that returned 0 unconditionally (the lesson
    recorded on #912/#927);
  * the report prints BOTH denominators, so a future silent under-report shows
    up as a suspicious count rather than an empty result;
  * the script uses neither the search API nor any write — the two properties
    the issue asks for that no fixture can demonstrate.

Run: python3 tests/workflow-logic/test_agentic_os_board_audit.py
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import re
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "audit-agentic-os-board.py"

ORG = "FreeForCharity"
HUB = "FreeForCharity/FFC-Cloudflare-Automation"
ADMIN = "FreeForCharity/FFC-IN-ffcadmin.org"


def load_module():
    spec = importlib.util.spec_from_file_location("agentic_os_board_audit", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = load_module()


# ---------------------------------------------------------------- fixtures


def issue_row(number, title="t", is_pr=False):
    row = {"number": number, "title": title, "html_url": f"https://x/{number}"}
    if is_pr:
        row["pull_request"] = {"url": "https://x/pulls"}
    return row


def board_node(repo, number, status="Todo", state="OPEN", typename="Issue", node_id=None):
    content = {"__typename": typename, "number": number, "state": state, "title": "t", "url": "u"}
    if repo:
        content["repository"] = {"nameWithOwner": repo}
    return {
        "id": node_id or f"{repo}-{number}",
        "fieldValueByName": {"name": status} if status else None,
        "content": content,
    }


def draft_node(title="a draft", status=None):
    return {
        "id": f"draft-{title}",
        "fieldValueByName": {"name": status} if status else None,
        "content": {"__typename": "DraftIssue", "title": title},
    }


def board_page(nodes, has_next=False, cursor=None, title="Agentic OS"):
    return {
        "organization": {
            "projectV2": {
                "title": title,
                "items": {
                    "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
                    "nodes": nodes,
                },
            }
        }
    }


def fake_graphql(pages):
    """A `_graphql` stand-in that serves `pages` in order and records cursors."""
    calls = []

    def _call(query, variables, token):
        calls.append(variables.get("after"))
        return pages[len(calls) - 1]

    _call.calls = calls
    return _call


def install_rest(mod, routes):
    """Patch `_request` with a dict of {url_substring: (payload, link)} routes.

    Matching on a substring rather than the full URL keeps fixtures readable
    while still exercising `_build_url`'s query assembly."""

    def _fake(path_or_url, token, params=None):
        url = mod._build_url(path_or_url, params)
        for needle, response in routes.items():
            if needle in url:
                return response
        raise AssertionError(f"unrouted REST call: {url}")

    mod._request = _fake


# ---------------------------------------------------------------- the core case


def test_an_item_in_rest_but_absent_from_the_board_is_reported():
    """Run 62's defect, as a fixture: the newest item is the one that goes missing."""
    expected = {
        (HUB, 964): {"repo": HUB, "number": 964, "title": "old", "url": "", "is_pr": False},
        (HUB, 965): {"repo": HUB, "number": 965, "title": "new PR", "url": "", "is_pr": True},
    }
    rows = M.normalize_board_items([board_node(HUB, 964)])
    result = M.audit(expected, rows)
    missing = [(r["repo"], r["number"]) for r in result["missing_from_board"]]
    assert missing == [(HUB, 965)], f"the unboarded item must be reported, got {missing}"
    assert M.has_findings(result), "a missing item must make the audit non-zero"


def test_items_are_keyed_by_repo_and_number_not_by_number_alone():
    """`#100` on the board in one repo must not satisfy `#100` expected in another."""
    expected = {
        (ADMIN, 100): {"repo": ADMIN, "number": 100, "title": "admin", "url": "", "is_pr": False}
    }
    rows = M.normalize_board_items([board_node(HUB, 100)])
    result = M.audit(expected, rows)
    missing = [(r["repo"], r["number"]) for r in result["missing_from_board"]]
    assert missing == [(ADMIN, 100)], (
        "a same-numbered item in a DIFFERENT repo must not count as present; "
        f"got {missing}"
    )


def test_a_boarded_item_is_not_reported_missing():
    expected = {
        (HUB, 1): {"repo": HUB, "number": 1, "title": "x", "url": "", "is_pr": False},
    }
    rows = M.normalize_board_items([board_node(HUB, 1)])
    result = M.audit(expected, rows)
    assert result["missing_from_board"] == [], "a boarded item must not be reported"


# ---------------------------------------------------------------- the other two sets


def test_as_dict_rejects_a_non_dict_rather_than_passing_it_through():
    """The mutation ritual found this claim unchecked: `value or {}` passes the
    mutation, because no fixture feeds a truthy non-dict — and it would then
    crash at the caller's `.get`. Pinned rather than shipped on trust."""
    assert M._as_dict({"a": 1}) == {"a": 1}
    assert M._as_dict(None) == {}, "a GraphQL null must become an empty mapping"
    assert M._as_dict("not a dict") == {}, "a truthy non-dict must not pass through"
    assert M._as_dict([1, 2]) == {}


def test_statusless_board_items_are_reported_including_drafts():
    rows = M.normalize_board_items(
        [
            board_node(HUB, 1, status="Todo"),
            board_node(HUB, 2, status=None),
            draft_node("no column"),
        ]
    )
    result = M.audit({}, rows)
    ids = sorted(r["id"] for r in result["statusless"])
    assert ids == ["FreeForCharity/FFC-Cloudflare-Automation-2", "draft-no column"], (
        "both a statusless issue card and a statusless draft must be reported; "
        f"got {ids}"
    )


def test_a_draft_item_never_satisfies_an_expected_repo_item():
    """A draft has no repo and no number — giving it a key would invent a match."""
    expected = {(HUB, 5): {"repo": HUB, "number": 5, "title": "x", "url": "", "is_pr": False}}
    rows = M.normalize_board_items([draft_node("looks like work", status="Todo")])
    result = M.audit(expected, rows)
    assert len(result["missing_from_board"]) == 1, "the draft must not mask a real gap"
    assert rows[0]["key"] is None, "a draft must carry no comparison key"


def test_closed_and_merged_items_without_done_are_reported():
    rows = M.normalize_board_items(
        [
            board_node(HUB, 1, status="In Progress", state="CLOSED"),
            board_node(HUB, 2, status="In Review", state="MERGED", typename="PullRequest"),
            board_node(HUB, 3, status="Done", state="CLOSED"),
            board_node(HUB, 4, status="Todo", state="OPEN"),
        ]
    )
    result = M.audit({}, rows)
    numbers = sorted(r["number"] for r in result["closed_not_done"])
    assert numbers == [1, 2], (
        "a CLOSED issue and a MERGED PR that are not Done must both be reported, "
        f"and nothing else; got {numbers}"
    )


def test_a_statusless_closed_item_is_reported_by_both_checks():
    """The two sets overlap on purpose — each answers a different question."""
    rows = M.normalize_board_items([board_node(HUB, 1, status=None, state="CLOSED")])
    result = M.audit({}, rows)
    assert len(result["statusless"]) == 1 and len(result["closed_not_done"]) == 1


def test_a_clean_board_produces_no_findings():
    expected = {(HUB, 1): {"repo": HUB, "number": 1, "title": "x", "url": "", "is_pr": False}}
    rows = M.normalize_board_items([board_node(HUB, 1, status="Todo", state="OPEN")])
    result = M.audit(expected, rows)
    assert not M.has_findings(result), f"a consistent board must be clean: {result}"


# ---------------------------------------------------------------- pagination, both sides


def test_board_read_follows_the_cursor_past_the_first_page():
    """Run 61's defect: an item at position 101+ must not be dropped."""
    page1 = [board_node(HUB, n) for n in range(1, 101)]
    page2 = [board_node(HUB, 101, status=None)]
    g = fake_graphql([board_page(page1, has_next=True, cursor="CUR1"), board_page(page2)])
    title, nodes = M.fetch_board_items(ORG, 9, "tok", _graphql=g)
    assert len(nodes) == 101, f"both pages must be read, got {len(nodes)}"
    assert g.calls == [None, "CUR1"], f"the second call must carry the cursor, got {g.calls}"
    assert title == "Agentic OS"
    result = M.audit({}, M.normalize_board_items(nodes))
    assert [r["number"] for r in result["statusless"]] == [101], (
        "the statusless item beyond the page boundary must survive the merge"
    )


def test_board_read_aborts_when_hasnextpage_has_no_cursor():
    g = fake_graphql([board_page([board_node(HUB, 1)], has_next=True, cursor=None)])
    try:
        M.fetch_board_items(ORG, 9, "tok", _graphql=g)
    except SystemExit as exc:
        assert "partial board" in str(exc), str(exc)
        return
    raise AssertionError("a truncated board read must abort, not return what it has")


def test_rest_listing_follows_link_next():
    mod = load_module()
    page2 = "https://api.github.com/repos/o/r/issues?page=2"
    install_rest(
        mod,
        {
            "page=2": ([issue_row(2)], None),
            "/repos/o/r/issues": ([issue_row(1)], f'<{page2}>; rel="next"'),
        },
    )
    expected = mod.collect_expected(["o/r"], "tok")
    assert sorted(k[1] for k in expected) == [1, 2], (
        f"a second REST page must be followed, got {sorted(expected)}"
    )


def test_expected_set_includes_pull_requests():
    """Run 62's missing item was a PR; filtering PRs out reproduces the bug."""
    mod = load_module()
    install_rest(mod, {"/repos/o/r/issues": ([issue_row(1), issue_row(2, is_pr=True)], None)})
    expected = mod.collect_expected(["o/r"], "tok")
    assert sorted(k[1] for k in expected) == [1, 2], "PRs must be part of the expected set"
    assert expected[("o/r", 2)]["is_pr"] is True


def test_archived_repos_are_skipped_but_active_ones_are_swept():
    mod = load_module()
    install_rest(
        mod,
        {
            "/orgs/FreeForCharity/repos": (
                [
                    {"full_name": HUB, "archived": False},
                    {"full_name": "FreeForCharity/dead", "archived": True},
                ],
                None,
            )
        },
    )
    assert mod.list_org_repos(ORG, "tok") == [HUB]


# ---------------------------------------------------------------- fail-closed


def test_missing_token_aborts():
    mod = load_module()
    saved = {k: mod.os.environ.pop(k, None) for k in ("GH_TOKEN", "GITHUB_TOKEN")}
    try:
        mod._token()
    except SystemExit as exc:
        assert "GH_TOKEN" in str(exc)
    else:
        raise AssertionError("an unauthenticated run must abort, not report an empty board")
    finally:
        for k, v in saved.items():
            if v is not None:
                mod.os.environ[k] = v


def test_a_missing_project_aborts_rather_than_reporting_an_empty_board():
    g = fake_graphql([{"organization": {"projectV2": None}}])
    try:
        M.fetch_board_items(ORG, 9, "tok", _graphql=g)
    except SystemExit as exc:
        assert "not found" in str(exc) or "not readable" in str(exc), str(exc)
        return
    raise AssertionError("an unreadable project must abort")


def test_an_unreadable_org_aborts():
    g = fake_graphql([{"organization": None}])
    try:
        M.fetch_board_items(ORG, 9, "tok", _graphql=g)
    except SystemExit:
        return
    raise AssertionError("an unreadable org must abort")


def test_a_non_list_rest_payload_aborts():
    """GitHub answers some errors with a 200 and an object; that is not zero rows."""
    mod = load_module()
    install_rest(mod, {"/repos/o/r/issues": ({"message": "Not Found"}, None)})
    try:
        mod.collect_expected(["o/r"], "tok")
    except SystemExit as exc:
        assert "array" in str(exc), str(exc)
        return
    raise AssertionError("an object where a list was expected must abort, not read as empty")


class _FakeResponse:
    """Minimal stand-in for urlopen's context manager."""

    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


def _urlopen_returning(payload):
    def _open(req, timeout=None):
        return _FakeResponse(payload)

    return _open


def test_graphql_errors_abort_even_on_http_200():
    """A permission denial arrives as 200 + `errors`, and would otherwise be an empty board.

    Exercised through `graphql` itself, not by grepping it: an assertion that the
    source *contains* the check passes against `if False and payload.get(...)`.
    That is how this test read green through a mutation run before being rewritten."""
    payload = {"data": None, "errors": [{"message": "Resource not accessible"}]}
    try:
        M.graphql("query{x}", {}, "tok", _urlopen=_urlopen_returning(payload))
    except SystemExit as exc:
        assert "Resource not accessible" in str(exc), str(exc)
        return
    raise AssertionError("a 200 carrying `errors` must abort, not yield an empty board")


def test_graphql_returns_data_when_there_are_no_errors():
    got = M.graphql("query{x}", {}, "tok", _urlopen=_urlopen_returning({"data": {"ok": 1}}))
    assert got == {"ok": 1}


def test_graphql_aborts_when_data_is_absent():
    try:
        M.graphql("query{x}", {}, "tok", _urlopen=_urlopen_returning({}))
    except SystemExit:
        return
    raise AssertionError("a response with no `data` must abort")


# ---------------------------------------------------------------- exit code


def test_has_findings_is_true_for_each_set_independently():
    """Asserted in-process: a subprocess run on a clean fixture only proves the zero path."""
    empty = {
        "expected_count": 0,
        "board_count": 0,
        "missing_from_board": [],
        "statusless": [],
        "closed_not_done": [],
    }
    assert not M.has_findings(empty), "all-empty must be clean"
    for key in ("missing_from_board", "statusless", "closed_not_done"):
        result = dict(empty, **{key: [{"repo": HUB, "number": 1}]})
        assert M.has_findings(result), f"a non-empty {key} must make the audit non-zero"


def test_main_returns_the_exit_code_has_findings_decides():
    """Pin the wiring: `main` must not compute its own verdict."""
    src = SCRIPT.read_text(encoding="utf-8")
    assert "return 1 if has_findings(result) else 0" in src, (
        "main's return must be driven by has_findings, so the tested decision is the "
        "shipped one"
    )


# ---------------------------------------------------------------- the two structural rules


def executable_source(path):
    """The script's source with comments and DOCSTRINGS removed — but not other strings.

    Both rules below are about what the script *does*, and both of their needles
    legitimately appear in its prose: the docstring explains that `gh search` is
    excluded and that no `mutation` is sent. A plain substring scan over the file
    therefore trips on the documentation written to satisfy the same issue.

    The first version of this helper dropped every STRING token, which is wrong
    in the direction that matters: an API path in a call *is* a string literal,
    so the guard deleted exactly what it was looking for. Re-pointing
    `collect_expected` at `search/issues` — the whole defect — left it green.
    Parsing and stripping only docstrings (a bare string expression statement)
    keeps real values in view; `ast.unparse` drops comments for free."""
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if (
            isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            body.pop(0)
            if not body:
                body.append(ast.Pass())
    return ast.unparse(ast.fix_missing_locations(tree))


def test_the_helper_that_strips_prose_keeps_real_string_values():
    """The guard below is only as good as this — see executable_source's docstring."""
    code = executable_source(SCRIPT)
    assert "api.github.com" in code, (
        "executable_source must keep ordinary string literals; stripping them would "
        "delete the API paths the search guard exists to look for"
    )
    assert "run 62" not in code, "executable_source must drop docstring prose"


def test_the_script_never_uses_the_search_api():
    """#966's central rule. The search index lags writes; the audit must not read it."""
    code = executable_source(SCRIPT)
    for needle in ("search/issues", "search/repositories", "gh search", "search_issues"):
        assert needle not in code, (
            f"{needle!r} appears in executable code. The expected set must come from the "
            "REST enumeration — a search-backed audit under-reports exactly for the "
            "newest items, which are the ones most likely to be missing (#966)."
        )


def test_the_script_issues_no_graphql_mutation():
    code = executable_source(SCRIPT)
    assert "mutation" not in code.lower(), (
        "the audit is read-only; a `mutation` in executable code would make it a writer"
    )


def test_the_script_issues_no_rest_write():
    src = SCRIPT.read_text(encoding="utf-8")
    for verb in ('method="PATCH"', 'method="PUT"', 'method="DELETE"'):
        assert verb not in src, f"{verb} is a write; this script is read-only"
    posts = re.findall(r'method="POST"', src)
    assert len(posts) == 1, (
        f"exactly one POST is expected — the GraphQL read — found {len(posts)}. "
        "Any other POST would be a write."
    )


def test_the_reason_for_avoiding_search_is_written_down_with_its_citation():
    """#966 asks for a comment saying why, citing the issue. Prose is the deliverable here."""
    src = SCRIPT.read_text(encoding="utf-8")
    assert "#966" in src, "the script must cite the issue that explains the rule"
    assert "lags" in src, "the script must say WHY search is excluded, not merely that it is"


# ---------------------------------------------------------------- reporting


def test_the_report_prints_both_denominators():
    """"0 missing" off a short expected set is only visible as a suspicious count."""
    result = M.audit({}, M.normalize_board_items([board_node(HUB, 1)]))
    text = M.render(result, ORG, 9, "Agentic OS", [HUB, ADMIN])
    assert "expected=0" in text and "board=1" in text, text
    assert "repos_swept=2" in text, text


def test_a_card_with_no_content_is_not_labelled_a_draft():
    """Three kinds of card, three remedies — so three labels (Copilot, #968).

    A card whose issue was deleted (or whose repo this token cannot read) has
    no repo AND no `DraftIssue` type. Labelling it "(draft item)" sent triage
    hunting for a draft that does not exist, and hid the one row that may need
    the card deleted outright."""
    deleted = {"id": "gone", "fieldValueByName": None, "content": None}
    # All three are statusless, so all three appear in the same section and the
    # labels can be compared side by side. A card with a Status and an open
    # issue lands in no section at all — asserting on it here would be asserting
    # on the absence of a row for the wrong reason.
    rows = M.normalize_board_items(
        [deleted, draft_node("a real draft"), board_node(HUB, 7, status=None)]
    )
    text = M.render(M.audit({}, rows), ORG, 9, None, [HUB])
    assert text.count("(draft item)") == 1, f"only the DraftIssue may be called a draft:\n{text}"
    assert "no readable content" in text, f"the contentless card must say so:\n{text}"
    assert "FFC-Cloudflare-Automation#7" in text, f"a real issue keeps its identity:\n{text}"


def test_a_contentless_card_still_reaches_the_statusless_set():
    """It is kept precisely so it can be reported; the label fix must not drop it."""
    rows = M.normalize_board_items([{"id": "gone", "fieldValueByName": None, "content": None}])
    assert [r["id"] for r in M.audit({}, rows)["statusless"]] == ["gone"]
    assert rows[0]["key"] is None, "a contentless card must carry no comparison key"


def test_the_report_names_every_finding():
    expected = {
        (HUB, 965): {"repo": HUB, "number": 965, "title": "a new PR", "url": "", "is_pr": True}
    }
    text = M.render(M.audit(expected, []), ORG, 9, None, [HUB])
    assert "FFC-Cloudflare-Automation#965" in text, text
    assert "a new PR" in text, text


def test_an_empty_section_says_none_rather_than_nothing():
    text = M.render(M.audit({}, []), ORG, 9, None, [HUB])
    assert text.count("(none)") == 3, "each empty set must be visibly empty, not absent"


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
    sys.exit(1 if failures else 0)
