"""The check tools measure this checkout's own code, and stop each step once and cleanly.

Two worktrees that share one Python environment install the package from whichever ran
`uv sync` last. A mutation run in one then switches a rule off there while the tests load
the other's code, and every rule looks unguarded. So `scripts/mutate.py` and
`scripts/ratchet.py` ask the suite's own interpreter, in a process of its own, where the
package comes from, and refuse to go on when it is not this checkout's `src/`.
"""

import ast
import contextlib
import importlib.util
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import time

import pytest
from bounded import WITHIN, run_bounded

ROOT = pathlib.Path(__file__).parent.parent


def _loaded(name):
    spec = importlib.util.spec_from_file_location(f"{name}_own", ROOT / f"scripts/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _two_checkouts(tmp_path):
    """Two checkouts, each with the package in its own src/."""
    here, other = tmp_path / "here", tmp_path / "other"
    for checkout in (here, other):
        (checkout / "src" / "gizmo").mkdir(parents=True)
        (checkout / "src" / "gizmo" / "__init__.py").write_text("")
    return here, other


def _must_not_run(*_args):
    raise AssertionError("the suite ran")


def test_a_mutation_run_measures_only_this_checkout_s_own_code(tmp_path, monkeypatch):
    here, other = _two_checkouts(tmp_path)
    (here / "src" / "gizmo" / "core.py").write_text("# RULE: the gizmo is on\non = True\n")
    rows = here / "scripts/mutations/src/gizmo/core.py"
    rows.mkdir(parents=True)
    (rows / "the-gizmo-is-on.toml").write_text(
        '[[rule]]\nname = "the gizmo is on"\nfile = "src/gizmo/core.py"\n'
        "find = '^on = True$'\nreplace = 'on = False'\n"
    )
    mutate = _loaded("mutate")
    for name, value in {
        "ROOT": here,
        "ROWS": here / "scripts/mutations",
        "RECOVERY": here / "scripts/.mutate-recovery.json",
        "MUTATE_TMP": tmp_path / "mutate-tmp",
        "PACKAGE": "gizmo",
        "run_suite": _must_not_run,
    }.items():
        monkeypatch.setattr(mutate, name, value)
    monkeypatch.setattr("sys.argv", ["mutate.py"])
    monkeypatch.setenv("PYTHONPATH", str(other / "src"))  # the other checkout's install
    with pytest.raises(SystemExit) as refused:
        mutate.main()
    said = str(refused.value)
    assert str(other / "src" / "gizmo") in said
    assert str(here / "src") in said
    assert "uv sync" in said
    monkeypatch.setenv("PYTHONPATH", str(here / "src"))
    mutate.own_code(dict(os.environ))  # its own: no refusal


def test_the_ratchet_measures_only_the_code_of_the_tree_it_measures(tmp_path, monkeypatch):
    here, other = _two_checkouts(tmp_path)
    ratchet = _loaded("ratchet")
    monkeypatch.setattr(ratchet, "ROOT", here)
    monkeypatch.setattr(ratchet, "SOURCE", here / "src")
    monkeypatch.setattr(ratchet, "PACKAGE", here / "src" / "gizmo")
    monkeypatch.setattr(ratchet, "PYTHON", (sys.executable,))
    monkeypatch.setenv("PYTHONPATH", str(other / "src"))
    with pytest.raises(SystemExit) as refused:
        ratchet.measure()  # asks before it measures anything
    said = str(refused.value)
    assert str(other / "src" / "gizmo") in said
    assert str(here / "src") in said
    assert "uv sync" in said
    monkeypatch.setenv("PYTHONPATH", str(here / "src"))
    ratchet.own_code()


@pytest.mark.parametrize("script", ["mutate", "ratchet"])
def test_a_check_tool_never_imports_the_package_itself(script):
    """Its own import would answer for this process, not for the suite it is about to run.

    The ratchet runs a pull request's copy of itself in the base's environment, so an
    import here would even find the wrong tree's package.
    """
    tree = ast.parse((ROOT / f"scripts/{script}.py").read_text())
    nodes = list(ast.walk(tree))
    imports = [node for node in nodes if isinstance(node, ast.Import)]
    imported = {alias.name.split(".")[0] for node in imports for alias in node.names}
    imported |= {(node.module or "").split(".")[0] for node in nodes if isinstance(node, ast.ImportFrom)}
    assert "ams_mcp" not in imported


COUNTING_STEP = """
import os, pathlib, signal, sys, time
tmp = pathlib.Path(sys.argv[1])
heard = 0

def stop(signum, frame):
    global heard
    heard += 1

signal.signal(signal.SIGINT, stop)
(tmp / "step").write_text(f"{os.getpid()} {os.getpgid(0)}")  # the test ends it in any case
(tmp / "ready").write_text("")
deadline = time.monotonic() + 20  # never told: give up, rather than wait for ever
while not heard and time.monotonic() < deadline:
    time.sleep(0.05)
time.sleep(1)  # the clean-up: any second Ctrl-C would arrive in here
(tmp / "heard").write_text(str(heard))
"""
FORWARDING_RATCHET = """
import importlib.util, sys
spec = importlib.util.spec_from_file_location("ratchet", sys.argv[1])
ratchet = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ratchet)
with ratchet.stopped_cleanly():
    ratchet._run("uv", "run", "--no-project", "python", "-c", sys.argv[3], sys.argv[2])
"""


def _end_the_step(record):
    """End the step and what started it, which killing the ratchet leaves running.

    The step's group is its own session, and only that is killed: when the ratchet
    starts it without a session of its own, the group is this test run's, and killing
    it would end the run with it.
    """
    if not record.exists():
        return
    pid, group = (int(n) for n in record.read_text().split())
    with contextlib.suppress(ProcessLookupError):
        if group == os.getpgrp():
            os.kill(pid, signal.SIGKILL)
        else:
            os.killpg(group, signal.SIGKILL)


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no SIGINT to pass on")
@pytest.mark.skipif(
    shutil.which("uv") is None and not os.environ.get("CI"),
    reason="needs uv, which the ratchet runs its steps through (in CI, its absence fails instead)",
)
def test_a_stopped_uv_run_step_hears_ctrl_c_once(tmp_path):
    """The step is `uv run`, which passes Ctrl-C on to the Python it started.

    Sent to the whole group, the Python would hear it twice: once from the ratchet, once
    through uv. Its clean-up must run to the end on the one it gets.
    """
    # In CI this proof must not drop out unseen: there a missing uv fails, not skips.
    if shutil.which("uv") is None:
        pytest.fail("CI has no uv, so the proof that a stopped step hears Ctrl-C once cannot run")
    command = [sys.executable, "-c", FORWARDING_RATCHET, str(ROOT / "scripts/ratchet.py"), str(tmp_path)]
    command.append(COUNTING_STEP)
    ratchet = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        ready = tmp_path / "ready"
        deadline = time.monotonic() + WITHIN
        while not ready.exists():
            assert ratchet.poll() is None, ratchet.communicate()[0]
            assert time.monotonic() < deadline, "the step never started"
            time.sleep(0.05)
        ratchet.send_signal(signal.SIGTERM)  # to the ratchet alone, as when its job is cancelled
        out, _ = ratchet.communicate(timeout=WITHIN)
        assert (tmp_path / "heard").read_text() == "1", out
    finally:
        if ratchet.poll() is None:
            ratchet.kill()
            ratchet.wait()
        _end_the_step(tmp_path / "step")


def test_main_is_copied_by_its_id_whatever_protocol_git_is_set_to(tmp_path, monkeypatch):
    """Git's older protocol refuses a commit no branch points at, unless asked not to."""
    ratchet = _loaded("ratchet")
    settings = tmp_path / "gitconfig"
    settings.write_text("[protocol]\n\tversion = 0\n[user]\n\temail = t@example.org\n\tname = t\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(settings))
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=repo, check=True, capture_output=True, text=True, timeout=WITHIN
        ).stdout

    git("init", "-q", "-b", "main")
    (repo / "notes.txt").write_text("the old main\n")
    git("add", "notes.txt")
    git("commit", "-q", "-m", "the old main")
    old = git("rev-parse", "HEAD").strip()
    (repo / "notes.txt").write_text("the new main\n")
    git("commit", "-q", "-am", "the new main")
    monkeypatch.setattr(ratchet, "ROOT", repo)
    ratchet.copy_of(old, tmp_path / "base")
    assert (tmp_path / "base" / "notes.txt").read_text() == "the old main\n"


class _StoppedWhileWaiting:
    """A step whose wait is cut short by Ctrl-C, and which has or has not ended by then."""

    def __init__(self, ended):
        self.pid, self.returncode, self._ended, self._waits = 4321, None, ended, 0

    def wait(self):
        self._waits += 1
        if self._waits == 1:
            raise KeyboardInterrupt
        return 0

    def poll(self):
        return 0 if self._ended else None


def _stop_finds_no_step(ratchet, monkeypatch, *, ended):
    monkeypatch.setattr(ratchet.subprocess, "Popen", lambda *_args, **_kwargs: _StoppedWhileWaiting(ended))

    def no_such_process(*_args):
        raise ProcessLookupError

    monkeypatch.setattr(ratchet.os, "kill", no_such_process)


def test_a_step_that_ended_as_the_stop_came_is_not_an_error(monkeypatch):
    """The stop is what the caller hears, not that the step had already gone."""
    ratchet = _loaded("ratchet")
    _stop_finds_no_step(ratchet, monkeypatch, ended=True)
    with pytest.raises(KeyboardInterrupt):
        ratchet._run("step")  # noqa: SLF001


def test_a_live_step_the_stop_cannot_reach_is_an_error(monkeypatch):
    """Ignoring that would leave a rule switched off with nobody told to put it back."""
    ratchet = _loaded("ratchet")
    _stop_finds_no_step(ratchet, monkeypatch, ended=False)
    # BaseException: a swallowed error lets the Ctrl-C through, which must fail this test,
    # not stop the whole test run.
    with pytest.raises(BaseException) as raised:  # noqa: PT011
        ratchet._run("step")  # noqa: SLF001
    assert raised.type is ProcessLookupError


@pytest.mark.parametrize(
    ("ci", "outcome"), [("", "1 skipped"), ("true", "1 failed")], ids=["locally", "in CI"]
)
def test_without_uv_the_ctrl_c_test_is_skipped_locally_and_fails_in_ci(tmp_path, ci, outcome):
    """A machine without uv cannot run the proof; only CI must never let it pass unseen."""
    bare = tmp_path / "bin"  # a path holding this Python and nothing else, so no uv
    bare.mkdir()
    (bare / "python").symlink_to(sys.executable)
    env = {**os.environ, "PATH": str(bare), "CI": ci}
    node = f"{pathlib.Path(__file__).name}::test_a_stopped_uv_run_step_hears_ctrl_c_once"
    said = run_bounded(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            f"--basetemp={tmp_path / 'run'}",
            node,
        ],
        cwd=pathlib.Path(__file__).parent,
        env=env,
        capture_output=True,
        text=True,
    )
    assert outcome in said.stdout, said.stdout
