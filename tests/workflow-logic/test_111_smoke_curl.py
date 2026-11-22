"""111's post-apply smoke test must call a curl that exists on its Linux runner.

#1772 wrote the probe as `curl.exe`, which only resolves on Windows. The apply
job runs on ubuntu-latest, so every live run of 111 since then has failed at
"Smoke test" with "The term 'curl.exe' is not recognized", even when the
redirect rule had applied (runs 45071203709, 45071231901, 45072029318).

The static case runs everywhere. The two pwsh cases execute the shipped step
body, not an excerpt, with a fixture executable named `curl` first on PATH and
no `curl.exe` anywhere, so restoring `curl.exe` fails them.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "111-dns-create-redirect-rule.yml"
PWSH = shutil.which("pwsh")


class Skip(Exception):
    pass


def _smoke_step() -> dict:
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    for job in wf["jobs"].values():
        for step in job.get("steps", []):
            if step.get("name") == "Smoke test":
                return {"run": step["run"], "runs_on": job.get("runs-on")}
    raise AssertionError("no 'Smoke test' step in 111")


def _run_smoke(status: str, location: str) -> subprocess.CompletedProcess:
    if not PWSH:
        raise Skip("pwsh not installed")
    if os.name == "nt":
        raise Skip("the bash curl fixture cannot execute natively on Windows")
    step = _smoke_step()
    with tempfile.TemporaryDirectory() as tmp:
        bindir = pathlib.Path(tmp, "bin")
        bindir.mkdir()
        fake = bindir / "curl"
        fake.write_text(
            "#!/usr/bin/env bash\n"
            f"printf 'status={status}\\nlocation={location}\\n'\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        script = pathlib.Path(tmp, "smoke.ps1")
        # The step sleeps to let Cloudflare propagate; nothing propagates here.
        script.write_text(step["run"].replace("Start-Sleep -Seconds 5", ""), encoding="utf-8")
        env = {
            **os.environ,
            "PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}",
            "HOME": tmp,
            "INPUT_SOURCE": "school.example.org",
            "INPUT_TARGET": "example.org/school",
            "INPUT_STATUS": "301",
        }
        return subprocess.run(
            [PWSH, "-NoProfile", "-NonInteractive", "-File", str(script)],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )


def test_the_smoke_step_runs_on_linux_and_never_names_curl_exe():
    step = _smoke_step()
    assert step["runs_on"] == "ubuntu-latest", step["runs_on"]
    assert "curl.exe" not in step["run"], "curl.exe does not exist on ubuntu-latest"


def test_a_matching_redirect_is_verified_with_a_plain_curl():
    proc = _run_smoke("301", "https://example.org/school/")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "OK: redirect verified." in out, out


def test_a_wrong_status_warns_instead_of_crashing():
    proc = _run_smoke("200", "")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "Expected HTTP 301, got 200" in out, out
    assert "not recognized" not in out, out


def _tests() -> list:
    return [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]


def main() -> int:
    failures = 0
    for fn in _tests():
        try:
            fn()
            print(f"  PASS {fn.__name__}")
        except Skip as exc:
            print(f"  SKIP {fn.__name__}: {exc}")
        except AssertionError as exc:
            failures += 1
            print(f"  FAIL {fn.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001 - a crash is a failure, and must say so
            failures += 1
            print(f"  FAIL {fn.__name__}: harness error: {type(exc).__name__}: {exc}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
