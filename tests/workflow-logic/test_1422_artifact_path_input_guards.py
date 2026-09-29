r"""Unit tests for the artifact-selector guard set and its checker (#1422).

THE FINDING THIS MODULE PINS
    The #1080 burn-down's completion test is "is the free-text input still
    substituted into the script's TEXT". Four workflows passed that test while
    the SECOND consumer of the same input -- an `actions/upload-artifact` step's
    `path:`, which is an `@actions/glob` SELECTOR rather than a filename -- still
    read it raw and unconstrained. All four were scored finished.

    Measured on `main` @ `3d0d68f`, by the checker this module also tests:

        213-whmcs-zeffy-payments-import-draft.yml  missing NEWLINE GLOB ROOTED TILDE DOTDOT
        218-whmcs-siteslist-reconciliation.yml     missing NEWLINE GLOB ROOTED TILDE DOTDOT
        801-candid-charity-check.yml               missing NEWLINE GLOB TILDE
        802-candid-essentials-search.yml           missing NEWLINE GLOB TILDE

    213 and 218 are the two the issue named. 801 and 802 were found by
    re-deriving the census from the tree rather than from the issue's table, and
    the three classes they were missing were confirmed independently by running
    their shipped condition under pwsh 7.4.6 against each payload:

        ~/.azure/x.json                        IsPathRooted false, no colon -> PASSED
        artifacts/[m]sal_token_cache.json      -> PASSED
        a.json<LF>.azure/msal_token_cache.json -> PASSED

    The last one matters for a reason worth keeping: a newline payload that ALSO
    carries a colon is refused, by the colon test rather than by anything about
    the newline. A census that used such a payload would score the newline class
    as covered. Two of the three measurements here point the flattering way if
    the probe is chosen carelessly.

WHAT THIS MODULE DOES *NOT* RE-TEST
    The behavioural half -- that each guard refuses each payload under a real
    PowerShell host -- is covered per lane by
    `test_202_products_export_wiring.py`'s pattern and is not duplicated here.
    This module is the CLASS-level guard: the six conditions are present, in the
    order that makes the anchored ones sound, on every site the checker can see,
    and the checker itself discriminates rather than merely being quiet.

    That division is deliberate. A module asserting both would need pwsh, and a
    whole-module `shutil.which` gate turns "could not run" into "everything
    passed" (#1182). Every case here is pure YAML/AST and runs on any host.

EVERY LOOKUP ASSERTS PRESENCE BEFORE INDEXING
    A `str.index` miss raises ValueError, which is not an AssertionError, so the
    module runner does not catch it and the roster ABORTS -- the remaining cases
    report no outcome at all, and a reviewer counting FAIL lines scores that as
    passing (ledger L194).
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import load_workflow  # noqa: E402

_CHECKER_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "scripts"
    / "check-workflow-artifact-path-input-guards.py"
)
_spec = importlib.util.spec_from_file_location("artifact_path_guard", _CHECKER_PATH)
assert _spec is not None and _spec.loader is not None, (
    f"cannot infer an importer for the #1422 checker at {_CHECKER_PATH} "
    f"(exists={_CHECKER_PATH.exists()}) — every assertion in this module is "
    f"stated against that checker, so name the path rather than failing on a "
    f"None attribute"
)
guard = importlib.util.module_from_spec(_spec)
# Registered BEFORE exec: `@dataclass` resolves the class's own module out of
# `sys.modules`, and without this the import dies inside dataclasses with an
# `AttributeError: 'NoneType' object has no attribute '__dict__'` naming neither
# this file nor the checker (test_1150_empty_input_guard.py carries the same note).
sys.modules[_spec.name] = guard
_spec.loader.exec_module(guard)

WORKFLOW_DIR = pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows"

# The guard message fragments, in the order the shipped guards apply them.
BLANK = "is blank."
NEWLINE = "carriage return or newline"
GLOB = "glob metacharacter"
ROOTED = "workspace-relative"
TILDE = "must not begin with '~'"
DOTDOT = "'..' segment"

# The two spellings that anchor with `^`, which is start-of-STRING. The newline
# guard must precede both or they validate only a multi-line value's first line.
ANCHORED = (ROOTED, TILDE)

# The condition texts, shared by every lane that uses the canonical set. Asserted
# as TEXT rather than by regex so a lane that rewrites one by hand -- widening
# the glob class, or swapping `\A` for `^` -- fails here.
CONDITIONS = {
    NEWLINE: r"-match '[\r\n]'",
    GLOB: r"-match '[*?\[\]]'",
    ROOTED: r"-match '^([A-Za-z]+:|[\\/])'",
    TILDE: r"-match '^~'",
    DOTDOT: r"-split '[\\/]') -contains '..'",
}

# The lanes this PR brought to the full set, and the step whose body carries it.
# 213 validates in a step of its own, before the credential is fetched; the other
# three guard inside their consuming step, as 201/203/208 do.
CANONICAL_LANES = {
    "213-whmcs-zeffy-payments-import-draft.yml": (
        "whmcs_to_zeffy_draft",
        "Validate artifact-consumed output paths",
    ),
    "218-whmcs-siteslist-reconciliation.yml": (
        "siteslist_reconciliation",
        "Reconcile WHMCS with the sites-list",
    ),
}

# 801/802 keep their own measured ROOTED spelling (a colon test paired with
# IsPathRooted, correct on the `windows-latest` runner they declare) and gained
# the three classes they were missing.
MIXED_LANES = {
    "801-candid-charity-check.yml": ("charity_check", "Charity Check lookup"),
    "802-candid-essentials-search.yml": ("essentials_search", "Essentials search"),
}
MIXED_ADDED = (NEWLINE, GLOB, TILDE)

# The lanes this PR did not touch, which must stay credited: a change here that
# started reporting them would be a false finding, and 601's allowlist is the one
# most likely to be mis-scored (it is STRICTER than the six conditions, not
# weaker).
UNTOUCHED_GUARDED = (
    "201-whmcs-export-domains.yml",
    "202-whmcs-export-products.yml",
    "203-whmcs-export-payment-methods.yml",
    "208-whmcs-tickets-export.yml",
    "601-wpmudev-export-sites.yml",
    # The five this freeze shipped holding, burned down together in #1080
    # lane 27 and guarded here in the same PR — the coupling below working as
    # designed. They join this tuple rather than merely leaving EXPECTED_FREEZE,
    # because "no longer frozen" and "positively credited" are different claims
    # and only the second one fails if a later change breaks a guard.
    "214-whmcs-clients-metrics.yml",
    "215-whmcs-nonprofit-clients-metrics.yml",
    "216-whmcs-activity-metrics.yml",
    "217-whmcs-client-fields-survey.yml",
    "220-whmcs-served-metrics.yml",
)

# EMPTY as of #1080 lane 27. The freeze shipped holding the five lanes above and
# they were burned down together, so every artifact-path site in the tree is now
# guarded.
#
# An empty expectation is not a weaker assertion than a populated one — it is
# the strongest state this coupling has, because from here ANY new free-text
# input reaching an `upload-artifact` `path:` is a finding on its first commit.
# The assertion below is an equality for that reason: a lane that needs a freeze
# entry has to add it HERE with a written reason, which is the review step the
# equality exists to force.
EXPECTED_FREEZE = ()


def _step_body(workflow: str, job: str, step_substring: str) -> str:
    """The `run:` text of the one step whose name contains `step_substring`.

    Matched on a substring rather than exactly, because several of these step
    names carry a trailing "(read-only)" that is not load-bearing. A miss is a
    named failure rather than a StopIteration that would abort the roster.
    """
    doc = load_workflow(workflow)
    jobs = doc.get("jobs") or {}
    assert job in jobs, f"{workflow} has no job {job!r} — jobs: {sorted(jobs)}"
    steps = jobs[job].get("steps") or []
    matches = [
        s for s in steps
        if isinstance(s, dict) and step_substring in (s.get("name") or "")
    ]
    assert len(matches) == 1, (
        f"{workflow} job {job!r} has {len(matches)} steps whose name contains "
        f"{step_substring!r}, expected exactly one — names: "
        f"{[s.get('name') for s in steps if isinstance(s, dict)]}"
    )
    body = matches[0].get("run")
    assert isinstance(body, str), (
        f"{workflow} step {step_substring!r} has no `run:` body ({body!r})"
    )
    return body


def _upload_paths(workflow: str, job: str) -> list:
    """Every (step name, path:) of an upload-artifact step in this job."""
    doc = load_workflow(workflow)
    out = []
    for step in (doc.get("jobs") or {}).get(job, {}).get("steps") or []:
        if not isinstance(step, dict):
            continue
        if "actions/upload-artifact" not in (step.get("uses") or ""):
            continue
        with_block = step.get("with")
        assert isinstance(with_block, dict) and "path" in with_block, (
            f"{workflow}: an upload-artifact step has no `with.path` "
            f"({with_block!r}) — that is a finding, not a crash"
        )
        out.append((step.get("name") or "", with_block["path"]))
    return out


# --------------------------------------------------------------------------
# The guard set, per lane
# --------------------------------------------------------------------------


def test_the_canonical_lanes_carry_all_six_conditions():
    """213 and 218 ship the same six conditions as 201/202/203/208.

    Both the MESSAGE and the CONDITION TEXT are asserted. A message alone would
    pass for a guard whose regex had been widened or whose anchors had been
    changed from `\\A` to `^`; a condition alone would pass for one that detects
    the payload and then says nothing useful about which input caused it.
    """
    for workflow, (job, step) in CANONICAL_LANES.items():
        body = _step_body(workflow, job, step)
        assert BLANK in body, (
            f"{workflow}: the blank guard's message is gone from {step!r}. "
            f"Body: {body!r}"
        )
        for message, condition in CONDITIONS.items():
            assert message in body, (
                f"{workflow}: step {step!r} no longer carries the {message!r} "
                f"guard — the artifact selector reads this input raw, so the "
                f"class it covers is reachable again (#1422)"
            )
            assert condition in body, (
                f"{workflow}: the {message!r} guard no longer uses the shipped "
                f"condition {condition!r}. A hand-rewritten spelling is how the "
                f"glob set gets widened past what @actions/glob expands (#1380) "
                f"or an anchor gets weakened; copy 202's text"
            )


def test_the_mixed_lanes_gained_the_three_classes_they_lacked():
    """801/802 keep their measured rooted spelling and gain newline, glob and `~`.

    Their `Contains(':')` + `IsPathRooted` pair is correct on the
    `windows-latest` runner they declare, so it is not rewritten. What was
    missing is asserted present; what was already there is asserted still there,
    so a later tidy-up cannot trade one for the other.
    """
    for workflow, (job, step) in MIXED_LANES.items():
        body = _step_body(workflow, job, step)
        for message in MIXED_ADDED:
            assert message in body, (
                f"{workflow}: step {step!r} lost the {message!r} guard — "
                f"measured on pwsh 7.4.6, this lane's pre-#1422 condition let "
                f"that payload through"
            )
            assert CONDITIONS[message] in body, (
                f"{workflow}: the {message!r} guard no longer uses the shipped "
                f"condition {CONDITIONS[message]!r}"
            )
        assert "IsPathRooted" in body and ".Contains(':')" in body, (
            f"{workflow}: the paired colon / IsPathRooted test is gone. "
            f"IsPathRooted alone is platform-DEPENDENT, which is why the pair "
            f"is what covers the ROOTED class here. Body: {body!r}"
        )
        assert DOTDOT.strip("'") in body or "-contains '..'" in body, (
            f"{workflow}: the '..' test is gone. Body: {body!r}"
        )


def test_the_newline_guard_precedes_the_anchored_guards_in_every_fixed_lane():
    """Ordering no behavioural case can observe, because every payload is refused.

    `^` anchors at the start of the STRING, not of each line, so the rooted and
    tilde checks see only the first line of a multi-line value. With the newline
    check first, a multi-line payload never reaches them; move it last and they
    silently validate a prefix. Both orders are green behaviourally, which is
    exactly why the ordering needs its own assertion.
    """
    lanes = dict(CANONICAL_LANES)
    lanes.update(MIXED_LANES)
    for workflow, (job, step) in lanes.items():
        body = _step_body(workflow, job, step)
        assert NEWLINE in body, (
            f"{workflow}: the newline guard is gone, so a multi-line value would "
            f"reach the anchored checks, which see only its first line"
        )
        newline_at = body.index(NEWLINE)
        for message in ANCHORED:
            if message not in body:
                # 801/802 cover ROOTED with the colon/IsPathRooted pair rather
                # than with the anchored regex, so its absence is legitimate
                # there. TILDE is anchored in every lane.
                assert message == ROOTED, (
                    f"{workflow}: the {message!r} guard is gone from {step!r}"
                )
                continue
            assert newline_at < body.index(message), (
                f"{workflow}: the newline guard must come before the {message!r} "
                f"guard, which anchors on '^' and would otherwise examine only "
                f"the first line of a multi-line value"
            )


def test_213_guards_before_the_credential_is_fetched():
    """A refused dispatch must not have loaded the WHMCS credential first.

    213's five values are first read by four different steps, so the guard lives
    in a step of its own. Putting it ahead of `whmcs-secrets-from-kv` means a bad
    dispatch never fetches the credential and never leaves an `az` session on
    disk (#1188/#1208) — the thing a path escaping the workspace would be aimed
    at.
    """
    workflow, (job, step) = (
        "213-whmcs-zeffy-payments-import-draft.yml",
        CANONICAL_LANES["213-whmcs-zeffy-payments-import-draft.yml"],
    )
    steps = (load_workflow(workflow).get("jobs") or {})[job]["steps"]
    names = [
        (s.get("name") or s.get("uses") or "") for s in steps if isinstance(s, dict)
    ]
    guard_at = [i for i, n in enumerate(names) if step in n]
    kv_at = [i for i, n in enumerate(names) if "whmcs-secrets-from-kv" in n]
    assert guard_at, f"{workflow}: the {step!r} step is gone — steps: {names}"
    assert kv_at, f"{workflow}: the Key Vault step is gone — steps: {names}"
    assert guard_at[0] < kv_at[0], (
        f"{workflow}: the guard step is at index {guard_at[0]} and the Key Vault "
        f"step at {kv_at[0]} — a refused dispatch would already hold the WHMCS "
        f"credential. Steps: {names}"
    )


def test_213_guards_every_input_its_artifact_steps_read_and_only_those():
    """The spec list is the structural half of "one guard body, five values".

    A sixth artifact-consumed input added to this workflow without a spec entry
    must fail HERE rather than ship as an unguarded path. Derived from the
    workflow's own upload steps, so the list cannot drift from the uploads it is
    supposed to cover — which is the mistake #1422 itself contains: its text
    names three artifact-consumed inputs for 213 and the tree has five.
    """
    workflow = "213-whmcs-zeffy-payments-import-draft.yml"
    job, step = CANONICAL_LANES[workflow]
    body = _step_body(workflow, job, step)

    free_text = guard.free_text_inputs(load_workflow(workflow))
    consumed = set()
    for _name, path in _upload_paths(workflow, job):
        consumed |= guard.inputs_in(path) & free_text
    assert len(consumed) == 5, (
        f"{workflow}: expected five artifact-consumed free-text inputs, found "
        f"{len(consumed)}: {sorted(consumed)}. If an upload was added or removed, "
        f"the guard loop's spec list must change with it."
    )

    anchor = "foreach ($spec in @("
    assert anchor in body, (
        f"{workflow}: the guard loop is gone from {step!r}, so NOTHING is "
        f"validated. Body: {body!r}"
    )
    start = body.index(anchor)
    opener = ")) {"
    assert opener in body[start:], (
        f"{workflow}: the guard loop no longer opens with {opener!r}, so its "
        f"spec list cannot be read. Body: {body!r}"
    )
    spec_list = body[start : body.index(opener, start)]

    for name in sorted(consumed):
        pairing = f"Input = '{name}'"
        assert pairing in spec_list, (
            f"{workflow}: the guard loop's spec list does not carry {pairing!r}, "
            f"so {name} reaches an artifact selector unguarded. "
            f"Spec list: {spec_list!r}"
        )
        entry = spec_list[spec_list.index(pairing) :]
        entry = entry[: entry.index("}") if "}" in entry else len(entry)]
        expected_var = "IN_" + name.upper()
        assert f"$env:{expected_var}" in entry, (
            f"{workflow}: the spec entry for {name} does not read "
            f"$env:{expected_var} — it reads {entry!r}. A spec that names one "
            f"input and reads another validates one value twice and leaves the "
            f"other unguarded, which is the copy-paste this loop shape exists "
            f"to prevent."
        )
    assert spec_list.count("Input =") == len(consumed), (
        f"{workflow}: the guard loop carries {spec_list.count('Input =')} spec "
        f"entries for {len(consumed)} artifact-consumed inputs. "
        f"Spec list: {spec_list!r}"
    )


def test_every_fixed_lane_still_reads_its_input_raw_in_the_upload_path():
    """The two-consumer premise the whole guard set rests on.

    If a later edit routed the upload through a guarded value instead, the
    reasoning for these guards would stop applying and their comments would
    become wrong. That should be a red test, not a stale paragraph.
    """
    lanes = dict(CANONICAL_LANES)
    lanes.update(MIXED_LANES)
    for workflow, (job, _step) in lanes.items():
        uploads = _upload_paths(workflow, job)
        assert uploads, f"{workflow}: job {job!r} has no upload-artifact step"
        free_text = guard.free_text_inputs(load_workflow(workflow))
        raw = set()
        for _name, path in uploads:
            raw |= guard.inputs_in(path) & free_text
        assert raw, (
            f"{workflow}: no upload-artifact `path:` interpolates a free-text "
            f"dispatch input any more. If that is deliberate, the guards and "
            f"their comments should be revisited in the same PR — they justify "
            f"themselves by that second consumer."
        )


# --------------------------------------------------------------------------
# The checker
# --------------------------------------------------------------------------


def test_the_checker_reports_nothing_outside_the_freeze():
    """The whole-tree verdict, which is the check CI runs."""
    sites, unreadable = guard.scan()
    assert not unreadable, f"workflows that would not parse: {unreadable}"
    new, stale = guard.compare(guard.current_map(sites))
    assert not new, f"new unguarded artifact-selector sites: {new}"
    assert not stale, f"stale KNOWN_UNGUARDED entries: {stale}"


def test_the_freeze_is_exactly_the_open_burn_down_lanes():
    """Every frozen lane is one the interpolation guard still reports.

    This is the coupling #1422 is about: a lane that leaves
    `check-workflow-input-interpolation.py`'s freeze must leave this one in the
    same PR, or it is scored finished with the second consumer unguarded. A
    workflow frozen HERE but already burned down THERE is precisely the state
    that produced the finding, so it fails.
    """
    interp_path = (
        pathlib.Path(__file__).resolve().parents[2]
        / "scripts"
        / "check-workflow-input-interpolation.py"
    )
    spec = importlib.util.spec_from_file_location("interp_guard_1422", interp_path)
    assert spec is not None and spec.loader is not None, (
        f"cannot import the #1080 checker at {interp_path}"
    )
    interp = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = interp
    spec.loader.exec_module(interp)

    assert set(guard.KNOWN_UNGUARDED) == set(EXPECTED_FREEZE), (
        f"the freeze changed: {sorted(guard.KNOWN_UNGUARDED)} vs the expected "
        f"{sorted(EXPECTED_FREEZE)}. A lane leaving this freeze must land its "
        f"guard set in the same PR; a lane joining it needs a written reason."
    )
    for workflow in guard.KNOWN_UNGUARDED:
        assert workflow in interp.KNOWN_UNGUARDED, (
            f"{workflow} is frozen for the artifact selector but is already "
            f"burned down in check-workflow-input-interpolation.py — that is "
            f"exactly #1422's finding (scored finished, second consumer "
            f"unguarded). Guard it here rather than freezing it."
        )


def test_the_untouched_guarded_lanes_are_credited():
    """No false findings, and 601's allowlist is the one at risk.

    601 constrains its value with a `\\A`-anchored allowlist that is STRICTER
    than the six conditions — a class of `[A-Za-z0-9._-]` admits no separator, no
    metacharacter, no `~`, no `:` and no newline. A checker that demanded the
    literal six spellings would report it, and a checker that reports correct
    code is the one that gets switched off.
    """
    sites, unreadable = guard.scan()
    assert not unreadable, f"workflows that would not parse: {unreadable}"
    flagged = {s.workflow for s in sites}
    for workflow in UNTOUCHED_GUARDED:
        assert (WORKFLOW_DIR / workflow).exists(), (
            f"{workflow} is named by this module but not in the tree — update "
            f"UNTOUCHED_GUARDED rather than leaving a vacuous assertion"
        )
        assert workflow not in flagged, (
            f"{workflow} is reported unguarded, but this PR did not touch it and "
            f"it constrains its value. Missing classes: "
            f"{[s.missing for s in sites if s.workflow == workflow]}"
        )


def test_the_allowlist_credit_requires_the_A_anchor_and_a_safe_class():
    """`^`/`$` must NOT earn the allowlist credit, and a loose class must not either.

    In .NET `$` also matches BEFORE a trailing newline, so `^[A-Za-z0-9._-]+$`
    accepts "out.csv\\n" — silently admitting the newline class it appears to
    exclude. 601's own comments record that measurement, and the credit is
    written to depend on it. The pre-fix control for the credit is the `^`/`$`
    spelling: if it were accepted too, the credit would be unsound.
    """
    body_ok = r"if ($env:IN_OUTPUT_FILE -notmatch '\A[A-Za-z0-9._-]+\z') { throw }"
    body_caret = r"if ($env:IN_OUTPUT_FILE -notmatch '^[A-Za-z0-9._-]+$') { throw }"
    body_loose = r"if ($env:IN_OUTPUT_FILE -notmatch '\A[A-Za-z0-9._/-]+\z') { throw }"
    refs = ["$env:IN_OUTPUT_FILE"]

    covered_ok = guard.satisfied_classes([body_ok], refs)
    for cls in guard._ALLOWLIST_CREDITS:
        assert cls in covered_ok, (
            f"the \\A-anchored allowlist no longer earns the {cls} credit, so "
            f"601 would be reported. Covered: {sorted(covered_ok)}"
        )
    assert "DOTDOT" not in covered_ok, (
        "an allowlist must NOT be credited for DOTDOT: '..' is spelled entirely "
        "in characters the class permits, so it needs its own test (601 pairs "
        "the allowlist with one, and its comments say why)"
    )

    covered_caret = guard.satisfied_classes([body_caret], refs)
    assert "NEWLINE" not in covered_caret, (
        "the `^`…`$` spelling earned the newline credit. In .NET `$` matches "
        "before a trailing newline, so that form accepts \"out.csv\\n\" — "
        "crediting it would reopen the class the credit claims to cover"
    )

    covered_loose = guard.satisfied_classes([body_loose], refs)
    assert "ROOTED" not in covered_loose, (
        "an allowlist whose class admits '/' earned the ROOTED credit — the "
        "forbidden-character test is what makes this credit safe, and it is not "
        "doing anything"
    )


def test_the_checker_flags_a_lane_whose_guards_are_removed():
    """The pre-fix control (ledger L47): does the checker discriminate, or is it quiet?

    Derived from the SHIPPED text by deleting the guard conditions, rather than
    from a hand-written 'before' that could drift. Each deletion is asserted to
    have changed the text, so a strip that silently did nothing cannot score as
    a control.
    """
    workflow = "218-whmcs-siteslist-reconciliation.yml"
    job, step = CANONICAL_LANES[workflow]
    body = _step_body(workflow, job, step)

    stripped = body
    for message, condition in CONDITIONS.items():
        assert condition in stripped, (
            f"{workflow}: {condition!r} is not in the shipped body, so this "
            f"control would measure a body it did not modify"
        )
        before = stripped
        stripped = stripped.replace(condition, "-match 'NEVER-MATCHES-ANYTHING'")
        assert stripped != before, f"stripping {condition!r} changed nothing"

    covered = guard.satisfied_classes([stripped], ["$env:IN_OUTPUT_FILE", "$out"])
    for cls in ("NEWLINE", "GLOB", "ROOTED", "TILDE", "DOTDOT"):
        assert cls not in covered, (
            f"the checker still credits {cls} for a body whose condition was "
            f"removed — it is matching something other than the guard, so its "
            f"silence on the real tree proves nothing. Covered: {sorted(covered)}"
        )
    assert "BLANK" in covered, (
        "the strip took the blank guard too, so this control cannot show that "
        "the checker distinguishes a present guard from an absent one"
    )


def test_the_checker_credits_the_loop_and_inline_shapes_alike():
    """Both shipped shapes, asserted against minimal bodies rather than the tree.

    The tree-level cases above would keep passing if the checker credited a
    variable for the wrong reason — for instance by ignoring the spec list and
    crediting `$value` whenever a loop appears anywhere in the job. That is the
    copy-paste bug the loop shape exists to prevent, so it is tested directly.
    """
    inline = r"""
      if ([string]::IsNullOrWhiteSpace($env:IN_X)) { throw 'blank' }
      if ($env:IN_X -match '[\r\n]') { throw 'nl' }
      if ($env:IN_X -match '[*?\[\]]') { throw 'glob' }
      if ($env:IN_X -match '^([A-Za-z]+:|[\\/])') { throw 'rooted' }
      if ($env:IN_X -match '^~') { throw 'tilde' }
      if (($env:IN_X -split '[\\/]') -contains '..') { throw 'dotdot' }
    """
    covered = guard.satisfied_classes([inline], ["$env:IN_X"])
    assert set(guard.CLASSES) <= covered, (
        f"the inline shape (201/203/208/218) is not fully credited: missing "
        f"{[c for c in guard.CLASSES if c not in covered]}"
    )

    loop = r"""
      foreach ($spec in @(
        @{ Input = 'a'; Value = $env:IN_A },
        @{ Input = 'b'; Value = $env:IN_B }
      )) {
        $value = $spec.Value
        if ([string]::IsNullOrWhiteSpace($value)) { throw 'blank' }
        if ($value -match '[\r\n]') { throw 'nl' }
        if ($value -match '[*?\[\]]') { throw 'glob' }
        if ($value -match '^([A-Za-z]+:|[\\/])') { throw 'rooted' }
        if ($value -match '^~') { throw 'tilde' }
        if (($value -split '[\\/]') -contains '..') { throw 'dotdot' }
      }
    """
    for var in ("IN_A", "IN_B"):
        extra = guard.loop_values([loop], {var})
        covered = guard.satisfied_classes([loop], [f"$env:{var}"] + sorted(extra))
        assert set(guard.CLASSES) <= covered, (
            f"the loop shape (202/213) is not fully credited for {var}: missing "
            f"{[c for c in guard.CLASSES if c not in covered]}"
        )

    # The discrimination that matters: a variable NOT in the spec list must not
    # be credited just because a guard loop exists in the same body.
    absent = guard.loop_values([loop], {"IN_C"})
    assert not absent, (
        f"a variable absent from the spec list was credited with the loop's "
        f"`$value` ({absent}) — that is exactly the copy-paste this shape exists "
        f"to prevent, and it would score an unguarded input as covered"
    )


def test_a_choice_or_boolean_input_is_not_treated_as_free_text():
    """Scope: GitHub constrains those, so guarding one would be decoration.

    More to the point, a checker that demanded guards for them would report
    correct code — and reading the type defensively matters because an input
    whose YAML body is empty parses to None rather than to a mapping.
    """
    doc = {
        True: {
            "workflow_dispatch": {
                "inputs": {
                    "free": {"type": "string"},
                    "untyped": {"description": "no type key defaults to string"},
                    "empty_body": None,
                    "picked": {"type": "choice", "options": ["a", "b"]},
                    "flag": {"type": "boolean"},
                    "count": {"type": "number"},
                }
            }
        }
    }
    found = guard.free_text_inputs(doc)
    assert found == {"free", "untyped", "empty_body"}, (
        f"free_text_inputs returned {sorted(found)} — a choice, boolean or "
        f"number cannot carry a payload, and an input with an empty body must "
        f"be counted rather than crashing on `.get`"
    )


def test_the_stale_detection_fires_on_a_freeze_entry_that_is_now_guarded():
    """Criterion 5's analogue: the freeze must not outlive the defect it records.

    Without this, a lane could be guarded and left frozen, and the next reader
    would believe the tree still had a hole there — which is the inverse of
    #1422's failure and just as misleading.

    Driven by a fabricated freeze passed through `known=`, not by the real one.
    It used to key off a live entry (`214`), and #1080 lane 27 then guarded
    every lane and emptied the freeze — which turned this case red for a reason
    that had nothing to do with stale detection. A test whose fixture is the
    thing being burned down expires on the burn-down, and it expires by failing
    in the ALARMING direction, which costs a reader the time to establish that
    nothing is wrong.

    The workflow names must nonetheless be REAL. `compare` tests file existence
    first and reports a missing file as its own kind of stale, so an invented
    name short-circuits before the branch under test and the case passes for
    the wrong reason — measured while writing this, with two invented names
    both coming back "does not exist". Ledger L306, in miniature: a fixture
    refused by the wrong check certifies the check it never reached.
    """
    real = "201-whmcs-export-domains.yml"
    assert (WORKFLOW_DIR / real).exists(), (
        f"{real} is this case's fixture and is not in the tree — pick another "
        f"real workflow rather than an invented name, or `compare` will take "
        f"its does-not-exist branch and never reach the one under test"
    )

    # Branch 1: the whole entry is stale — nothing in that workflow is
    # unguarded any more, which is the state a completed lane leaves behind.
    new, stale = guard.compare({}, known={real: ["export_domains:output_file"]})
    assert not new, f"an empty current map cannot contain a new instance: {new}"
    assert len(stale) == 1 and "nothing is unguarded" in stale[0], (
        f"a freeze entry whose workflow has no unguarded site left must read "
        f"stale; got {stale}"
    )

    # Branch 2: the entry survives but one of its keys was guarded. A per-file
    # check would miss this, and the freeze is per-site.
    new, stale = guard.compare(
        {real: ["export_domains:output_file"]},
        known={real: ["export_domains:output_file", "export_domains:phantom_input"]},
    )
    assert not new, f"a narrowed entry is not a new instance: {new}"
    assert len(stale) == 1 and "phantom_input" in stale[0], (
        f"a freeze key that is now guarded must be named stale on its own, even "
        f"while its workflow keeps another key; got {stale}"
    )

    missing_file = {"no-such-workflow.yml": ["j:x"]}
    new2, stale2 = guard.compare({}, known=missing_file)
    assert not new2 and len(stale2) == 1 and "does not exist" in stale2[0], (
        f"a freeze entry naming a missing file must read stale; got {stale2}"
    )


def test_an_unguarded_site_outside_the_freeze_is_a_finding():
    """The check's whole purpose, exercised rather than assumed.

    `compare` is what CI's exit code comes from, so a fabricated current map is
    the direct test: a workflow with an unguarded site and no freeze entry must
    be reported, and the message must name it.
    """
    new, stale = guard.compare({"999-brand-new.yml": ["job:output_file"]})
    assert any("999-brand-new.yml" in line for line in new), (
        f"an unguarded site outside the freeze was not reported: {new}"
    )
    assert any("NOT in KNOWN_UNGUARDED" in line for line in new), (
        f"the finding does not say why it is a finding: {new}"
    )


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

# Every case here is pure YAML/AST: no PowerShell host is required, so tool
# absence cannot turn "could not run" into "everything passed" (#1182). The
# behavioural half lives in the per-lane modules.
NEEDS_PWSH: set = set()

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
