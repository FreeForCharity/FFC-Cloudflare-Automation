"""The 24 burnt-down call sites, exercised as PowerShell (#1150 burn-down).

`test_1150_empty_input_guard.py` proves the CHECKER classifies these sites as
guarded. That is a statement about a static analyser, not about the workflows:
a checker with a too-permissive guard predicate reports a clean tree for a tree
full of holes, and it reports it in the reassuring direction. This module closes
that gap by running the real `run:` bodies out of the committed workflow files
and watching them refuse.

Every case therefore asserts BOTH directions, because a rule tested only in the
direction it passes is green and wrong (#1027):

* **variable removed** -> the body exits 1, emits its own `::error::<VAR> is
  empty` line, and the callee is never reached;
* **variable present** -> that line is absent and the callee IS reached.

The second half is what makes the first half's "callee not reached" mean
anything. Without it, a body that could never reach its callee under this
harness -- a wrong stub path, a missing `RUNNER_TEMP` -- would satisfy the
removal case for the wrong reason and score as a guard that works.

Three mechanics carried over from the sibling module, each of which has cost a
previous session real time:

* **"Empty" is always REMOVAL, never `$env:X = ''`.** On Windows an assigned
  empty string leaves the variable present and the hazard does not reproduce,
  so a control written that way measures every site as safe on the platform
  these workflows actually run on (#1150, pitfall 3). `env.pop` is unambiguous
  on both hosts.
* **The environment is inherited and then layered**, never a fresh minimal dict
  (ledger L35).
* **A non-zero exit code is never asserted on its own** (CLAUDE.md): a harness
  that cannot start also exits non-zero, so every failure case also asserts the
  guard's own text AND that PowerShell parsed the body. `ParserError` is
  explicitly excluded, because pwsh echoes the offending source line back on a
  parse error -- which can put the very marker being searched for into stdout
  for a reason that has nothing to do with the guard.

The callee is a STUB with no `param()` block, so it binds anything and reports
what it received. That is deliberate: argument-BINDING semantics are already
measured in `test_1146_empty_input_argument_binding.py` and
`test_1150_empty_input_guard.py`, and re-measuring them here would let a
binding change masquerade as a guard failure. What this module measures is
reachability -- did control get past the guard, yes or no.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import REPO_ROOT  # noqa: E402

CHECKER = REPO_ROOT / "scripts" / "check-workflow-empty-input-guard.py"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"


def _load():
    spec = importlib.util.spec_from_file_location("check_wf_empty_input_bd", CHECKER)
    assert spec is not None and spec.loader is not None, (
        f"cannot import the checker at {CHECKER} — the path is not an importable "
        "Python source file. If it was renamed, update CHECKER above."
    )
    module = importlib.util.module_from_spec(spec)
    # Registered BEFORE exec: `@dataclass` resolves the class's own module out of
    # `sys.modules`, and without this the import dies inside `dataclasses` with an
    # error naming neither this file nor the checker.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


guard = _load()

HAVE_PWSH = guard.find_powershell() is not None

FAIL_CLOSED = "fail-closed"
DEFAULT_FILL = "default-fill"

# The 24 references frozen by #1150 and fixed here, keyed by STEP rather than by
# (workflow, variable): 111, 122 and 124 each run the SAME body on a read lane
# and a write lane, so collapsing them to one key would assert `preview` and
# silently drop `apply` — the lane that actually writes.
#
# The last column is the remedy, and it is not cosmetic: a default-fill site
# must NOT emit an error when its variable is missing, so asserting the
# fail-closed shape against it would be asserting the wrong contract.
SITES = [
    ("111-dns-create-redirect-rule.yml", "preview", "Preview redirect rule", "INPUT_SOURCE", FAIL_CLOSED),
    ("111-dns-create-redirect-rule.yml", "preview", "Preview redirect rule", "INPUT_TARGET", FAIL_CLOSED),
    ("111-dns-create-redirect-rule.yml", "apply", "Apply redirect rule", "INPUT_SOURCE", FAIL_CLOSED),
    ("111-dns-create-redirect-rule.yml", "apply", "Apply redirect rule", "INPUT_TARGET", FAIL_CLOSED),
    ("122-cloudflare-zone-member-add.yml", "preview", "Preview zone member invite", "INPUT_DOMAIN", FAIL_CLOSED),
    ("122-cloudflare-zone-member-add.yml", "preview", "Preview zone member invite", "INPUT_EMAIL", FAIL_CLOSED),
    ("122-cloudflare-zone-member-add.yml", "apply", "Invite zone member", "INPUT_DOMAIN", FAIL_CLOSED),
    ("122-cloudflare-zone-member-add.yml", "apply", "Invite zone member", "INPUT_EMAIL", FAIL_CLOSED),
    ("124-cloudflare-cache-purge.yml", "preview", "Preview purge payload", "INPUT_DOMAIN", FAIL_CLOSED),
    ("124-cloudflare-cache-purge.yml", "preview", "Preview purge payload", "INPUT_URLS", FAIL_CLOSED),
    ("124-cloudflare-cache-purge.yml", "apply", "Purge cache", "INPUT_DOMAIN", FAIL_CLOSED),
    ("124-cloudflare-cache-purge.yml", "apply", "Purge cache", "INPUT_URLS", FAIL_CLOSED),
    ("125-cloudflare-cache-rules-audit.yml", "audit", "Audit cache rules", "INPUT_DOMAIN", FAIL_CLOSED),
    ("205-whmcs-ticket-open.yml", "open_ticket", "Open ticket", "TICKET_SUBJECT", FAIL_CLOSED),
    ("205-whmcs-ticket-open.yml", "open_ticket", "Open ticket", "TICKET_MESSAGE", FAIL_CLOSED),
    ("211-whmcs-order-update.yml", "order_update", "Update order", "ACTION", FAIL_CLOSED),
    ("230-whmcs-record-field-set.yml", "record_field_set", "Set record field", "TARGET", FAIL_CLOSED),
    ("230-whmcs-record-field-set.yml", "record_field_set", "Set record field", "FIELD", FAIL_CLOSED),
    ("230-whmcs-record-field-set.yml", "record_field_set", "Set record field", "VALUE", FAIL_CLOSED),
    ("305-m365-add-tenant-domain.yml", "add-tenant-domain", "Add domain + print DNS", "INPUT_DOMAIN", FAIL_CLOSED),
    ("503-google-gtm-provision.yml", "provision", "Provision GTM container", "DOMAIN", FAIL_CLOSED),
    ("503-google-gtm-provision.yml", "provision", "Provision GTM container", "MEASUREMENT_ID", FAIL_CLOSED),
    ("503-google-gtm-provision.yml", "provision", "Provision GTM container", "ACCOUNT_ID", DEFAULT_FILL),
    ("505-google-ga-property-provision.yml", "provision", "Provision GA4 property", "DOMAIN", FAIL_CLOSED),
]

# `account_id` is `required: false` and declares this default in the workflow's
# own `inputs:` block. The default-fill remedy must reproduce THAT value: a
# guard that invents a different account id fails closed in form and provisions
# into the wrong GTM account in fact.
GTM_ACCOUNT_DEFAULT = "4702611686"

SENTINEL = "1"

# Values the runner provides that are not dispatch inputs. Without them the
# bodies fail for reasons that have nothing to do with the guard — and they fail
# AFTER it, so their absence would only ever corrupt the variable-present half.
RUNNER_ENV = {
    "GRAPH_ACCESS_TOKEN": "stub-token",
    "GH_TOKEN": "stub-token",
}

_PS1_REF = re.compile(r"scripts[/\\]([A-Za-z0-9._-]+\.ps1)")

# No `param()` block: the stub binds anything, so a binding failure can never be
# mistaken for the guard refusing.
STUB = """Write-Output "CALLED $($args -join ' ')"
"""

# `az` runs before the callee in 503/505 and its exit code is checked. A stub
# that exits 0 keeps the variable-present half reaching the callee without any
# network call or credential — this module reads nothing from Key Vault.
AZ_STUB = """#!/bin/sh
exit 0
"""
AZ_STUB_CMD = """@echo off
exit /b 0
"""


def _unit_for(workflow: str, job: str, step_substring: str):
    """The one `run:` body that owns a site, or an explanatory assertion."""
    path = WORKFLOWS / workflow
    assert path.is_file(), f"{workflow} does not exist — the table names a deleted file"
    units = guard.pwsh_units(path.read_text(encoding="utf-8"), workflow)
    matches = [
        unit for unit in units if unit.job == job and step_substring in unit.step
    ]
    assert len(matches) == 1, (
        f"{workflow} :: {job} :: …{step_substring}… matched {len(matches)} PowerShell "
        f"bodies, expected exactly 1. Zero means the step was renamed or its `env:` "
        f"mapping dropped, so every assertion below would be measuring nothing; more "
        f"than one means the substring stopped identifying a single call site"
    )
    return matches[0]


def _run_unit(unit, *, remove: str | None) -> tuple[str, int]:
    """Run a real `run:` body with one mapped variable removed (or none)."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        (tmp / "scripts").mkdir()
        callees = sorted(set(_PS1_REF.findall(unit.text)))
        assert callees, (
            f"{unit.id} references no `scripts/*.ps1`, so there is no callee whose "
            f"reachability this module could measure"
        )
        for name in callees:
            (tmp / "scripts" / name).write_text(STUB, encoding="utf-8")

        binroot = tmp / "bin"
        binroot.mkdir()
        (binroot / "az").write_text(AZ_STUB, encoding="utf-8")
        (binroot / "az").chmod(0o755)
        (binroot / "az.cmd").write_text(AZ_STUB_CMD, encoding="utf-8")

        script = tmp / "step.ps1"
        script.write_text(unit.text, encoding="utf-8")

        env = dict(os.environ)
        env.update(RUNNER_ENV)
        for name in unit.env:
            env[name] = SENTINEL
        env["RUNNER_TEMP"] = str(tmp)
        env["GITHUB_STEP_SUMMARY"] = str(tmp / "summary.md")
        env["GITHUB_ENV"] = str(tmp / "env.txt")
        env["PATH"] = f"{binroot}{os.pathsep}{env.get('PATH', '')}"
        if remove is not None:
            env.pop(remove, None)

        proc = subprocess.run(
            [guard.find_powershell(), "-NoProfile", "-File", str(script)],
            cwd=tmp,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=180,
        )
        return proc.stdout + proc.stderr, proc.returncode


def _assert_parsed(out: str, where: str) -> None:
    """A parse failure exits non-zero AND echoes the source line back.

    Both of those are things this module otherwise reads as evidence, so a body
    that will not parse can satisfy "rc == 1" and "the marker is in stdout" for
    a reason that has nothing to do with the guard (ledger L203, one layer up).
    """
    assert "ParserError" not in out and "ParseException" not in out, (
        f"{where}: PowerShell could not parse the body, so nothing below is a "
        f"measurement of the guard: {out}"
    )


# --------------------------------------------------------------------------
# The table itself, before anything is run with it.
# --------------------------------------------------------------------------


def test_the_table_covers_every_site_the_freeze_used_to_hold():
    assert len(SITES) == 24, (
        f"the table lists {len(SITES)} sites; #1150 froze 24, and a short table is "
        f"a burn-down assertion that silently covers less than it claims"
    )
    assert len({(w, j, s, v) for w, j, s, v, _ in SITES}) == 24, (
        "the table has duplicate keys, so its length overstates its coverage"
    )
    write_lane_workflows = {w for w, *_ in SITES} - {"125-cloudflare-cache-rules-audit.yml"}
    assert len(write_lane_workflows) == 9, (
        f"expected 9 workflows with a write lane, got {sorted(write_lane_workflows)}"
    )


def test_the_freeze_no_longer_holds_any_of_them():
    """The other direction of the burn-down: nothing here is still deferred."""
    for workflow, _job, _step, variable, _remedy in SITES:
        assert workflow not in guard.KNOWN_UNGUARDED, (
            f"{workflow} is still frozen although this module asserts its "
            f"{variable} reference fails closed — the freeze and the tree disagree"
        )


def test_every_site_resolves_to_exactly_one_body():
    for workflow, job, step, _variable, _remedy in SITES:
        _unit_for(workflow, job, step)


def test_every_named_variable_is_actually_mapped_in_that_body():
    """Guards against a table that names a variable the step does not carry."""
    for workflow, job, step, variable, _remedy in SITES:
        unit = _unit_for(workflow, job, step)
        assert variable in unit.env, (
            f"{unit.id} does not map {variable}; the `env:` block carries "
            f"{sorted(unit.env)}. A guard on an unmapped name can never fire"
        )


# --------------------------------------------------------------------------
# The payloads. Both directions, per site.
# --------------------------------------------------------------------------


def test_each_site_fails_closed_when_its_variable_is_removed():
    for workflow, job, step, variable, remedy in SITES:
        if remedy != FAIL_CLOSED:
            continue
        unit = _unit_for(workflow, job, step)
        where = f"{workflow} :: {job} :: {variable}"
        out, rc = _run_unit(unit, remove=variable)
        _assert_parsed(out, where)
        assert rc == 1, (
            f"{where} did not fail closed (rc={rc}). Exit 0 is the dangerous "
            f"outcome: a native command's failure does not propagate through "
            f"`$ErrorActionPreference = 'Stop'`, so the step would go green having "
            f"run nothing: {out}"
        )
        assert f"::error::{variable} is empty" in out, (
            f"{where} exited 1 without naming {variable}. Exit 1 alone cannot "
            f"distinguish the guard from a broken harness (CLAUDE.md): {out}"
        )
        assert "CALLED" not in out, (
            f"{where} reached the callee anyway, so the guard is decorative: {out}"
        )


def test_each_site_does_not_fire_when_its_variable_is_present():
    """Polarity. Also what makes 'CALLED not in out' above mean anything."""
    for workflow, job, step, variable, _remedy in SITES:
        unit = _unit_for(workflow, job, step)
        where = f"{workflow} :: {job} :: {variable}"
        out, rc = _run_unit(unit, remove=None)
        _assert_parsed(out, where)
        assert f"::error::{variable} is empty" not in out, (
            f"{where} refused a NON-empty value, so the guard fires on correct "
            f"input — the failure direction that gets a guard switched off: {out}"
        )
        assert "CALLED" in out, (
            f"{where} never reached its callee even with every variable set, so "
            f"the removal case's 'CALLED not in out' proves nothing about the "
            f"guard (rc={rc}): {out}"
        )


def test_the_default_fill_site_supplies_the_workflows_own_default():
    """503's `account_id`: `required: false`, so the remedy is a value, not a stop.

    Asserted on the value that reaches the callee rather than on the absence of
    an error, because "no error" is also what a guard that does nothing at all
    produces.
    """
    sites = [row for row in SITES if row[4] == DEFAULT_FILL]
    assert len(sites) == 1, f"expected exactly one default-fill site, got {sites}"
    workflow, job, step, variable, _remedy = sites[0]
    unit = _unit_for(workflow, job, step)

    out, rc = _run_unit(unit, remove=variable)
    _assert_parsed(out, f"{workflow} :: {variable} removed")
    assert f"::error::{variable} is empty" not in out, (
        f"{variable} is `required: false` with a default; failing closed on it "
        f"makes an optional input mandatory: {out}"
    )
    assert f"-AccountId {GTM_ACCOUNT_DEFAULT}" in out, (
        f"the missing {variable} did not default-fill to the workflow's own "
        f"declared default {GTM_ACCOUNT_DEFAULT} (rc={rc}): {out}"
    )

    out, rc = _run_unit(unit, remove=None)
    _assert_parsed(out, f"{workflow} :: {variable} present")
    assert f"-AccountId {SENTINEL}" in out, (
        f"a supplied {variable} was overwritten by the default, so the fill is "
        f"unconditional rather than a fallback: {out}"
    )


def test_a_removed_variable_is_not_the_same_as_an_assigned_empty_string():
    """Why every case above uses `env.pop` (#1150, pitfall 3).

    On Windows `$env:X = ''` deletes nothing and the hazard does not reproduce;
    on Linux `SetEnvironmentVariable(name, "")` removes the variable, so the two
    scenarios collapse into one. Pinned here so that a future rewrite to the
    convenient `= ''` form is a red test rather than a silent loss of coverage
    on the platform these workflows run on.
    """
    workflow, job, step, variable, _remedy = SITES[0]
    unit = _unit_for(workflow, job, step)
    out, rc = _run_unit(unit, remove=variable)
    assert rc == 1 and f"::error::{variable} is empty" in out, (
        f"removal did not reproduce the empty case, so the pitfall this test "
        f"documents cannot be demonstrated: {out}"
    )


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

# Everything that runs a payload needs a PowerShell host. Refusing to report a
# pass is the same posture the checker itself takes: this module will not
# certify what it did not measure.
PWSH_REQUIRED = {
    "test_each_site_fails_closed_when_its_variable_is_removed",
    "test_each_site_does_not_fire_when_its_variable_is_present",
    "test_the_default_fill_site_supplies_the_workflows_own_default",
    "test_a_removed_variable_is_not_the_same_as_an_assigned_empty_string",
}


def main() -> int:
    failures = 0
    for test in TESTS:
        name = test.__name__
        if name in PWSH_REQUIRED and not HAVE_PWSH:
            print(f"  SKIP {name}: no PowerShell host on PATH (runs in CI)")
            continue
        try:
            test()
        except AssertionError as error:
            failures += 1
            print(f"  FAIL {name}: {str(error)[:400]}")
        else:
            print(f"  PASS {name}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
