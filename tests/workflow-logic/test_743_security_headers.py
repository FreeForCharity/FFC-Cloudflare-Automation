"""Unit tests for the 743 fleet security-header audit logic.

The workflow's github-script step `require`s scripts/fleet-security-header-lib.js,
so exercising that module directly tests the shipped logic: what counts as a
served header, which domains are in scope, and which buckets raise the rolling
issue.

The defect this audit exists to prevent (#884) is a check that reports coverage
which does not exist — `public/_headers` is present in every template and inert
on this stack. So the tests concentrate on the ways a probe could be read as
protected when it is not: a report-only CSP, a `<meta>` tag standing in for real
headers, an egress proxy's CONNECT block read as the site's response, and an
domain with no usable response scored as compliant.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import load_workflow

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
LIB = REPO_ROOT / "scripts" / "fleet-security-header-lib.js"
WF_FILE = "743-fleet-security-header-audit.yml"

FULL_SET = {
    "strict-transport-security": "max-age=31536000; includeSubDomains",
    "x-content-type-options": "nosniff",
    "x-frame-options": "SAMEORIGIN",
    "referrer-policy": "strict-origin-when-cross-origin",
    "permissions-policy": "geolocation=()",
    "content-security-policy": "default-src 'self'",
}


def _node(expr_body: str, *argv: str) -> object:
    code = f"const l=require({json.dumps(str(LIB))});{expr_body}"
    proc = subprocess.run(
        ["node", "-e", code, *argv],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed: {proc.stderr}")
    return json.loads(proc.stdout)


def parse_headers(raw: str) -> dict:
    return _node(
        "process.stdout.write(JSON.stringify(l.parseHeaderBlocks(process.argv[1])));", raw
    )


def classify(entry: dict) -> dict:
    return _node(
        "process.stdout.write(JSON.stringify(l.classify(JSON.parse(process.argv[1]))));",
        json.dumps(entry),
    )


def analyze(entries: list, skipped: list | None = None) -> dict:
    return _node(
        "process.stdout.write(JSON.stringify("
        "l.analyze(JSON.parse(process.argv[1]), JSON.parse(process.argv[2]))));",
        json.dumps(entries),
        json.dumps(skipped or []),
    )


def select_domains(records: list) -> dict:
    # Via a temp file, not argv: the real sites list is ~390 records and blows
    # past the exec argument limit.
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump(records, fh)
        path = fh.name
    try:
        return _node(
            "const fs=require('fs');process.stdout.write(JSON.stringify("
            "l.selectDomains(JSON.parse(fs.readFileSync(process.argv[1],'utf8')))));",
            path,
        )
    finally:
        pathlib.Path(path).unlink(missing_ok=True)


def render(analysis: dict, ts: str) -> str:
    return _node(
        "process.stdout.write(JSON.stringify("
        "l.renderBody(JSON.parse(process.argv[1]), process.argv[2])));",
        json.dumps(analysis),
        ts,
    )


def _probe(domain: str, headers: dict, **kw) -> dict:
    entry = {"domain": domain, "hostCategory": "GitHub Pages", "status": 200, "headers": headers}
    entry.update(kw)
    return entry


# --- header-block parsing --------------------------------------------------


def test_proxy_connect_block_is_not_mistaken_for_the_response():
    # An egress proxy prepends its own tunnel block, which carries no site
    # headers. Reading the FIRST block reports every proxied probe as bare —
    # the exact false finding this audit exists to avoid, inverted.
    raw = (
        "HTTP/1.1 200 Connection Established\r\n"
        "\r\n"
        "HTTP/2 200 \r\n"
        "server: cloudflare\r\n"
        "strict-transport-security: max-age=15552000\r\n"
    )
    parsed = parse_headers(raw)
    assert parsed["status"] == 200, parsed
    assert parsed["headers"].get("server") == "cloudflare", parsed
    assert parsed["headers"].get("strict-transport-security") == "max-age=15552000", parsed


def test_redirect_chain_is_judged_on_the_final_hop():
    raw = (
        "HTTP/2 301 \n"
        "location: https://www.example.org/\n"
        "server: cloudflare\n"
        "\n"
        "HTTP/2 200 \n"
        "server: GitHub.com\n"
        "x-content-type-options: nosniff\n"
    )
    parsed = parse_headers(raw)
    assert parsed["status"] == 200, parsed
    assert parsed["headers"].get("server") == "GitHub.com", parsed
    assert "location" not in parsed["headers"], parsed


def test_duplicate_header_takes_the_last_value():
    parsed = parse_headers("HTTP/2 200 \nreferrer-policy: no-referrer\nReferrer-Policy: origin\n")
    assert parsed["headers"].get("referrer-policy") == "origin", parsed


def test_empty_response_parses_to_no_status():
    assert parse_headers("") == {"status": 0, "headers": {}}


# --- what counts as served -------------------------------------------------


def test_full_set_is_compliant():
    r = classify(_probe("a.org", dict(FULL_SET)))
    assert r["reachable"] is True, r
    assert r["missing"] == [], r
    assert len(r["present"]) == 6, r


def test_report_only_csp_does_not_count_as_a_csp():
    """freeforcharity.org's real posture: the whole set, CSP in report-only mode.

    Report-only reports violations and blocks nothing. Counting it would mark a
    site protected on the strength of a header whose purpose is to not protect
    it.
    """
    headers = {k: v for k, v in FULL_SET.items() if k != "content-security-policy"}
    headers["content-security-policy-report-only"] = "default-src 'self'; frame-ancestors 'self'"
    r = classify(_probe("freeforcharity.org", headers))
    assert r["missing"] == ["content-security-policy"], r
    assert r["reportOnlyCsp"] is True, r


def test_csp_frame_ancestors_satisfies_x_frame_options():
    # frame-ancestors supersedes X-Frame-Options; flagging a site that uses the
    # modern directive would be a false finding.
    headers = {k: v for k, v in FULL_SET.items() if k != "x-frame-options"}
    headers["content-security-policy"] = "default-src 'self'; frame-ancestors 'self'"
    r = classify(_probe("a.org", headers))
    assert r["missing"] == [], r


def test_report_only_frame_ancestors_does_not_satisfy_x_frame_options():
    # The report-only rule has to hold transitively, or a report-only policy
    # silently satisfies a second header too.
    headers = {k: v for k, v in FULL_SET.items() if k not in ("x-frame-options",
                                                             "content-security-policy")}
    headers["content-security-policy-report-only"] = "frame-ancestors 'self'"
    r = classify(_probe("a.org", headers))
    assert sorted(r["missing"]) == ["content-security-policy", "x-frame-options"], r


def test_meta_csp_tag_satisfies_nothing():
    """The #884 finding in one assertion.

    A GitHub Pages static export can set a CSP `<meta>` tag and nothing else.
    The tag is recorded — it is what actually protects those sites today — but
    it must never mark a required response header as served.
    """
    r = classify(_probe("spt.example.org", {"server": "GitHub.com"}, metaCsp=True))
    assert r["present"] == [], r
    assert len(r["missing"]) == 6, r
    assert r["metaCsp"] is True, r


def test_empty_header_value_is_not_served():
    headers = dict(FULL_SET)
    headers["referrer-policy"] = "   "
    r = classify(_probe("a.org", headers))
    assert r["missing"] == ["referrer-policy"], r


def test_direct_github_origin_is_not_proxied():
    # A zone Transform Rule cannot reach a domain that bypasses the proxy, so
    # the report has to distinguish them. ffcadmin.org is live in this state.
    r = classify(_probe("ffcadmin.org", {"server": "GitHub.com"}))
    assert r["proxied"] is False, r
    r2 = classify(_probe("a.org", {"server": "cloudflare"}))
    assert r2["proxied"] is True, r2


def test_error_and_http_failure_are_unmeasured_not_bare():
    err = classify({"domain": "a.org", "error": "Could not resolve host", "status": 0})
    assert err["reachable"] is False, err
    assert err["missing"] == [], err
    http = classify(_probe("b.org", {}, status=503))
    assert http["reachable"] is False, http


# --- bucketing -------------------------------------------------------------


def test_unmeasured_domain_never_counts_as_a_gap():
    # Not-measured must not render as measured-and-bad; that is the same error
    # #884 caught one level up.
    r = analyze([{"domain": "a.org", "error": "timeout", "status": 0}])
    assert r["hasGap"] is False, r
    assert len(r["unmeasured"]) == 1, r
    assert r["measured"] == 0, r


def test_an_error_status_is_reported_as_answered_not_as_no_response():
    """A 503 is emphatically reachable.

    Both failure modes land in the same bucket, so the report must not describe
    the bucket as "did not answer" — that sends triage hunting a DNS or TLS
    fault that is not there.
    """
    r = analyze([_probe("busy.org", {"server": "cloudflare"}, status=503)])
    body = render(r, "2026-07-26T00:00:00.000Z")
    assert "## Unmeasured (1)" in body, body
    assert "did not answer" not in body.lower(), body
    assert "HTTP 503" in body, body


def test_out_of_scope_note_does_not_call_every_skip_a_migration_target():
    # `left FFC` is not in the #702 migration backlog, and a skip is not a
    # statement about the domain's header posture either way.
    r = analyze([_probe("a.org", dict(FULL_SET))],
                [{"domain": "gone.org", "reason": "left FFC"}])
    body = render(r, "2026-07-26T00:00:00.000Z")
    assert "## Out of scope (1)" in body, body
    assert "#702" not in body, body


def test_partial_and_bare_both_raise_the_issue():
    partial = _probe("p.org", {"x-content-type-options": "nosniff"})
    bare = _probe("b.org", {"server": "GitHub.com"})
    r = analyze([partial, bare, _probe("ok.org", dict(FULL_SET))])
    assert r["hasGap"] is True, r
    assert [x["domain"] for x in r["partial"]] == ["p.org"], r
    assert [x["domain"] for x in r["bare"]] == ["b.org"], r
    assert [x["domain"] for x in r["compliant"]] == ["ok.org"], r


def test_fully_compliant_fleet_closes_the_issue():
    r = analyze([_probe("a.org", dict(FULL_SET)), _probe("b.org", dict(FULL_SET))])
    assert r["hasGap"] is False, r
    assert r["measured"] == 2, r


# --- scope selection -------------------------------------------------------


def test_scope_is_the_cloudflare_zone_population():
    records = [
        {"Domain": "pages.org", "Host Category": "GitHub Pages", "Is In Cloudflare": "Yes"},
        {"Domain": "legacy.org", "Host Category": "Hostinger", "Is In Cloudflare": "Yes"},
        {"Domain": "elsewhere.org", "Host Category": "Other-external", "Is In Cloudflare": "No"},
        {"Domain": "left.org", "Host Category": "GitHub Pages", "Is In Cloudflare": "Yes",
         "Left FFC": "Yes"},
    ]
    out = select_domains(records)
    # A Transform Rule is per zone, so a legacy origin inside an FFC zone is in
    # scope; a domain outside FFC's Cloudflare is not.
    assert [t["domain"] for t in out["targets"]] == ["legacy.org", "pages.org"], out
    reasons = {s["domain"]: s["reason"] for s in out["skipped"]}
    assert reasons["elsewhere.org"] == "zone not in FFC Cloudflare", reasons
    assert reasons["left.org"] == "left FFC", reasons


def test_a_stale_host_category_cannot_shrink_the_scope():
    """The defect the live rehearsal caught.

    freeforcharity.org is the one FFC site already serving the full header set,
    and its curated row reads `InterServer cPanel` / `Transferred Away`. Gating
    on those columns dropped it from the audit entirely — and an unprobed domain
    reports as no finding, which is indistinguishable from a domain that passed.
    """
    records = [{"Domain": "freeforcharity.org", "Host Category": "InterServer cPanel",
                "Status": "Transferred Away", "Is In Cloudflare": "Yes"}]
    out = select_domains(records)
    assert [t["domain"] for t in out["targets"]] == ["freeforcharity.org"], out


def test_real_sites_list_selects_the_live_cloudflare_population():
    # Against the shipped data, not a fixture: an audit that silently selects
    # zero domains would report a fully-compliant fleet.
    records = json.loads((REPO_ROOT / "sites-list" / "sites_list.json").read_text(encoding="utf-8"))
    out = select_domains(records)
    assert len(out["targets"]) >= 100, len(out["targets"])
    domains = {t["domain"] for t in out["targets"]}
    # The two live examples from #884's evidence must both be measured: the one
    # serving everything, and one serving nothing.
    assert "freeforcharity.org" in domains
    assert "ffcadmin.org" in domains


# --- report ----------------------------------------------------------------


def test_body_carries_the_marker_and_the_report_only_note():
    headers = {k: v for k, v in FULL_SET.items() if k != "content-security-policy"}
    headers["content-security-policy-report-only"] = "default-src 'self'"
    analysis = analyze([_probe("ro.org", headers), _probe("bare.org", {"server": "GitHub.com"})])
    body = render(analysis, "2026-07-26T00:00:00.000Z")
    assert body.startswith("<!-- fleet-security-header-audit -->"), body[:80]
    assert "report-only" in body, body
    assert "bare.org" in body, body
    assert "Refs #884" in body, body


def test_body_reports_unproxied_domains_as_out_of_reach_for_transform_rules():
    analysis = analyze([_probe("direct.org", {"server": "GitHub.com"})])
    body = render(analysis, "2026-07-26T00:00:00.000Z")
    assert "Not behind the Cloudflare proxy" in body, body
    assert "direct.org" in body, body


# --- workflow shape --------------------------------------------------------


def test_workflow_is_ungated_and_read_only():
    wf = load_workflow(WF_FILE)
    perms = wf["permissions"]
    assert perms.get("contents") == "read", perms
    assert perms.get("issues") == "write", perms
    for name, job in wf["jobs"].items():
        assert "environment" not in job, f"{name} must not use an environment gate"


def test_workflow_has_schedule_and_dispatch():
    wf = load_workflow(WF_FILE)
    on = wf.get(True, wf.get("on"))
    assert "schedule" in on, on
    assert "workflow_dispatch" in on, on


def test_schedule_does_not_collide_with_the_other_fleet_audits():
    def cron_of(f):
        wf = load_workflow(f)
        on = wf.get(True, wf.get("on"))
        return {s["cron"] for s in on["schedule"]}

    mine = cron_of(WF_FILE)
    for other in ("741-fleet-security-audit-coverage.yml", "738-fleet-smoke-engine-drift-audit.yml"):
        assert mine.isdisjoint(cron_of(other)), (mine, other)


def test_audit_never_asserts_on_a_source_file():
    """The whole point of #884: measure the response, not the repo.

    A future edit that reintroduces a `public/_headers` existence check here
    would restore exactly the audit that reported coverage which did not exist.
    """
    raw = (REPO_ROOT / ".github" / "workflows" / WF_FILE).read_text(encoding="utf-8")
    body = "\n".join(ln for ln in raw.splitlines() if not ln.strip().startswith("#"))
    assert "public/_headers" not in body, "743 must not read public/_headers — it is inert (#884)"

    # The library is pure: it is handed parsed responses and returns verdicts.
    # No file I/O at all means it CANNOT grow a source-file check by accident —
    # a stronger invariant than banning one filename, since the next inert
    # artifact will have a different name.
    lib_src = LIB.read_text(encoding="utf-8")
    code = "\n".join(ln for ln in lib_src.splitlines() if not ln.strip().startswith("//"))
    for io_call in ["require('fs')", 'require("fs")', "readFileSync", "existsSync"]:
        assert io_call not in code, f"the classification library must stay pure ({io_call})"


def test_header_comment_never_wraps_mid_hyphenated_word():
    # The catalog generator joins header comment lines with a space, so a word
    # split across the wrap ships to the public catalog as `read- only`.
    raw = (REPO_ROOT / ".github" / "workflows" / WF_FILE).read_text(encoding="utf-8")
    for line in [ln for ln in raw.splitlines() if ln.startswith("#")]:
        assert not line.rstrip().endswith("-"), line


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
