"""Unit tests for the 738 fleet-smoke engine-drift decision logic.

The workflow's github-script step `require`s scripts/fleet-smoke-drift-lib.js, so
exercising that module directly tests the shipped logic: which repos count as
drift (a deployed copy whose hash differs from canonical) versus the benign
buckets (not-yet-onboarded `missing`, `unreadable` fetch failures) that must NOT
raise the rolling issue. A shape test guards the workflow wiring itself.

The classification logic is only as good as the digests fed to it, so the `gather`
step is driven end-to-end against the fake `gh` as well (#893, ledger L27). The
digest it published was NOT the file's sha256: the body was captured with
`out=$(gh api …)`, which strips every trailing newline, and `printf '%s' "$out"`
then hashed the file minus its final byte. Two copies differing only in trailing
newlines therefore hashed identically — the exact class of difference a
byte-identity audit exists to catch — and nobody could reproduce a published
canonical hash with `sha256sum`. Those are the two properties the gather tests
below pin, because neither is visible from the lib.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import load_workflow, step_run

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
LIB = REPO_ROOT / "scripts" / "fleet-smoke-drift-lib.js"
WF_FILE = "738-fleet-smoke-engine-drift-audit.yml"
HARNESS_DIR = pathlib.Path(__file__).resolve().parent / "harness"
GATHER_STEP = "Gather smoke-engine hashes"
FLEET_RE = re.compile(r"^(FFC-EX-|FFC-IN-Footer_Only_Template$)")


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


def analyze(canonical: str, entries: list) -> dict:
    return _node(
        "process.stdout.write(JSON.stringify("
        "l.analyze(process.argv[1], JSON.parse(process.argv[2]))));",
        canonical,
        json.dumps(entries),
    )


def render(analysis_canonical_ts: dict) -> str:
    return _node(
        "const a=JSON.parse(process.argv[1]);"
        "process.stdout.write(JSON.stringify("
        "l.renderBody(a.analysis, a.canonical, a.ts)));",
        json.dumps(analysis_canonical_ts),
    )


# --- classification --------------------------------------------------------

CANON = "a" * 64


def test_uniform_fleet_is_no_drift():
    r = analyze(CANON, [
        {"repo": "FreeForCharity/FFC-EX-one.org", "hash": CANON},
        {"repo": "FreeForCharity/FFC-EX-two.org", "hash": CANON},
    ])
    assert r["hasDrift"] is False, r
    assert sorted(r["matching"]) == [
        "FreeForCharity/FFC-EX-one.org",
        "FreeForCharity/FFC-EX-two.org",
    ], r
    assert r["divergent"] == [] and r["missing"] == [] and r["unreadable"] == [], r


def test_differing_hash_is_drift():
    r = analyze(CANON, [
        {"repo": "FreeForCharity/FFC-EX-good.org", "hash": CANON},
        {"repo": "FreeForCharity/FFC-EX-bad.org", "hash": "b" * 64},
    ])
    assert r["hasDrift"] is True, r
    assert [d["repo"] for d in r["divergent"]] == ["FreeForCharity/FFC-EX-bad.org"], r
    assert r["divergent"][0]["hash"] == "b" * 64, r


def test_absent_file_is_missing_not_drift():
    # Incremental rollout: a repo without the engine yet must not raise the issue.
    r = analyze(CANON, [
        {"repo": "FreeForCharity/FFC-EX-new.org", "hash": None},
        {"repo": "FreeForCharity/FFC-EX-old.org"},  # hash key absent entirely
    ])
    assert r["hasDrift"] is False, r
    assert sorted(r["missing"]) == [
        "FreeForCharity/FFC-EX-new.org",
        "FreeForCharity/FFC-EX-old.org",
    ], r


def test_fetch_error_is_unreadable_not_drift():
    r = analyze(CANON, [
        {"repo": "FreeForCharity/FFC-EX-priv.org", "error": "HTTP 403 rate limited"},
    ])
    assert r["hasDrift"] is False, r
    assert r["unreadable"] == [
        {"repo": "FreeForCharity/FFC-EX-priv.org", "error": "HTTP 403 rate limited"}
    ], r


def test_mixed_fleet_only_divergent_triggers():
    r = analyze(CANON, [
        {"repo": "FreeForCharity/FFC-EX-a.org", "hash": CANON},
        {"repo": "FreeForCharity/FFC-EX-b.org", "hash": "c" * 64},
        {"repo": "FreeForCharity/FFC-EX-c.org", "hash": None},
        {"repo": "FreeForCharity/FFC-EX-d.org", "error": "boom"},
    ])
    assert r["hasDrift"] is True, r
    assert len(r["divergent"]) == 1 and len(r["matching"]) == 1
    assert len(r["missing"]) == 1 and len(r["unreadable"]) == 1


def test_empty_entries_is_no_drift():
    r = analyze(CANON, [])
    assert r["hasDrift"] is False, r
    assert r["matching"] == [] and r["divergent"] == [], r


# --- report rendering ------------------------------------------------------

def test_render_contains_marker_and_divergent_row():
    analysis = analyze(CANON, [
        {"repo": "FreeForCharity/FFC-EX-good.org", "hash": CANON},
        {"repo": "FreeForCharity/FFC-EX-bad.org", "hash": "d" * 64},
    ])
    body = render({"analysis": analysis, "canonical": CANON, "ts": "2026-07-20T00:00:00Z"})
    assert "<!-- fleet-smoke-engine-drift-audit -->" in body, body
    assert "FFC-EX-bad.org" in body, body
    assert ("d" * 12) in body, body  # short hash rendered
    assert "Divergent (1)" in body, body
    assert "Matching (1)" in body, body


def test_render_missing_and_unreadable_sections():
    analysis = analyze(CANON, [
        {"repo": "FreeForCharity/FFC-EX-x.org", "hash": "e" * 64},
        {"repo": "FreeForCharity/FFC-EX-y.org", "hash": None},
        {"repo": "FreeForCharity/FFC-EX-z.org", "error": "nope"},
    ])
    body = render({"analysis": analysis, "canonical": CANON, "ts": "t"})
    assert "Not yet deployed (1)" in body, body
    assert "Unreadable (1)" in body, body
    assert "nope" in body, body


# --- the gather step: what actually gets hashed -----------------------------

CANONICAL_BYTES = b"name: Post-deploy smoke\non: workflow_run\njobs: {}\n"


def run_gather(
    files: dict[str, bytes],
    errors: dict[str, str] | None = None,
    fleet: list[str] | None = None,
) -> tuple[str, list[dict]]:
    """Run 738's `gather` step against the fake gh.

    `files` maps a repo NAME (not owner/name) to the exact bytes the raw-contents
    fetch returns for it; `errors` maps a name to a non-404 failure body. Returns
    (canonical_hash, entries).

    CANONICAL_REPO / SMOKE_PATH / ORG come from the workflow's own `env:` block
    rather than being restated here — a test that hard-codes them keeps passing
    after the workflow points somewhere else.
    """
    wf = load_workflow(WF_FILE)
    script = step_run(WF_FILE, "audit", GATHER_STEP)
    canonical_name = str(wf["env"]["CANONICAL_REPO"]).split("/")[-1]
    if fleet is None:
        fleet = sorted(
            n
            for n in list(files) + list(errors or {})
            if n != canonical_name and FLEET_RE.match(n)
        )
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        smoke = td / "smoke"
        smoke.mkdir()
        for name, content in files.items():
            (smoke / name).write_bytes(content)
        for name, body in (errors or {}).items():
            (smoke / f"{name}.error").write_text(body, encoding="utf-8")
        gh_output = td / "output.txt"
        gh_output.touch()
        env = {
            "PATH": f"{HARNESS_DIR}:/usr/bin:/bin",
            "HOME": str(td),
            "GITHUB_OUTPUT": str(gh_output),
            "TEST_SMOKE_DIR": str(smoke),
            "TEST_REPO_LIST": "\n".join(fleet),
        }
        env.update({k: str(v) for k, v in wf["env"].items()})
        proc = subprocess.run(
            ["bash", "-c", script], env=env, capture_output=True, text=True, timeout=120
        )
        if proc.returncode != 0:
            raise AssertionError(
                f"gather exited {proc.returncode}: {proc.stdout}\n{proc.stderr}"
            )
        outputs = dict(
            line.split("=", 1)
            for line in gh_output.read_text(encoding="utf-8").splitlines()
            if "=" in line
        )
        entries = json.loads(
            pathlib.Path(outputs["entries_file"]).read_text(encoding="utf-8")
        )
        return outputs["canonical_hash"], entries


def test_the_published_hash_is_the_files_own_sha256():
    """#893: the canonical digest must be reproducible with `sha256sum <file>`.

    Pre-fix this was the sha256 of the content with its trailing newline stripped,
    so anyone verifying a repo by hand got a mismatch on a byte-identical file.
    """
    canonical, _ = run_gather({"FFC-IN-FFC_Single_Page_Template": CANONICAL_BYTES})
    assert canonical == hashlib.sha256(CANONICAL_BYTES).hexdigest(), (
        "published canonical hash is not the file's sha256 — a digest nobody can "
        f"reproduce with sha256sum (got {canonical})"
    )


def test_a_trailing_newline_only_difference_is_reported_divergent():
    """The narrow case that made this a correctness bug, not a cosmetic one.

    `$(...)` strips ALL trailing newlines, so a copy with no final newline and a
    copy with a doubled one both hashed the same as canonical. A byte-identity
    audit that cannot see a trailing-newline difference is not a byte-identity
    audit — and prettier in the target repo would flag exactly that difference.
    """
    canonical, entries = run_gather(
        {
            "FFC-IN-FFC_Single_Page_Template": CANONICAL_BYTES,
            "FFC-EX-identical.org": CANONICAL_BYTES,
            "FFC-EX-doubled-newline.org": CANONICAL_BYTES + b"\n",
            "FFC-EX-no-final-newline.org": CANONICAL_BYTES.rstrip(b"\n"),
        }
    )
    by_repo = {e["repo"].split("/")[-1]: e.get("hash") for e in entries}
    assert by_repo["FFC-EX-identical.org"] == canonical, by_repo
    for name in ("FFC-EX-doubled-newline.org", "FFC-EX-no-final-newline.org"):
        assert by_repo[name] != canonical, (
            f"{name} differs from canonical only in trailing newlines and was "
            "reported as matching — the audit is blind to the difference it exists "
            f"to catch ({by_repo})"
        )
    assert (
        by_repo["FFC-EX-doubled-newline.org"] != by_repo["FFC-EX-no-final-newline.org"]
    ), by_repo


def test_a_missing_file_is_a_null_hash_not_a_digest_of_the_404():
    _, entries = run_gather(
        {
            "FFC-IN-FFC_Single_Page_Template": CANONICAL_BYTES,
            "FFC-EX-deployed.org": CANONICAL_BYTES,
        },
        fleet=["FFC-EX-deployed.org", "FFC-EX-not-onboarded.org"],
    )
    by_repo = {e["repo"].split("/")[-1]: e for e in entries}
    assert by_repo["FFC-EX-not-onboarded.org"]["hash"] is None, by_repo
    assert "error" not in by_repo["FFC-EX-not-onboarded.org"], by_repo


def test_an_unreadable_repo_is_an_error_entry_and_its_body_is_never_hashed():
    """Ledger L02 shape: gh puts some error bodies on STDOUT.

    Redirecting the body to a file makes that error text land where the digest is
    taken from, so the exit-code check is what keeps a 403 from being published as
    a repo's smoke-engine hash.
    """
    _, entries = run_gather(
        {"FFC-IN-FFC_Single_Page_Template": CANONICAL_BYTES},
        errors={
            "FFC-EX-forbidden.org": (
                '{"message":"API rate limit exceeded","status":"403"}'
            )
        },
    )
    entry = next(e for e in entries if e["repo"].endswith("FFC-EX-forbidden.org"))
    assert "hash" not in entry, f"an unreadable repo must carry no digest: {entry}"
    assert "403" in entry["error"], entry
    assert not re.fullmatch(r"[0-9a-f]{64}", entry.get("error", "")), entry


def test_the_gather_step_hashes_a_file_and_never_a_shell_variable():
    """Structural companion to the behavioural guards above (ledger L27).

    The behavioural tests would also pass on some other reimplementation that
    happens to be correct today; this one pins the mechanism, because the
    variable-capture form is the one a future edit reaches for by habit.
    """
    # Comment lines are excluded, or the step's own explanation of the defect
    # trips the guard — the same self-trip ledger L02's scan had to avoid.
    script = step_run(WF_FILE, "audit", GATHER_STEP)
    code = "\n".join(
        ln for ln in script.splitlines() if not ln.strip().startswith("#")
    )
    piped_var = re.search(r"\$\{?\w+\}?\"?\s*\|\s*sha256sum", code)
    assert not piped_var, (
        "the gather step pipes a shell variable into sha256sum — command "
        "substitution strips trailing newlines, so that is the digest of the file "
        f"minus its final byte(s) (#893): {piped_var.group(0) if piped_var else ''}"
    )
    assert re.search(r"sha256sum\s+(/\S+|\"\$\w+\"|\$\w+)", code), (
        "the gather step must hash a file path so the published digest is "
        "reproducible with `sha256sum <file>`"
    )


# --- workflow wiring shape -------------------------------------------------

def test_workflow_requires_lib_and_is_read_only():
    raw = (REPO_ROOT / ".github" / "workflows" / WF_FILE).read_text(encoding="utf-8")
    assert "scripts/fleet-smoke-drift-lib.js" in raw, "workflow must require the shipped lib"
    wf = load_workflow(WF_FILE)
    perms = wf["permissions"]
    assert perms.get("contents") == "read", perms
    assert perms.get("issues") == "write", perms
    # No environment gate on any job (read-only, GITHUB_TOKEN).
    for name, job in wf["jobs"].items():
        assert "environment" not in job, f"{name} must not use an environment gate"


def test_workflow_has_schedule_and_dispatch():
    wf = load_workflow(WF_FILE)
    on = wf.get(True, wf.get("on"))
    assert "schedule" in on, on
    assert "workflow_dispatch" in on, on


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
