"""701 content step: the call-to-action issue for fields awaiting the charity.

When the content script leaves footer-standard fields empty (never Free For
Charity's values) it lists them as pending. 701 then opens an issue on the new
FFC-EX-<domain> repo asking the charity's technical POC for them, and reports
the list and the issue in its outputs. These tests run the REAL tail of the
`apply` step (extracted from the YAML) under pwsh, with `gh` replaced by a
PowerShell function that records its calls, so no network is touched.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import child_env, load_workflow  # noqa: E402

WF = load_workflow("701-website-provision.yml")
STEP = next(s for s in WF["jobs"]["content"]["steps"] if s.get("id") == "apply")
BODY = STEP["run"]
TAIL_START = "          # Call to action: ask the charity's technical POC for every pending field,"


def tail() -> str:
    # The run: body is dedented by YAML; find the block by its first line's text.
    marker = TAIL_START.strip()
    at = BODY.index(marker)
    return BODY[at:]


FAKE_GH = r"""
function gh {
  $all = @($args)
  Add-Content -LiteralPath $env:TEST_GH_LOG -Value (($all | ForEach-Object { [string]$_ }) -join ' ')
  $bf = [array]::IndexOf($all, '--body-file')
  if ($bf -ge 0) { Copy-Item -LiteralPath $all[$bf + 1] -Destination $env:TEST_BODY_COPY -Force }
  $global:LASTEXITCODE = 0
  if ($all[0] -eq 'issue' -and $all[1] -eq 'list') { return $env:TEST_EXISTING }
  if ($all[0] -eq 'issue' -and $all[1] -eq 'create') {
    if ($env:TEST_CREATE_FAILS -eq '1') { $global:LASTEXITCODE = 1; return 'boom' }
    return 'https://github.com/FreeForCharity/FFC-EX-iwilf.example/issues/7'
  }
}
"""


def run_tail(
    *,
    status: str = "applied",
    pending: list[str] | None = None,
    rendered: bool = False,
    poc: str = "iwilf-poc",
    dry_run: str = "false",
    existing: str = "[]",
    create_fails: bool = False,
) -> dict:
    pending = ["phone", "address", "guidestar"] if pending is None else pending
    with tempfile.TemporaryDirectory() as td:
        tdp = pathlib.Path(td)
        out = tdp / "github_output"
        out.write_text("", encoding="utf-8")
        log = tdp / "gh.log"
        body_copy = tdp / "body.md"
        prelude = "\n".join(
            [
                "$ErrorActionPreference = 'Stop'",
                f"$workDir = '{td}'",
                "$repoFull = 'FreeForCharity/FFC-EX-iwilf.example'",
                "$domain = 'iwilf.example'",
                "$charityName = 'Interpreters Legacy Test Foundation'",
                f"$technicalPoc = '{poc}'",
                f"$contentStatus = '{status}'",
                "$pendingFields = @(" + ", ".join(f"'{p}'" for p in pending) + ")",
                f"$pendingRendered = ${'true' if rendered else 'false'}",
                FAKE_GH,
            ]
        )
        script = tdp / "tail.ps1"
        script.write_text(prelude + "\n" + tail(), encoding="utf-8")
        env = child_env(
            GITHUB_OUTPUT=str(out),
            DRY_RUN=dry_run,
            TEST_GH_LOG=str(log),
            TEST_BODY_COPY=str(body_copy),
            TEST_EXISTING=existing,
            TEST_CREATE_FAILS="1" if create_fails else "0",
        )
        proc = subprocess.run(
            ["pwsh", "-NoProfile", "-NonInteractive", "-File", str(script)],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
        outputs = {}
        for line in out.read_text(encoding="utf-8-sig").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                outputs[k] = v
        return {
            "rc": proc.returncode,
            "log": proc.stdout + proc.stderr,
            "outputs": outputs,
            "gh": log.read_text(encoding="utf-8").splitlines() if log.exists() else [],
            "body": body_copy.read_text(encoding="utf-8-sig") if body_copy.exists() else "",
        }


def test_the_step_reports_pending_fields_and_issue_url_as_outputs():
    # Static wiring: outputs declared on the job and read by finalize.
    outputs = WF["jobs"]["content"]["outputs"]
    for name in ("content_pending_fields", "content_pending_rendered", "content_pending_issue_url"):
        assert outputs[name] == "${{ steps.apply.outputs.%s }}" % name, outputs
    finalize = next(s for s in WF["jobs"]["finalize"]["steps"] if s.get("name") == "Comment completion")
    for env_name, out in (
        ("CONTENT_PENDING_FIELDS", "content_pending_fields"),
        ("CONTENT_PENDING_RENDERED", "content_pending_rendered"),
        ("CONTENT_PENDING_ISSUE_URL", "content_pending_issue_url"),
    ):
        assert finalize["env"][env_name] == "${{ needs.content.outputs.%s }}" % out, finalize["env"]
    # ffc-content.json records the pending list.
    assert "$content.pendingFields = @($pendingFields)" in BODY, BODY
    assert BODY.index("$content.pendingFields") < BODY.index("Save-ContentRecord -Record $content")


def test_pending_fields_open_an_issue_mentioning_the_poc():
    r = run_tail()
    assert r["rc"] == 0, r["log"]
    assert r["outputs"]["content_pending_fields"] == "phone,address,guidestar", r["outputs"]
    assert r["outputs"]["content_pending_rendered"] == "false", r["outputs"]
    assert (
        r["outputs"]["content_pending_issue_url"]
        == "https://github.com/FreeForCharity/FFC-EX-iwilf.example/issues/7"
    ), r
    create = [c for c in r["gh"] if c.startswith("issue create")]
    assert len(create) == 1, r["gh"]
    assert "-R FreeForCharity/FFC-EX-iwilf.example" in create[0], create
    assert "--title Website details needed from Interpreters Legacy Test Foundation" in create[0], create
    body = r["body"]
    assert "@iwilf-poc" in body, body
    for field in ("`phone`", "`address`", "`guidestar`"):
        assert field in body, body
    assert "Public phone number" in body and "Candid / GuideStar profile link" in body, body
    assert "does not show an \"awaiting information\" placeholder yet" in body, body


def test_a_rendered_template_says_the_placeholder_is_showing():
    r = run_tail(rendered=True)
    assert r["rc"] == 0, r["log"]
    assert 'the site shows "Awaiting information from the charity" in its place' in r["body"], r["body"]


def test_a_rerun_refreshes_the_open_issue_instead_of_opening_another():
    existing = json.dumps(
        [
            {
                "number": 7,
                "title": "Website details needed from Interpreters Legacy Test Foundation",
                "url": "https://github.com/FreeForCharity/FFC-EX-iwilf.example/issues/7",
            }
        ]
    )
    r = run_tail(existing=existing, pending=["phone"])
    assert r["rc"] == 0, r["log"]
    lookup = [c for c in r["gh"] if c.startswith("issue list")]
    assert lookup and "--search in:title \"Website details needed from\"" in lookup[0], r["gh"]
    assert not [c for c in r["gh"] if c.startswith("issue create")], r["gh"]
    assert [c for c in r["gh"] if c.startswith("issue edit 7")], r["gh"]
    assert r["outputs"]["content_pending_issue_url"].endswith("/issues/7"), r["outputs"]


def test_no_issue_without_a_poc_on_a_dry_run_or_with_nothing_pending():
    for kwargs in (
        {"poc": ""},
        {"poc": "not a login!"},
        {"dry_run": "true"},
        {"pending": []},
        {"status": "failed"},
        {"status": "skipped"},
    ):
        r = run_tail(**kwargs)
        assert r["rc"] == 0, (kwargs, r["log"])
        assert r["gh"] == [], (kwargs, r["gh"])
        assert r["outputs"]["content_pending_issue_url"] == "", (kwargs, r["outputs"])


def test_a_failed_issue_create_is_non_fatal():
    r = run_tail(create_fails=True)
    assert r["rc"] == 0, r["log"]
    assert "Could not open the call-to-action issue" in r["log"], r["log"]
    assert r["outputs"]["content_status"] == "applied", r["outputs"]
    assert r["outputs"]["content_pending_issue_url"] == "", r["outputs"]
    assert r["outputs"]["content_pending_fields"] == "phone,address,guidestar", r["outputs"]


NEEDS_PWSH = {
    "test_pending_fields_open_an_issue_mentioning_the_poc",
    "test_a_rendered_template_says_the_placeholder_is_showing",
    "test_a_rerun_refreshes_the_open_issue_instead_of_opening_another",
    "test_no_issue_without_a_poc_on_a_dry_run_or_with_nothing_pending",
    "test_a_failed_issue_create_is_non_fatal",
}

TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    have_pwsh = shutil.which("pwsh") is not None
    failures = 0
    for t in TESTS:
        if t.__name__ in NEEDS_PWSH and not have_pwsh:
            print(f"  SKIP {t.__name__} (pwsh not installed; runs in CI)")
            continue
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {str(e)[:600]}")
    sys.exit(1 if failures else 0)
