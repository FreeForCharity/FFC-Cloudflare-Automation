"""Tests for the pull-request-permission guard (#1417).

The subject is `scripts/check-workflow-pull-request-permission.py`. What it
claims: every job whose executable body POSITIVELY reads pull requests declares
`pull-requests` in its EFFECTIVE `permissions:` — job-level where present,
otherwise top-level — and the freeze is exact in both directions.

The defect it closes is silent by construction. 747 declared `contents: read` +
`issues: write`; an explicit block sets every unnamed scope to `none`, so its
`issues.listForRepo` returned issues only and nothing raised. `open-pr-cap` then
scored a filtered listing as `OK — 0 open` against a ground truth of 3.

Three failure modes this module is designed against:

* **Vacuous green.** A sweep whose input set silently empties passes by
  inspecting nothing. `test_the_scan_sees_the_real_tree` pins the denominator,
  and `test_the_shipped_747_declares_the_scope` plus
  `test_the_conventional_pair_are_clean` pin the specific real workflows whose
  absence from the scan would be indistinguishable from never having looked.

* **A guard that cannot fail.** Reading the checker proves it is wired, never
  that it detects. The pre-fix control is therefore taken from `main`'s OWN TEXT
  via `git show` (ledger L47) rather than hand-written to fail, so it cannot
  drift into a shape 747 never had.

* **A guard that flags its own remedy, or the opposite use of the same token.**
  `!i.pull_request` EXCLUDES pull requests — 113, 228, 737 and 740 all do this
  to find a marker-bearing issue, and a token that cannot see PRs gives them
  exactly what they asked for. Flagging it would be the #1019 shape. The `!` is
  the boundary and it is asserted here, not left to inspection.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import tempfile

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CHECKER = REPO_ROOT / "scripts" / "check-workflow-pull-request-permission.py"


def load_checker():
    """Import the checker as a module so its constants can be redirected."""
    spec = importlib.util.spec_from_file_location("pr_perm_guard", CHECKER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_on(files: dict[str, str], freeze: dict | None = None):
    """Run the guard over a synthetic `.github/workflows` tree.

    Returns `(findings, hard_errors, scanned)`. The module's `WORKFLOWS` is
    redirected rather than the real tree being mutated — the repo's own mutation
    sites work on a copy for the same reason (ledger L182).
    """
    mod = load_checker()
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        for name, text in files.items():
            (root / name).write_text(text, encoding="utf-8", newline="\n")
        mod.WORKFLOWS = root
        if freeze is not None:
            mod.KNOWN_MISSING_PR_PERMISSION = freeze
        return mod.scan_all()


def wf(body: str, permissions: str | None = "  contents: read\n  issues: write\n",
       job_permissions: str | None = None) -> str:
    """A minimal one-job workflow carrying `body` as a github-script `script:`."""
    out = ["name: probe", "on:", "  workflow_dispatch:", ""]
    if permissions is not None:
        out.append("permissions:")
        out.append(permissions.rstrip("\n"))
        out.append("")
    out += ["jobs:", "  check:", "    runs-on: ubuntu-latest"]
    if job_permissions is not None:
        out.append("    permissions:")
        for line in job_permissions.rstrip("\n").splitlines():
            # The caller writes keys at 2 spaces; a job block nests them at 6.
            out.append("    " + line)
    out += [
        "    steps:",
        "      - uses: actions/github-script@v9",
        "        with:",
        "          script: |",
    ]
    for line in body.splitlines():
        out.append("            " + line)
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------
# The denominator, and the real tree
# --------------------------------------------------------------------------


def test_the_scan_sees_the_real_tree():
    """A sweep that reads nothing passes; pin the count it actually read."""
    mod = load_checker()
    _findings, _errors, scanned = mod.scan_all()
    assert scanned > 90, (
        f"only {scanned} workflow file(s) scanned; the real tree holds >90, so "
        f"this sweep has gone blind and a green result means nothing"
    )


def test_the_real_tree_is_clean_apart_from_the_freeze():
    """Exit 0 on the tree as shipped -- the guard must not be born red."""
    mod = load_checker()
    assert mod.main() == 0, "the guard reports findings on the tree it ships with"


def test_the_shipped_747_declares_the_scope():
    """The fix itself, asserted at the file rather than via the guard."""
    text = (REPO_ROOT / ".github" / "workflows" / "747-conductor-liveness.yml").read_text(
        encoding="utf-8"
    )
    head = text.split("jobs:", 1)[0]
    assert "pull-requests: read" in head, (
        "747's top-level permissions no longer declare `pull-requests: read`; "
        "its merge-silence and open-pr-cap signals are blind again (#1417)"
    )


def test_the_conventional_pair_are_clean():
    """737 and 739 established the convention 747 diverged from.

    737 is the load-bearing case: its TOP level is `contents: read` alone and it
    is correct, because each job declares the scope itself. A guard reading only
    the top level would report it.
    """
    mod = load_checker()
    findings, _errors, _scanned = mod.scan_all()
    for name in ("737-claim-sync.yml", "739-process-health-metrics.yml"):
        offenders = [f for f in findings if f.startswith(name)]
        assert not offenders, f"{name} reported, but it declares the scope: {offenders}"


def test_the_pre_fix_747_is_a_finding():
    """The control, derived from the SHIPPED text by inverting the fix (L47).

    NOT `git show main:…`, which is what this shipped as and what CI rejected:
    the runner checks out a detached PR merge ref with no local `main`, so the
    call died with `invalid object name 'main'`. It passed on the Conductor host,
    where `main` exists. A control that cannot run is not a control — and this one
    failed in the direction of looking like a defect in the guard rather than in
    the test.

    Inverting the fix — removing the single line it added — keeps the property
    that matters: the control is real workflow text rather than a hand-written
    fixture that could drift into a shape 747 never had. It is also
    self-falsifying, because if the line is not there to remove it says so
    instead of quietly passing over a tree where the fix was reverted.
    """
    text = (REPO_ROOT / ".github" / "workflows" / "747-conductor-liveness.yml").read_text(
        encoding="utf-8"
    )
    head, sep, tail = text.partition("jobs:")
    assert sep, "747 has no `jobs:` key; the file layout has changed"
    assert "  pull-requests: read\n" in head, (
        "747's top-level block does not declare `pull-requests: read`, so there is "
        "nothing to invert -- either the fix was reverted (in which case the guard "
        "should be failing on the real tree) or the scope moved to the job level "
        "and this control needs rewriting"
    )
    before = head.replace("  pull-requests: read\n", "", 1) + sep + tail
    assert "pull-requests" not in before.split("jobs:", 1)[0], (
        "removing the declaration left another `pull-requests` mention in the "
        "top-level block, so this control does not reproduce the pre-fix state"
    )

    findings, errors, _scanned = run_on({"747-conductor-liveness.yml": before}, freeze={})
    assert not errors, f"the control produced hard errors: {errors}"
    assert len(findings) == 1, f"expected exactly one finding, got {findings}"
    assert "747-conductor-liveness.yml:check" in findings[0]
    assert "pull-requests" in findings[0]


# --------------------------------------------------------------------------
# The boundary: a positive read versus the opposite use of the same token
# --------------------------------------------------------------------------


def test_a_positive_pull_request_key_read_is_a_finding():
    findings, errors, _ = run_on(
        {"p.yml": wf("const o = await github.rest.issues.listForRepo({});\n"
                     "if (o.data[0].pull_request) { core.info('pr'); }\n")},
        freeze={},
    )
    assert not errors, errors
    assert len(findings) == 1, findings


def test_a_negated_pull_request_key_is_not_a_finding():
    """`!i.pull_request` excludes PRs; the missing scope makes it MORE correct."""
    findings, errors, _ = run_on(
        {"p.yml": wf("const o = await github.rest.issues.listForRepo({});\n"
                     "const hit = o.data.find((i) => !i.pull_request && i.body);\n")},
        freeze={},
    )
    assert not errors, errors
    assert not findings, f"the exclusion filter was reported: {findings}"


def test_the_negation_boundary_is_the_bang_and_not_the_word():
    """Both spellings in one body: only the positive one is a read.

    Asserted on `positive_pr_reads` rather than on the finding count, because a
    finding is emitted once PER JOB however many reads it holds — so a body
    carrying both spellings yields one finding either way, and an assertion on
    the count cannot tell the negation clause is working. Found by a mutation
    that dropped the clause and flipped this case not at all.
    """
    mod = load_checker()
    reads = mod.positive_pr_reads(
        "const a = list.find((i) => !i.pull_request);\n"
        "const b = list.filter((j) => j.pull_request);\n"
    )
    assert reads == ["j.pull_request"], (
        f"expected only the un-negated read, got {reads} -- the `!` is the "
        f"boundary between excluding pull requests and depending on them"
    )


def test_the_pulls_api_is_a_finding():
    findings, errors, _ = run_on(
        {"p.yml": wf("const prs = await github.rest.pulls.list({ state: 'open' });\n")},
        freeze={},
    )
    assert not errors, errors
    assert len(findings) == 1, findings
    assert "rest.pulls.list" in findings[0]


def test_the_event_payload_is_not_an_api_read():
    """`github.event.pull_request` arrives with the event and costs no scope."""
    findings, errors, _ = run_on(
        {"p.yml": wf("const n = context.payload.pull_request.number;\n"
                     "core.info(`${n}`);\n")},
        freeze={},
    )
    assert not errors, errors
    assert not findings, f"the event payload was reported: {findings}"


def test_a_comment_naming_the_token_is_not_a_finding():
    """#1019: a guard that flags prose ABOUT the thing it catches gets switched off."""
    findings, _errors, _ = run_on(
        {"p.yml": wf("// reads i.pull_request from the listing, see github.rest.pulls.list\n"
                     "core.info('nothing');\n")},
        freeze={},
    )
    assert not findings, f"a comment line was reported: {findings}"


def test_a_block_comment_naming_the_token_is_not_a_finding():
    """The `/* */` half of #1019, which the first cut of this guard missed.

    747's own inline script is heavily commented about `pull_request`. Today
    those are `//`; an author who reaches for `/* */` -- or wraps a paragraph
    of rationale in one -- would have reddened CI on correct code.
    """
    for label, prose in (
        ("one line", "/* reads i.pull_request from the listing */\n"),
        ("multi-line", "/*\n * reads i.pull_request\n * and github.rest.pulls.list\n */\n"),
        ("trailing on a line of code", "core.info('x'); /* i.pull_request */\n"),
    ):
        findings, errors, _ = run_on({"p.yml": wf(prose + "core.info('nothing');\n")}, freeze={})
        assert not errors, (label, errors)
        assert not findings, f"a {label} block comment was reported: {findings}"


def test_a_glob_in_a_string_does_not_open_a_block_comment():
    """`'src/*'` is a glob, and mistaking it for a comment opener is fail-OPEN.

    A quote-blind stripper blanks from the glob to the next `*/` -- here, past
    the read -- and the workflow is then reported as correctly scoped. This is
    the case that decides whether block-comment handling is an improvement or a
    regression, so both quote styles are pinned.
    """
    for quote in ("'", '"'):
        body = (
            f"const pattern = {quote}src/*{quote};\n"
            "if (i.pull_request) { skip() }\n"
            f"const close = {quote}*/{quote};\n"
        )
        findings, errors, _ = run_on({"p.yml": wf(body)}, freeze={})
        assert not errors, errors
        assert findings, f"a read after a {quote}-quoted glob was LOST: {body!r}"


def test_an_unclosed_block_comment_leaves_the_rest_scannable():
    """No `*/` means no span, rather than swallowing the file to EOF."""
    body = "/* opened and never closed\nif (i.pull_request) { skip() }\n"
    findings, errors, _ = run_on({"p.yml": wf(body)}, freeze={})
    assert not errors, errors
    assert findings, "an unclosed `/*` swallowed the read that followed it"


def test_an_apostrophe_in_a_comment_does_not_invert_the_polarity():
    """`// the listing's shape` must not open a string that runs on.

    A single quote inside prose is the commonest way a naive quote tracker
    flips state for the remainder of the body, after which every `/*` and every
    string boundary is read wrongly.
    """
    for lead in ("//", "#"):
        body = f"{lead} the listing's shape\nif (i.pull_request) {{ skip() }}\n"
        findings, errors, _ = run_on({"p.yml": wf(body)}, freeze={})
        assert not errors, errors
        assert findings, f"an apostrophe in a {lead} comment lost the read that followed"


def test_the_finding_states_the_consequence_that_is_TRUE_of_the_spelling_it_found():
    """L307: a guard that fires correctly and explains wrongly misdirects its reader.

    One message for three spellings was wrong for two of them. `issues.listForRepo`
    is the silent one — the whole reason this guard exists — but a `rest.pulls.*`
    call hits the pulls endpoint and is not "a listing that returns issues only",
    and a single-number `issues.get` (the shape the 113 freeze rests on) may
    refuse outright rather than return a short list. Only "its token cannot see
    pull requests" is true of all three, so only that is unconditional.
    """
    key_only, errors, _ = run_on({"p.yml": wf("if (i.pull_request) { skip() }\n")}, freeze={})
    assert not errors, errors
    assert len(key_only) == 1, key_only
    assert "cannot see pull requests" in key_only[0], key_only[0]
    assert "will not raise" in key_only[0], "the silent-listing case must still say so"

    pulls, errors, _ = run_on({"p.yml": wf("await github.rest.pulls.list({})\n")}, freeze={})
    assert not errors, errors
    assert len(pulls) == 1, pulls
    assert "cannot see pull requests" in pulls[0], pulls[0]
    assert "will not raise" not in pulls[0], (
        "a rest.pulls.* read is not a listing that silently returns issues: " + pulls[0]
    )


def test_a_backtick_in_a_line_comment_does_not_hide_a_later_block_comment():
    """Why the scanner skips a `//`/`#` comment WHOLE rather than reading it.

    A lone backtick is ordinary in FFC workflow comments (they quote code), and
    a backtick template literal is exempt from the end-of-line close because it
    legitimately spans lines. Read the comment character by character and that
    backtick opens a string that never shuts, so every later `/*` looks quoted
    and the block comment below goes unblanked.

    The apostrophe case cannot see this: an unterminated `'` closes at the
    newline, so the line skip and that rule mask each other there. Mutation
    review found exactly that redundancy, which is why this case exists.

    The backtick must come BEFORE any apostrophe on the line, and the first
    draft of this case got that wrong: with the line skip dropped, an earlier
    `'` opens a string the backtick then sits INSIDE, and the newline closes it
    -- so the phantom never forms and the case passes under its own mutation.
    """
    body = (
        "// a lone backtick ` in prose, quoting code\n"
        "/* reads i.pull_request */\n"
        "core.info('nothing');\n"
    )
    findings, errors, _ = run_on({"p.yml": wf(body)}, freeze={})
    assert not errors, errors
    assert not findings, f"a backtick in a line comment hid a block comment: {findings}"


def test_an_unterminated_quote_does_not_run_past_its_line():
    """Bounds the blast radius of a malformed body, pinned at the span level.

    End to end this is masked by the line skip above, so it is asserted against
    `block_comment_spans` directly: an unterminated `'` must not consume the
    `/* ... */` two lines below it. A backtick may, and does -- that asymmetry
    is the rule, so both halves are asserted here rather than only the one that
    fails safe.
    """
    mod = load_checker()
    apostrophe = "const s = 'unterminated\nconst t = 2\n/* c */\n"
    assert mod.block_comment_spans(apostrophe), (
        "an unterminated ' swallowed the block comment two lines below it"
    )
    backtick = "const s = `unterminated\nconst t = 2\n/* c */\n"
    assert not mod.block_comment_spans(backtick), (
        "a template literal must stay open across newlines"
    )


def test_a_read_inside_a_string_literal_is_still_reported():
    """Declared behaviour, not an oversight -- and the reason is asymmetric.

    Blanking a string literal's contents would silence `core.info('reads
    i.pull_request')`, and it is the one change here that could convert this
    scanner from over-reporting to UNDER-reporting: a read written inside a
    string would then be invisible, and the workflow would be reported as
    correctly scoped while its token cannot see pull requests -- the exact L02
    fail-open this module exists to catch. The remedy for the false positive is
    to reword the string, which costs a CI run someone reads.
    """
    body = "core.info('reads i.pull_request from the listing');\n"
    findings, errors, _ = run_on({"p.yml": wf(body)}, freeze={})
    assert not errors, errors
    assert findings, "the over-report inside a string literal is deliberate and was lost"


# --------------------------------------------------------------------------
# Effective permissions
# --------------------------------------------------------------------------


def test_a_job_level_block_satisfies_the_rule():
    findings, errors, _ = run_on(
        {"p.yml": wf("if (i.pull_request) {}\n",
                     permissions="  contents: read\n",
                     job_permissions="  contents: read\n  pull-requests: read\n")},
        freeze={},
    )
    assert not errors, errors
    assert not findings, findings


def test_a_job_level_block_REPLACES_the_top_level_one():
    """A job block that omits the scope is a finding even when the top level has it."""
    findings, errors, _ = run_on(
        {"p.yml": wf("if (i.pull_request) {}\n",
                     permissions="  contents: read\n  pull-requests: read\n",
                     job_permissions="  contents: read\n  issues: write\n")},
        freeze={},
    )
    assert not errors, errors
    assert len(findings) == 1, f"a job block does not merge with the top level: {findings}"


def test_no_permissions_block_anywhere_is_out_of_scope():
    """The repository default is unknowable here, so it is not guessed at."""
    findings, errors, _ = run_on(
        {"p.yml": wf("if (i.pull_request) {}\n", permissions=None)},
        freeze={},
    )
    assert not errors, errors
    assert not findings, f"a workflow with no explicit block was reported: {findings}"


def test_read_all_satisfies_the_rule():
    findings, errors, _ = run_on(
        {"p.yml": wf("if (i.pull_request) {}\n", permissions=None).replace(
            "jobs:", "permissions: read-all\n\njobs:", 1
        )},
        freeze={},
    )
    assert not errors, errors
    assert not findings, findings


def test_a_run_body_is_scanned_too_not_only_github_script():
    body = (
        "name: probe\non:\n  workflow_dispatch:\n\npermissions:\n  contents: read\n\n"
        "jobs:\n  check:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - run: |\n          node -e 'if (i.pull_request) {}'\n"
    )
    findings, errors, _ = run_on({"p.yml": body}, freeze={})
    assert not errors, errors
    assert len(findings) == 1, findings


# --------------------------------------------------------------------------
# Fail closed
# --------------------------------------------------------------------------


def test_unparseable_yaml_is_a_hard_error():
    findings, errors, _ = run_on({"p.yml": "name: [unclosed\n"}, freeze={})
    assert errors, "a workflow that will not parse was passed over in silence"
    assert not findings


def test_a_non_mapping_permissions_is_a_hard_error():
    body = (
        "name: probe\non:\n  workflow_dispatch:\n\npermissions:\n  - contents\n\n"
        "jobs:\n  check:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo hi\n"
    )
    _findings, errors, _ = run_on({"p.yml": body}, freeze={})
    assert any("not a mapping" in e for e in errors), errors


def test_an_unrecognised_permissions_string_is_a_hard_error():
    body = (
        "name: probe\non:\n  workflow_dispatch:\n\npermissions: something-else\n\n"
        "jobs:\n  check:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo hi\n"
    )
    _findings, errors, _ = run_on({"p.yml": body}, freeze={})
    assert any("not a recognised form" in e for e in errors), errors


def test_a_non_string_script_is_a_hard_error():
    body = (
        "name: probe\non:\n  workflow_dispatch:\n\npermissions:\n  contents: read\n\n"
        "jobs:\n  check:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - uses: actions/github-script@v9\n        with:\n          script: 42\n"
    )
    _findings, errors, _ = run_on({"p.yml": body}, freeze={})
    assert any("not a string" in e for e in errors), errors


# --------------------------------------------------------------------------
# The freeze is exact in both directions
# --------------------------------------------------------------------------


def test_the_freeze_excuses_only_the_named_job():
    """A frozen job passes; a sibling in the same file still fails."""
    mod = load_checker()
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        body = (
            "name: probe\non:\n  workflow_dispatch:\n\npermissions:\n  contents: read\n\n"
            "jobs:\n"
            "  resolve:\n    runs-on: ubuntu-latest\n    steps:\n"
            "      - run: node -e 'if (a.pull_request) {}'\n"
            "  other:\n    runs-on: ubuntu-latest\n    steps:\n"
            "      - run: node -e 'if (b.pull_request) {}'\n"
        )
        (root / "p.yml").write_text(body, encoding="utf-8", newline="\n")
        mod.WORKFLOWS = root
        mod.KNOWN_MISSING_PR_PERMISSION = {"p.yml": ("resolve",)}
        assert mod.main() == 1, "the unfrozen sibling job was excused by the freeze"


def test_a_freeze_entry_naming_a_missing_file_is_an_error():
    mod = load_checker()
    with tempfile.TemporaryDirectory() as td:
        mod.WORKFLOWS = pathlib.Path(td)
        mod.KNOWN_MISSING_PR_PERMISSION = {"nope.yml": ("check",)}
        errors = mod.compare({})
        assert any("no such workflow exists" in e for e in errors), errors


def test_a_stale_freeze_entry_is_an_error():
    """A job that no longer offends must be pruned, not left excused."""
    mod = load_checker()
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        (root / "p.yml").write_text(
            "name: probe\non:\n  workflow_dispatch:\n\npermissions:\n  contents: read\n"
            "  pull-requests: read\n\njobs:\n  check:\n    runs-on: ubuntu-latest\n"
            "    steps:\n      - run: node -e 'if (i.pull_request) {}'\n",
            encoding="utf-8",
            newline="\n",
        )
        mod.WORKFLOWS = root
        mod.KNOWN_MISSING_PR_PERMISSION = {"p.yml": ("check",)}
        assert mod.main() == 1, "a stale freeze entry was not reported"


def test_the_shipped_freeze_describes_the_tree():
    """113's entry must be live: frozen AND still offending, or it is stale."""
    mod = load_checker()
    assert set(mod.KNOWN_MISSING_PR_PERMISSION) == {
        "113-cloudflare-domain-register.yml"
    }, (
        "the shipped freeze has changed; if 113 was fixed or another entry added, "
        "update the docstring's reasoning with it"
    )
    findings, _errors, _ = mod.scan_all()
    assert any(
        f.startswith("113-cloudflare-domain-register.yml:resolve") for f in findings
    ), (
        "113:resolve is frozen but no longer reported, so the freeze has stopped "
        "describing the tree -- prune the entry"
    )


TESTS = [
    test_the_scan_sees_the_real_tree,
    test_the_real_tree_is_clean_apart_from_the_freeze,
    test_the_shipped_747_declares_the_scope,
    test_the_conventional_pair_are_clean,
    test_the_pre_fix_747_is_a_finding,
    test_a_positive_pull_request_key_read_is_a_finding,
    test_a_negated_pull_request_key_is_not_a_finding,
    test_the_negation_boundary_is_the_bang_and_not_the_word,
    test_the_pulls_api_is_a_finding,
    test_the_event_payload_is_not_an_api_read,
    test_a_comment_naming_the_token_is_not_a_finding,
    test_a_block_comment_naming_the_token_is_not_a_finding,
    test_a_glob_in_a_string_does_not_open_a_block_comment,
    test_an_unclosed_block_comment_leaves_the_rest_scannable,
    test_an_apostrophe_in_a_comment_does_not_invert_the_polarity,
    test_the_finding_states_the_consequence_that_is_TRUE_of_the_spelling_it_found,
    test_a_backtick_in_a_line_comment_does_not_hide_a_later_block_comment,
    test_an_unterminated_quote_does_not_run_past_its_line,
    test_a_read_inside_a_string_literal_is_still_reported,
    test_a_job_level_block_satisfies_the_rule,
    test_a_job_level_block_REPLACES_the_top_level_one,
    test_no_permissions_block_anywhere_is_out_of_scope,
    test_read_all_satisfies_the_rule,
    test_a_run_body_is_scanned_too_not_only_github_script,
    test_unparseable_yaml_is_a_hard_error,
    test_a_non_mapping_permissions_is_a_hard_error,
    test_an_unrecognised_permissions_string_is_a_hard_error,
    test_a_non_string_script_is_a_hard_error,
    test_the_freeze_excuses_only_the_named_job,
    test_a_freeze_entry_naming_a_missing_file_is_an_error,
    test_a_stale_freeze_entry_is_an_error,
    test_the_shipped_freeze_describes_the_tree,
]

if __name__ == "__main__":
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {str(e)[:400]}")
    sys.exit(1 if failures else 0)
