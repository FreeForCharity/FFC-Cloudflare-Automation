"""Unit tests for scripts/charity_lane_gate.py (epic #1618).

The script is the only way the unattended Onboarding Conductor clears an
environment gate, so these tests pin the refusals: inputs outside a rule, an
ambiguous run, an environment the rule does not list, and a domain that already
serves a live site. No network: every check below runs on fixture data.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("gate", REPO_ROOT / "scripts" / "charity_lane_gate.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

POLICY = gate.load_policy()
T0 = dt.datetime(2026, 10, 11, 22, 0, 0, tzinfo=dt.timezone.utc)


def refused(rule_id, inputs):
    try:
        gate.validate_inputs(gate.find_rule(POLICY, rule_id), inputs)
    except gate.PolicyError:
        return True
    return False


def dep(env, can=True, env_id=1):
    return {"environment": {"name": env, "id": env_id}, "current_user_can_approve": can}


def run(rid, login="clarkemoyer", event="workflow_dispatch", created="2026-10-11T22:00:10Z"):
    return {"id": rid, "event": event, "actor": {"login": login}, "created_at": created}


# --- inputs ------------------------------------------------------------------


def test_226_approve_exactly_one_order():
    assert not refused("226-approve-one", {"mode": "approve", "orderids": "1234"})
    assert refused("226-approve-one", {"mode": "approve", "orderids": "1234,1240"})
    assert refused("226-approve-one", {"mode": "approve", "orderids": ""})


def test_226_reconcile_is_never_allowed():
    assert refused("226-approve-one", {"mode": "reconcile", "orderids": "1234"})
    assert refused("226-report", {"mode": "reconcile-report"})


def test_211_accept_only():
    assert not refused("211-accept-one", {"order_id": "77", "action": "accept", "dry_run": "false"})
    assert refused("211-accept-one", {"order_id": "77", "action": "cancel"})
    assert refused("211-accept-one", {"order_id": "77", "action": "fraud"})


def test_unlisted_input_is_refused():
    assert refused("211-accept-one", {"order_id": "77", "action": "accept", "api_url": "https://evil.example"})


def test_205_requires_allowlisted_template():
    assert not refused("205-templated", {"template": "onboarding_need_info", "client_id": "42", "extra_message": "Missing: organization LinkedIn page"})
    # Both allowed templates set extra_message_required, so a dispatch without it
    # would be approved into a guaranteed-failed run; refuse it at validation.
    assert refused("205-templated", {"template": "onboarding_need_info", "client_id": "42"})
    assert refused("205-templated", {"template": "preview_ready", "client_id": "42"})
    assert refused("205-templated", {"template": "need_info", "client_id": "42"})  # reply-only support template
    assert refused("205-templated", {"template": "international_techsoup", "client_id": "42"})
    assert refused("205-templated", {"subject": "hi", "message": "free text", "client_id": "42"})


def test_103_must_be_pages_only_without_m365():
    ok = {"domain": "newcharity.org", "github_pages_only": "true", "dry_run": "false"}
    assert not refused("103-pages-apex", ok)
    # M365 is opt-in, so omitting the flag is the safe default; saying so explicitly is too.
    assert not refused("103-pages-apex", {**ok, "manage_m365": "false"})
    assert refused("103-pages-apex", {**ok, "manage_m365": "true"})
    assert refused("103-pages-apex", {**ok, "github_pages_only": "false"})
    # The retired opt-out flag is no longer an input of 103, so it must not pass either.
    assert refused("103-pages-apex", {**ok, "skip_m365": "true"})


def test_domain_pattern_rejects_injection():
    assert refused("701-provision", {"domain": "x.org; rm -rf /"})
    assert refused("701-provision", {"domain": "UPPER.org"})
    assert not refused("701-provision", {"domain": "hope-house.org", "dry_run": "false"})


def test_701_template_allowlist():
    assert refused("701-provision", {"domain": "a.org", "template_repo": "someone/else"})


# --- run identification ------------------------------------------------------


def test_pick_run_needs_exactly_one_candidate():
    assert gate.pick_run([run(1)], "clarkemoyer", T0)["id"] == 1
    for runs in ([], [run(1), run(2)]):
        try:
            gate.pick_run(runs, "clarkemoyer", T0)
        except gate.PolicyError:
            continue
        raise AssertionError("ambiguous or missing run was accepted")


def test_pick_run_ignores_other_actors_events_and_old_runs():
    runs = [
        run(1, login="someone-else"),
        run(2, event="schedule"),
        run(3, created="2026-10-11T21:00:00Z"),
        run(4),
    ]
    assert gate.pick_run(runs, "clarkemoyer", T0)["id"] == 4


# --- live-site check ---------------------------------------------------------


def test_brand_new_domain_is_not_live():
    assert gate.classify_live_site([], [], [], None) == (
        False,
        "no live non-GitHub site (no foreign records, nothing else answering)",
    )


def test_github_pages_domain_is_not_live():
    live, _ = gate.classify_live_site(
        ["185.199.108.153"], ["freeforcharity.github.io."], [], {"status": 200, "server": "GitHub.com"}
    )
    assert not live


def test_foreign_a_record_is_live():
    assert gate.classify_live_site(["104.21.3.4"], [], [], None)[0]


def test_foreign_www_cname_is_live():
    assert gate.classify_live_site([], ["ext-cust.squarespace.com."], [], None)[0]


def test_non_github_server_is_live_even_without_records():
    assert gate.classify_live_site([], [], [], {"status": 200, "server": "cloudflare"})[0]


# --- approval decision -------------------------------------------------------


def test_decide_only_rule_environments():
    rule = gate.find_rule(POLICY, "226-approve-one")
    todo, why = gate.decide(rule, [dep("whmcs-prod"), dep("m365-prod")], None)
    assert [d["environment"]["name"] for d in todo] == ["whmcs-prod"]
    assert len(why) == 1 and why[0].startswith("m365-prod")


def test_decide_refuses_cloudflare_when_live():
    rule = gate.find_rule(POLICY, "701-provision")
    todo, why = gate.decide(rule, [dep("cloudflare-prod-write")], (True, "A records point outside GitHub Pages"))
    assert not todo and "live-site" in why[0]
    todo, _ = gate.decide(rule, [dep("cloudflare-prod-write")], (False, "ok"))
    assert len(todo) == 1


def test_decide_github_prod_needs_no_live_check():
    rule = gate.find_rule(POLICY, "701-provision")
    todo, _ = gate.decide(rule, [dep("github-prod")], (True, "live"))
    assert len(todo) == 1


def test_decide_refuses_when_not_a_reviewer():
    rule = gate.find_rule(POLICY, "211-accept-one")
    todo, why = gate.decide(rule, [dep("whmcs-prod", can=False)], None)
    assert not todo and "not a reviewer" in why[0]


def test_audit_redacts_contact_inputs():
    out = gate.audit_inputs({"domain": "a.org", "footer_email": "x@y.org", "message": "hello"})
    assert out == {"domain": "a.org", "footer_email": "<redacted>", "message": "<redacted>"}


def test_never_list_has_no_matching_rule():
    workflows = {r["workflow"] for r in POLICY["rules"]}
    assert "113-domain-register.yml" not in workflows
    for r in POLICY["rules"]:
        assert "m365-prod" not in r["environments"]
        if r["workflow"].startswith("120-"):
            # 120 only as the CNAME-only step for one new domain, never its DNS flip.
            assert r["environments"] == ["github-prod"]
            assert r["inputs"]["skip_dns"] == {"enum": ["true"]}
            assert "cloudflare-prod-write" not in r["environments"]


def test_707_rule_only_targets_ffc_ex_and_caps_payload():
    ok = {"target_repo": "FFC-EX-hope-house.org", "content_json": "{}", "dry_run": "false"}
    assert not refused("707-content-apply", ok)
    assert refused("707-content-apply", {**ok, "target_repo": "FFC-IN-ffcadmin.org"})
    assert refused("707-content-apply", {**ok, "target_repo": "FFC-Cloudflare-Automation"})
    assert refused("707-content-apply", {**ok, "content_json": "x" * 61441})
    assert refused("707-content-apply", {**ok, "issue_url": "https://evil.example/issues/1"})


def test_120_rule_is_one_domain_cname_only():
    ok = {"domains": "newcharity.org", "skip_dns": "true", "dry_run": "false"}
    assert not refused("120-cname-new-domain", ok)
    assert refused("120-cname-new-domain", {**ok, "skip_dns": "false"})
    assert refused("120-cname-new-domain", {**ok, "domains": "a.org,b.org"})
    assert refused("120-cname-new-domain", {**ok, "legacy_apex_ip": "1.2.3.4"})
    assert refused("120-cname-new-domain", {**ok, "skip_cname": "true"})


def test_120_github_prod_needs_the_live_site_check():
    rule = gate.find_rule(POLICY, "120-cname-new-domain")
    todo, why = gate.decide(rule, [dep("github-prod")], (True, "A records point outside GitHub Pages"))
    assert not todo and "live-site" in why[0]
    todo, _ = gate.decide(rule, [dep("github-prod")], (False, "ok"))
    assert len(todo) == 1


def test_dispatched_but_unidentified_exits_3_and_audits():
    calls = []

    def fake_gh(args, body=None):
        calls.append((args[0], body))
        if args[0] == "user":
            return {"login": "clarkemoyer"}
        if args[0].endswith("/runs?event=workflow_dispatch&per_page=20"):
            return {"workflow_runs": []}  # the new run never shows up
        return None

    real_gh, real_sleep = gate.gh, gate.time.sleep
    gate.gh, gate.time.sleep = fake_gh, lambda s: None
    try:
        rule = gate.find_rule(POLICY, "226-report")
        code = gate.cmd_run(POLICY, rule, {"mode": "report"}, timeout_s=1, audit=True)
    finally:
        gate.gh, gate.time.sleep = real_gh, real_sleep
    assert code == 3, code
    paths = [c[0] for c in calls]
    assert any(p.endswith("/dispatches") for p in paths)
    assert not any("pending_deployments" in p for p in paths)
    audit = [b for p, b in calls if p.endswith("/issues/1608/comments")]
    assert audit and "RUN NOT IDENTIFIED" in audit[0]["body"]


def test_live_site_subcommand_rejects_non_hostnames():
    assert gate.main(["live-site", "evil.com/x#"]) == 2
    assert gate.main(["live-site", "https://a.org"]) == 2


def test_751_rule_syncs_exactly_one_ffc_ex_repo():
    """The conductor acts per hub item, so one repo per dispatch; 751 itself
    accepts up to ten, which is a human's batch, not the lane's (#1644)."""
    ok = {"target_repos": "FFC-EX-hope-house.org", "dry_run": "false"}
    assert not refused("751-overrides-sync", ok)
    assert refused("751-overrides-sync", {"dry_run": "false"})
    assert refused("751-overrides-sync", {**ok, "target_repos": "FFC-EX-a.org,FFC-EX-b.org"})
    assert refused("751-overrides-sync", {**ok, "target_repos": "FFC-EX-a.org FFC-EX-b.org"})
    assert refused("751-overrides-sync", {**ok, "target_repos": "FFC-IN-ffcadmin.org"})
    assert refused("751-overrides-sync", {**ok, "target_repos": "FFC-Cloudflare-Automation"})
    assert refused("751-overrides-sync", {**ok, "dry_run": "maybe"})
    assert refused("751-overrides-sync", {**ok, "merge": "true"})


def test_751_rule_clears_only_github_prod_without_a_live_site_check():
    rule = gate.find_rule(POLICY, "751-overrides-sync")
    assert rule["environments"] == ["github-prod"]
    assert "live_site_check" not in rule
    todo, _ = gate.decide(rule, [dep("github-prod"), dep("cloudflare-prod-write", env_id=2)], None)
    assert [d["environment"]["name"] for d in todo] == ["github-prod"]


def test_every_rule_matches_its_workflow_dispatch_inputs():
    """The policy is the closed set the conductor dispatches verbatim; an input the
    workflow does not define makes GitHub refuse the dispatch (HTTP 422), and an
    environment the workflow never uses is dead policy. Pin both against the YAML."""
    import yaml

    for rule in POLICY["rules"]:
        wf_path = REPO_ROOT / ".github" / "workflows" / rule["workflow"]
        assert wf_path.exists(), f"{rule['id']}: {rule['workflow']} does not exist"
        wf = yaml.safe_load(wf_path.read_text(encoding="utf-8"))
        on = wf.get(True) or wf.get("on")
        defined = set(((on.get("workflow_dispatch") or {}).get("inputs") or {}).keys())
        extra = set(rule["inputs"]) - defined
        assert not extra, f"{rule['id']}: inputs not defined by {rule['workflow']}: {sorted(extra)}"
        envs = set()
        for job in wf["jobs"].values():
            env = job.get("environment")
            envs.add(env["name"] if isinstance(env, dict) else env)
        missing = set(rule["environments"]) - envs
        assert not missing, f"{rule['id']}: {rule['workflow']} never uses {sorted(missing)}"


# --- 753 gate broker: advance / sequence / JSON inputs (2026-11-12) ------------


def _report(rows, screened=True):
    return {"schema": "charity-lane-verdicts/v2", "fraud_check": "screened" if screened else "not_run", "verdicts": rows}


def _row(oid, gate=1, verdict="ACCEPT", fraud="pass"):
    return {"order_id": oid, "client_id": 9, "gate": gate, "verdict": verdict, "fraud_check": fraud}


def test_advance_accepts_only_screened_passing_accept_rows():
    plan = gate.select_accepts(
        POLICY,
        _report([_row(5), _row(6, verdict="NEED_INFO"), _row(7, fraud="review"), _row(8, verdict="ESCALATE"), _row(9, gate=2)]),
        3,
    )
    assert plan == [
        ("226-approve-one", {"mode": "approve", "orderids": "5"}),
        ("211-accept-one", {"order_id": "9", "action": "accept", "dry_run": "false"}),
    ], plan


def test_advance_refuses_an_unscreened_report():
    try:
        gate.select_accepts(POLICY, _report([_row(5)], screened=False), 3)
    except gate.PolicyError:
        return
    raise AssertionError("an unscreened verdicts report must never produce an accept")


def test_advance_caps_accepts_oldest_first():
    plan = gate.select_accepts(POLICY, _report([_row(40), _row(12), _row(31), _row(7)]), 3)
    assert [i["orderids"] for _, i in plan] == ["7", "12", "31"], plan


def test_advance_ignores_gate_zero_rows():
    assert gate.select_accepts(POLICY, _report([_row(5, gate=0)]), 3) == []


def test_policy_advance_cap_is_the_testing_period_limit():
    cap = POLICY["advance"]["max_accepts"]
    assert 1 <= cap <= 3, f"advance.max_accepts={cap}; raising it past 3 is a policy decision for Clarke"
    verdicts = gate.find_rule(POLICY, POLICY["advance"]["verdicts_rule"])
    assert verdicts["environments"] == [], "the verdicts rule must stay read-only (ungated)"


def test_every_sequence_step_validates_against_its_rule():
    for name, steps in POLICY["sequences"].items():
        for st in steps:
            gate.validate_inputs(gate.find_rule(POLICY, st["rule"]), dict(st.get("inputs") or {}))


def test_703_rule_only_runs_without_priming_601():
    assert not refused("703-generate", {"prime_wpmudev": "false"})
    assert refused("703-generate", {"prime_wpmudev": "true"})
    assert refused("703-generate", {})


def test_inputs_from_env_parses_a_json_object():
    import os

    os.environ["T_GATE"] = '{"domain": "example.org", "dry_run": false, "n": 3}'
    try:
        assert gate.inputs_from_env("T_GATE") == {"domain": "example.org", "dry_run": "false", "n": "3"}
        for bad in ("[1]", "not json", '{"a": {"b": 1}}', '{"a": null}'):
            os.environ["T_GATE"] = bad
            try:
                gate.inputs_from_env("T_GATE")
            except gate.PolicyError:
                continue
            raise AssertionError(f"inputs_from_env accepted {bad!r}")
    finally:
        os.environ.pop("T_GATE", None)


def test_sequence_dry_run_dispatches_nothing():
    import contextlib
    import io

    calls = []
    real = gate.gh
    gate.gh = lambda *a, **k: calls.append(a)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            assert gate.cmd_sequence(POLICY, "weekly-sites-list", True, 10) == 0
    finally:
        gate.gh = real
    assert calls == [], calls


def test_unknown_sequence_is_refused():
    try:
        gate.cmd_sequence(POLICY, "no-such-sequence", True, 10)
    except gate.PolicyError:
        return
    raise AssertionError("an unknown sequence name must be refused")


def test_only_753_uses_the_broker_environment():
    hits = []
    for wf in sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml")):
        if "charity-lane-broker" in wf.read_text(encoding="utf-8"):
            hits.append(wf.name)
    assert hits == ["753-charity-lane-gate-broker.yml"], (
        f"the ungated broker environment (it holds an approval-capable token) is named by {hits}; "
        "only 753 may use it"
    )


def test_753_refuses_non_main_refs_and_pins_its_schedules():
    text = (REPO_ROOT / ".github" / "workflows" / "753-charity-lane-gate-broker.yml").read_text(encoding="utf-8")
    assert 'if [ "$REF" != "refs/heads/main" ]' in text
    assert "ref: main" in text
    assert "'20 14 * * *') MODE=advance" in text
    assert "'10 8 * * 1')  MODE=sequence; RULE=weekly-sites-list" in text
    # inputs reach the script through env, never interpolated into the shell body
    body = text.split("- name: Broker", 1)[1]
    run_body = body.split("run: |", 1)[1]
    assert "${{" not in run_body, "753's Broker step must read inputs from env, not interpolate them into the script"


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
