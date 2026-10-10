"""A new ledger id must not be one another open PR already claims.

The defect this guards, measured on #1584 and #1586 (Conductor run 240). `main`
ended at L351; #1584 added L352-L355 and #1586 also added L352. Different
lessons, same id, and **both PRs were green** — `test_lessons_ledger.py` compares
the ledger against itself, so each branch saw a unique L352 and reported 79 PASS
/ 0 FAIL. The duplicate existed only in the union, which no per-PR check builds.

Two properties make it worth a guard rather than a doc line:

  * the merge queue catches it, but on the **merge group** — so the red names
    neither PR's own change, and it costs a full ~17-minute cycle (CLAUDE.md's
    measured budget) to find out;
  * the natural conflict resolution makes it worse. #1584 re-pads all ~145 rows,
    so the pair conflicts across the whole table, and "keep the re-padded table,
    append the new row" ships **two** L352 rows. That is L43's failure verbatim.

The evidence was in the repository and unread: #1588's `reserved-ids` block
declared `L352 #1584` two days before anyone looked. This module exists because
that signal was free and nothing consumed it.

Everything here is pure Python against fixtures. No bash, no network, no `gh` —
deliberately, so the decision logic is testable on the Conductor's Windows host
where every bash-invoking module in this suite is red for platform reasons
(CLAUDE.md's "local red is a host artifact" set, and L08).
"""

from __future__ import annotations

import importlib.util
import pathlib
import re
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_GUARD_PATH = _REPO_ROOT / "scripts" / "check-ledger-id-collisions.py"

_spec = importlib.util.spec_from_file_location("ledger_id_guard", _GUARD_PATH)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

LEDGER = _REPO_ROOT / "docs" / "lessons-ledger.md"
CI_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "722-ci.yml"

# A base holding two rows, so "new" is a real subtraction rather than "all ids".
BASE = (
    "| ID   | Lesson | Enforced by | Evidence |\n"
    "| ---- | ------ | ----------- | -------- |\n"
    "| L350 | an inherited lesson | `doc` -- prose | run 1 |\n"
    "| L351 | another inherited lesson | `doc` -- prose | run 2 |\n"
    "\n<!-- reserved-ids\n-->\n"
)


def _ledger(rows: tuple[int, ...], reserved: tuple[str, ...] = ()) -> str:
    """A ledger holding BASE's rows plus `rows`, and `reserved` declarations."""
    text = (
        "| ID   | Lesson | Enforced by | Evidence |\n"
        "| ---- | ------ | ----------- | -------- |\n"
        "| L350 | an inherited lesson | `doc` -- prose | run 1 |\n"
        "| L351 | another inherited lesson | `doc` -- prose | run 2 |\n"
    )
    for lid in rows:
        text += f"| L{lid} | a new lesson | `doc` -- prose | run 3 |\n"
    block = "\n".join(reserved)
    text += f"\n<!-- reserved-ids\n{block}\n-->\n" if reserved else "\n<!-- reserved-ids\n-->\n"
    return text


# --- parsing ----------------------------------------------------------------


def test_row_ids_reads_every_row_and_nothing_else():
    ids = guard.row_ids(_ledger((352, 353)))
    assert ids == {350, 351, 352, 353}, ids


def test_an_id_mentioned_in_PROSE_is_not_a_claim():
    """Anchoring matters: ledger rows routinely cite other rows by id.

    L43 is named inside the error text of the very test that catches duplicate
    ids, so a guard matching `L\\d+` anywhere would read a row's commentary as a
    claim on that id and refuse every PR that referenced one.
    """
    text = _ledger((352,)).replace(
        "| L352 | a new lesson |", "| L352 | a new lesson, see L43 and L351 |"
    )
    assert guard.row_ids(text) == {350, 351, 352}, guard.row_ids(text)


def test_reserved_ids_reads_the_holder():
    assert guard.reserved_ids(_ledger((), ("L352 #1584", "L356 #1588"))) == {
        352: "#1584",
        356: "#1588",
    }


def test_a_reservation_with_no_holder_is_ignored_here():
    """`test_lessons_ledger.py` already errors on it; two voices do not help."""
    assert guard.reserved_ids(_ledger((), ("L352",))) == {}


def test_the_guard_and_the_ledger_test_agree_on_what_a_reservation_IS():
    """The two files parse the same block, so pin them to each other.

    `check-ledger-id-collisions.py` says in a comment that its reservation
    regexes are "deliberately identical in shape" to the ledger test's. A comment
    is not a constraint -- if `test_lessons_ledger.py` ever accepts a syntax this
    guard does not, the guard stops seeing reservations and silently reverts to
    the both-rows arm alone. That is the opt-in-guard failure (an enforcement
    whose coverage quietly shrinks while its pass count stays green), so the
    agreement is asserted rather than asserted-in-prose.
    """
    spec = importlib.util.spec_from_file_location(
        "ledger_rules", _REPO_ROOT / "tests" / "workflow-logic" / "test_lessons_ledger.py"
    )
    ledger_rules = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ledger_rules)

    block = "<!-- reserved-ids\nL352 #1584\nL353 #1584\n-->"
    theirs, complaints = ledger_rules.declared_reservations(block)
    mine = guard.reserved_ids(block)

    assert not complaints, complaints
    assert {f"L{lid}" for lid in mine} == set(theirs), (
        f"the two parsers disagree on the same block: guard={sorted(mine)} "
        f"ledger-test={sorted(theirs)} — reconcile the regex pair in "
        "scripts/check-ledger-id-collisions.py"
    )


# --- the both-rows arm ------------------------------------------------------


def test_the_same_new_id_on_two_PRs_is_reported():
    problems = guard.collision_problems(
        BASE, _ledger((352,)), {"#1584": _ledger((352, 353))}, "#1586"
    )
    assert len(problems) == 1, problems
    assert "L352" in problems[0] and "#1584" in problems[0], problems[0]


def test_an_id_INHERITED_from_the_base_is_not_a_collision():
    """Both branches carry every row `main` already has. If "new" were measured
    as "present", every PR would collide with every other PR on all ~350 rows."""
    assert (
        guard.collision_problems(
            BASE, _ledger((352,)), {"#1584": _ledger((353,))}, "#1586"
        )
        == []
    )


def test_distinct_new_ids_are_clean():
    assert (
        guard.collision_problems(
            BASE, _ledger((357,)), {"#1584": _ledger((352, 353, 354, 355))}, "#1586"
        )
        == []
    )


# --- the declared-elsewhere arm --------------------------------------------


def test_a_row_the_SIBLING_declares_reserved_for_someone_else_is_reported():
    """The #1588 case: the sibling has only the declaration, not the row.

    This is the arm that fires earliest and most cheaply, and it is the one the
    both-rows arm cannot cover -- #1588 never wrote an L352 row, so comparing
    rows alone finds nothing.
    """
    problems = guard.collision_problems(
        BASE,
        _ledger((352,)),
        {"#1588": _ledger((356,), ("L352 #1584", "L353 #1584"))},
        "#1586",
    )
    assert len(problems) == 1, problems
    assert "L352" in problems[0] and "#1584" in problems[0], problems[0]


def test_an_id_reserved_FOR_ME_is_not_a_collision():
    """Declaring your own id is how the block is meant to be used."""
    assert (
        guard.collision_problems(
            BASE, _ledger((352,)), {"#1588": _ledger((356,), ("L352 #1584",))}, "#1584"
        )
        == []
    )


def test_a_cosmetic_holder_spelling_still_counts_as_mine():
    """`PR #1584` and a URL must not read as a different holder.

    Failing here would block a PR that did the right thing, which is the
    expensive direction -- a false refusal trains the next agent to route around
    the guard.
    """
    for holder in ("PR #1584", "#1584", "https://github.com/o/r/pull/1584"):
        assert (
            guard.collision_problems(
                BASE, _ledger((352,)), {"#1588": _ledger((), (f"L352 {holder}",))}, "#1584"
            )
            == []
        ), holder


def test_a_siblings_STALE_reservation_for_a_landed_row_does_not_fail_my_PR():
    """A reservation whose holder has already landed must not block anyone.

    This window is routine, not hypothetical: the documented remedy is that
    whoever reaches the queue second drops the matching `reserved-ids` lines, so
    between #1584 landing and #1588 being updated, #1588 declares `L352 #1584`
    while L352 is a row on `main`. If this guard measured "ids I have" rather
    than "ids new since the base", every unrelated PR would then fail on an id
    it merely inherited -- and the failure would name a row nobody touched,
    which is the same unactionable red this guard exists to replace.

    Found by mutation: dropping the `- base` subtraction left every other test
    green, so the suite was measuring the both-rows arm's subtraction twice and
    the declared-elsewhere arm's not at all.
    """
    assert (
        guard.collision_problems(
            BASE, _ledger((352,)), {"#1588": _ledger((356,), ("L350 #1584",))}, "#1586"
        )
        == []
    )


def test_a_reservation_naming_a_DIFFERENT_number_is_not_mine():
    """The positive control for the test above: substring matching on digits
    must not make every holder look like every PR."""
    problems = guard.collision_problems(
        BASE, _ledger((352,)), {"#1588": _ledger((), ("L352 #1584",))}, "#1590"
    )
    assert len(problems) == 1, problems


# --- both arms, and the real tree ------------------------------------------


def test_every_sibling_is_examined_not_just_the_first():
    problems = guard.collision_problems(
        BASE,
        _ledger((352, 356)),
        {"#1584": _ledger((352,)), "#1588": _ledger((356,))},
        "#1586",
    )
    assert len(problems) == 2, problems
    assert any("#1584" in p for p in problems) and any("#1588" in p for p in problems)


def test_no_siblings_is_clean_rather_than_an_error():
    assert guard.collision_problems(BASE, _ledger((352,)), {}, "#1586") == []


def test_the_real_ledger_is_a_population_this_guard_can_read():
    """A guard that reports nothing because it parses nothing is not a guard.

    Anchors the regexes against the shipped ledger: if a future reformat breaks
    `_ROW`, every arm above still passes on fixtures while the real check goes
    permanently quiet.
    """
    text = LEDGER.read_text(encoding="utf-8")
    ids = guard.row_ids(text)
    assert len(ids) > 300, f"only {len(ids)} rows parsed from the real ledger"
    assert max(ids) >= 350, max(ids)
    # ...and the real ledger must not collide with itself.
    assert guard.collision_problems(text, text, {}, "#0") == []


def test_the_real_ledger_compared_against_itself_as_a_sibling_reports_nothing_new():
    """Self-comparison yields no NEW ids, so both arms must stay silent.

    The negative control for the whole module: a guard that fires here would fire
    on every PR.
    """
    text = LEDGER.read_text(encoding="utf-8")
    assert guard.collision_problems(text, text, {"#9999": text}, "#1") == []


# --- wiring: the guard must actually run, with the scope it needs -----------


def test_ci_invokes_this_guard():
    """An unreferenced script in `scripts/` is prose with a shebang."""
    body = CI_WORKFLOW.read_text(encoding="utf-8")
    assert "check-ledger-id-collisions.py" in body, (
        "722-ci.yml does not run scripts/check-ledger-id-collisions.py — a guard "
        "nothing invokes cannot fail"
    )


def test_the_job_running_it_declares_pull_requests_read():
    """It reads sibling PRs, so the scope must be declared, not assumed.

    Without it the script's fail-closed path fires on every PR, which is loud but
    useless -- and `check-workflow-pull-request-permission.py` exists to catch
    exactly this omission, so this test is the local half of a rule the repo
    already enforces globally.
    """
    body = CI_WORKFLOW.read_text(encoding="utf-8")
    head = body[: body.index("check-ledger-id-collisions.py")]
    job = head.rindex("\n  validate:")
    permissions = head[job:]
    assert re.search(r"^\s+pull-requests:\s*read\s*$", permissions, re.MULTILINE), (
        "the job that runs the ledger-id guard must declare `pull-requests: read`"
    )


def test_the_guard_fails_closed_rather_than_clean_when_it_cannot_look():
    """`main()` must return 2, not 0, when the comparison is impossible.

    Checked by pointing it at a path that does not exist, which is the one
    unreadable input reachable without the network. The exit code is asserted
    *and* the message, per CLAUDE.md's rule that a test asserting a failure code
    must also assert on output -- otherwise a guard that crashed for an unrelated
    reason passes this test.
    """
    import io
    import contextlib

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = guard.main(["check", str(_REPO_ROOT / "docs" / "no-such-ledger.md")])
    output = buffer.getvalue()
    assert code == 2, f"expected 2 (could not compare), got {code}: {output}"
    assert "not found" in output, output


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {str(e)[:500]}")
    sys.exit(1 if failures else 0)
