# SPDX-License-Identifier: GPL-3.0-or-later
"""The mutation tool and the ratchet leave nothing in the temporary folder, however they end.

Both run the whole suite, many times over, and the ratchet also copies main and
installs it. Each run now keeps everything it and its commands make in one folder
of its own, removed when it ends, also when it is stopped with SIGTERM or SIGHUP,
as a timeout or a closed terminal does. A run killed outright is swept at the next
start. These tests point the temporary folder at their own, and look at what is left.
"""

import importlib.util
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import tempfile
import time

import pytest

ROOT = pathlib.Path(__file__).parent.parent

SLOW_TEST = """
import pathlib

def test_the_value_is_one():
    from x import value
    pathlib.Path({started!r}).touch()
    import time; time.sleep(60)
    assert value == 1
"""

LEAKY_TEST = """
import tempfile

def test_the_value_is_one():
    from x import value
    tempfile.mkstemp()  # a test that forgets its file
    assert value == 1
"""

ROW = """[[rule]]
name = "the value is one"
file = "src/x.py"
find = '^value = 1$'
replace = 'value = 2'
"""


def _project(tmp_path, test):
    """A tiny repository with one rule, one row, its test, and a copy of the runner."""
    project = tmp_path / "project"
    for folder in ("scripts/mutations/src/x.py", "src", "tests"):
        (project / folder).mkdir(parents=True)
    shutil.copy(ROOT / "scripts/mutate.py", project / "scripts/mutate.py")
    (project / "scripts/mutations/src/x.py/the-value-is-one.toml").write_text(ROW)
    (project / "src/x.py").write_text("# RULE: the value is one\nvalue = 1\n")
    (project / "tests/test_x.py").write_text(test)
    (project / "pyproject.toml").write_text('[tool.pytest.ini_options]\npythonpath = ["src"]\n')
    return project


def _load(path, name, tmp, monkeypatch):
    """Load a script with the temporary folder pointed at `tmp`, as a run started there would see it."""
    tmp.mkdir(exist_ok=True)
    monkeypatch.setenv("TMPDIR", str(tmp))
    monkeypatch.setattr(tempfile, "tempdir", None)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _stop_when_started(command, started, tmp, cwd):
    """Start `command`, send it SIGTERM once `started` exists, and give back its exit code."""
    run = subprocess.Popen(command, cwd=cwd, env={**os.environ, "TMPDIR": str(tmp)})
    deadline = time.monotonic() + 30
    while not started.exists():
        assert run.poll() is None, "the run ended before it got going"
        assert time.monotonic() < deadline, "the run never got going"
        time.sleep(0.05)
    run.send_signal(signal.SIGTERM)
    return run.wait(timeout=30)


def test_a_suite_run_s_own_temporary_files_stay_in_its_folder(tmp_path, monkeypatch):
    """A file a test forgets is made in the run's folder, and goes with it."""
    project = _project(tmp_path, LEAKY_TEST)
    mutate = _load(project / "scripts/mutate.py", "mutate_leaky", tmp_path / "tmp", monkeypatch)
    mutate.run_suite()
    assert [p.name for p in (tmp_path / "tmp").rglob("*")] == [mutate.MUTATE_TMP.name]


def test_a_finished_run_leaves_no_empty_folder_behind(tmp_path, monkeypatch):
    project = _project(tmp_path, LEAKY_TEST.replace("tempfile.mkstemp()", "pass"))
    mutate = _load(project / "scripts/mutate.py", "mutate_finished", tmp_path / "tmp", monkeypatch)
    monkeypatch.setattr("sys.argv", ["mutate.py"])
    assert mutate.main() == 0
    assert list((tmp_path / "tmp").iterdir()) == []


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no SIGTERM to send")
def test_a_stopped_run_cleans_up_as_an_interrupted_one_does(tmp_path):
    """SIGTERM mid-run: the source is put back, and nothing is left in the temporary folder."""
    started, tmp = tmp_path / "started", tmp_path / "tmp"
    tmp.mkdir()
    project = _project(tmp_path, SLOW_TEST.format(started=str(started)))
    code = _stop_when_started([sys.executable, "scripts/mutate.py"], started, tmp, project)
    assert (code, list(tmp.iterdir())) == (128 + signal.SIGTERM, [])
    assert (project / "src/x.py").read_text() == "# RULE: the value is one\nvalue = 1\n"
    assert not (project / "scripts/.mutate-recovery.json").exists()


def _ratchet(tmp, monkeypatch):
    return _load(ROOT / "scripts/ratchet.py", "ratchet_tmp", tmp, monkeypatch)


def test_a_ratchet_run_s_commands_keep_their_temporary_files_in_its_folder(tmp_path, monkeypatch):
    ratchet = _ratchet(tmp_path / "tmp", monkeypatch)
    with ratchet.run_folder() as folder:
        said = subprocess.run(
            [sys.executable, "-c", "import tempfile; print(tempfile.gettempdir())"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert pathlib.Path(said.stdout.strip()) == folder
    assert list((tmp_path / "tmp").iterdir()) == []


STOPPED_RATCHET = """
import importlib.util, pathlib, sys
spec = importlib.util.spec_from_file_location("ratchet", {ratchet!r})
ratchet = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ratchet)
sleeper = "import pathlib, time; pathlib.Path({started!r}).touch(); time.sleep(60)"
with ratchet.stopped_cleanly(), ratchet.run_folder():
    ratchet._run(sys.executable, "-c", sleeper)
"""


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no SIGTERM to send")
def test_a_stopped_ratchet_run_cleans_up_as_an_interrupted_one_does(tmp_path):
    started, tmp = tmp_path / "started", tmp_path / "tmp"
    tmp.mkdir()
    script = STOPPED_RATCHET.format(ratchet=str(ROOT / "scripts/ratchet.py"), started=str(started))
    code = _stop_when_started([sys.executable, "-c", script], started, tmp, tmp_path)
    assert (code, list(tmp.iterdir())) == (128 + signal.SIGTERM, [])


def _a_dead_pid():
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    return gone.pid


def test_a_killed_ratchet_run_s_folder_is_swept_at_the_next_start(tmp_path, monkeypatch):
    ratchet = _ratchet(tmp_path / "tmp", monkeypatch)
    left = ratchet.RATCHET_TMP / f"run-{_a_dead_pid()}-abc"
    (left / "shut").mkdir(parents=True)
    (left / "shut" / "inside.txt").write_text("x")
    (left / "shut").chmod(0)
    pruned = []
    monkeypatch.setattr(ratchet, "_run", lambda *command, **_: pruned.append(command))
    ratchet.sweep()
    assert (left.exists(), pruned) == (False, [("git", "worktree", "prune")])


def test_a_running_ratchet_s_folder_is_never_swept(tmp_path, monkeypatch):
    """Two ratchets in one checkout share the folder; neither takes the other's."""
    ratchet = _ratchet(tmp_path / "tmp", monkeypatch)
    running = ratchet.RATCHET_TMP / f"run-{os.getpid()}-abc"
    running.mkdir(parents=True)
    ratchet.sweep()
    assert running.exists()
