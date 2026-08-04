r"""Unit tests for scripts/check-pwsh-workflow-invocations.py (#1051).

The guard exists because the seam between a workflow's inline pwsh and this
repo's PowerShell scripts had no tests at all. `Update-CloudflareDns.ps1` has 263
Pester cases; the ~20 places a workflow decides how to *call* it had zero, and
that is where PR #1050 broke — totally, every job, every input combination:

    $argsList = @('-Zone', $Domain, '-EnforceStandard')
    & .\Update-CloudflareDns.ps1 @argsList

Array splatting binds POSITIONALLY, so '-Zone' arrives as a value.

BOTH POLARITIES, PER CALL SHAPE
    The rule is not "splatting is suspicious" — splatting is how nearly every
    call site in this tree passes its arguments, and three of the four
    combinations are correct. Measured on PowerShell 7.6.4 against a stub with
    `param([string]$Zone, [switch]$DryRun)`:

        & ./t.ps1 @array          -> Zone='-Zone' DryRun=False   BROKEN, exit 0
        & ./t.ps1 @hashtable      -> Zone='abc'   DryRun=True     correct
        pwsh -File ./t.ps1 @array -> Zone='abc'   DryRun=True     correct
        pwsh -File ./t.ps1 @hash  -> Zone='abc'   DryRun=True     correct

    A guard that flagged the shape without the position would report ~20
    correct call sites, and would be switched off within a month (ledger L09).
    So each of those four rows is a test here.

THE FIRST ROW IS WHY THE SHAPE IS A FINDING AT ALL
    It exits 0. The stronger "bind it against the real param() block and see
    whether it throws" check — which this guard also performs, and which is what
    #1051's AC5 asked for — CANNOT see it, because a mis-bind is still a bind.
    #1050 failed loudly only because `Update-CloudflareDns.ps1` happens to have
    no free positional slot. `test_the_array_splat_defect_binds_silently_on_a_
    positional_target` pins that measurement so nobody later "simplifies" the
    shape rule away as redundant.

THE LOAD-BEARING TEST
    `test_the_106_call_sites_in_the_array_form_are_caught` reverts the real
    `106-enforce-standard.yml` to the #1050 form — in a COPY of the workflow
    directory, so the author's file is never mutated (#1051 AC6: #1050 owns that
    file) — and requires the finding. Without it this module only asserts a
    happy path, which a scanner returning `[]` unconditionally would pass.

PowerShell dependency: only PowerShell can parse PowerShell, so the AST-backed
tests need a host. CI always has one (722-ci.yml already runs several
`shell: pwsh` steps and the #930 guard). Where none is present they report SKIP
rather than a false red, per CLAUDE.md.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check-pwsh-workflow-invocations.py"

_SPEC = importlib.util.spec_from_file_location("check_pwsh_workflow_invocations", SCRIPT)
guard = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
sys.modules[_SPEC.name] = guard
_SPEC.loader.exec_module(guard)

PWSH = guard.find_powershell()
SKIPPED: list[str] = []


class Skip(Exception):
    """Raised by an AST-backed test when no PowerShell host is available."""


def require_pwsh() -> str:
    if not PWSH:
        raise Skip("no PowerShell host")
    return PWSH


def scan_body(body: str, name: str = "999-fixture.yml") -> list:
    """Findings for a single fixture `run:` block, written as a real workflow.

    The fixture goes through the same YAML -> step -> AST path as production;
    calling the internals directly would test a shortcut nothing takes.
    """
    require_pwsh()
    workflow = {
        "name": "999. Fixture",
        "on": {"workflow_dispatch": {}},
        "jobs": {
            "fixture": {
                "runs-on": "ubuntu-latest",
                "steps": [{"name": "Call the script", "shell": "pwsh", "run": body}],
            }
        },
    }
    with tempfile.TemporaryDirectory() as tmp:
        directory = pathlib.Path(tmp)
        (directory / name).write_text(yaml.safe_dump(workflow), encoding="utf-8")
        findings, _ = guard.scan_repo(workflows=directory, pwsh=PWSH)
    return findings


def kinds(findings: list) -> list[str]:
    return [finding.kind for finding in findings]


# --- the four call shapes, measured ----------------------------------------


def test_an_array_splat_into_a_script_invocation_is_a_finding():
    """Row 1: the #1050 defect itself."""
    findings = scan_body(
        "$argsList = @('-Zone', 'example.org', '-EnforceStandard')\n"
        "& .\\Update-CloudflareDns.ps1 @argsList\n"
    )
    assert kinds(findings) == [guard.ARRAY_SPLAT], [str(f) for f in findings]


def test_the_finding_names_the_flag_string_that_becomes_a_value():
    """The negative control's message has to discriminate, not merely be red.

    #1051's own comment records a harness that reported all nine shapes as
    unbindable and read as a find-everything success; the discriminator was one
    word of the message — `argument '-Zone'` (a flag arriving as a value) versus
    `argument 'System.Collections.Hashtable'` (a broken harness). This asserts
    the guard's message carries the same discriminator.
    """
    findings = scan_body(
        "$argsList = @('-Zone', 'example.org', '-EnforceStandard')\n"
        "& .\\Update-CloudflareDns.ps1 @argsList\n"
    )
    detail = findings[0].detail
    assert "'-Zone'" in detail, detail
    assert "Update-CloudflareDns.ps1" in detail, detail
    assert "hashtable" in detail.lower(), f"the message must name the fix: {detail}"


def test_a_hashtable_splat_into_a_script_invocation_is_clean():
    """Row 2: the correct form, and the one the fix for #1050 uses."""
    findings = scan_body(
        "$params = @{ Zone = 'example.org'; EnforceStandard = $true }\n"
        "& .\\Update-CloudflareDns.ps1 @params\n"
    )
    assert findings == [], [str(f) for f in findings]


def test_an_array_splat_into_a_native_pwsh_host_is_clean():
    """Row 3: `pwsh -File x.ps1 @args` is argv, where an array is correct.

    This is the majority form in this tree (~20 sites). A guard that flagged it
    would be reporting correct code, which is how guards get switched off.
    """
    findings = scan_body(
        "$scriptArgs = @('-Zone', 'example.org')\n"
        "$scriptArgs += '-EnforceStandard'\n"
        "& pwsh -NoProfile -File .\\Update-CloudflareDns.ps1 @scriptArgs\n"
    )
    assert findings == [], [str(f) for f in findings]


def test_a_hashtable_splat_into_a_native_pwsh_host_is_clean():
    """Row 4: renders as -Key:Value, which the child pwsh binds by name."""
    findings = scan_body(
        "$params = @{ Zone = 'example.org'; EnforceStandard = $true }\n"
        "& pwsh -NoProfile -File .\\Update-CloudflareDns.ps1 @params\n"
    )
    assert findings == [], [str(f) for f in findings]


def test_the_array_splat_defect_binds_silently_on_a_positional_target():
    """The measurement the shape rule rests on: a mis-bind is still a bind.

    If this ever stops being true, the shape rule becomes redundant with the
    binding check and could be dropped. While it holds, dropping it would lose
    the only detection of the silent case — the dangerous one.
    """
    pwsh = require_pwsh()
    with tempfile.TemporaryDirectory() as tmp:
        target = pathlib.Path(tmp) / "t.ps1"
        target.write_text(
            "param([string]$Zone, [switch]$DryRun)\n"
            "Write-Host \"BOUND Zone='$Zone' DryRun=$DryRun\"\n",
            encoding="utf-8",
        )
        proc = subprocess.run(
            [
                pwsh,
                "-NoProfile",
                "-Command",
                f"$a = @('-Zone','abc','-DryRun'); & '{target.as_posix()}' @a",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    assert proc.returncode == 0, f"expected a SILENT mis-bind: {proc.stdout}{proc.stderr}"
    assert "BOUND Zone='-Zone'" in proc.stdout, proc.stdout
    assert "DryRun=False" in proc.stdout, proc.stdout


# --- the load-bearing mutation (#1051 AC4) ----------------------------------


def _mutated_106() -> tuple[str, list[str]]:
    """The real 106, with both pwsh steps rewritten to the #1050 array form.

    Returns the YAML and the step names mutated, so the caller can assert the
    plant landed rather than trusting a red run (AGENTS.md: prove the plant).
    """
    source = REPO_ROOT / ".github" / "workflows" / "106-enforce-standard.yml"
    doc = yaml.safe_load(source.read_text(encoding="utf-8"))
    mutated: list[str] = []
    for job in doc["jobs"].values():
        for step in job.get("steps", []):
            if step.get("shell") != "pwsh" or "Update-CloudflareDns.ps1" not in step.get("run", ""):
                continue
            step["run"] = (
                "$Domain = 'example.org'\n"
                "$argsList = @('-Zone', $Domain, '-EnforceStandard')\n"
                'Write-Host "Update-CloudflareDns.ps1 $($argsList -join \' \')"\n'
                "& .\\Update-CloudflareDns.ps1 @argsList\n"
            )
            mutated.append(step["name"])
    return yaml.safe_dump(doc), mutated


def test_the_106_call_sites_in_the_array_form_are_caught():
    """Revert 106 to what #1050 shipped and require the finding, by step name.

    Mutates a COPY of the workflow directory: #1050 owns the real file and a
    second PR touching it would conflict on Clarke's branch (#1051 AC6).
    """
    require_pwsh()
    mutated_yaml, mutated_steps = _mutated_106()
    assert len(mutated_steps) == 2, (
        f"expected to plant the array form in 2 pwsh steps of 106, planted "
        f"{len(mutated_steps)}: {mutated_steps}. A green run below would prove "
        f"nothing if the plant did not land."
    )

    with tempfile.TemporaryDirectory() as tmp:
        directory = pathlib.Path(tmp)
        for path in (REPO_ROOT / ".github" / "workflows").glob("*.yml"):
            shutil.copy2(path, directory / path.name)
        (directory / "106-enforce-standard.yml").write_text(mutated_yaml, encoding="utf-8")
        findings, _ = guard.scan_repo(workflows=directory, pwsh=PWSH)

    array_splats = [f for f in findings if f.kind == guard.ARRAY_SPLAT]
    assert len(array_splats) == 2, [str(f) for f in findings]
    for finding in array_splats:
        assert finding.workflow == "106-enforce-standard.yml", str(finding)
    named = {step for step in mutated_steps for f in array_splats if step in f.step}
    assert named == set(mutated_steps), (
        f"every mutated step must be named in a finding; named {named}, mutated {mutated_steps}"
    )


def test_the_real_106_is_untouched_by_this_module():
    """The mutation above must never reach the working tree (#1051 AC6)."""
    real = (REPO_ROOT / ".github" / "workflows" / "106-enforce-standard.yml").read_text(
        encoding="utf-8"
    )
    assert "@argsList" not in real, (
        "106-enforce-standard.yml carries the array-splat form in the working "
        "tree — either #1050 landed and this test needs rewriting against a "
        "different fixture, or a mutation was written back."
    )


# --- the general binding check (#1051 AC5) ----------------------------------


def test_a_parameter_the_target_does_not_have_is_a_finding():
    """The general form: bind against the real param() block, not a pattern."""
    findings = scan_body(
        "$params = @{ Zone = 'example.org'; EnforceStandrd = $true }\n"
        "& .\\Update-CloudflareDns.ps1 @params\n"
    )
    assert guard.UNKNOWN_PARAMETER in kinds(findings), [str(f) for f in findings]
    assert "-EnforceStandrd" in findings[0].detail, findings[0].detail


def test_a_parameter_name_written_out_is_bound_too():
    """Not only splatted keys: `-Foo` as written is checked the same way."""
    findings = scan_body("& .\\Update-CloudflareDns.ps1 -Zone 'example.org' -Enfrce\n")
    assert guard.UNKNOWN_PARAMETER in kinds(findings), [str(f) for f in findings]


def test_a_combination_no_parameter_set_accepts_is_a_finding():
    """`-Audit` and `-EnforceStandard` live in different sets of the real script.

    This is the class that killed run 30876332723 — every name resolves, and the
    call is still unbindable.
    """
    findings = scan_body(
        "& .\\Update-CloudflareDns.ps1 -Zone 'example.org' -Audit -EnforceStandard\n"
    )
    assert guard.NO_PARAMETER_SET in kinds(findings), [str(f) for f in findings]


def test_a_legitimate_parameter_set_combination_is_clean():
    """The other polarity: 106's real arguments must not be reported."""
    findings = scan_body(
        "& .\\Update-CloudflareDns.ps1 -Zone 'example.org' -EnforceStandard "
        "-GitHubPagesOnly -ProxyGitHubPages -DryRun\n"
    )
    assert findings == [], [str(f) for f in findings]


def test_flags_inside_a_native_array_splat_are_bound_as_well():
    """`pwsh -File x.ps1 @args` is correct SHAPE, and its flags are still names.

    The array is argv for the child host, so a typo in one of its flag strings
    is the same defect as a typo in a hashtable key — and the shape rule, which
    deliberately says nothing about native calls, would miss it.
    """
    findings = scan_body(
        "$scriptArgs = @('-Zone', 'example.org', '-EnfrceStandard')\n"
        "& pwsh -NoProfile -File .\\Update-CloudflareDns.ps1 @scriptArgs\n"
    )
    assert guard.UNKNOWN_PARAMETER in kinds(findings), [str(f) for f in findings]


def test_the_hosts_own_flags_are_not_attributed_to_the_script():
    """`-NoProfile` and `-File` belong to pwsh, not to the target script.

    Attributing them would report every native call site as passing unknown
    parameters — the false-positive flood that gets a guard disabled.
    """
    findings = scan_body(
        "& pwsh -NoProfile -NonInteractive -File .\\Update-CloudflareDns.ps1 "
        "-Zone 'example.org' -Audit\n"
    )
    assert findings == [], [str(f) for f in findings]


# --- failing closed ---------------------------------------------------------


def test_a_splat_that_cannot_be_classified_fails_closed():
    """An unreadable splat is the blind spot, so it is a finding, not a skip."""
    findings = scan_body(
        "$params = Get-SomethingFromSomewhere\n& .\\Update-CloudflareDns.ps1 @params\n"
    )
    assert kinds(findings) == [guard.UNRESOLVED_SPLAT], [str(f) for f in findings]


def test_a_run_body_that_does_not_parse_is_a_finding():
    """Partial facts from a broken parse would read as 'nothing to see here'."""
    findings = scan_body("$x = @{\n& .\\Update-CloudflareDns.ps1 @x\n")
    assert kinds(findings) == [guard.UNPARSEABLE], [str(f) for f in findings]


def test_a_missing_powershell_host_fails_closed():
    """No host means no verdict — never a pass. Needs no host to run."""
    try:
        guard.scan_repo(pwsh=None if PWSH is None else "definitely-not-a-shell")
    except RuntimeError as error:
        assert "PowerShell" in str(error) or "pwsh" in str(error), str(error)
        return
    except FileNotFoundError:
        return  # the fake host resolved to nothing; also a refusal, also fine
    raise AssertionError("scan_repo returned a verdict with no PowerShell host")


# --- the plumbing the findings' line numbers rest on ------------------------


def test_masking_a_github_expression_preserves_the_line_count():
    """A multi-line `${{ }}` must not shift every line number below it."""
    text = "$a = 1\n$b = '${{ inputs.one\n  && inputs.two }}'\n$c = 3\n"
    masked = guard.mask_expressions(text)
    assert masked.count("\n") == text.count("\n"), masked
    assert "${{" not in masked, masked
    assert masked.splitlines()[3] == "$c = 3", masked.splitlines()


def test_a_step_inherits_pwsh_from_job_defaults():
    """A job-level `defaults.run.shell` is how several workflows set pwsh once."""
    workflow = yaml.safe_dump(
        {
            "name": "999. Fixture",
            "on": {"workflow_dispatch": {}},
            "jobs": {
                "fixture": {
                    "runs-on": "ubuntu-latest",
                    "defaults": {"run": {"shell": "pwsh"}},
                    "steps": [{"name": "Call", "run": "$x = 1\n"}],
                }
            },
        }
    )
    units = guard.pwsh_units(workflow, "999-fixture.yml")
    assert len(units) == 1, units
    assert units[0].job == "fixture", units


# --- the guard has to actually run ------------------------------------------


def test_the_real_tree_is_clean():
    """The guard as wired into CI, over the repository as it stands."""
    require_pwsh()
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "pwsh invocation binding OK" in proc.stdout, proc.stdout


def test_the_guard_is_wired_into_ci():
    """A checker nobody runs is the failure mode this whole class is about."""
    ci = (REPO_ROOT / ".github" / "workflows" / "722-ci.yml").read_text(encoding="utf-8")
    assert "scripts/check-pwsh-workflow-invocations.py" in ci, (
        "check-pwsh-workflow-invocations.py must run in 722-ci.yml, "
        "or the rule is documentation"
    )


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except Skip as e:
            SKIPPED.append(t.__name__)
            print(f"  SKIP {t.__name__}: {e} (CI always has one; see module docstring)")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {str(e)[:2000]}")
    if SKIPPED:
        print(f"  {len(SKIPPED)} test(s) skipped for want of a PowerShell host.")
    sys.exit(1 if failures else 0)
