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
import re
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
SCRIPT = REPO_ROOT / "scripts" / "verify-conductor-hooks.py"
TEMPLATE = REPO_ROOT / ".claude" / "conductor" / "settings.template.json"
HUB_SETTINGS = REPO_ROOT / ".claude" / "settings.json"
PLACEHOLDER = "__HUB_CLONE__"


def run(
    *args: str, cwd: str | None = None, project_dir: str | None = None
) -> subprocess.CompletedProcess:
    """Invoke the verifier. Full env (never a scrubbed dict -- CLAUDE.md), pinned codec (#945).

    `CLAUDE_PROJECT_DIR` is removed unless a test sets it. It is inherited from
    whatever session runs the suite, and it decides whether a no-argument call
    counts as *stated* or *inferred* (#1237) -- so leaving it ambient would make
    the workspace-provenance tests pass or fail according to who ran them, which
    is the one thing a test about provenance must not do.
    """
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        # Written as an inline literal, not a prepared dict: the encoding scanner
        # (#962) reads this call statically and cannot follow a variable, so
        # `env=env` fails `check-subprocess-encoding.py` even when the pin is
        # correct.
        #
        # `CLAUDE_PROJECT_DIR` is genuinely ABSENT when a test does not set it,
        # rather than present-but-empty. An earlier version passed `"" `and leaned
        # on the verifier testing it for truthiness -- which works today and
        # couples these tests to an implementation detail of the thing they test:
        # if the verifier ever switched to `"CLAUDE_PROJECT_DIR" in os.environ`,
        # every provenance test would silently start exercising the `env` branch
        # while still passing. The comprehension also strips an inherited value,
        # so a suite run from a rooted session cannot leak one in.
        env={
            **{k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"},
            "PYTHONIOENCODING": "utf-8",
            **({"CLAUDE_PROJECT_DIR": project_dir} if project_dir is not None else {}),
        },
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


# --------------------------------------------------------------------------
# The multi-repo cloud worker (#1237). L218 named the Conductor as the only
# unguarded session and the sandboxed agents as the protected class. A scheduled
# cloud worker clones five FFC repos side by side and runs with its project root
# set to their PARENT, so it is in the unguarded population too -- and it is the
# population that does the issue->PR work.
#
# Measured in the session that filed this: project root `/home/user`, no
# `/home/user/.claude` at all, the hub's `.claude/settings.json` present with all
# four hook events and never loaded. Running this very script from inside the
# clone with no arguments printed `HOOKS: wired ... exit 0`.
# --------------------------------------------------------------------------

WORKER_REPOS = (
    "FFC-Cloudflare-Automation",
    "FFC-EX-canary",
    "FFC-IN-FFC_Single_Page_Template",
    "FFC-IN-Footer_Only_Template",
    "FFC-IN-ffcadmin.org",
)


def worker_layout(td: str, *, ship_hooks: bool = True) -> tuple[pathlib.Path, pathlib.Path]:
    """Build the five-repo sandbox layout. Returns (session_root, hub_clone).

    The hub clone gets a `$CLAUDE_PROJECT_DIR`-spelled settings block and a real
    two-sided guard, exactly like the tracked `.claude/settings.json`. That is
    what makes the layout dangerous rather than merely wrong: pointed at the
    clone, every check the verifier runs genuinely passes.
    """
    root = pathlib.Path(td) / "home" / "user"
    root.mkdir(parents=True)
    for name in WORKER_REPOS:
        (root / name / ".git").mkdir(parents=True)
    hub = root / WORKER_REPOS[0]
    if ship_hooks:
        hooks = hub / ".claude" / "hooks"
        hooks.mkdir(parents=True)
        behaving_guard(hooks / "guard_bash.py")
        # Every OTHER hook the template names gets an inert stub, so this fake
        # clone can stand in as a `--hub-clone` and a render resolves fully.
        # Derived from the template rather than hard-coded: if the template
        # gains a hook, the fixture grows one too, instead of the render
        # silently reporting a missing path that has nothing to do with the
        # layout under test.
        for name in sorted(set(re.findall(r"/([A-Za-z_]+\.py)", TEMPLATE.read_text(encoding="utf-8")))):
            target = hooks / name
            if not target.exists():
                target.write_text("import sys; sys.exit(0)\n", encoding="utf-8")
        write_settings(
            hub,
            guard_config("$CLAUDE_PROJECT_DIR/.claude/hooks/guard_bash.py"),
        )
    return root, hub


def test_a_worker_layout_inferred_from_inside_a_clone_is_not_reported_as_wired():
    """The #1237 false green, pinned.

    Before the provenance check this printed `HOOKS: wired` and exited 0 in a
    session that loads no hooks whatsoever. If this test ever goes green again,
    the verifier has started certifying itself.
    """
    with tempfile.TemporaryDirectory() as td:
        _, hub = worker_layout(td)
        proc = run(cwd=str(hub))
        assert proc.returncode == 1, proc.stdout + proc.stderr
        assert "UNVERIFIED" in proc.stdout, proc.stdout
        assert "HOOKS: wired" not in proc.stdout, proc.stdout


def test_the_same_clone_is_still_wired_when_the_workspace_is_stated():
    """The refusal must key on PROVENANCE, not on the config.

    Same bytes on disk as the test above; the only difference is that the
    workspace was stated. A refusal that fired here too would just be the
    verifier refusing to work, which would be indistinguishable from a fix.
    """
    with tempfile.TemporaryDirectory() as td:
        _, hub = worker_layout(td)
        proc = run("--workspace", str(hub))
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "HOOKS: wired" in proc.stdout, proc.stdout


def test_the_worker_session_root_itself_reports_not_wired():
    """The true answer for the session #1237 was filed from."""
    with tempfile.TemporaryDirectory() as td:
        root, _ = worker_layout(td)
        proc = run("--workspace", str(root))
        assert proc.returncode == 1, proc.stdout
        assert "NOT WIRED" in proc.stdout, proc.stdout


def test_rendering_into_the_worker_session_root_wires_it():
    """AC2: the remedy has to actually work for the five-repo layout, not only
    for the Conductor's single-workspace one."""
    with tempfile.TemporaryDirectory() as td:
        root, hub = worker_layout(td)
        # `--hub-clone` is the fixture's own clone, not REPO_ROOT: pointing at the
        # real checkout would make this test depend on the repo's live
        # `.claude/hooks/` and pass or fail for reasons unrelated to the five-repo
        # layout it is named for. The real template against the real hooks is
        # already covered by `test_the_rendered_template_is_wired`.
        proc = run("--render", "--workspace", str(root), "--hub-clone", str(hub))
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "HOOKS: wired" in proc.stdout, proc.stdout
        assert (root / ".claude" / "settings.json").is_file()
        # The rendered config must point into the fixture's clone -- if it still
        # named REPO_ROOT the assertions above would pass while proving nothing
        # about this layout.
        #
        # `as_posix()`, not `str()`. `render()` substitutes the hub path through
        # `hub_clone.as_posix()` deliberately (a backslash would make the settings
        # invalid JSON, and Claude Code answers invalid JSON by loading NO settings
        # -- silently unguarded by the very command meant to fix it). `str(hub)` is
        # the backslash spelling on Windows, so this assertion could never hold on
        # the Conductor's own platform and always held on CI's ubuntu runner. It
        # was red on `main` for exactly that reason and no CI run could show it.
        assert hub.as_posix() in (root / ".claude" / "settings.json").read_text(
            encoding="utf-8"
        )
        # And the five sibling clones are untouched -- the fix belongs to the
        # session root, never to a repo checkout that a PR would then carry.
        assert not (hub / ".claude" / "settings.local.json").exists()


def test_the_refusal_names_the_session_root_it_detected():
    """A refusal that does not say what to pass instead just relocates the problem."""
    with tempfile.TemporaryDirectory() as td:
        root, hub = worker_layout(td)
        proc = run("--json", cwd=str(hub))
        assert proc.returncode == 1, proc.stdout
        report = json.loads(proc.stdout)
        assert report["self_certifying"] is True, report
        assert report["workspace_source"] == "inferred", report
        assert report["candidate_session_root"] == str(root), report
        assert "UNVERIFIED" in report["start_line"], report


def test_claude_project_dir_counts_as_stated_not_inferred():
    """The session sets this variable, so it is a statement of the real root.

    Without this branch the check would refuse every session that *is* correctly
    rooted at the clone -- the false red that would get the whole thing reverted.
    """
    with tempfile.TemporaryDirectory() as td:
        _, hub = worker_layout(td)
        proc = run(cwd=str(hub), project_dir=str(hub))
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "HOOKS: wired" in proc.stdout, proc.stdout


def test_an_inferred_workspace_that_ships_no_hooks_is_still_measured():
    """Narrow targeting: the hazard is self-certification, not inference itself.

    A workspace whose settings name a guard living somewhere else cannot grade
    itself, so inferring it is harmless and the verdict must still be computed.
    Refusing here would make the check a blanket "always pass --workspace" nag.
    """
    with tempfile.TemporaryDirectory() as td:
        root, _ = worker_layout(td, ship_hooks=False)
        elsewhere = pathlib.Path(td) / "guards"
        elsewhere.mkdir()
        behaving_guard(elsewhere / "guard_bash.py")
        ws = root / WORKER_REPOS[0]
        write_settings(ws, guard_config(str(elsewhere / "guard_bash.py")))
        proc = run(cwd=str(ws))
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "HOOKS: wired" in proc.stdout, proc.stdout


def test_an_empty_hooks_dir_gets_the_missing_path_diagnostics_not_the_refusal():
    """An empty `.claude/hooks/` cannot self-certify, so refusing on it is wrong twice.

    The refusal's own wording claims the workspace "ships the very
    `.claude/hooks/`" under test, which is false for an empty directory; and it
    replaces the strictly more useful report that names each hook path that did
    not resolve. Nothing can resolve INTO an empty directory, so there is no
    false green to prevent here.
    """
    with tempfile.TemporaryDirectory() as td:
        ws = pathlib.Path(td) / "clone"
        (ws / ".claude" / "hooks").mkdir(parents=True)
        write_settings(ws, guard_config("$CLAUDE_PROJECT_DIR/.claude/hooks/guard_bash.py"))
        proc = run(cwd=str(ws))
        assert proc.returncode == 1, proc.stdout
        assert "UNVERIFIED" not in proc.stdout, proc.stdout
        assert "NOT WIRED" in proc.stdout, proc.stdout
        assert "do not exist" in proc.stdout, proc.stdout


def test_one_hook_script_is_enough_to_be_self_certifying():
    """The other side of that line: a populated dir must still be refused.

    Asserted next to the empty case on purpose -- narrowing a safeguard is only
    safe if the narrowing is pinned in both directions, or the next edit quietly
    turns "at least one .py" into "the directory exists" or into nothing at all.
    """
    with tempfile.TemporaryDirectory() as td:
        ws = pathlib.Path(td) / "clone"
        (ws / ".claude" / "hooks").mkdir(parents=True)
        behaving_guard(ws / ".claude" / "hooks" / "guard_bash.py")
        write_settings(ws, guard_config("$CLAUDE_PROJECT_DIR/.claude/hooks/guard_bash.py"))
        proc = run(cwd=str(ws))
        assert proc.returncode == 1, proc.stdout
        assert "UNVERIFIED" in proc.stdout, proc.stdout


def test_a_lone_clone_with_no_siblings_names_no_session_root():
    """`candidate_session_root` is a hint, and a wrong hint is worse than none.

    One checkout under a parent says nothing about where the session is rooted,
    so the parent must not be offered as somewhere to write a settings file.
    """
    with tempfile.TemporaryDirectory() as td:
        lone = pathlib.Path(td) / "solo" / "only-repo"
        (lone / ".git").mkdir(parents=True)
        (lone / ".claude" / "hooks").mkdir(parents=True)
        behaving_guard(lone / ".claude" / "hooks" / "guard_bash.py")
        write_settings(lone, guard_config("$CLAUDE_PROJECT_DIR/.claude/hooks/guard_bash.py"))
        proc = run("--json", cwd=str(lone))
        report = json.loads(proc.stdout)
        assert report["self_certifying"] is True, report
        assert report["candidate_session_root"] is None, report


def test_a_parent_that_already_has_its_own_claude_is_still_named():
    """An already-configured parent is the MOST likely session root, not the least.

    An earlier draft excluded it, reasoning that naming it was advice to clobber
    a config. That is true of a `--render` suggestion and this hint does not feed
    one -- the refusal's remedy is a read-only re-measure. The exclusion was
    measured degrading the message on a real box: once `/home/user/.claude`
    existed, the remedy fell back to a `<session project root>` placeholder at
    exactly the moment it could have named the answer.
    """
    with tempfile.TemporaryDirectory() as td:
        root, hub = worker_layout(td)
        (root / ".claude").mkdir()
        proc = run("--json", cwd=str(hub))
        report = json.loads(proc.stdout)
        assert report["candidate_session_root"] == str(root), report


# --------------------------------------------------------------------------
# Review round 1 (Copilot on #1253). Four findings, all real; these pin them.
# --------------------------------------------------------------------------


def test_an_empty_workspace_is_a_usage_error_not_an_explicit_statement():
    """`--workspace ""` reproduced the #1237 false green through the fix's own flag.

    `Path("").resolve()` is the cwd, so an empty value is the inferred fallback
    wearing the explicit flag's clothes: it scored as `explicit`, skipped the
    self-certification refusal, and printed `HOOKS: wired ... exit 0` from inside
    a hook-shipping clone. Measured before the guard existed.
    """
    with tempfile.TemporaryDirectory() as td:
        _, hub = worker_layout(td)
        for value in ("", "   "):
            proc = run("--workspace", value, cwd=str(hub))
            assert proc.returncode == 2, (value, proc.stdout, proc.stderr)
            assert "HOOKS: wired" not in proc.stdout, (value, proc.stdout)
            assert "--workspace was empty" in proc.stderr, (value, proc.stderr)


def test_a_padded_workspace_is_stripped_before_it_is_resolved():
    """A LEADING space makes the path relative, so the stated root is not measured.

    Measured: `Path(" /home/user ").resolve()` is `<cwd>/ /home/user `, which does
    not exist -- so a correctly stated session root reports NOT WIRED. It errs the
    safe way and is still not harmless, because the remedy then offers `--render`
    into that junk path and render creates its parents.
    """
    with tempfile.TemporaryDirectory() as td:
        _, hub = worker_layout(td)
        padded = run("--workspace", f"  {hub}  ")
        assert padded.returncode == 0, padded.stdout + padded.stderr
        assert "HOOKS: wired" in padded.stdout, padded.stdout
        # The verdict must be the same one the unpadded path gets, not merely
        # non-zero: a bare exit assertion here would pass on a NOT WIRED too.
        assert padded.stdout == run("--workspace", str(hub)).stdout


def test_the_env_branch_is_reached_by_a_real_variable_not_an_empty_one():
    """The helper must leave `CLAUDE_PROJECT_DIR` absent, not present-and-empty.

    Otherwise every provenance test depends on the verifier reading that variable
    for truthiness, and would keep passing -- while measuring something else --
    if it ever switched to an `in os.environ` check.
    """
    with tempfile.TemporaryDirectory() as td:
        _, hub = worker_layout(td)
        unset = json.loads(run("--json", cwd=str(hub)).stdout)
        assert unset["workspace_source"] == "inferred", unset
        given = json.loads(run("--json", cwd=str(hub), project_dir=str(hub)).stdout)
        assert given["workspace_source"] == "env", given


def test_an_unknown_workspace_source_raises_rather_than_certifying():
    """A free-form string gating a safeguard fails OPEN on a typo.

    `"inferrred"` compares unequal to `SOURCE_INFERRED`, which would skip the
    refusal and certify the exact case this exists to catch. Raising is the
    fail-closed choice: an unknown provenance is not a provenance.
    """
    vch = _vch()
    assert vch.SOURCES == frozenset({"explicit", "env", "inferred"}), vch.SOURCES
    with tempfile.TemporaryDirectory() as td:
        _, hub = worker_layout(td)
        try:
            vch.verify(pathlib.Path(hub), source="inferrred")
        except ValueError as exc:
            assert "inferrred" in str(exc), exc
        else:
            raise AssertionError("a typo'd source was accepted and the safeguard skipped")


def test_verify_refuses_to_run_without_a_stated_provenance():
    """`source` has no default, so forgetting it cannot silently mean "trusted".

    A default of `SOURCE_EXPLICIT` would give the most-trusting provenance to the
    caller who thought about it least, which is the wrong way round for a value
    that gates the refusal. Keyword-only additionally stops a positional call
    from binding some other string as provenance.
    """
    vch = _vch()
    with tempfile.TemporaryDirectory() as td:
        _, hub = worker_layout(td)
        try:
            vch.verify(pathlib.Path(hub))
        except TypeError as exc:
            assert "source" in str(exc), exc
        else:
            raise AssertionError("verify() ran with no provenance and did not object")
        # And positionally, which is the other way a caller could get it wrong.
        try:
            vch.verify(pathlib.Path(hub), "inferred")
        except TypeError:
            pass
        else:
            raise AssertionError("verify() accepted provenance positionally")


def test_the_self_certifying_refusal_is_the_only_thing_that_costs_a_parent_scan():
    """The sibling hint is computed lazily: `null` means "no hint was needed".

    Every non-refusal run would otherwise pay a directory scan of the parent for
    a string nobody prints.
    """
    with tempfile.TemporaryDirectory() as td:
        _, hub = worker_layout(td)
        stated = json.loads(run("--json", "--workspace", str(hub)).stdout)
        assert stated["wired"] is True, stated
        assert stated["candidate_session_root"] is None, stated
        inferred = json.loads(run("--json", cwd=str(hub)).stdout)
        assert inferred["self_certifying"] is True, inferred
        assert inferred["candidate_session_root"] is not None, inferred


def test_the_printed_remedy_quotes_paths_that_contain_spaces():
    """The remedy is printed to be pasted, and both hosts have spaces in paths.

    Unquoted, `--workspace C:/My Workspace` arrives as two arguments and the
    remedy fails in a way that reads as the script being broken.
    """
    with tempfile.TemporaryDirectory() as td:
        spaced = pathlib.Path(td) / "My Workspace"
        spaced.mkdir()
        proc = run("--workspace", str(spaced))
        assert proc.returncode == 1, proc.stdout
        assert "NOT WIRED" in proc.stdout, proc.stdout
        remedy = [ln for ln in proc.stdout.splitlines() if "--render" in ln]
        assert remedy, proc.stdout
        # shlex.quote wraps a spaced path in single quotes; the bare form would
        # split on the space and silently target `My`.
        assert f"--workspace '{spaced}'" in remedy[0], remedy[0]


# --------------------------------------------------------------------------
# Which directory the #1237 refusal NAMES as the likely session root (run 156).
#
# The refusal is only as useful as its hint, and a wrong hint is worse than
# none: it sends the reader to re-measure a directory that is nobody's session
# root, where they get a confident `NOT WIRED` and a `--render` offer that would
# write settings no session reads. The function's own docstring said so before
# it had this bug.
#
# The two layouts below are the two real scheduled sessions. They differ in
# exactly one way -- where the clones sit relative to the root -- and the
# sibling-clone heuristic alone answers the second correctly and the first
# wrongly.
# --------------------------------------------------------------------------


def _layout(root: pathlib.Path, clone_parent: pathlib.Path, n_clones: int = 3):
    """Build `n_clones` sibling checkouts under `clone_parent`; return the first."""
    clone_parent.mkdir(parents=True, exist_ok=True)
    first = None
    for i in range(n_clones):
        clone = clone_parent / f"repo-{i}"
        (clone / ".git").mkdir(parents=True)
        first = first or clone
    return first


def _hint(clone: pathlib.Path):
    """`(candidate_session_root, basis)` as the verifier's own code computes it."""
    module = _vch()
    return (
        module.candidate_session_root(clone),
        module.settings_bearing_ancestor(clone),
    )


def test_the_conductor_shape_names_the_root_that_holds_the_settings_not_the_clones_parent():
    """Clones in `repos/`, settings one level above it -- the Conductor.

    Fails on the pre-fix tree, which returns the clones' parent (`<root>/repos`).
    That directory has no `.claude`, so re-measuring it reports NOT WIRED for a
    session that is wired -- which is what run 156 actually observed.
    """
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td) / "Claude_AI_OS_Routine"
        clone = _layout(root, root / "repos")
        (root / ".claude").mkdir(parents=True)
        (root / ".claude" / "settings.json").write_text("{}", encoding="utf-8")

        hint, configured = _hint(clone)
        assert hint == str(root), (
            f"expected the settings-bearing root {root!s}, got {hint!s} -- "
            f"naming the clones' parent sends the reader to a directory that is "
            f"nobody's project root"
        )
        assert configured == str(root), configured


def test_the_worker_shape_is_unchanged_when_no_ancestor_carries_settings():
    """Clones directly under the root, nothing configured yet -- the cloud worker.

    This is the case the sibling heuristic was written for and it must survive:
    before `--render` there is no `.claude` anywhere, so the evidence branch has
    nothing to say and the shape branch answers correctly.
    """
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td) / "home-user"
        clone = _layout(root, root)

        hint, configured = _hint(clone)
        assert configured is None, f"nothing is configured yet, got {configured!s}"
        assert hint == str(root), f"expected the sibling-clone parent {root!s}, got {hint!s}"


def test_a_lone_clone_with_no_configured_ancestor_still_yields_no_hint():
    """The guess-refusal is preserved: one checkout says nothing about a root."""
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td) / "solo"
        clone = _layout(root, root, n_clones=1)

        hint, configured = _hint(clone)
        assert configured is None, configured
        assert hint is None, f"a lone clone must not produce a hint, got {hint!s}"


def test_the_refusal_says_which_evidence_produced_its_hint():
    """`claude_settings` and `sibling_clones` are not equally trustworthy.

    The reader is about to act on the named directory, so the message has to
    distinguish "this one is already configured" from "these checkouts share a
    parent". Asserted on the printed text, because that is the only part a human
    triaging at 3am actually sees.
    """
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td) / "Claude_AI_OS_Routine"
        clone = _layout(root, root / "repos")
        (root / ".claude").mkdir(parents=True)
        (root / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
        # The clone must itself ship `.claude/hooks/` for the #1237 self-grading
        # refusal to fire at all -- that refusal is what carries the hint.
        (clone / ".claude" / "hooks").mkdir(parents=True)
        (clone / ".claude" / "hooks" / "guard_bash.py").write_text("", encoding="utf-8")

        proc = run(cwd=str(clone))
        assert "UNVERIFIED" in proc.stdout, proc.stdout
        assert "already carries a Claude settings file" in proc.stdout, proc.stdout
        assert str(root) in proc.stdout, proc.stdout


def _with_fake_home(fake_home: pathlib.Path, fn):
    """Run `fn()` with `Path.home()` answering `fake_home`, then restore.

    `Path.home()` is `os.path.expanduser("~")`, which reads `HOME` on POSIX and
    `USERPROFILE` on Windows, so both are set -- pinning only one makes the test
    pass on the platform that happens to run it and say nothing on the other.

    Restored in `finally`. Mutating `os.environ` is exactly the in-place edit
    CLAUDE.md/L182 records as dangerous when it is not paired with a restore, and
    here the blast radius is every later test in the module.
    """
    saved = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE")}
    os.environ["HOME"] = str(fake_home)
    os.environ["USERPROFILE"] = str(fake_home)
    try:
        return fn()
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_a_clone_directly_under_a_configured_home_names_home():
    """The cloud worker AFTER `--render --workspace /home/user` -- #1283 review.

    Its checkouts sit directly in the home directory, so the settings that render
    just wrote are at `workspace.parent`. The first fix for #1237 skipped home
    UNCONDITIONALLY, so this returned None and the refusal said `no Claude
    settings found above this clone` about a root carrying exactly that.

    The claim that made the skip look free was that the sibling branch answers
    `/home/user` anyway "by the other route" -- true only while two or more
    clones exist. `n_clones=1` is the state every worker passes through on its
    way to that shape, and there the fallback declines too, so BOTH routes were
    silent at once. Asserted on `configured`, not just `hint`: the routes return
    the same string and only the basis distinguishes them.
    """
    with tempfile.TemporaryDirectory() as td:
        home = pathlib.Path(td) / "home" / "user"
        clone = _layout(home, home, n_clones=1)
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "settings.json").write_text("{}", encoding="utf-8")

        hint, configured = _with_fake_home(home, lambda: _hint(clone))
        assert configured == str(home), (
            f"home is this clone's own parent and carries settings, "
            f"got {configured!s}"
        )
        assert hint == str(home), f"expected the configured home {home!s}, got {hint!s}"


def test_a_distant_home_ancestor_is_still_not_evidence():
    r"""...and the exclusion that narrowing relaxes is otherwise intact.

    `~/.claude` is the user-level config and exists on essentially every machine,
    so a clone nested somewhere beneath home must NOT resolve to home -- that is
    the wrong hint the skip was written for, and why an unbounded walk failed
    three pre-existing tests here by naming the real `C:\Users\clark`
    (`TemporaryDirectory()` sits under home on Windows).

    One hop is the whole difference between this and the test above, which is
    what makes the pair worth keeping adjacent.
    """
    with tempfile.TemporaryDirectory() as td:
        home = pathlib.Path(td) / "home" / "user"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
        nested = home / "work" / "checkouts"
        clone = _layout(nested, nested, n_clones=1)

        # Asserted as "not this directory" rather than "None": on Windows the
        # fixture lives under the REAL home, which carries a real `~/.claude`, so
        # once `Path.home()` is pointed at the fake one the walk can legitimately
        # reach the real one further up and answer with it. That is the exclusion
        # working, not failing. The property under test is that the DISTANT home
        # is not chosen, and stating it that way is true on both platforms --
        # `is None` would only ever have held on Linux.
        hint, configured = _with_fake_home(home, lambda: _hint(clone))
        assert configured != str(home), (
            f"a distant home ancestor must not count as evidence, got {configured!s}"
        )
        assert hint != str(home), f"the hint must not name a distant home, got {hint!s}"


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
