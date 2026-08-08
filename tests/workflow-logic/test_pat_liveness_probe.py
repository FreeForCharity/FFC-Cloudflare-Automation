"""Unit tests for the in-step GitHub-PAT liveness probe (#1102).

Sixteen credential steps across fourteen workflows load a GitHub PAT from Key
Vault. Until #1102 each guarded it with `if [ -z "$token" ]` only -- and
**emptiness is not the failure mode that happens**. A revoked-but-present PAT
cleared that check and failed two to four steps later inside a script, naming
the wrong subsystem: 502's `deliver` job failed 11 consecutive times with a bare
`401 Bad credentials` from `generate-agentic-os-status.py`, and the log's most
greppable line was the *echoed* `run:` body saying the secret "came back empty
from Key Vault", which named vault RBAC as the cause of a revoked token. Nine
days were read that way (#848).

What is asserted here, in two layers:

  * **Shape**, over every one of the 16 steps: the probe exists, sits after the
    `::add-mask::` and before the `GITHUB_ENV` export (so a dead token is never
    exported), keeps the emptiness branch with a distinguishable message, names
    the secret and the status code, and never routes the token into output.
  * **Behaviour**, by running the real step bodies under the shell the runner
    uses (`bash --noprofile --norc -e -o pipefail`) with `az` and `curl` shims.
    Shape tests cannot see the failure this class actually produces: under an
    inherited `-e`, `code="$(curl ...)"` without `|| true` aborts the step before
    its own branch runs, so the guard would be present, unreachable, and green
    (L73). `test_unreachable_endpoint_fails_closed` is that proof -- delete the
    `|| true` from a step body and it fails with an empty stdout.

No network: `curl` here is a shim that records its argv and returns the status
the test asks for. `test_real_curl_prints_000_on_a_refused_connection` is the
control on that shim's premise -- it exercises the *real* curl against a closed
local port to confirm the `000` + non-zero-exit pair the shim simulates.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import child_env, load_workflow

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

EMPTY_MARKER = "came back empty from Key Vault"
SECRET_RE = re.compile(r"--name\s+(\S*github-pat)\b")
PROBE_URL = "https://api.github.com/user"
MASK_LINE = 'echo "::add-mask::$token"'

# The census is asserted, not assumed. A helper that quietly returns [] makes
# every "for every step ..." test below pass over nothing -- the reassuring
# direction, and the shape L47 warns about one layer down in the harness.
EXPECTED_STEPS = 16

BASH = shutil.which("bash")
CURL = shutil.which("curl")


class Skip(Exception):
    """Raised when this host cannot run the behavioural half."""


def require_bash() -> str:
    if not BASH:
        raise Skip("no bash on PATH")
    return BASH


def pat_steps() -> list[tuple[str, str, str, str]]:
    """(workflow file, job id, secret name, run body) per GitHub-PAT load step."""
    found = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        if EMPTY_MARKER not in text:
            continue
        workflow = load_workflow(path.name)
        for job_id, job in (workflow.get("jobs") or {}).items():
            for step in job.get("steps") or []:
                body = step.get("run") or ""
                if EMPTY_MARKER not in body:
                    continue
                match = SECRET_RE.search(body)
                assert match, f"{path.name}/{job_id}: cannot tell which secret this step loads"
                found.append((path.name, job_id, match.group(1), body))
    return found


STEPS = pat_steps()


EXPORT_RE = re.compile(r'^\s*d="\w+_EOF_\$\{RANDOM\}', re.M)
EXPORTED_VAR_RE = re.compile(r'echo "(\w+)<<\$\{d\}"')


def export_start(body: str) -> int:
    """Offset of the heredoc-delimiter line that begins the GITHUB_ENV export."""
    match = EXPORT_RE.search(body)
    assert match, "step does not export the token to GITHUB_ENV in the expected shape"
    return match.start()


def exported_var(body: str) -> str:
    """The env var this step publishes (745 uses BOARD_AUDIT_TOKEN, the rest GH_TOKEN)."""
    match = EXPORTED_VAR_RE.search(body)
    assert match, "cannot tell which variable this step exports"
    return match.group(1)


def probe_block(body: str) -> str:
    """The text between the mask and the GITHUB_ENV export -- where the probe lives.

    Missing anchors are assertions, never a bare `.index()` ValueError: the
    module runner catches AssertionError only, so an exception here would end
    the MODULE and the tests after it would never report -- a truncated roster
    that still reads as a list of PASSes (L82). Measured, not theorised: the
    first mutation round deleted a probe and this function's ValueError hid
    three tests that would otherwise have caught it.
    """
    assert MASK_LINE in body, "step does not mask the token"
    start = body.index(MASK_LINE) + len(MASK_LINE)
    return body[start : export_start(body)]


# --- shape -------------------------------------------------------------------


def test_step_inventory_is_complete():
    """Positive control for every loop below: the census is 16, and per file it
    matches the number of emptiness guards a grep finds."""
    assert len(STEPS) == EXPECTED_STEPS, f"expected {EXPECTED_STEPS} PAT load steps, found {len(STEPS)}: {[s[:2] for s in STEPS]}"
    per_file: dict[str, int] = {}
    for name, _job, _secret, _body in STEPS:
        per_file[name] = per_file.get(name, 0) + 1
    for name, count in per_file.items():
        raw = (WORKFLOWS / name).read_text(encoding="utf-8").count(EMPTY_MARKER)
        assert raw == count, f"{name}: {raw} emptiness guards in the file but {count} steps parsed"


def test_every_pat_step_probes_liveness():
    for name, job, secret, body in STEPS:
        block = probe_block(body)
        where = f"{name}/{job} ({secret})"
        assert PROBE_URL in block, f"{where}: no GET /user liveness probe between the mask and the export"
        assert "%{http_code}" in block, f"{where}: the probe must read a status code"
        assert "exit 1" in block, f"{where}: the probe must fail the step"


def test_probe_runs_after_the_mask_and_before_the_export():
    """Order is the guarantee: a token that fails the probe is never exported,
    and it is masked before any request is built from it."""
    for name, job, _secret, body in STEPS:
        assert MASK_LINE in body and PROBE_URL in body, f"{name}/{job}: no mask or no probe to order"
        mask = body.index(MASK_LINE)
        probe = body.index(PROBE_URL)
        export = export_start(body)
        assert mask < probe < export, f"{name}/{job}: probe at {probe} is not between mask {mask} and export {export}"


def test_emptiness_check_is_kept_and_stays_distinguishable():
    """Empty and dead have different remedies -- vault RBAC vs remint -- so the
    two messages must never collapse into one (AC2)."""
    for name, job, secret, body in STEPS:
        assert 'if [ -z "$token" ]' in body, f"{name}/{job}: the emptiness check was replaced, not kept"
        assert f"::error::{secret} {EMPTY_MARKER}" in body, f"{name}/{job}: emptiness message lost its secret name"
        block = probe_block(body)
        assert EMPTY_MARKER not in block, f"{name}/{job}: the liveness branch reuses the emptiness wording"


def test_probe_error_names_the_secret_and_the_status_code():
    for name, job, secret, body in STEPS:
        errors = [ln for ln in probe_block(body).splitlines() if "::error::" in ln]
        assert errors, f"{name}/{job}: the probe fails the step without emitting ::error::"
        for line in errors:
            assert secret in line, f"{name}/{job}: error does not name the secret: {line.strip()}"
            assert "$code" in line, f"{name}/{job}: error does not report the status code: {line.strip()}"


def test_probe_states_that_200_is_not_a_scope_guarantee():
    """AC4: `GET /user` proves the token authenticates and nothing about scope --
    502 and 735 both go on to write. The limit is stated where the next reader
    of the step will see it."""
    for name, job, _secret, body in STEPS:
        block = probe_block(body).upper()
        assert "SCOPE" in block, f"{name}/{job}: the probe does not say what a 200 fails to prove"


def test_probe_never_routes_the_token_into_output():
    """The value is masked; do not add a code path that could defeat it (AC3)."""
    for name, job, _secret, body in STEPS:
        block = probe_block(body)
        assert "-o /dev/null" in block, f"{name}/{job}: the probe keeps the response body"
        for flag in (" -v", " --verbose", " --trace", "--trace-ascii"):
            assert flag not in block, f"{name}/{job}: {flag.strip()} would print the request headers"
        for line in block.splitlines():
            bare = line.strip()
            if bare.startswith("#") or "$token" not in bare:
                continue
            assert bare.startswith("-H \"Authorization: Bearer $token\""), f"{name}/{job}: token used outside the request header: {bare}"


def test_probe_assignment_tolerates_a_failing_curl():
    """L73 / AC5: the runner's `-e` is inherited and `set -euo pipefail` does not
    clear it, so without `|| true` the failure branch is unreachable."""
    for name, job, _secret, body in STEPS:
        block = probe_block(body)
        assert "|| true)" in block, f"{name}/{job}: a curl that cannot reach the network would abort the step before its own check"


# --- behaviour ---------------------------------------------------------------


def _shims(root: pathlib.Path) -> pathlib.Path:
    """`az` and `curl` stand-ins, driven by TEST_* env vars. No network."""
    bin_dir = root / "bin"
    bin_dir.mkdir()
    az = bin_dir / "az"
    az.write_text(
        "#!/usr/bin/env bash\n"
        "# Fake `az`: prints whatever the test says Key Vault holds.\n"
        'printf "%s" "${TEST_KV_TOKEN-}"\n',
        encoding="utf-8",
    )
    curl = bin_dir / "curl"
    curl.write_text(
        "#!/usr/bin/env bash\n"
        "# Fake `curl`: records argv, prints the status code the test asks for,\n"
        "# and exits with the rc real curl would use. `000` + rc 7 is what real\n"
        "# curl produces when the request never completes -- asserted against the\n"
        "# real binary in test_real_curl_prints_000_on_a_refused_connection.\n"
        'printf "%s\\n" "$*" >> "$TEST_CURL_LOG"\n'
        'printf "%s" "${TEST_HTTP_CODE-200}"\n'
        'exit "${TEST_CURL_RC-0}"\n',
        encoding="utf-8",
    )
    for f in (az, curl):
        f.chmod(0o755)
    return bin_dir


def run_step(body: str, *, token: str = "ghp_liveTOKEN123", http_code: str = "200", curl_rc: str = "0"):
    """Run a real step body under the runner's shell. Returns (proc, github_env, curl_log)."""
    bash = require_bash()
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        bin_dir = _shims(root)
        script = root / "step.sh"
        script.write_text(body, encoding="utf-8")
        gh_env = root / "github_env"
        gh_env.write_text("", encoding="utf-8")
        curl_log = root / "curl.log"
        curl_log.write_text("", encoding="utf-8")
        proc = subprocess.run(
            [bash, "--noprofile", "--norc", "-e", "-o", "pipefail", str(script)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
            env=child_env(
                bin_dir,
                TEST_KV_TOKEN=token,
                TEST_HTTP_CODE=http_code,
                TEST_CURL_RC=curl_rc,
                TEST_CURL_LOG=str(curl_log),
                GITHUB_ENV=str(gh_env),
            ),
        )
        return proc, gh_env.read_text(encoding="utf-8"), curl_log.read_text(encoding="utf-8")


def _body(workflow: str, job: str) -> str:
    for name, job_id, _secret, body in STEPS:
        if name.startswith(workflow) and job_id == job:
            return body
    raise AssertionError(f"no PAT step for {workflow}/{job}")


DELIVER = "502-google-analytics-report.yml"


def test_live_token_passes_and_exports_gh_token():
    proc, gh_env, curl_log = run_step(_body(DELIVER, "deliver"), http_code="200")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "::error::" not in proc.stdout, proc.stdout
    assert "GH_TOKEN<<" in gh_env and "ghp_liveTOKEN123" in gh_env, gh_env
    assert PROBE_URL in curl_log and "-o /dev/null" in curl_log, curl_log


def test_revoked_token_fails_with_the_revoked_message():
    proc, gh_env, _ = run_step(_body(DELIVER, "deliver"), http_code="401")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "::error::read-all-cbm-github-pat returned 401 from GET /user" in proc.stdout, proc.stdout
    assert "not missing" in proc.stdout, proc.stdout
    assert EMPTY_MARKER not in proc.stdout, "a revoked token must not be reported as an empty one"
    assert "GH_TOKEN" not in gh_env, "a rejected token was exported to GITHUB_ENV anyway"


def test_forbidden_token_is_treated_as_rejected():
    proc, gh_env, _ = run_step(_body(DELIVER, "deliver"), http_code="403")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "returned 403 from GET /user" in proc.stdout, proc.stdout
    assert "GH_TOKEN" not in gh_env, gh_env


def test_server_error_fails_closed_as_unverified():
    """A 502 says nothing about the credential, so it is never 'revoked' -- but it
    is also never a pass (321's live/dead/unverified split)."""
    proc, gh_env, _ = run_step(_body(DELIVER, "deliver"), http_code="502")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "could not be verified" in proc.stdout, proc.stdout
    assert "answered 502" in proc.stdout, proc.stdout
    assert "revoked" not in proc.stdout, proc.stdout
    assert "GH_TOKEN" not in gh_env, gh_env


def test_unreachable_endpoint_fails_closed():
    """The `|| true` proof: curl exits non-zero and prints 000. Without it the
    step dies at the assignment and stdout carries no message of ours."""
    proc, gh_env, _ = run_step(_body(DELIVER, "deliver"), http_code="000", curl_rc="7")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "could not be verified" in proc.stdout, proc.stdout
    assert "answered 000" in proc.stdout, proc.stdout
    assert "GH_TOKEN" not in gh_env, gh_env


def test_empty_secret_still_reports_emptiness_not_liveness():
    proc, gh_env, curl_log = run_step(_body(DELIVER, "deliver"), token="")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert EMPTY_MARKER in proc.stdout, proc.stdout
    assert "GET /user" not in proc.stdout, "an absent token was probed instead of reported"
    assert curl_log.strip() == "", "the probe ran on an empty token"
    assert "GH_TOKEN" not in gh_env, gh_env


def test_only_the_mask_directive_ever_prints_the_token():
    """`::add-mask::$token` prints the value by design -- that IS the registration,
    and the runner redacts it from then on. Every other line must be clean, and
    the failure path is where a naive probe would leak it (a curl error body, an
    error message quoting the token). Asserted per line, not over the whole
    stream, because a blanket 'token not in stdout' can only pass by also
    rejecting the mask line the step needs."""
    proc, _gh_env, _ = run_step(_body(DELIVER, "deliver"), token="ghp_SECRETVALUE", http_code="401")
    leaks = [
        line
        for line in (proc.stdout + proc.stderr).splitlines()
        if "ghp_SECRETVALUE" in line and not line.startswith("::add-mask::")
    ]
    assert not leaks, f"the credential reached output outside the mask directive: {leaks}"


def test_every_step_body_fails_closed_on_a_revoked_token():
    """All 16, not one representative: these bodies were edited in bulk."""
    for name, job, secret, body in STEPS:
        proc, gh_env, _ = run_step(body, http_code="401")
        where = f"{name}/{job}"
        assert proc.returncode == 1, f"{where} continued with a rejected token: {proc.stdout}{proc.stderr}"
        assert f"::error::{secret} returned 401 from GET /user" in proc.stdout, f"{where}: {proc.stdout}"
        assert exported_var(body) not in gh_env, f"{where} exported a rejected token"


def test_every_step_body_still_exports_on_a_live_token():
    for name, job, _secret, body in STEPS:
        proc, gh_env, _ = run_step(body, http_code="200")
        where = f"{name}/{job}"
        assert proc.returncode == 0, f"{where}: {proc.stdout}{proc.stderr}"
        assert f"{exported_var(body)}<<" in gh_env, f"{where} stopped exporting the credential"


def test_real_curl_prints_000_on_a_refused_connection():
    """Control on the shim's premise. Port 1 on loopback refuses; real curl must
    print `000` and exit non-zero, which is the pair every unreachable-endpoint
    assertion above is built on."""
    if not CURL:
        raise Skip("no curl on PATH")
    proc = subprocess.run(
        [CURL, "-sS", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "5", "http://127.0.0.1:1/"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert proc.returncode != 0, "a refused connection exited 0"
    assert proc.stdout.strip() == "000", proc.stdout


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
SKIPPED: list[str] = []

if __name__ == "__main__":
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except Skip as e:
            SKIPPED.append(t.__name__)
            print(f"  SKIP {t.__name__}: {e} (CI has both; see module docstring)")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {str(e)[:2000]}")
    if SKIPPED:
        print(f"  {len(SKIPPED)} test(s) skipped for want of a shell or curl.")
    sys.exit(1 if failures else 0)
