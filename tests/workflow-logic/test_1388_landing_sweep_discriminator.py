#!/usr/bin/env python3
"""Tests for `AGENTS.md` § "The landing sweep, and when it is finished" (#1388).

The section tells a worker that finds every open PR *complete* how to name the
cause and how loudly to escalate. Before #1388 it could not distinguish "no
human with promotion rights is present" from "a human is present, merging a
different lane", so fifteen consecutive runs reported the first while the second
was true -- and the escalation that should have grown more urgent every run grew
more routine instead, because its text was identical each time.

Why prose gets a test module at all, and why it is this shape:

* The defect being fixed is **documentation that stopped describing reality**.
  The remedy is more documentation, so without a guard the fix has exactly the
  failure mode of the thing it fixes. `test_conductor_clock.py` sets the
  precedent (its `test_agents_md_tells_the_conductor_to_run_this`).
* Every assertion is asked of the **section**, sliced between its own heading and
  the next `##`, never of the whole file. A whole-body `in` check is satisfied by
  any other copy of the phrase in an 850-line document -- that is the vacuous
  assertion #1386 shipped and then had to fix, and `test_the_slice_excludes_its
  _neighbours` is the control that keeps this module honest about it.
* **Both slice boundaries are asserted present, with their occurrence counts,
  before the slice is taken.** A renamed heading must fail loudly here rather
  than silently reduce every later assertion to a test against an empty string.
* AC5's command is not merely matched -- it is **extracted from `AGENTS.md` and
  run** against a fixture repository with one conflicting and one clean pair. A
  documented command that does not discriminate is worth less than no command,
  and retyping it here would test this file instead of the doc (the #1341
  `a84031e` technique).

Nothing here touches the network, and the fixture repository lives in a
`TemporaryDirectory` -- never the checkout, so no run can leave a stray file in
the working tree (#1023).
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
AGENTS = REPO_ROOT / "AGENTS.md"

# The slice boundaries. Spelled as the shortest prefix that is unique in the
# file, so prettier reflowing the heading's trailing issue references does not
# break the slice.
SECTION_HEADING = "### The landing sweep, and when it is finished"
NEXT_HEADING = "## GitHub API rate budget"

# A sentence that lives OUTSIDE the section, immediately after it. The control
# test asserts the slice does not reach it -- which is what makes every other
# assertion in this module a statement about the section rather than the file.
NEIGHBOUR_SENTINEL = "shares **one REST core budget"


def _agents_text() -> str:
    return AGENTS.read_text(encoding="utf-8")


def _section_flat() -> str:
    """The section with every run of whitespace collapsed to one space.

    Two properties, both learned from the mutation pass on this very module:

    * **It survives a prettier reflow.** The assertions below match whole
      requirement clauses rather than bare phrases, and prettier wraps
      `AGENTS.md` at 100 columns, so a literal that reads as one sentence in the
      source is split across lines in the file. Matching against the flattened
      view is what lets a test assert the sentence instead of a fragment.
    * **It is what makes those long literals affordable.** Three mutations
      originally SURVIVED this module because each assertion was a short phrase
      (`"identical"`, `"consecutive runs"`, `"end state"`) that a *different,
      unrelated sentence in the same section* already satisfied -- `"identical"`
      by the word "identically" two paragraphs up, for instance. Slicing to the
      section was necessary and not sufficient: within one section a phrase can
      still be answered by the wrong sentence.
    """
    return re.sub(r"\s+", " ", _section())


def _section() -> str:
    """Return the landing-sweep section, or fail naming the missing boundary.

    Deliberately raises `AssertionError` rather than returning `""`: an empty
    string would let every caller pass by having nothing to contradict them,
    which is the failure this module exists to avoid in the document it guards.
    """
    text = _agents_text()

    start_count = text.count(SECTION_HEADING)
    assert start_count == 1, (
        f"expected exactly 1 occurrence of {SECTION_HEADING!r} in AGENTS.md, found "
        f"{start_count} -- the section was renamed or duplicated, so this module's "
        "slice no longer identifies it"
    )
    end_count = text.count(NEXT_HEADING)
    assert end_count == 1, (
        f"expected exactly 1 occurrence of the following heading {NEXT_HEADING!r} in "
        f"AGENTS.md, found {end_count} -- the slice has no unambiguous end"
    )

    start = text.index(SECTION_HEADING)
    end = text.index(NEXT_HEADING, start)
    assert end > start, "the following heading precedes the section heading"
    return text[start:end]


# --------------------------------------------------------------------------
# The control. If this test ever fails, every other assertion here is suspect.
# --------------------------------------------------------------------------


def test_the_slice_excludes_its_neighbours():
    """The slice must be the section, not the document.

    This is the discriminator for the whole module: it proves the assertions
    below could actually fail. Without it, a slice accidentally spanning the
    rest of the file would make every `in` check below pass on unrelated prose.
    """
    section = _section()
    assert NEIGHBOUR_SENTINEL not in section, (
        "the slice reached past the section into the rate-budget section, so every "
        "other assertion in this module is being asked of the wrong text"
    )
    # ...and it must not be empty or trivially short, which is the other way a
    # slice can satisfy a `not in` check for the wrong reason.
    assert len(section) > 2000, f"section is only {len(section)} chars -- suspiciously short"
    assert SECTION_HEADING in section


# --------------------------------------------------------------------------
# AC1 -- the discriminator
# --------------------------------------------------------------------------


def test_ac1_the_section_requires_a_merge_activity_check():
    section = _section()
    assert "state=closed" in section, (
        "the section does not carry the merge-activity query; a worker has no way to "
        "tell an absent human from queue starvation"
    )
    assert "merged_at" in section


def test_ac1_the_two_causes_are_named_differently():
    """Naming one cause is what produced fifteen identical notes."""
    section = _section()
    lowered = section.lower()
    assert "queue starvation" in lowered, "the starvation cause is not named"
    # The absence case must survive as its own named reading, not be replaced by
    # the new one -- a fix that only ever reports starvation is the same defect
    # mirrored.
    assert "promotion rights is present" in lowered or "absence" in lowered, (
        "the absent-human reading is no longer named as a distinct cause"
    )


def test_ac1_the_query_filters_on_merged_at_rather_than_on_closed():
    """`state=closed` includes closed-unmerged PRs; counting one as a merge
    turns starvation back into absence -- wrong, in the reassuring direction."""
    section = _section()
    assert "select(.merged_at)" in section, (
        "the documented query does not filter on `merged_at`, so a closed-unmerged "
        "PR would be counted as a merge"
    )
    # And the reason must be stated, not just the code: an unexplained filter is
    # the first thing a later edit 'simplifies' away.
    assert "load-bearing" in section


def test_ac1_the_query_windows_on_update_time_rather_than_creation_time():
    """The window must be the newest 20 by *update*, not by creation.

    `sort` defaults to `created`, so the default window is the newest 20 PRs by
    creation -- and the stalled PRs are by definition the oldest-created ones, so
    the moment one merges it is exactly the merge that window drops. Measured on
    this repo when the finding landed: the newest 20 closed-by-creation reached
    back only to 2026-09-22 while #1341 was created 09-19.
    """
    section = _section()
    assert "sort=updated" in section, (
        "the documented query uses the default `created` sort, so its window is the "
        "newest PRs by creation rather than the newest merges"
    )


def test_ac1_the_query_pins_the_sort_direction():
    """`sort=updated` WITHOUT `direction=desc` is strictly worse than no sort.

    `direction` defaults to `desc` only while `sort` is `created` or absent; once
    a `sort` is named it defaults to `asc`. Measured: `state=closed&sort=updated`
    with no direction returns PRs #1, #3 and #5, merged in November 2025 -- zero
    recent merges off a ten-month-old page, i.e. the absent-human reading. So the
    two parameters are a pair, and a half-applied fix is a regression.
    """
    section = _section()
    assert "direction=desc" in section, (
        "the documented query names a `sort` without pinning `direction`, which "
        "defaults to `asc` and returns the OLDEST page"
    )
    # The trap must be written down, not just avoided: the next editor adding a
    # sort elsewhere needs to know why the direction is there.
    flat = _section_flat().lower()
    assert "defaults to `asc`" in flat, (
        "the section does not record that naming a sort flips the direction default "
        "to asc -- the reason `direction=desc` is not optional"
    )


def test_ac1_the_mcp_spelling_carries_the_same_two_parameters():
    """The worker's client has the identical `asc` default, measured through it.

    A `gh` example that is correct beside an MCP spelling that is not would send
    the one agent that hits this state every run down the broken path.
    """
    flat = _section_flat()
    assert "`direction: desc`" in flat, (
        "the MCP spelling omits `direction: desc`, so the sandboxed worker -- the "
        "only regular reader of this section -- gets the ascending page"
    )
    assert "`sort: updated`" in flat, "the MCP spelling omits `sort: updated`"


def test_ac1_is_executable_by_the_agent_class_that_reads_it():
    """The sandboxed worker has no `gh` CLI (#1360), so a `gh`-only instruction
    is unrunnable by the one reader that hits this state every run."""
    section = _section()
    assert "mcp__github__list_pull_requests" in section, (
        "the section prescribes only a `gh` call; the sandboxed worker cannot run it"
    )
    assert "#1360" in section, "the section does not cite the no-gh-CLI issue"


# --------------------------------------------------------------------------
# AC2 / AC3 -- persistence, and a note that cannot be identical
# --------------------------------------------------------------------------


def test_ac2_escalation_strength_is_tied_to_persistence():
    section = _section()
    lowered = section.lower()
    # The REQUIREMENT, not the phrase. `"consecutive runs"` alone is satisfied by
    # the worked example's "fifteen consecutive runs filed a two-week stall" two
    # paragraphs down -- that mutation survived until this assertion was tightened.
    assert "how many consecutive runs" in _section_flat().lower(), (
        "the section does not require the consecutive-run count, so a two-week stall "
        "reads like one that started this morning"
    )
    assert "oldest complete pr" in lowered, "the age of the oldest complete PR is not required"


def test_ac2_records_that_the_cap_can_only_rise():
    """The monotonicity is the reason persistence matters: no worker run can
    lower the cap, so 'stable' does not mean 'fixed'."""
    section = _section()
    assert "monotonic" in section.lower(), (
        "the section does not say the cap is monotonic, which is why each run in the "
        "terminal state makes the next one worse"
    )


def test_ac3_forbids_a_note_identical_to_the_last_one():
    # `"identical"` alone survived a mutation that deleted the prohibition
    # outright: the word is already present up in the AC1 paragraph, as
    # "renders two different situations identically". Assert the prohibition.
    flat = _section_flat().lower()
    assert "never post a note byte-identical to the previous one" in flat, (
        "the section does not forbid re-posting the same note, which is what let "
        "fifteen of them pass unread"
    )
    # The prohibition is only actionable if the section names figures that change
    # even when the verdict does not.
    assert "merge-activity reading" in flat


# --------------------------------------------------------------------------
# AC4 -- a complete set is a failure, not a clean result
# --------------------------------------------------------------------------


def test_ac4_a_complete_pr_set_is_named_a_failure():
    section = _section()
    assert "reportable failure of the system" in section, (
        "the section still frames a fully-complete PR set as a benign state"
    )
    assert "not a clean result" in section


def test_ac4_keeps_the_do_not_re_verify_rule():
    """AC4 must not be implemented by deleting the rule it reframes: re-measuring
    an unchanged tree really is waste, and a fix that restores that busywork
    would trade one no-op run for another."""
    section = _section()
    lowered = section.lower()
    assert "do not re-verify" in lowered, "the do-not-re-verify rule was dropped"
    assert "reads like" in lowered and "progress" in lowered


# --------------------------------------------------------------------------
# AC5 -- the cohort is not the sum of its PRs
# --------------------------------------------------------------------------


def test_ac5_requires_a_pairwise_check_before_a_punch_list():
    section = _section()
    assert "git merge-tree --write-tree" in section, (
        "the section does not prescribe the pairwise composition check, so a punch "
        "list can claim N independent enqueues for a cohort that conflicts"
    )
    lowered = section.lower()
    assert "pairwise" in lowered
    assert "against `main`" in section, (
        "the section does not say WHY per-PR cleanliness misses this -- each clause "
        "is measured against main, independently per PR"
    )


def test_ac5_the_command_does_not_discard_the_evidence_for_its_own_verdict():
    """`rc 1` is what a mistyped ref returns too, measured identical to a real
    conflict. The first draft of the bullet ended `>/dev/null`, which made the
    two indistinguishable -- and in the direction that invents a coupling and
    makes an enqueuer serialize a cohort that composes fine."""
    section = _section()
    command = _extract_documented_merge_tree_command(section)
    assert ">/dev/null" not in command and "> /dev/null" not in command, (
        "the documented command discards stdout, so a mistyped ref is "
        f"indistinguishable from a real conflict: {command!r}"
    )
    # ...and the ambiguity must be stated, because keeping stdout only helps a
    # reader who knows what to look for in it.
    assert "not something we can merge" in section, (
        "the section does not warn that a bad ref also exits 1"
    )


def test_ac5_requires_the_resolution_end_state_not_just_the_conflict():
    """'These two conflict' is not actionable. The red lands on a resolution that
    applies cleanly and still fails CI."""
    section = _section()
    # `"end state"` alone survived a mutation that deleted the requirement: the
    # worked example's "The correct end state is the same whichever merges second"
    # satisfied it. Assert the instruction, not the noun phrase.
    assert "for each coupling give the end state" in _section_flat().lower(), (
        "the section does not REQUIRE the resolution's end state per coupling -- "
        "'these two conflict' is not actionable"
    )
    assert "reserved-ids" in section, "the known both-directions case is not named"
    assert "#1278" in section, "the concurrent-ledger-id issue is not cited"


def _extract_documented_merge_tree_command(section: str) -> str:
    """Pull the AC5 command out of `AGENTS.md` rather than retyping it here.

    Retyping would test this file. Extracting means a doc edit that breaks the
    command breaks this test -- which is the entire point of the module.
    """
    m = re.search(r"^(git merge-tree --write-tree[^\n]*)$", section, re.M)
    assert m, "no `git merge-tree --write-tree` line found in the section"
    command = m.group(1)
    # Drop the trailing explanatory comment; the shell would too, but slicing it
    # off keeps the failure message readable.
    command = command.split("#", 1)[0].strip()
    assert command, "the extracted command is empty once its comment is removed"
    return command


def _build_fixture_repo(root: pathlib.Path, env: dict) -> None:
    """Three branches off one base: `left` and `right` edit the same line (so
    they conflict), `other` adds an unrelated file (so it does not)."""

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    git("init", "-q", "-b", "main", ".")
    (root / "f.txt").write_text("a\nb\nc\n", encoding="utf-8", newline="\n")
    git("add", "f.txt")
    git("commit", "-qm", "base")

    git("checkout", "-q", "-b", "left")
    (root / "f.txt").write_text("a\nb\nc\nLEFT\n", encoding="utf-8", newline="\n")
    git("commit", "-qam", "left")

    git("checkout", "-q", "main")
    git("checkout", "-q", "-b", "right")
    (root / "f.txt").write_text("a\nb\nc\nRIGHT\n", encoding="utf-8", newline="\n")
    git("commit", "-qam", "right")

    git("checkout", "-q", "main")
    git("checkout", "-q", "-b", "other")
    (root / "g.txt").write_text("unrelated\n", encoding="utf-8", newline="\n")
    git("add", "g.txt")
    git("commit", "-qm", "other")

    git("checkout", "-q", "main")


def _run_documented_command(command: str, root: pathlib.Path, a: str, b: str, env: dict):
    """Substitute the two branch placeholders and run the doc's own command."""
    for placeholder in ("origin/<branch-a>", "origin/<branch-b>"):
        assert placeholder in command, (
            f"the documented command no longer contains {placeholder!r}; it reads "
            f"{command!r}. Update this test's substitution deliberately rather than "
            "loosening it -- an unsubstituted placeholder would make git resolve a "
            "ref that does not exist and fail for the wrong reason."
        )
    runnable = command.replace("origin/<branch-a>", a).replace("origin/<branch-b>", b)
    return subprocess.run(
        ["bash", "-c", runnable],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_ac5_the_documented_command_actually_discriminates():
    """Run the doc's command on a conflicting pair and a clean one.

    This is the only test here that can fail because the *command* is wrong
    rather than because a phrase is missing. It asserts both polarities, because
    a command that always reports a conflict would pass a conflict-only test and
    freeze every enqueue; and it asserts on the conflict case's **output**, not
    only its exit code, because `rc != 0` is equally satisfied by a git that
    could not start at all (CLAUDE.md's non-zero-exit rule).
    """
    section = _section()
    command = _extract_documented_merge_tree_command(section)

    # Inherit the environment rather than building a minimal one: a scrubbed
    # `env=` is what made 26 modules abort on the Windows host (CLAUDE.md, #943).
    env = dict(os.environ)
    env.update(
        {
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.invalid",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
        }
    )

    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td) / "fixture"
        root.mkdir()
        _build_fixture_repo(root, env)

        conflicting = _run_documented_command(command, root, "left", "right", env)
        clean = _run_documented_command(command, root, "left", "other", env)

    assert conflicting.returncode == 1, (
        "the documented command did not report a conflict for two branches that "
        f"edit the same line: rc={conflicting.returncode}\n"
        f"stdout={conflicting.stdout[:600]}\nstderr={conflicting.stderr[:600]}"
    )
    # The exit code alone cannot tell a detected conflict from a git that failed
    # to run, so require the conflicted path to be named somewhere it printed.
    combined = conflicting.stdout + conflicting.stderr
    assert "f.txt" in combined, (
        "rc was 1 but the conflicted path was never named, so this may be a harness "
        f"failure rather than a detection.\nstdout={conflicting.stdout[:600]}\n"
        f"stderr={conflicting.stderr[:600]}"
    )

    assert clean.returncode == 0, (
        "the documented command reported a conflict for two branches that touch "
        f"different files: rc={clean.returncode}\nstdout={clean.stdout[:600]}\n"
        f"stderr={clean.stderr[:600]}"
    )


# --------------------------------------------------------------------------
# The measured instance. Not decoration: the figures are what make the
# escalation credible, and a later edit that drops them leaves the section
# arguing from a hypothetical.
# --------------------------------------------------------------------------


def test_the_worked_example_names_the_cohort_and_the_lane_it_lost_to():
    section = _section()
    for pr in ("#1341", "#1346", "#1347", "#1361", "#1386"):
        assert pr in section, f"the worked example does not name {pr}"
    assert "#1388" in section, "the section does not cite the issue this came from"
    assert "L215" in section, (
        "the section does not connect this to L215 -- a premise going stale while "
        "every fact quoted for it stays true"
    )


def run_module_tests(tests):
    """Run every test, report each, and return the failure count.

    `SystemExit` is not an `Exception`, so `except Exception` would let one test
    end the module and leave the rest unnamed -- the truncated-roster shape
    (L194)."""
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
