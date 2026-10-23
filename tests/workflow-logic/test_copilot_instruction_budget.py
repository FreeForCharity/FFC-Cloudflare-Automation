"""Unit tests for scripts/check-copilot-instruction-budget.py.

The guard exists because the Copilot instruction corpus is the one input to the
reviewer's prompt this repository controls, and nothing anywhere read how much
of the reviewer's window it already consumes (#1696). So the tests here are
almost entirely about the ways the guard could UNDER-report the corpus, because
that is the only failure direction that costs anything: an over-report is a
minute of argument, an under-report reinstates the silent erosion the guard was
written to stop.

Pinned here:
  * `CLAUDE.md` is in the candidate set -- the regression anchor for this whole
    file. Copilot's refusal body names four instruction files to shorten and
    omits `CLAUDE.md`, which is 61% of the corpus it refused; #1696's own
    acceptance criteria took their file list from that message. A guard built to
    it measures 102 KB of a 261 KB corpus and reports headroom that is not
    there. The measured set comes from the reviewer's job log
    (`names=["AGENTS.md","CLAUDE.md"]`), and the test asserts that, not the body.
  * every file the refusal body DOES name is a candidate too, present or not, so
    adding one later is not a silent budget increase;
  * the `.github/instructions/**` glob is recursive, so a nested instruction
    file cannot hide from the total;
  * sizes are read as bytes, asserted with CRLF content -- text mode would
    silently drop one byte per line on a CRLF checkout, which is the exact
    failure CLAUDE.md records twice;
  * an unreadable candidate is an error, never 0 bytes;
  * the share is computed from the injected budget rather than a hardcoded
    number, asserted by driving a different budget and requiring the answer to
    follow it;
  * the verdict boundaries are `>=` on both thresholds, checked one unit either
    side, so an off-by-one in either direction is caught;
  * the exit code is non-zero ONLY over the hard threshold -- a guard that
    exited 1 on today's tree would be reverted within a day, and one that
    exited 0 unconditionally would never be noticed;
  * the report prints the observed-refusal note, so a verdict of `OK` cannot be
    read as "review works" -- refusals are already happening at today's share;
  * `MAX_PROMPT_TOKENS` still matches the provenance recorded in the docstring,
    so the budget cannot be raised without the evidence being updated with it;
  * and the live tree is within the hard threshold. That last test is how this
    reaches CI at all: `run_all.py` picks the module up by name, with no
    `.github/workflows/` edit -- a reserved lane this work must not enter.

No network, no subprocess.

Run: python3 tests/workflow-logic/test_copilot_instruction_budget.py
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
SCRIPT = ROOT / "scripts" / "check-copilot-instruction-budget.py"


def load_module():
    spec = importlib.util.spec_from_file_location("check_copilot_instruction_budget", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = load_module()

_CURRENT: list[str] = []


def check(name, cond, detail=""):
    if not cond:
        _CURRENT.append("%s%s" % (name, " -- " + detail if detail else ""))


def fixture(files: dict) -> pathlib.Path:
    """Write a throwaway checkout. Values are bytes, written verbatim."""
    tmp = pathlib.Path(tempfile.mkdtemp())
    for rel, blob in files.items():
        path = tmp / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob)
    return tmp


def test_claude_md_is_a_candidate_though_the_refusal_body_omits_it():
    check("CLAUDE.md in CANDIDATE_PATHS", "CLAUDE.md" in M.CANDIDATE_PATHS,
          "the refusal body's file list is not the loaded set; the job log is")
    root = fixture({"CLAUDE.md": b"x" * 500, "AGENTS.md": b"y" * 100})
    report = M.measure(root)
    check("counted", report["total_bytes"] == 600, str(report["total_bytes"]))
    claude = [f for f in report["files"] if f["path"] == "CLAUDE.md"][0]
    check("share of corpus reported", abs(claude["share_of_corpus"] - 500 / 600) < 1e-9,
          str(claude["share_of_corpus"]))


def test_every_file_the_refusal_body_names_is_also_a_candidate():
    for named in ("AGENTS.md", "REVIEW.md", ".github/copilot-instructions.md"):
        check("candidate %s" % named, named in M.CANDIDATE_PATHS)
    check("instructions glob present",
          any("instructions" in g for g in M.CANDIDATE_GLOBS), str(M.CANDIDATE_GLOBS))


def test_an_absent_candidate_is_reported_absent_and_contributes_zero():
    report = M.measure(fixture({"AGENTS.md": b"z" * 40}))
    absent = [f for f in report["files"] if f["path"] == "REVIEW.md"][0]
    check("present False", absent["present"] is False, str(absent))
    check("bytes None", absent["bytes"] is None, str(absent))
    check("total unaffected", report["total_bytes"] == 40, str(report["total_bytes"]))


def test_a_file_added_at_a_candidate_path_is_counted():
    plain = M.measure(fixture({"AGENTS.md": b"a" * 100}))
    with_new = M.measure(fixture({
        "AGENTS.md": b"a" * 100,
        "REVIEW.md": b"b" * 70,
        ".github/copilot-instructions.md": b"c" * 30,
    }))
    check("growth counted", with_new["total_bytes"] == plain["total_bytes"] + 100,
          "%d vs %d" % (with_new["total_bytes"], plain["total_bytes"]))


def test_a_nested_instructions_file_is_counted_recursively():
    report = M.measure(fixture({
        "AGENTS.md": b"a" * 10,
        ".github/instructions/deep/nested/go.instructions.md": b"g" * 25,
    }))
    paths = [f["path"] for f in report["files"]]
    check("nested path discovered",
          ".github/instructions/deep/nested/go.instructions.md" in paths, str(paths))
    check("nested bytes counted", report["total_bytes"] == 35, str(report["total_bytes"]))


def test_sizes_are_read_as_bytes_not_text():
    # Three CRLF line endings: text mode would report 30 bytes, not 33.
    blob = b"line one" + b"\r\n" + b"line two" + b"\r\n" + b"line three" + b"\r\n"
    report = M.measure(fixture({"AGENTS.md": blob}))
    check("CR bytes counted", report["total_bytes"] == len(blob),
          "%d, expected %d" % (report["total_bytes"], len(blob)))


def test_an_unreadable_candidate_is_an_error_not_zero_bytes():
    root = fixture({"AGENTS.md": b"a" * 10, "CLAUDE.md": b"c" * 10})
    real = pathlib.Path.read_bytes

    def refuse(self):
        if self.name == "CLAUDE.md":
            raise OSError("simulated unreadable file")
        return real(self)

    pathlib.Path.read_bytes = refuse
    try:
        try:
            report = M.measure(root)
        except M.CorpusError:
            return
        except Exception as exc:  # noqa: BLE001 -- any other type is a finding
            check("raises CorpusError", False, "%s: %s" % (type(exc).__name__, exc))
            return
        check("refused rather than under-reported", False,
              "returned total_bytes=%d" % report["total_bytes"])
    finally:
        pathlib.Path.read_bytes = real


def test_the_share_follows_the_injected_budget_not_a_hardcoded_number():
    root = fixture({"AGENTS.md": b"a" * 4000})
    wide = M.measure(root, budget_tokens=10000)
    narrow = M.measure(root, budget_tokens=2000)
    check("1000 tokens of 10000", abs(wide["share_of_budget"] - 0.10) < 1e-9,
          str(wide["share_of_budget"]))
    check("1000 tokens of 2000", abs(narrow["share_of_budget"] - 0.50) < 1e-9,
          str(narrow["share_of_budget"]))
    check("budget echoed", narrow["budget_tokens"] == 2000, str(narrow["budget_tokens"]))


def test_the_verdict_boundaries_are_inclusive_on_both_thresholds():
    eps = 1e-6
    check("below advisory is OK", M.verdict(M.ADVISORY_SHARE - eps) == "OK")
    check("at advisory is WARN", M.verdict(M.ADVISORY_SHARE) == "WARN")
    check("below hard is WARN", M.verdict(M.HARD_SHARE - eps) == "WARN")
    check("at hard is OVER", M.verdict(M.HARD_SHARE) == "OVER")
    check("above hard is OVER", M.verdict(0.99) == "OVER")
    check("thresholds ordered", M.ADVISORY_SHARE < M.HARD_SHARE)


def test_the_exit_code_is_non_zero_only_over_the_hard_threshold():
    under = fixture({"AGENTS.md": b"a" * 1000})
    # Bytes needed to reach the hard share, given the recorded budget.
    over_bytes = int(M.HARD_SHARE * M.MAX_PROMPT_TOKENS * M.BYTES_PER_TOKEN) + M.BYTES_PER_TOKEN
    over = fixture({"AGENTS.md": b"a" * over_bytes})
    # The report goes to stdout; swallow it so the roster `run_all.py` parses
    # stays one line per test.
    with contextlib.redirect_stdout(io.StringIO()):
        under_rc = M.main(["--root", str(under)])
        over_rc = M.main(["--root", str(over)])
    check("under exits 0", under_rc == 0)
    check("over exits 1", over_rc == 1)


def test_the_report_names_the_observed_refusal_so_ok_is_not_read_as_working():
    report = M.measure(fixture({"AGENTS.md": b"a" * 10}))
    check("verdict is OK on a tiny corpus", report["verdict"] == "OK", report["verdict"])
    text = M.render(report)
    check("note rendered", "NOTE:" in text, text[-200:])
    check("names the refused PR", "#1596" in text, text[-200:])
    check("names the tracking issue", "#1696" in text, text[-200:])


def test_json_output_carries_per_file_bytes_and_both_thresholds():
    report = M.measure(fixture({"AGENTS.md": b"a" * 80, "CLAUDE.md": b"c" * 120}))
    blob = json.loads(json.dumps(report))
    for key in ("files", "total_bytes", "estimated_tokens", "budget_tokens",
                "share_of_budget", "advisory_share", "hard_share", "verdict"):
        check("json key %s" % key, key in blob, str(sorted(blob)))
    sizes = {f["path"]: f["bytes"] for f in blob["files"]}
    check("per-file bytes", sizes.get("AGENTS.md") == 80 and sizes.get("CLAUDE.md") == 120,
          str(sizes))


def test_the_budget_constant_still_matches_its_recorded_provenance():
    doc = M.__doc__ or ""
    check("job log value quoted in the docstring",
          "MaxPromptTokens=%d" % M.MAX_PROMPT_TOKENS in doc,
          "raise the constant and the evidence for it must move with it")
    check("the loaded set is cited from the log, not the refusal body",
          'names=["AGENTS.md","CLAUDE.md"]' in doc, "docstring lost its provenance")


def test_the_live_tree_is_within_the_recorded_hard_threshold():
    report = M.measure(ROOT)
    present = {f["path"] for f in report["files"] if f["present"]}
    check("AGENTS.md measured in the live tree", "AGENTS.md" in present, str(present))
    check("CLAUDE.md measured in the live tree", "CLAUDE.md" in present, str(present))
    check(
        "corpus within the hard threshold",
        report["verdict"] != "OVER",
        "corpus is %d B = %.1f%% of the %d-token budget (hard %.0f%%); "
        "trimming it is #1696 AC1, a policy change reserved to @clarkemoyer"
        % (report["total_bytes"], 100 * report["share_of_budget"],
           report["budget_tokens"], 100 * M.HARD_SHARE),
    )


def main():
    print("copilot instruction budget guard (scripts/check-copilot-instruction-budget.py):")
    failed = 0
    for name, fn in sorted(globals().items()):
        if not (name.startswith("test_") and callable(fn)):
            continue
        del _CURRENT[:]
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 -- reported, never swallowed
            _CURRENT.append("%s: %s" % (type(exc).__name__, exc))
        if _CURRENT:
            failed += 1
            print("  FAIL %s: %s" % (name, "; ".join(_CURRENT)))
        else:
            print("  PASS %s" % name)
    print("\n%d failed" % failed if failed else "\nall passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
