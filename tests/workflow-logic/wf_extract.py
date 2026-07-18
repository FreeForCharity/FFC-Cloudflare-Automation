"""Shared helpers: extract embedded step scripts from workflow YAML.

Tests run against the *actual* script text inside the workflow files, so
they can never drift from what ships — editing a workflow's embedded
logic without updating the tests fails CI, not production.
"""

from __future__ import annotations

import pathlib

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"


def load_workflow(filename: str) -> dict:
    return yaml.safe_load((WORKFLOWS / filename).read_text())


def find_step(workflow: dict, job_id: str, name_substring: str) -> dict:
    jobs = workflow.get("jobs", {})
    if job_id not in jobs:
        raise KeyError(f"job '{job_id}' not found (have: {list(jobs)})")
    for step in jobs[job_id].get("steps", []):
        if name_substring.lower() in str(step.get("name", "")).lower():
            return step
    raise KeyError(f"no step matching '{name_substring}' in job '{job_id}'")


def step_run(workflow_file: str, job_id: str, name_substring: str) -> str:
    """The shell script body of a run: step."""
    step = find_step(load_workflow(workflow_file), job_id, name_substring)
    return step["run"]


def step_github_script(workflow_file: str, job_id: str, name_substring: str) -> str:
    """The JS body of an actions/github-script step."""
    step = find_step(load_workflow(workflow_file), job_id, name_substring)
    uses = step.get("uses", "")
    if "github-script" not in uses:
        raise ValueError(f"step is not a github-script step (uses: {uses})")
    return step["with"]["script"]
