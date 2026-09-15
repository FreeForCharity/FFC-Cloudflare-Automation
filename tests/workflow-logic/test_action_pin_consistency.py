"""Guard: one third-party action, one pinned SHA, across workflows AND composite actions.

WHAT HAPPENED
    Every `*-from-kv` composite action under `.github/actions/` opens with
    `azure/login@<sha>`, and every Key Vault credential fetch in this repo goes
    through one of them. Dependabot's `github-actions` entry was configured with
    `directory: '/'`, which scans `.github/workflows/` and nothing else. So each
    azure/login bump updated the 37 direct workflow pins and skipped the six
    composite actions. Dependabot #1151 (3.0.0 -> 3.0.1) and the 3.0.2 bump both
    landed that way. By #1316 (3.0.2 -> 3.1.0) the workflows were two releases
    ahead of the logins that actually mint credentials.

    It was not caught by review. The Conductor verified #1151's SHA upstream
    and confirmed the diff was a pure SHA swap. Both checks were correct and
    neither could see files the PR did not touch. Copilot caught it on #1316.

WHAT THIS MODULE ENFORCES
    1. Every `owner/repo@<40-hex sha>` reference resolves to exactly one SHA
       across `.github/workflows/*.y*ml` and `.github/actions/**/action.y*ml`.
       Sub-path actions (`github/codeql-action/init`) group with their repo,
       because they ship from one release. Tag refs (`@v3`) and local
       `./...` actions are out of scope: they carry no SHA to disagree about.
    2. Dependabot's `github-actions` entry covers every composite action
       directory, so the drift is prevented at the source and not only detected.

    Adding a composite action under `.github/actions/` is covered by the
    `/.github/actions/*` glob. A composite action anywhere else fails check 2.

Run: python3 tests/workflow-logic/test_action_pin_consistency.py
"""

from __future__ import annotations

import fnmatch
import pathlib
import re
import sys

import yaml

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
GITHUB_DIR = REPO_ROOT / ".github"

USES = re.compile(
    r"""^\s*(?:-\s*)?uses:\s*['"]?"""
    r"""(?P<action>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)(?:/[^@\s'"]*)?"""
    r"""@(?P<ref>[^\s'"#]+)""",
    re.MULTILINE,
)
SHA = re.compile(r"^[0-9a-f]{40}$")


def pins(files: dict[str, str]) -> dict[str, dict[str, list[str]]]:
    """{action (lower-cased owner/repo): {sha: ["path:line", ...]}} for SHA-pinned uses only."""
    found: dict[str, dict[str, list[str]]] = {}
    for path, text in files.items():
        for m in USES.finditer(text):
            ref = m.group("ref")
            if not SHA.match(ref):
                continue
            line = text.count("\n", 0, m.start()) + 1
            found.setdefault(m.group("action").lower(), {}).setdefault(ref, []).append(f"{path}:{line}")
    return found


def disagreements(found: dict[str, dict[str, list[str]]]) -> dict[str, dict[str, list[str]]]:
    return {action: shas for action, shas in found.items() if len(shas) > 1}


def action_files(root: pathlib.Path = GITHUB_DIR) -> dict[str, str]:
    paths = sorted(root.glob("workflows/*.yml")) + sorted(root.glob("workflows/*.yaml"))
    paths += sorted(root.glob("actions/**/action.yml")) + sorted(root.glob("actions/**/action.yaml"))
    return {p.relative_to(REPO_ROOT).as_posix(): p.read_text(encoding="utf-8-sig") for p in paths}


def dependabot_action_dirs(config: dict) -> list[str]:
    """Directory patterns Dependabot scans for the github-actions ecosystem."""
    patterns: list[str] = []
    for entry in config.get("updates") or []:
        if entry.get("package-ecosystem") != "github-actions":
            continue
        if entry.get("directory"):
            patterns.append(entry["directory"])
        patterns.extend(entry.get("directories") or [])
    return patterns


def uncovered(action_dirs: list[str], patterns: list[str]) -> list[str]:
    """Composite action directories ('/.github/actions/x') no pattern matches.

    '/' is deliberately NOT treated as covering them: for this ecosystem it
    means .github/workflows, which is the whole defect this module exists for.
    """
    return [d for d in action_dirs if not any(p != "/" and fnmatch.fnmatchcase(d, p) for p in patterns)]


def composite_action_dirs(root: pathlib.Path = REPO_ROOT) -> list[str]:
    dirs = {p.parent for pattern in ("action.yml", "action.yaml") for p in root.glob(f".github/**/{pattern}")}
    return sorted("/" + d.relative_to(root).as_posix() for d in dirs)


# --------------------------------------------------------------------------
# Fixture tests: the detectors discriminate.
# --------------------------------------------------------------------------

A = "a" * 40
B = "b" * 40


def test_a_split_pin_is_reported_with_both_locations():
    found = pins(
        {
            ".github/workflows/1.yml": f"    steps:\n      - uses: azure/login@{A} # v3\n",
            ".github/actions/x/action.yml": f"  steps:\n    - name: Login\n      uses: azure/login@{B} # v3\n",
        }
    )
    bad = disagreements(found)
    assert list(bad) == ["azure/login"], bad
    assert bad["azure/login"] == {
        A: [".github/workflows/1.yml:2"],
        B: [".github/actions/x/action.yml:3"],
    }, bad


def test_agreeing_pins_tag_refs_and_local_actions_are_not_reported():
    found = pins(
        {
            "w1.yml": f"- uses: azure/login@{A}\n- uses: actions/checkout@v4\n",
            "w2.yml": f"      uses: 'azure/login@{A}'\n      uses: ./.github/actions/zeffy-secrets-from-kv\n",
        }
    )
    assert disagreements(found) == {}, found
    assert list(found) == ["azure/login"], found


def test_sub_path_actions_group_with_their_repo_and_owner_case_is_ignored():
    found = pins(
        {
            "w.yml": (
                f"- uses: github/codeql-action/init@{A}\n"
                f"- uses: github/codeql-action/analyze@{B}\n"
                f"- uses: Azure/login@{A}\n"
                f"- uses: azure/login@{A}\n"
            )
        }
    )
    assert list(disagreements(found)) == ["github/codeql-action"], found


def test_coverage_check_discriminates():
    dirs = ["/.github/actions/candid-keys-from-kv", "/.github/actions/zeffy-secrets-from-kv"]
    assert uncovered(dirs, ["/"]) == dirs
    assert uncovered(dirs, ["/", "/.github/actions/*"]) == []
    assert uncovered(dirs, ["/", "/.github/actions/candid-keys-from-kv"]) == ["/.github/actions/zeffy-secrets-from-kv"]
    config = {"updates": [{"package-ecosystem": "github-actions", "directory": "/"}, {"package-ecosystem": "npm", "directories": ["/.github/actions/*"]}]}
    assert dependabot_action_dirs(config) == ["/"], "an npm entry must not count as github-actions coverage"


# --------------------------------------------------------------------------
# Live-tree tests.
# --------------------------------------------------------------------------


def test_every_action_pin_agrees_across_workflows_and_composite_actions():
    bad = disagreements(pins(action_files()))
    lines = []
    for action, shas in sorted(bad.items()):
        lines.append(f"{action} is pinned to {len(shas)} different SHAs:")
        for sha, where in sorted(shas.items(), key=lambda kv: -len(kv[1])):
            lines.append(f"  {sha} x{len(where)}: {', '.join(where[:4])}{' ...' if len(where) > 4 else ''}")
    assert not bad, "\n".join(lines)


def test_dependabot_covers_every_composite_action_directory():
    config = yaml.safe_load((GITHUB_DIR / "dependabot.yml").read_text(encoding="utf-8-sig"))
    patterns = dependabot_action_dirs(config)
    assert patterns, "dependabot.yml has no github-actions entry"
    missing = uncovered(composite_action_dirs(), patterns)
    assert not missing, f"Dependabot's github-actions entry ({patterns}) does not scan: {missing}"


def test_the_scan_sees_the_real_tree():
    """A walk that matched nothing would satisfy every live-tree assertion above (L143)."""
    files = action_files()
    workflows = [p for p in files if p.startswith(".github/workflows/")]
    composites = [p for p in files if p.startswith(".github/actions/")]
    assert len(workflows) > 90, f"only {len(workflows)} workflow files scanned"
    assert len(composites) >= 6, f"only {len(composites)} composite action files scanned"
    login = pins(files).get("azure/login", {})
    seen = [loc for where in login.values() for loc in where]
    assert any(loc.startswith(".github/workflows/") for loc in seen), "no azure/login pin found in workflows"
    assert sum(loc.startswith(".github/actions/") for loc in seen) >= 6, "azure/login pins in composite actions not found"
    assert len(composite_action_dirs()) >= 6, composite_action_dirs()


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
