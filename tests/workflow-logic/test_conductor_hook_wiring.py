#!/usr/bin/env python3
"""Tests for `scripts/verify-conductor-hooks.py` (#1042, ledger L218).

The subject is a verifier, so the tests are mostly about the two ways a verifier
is worthless: it passes a workspace that is not wired (false green), or it fails
one that is (false red). Both are covered, and the false-green half is covered
against *stubbed guards* rather than against a missing config -- because a
missing config is the easy case and a neutered guard is the one a config diff
cannot see.

Every fixture is built in a `TemporaryDirectory`. Nothing here mutates the repo
tree: the in-place-mutate-and-restore habit is what CLAUDE.md/L182 records as
restoring against the wrong baseline, and a test module that leaves a stray file
in the working tree is #1023.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
SCRIPT = REPO_ROOT / "scripts" / "verify-conductor-hooks.py"
TEMPLATE = REPO_ROOT / ".claude" / "conductor" / "settings.template.json"
HUB_SETTINGS = REPO_ROOT / ".claude" / "settings.json"
PLACEHOLDER = "__HUB_CLONE__"


def run(*args: str, cwd: str | None = None) -> subprocess.CompletedProcess:
    """Invoke the verifier. Full env (never a scrubbed dict -- CLAUDE.md), pinned codec (#945)."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        cwd=cwd,
        timeout=120,
    )


def write_settings(ws: pathlib.Path, data: dict, name: str = "settings.json") -> None:
    (ws / ".claude").mkdir(parents=True, exist_ok=True)
    (ws / ".claude" / name).write_text(json.dumps(data), encoding="utf-8")


def guard_config(guard_path: str) -> dict:
    return {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [{"type": "command", "command": f'python3 "{guard_path}"'}],
                }
            ]
        }
    }


def _vch():
    """Import `verify-conductor-hooks.py` by path.

    Loaded rather than re-implemented: these tests assert the template against
    the SAME parser the verifier uses, so a template the parser cannot read can
    never pass the drift check by being parsed a second, more forgiving way.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("verify_conductor_hooks", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def stub(tmp: pathlib.Path, name: str, code: int) -> str:
    path = tmp / f"{name}.py"
    path.write_text(f"import sys; sys.exit({code})\n", encoding="utf-8")
    return str(path)


def behaving_guard(path: pathlib.Path) -> None:
    """A stub that PASSES both probes -- blocks the L50 shape, allows `git status`.

    The always-allow/always-block stubs above cannot expose a resolution bug:
    whichever file gets found, they fail the probe anyway, so the report reads
    "not wired" for a reason that has nothing to do with which file was read.
    Only a guard that would legitimately pass can turn a wrong path into a
    green verdict.
    """
    path.write_text(
        "import json, sys\n"
        "payload = json.load(sys.stdin)\n"
        "command = payload['tool_input']['command']\n"
        "sys.exit(2 if 'tail -45' in command else 0)\n",
        encoding="utf-8",
    )


# --------------------------------------------------------------------------
# The four real-world states, in the order a workspace passes through them.
# --------------------------------------------------------------------------


def test_a_workspace_with_no_settings_at_all_is_not_wired():
    with tempfile.TemporaryDirectory() as td:
        proc = run("--workspace", td)
        assert proc.returncode == 1, proc.stdout
        assert "NOT WIRED" in proc.stdout, proc.stdout


def test_permissions_only_settings_is_not_wired():
    # The literal state L218 measured on the Conductor workspace: a
    # `permissions.allow` list and no `hooks` key anywhere.
    with tempfile.TemporaryDirectory() as td:
        ws = pathlib.Path(td)
        write_settings(ws, {"permissions": {"allow": ["Bash(git status:*)"]}}, "settings.local.json")
        proc = run("--workspace", td)
        assert proc.returncode == 1, proc.stdout
        assert "no `hooks` block" in proc.stdout, proc.stdout


def test_the_hubs_own_settings_copied_verbatim_is_not_wired():
    """The tempting wrong fix, and the reason the template exists.

    `.claude/settings.json` spells every path `$CLAUDE_PROJECT_DIR/...`. Copied
    into the workspace it is valid JSON with a real `hooks` block -- it passes
    every check that stops at config presence -- and resolves to nothing, because
    $CLAUDE_PROJECT_DIR is the workspace. If this test ever goes green, the
    verifier has stopped detecting #1042 itself.
    """
    with tempfile.TemporaryDirectory() as td:
        ws = pathlib.Path(td)
        (ws / ".claude").mkdir(parents=True)
        (ws / ".claude" / "settings.json").write_text(
            HUB_SETTINGS.read_text(encoding="utf-8"), encoding="utf-8"
        )
        proc = run("--workspace", td)
        assert proc.returncode == 1, proc.stdout
        assert "do not exist" in proc.stdout, proc.stdout


def test_the_rendered_template_is_wired():
    with tempfile.TemporaryDirectory() as td:
        proc = run("--render", "--workspace", td, "--hub-clone", str(REPO_ROOT))
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "HOOKS: wired" in proc.stdout, proc.stdout


# --------------------------------------------------------------------------
# Polarity (#1027). A guard is only proven by BOTH verdicts landing; each stub
# below satisfies exactly one of them and must still be reported as not wired.
# --------------------------------------------------------------------------


def test_a_guard_stubbed_to_always_allow_is_not_wired():
    with tempfile.TemporaryDirectory() as td:
        ws = pathlib.Path(td)
        write_settings(ws, guard_config(stub(ws, "always_allow", 0)))
        proc = run("--workspace", td)
        assert proc.returncode == 1, proc.stdout
        assert "expected to block" in proc.stdout, proc.stdout


def test_a_guard_stubbed_to_always_block_is_not_wired():
    # The other stub. A guard that refuses `git status` blocks step 0 of every
    # run, so "it blocks things" is not on its own the property we want.
    with tempfile.TemporaryDirectory() as td:
        ws = pathlib.Path(td)
        write_settings(ws, guard_config(stub(ws, "always_block", 2)))
        proc = run("--workspace", td)
        assert proc.returncode == 1, proc.stdout
        assert "expected to allow" in proc.stdout, proc.stdout


def test_a_guard_that_crashes_is_reported_as_crashed_not_as_a_detection():
    """L203, applied to the probe rather than to a mutation.

    A guard that cannot start exits non-zero exactly like a guard that caught
    you, and it fails in the flattering direction. The report must say the guard
    did not run -- not that it blocked.
    """
    with tempfile.TemporaryDirectory() as td:
        ws = pathlib.Path(td)
        write_settings(ws, guard_config(stub(ws, "crashes", 7)))
        proc = run("--workspace", td)
        assert proc.returncode == 1, proc.stdout
        assert "did not run" in proc.stdout, proc.stdout
        assert "expected to block" not in proc.stdout, proc.stdout


def test_a_hook_path_that_does_not_exist_is_named_in_the_report():
    with tempfile.TemporaryDirectory() as td:
        ws = pathlib.Path(td)
        write_settings(ws, guard_config(str(ws / "nope" / "guard_bash.py")))
        proc = run("--workspace", td, "--json")
        report = json.loads(proc.stdout)
        assert proc.returncode == 1, proc.stdout
        assert report["missing_paths"], report
        # Not just the field -- the REFUSAL. Populating `missing_paths` and then
        # carrying on lands on a different problem ("no Bash matcher") and still
        # exits 1, so an assertion on the data alone cannot tell the two apart.
        # Mutation M4 (drop the missing-path early return) survives without this.
        assert any("do not exist" in p for p in report["problems"]), report["problems"]


def test_a_relative_hook_path_resolves_against_the_workspace_not_the_cwd():
    """A hook path that is still relative after expansion is the workspace's.

    Measured on `36ca99c`, before the fix: a workspace whose only hook names a
    bare `guard_rel.py`, with that file absent from the workspace and present
    only in the directory the verifier was invoked from, reported
    `missing_paths: []`, `problems: []`, `wired: True`, rc=0. The verifier
    cleared a session by probing a guard that was not part of it -- the exact
    L218 false green it exists to make impossible, reached through the cwd
    instead of through `$CLAUDE_PROJECT_DIR`.
    """
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        ws, elsewhere = root / "workspace", root / "elsewhere"
        (ws / ".claude").mkdir(parents=True)
        elsewhere.mkdir()
        write_settings(ws, guard_config("guard_rel.py"))
        behaving_guard(elsewhere / "guard_rel.py")
        proc = run("--workspace", str(ws), "--json", cwd=str(elsewhere))
        report = json.loads(proc.stdout)
        assert proc.returncode == 1, proc.stdout
        assert not report["wired"], report
        assert report["missing_paths"], report
        # The reported path must be the one the SESSION would load, so a reader
        # of the report is told where to put the file.
        named = pathlib.Path(report["missing_paths"][0]["path"])
        assert named == ws / "guard_rel.py", named


def test_a_relative_hook_path_in_the_workspace_is_still_found():
    """Polarity control for the test above.

    Resolving relative paths against the workspace must FIND them there; a fix
    that simply stopped accepting relative paths would satisfy the case above
    and break every config that uses one.
    """
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        ws, elsewhere = root / "workspace", root / "elsewhere"
        (ws / ".claude").mkdir(parents=True)
        elsewhere.mkdir()
        write_settings(ws, guard_config("guard_rel.py"))
        behaving_guard(ws / "guard_rel.py")
        proc = run("--workspace", str(ws), "--json", cwd=str(elsewhere))
        report = json.loads(proc.stdout)
        assert not report["missing_paths"], report
        assert report["wired"], report
        assert proc.returncode == 0, proc.stdout


def test_hooks_present_but_no_bash_matcher_is_not_wired():
    with tempfile.TemporaryDirectory() as td:
        ws = pathlib.Path(td)
        real = str(REPO_ROOT / ".claude" / "hooks" / "post_edit.py")
        write_settings(
            ws,
            {
                "hooks": {
                    "PostToolUse": [
                        {
                            "matcher": "Edit",
                            "hooks": [{"type": "command", "command": f'python3 "{real}"'}],
                        }
                    ]
                }
            },
        )
        proc = run("--workspace", td)
        assert proc.returncode == 1, proc.stdout
        assert "not wired" in proc.stdout.lower(), proc.stdout


# --------------------------------------------------------------------------
# The template itself.
# --------------------------------------------------------------------------


def test_an_msys_path_becomes_a_windows_path_on_windows():
    """git-bash `$PWD` is `/c/...`, and native Windows Python misreads it.

    Windows treats a leading `/c/` as DRIVE-RELATIVE, so `/c/Users/x` resolves to
    `C:\\c\\Users\\x` -- a directory that does not exist. CLAUDE.md records this
    trap, and the Conductor's shell is git-bash, so `$PWD` is the value this
    script is most likely to be handed on the one host it exists for.

    Reported by Copilot on #1223, in three places at once (the script and both
    doc snippets). The consequence is not only a false NOT WIRED: with --render
    it creates `C:/c/Users/.../.claude/` and writes the settings THERE, then
    reports success for a config the real session will never read.
    """
    f = _vch().from_msys_path
    assert f("/c/Users/clark/Claude_AI_OS_Routine", windows=True) == "C:/Users/clark/Claude_AI_OS_Routine"
    assert f("/d/repos/hub", windows=True) == "D:/repos/hub"
    assert f("/c", windows=True) == "C:/"


def test_an_msys_looking_path_is_left_alone_off_windows():
    """Polarity control, and the reason the translation is platform-gated.

    `/c/data` is an ordinary absolute directory on Linux. A fix that rewrote the
    prefix unconditionally would satisfy the test above while corrupting every
    POSIX path starting with a single-letter directory -- so the Linux branch is
    a real behaviour with its own assertion, not an untested default.
    """
    f = _vch().from_msys_path
    assert f("/c/Users/clark/ws", windows=False) == "/c/Users/clark/ws"
    assert f("/d/repos/hub", windows=False) == "/d/repos/hub"


def test_a_path_that_is_not_msys_survives_either_platform():
    """Only the `/<single letter>/` shape is touched, on either branch."""
    f = _vch().from_msys_path
    for value in ("/home/user", "C:/Users/x", "rel/path", "/usr/local/bin", ""):
        for windows in (True, False):
            assert f(value, windows=windows) == value, (value, windows)


def test_the_platform_default_matches_an_explicit_call_for_this_host():
    """The `windows=None` default must agree with the host it actually runs on.

    Without this, the injectable parameter that makes both branches testable
    could drift from the real behaviour and every test above would still pass.
    """
    f = _vch().from_msys_path
    assert f("/c/x") == f("/c/x", windows=sys.platform == "win32")


def test_the_template_is_tracked_and_valid_json():
    # AC1: the answer to "where does the Conductor's hook config live" has to be
    # a file a PR can review. An untracked local settings.json is not one.
    assert TEMPLATE.is_file(), f"{TEMPLATE} is the tracked answer to #1042 AC1"
    json.loads(TEMPLATE.read_text(encoding="utf-8"))


def test_every_template_hook_command_uses_the_placeholder_not_claude_project_dir():
    """$CLAUDE_PROJECT_DIR in a template hook command would reintroduce the whole bug.

    Asserted over the extracted `command` strings, not over the file's raw text:
    the template's `_comment` explains what $CLAUDE_PROJECT_DIR resolves to and
    why that is wrong here, and a raw-text scan cannot tell an explanation from
    a use. Scanning the text would fail on the documentation of the very bug.
    """
    data = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    commands = [c for _, _, c in _vch().iter_hook_commands(data)]
    assert commands, "template wires no hooks at all"
    for command in commands:
        assert "CLAUDE_PROJECT_DIR" not in command, (
            f"{command!r}: the workspace's project dir is not the clone (L218)"
        )
        assert PLACEHOLDER in command, command


def test_the_template_covers_every_hook_event_the_hub_wires():
    """Drift guard: a hook added to the hub must reach the Conductor too.

    Compared by (event, script basename) rather than by full path -- the paths
    differ by design, which is the entire point of the template, so comparing
    them would only ever assert that the template had not been written.
    """
    vch = _vch()

    def pairs(path: pathlib.Path) -> set:
        data = json.loads(path.read_text(encoding="utf-8"))
        out = set()
        for event, matcher, command in vch.iter_hook_commands(data):
            for script in vch.script_paths(command):
                out.add((event, matcher, pathlib.PurePosixPath(script).name))
        return out

    hub, template = pairs(HUB_SETTINGS), pairs(TEMPLATE)
    assert hub == template, f"only in hub: {hub - template}; only in template: {template - hub}"


def test_render_refuses_to_clobber_an_existing_settings_file_without_force():
    with tempfile.TemporaryDirectory() as td:
        ws = pathlib.Path(td)
        write_settings(ws, {"permissions": {"allow": []}})
        proc = run("--render", "--workspace", td, "--hub-clone", str(REPO_ROOT))
        assert proc.returncode == 2, proc.stdout + proc.stderr
        assert "--force" in proc.stderr, proc.stderr
        # and it really did not overwrite
        assert "permissions" in (ws / ".claude" / "settings.json").read_text(encoding="utf-8")


def test_rendered_settings_carry_no_leftover_placeholder():
    with tempfile.TemporaryDirectory() as td:
        ws = pathlib.Path(td)
        run("--render", "--workspace", td, "--hub-clone", str(REPO_ROOT))
        body = (ws / ".claude" / "settings.json").read_text(encoding="utf-8")
        assert PLACEHOLDER not in body
        assert str(REPO_ROOT.as_posix()) in body


def test_render_and_json_together_still_emit_parseable_json():
    """`--render --json` must not put a status line ahead of the report.

    Found by Copilot on #1223. stdout belongs to the data whenever --json is in
    play; a render status line is not data, so it goes to stderr. Cheap to get
    wrong because neither flag is broken on its own -- only the combination is,
    and no test exercised the combination.
    """
    with tempfile.TemporaryDirectory() as td:
        proc = run("--render", "--json", "--workspace", td, "--hub-clone", str(REPO_ROOT))
        try:
            report = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            # Asserted, not left to raise: a non-AssertionError ends the module
            # and the PASSes already printed read as a green roster (L82). The
            # runner catches AssertionError only.
            raise AssertionError(
                f"--json stdout is not parseable ({exc}); first line: {proc.stdout.splitlines()[:1]}"
            ) from None
        assert report["wired"] is True, report
        assert "rendered" in proc.stderr, proc.stderr


def test_the_json_report_carries_a_start_line_in_both_directions():
    """AC2: the START comment says which state the run is in, not only the bad one."""
    with tempfile.TemporaryDirectory() as td:
        bad = json.loads(run("--workspace", td, "--json").stdout)
        run("--render", "--workspace", td, "--hub-clone", str(REPO_ROOT))
        good = json.loads(run("--workspace", td, "--json").stdout)
    assert "NOT WIRED" in bad["start_line"], bad
    assert bad["wired"] is False
    assert "wired" in good["start_line"] and "NOT WIRED" not in good["start_line"], good
    assert good["wired"] is True


def test_the_runbook_exists_and_states_the_chosen_option():
    doc = REPO_ROOT / "docs" / "runbooks" / "conductor-hook-wiring.md"
    assert doc.is_file(), "AC1 asks for the choice to be stated somewhere reviewable"
    text = doc.read_text(encoding="utf-8")
    assert "#1042" in text
    assert "verify-conductor-hooks.py" in text


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
