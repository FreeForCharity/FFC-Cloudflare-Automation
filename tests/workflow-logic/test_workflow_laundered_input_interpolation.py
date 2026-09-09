"""Tests for the laundered dispatch-input guard (#1233).

The subject is `scripts/check-workflow-laundered-input-interpolation.py`. What
it claims: a free-text `workflow_dispatch` input never reaches a `run:` or
`github-script` body through a step or job OUTPUT either, except for the hops
frozen in `KNOWN_LAUNDERED` — and that freeze is exact in both directions.

The class it covers is the one that fails in the FLATTERING direction. Moving an
input into step-level `env:` is the remedy the sibling guard prescribes, and it
is also what deletes the `inputs.X` reference the sibling is defined over. So a
lane can fix every call site the sibling names, delete its freeze entry, go
green on every check in the repo, and leave a working injection under a
production credential. Four of the five workflows this guard reports are absent
from the sibling's freeze today.

Designed against the two failure modes this repo keeps hitting:

* **Vacuous green.** A sweep whose input set silently empties passes by
  inspecting nothing. `test_the_scan_sees_real_workflows` and
  `test_the_taint_engine_finds_real_taint` pin the input, and the mutation
  tests assert a CONTROL exits 0 immediately before each mutation (ledger L209)
  so a red is attributable to the mutation and not to the tree.
* **A guard that cannot fail.** Reading the checker proves it is wired, never
  that it detects (AGENTS.md). Every claim below that says "X makes it exit 1"
  is exercised by actually doing X — including reintroducing 102's real hop,
  which is what #1233 requires.

Mutations are applied to a COPY in a temp directory, never to the tree, so
restore fidelity is never in question (ledger L182, and the CRLF half of the
same trap on the Conductor's host).
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import tempfile

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import REPO_ROOT  # noqa: E402

CHECKER = REPO_ROOT / "scripts" / "check-workflow-laundered-input-interpolation.py"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"


def _load():
    spec = importlib.util.spec_from_file_location("check_wf_laundered", CHECKER)
    # Same reasoning as the sibling module's loader: `spec is None` means the
    # path is not an importable Python source file at all (renamed, no
    # extension, a directory), and `module_from_spec(None)` would raise an
    # AttributeError naming neither the path nor the cause — at import time, so
    # run_all.py would report a module that declared tests and produced no
    # outcomes, and the reader would hunt a broken test instead of a moved file.
    assert spec is not None and spec.loader is not None, (
        f"cannot import the checker at {CHECKER} — the path is not an importable "
        "Python source file (wrong extension, or a directory). If it was renamed, "
        "update CHECKER above."
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = _load()


def _write(directory: pathlib.Path, name: str, document: str) -> pathlib.Path:
    path = directory / name
    path.write_text(document, encoding="utf-8", newline="\n")
    return path


# A minimal workflow carrying the full laundering shape: an input arrives the
# REMEDIED way (step-level `env:`), is written to GITHUB_OUTPUT, and is read
# back into a script body one job later. Everything below is a variation on it,
# so the differences carry the meaning.
LAUNDERING = """\
name: sample
on:
  workflow_dispatch:
    inputs:
      domain:
        type: string
jobs:
  resolve:
    runs-on: ubuntu-latest
    environment: cloudflare-prod-write
    outputs:
      domain: ${{ steps.meta.outputs.domain }}
    steps:
      - name: Metadata
        id: meta
        env:
          IN_DOMAIN: ${{ inputs.domain }}
        run: |
          d=$(echo "$IN_DOMAIN" | tr 'A-Z' 'a-z')
          echo "domain=$d" >> "$GITHUB_OUTPUT"
  use:
    runs-on: ubuntu-latest
    needs: resolve
    steps:
      - name: Use it
        run: |
          domain="${{ needs.resolve.outputs.domain }}"
          echo "$domain"
"""


def _scan(document: str, name: str = "999-sample.yml"):
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(pathlib.Path(tmp), name, document)
        return guard.scan_all([path])


# --- the input set is real -------------------------------------------------


def test_the_scan_sees_real_workflows():
    """A sweep whose input set empties passes by inspecting nothing."""
    _hops, unreadable, scanned = guard.scan_all()
    assert scanned > 50, f"expected the real workflow directory, scanned {scanned}"
    assert unreadable == [], f"the tree should parse, got {unreadable}"


def test_the_taint_engine_finds_real_taint():
    """The positive control: the tree really does contain laundering hops.

    Without this, every assertion below is compatible with an engine that
    computes an empty taint set for everything.
    """
    hops, _unreadable, _scanned = guard.scan_all()
    assert hops, "the tree carries laundering hops; finding none means the engine died"
    workflows = guard.current_map(hops)
    # Derived from the freeze rather than pinned to one workflow. It used to
    # name 706 — the canonical #1233 shape — and that made this control a file
    # every burn-down lane had to edit, which is the coupling #1210 objects to
    # in #1080's own freeze. Derived, a lane touches only its own entries.
    #
    # When the burn-down finishes, `KNOWN_LAUNDERED` is empty, the real tree
    # carries no hop, and `assert hops` above fails LOUDLY. That is the intended
    # end state, not a regression: retire this control then and let the
    # synthetic `LAUNDERING` sample carry the engine's positive evidence — do
    # not weaken the assertion to make an empty tree pass.
    assert set(workflows) == set(guard.KNOWN_LAUNDERED), (
        "the engine must find exactly the frozen workflows; got "
        f"{sorted(workflows)} against {sorted(guard.KNOWN_LAUNDERED)}"
    )


def test_the_tree_is_exactly_the_freeze():
    """The shipped verdict on the real tree: exit 0, nothing new, nothing stale."""
    hops, unreadable, _scanned = guard.scan_all()
    assert unreadable == []
    new, stale = guard.compare(guard.current_map(hops))
    assert new == [], f"unfrozen laundering hops: {new}"
    assert stale == [], f"stale freeze entries: {stale}"


def test_every_frozen_entry_carries_a_reason():
    """A freeze whose entries cannot be told apart stops being read.

    The analysis is pessimistic on purpose, so the list mixes live injections
    with `$RUNNER_TEMP` constants that are listed only because the step writing
    them also holds the input. A reason per entry is what keeps those
    distinguishable — that is why this freeze is a dict and not the sibling's
    tuple.
    """
    for workflow, entries in guard.KNOWN_LAUNDERED.items():
        assert isinstance(entries, dict), f"{workflow}: freeze entries need reasons"
        for reference, reason in entries.items():
            assert len(reason) > 30, (
                f"{workflow}: {reference} has no usable reason ({reason!r})"
            )


def test_the_freeze_names_only_workflows_that_exist():
    """A freeze entry for a deleted file is a claim about nothing."""
    for workflow in guard.KNOWN_LAUNDERED:
        assert (WORKFLOWS / workflow).exists(), f"{workflow} is not in the tree"


# --- detection -------------------------------------------------------------


def test_the_env_remedied_hop_is_detected():
    """#1233's exact shape: `env:` in, GITHUB_OUTPUT, `${{ }}` out."""
    hops, unreadable, _ = _scan(LAUNDERING)
    assert unreadable == []
    assert len(hops) == 1, f"expected one hop, got {[str(h) for h in hops]}"
    assert hops[0].reference == "needs.resolve.outputs.domain"
    assert hops[0].job == "use"


def test_the_sibling_guard_reads_the_same_document_as_clean():
    """The whole premise, asserted rather than argued.

    If the sibling ever grows to cover this, this test fails and the two guards
    should be reconciled — deliberately, not by discovering an overlap later.
    """
    sibling = guard.sibling
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(pathlib.Path(tmp), "999-sample.yml", LAUNDERING)
        findings, unreadable, _ = sibling.scan_all([path])
    assert unreadable == []
    assert findings == [], (
        "the sibling guard is defined over `inputs.X` and must read the remedied "
        f"document as clean; it reported {[str(f) for f in findings]}"
    )


def test_a_step_output_hop_inside_one_job_is_detected():
    """Not every hop crosses a job boundary."""
    document = LAUNDERING.replace(
        """  use:
    runs-on: ubuntu-latest
    needs: resolve
    steps:
      - name: Use it
        run: |
          domain="${{ needs.resolve.outputs.domain }}"
          echo "$domain"
""",
        "",
    ).replace(
        """          echo "domain=$d" >> "$GITHUB_OUTPUT"
""",
        """          echo "domain=$d" >> "$GITHUB_OUTPUT"
      - name: Use it
        run: |
          domain="${{ steps.meta.outputs.domain }}"
          echo "$domain"
""",
    )
    hops, unreadable, _ = _scan(document)
    assert unreadable == []
    assert [h.reference for h in hops] == ["steps.meta.outputs.domain"], (
        f"expected the same-job step-output hop, got {[str(h) for h in hops]}"
    )


def test_a_github_env_hop_read_back_as_an_expression_is_detected():
    """`env.X` is substituted text; `$X` is not. Only the first is a hop."""
    document = LAUNDERING.replace(
        """          echo "domain=$d" >> "$GITHUB_OUTPUT"
""",
        """          echo "DOMAIN=$d" >> "$GITHUB_ENV"
      - name: Use it
        run: |
          domain="${{ env.DOMAIN }}"
          echo "$domain"
""",
    )
    hops, _unreadable, _ = _scan(document)
    assert [h.reference for h in hops] == ["env.DOMAIN"], (
        f"expected the env-context hop, got {[str(h) for h in hops]}"
    )


def test_a_core_setoutput_hop_is_detected():
    """`core.setOutput` is a GITHUB_OUTPUT write the run-file parser cannot see.

    105 and 113 both publish this way, so a guard blind to it would read them as
    holding no outputs at all.
    """
    document = """\
name: sample
on:
  workflow_dispatch:
    inputs:
      domain:
        type: string
jobs:
  one:
    runs-on: ubuntu-latest
    steps:
      - name: Resolve
        id: meta
        uses: actions/github-script@v7
        env:
          IN_DOMAIN: ${{ inputs.domain }}
        with:
          script: |
            core.setOutput('domain', process.env.IN_DOMAIN.toLowerCase());
      - name: Use it
        run: |
          domain="${{ steps.meta.outputs.domain }}"
          echo "$domain"
"""
    hops, _unreadable, _ = _scan(document)
    assert [h.reference for h in hops] == ["steps.meta.outputs.domain"], (
        f"expected the setOutput hop, got {[str(h) for h in hops]}"
    )


def test_a_three_hop_chain_is_followed():
    """Taint has to survive output -> job output -> output -> read."""
    document = """\
name: sample
on:
  workflow_dispatch:
    inputs:
      domain:
        type: string
jobs:
  a:
    runs-on: ubuntu-latest
    outputs:
      domain: ${{ steps.meta.outputs.domain }}
    steps:
      - name: Meta
        id: meta
        env:
          IN_DOMAIN: ${{ inputs.domain }}
        run: echo "domain=$IN_DOMAIN" >> "$GITHUB_OUTPUT"
  b:
    runs-on: ubuntu-latest
    needs: a
    outputs:
      domain: ${{ steps.again.outputs.domain }}
    steps:
      - name: Again
        id: again
        env:
          UPSTREAM: ${{ needs.a.outputs.domain }}
        run: echo "domain=$UPSTREAM" >> "$GITHUB_OUTPUT"
  c:
    runs-on: ubuntu-latest
    needs: b
    steps:
      - name: Use it
        run: |
          domain="${{ needs.b.outputs.domain }}"
"""
    hops, _unreadable, _ = _scan(document)
    assert [h.reference for h in hops] == ["needs.b.outputs.domain"], (
        f"the chain should reach job c, got {[str(h) for h in hops]}"
    )


# --- the false-positive surface -------------------------------------------


def test_reading_the_value_through_an_env_mapping_is_not_a_finding():
    """The REMEDY must not be a finding, or the fix cannot land (ledger L27)."""
    document = LAUNDERING.replace(
        """      - name: Use it
        run: |
          domain="${{ needs.resolve.outputs.domain }}"
          echo "$domain"
""",
        """      - name: Use it
        env:
          IN_DOMAIN_RESOLVED: ${{ needs.resolve.outputs.domain }}
        run: |
          domain="$IN_DOMAIN_RESOLVED"
          echo "$domain"
""",
    )
    hops, unreadable, _ = _scan(document)
    assert unreadable == []
    assert hops == [], f"the env: remedy must be clean, got {[str(h) for h in hops]}"


def test_an_output_that_never_touched_an_input_is_not_a_finding():
    """An untainted output read into a body is ordinary workflow code."""
    document = """\
name: sample
on:
  workflow_dispatch:
    inputs:
      domain:
        type: string
jobs:
  one:
    runs-on: ubuntu-latest
    steps:
      - name: Timestamp
        id: stamp
        run: echo "at=$(date +%s)" >> "$GITHUB_OUTPUT"
      - name: Use it
        run: echo "${{ steps.stamp.outputs.at }}"
"""
    hops, _unreadable, _ = _scan(document)
    assert hops == [], f"an untainted output is not a hop, got {[str(h) for h in hops]}"


def test_a_constrained_input_type_cannot_launder():
    """`boolean` and `choice` are values GitHub generated; they carry nothing."""
    document = LAUNDERING.replace("        type: string", "        type: boolean")
    hops, _unreadable, _ = _scan(document)
    assert hops == [], f"a boolean cannot carry a payload, got {[str(h) for h in hops]}"


def test_the_expression_outside_a_script_body_is_not_a_finding():
    """`concurrency:`, `if:`, `with:` and `name:` are not script text.

    706 is why this is pinned: the first textual occurrence of its laundered
    reference in the file is a `concurrency: group:`, and a guard that read
    those would report a hop that cannot execute.
    """
    document = LAUNDERING.replace(
        """      - name: Use it
        run: |
          domain="${{ needs.resolve.outputs.domain }}"
          echo "$domain"
""",
        """      - name: Use it
        if: needs.resolve.outputs.domain != ''
        with:
          who: ${{ needs.resolve.outputs.domain }}
        uses: actions/checkout@v7.0.1
""",
    )
    document = document.replace(
        """  use:
    runs-on: ubuntu-latest
    needs: resolve
""",
        """  use:
    runs-on: ubuntu-latest
    needs: resolve
    concurrency:
      group: g-${{ needs.resolve.outputs.domain }}
""",
    )
    hops, unreadable, _ = _scan(document)
    assert unreadable == []
    assert hops == [], (
        f"only run:/script: bodies are script text, got {[str(h) for h in hops]}"
    )


def test_a_direct_input_reference_is_left_to_the_sibling():
    """The two populations are disjoint, so nothing is reported twice."""
    document = LAUNDERING.replace(
        """          domain="${{ needs.resolve.outputs.domain }}"
""",
        """          domain="${{ inputs.domain }}"
""",
    )
    hops, _unreadable, _ = _scan(document)
    assert hops == [], (
        f"a bare `inputs.` reference belongs to the sibling guard, got "
        f"{[str(h) for h in hops]}"
    )


def test_the_finding_renders_a_real_actions_expression():
    """A finding must quote the expression in the form it appears in the file.

    The f-string that builds it is `${{{{{self.expression}}}}}` — five braces a
    side, four of which are escapes for two literals. That is unreadable enough
    that a review pass read it as emitting `${{{...}}}` and filed it as a defect
    (Copilot on #1240, second round). It does not: the rendered text is exactly
    `${{ … }}`. Asserting on the RENDERED string rather than on the source is
    the whole point — brace-counting in an f-string is what produced the wrong
    reading, and the same trap is waiting for the next person to edit this
    line.
    """
    hop = guard.Hop(
        "706-x.yml", "convert", "Gate", "run",
        "needs.resolve.outputs.domain", " needs.resolve.outputs.domain ", 608,
    )
    rendered = str(hop)
    assert "`${{ needs.resolve.outputs.domain }}`" in rendered, rendered
    assert "{{{" not in rendered, f"extra opening brace: {rendered}"
    assert "}}}" not in rendered, f"extra closing brace: {rendered}"
    assert rendered.startswith("706-x.yml:608 "), rendered


# --- the line citation -----------------------------------------------------


def test_the_citation_lands_on_the_body_not_the_first_textual_match():
    """A citation that opens on a non-finding reads as a false positive.

    Measured on 706: the first occurrence of its laundered reference is a
    `concurrency: group:` several hundred lines above the `run:` that actually
    reads it.
    """
    document = LAUNDERING.replace(
        """  use:
    runs-on: ubuntu-latest
    needs: resolve
""",
        """  use:
    runs-on: ubuntu-latest
    needs: resolve
    concurrency:
      group: g-${{ needs.resolve.outputs.domain }}
""",
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(pathlib.Path(tmp), "999-sample.yml", document)
        hops = guard.scan_workflow(path)
        lines = path.read_text(encoding="utf-8").splitlines()
    assert len(hops) == 1
    cited = lines[hops[0].line - 1]
    assert "domain=" in cited, (
        f"citation landed on {cited!r}, which is not the script body"
    )
    assert "group:" not in cited


# --- fail closed -----------------------------------------------------------


def test_unparseable_yaml_is_a_finding_not_a_skip():
    """An unreadable file is exactly the blind spot this exists to close."""
    with tempfile.TemporaryDirectory() as tmp:
        bad = _write(pathlib.Path(tmp), "999-bad.yml", "a: [1, 2\nb: {\n")
        _hops, unreadable, _ = guard.scan_all([bad])
    assert len(unreadable) == 1, f"expected one unreadable, got {unreadable}"


def test_a_non_mapping_document_is_a_finding():
    with tempfile.TemporaryDirectory() as tmp:
        bad = _write(pathlib.Path(tmp), "999-str.yml", "just a string\n")
        _hops, unreadable, _ = guard.scan_all([bad])
    assert len(unreadable) == 1, f"expected one unreadable, got {unreadable}"


def test_a_solver_that_does_not_converge_is_a_finding_not_a_partial_result():
    """Exhausting the iteration bound must fail closed, never truncate.

    A truncated pass returns the hops it happens to have found and prints the
    reassuring OK line for the rest — under-reporting, which is the exact
    direction this guard exists to close. Driven by shrinking the bound rather
    than by constructing a pathological workflow, so the test states the
    contract instead of depending on a workflow shape that a later refactor
    could make converge.
    """
    # CONTROL first: with the real bound this sample converges and reports its
    # one hop, so a failure below is attributable to the shrunk bound and not
    # to the sample (ledger L209).
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(pathlib.Path(tmp), "999-sample.yml", LAUNDERING)
        hops, unreadable, _ = guard.scan_all([path])
    assert len(hops) == 1 and unreadable == [], (
        f"CONTROL: expected one hop and no unreadable, got {hops} {unreadable}"
    )

    # One pass cannot settle: the first pass discovers the writer's output, so
    # a second is needed just to observe that nothing further changed.
    original = guard.Analysis._pass_limit
    guard.Analysis._pass_limit = lambda self: 1
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(pathlib.Path(tmp), "999-sample.yml", LAUNDERING)
            hops, unreadable, _ = guard.scan_all([path])
    finally:
        guard.Analysis._pass_limit = original

    assert hops == [], "a non-converged analysis must contribute no hops"
    assert len(unreadable) == 1, f"expected one unreadable, got {unreadable}"
    assert "did not converge" in unreadable[0], unreadable[0]

    # And the restore is real, not assumed.
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(pathlib.Path(tmp), "999-sample.yml", LAUNDERING)
        hops, unreadable, _ = guard.scan_all([path])
    assert len(hops) == 1 and unreadable == [], "bound not restored after the test"


def test_an_unreadable_from_the_analysis_names_the_workflow():
    """A fail-closed report nobody can act on is only half a guard.

    `Analysis` does not know which file it was built from, so without the
    re-raise its message reaches `scan_all`'s unreadable list naming no
    workflow — and the operator gets "something did not converge" with 108
    candidates.
    """
    original = guard.Analysis._pass_limit
    guard.Analysis._pass_limit = lambda self: 1
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(pathlib.Path(tmp), "999-named.yml", LAUNDERING)
            _hops, unreadable, _ = guard.scan_all([path])
    finally:
        guard.Analysis._pass_limit = original

    assert len(unreadable) == 1, f"expected one unreadable, got {unreadable}"
    assert unreadable[0].startswith("999-named.yml: "), (
        f"the finding must name the workflow, got {unreadable[0]!r}"
    )


def test_the_convergence_bound_scales_with_the_workflow():
    """An exhausted bound must mean the analysis is wrong, not that this is big.

    A fixed bound would make a legitimately deep workflow indistinguishable
    from a broken solver, and the fail-closed behaviour above turns that
    confusion into a CI failure on a correct file.
    """
    small = yaml.safe_load(LAUNDERING)
    analysis = guard.Analysis(small, {"domain"})
    assert analysis.jobs, "the sample must have jobs for this to mean anything"
    # Convergence on the real tree is the practical assertion: every shipped
    # workflow settles inside its own bound, so no file in the repo is
    # reported unreadable for this reason.
    _hops, unreadable, _ = guard.scan_all()
    assert unreadable == [], f"the real tree must converge: {unreadable}"


# --- the freeze is exact in BOTH directions --------------------------------


def test_a_new_hop_in_an_already_frozen_workflow_fails():
    """Per-reference, not per-file — otherwise a frozen file is a free pass.

    The frozen workflow is taken from the freeze rather than named, for the
    reason given on the positive control above: a lane that burns down the
    named file should not have to edit this test to land.
    """
    assert guard.KNOWN_LAUNDERED, "nothing is frozen; this test has no subject"
    workflow = sorted(guard.KNOWN_LAUNDERED)[0]
    invented = "needs.invented.outputs.not_frozen"
    assert invented not in guard.KNOWN_LAUNDERED[workflow]
    current = {workflow: tuple(sorted(
        set(guard.KNOWN_LAUNDERED[workflow]) | {invented}
    ))}
    new, _stale = guard.compare(current)
    assert new == [f"{workflow}: {invented}"], (
        f"a new reference in a frozen workflow must be reported, got {new}"
    )


def test_a_hop_that_is_gone_is_reported_stale():
    """A burn-down that does not delete its entry leaves the list lying."""
    new, stale = guard.compare({})
    assert new == []
    assert len(stale) == sum(len(v) for v in guard.KNOWN_LAUNDERED.values()), (
        f"every frozen reference should read stale against an empty tree: {stale}"
    )


# --- mutation: the guard is shown to FAIL ----------------------------------


def _run_against(document: str) -> tuple[int, list[str]]:
    """(new-count, references) for a one-file scan. No freeze applied."""
    hops, unreadable, _ = _scan(document)
    assert unreadable == [], f"the sample must parse: {unreadable}"
    return len(hops), sorted({h.reference for h in hops})


def test_mutation_reintroducing_102s_hop_is_caught():
    """#1233's requirement: put 102's hop back and watch the guard fail.

    102 is the lane that found this class. #1234 closed its real hop by moving
    the downstream read into a step-level `env:`; this puts the shipped form
    back — `$domain = "${{ steps.meta.outputs.domain }}"` — on a copy, and
    requires the guard to name it.

    The CONTROL is asserted first and on the same tree shape (ledger L209): the
    remedied document must be clean, or a red below is not attributable to the
    mutation.
    """
    remedied = """\
name: 102-like
on:
  workflow_dispatch:
    inputs:
      domain:
        type: string
jobs:
  whmcs_preflight:
    runs-on: windows-latest
    environment: whmcs-prod
    outputs:
      domain: ${{ steps.meta.outputs.domain }}
    steps:
      - name: Metadata
        id: meta
        shell: pwsh
        env:
          IN_DOMAIN: ${{ inputs.domain }}
        run: |
          $d = $env:IN_DOMAIN.Trim().ToLowerInvariant().Trim('.')
          "domain=$d" | Out-File -FilePath $env:GITHUB_OUTPUT -Append -Encoding utf8
      - name: Check domain exists in WHMCS
        shell: pwsh
        env:
          IN_DOMAIN_RESOLVED: ${{ steps.meta.outputs.domain }}
        run: |
          $domain = $env:IN_DOMAIN_RESOLVED
          Write-Output $domain
"""
    control_count, control_refs = _run_against(remedied)
    assert control_count == 0, (
        f"CONTROL is not clean, so a mutation result would be unattributable: "
        f"{control_refs}"
    )

    anchor = """          $domain = $env:IN_DOMAIN_RESOLVED
"""
    assert remedied.count(anchor) == 1, (
        "the mutation anchor is not present exactly once — the sample changed "
        "and this test would silently mutate nothing (AGENTS.md: prove the "
        "plant landed)"
    )
    mutant = remedied.replace(
        anchor,
        """          $domain = "${{ steps.meta.outputs.domain }}"
""",
    )
    assert mutant != remedied, "the mutation did not apply"
    # The mutant must still PARSE, or a guard that died reading it would score
    # as a detection (ledger L203).
    assert isinstance(yaml.safe_load(mutant), dict), "the mutant is not valid YAML"

    count, references = _run_against(mutant)
    assert count == 1, f"the reintroduced 102 hop was not caught: {references}"
    assert references == ["steps.meta.outputs.domain"], references


def test_mutation_removing_the_env_source_blinds_the_guard():
    """The `env:` surface is load-bearing, not decoration.

    This mutation is the inverse of the one above: instead of reintroducing the
    defect, it removes the surface the analysis reads it through. If the guard
    still reported the hop, the report would not be coming from the dataflow at
    all — a green that measures nothing, arrived at from the other side.
    """
    control_count, _ = _run_against(LAUNDERING)
    assert control_count == 1, "CONTROL: the laundering sample must report one hop"

    anchor = """        env:
          IN_DOMAIN: ${{ inputs.domain }}
"""
    assert LAUNDERING.count(anchor) == 1, "mutation anchor missing or duplicated"
    mutant = LAUNDERING.replace(anchor, "")
    assert isinstance(yaml.safe_load(mutant), dict), "the mutant is not valid YAML"

    count, references = _run_against(mutant)
    assert count == 0, (
        f"with no input reaching the writing step there is nothing to launder, "
        f"but the guard reported {references} — it is not reading the dataflow"
    )


def test_mutation_each_sink_kind_is_detected_independently():
    """Sibling mutations must fire on DIFFERENT, correctly-named subsets.

    A set of mutations that all fire is weaker evidence than a set where each
    one fires on exactly its own case (AGENTS.md). So the three sink spellings
    are driven separately and each must report its own reference and no other.
    """
    cases = {
        "needs.resolve.outputs.domain": LAUNDERING,
        "steps.meta.outputs.domain": LAUNDERING.replace(
            'domain="${{ needs.resolve.outputs.domain }}"',
            'domain="${{ steps.meta.outputs.domain }}"',
        ).replace(
            """  use:
    runs-on: ubuntu-latest
    needs: resolve
    steps:
""",
            """  use:
    runs-on: ubuntu-latest
    needs: resolve
    steps:
""",
        ),
    }
    # The same-job spelling only resolves inside the writing job, so move the
    # reader there rather than asserting a cross-job `steps.` reference that
    # GitHub itself would not resolve.
    cases["steps.meta.outputs.domain"] = LAUNDERING.replace(
        """  use:
    runs-on: ubuntu-latest
    needs: resolve
    steps:
      - name: Use it
        run: |
          domain="${{ needs.resolve.outputs.domain }}"
          echo "$domain"
""",
        "",
    ).replace(
        """          echo "domain=$d" >> "$GITHUB_OUTPUT"
""",
        """          echo "domain=$d" >> "$GITHUB_OUTPUT"
      - name: Use it
        run: |
          domain="${{ steps.meta.outputs.domain }}"
""",
    )

    for expected, document in cases.items():
        assert isinstance(yaml.safe_load(document), dict), f"{expected}: bad YAML"
        count, references = _run_against(document)
        assert references == [expected], (
            f"expected exactly {expected}, got {references} ({count} hops)"
        )


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
