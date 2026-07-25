"""Unit tests for the 321 KV credential liveness + expiry decision logic.

The workflow's github-script step `require`s scripts/kv-credential-liveness-lib.js,
so exercising that module directly tests the shipped logic. What matters here is
not "does it notice a 401" but the two things #848 proved can go wrong silently:

  1. A credential that could NOT be evaluated must never read as healthy — the
     #726/#855 lesson applied to credentials. Every path that fails to produce a
     verdict has a test asserting `hasFinding`.
  2. This workflow runs ungated on a READ lane and handles credential material,
     so the read-all-only probe registry is asserted structurally, not by
     reading the file and trusting it.

Shape tests cover the workflow wiring the library cannot see: the host allowlist
in the YAML, the absence of a gated environment, and value-leak surfaces.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import load_workflow

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
LIB = REPO_ROOT / "scripts" / "kv-credential-liveness-lib.js"
WF_FILE = "321-azure-kv-credential-liveness.yml"
WF_RAW = (REPO_ROOT / ".github" / "workflows" / WF_FILE).read_text(encoding="utf-8")

NOW = "2026-07-25T00:00:00Z"


def _node(expr_body: str, *argv: str, expect_fail: bool = False):
    code = f"const l=require({json.dumps(str(LIB))});{expr_body}"
    proc = subprocess.run(
        ["node", "-e", code, *argv], capture_output=True, text=True, timeout=60
    )
    if expect_fail:
        assert proc.returncode != 0, f"expected a throw, got: {proc.stdout}"
        return proc.stderr
    if proc.returncode != 0:
        raise AssertionError(f"node failed: {proc.stderr}")
    return json.loads(proc.stdout)


def analyze(**kwargs) -> dict:
    kwargs.setdefault("now", NOW)
    return _node(
        "process.stdout.write(JSON.stringify(l.analyze(JSON.parse(process.argv[1]))));",
        json.dumps(kwargs),
    )


def classify(result: dict) -> str:
    return _node(
        "process.stdout.write(JSON.stringify(l.classifyProbe(JSON.parse(process.argv[1]))));",
        json.dumps(result),
    )


def render(analysis: dict) -> str:
    return _node(
        "process.stdout.write(JSON.stringify(l.renderBody(JSON.parse(process.argv[1]), process.argv[2])));",
        json.dumps(analysis),
        NOW,
    )


def const(name: str):
    return _node(f"process.stdout.write(JSON.stringify(l.{name}));")


def assert_probes(probes: list, expect_fail: bool = False):
    return _node(
        "process.stdout.write(JSON.stringify(l.assertNoWriteScopeProbes(JSON.parse(process.argv[1]))));",
        json.dumps(probes),
        expect_fail=expect_fail,
    )


def _secret(name: str, expires: str | None = "2027-02-21T00:00:00Z", enabled: bool = True) -> dict:
    return {"name": name, "expires": expires, "enabled": enabled, "updated": "2026-05-16"}


PAT = "read-all-cbm-github-pat"
CF = "read-all-ffc-cloudflare-api-token-zone-and-dns"


def _live_pat(secret: str = PAT) -> dict:
    return {"secret": secret, "kind": "github-pat", "httpStatus": 200}


def _live_cf(secret: str = CF) -> dict:
    return {
        "secret": secret,
        "kind": "cloudflare-token",
        "httpStatus": 200,
        "cloudflareStatus": "active",
    }


# --- probe classification --------------------------------------------------


def test_a_200_is_live():
    assert classify(_live_pat()) == "LIVE"


def test_the_848_finding_a_401_pat_is_dead():
    # The exact observation in #848: `read-all-cbm-github-pat` -> 401.
    assert classify({"secret": PAT, "kind": "github-pat", "httpStatus": 401}) == "DEAD"


def test_a_403_pat_is_dead():
    assert classify({"secret": PAT, "kind": "github-pat", "httpStatus": 403}) == "DEAD"


def test_a_cloudflare_400_is_dead_not_unverified():
    # Cloudflare answers an invalid token with 400/code 1000, not 401. Reading
    # that as "unclassified" would downgrade a dead token to a soft warning.
    assert classify({"secret": CF, "kind": "cloudflare-token", "httpStatus": 400}) == "DEAD"


def test_a_cloudflare_200_that_is_not_active_is_dead():
    r = dict(_live_cf(), cloudflareStatus="disabled")
    assert classify(r) == "DEAD"


def test_a_cloudflare_200_with_no_status_is_unverified_not_live():
    r = dict(_live_cf())
    r.pop("cloudflareStatus")
    assert classify(r) == "UNVERIFIED"


def test_a_500_is_unverified_not_dead_and_not_live():
    assert classify({"secret": PAT, "kind": "github-pat", "httpStatus": 500}) == "UNVERIFIED"


def test_a_429_is_unverified():
    assert classify({"secret": PAT, "kind": "github-pat", "httpStatus": 429}) == "UNVERIFIED"


def test_a_transport_error_is_unverified():
    r = {"secret": PAT, "kind": "github-pat", "httpStatus": None, "error": "probe request failed"}
    assert classify(r) == "UNVERIFIED"


def test_an_unreadable_secret_is_unverified():
    r = {"secret": PAT, "kind": "github-pat", "error": "key vault read failed: Forbidden"}
    assert classify(r) == "UNVERIFIED"


def test_an_unknown_kind_is_unverified_rather_than_assumed_live():
    assert classify({"secret": PAT, "kind": "smtp-password", "httpStatus": 200}) == "UNVERIFIED"


# --- analyze: the clean state ---------------------------------------------


def test_all_live_and_far_from_expiry_is_no_finding():
    a = analyze(
        secrets=[_secret(PAT, "2028-01-01T00:00:00Z"), _secret(CF, None)],
        probes=[_live_pat(), _live_cf()],
    )
    assert a["hasFinding"] is False, a
    assert len(a["live"]) == 2, a
    # A secret with no expiry date is informational, not a finding.
    assert [s["name"] for s in a["noExpiry"]] == [CF], a


def test_a_secret_with_no_expiry_does_not_raise_a_finding():
    a = analyze(secrets=[_secret(PAT, None)], probes=[_live_pat()])
    assert a["hasFinding"] is False, a


# --- analyze: every unevaluable path is a finding -------------------------


def test_a_dead_probe_is_a_finding():
    a = analyze(
        secrets=[_secret(PAT, "2028-01-01T00:00:00Z")],
        probes=[{"secret": PAT, "kind": "github-pat", "httpStatus": 401}],
    )
    assert a["hasFinding"] is True, a
    assert [d["secret"] for d in a["dead"]] == [PAT], a
    # ...and it is NOT also counted as live.
    assert a["live"] == [], a


def test_an_unverifiable_probe_is_a_finding_not_a_pass():
    a = analyze(
        secrets=[_secret(PAT, "2028-01-01T00:00:00Z")],
        probes=[{"secret": PAT, "kind": "github-pat", "httpStatus": 503}],
    )
    assert a["hasFinding"] is True, a
    assert [d["secret"] for d in a["unverified"]] == [PAT], a


def test_a_probe_target_absent_from_the_vault_is_a_finding():
    a = analyze(secrets=[_secret("some-other-secret", None)], probes=[_live_pat()])
    assert a["hasFinding"] is True, a
    assert [d["secret"] for d in a["missing"]] == [PAT], a
    assert a["live"] == [], a


def test_a_listing_failure_is_a_finding_even_with_every_probe_live():
    a = analyze(secrets=[], probes=[_live_pat(), _live_cf()], listError="AuthorizationFailed")
    assert a["hasFinding"] is True, a
    assert a["listError"] == "AuthorizationFailed", a


def test_a_listing_failure_does_not_fabricate_missing_secrets():
    # Without a listing, presence is unknowable; reporting four `missing`
    # credentials would bury the one real finding under four invented ones.
    a = analyze(secrets=[], probes=[_live_pat(), _live_cf()], listError="AuthorizationFailed")
    assert a["missing"] == [], a
    assert len(a["live"]) == 2, a


def test_no_probes_at_all_still_reports_expiry_findings():
    a = analyze(secrets=[_secret(PAT, "2026-07-30T00:00:00Z")], probes=[])
    assert a["hasFinding"] is True, a
    assert [s["name"] for s in a["expiring"]] == [PAT], a


# --- analyze: expiry ------------------------------------------------------


def test_expiry_inside_the_window_is_a_finding_with_days_left():
    a = analyze(secrets=[_secret(PAT, "2026-08-10T00:00:00Z")], probes=[_live_pat()])
    assert a["hasFinding"] is True, a
    assert a["expiring"][0]["daysToExpiry"] == 16, a


def test_an_already_expired_secret_is_expired_not_expiring():
    a = analyze(secrets=[_secret(PAT, "2026-07-01T00:00:00Z")], probes=[_live_pat()])
    assert [s["name"] for s in a["expired"]] == [PAT], a
    assert a["expiring"] == [], a


def test_the_boundary_day_warns_rather_than_reading_as_healthy():
    warn = const("EXPIRY_WARN_DAYS")
    a = analyze(secrets=[_secret(PAT, "2026-08-24T00:00:00Z")], probes=[_live_pat()])
    assert warn == 30, warn
    assert a["expiring"][0]["daysToExpiry"] == 30, a
    assert a["hasFinding"] is True, a


def test_one_day_past_the_window_is_healthy():
    a = analyze(secrets=[_secret(PAT, "2026-08-25T00:00:00Z")], probes=[_live_pat()])
    assert a["expiring"] == [], a
    assert a["hasFinding"] is False, a


def test_the_two_pats_sharing_an_expiry_are_both_reported():
    # #848: both working PATs expire 2027-02-21 and will die together, so the
    # report must name each one rather than collapsing to a single warning.
    both = ["wr-all-cbm-github-pat", "wr-all-cbm-ffc-copilot-mcp-github-pat"]
    a = analyze(
        secrets=[_secret(n, "2026-08-01T00:00:00Z") for n in both],
        probes=[],
        now=NOW,
    )
    assert sorted(s["name"] for s in a["expiring"]) == sorted(both), a


def test_a_disabled_secret_is_reported_separately_from_expiry():
    a = analyze(secrets=[_secret(PAT, "2028-01-01T00:00:00Z", enabled=False)], probes=[_live_pat()])
    assert [s["name"] for s in a["disabled"]] == [PAT], a
    assert a["expiring"] == [] and a["healthy"] == [], a
    assert a["hasFinding"] is True, a


# --- the read-all-only probe registry (security invariant) ----------------


def test_the_shipped_registry_probes_read_all_secrets_only():
    probes = const("PROBES")
    assert probes, probes
    offenders = [p for p in probes if not p["secret"].startswith("read-all-")]
    assert not offenders, offenders


def test_the_registry_covers_both_dead_pats_from_848():
    named = {p["secret"] for p in const("PROBES")}
    assert "read-all-cbm-github-pat" in named, named
    assert "read-all-cbm-ffc-copilot-mcp-github-pat" in named, named


def test_a_write_scope_probe_target_is_refused():
    # The guard the workflow calls BEFORE reading any secret. `wr-all-cbm-github-pat`
    # carries repo create/archive/collaborator scope over 100+ repos; it must
    # never reach an ungated scheduled workflow through this registry.
    err = assert_probes(
        [{"secret": "wr-all-cbm-github-pat", "kind": "github-pat"}], expect_fail=True
    )
    assert "read-all-" in err, err


def test_a_valid_registry_passes_the_guard():
    assert assert_probes(const("PROBES")) == const("PROBES")


def test_an_unknown_kind_is_refused_by_the_guard():
    err = assert_probes([{"secret": "read-all-x", "kind": "ssh-key"}], expect_fail=True)
    assert "unknown probe kind" in err, err


def test_every_probe_url_is_https_on_an_allowlisted_host():
    kinds = const("PROBE_KINDS")
    for name, k in kinds.items():
        assert k["url"].startswith("https://"), (name, k)
        host = k["url"].split("/")[2]
        assert host in ("api.github.com", "api.cloudflare.com"), (name, host)


def test_the_workflow_allowlist_agrees_with_the_library_hosts():
    """The library names the probe URL; the workflow re-validates the host before
    sending a credential. If the two drift, either a credential goes to a host
    the workflow never vetted, or a legitimate probe fails — so assert agreement
    rather than trusting one side."""
    hosts = {k["url"].split("/")[2] for k in const("PROBE_KINDS").values()}
    step = next(
        s
        for s in load_workflow(WF_FILE)["jobs"]["monitor"]["steps"]
        if "probe liveness" in (s.get("name") or "")
    )
    assert "$allowedHosts" in step["run"], step["run"][:200]
    for h in hosts:
        assert f"'{h}'" in step["run"], (h, "missing from the workflow allowlist")


# --- report body ----------------------------------------------------------


def test_the_body_carries_the_marker_so_the_issue_is_a_rolling_one():
    a = analyze(secrets=[_secret(PAT, "2026-07-01T00:00:00Z")], probes=[])
    assert const("MARKER") in render(a)


def test_the_body_names_the_dead_credential_and_its_status():
    a = analyze(
        secrets=[_secret(PAT, "2028-01-01T00:00:00Z")],
        probes=[{"secret": PAT, "kind": "github-pat", "httpStatus": 401}],
    )
    body = render(a)
    assert PAT in body, body
    assert "401" in body, body
    assert "Dead" in body, body


def test_the_body_distinguishes_unverifiable_from_dead():
    a = analyze(
        secrets=[
            _secret(PAT, "2028-01-01T00:00:00Z"),
            _secret(CF, "2028-01-01T00:00:00Z"),
        ],
        probes=[
            {"secret": PAT, "kind": "github-pat", "httpStatus": 401},
            {"secret": CF, "kind": "cloudflare-token", "httpStatus": 503},
        ],
    )
    body = render(a)
    assert "Unverifiable" in body, body
    assert "not** evidence of a healthy one" in body, body


def test_the_body_states_the_read_only_probe_scope():
    a = analyze(secrets=[_secret(PAT, "2026-07-01T00:00:00Z")], probes=[])
    assert "read-all-" in render(a), "the report must say which scope is probed"


def test_a_listing_failure_says_the_run_is_incomplete_not_clean():
    body = render(analyze(secrets=[], probes=[], listError="AuthorizationFailed"))
    assert "could not be listed" in body, body
    assert "not clean" in body, body


# --- workflow shape -------------------------------------------------------


def test_the_workflow_is_scheduled_and_dispatchable():
    on = load_workflow(WF_FILE).get(True, load_workflow(WF_FILE).get("on"))
    assert "schedule" in on, on
    # A monitor that cannot be run on demand cannot be proven to work, which is
    # how the 740 alerter stayed dead for six days (#843).
    assert "workflow_dispatch" in on, on


def test_the_cron_collides_with_no_other_hub_schedule():
    mine = {
        s["cron"]
        for s in load_workflow(WF_FILE).get(True, load_workflow(WF_FILE).get("on"))["schedule"]
    }
    others = set()
    for f in sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml")):
        if f.name == WF_FILE:
            continue
        raw = f.read_text(encoding="utf-8-sig")
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("- cron:"):
                others.add(line.split("cron:", 1)[1].strip().strip("'\"").split("#")[0].strip())
    minutes_mine = {c.split()[0] for c in mine}
    minutes_others = {c.split()[0] for c in others}
    assert minutes_mine.isdisjoint(minutes_others), (minutes_mine & minutes_others, mine)


def test_the_monitor_is_never_gated():
    """A credential-health monitor behind an approval gate reports nothing until
    a human happens to approve it — the #834 failure class, and worse here since
    the whole point is catching a silent credential death."""
    env = load_workflow(WF_FILE)["jobs"]["monitor"]["environment"]
    gated = {
        "cloudflare-prod",
        "cloudflare-prod-write",
        "github-prod",
        "m365-prod",
        "whmcs-prod",
        "wpmudev-prod",
    }
    assert env not in gated, env


def test_the_sweep_is_not_cancel_in_progress():
    # A cancelled half-sweep leaves the credentials it never reached unchecked
    # while the run still reads as "handled" (#834's self-cancelling crons).
    assert load_workflow(WF_FILE)["concurrency"]["cancel-in-progress"] is False


def test_the_workflow_masks_before_it_uses_a_fetched_value():
    step = next(
        s
        for s in load_workflow(WF_FILE)["jobs"]["monitor"]["steps"]
        if "probe liveness" in (s.get("name") or "")
    )
    run = step["run"]
    assert "::add-mask::" in run, run[:200]
    assert run.index("::add-mask::") < run.index("Authorization = "), (
        "the value must be masked before it is put in a header"
    )


def test_the_workflow_never_prints_or_persists_a_value():
    run = next(
        s
        for s in load_workflow(WF_FILE)["jobs"]["monitor"]["steps"]
        if "probe liveness" in (s.get("name") or "")
    )["run"]
    # Every line touching the fetched value must be an assignment, a mask, the
    # newline trim, the header, or the null-out — never an output sink.
    sinks = ("Out-File", "Set-Content", "GITHUB_OUTPUT", "GITHUB_STEP_SUMMARY", "ConvertTo-Json")
    for line in run.splitlines():
        if "$value" not in line:
            continue
        assert not any(s in line for s in sinks), f"value reaches an output sink: {line.strip()}"
        if "Write-Host" in line:
            assert "::add-mask::" in line, f"value printed unmasked: {line.strip()}"
    # The payload handed to the reporting step carries names and statuses only.
    assert "value" not in run.split("$payload = ")[1], run.split("$payload = ")[1][:300]


def test_the_trailing_newline_guard_is_present():
    # 227's lesson: a stored credential can carry CRLF, which turns a live token
    # into a 401 and would be misreported here as a dead credential.
    run = next(
        s
        for s in load_workflow(WF_FILE)["jobs"]["monitor"]["steps"]
        if "probe liveness" in (s.get("name") or "")
    )["run"]
    assert "TrimEnd([char]13, [char]10)" in run, run[:200]


def test_the_registry_guard_runs_before_any_secret_is_read():
    run = next(
        s
        for s in load_workflow(WF_FILE)["jobs"]["monitor"]["steps"]
        if "probe liveness" in (s.get("name") or "")
    )["run"]
    assert run.index("assertNoWriteScopeProbes") < run.index("keyvault secret show"), (
        "the read-all-only guard must gate the first secret read"
    )


def test_the_header_never_wraps_mid_hyphenated_word():
    # The catalog generator joins header comment lines with a space, so a word
    # split across the wrap ships to the public catalog as `read- only` (#840).
    offenders = [
        ln for ln in WF_RAW.splitlines() if ln.startswith("#") and ln.rstrip().endswith("-")
    ]
    assert not offenders, offenders


def test_the_workflow_has_a_safety_table_row():
    doc = (REPO_ROOT / "docs" / "workflow-safety-and-approvals.md").read_text(encoding="utf-8")
    assert "| 321 " in doc, "add a row to docs/workflow-safety-and-approvals.md"


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
