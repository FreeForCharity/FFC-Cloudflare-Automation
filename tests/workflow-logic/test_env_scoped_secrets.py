"""Guard: environment-scoped secrets are only referenced by gated jobs.

This repo keeps ZERO repo-level Actions secrets — every secret lives inside an
approval-gated environment (e.g. CBM_TOKEN exists only in `github-prod`). A job
that references such a secret WITHOUT declaring the matching `environment:`
gets an empty string at run time, which surfaces as a confusing runtime error
("Input required and not supplied: github-token") instead of a review-time
failure — exactly how the 737 claim sweep's first scheduled fire broke on
2026-07-20 (fixed in the same PR that adds this test).

Sweeps every workflow: any job whose YAML mentions one of the known
environment-scoped secrets must declare the environment that holds it.
Extend ENV_SCOPED_SECRETS when a new gated-environment secret is introduced.
"""

from __future__ import annotations

import pathlib
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import WORKFLOWS

# secret name -> the only environment(s) where it is defined
ENV_SCOPED_SECRETS = {
    "CBM_TOKEN": {"github-prod"},
}


def _job_environment_names(job: dict) -> set[str]:
    env = job.get("environment")
    if env is None:
        return set()
    if isinstance(env, dict):
        name = env.get("name")
        return {name} if isinstance(name, str) else set()
    return {str(env)}


def _yaml_of(node) -> str:
    return yaml.safe_dump(node, default_flow_style=False)


def test_env_scoped_secrets_only_in_gated_jobs():
    violations = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        wf = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job_id, job in (wf.get("jobs") or {}).items():
            if not isinstance(job, dict):
                continue
            job_text = _yaml_of(job)
            for secret, envs in ENV_SCOPED_SECRETS.items():
                if f"secrets.{secret}" not in job_text:
                    continue
                declared = _job_environment_names(job)
                if not (declared & envs):
                    violations.append(
                        f"{path.name}: job '{job_id}' references secrets.{secret} "
                        f"but does not declare environment {sorted(envs)} "
                        f"(declared: {sorted(declared) or 'none'}) — the secret "
                        "will be EMPTY at run time"
                    )
    assert not violations, "\n".join(violations)


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {str(e)[:2000]}")
    sys.exit(1 if failures else 0)
