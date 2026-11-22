"""Unit tests for 237 (application EIN check) and scripts/collect_site_eins.py.

237 runs in a PUBLIC repo with a WHMCS credential, so its wiring is pinned:

  * dispatch-only, read lane: the `application_eins` job alone holds an
    environment (`whmcs-prod-read`), with the READ-scoped credential
    (`scope: read` on the READ_ALL_FFC_AZURE_* identity, as 232);
  * the repo-cloning `site_eins` job holds no environment and no secret;
  * the callee's WHMCS actions are read actions only, and its report never
    writes an EIN field.

collect_site_eins.py is exercised on fixture repos: an EIN next to its label
counts, FFC's own EIN in template files does not (except on FFC's own repo),
and placeholders do not.
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import load_workflow  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = "237-whmcs-application-ein-check.yml"
CALLEE = ROOT / "scripts" / "whmcs-application-ein-check.ps1"
COLLECT = ROOT / "scripts" / "collect_site_eins.py"
READ_ACTIONS = {"GetClientsProducts", "GetClientsDetails"}


def wf() -> dict:
    return load_workflow(WORKFLOW)


def test_dispatch_only():
    on = wf().get(True, wf().get("on"))
    assert set(on) == {"workflow_dispatch"}, on


def test_only_the_whmcs_job_has_an_environment_and_it_is_the_read_lane():
    jobs = wf()["jobs"]
    assert "environment" not in jobs["site_eins"]
    assert jobs["application_eins"]["environment"] == "whmcs-prod-read"
    assert jobs["application_eins"]["permissions"] == {"contents": "read", "id-token": "write"}
    assert wf()["permissions"] == {"contents": "read"}


def test_whmcs_credential_is_read_scoped():
    steps = wf()["jobs"]["application_eins"]["steps"]
    kv = [s for s in steps if str(s.get("uses", "")).endswith("whmcs-secrets-from-kv")]
    assert len(kv) == 1
    w = kv[0]["with"]
    assert w["scope"] == "read"
    assert "READ_ALL_FFC_AZURE_KV_CLIENT_ID" in w["azure-client-id"]
    assert "READ_ALL_FFC_AZURE_TENANT_ID" in w["azure-tenant-id"]


def test_site_job_uses_no_secret():
    text = json.dumps(wf()["jobs"]["site_eins"])
    assert "secrets." not in text and "vars." not in text


def test_callee_sends_read_actions_only():
    src = CALLEE.read_text(encoding="utf-8")
    actions = set(re.findall(r"\.action\s*=\s*'([A-Za-z]+)'", src))
    assert actions and actions <= READ_ACTIONS, actions


def test_report_row_has_no_ein_field():
    src = CALLEE.read_text(encoding="utf-8")
    # The row literal ends at its own closing brace, alone on its line.
    row = re.split(r"\n\s*\}\s*\n", src.split("[pscustomobject][ordered]@{", 1)[1], maxsplit=1)[0]
    keys = re.findall(r"^\s*([a-z_]+)\s*=", row, re.M)
    assert "order_id" in keys and "finding" in keys, keys
    # has_ein is a yes/no; no key may carry the EIN itself.
    assert not [k for k in keys if (k == "ein" or k.endswith("_ein")) and k != "has_ein"], keys


def _run_collect(repos: dict[str, dict[str, str]]) -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "repos"
        for repo, files in repos.items():
            for rel, body in files.items():
                p = root / repo / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(body, encoding="utf-8")
        out = pathlib.Path(tmp) / "site-eins.json"
        r = subprocess.run([sys.executable, str(COLLECT), str(root), str(out)], capture_output=True, text=True, encoding="utf-8")
        assert r.returncode == 0, r.stderr
        return json.loads(out.read_text(encoding="utf-8"))


def test_collect_reads_labelled_eins_and_config_keys():
    rows = _run_collect({
        "FFC-EX-example.org": {"src/lib/site.config.ts": "  ein: '12-3456780',\n"},
        "FFC-EX-other.org": {"src/components/footer.tsx": "<p>Other Org EIN: 98-7654320</p>"},
    })
    by = {r["repo"]: r for r in rows}
    assert by["FFC-EX-example.org"]["eins"] == ["123456780"]
    assert by["FFC-EX-example.org"]["domain"] == "example.org"
    assert by["FFC-EX-other.org"]["eins"] == ["987654320"]


def test_collect_ignores_ffc_ein_outside_ffc_repo_and_placeholders():
    rows = _run_collect({
        "FFC-EX-charity.org": {"ADOPTERS.md": "Free For Charity EIN 46-2471893", "a.ts": "ein: '12-3456789'"},
        "FFC-EX-freeforcharity.org": {"a.ts": "ein: '46-2471893'"},
        "FFC-EX-blank.org": {"README.md": "no tax id here"},
    })
    by = {r["repo"]: r for r in rows}
    assert by["FFC-EX-charity.org"]["eins"] == []
    assert by["FFC-EX-freeforcharity.org"]["eins"] == ["462471893"]
    assert by["FFC-EX-blank.org"]["eins"] == []


def test_collect_skips_node_modules():
    rows = _run_collect({"FFC-EX-x.org": {"node_modules/pkg/a.ts": "ein: '12-3456780'"}})
    assert rows[0]["eins"] == []


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

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
