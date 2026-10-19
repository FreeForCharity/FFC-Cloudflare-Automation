"""701's `skip_dns` input (epic #1618).

A charity whose domain is already in FFC Cloudflare AND serving a live site must get
its repo on the default GitHub Pages URL without 701 repointing the apex. Before this
input, 701's `dns` job was the only path to `repo`, and the charity-lane gate script
correctly refuses that gate for a live site, so the run stalled with no repo.

`skip_dns` works by making `zone_check` report the zone as not controlled for the
run. Every DNS-touching job and the CNAME choice already key off that one output,
which these tests pin, so the override cannot miss one of them.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import find_step, load_workflow  # noqa: E402

WORKFLOW = "701-website-provision.yml"


def _wf() -> dict:
    return load_workflow(WORKFLOW)


def test_input_is_dispatch_only_and_defaults_off():
    on = _wf().get(True) or _wf().get("on")
    spec = on["workflow_dispatch"]["inputs"]["skip_dns"]
    assert spec["type"] == "boolean" and spec["default"] is False


def test_zone_check_reads_it_through_env_only():
    step = find_step(_wf(), "zone_check", "zone check")
    assert step["env"]["SKIP_DNS"] == "${{ inputs.skip_dns }}"
    assert "${{" not in step["run"]
    body = step["run"]
    assert "[string]$env:SKIP_DNS -eq 'true' -and $controlled" in body
    # the override comes after the real computation and before the outputs are written
    assert body.index("$controlled = ($existsFfc -or $existsCm)") < body.index("$env:SKIP_DNS -eq 'true'")
    assert body.index("$env:SKIP_DNS -eq 'true'") < body.index("-Name 'zone_controlled'")


def test_every_dns_touching_job_keys_off_zone_controlled():
    jobs = _wf()["jobs"]
    for job in ("dns_preview", "dns", "verify"):
        cond = " ".join(str(jobs[job].get("if", "")).split())
        assert "needs.zone_check.outputs.zone_controlled == 'true'" in cond, job


def test_the_custom_domain_choice_keys_off_zone_controlled():
    repo_steps = _wf()["jobs"]["repo"]["steps"]
    texts = [s.get("run", "") for s in repo_steps]
    create = next(t for t in texts if "$createParams.CNAME = $domain" in t)
    assert "$zoneControlled = ([string]$env:ZONE_CONTROLLED) -eq 'true'" in create
    assert create.index("if ($zoneControlled) {") < create.index("$createParams.CNAME = $domain")
    envs = [s.get("env", {}) for s in repo_steps if "$createParams.CNAME = $domain" in s.get("run", "")]
    assert envs[0]["ZONE_CONTROLLED"] == "${{ needs.zone_check.outputs.zone_controlled }}"


def test_override_behaviour_in_pwsh():
    if shutil.which("pwsh") is None:
        return  # CI runners have pwsh; the structural tests above still pin the wiring
    body = find_step(_wf(), "zone_check", "zone check")["run"]
    start = body.index("if ([string]$env:SKIP_DNS")
    end = body.index("}\n", body.index("$controlled = $false")) + 2
    snippet = body[start:end]
    for skip, existing, expect in (("true", "$true", "False"), ("false", "$true", "True"), ("", "$true", "True"),
                                   ("true", "$false", "False")):
        script = (
            f"$env:SKIP_DNS='{skip}'; $domain='a.org'; $existsFfc={existing}; $existsCm=$false; "
            f"$controlled = ($existsFfc -or $existsCm)\n{snippet}\n[string]$controlled"
        )
        out = subprocess.run(["pwsh", "-NoProfile", "-Command", script], capture_output=True, text=True,
                             encoding="utf-8", timeout=60).stdout.strip().splitlines()[-1]
        assert out == expect, (skip, existing, out)


def main():
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS {name}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"  FAIL {name}: {e!r}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
