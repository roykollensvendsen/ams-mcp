# SPDX-License-Identifier: GPL-3.0-or-later
"""The mutation tool and the ratchet leave nothing in the temporary folder, however they end.

Both run the whole suite, many times over, and the ratchet also copies main and
installs it. Each run now keeps everything it and its commands make in one folder
of its own, removed when it ends, also when it is stopped with SIGTERM or SIGHUP,
as a timeout or a closed terminal does. A run killed outright is swept at the next
start. These tests point the temporary folder at their own, and look at what is left.
"""

import fcntl
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


def _a_run_folder(ratchet, name, *, held):
    """A run's folder with its lock file, and the lock held when the run is still going."""
    folder = ratchet.RATCHET_TMP / name
    folder.mkdir(parents=True)
    lock = os.open(folder / ratchet.LOCK, os.O_RDWR | os.O_CREAT, 0o600)
    if held:
        fcntl.flock(lock, fcntl.LOCK_EX)
    else:
        os.close(lock)
    return folder, lock


def test_a_killed_ratchet_run_s_folder_is_swept_at_the_next_start(tmp_path, monkeypatch):
    ratchet = _ratchet(tmp_path / "tmp", monkeypatch)
    left, _ = _a_run_folder(ratchet, "run-abc", held=False)
    (left / "shut").mkdir()
    (left / "shut" / "inside.txt").write_text("x")
    (left / "shut").chmod(0)
    pruned = []  # the base is a repository of its own now, so no git command is needed
    monkeypatch.setattr(ratchet, "_run", lambda *command, **_: pruned.append(command))
    ratchet.sweep()
    assert (left.exists(), pruned) == (False, [])


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no flock")
def test_a_running_ratchet_s_folder_is_never_swept(tmp_path, monkeypatch):
    """Two ratchets in one checkout share the folder; neither takes one whose lock is held."""
    ratchet = _ratchet(tmp_path / "tmp", monkeypatch)
    running, lock = _a_run_folder(ratchet, "run-abc", held=True)
    try:
        ratchet.sweep()
        assert running.exists()
    finally:
        os.close(lock)


def test_a_ratchet_run_that_is_still_starting_is_never_swept(tmp_path, monkeypatch):
    """A new folder is locked before it takes a run's name; until an hour old, it may be mid-way."""
    ratchet = _ratchet(tmp_path / "tmp", monkeypatch)
    young = ratchet.RATCHET_TMP / "starting-abc"
    old = ratchet.RATCHET_TMP / "starting-old"
    young.mkdir(parents=True)
    old.mkdir()
    an_hour_ago = time.time() - ratchet.STARTING_SECONDS - 1
    os.utime(old, (an_hour_ago, an_hour_ago))
    monkeypatch.setattr(ratchet, "_run", lambda *_, **__: None)
    ratchet.sweep()
    assert (young.exists(), old.exists()) == (True, False)


def test_a_ratchet_run_holds_its_folder_s_lock_until_it_ends(tmp_path, monkeypatch):
    ratchet = _ratchet(tmp_path / "tmp", monkeypatch)
    with ratchet.run_folder() as folder:
        theirs = os.open(folder / ratchet.LOCK, os.O_RDWR)
        with pytest.raises(BlockingIOError):
            fcntl.flock(theirs, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.close(theirs)
    assert not folder.exists()


def test_a_link_in_the_ratchet_folder_is_never_followed(tmp_path, monkeypatch):
    """Only a real folder of this user's is swept; a link named like a run is left, and so is its target."""
    ratchet = _ratchet(tmp_path / "tmp", monkeypatch)
    outside = tmp_path / "outside"
    (outside / "kept").mkdir(parents=True)
    (outside / "kept").chmod(0o755)
    (outside / ratchet.LOCK).write_text("")
    ratchet.RATCHET_TMP.mkdir(mode=0o700)
    (ratchet.RATCHET_TMP / "run-planted").symlink_to(outside)
    ratchet.sweep()
    assert (ratchet.RATCHET_TMP / "run-planted").is_symlink()
    assert (outside / "kept").stat().st_mode & 0o777 == 0o755


def test_a_ratchet_folder_that_is_not_ours_alone_is_refused(tmp_path, monkeypatch):
    """It has a fixed name in the shared /tmp, so anyone could have made it first."""
    ratchet = _ratchet(tmp_path / "tmp", monkeypatch)
    ratchet.RATCHET_TMP.mkdir()
    ratchet.RATCHET_TMP.chmod(0o777)
    with pytest.raises(SystemExit, match="not a folder of this user's alone"), ratchet.run_folder():
        pass


def _a_link_in_place_of(folder, tmp_path):
    """A link where the folder should be, to a private folder of ours that would pass every other check."""
    (tmp_path / "elsewhere").mkdir(mode=0o700)
    folder.symlink_to(tmp_path / "elsewhere")


def test_a_link_in_place_of_the_ratchet_folder_is_refused(tmp_path, monkeypatch):
    ratchet = _ratchet(tmp_path / "tmp", monkeypatch)
    _a_link_in_place_of(ratchet.RATCHET_TMP, tmp_path)
    with pytest.raises(SystemExit, match="not a folder of this user's alone"), ratchet.run_folder():
        pass


def test_a_link_in_place_of_the_runs_folder_is_refused(tmp_path, monkeypatch):
    project = _project(tmp_path, LEAKY_TEST)
    mutate = _load(project / "scripts/mutate.py", "mutate_link", tmp_path / "tmp", monkeypatch)
    _a_link_in_place_of(mutate.MUTATE_TMP, tmp_path)
    with pytest.raises(SystemExit, match="not a folder of this user's alone"):
        mutate.run_suite()


def test_a_shared_folder_for_runs_that_is_not_ours_alone_is_refused(tmp_path, monkeypatch):
    project = _project(tmp_path, LEAKY_TEST)
    mutate = _load(project / "scripts/mutate.py", "mutate_open", tmp_path / "tmp", monkeypatch)
    mutate.MUTATE_TMP.mkdir()
    mutate.MUTATE_TMP.chmod(0o777)
    with pytest.raises(SystemExit, match="not a folder of this user's alone"):
        mutate.run_suite()


def _a_second_stop_waits(script, tmp_path, monkeypatch):
    """Once a stop is under way, a second one (Ctrl-C, then a timeout's SIGTERM) waits until it is done."""
    module = _load(ROOT / f"scripts/{script}.py", f"{script}_twice", tmp_path / "tmp", monkeypatch)
    before = signal.getsignal(signal.SIGTERM)
    with module.stopped_cleanly():
        with pytest.raises(KeyboardInterrupt):  # Ctrl-C, as the handler Python runs for it sees it
            signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
        assert signal.getsignal(signal.SIGTERM) == signal.SIG_IGN
    assert signal.getsignal(signal.SIGTERM) == before


def test_a_second_stop_does_not_cut_the_clean_up_short(tmp_path, monkeypatch):
    _a_second_stop_waits("mutate", tmp_path, monkeypatch)


def test_a_second_stop_does_not_cut_a_ratchet_s_clean_up_short(tmp_path, monkeypatch):
    _a_second_stop_waits("ratchet", tmp_path, monkeypatch)


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no SIGTERM to send")
def test_ctrl_c_then_a_stop_still_cleans_up(tmp_path):
    started, tmp = tmp_path / "started", tmp_path / "tmp"
    tmp.mkdir()
    project = _project(tmp_path, SLOW_TEST.format(started=str(started)))
    run = subprocess.Popen(
        [sys.executable, "scripts/mutate.py"], cwd=project, env={**os.environ, "TMPDIR": str(tmp)}
    )
    deadline = time.monotonic() + 30
    while not started.exists():
        assert run.poll() is None, "the run ended before it got going"
        assert time.monotonic() < deadline, "the run never got going"
        time.sleep(0.05)
    run.send_signal(signal.SIGINT)
    run.send_signal(signal.SIGTERM)
    assert run.wait(timeout=30) != 0
    assert list(tmp.iterdir()) == []
    assert (project / "src/x.py").read_text() == "# RULE: the value is one\nvalue = 1\n"


def test_the_base_is_measured_where_no_other_branch_can_be_seen(tmp_path, monkeypatch):
    """A worktree shares every branch of this clone; a one-commit copy needs no cleaning in it after."""
    ratchet = _ratchet(tmp_path / "tmp", monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

    for args in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "t@example.org"],
        ["config", "user.name", "t"],
    ):
        git(*args)
    (repo / "notes.txt").write_text("main\n")
    git("add", "notes.txt")
    git("commit", "-q", "-m", "main")
    git("switch", "-q", "-c", "under-review")
    (repo / "notes.txt").write_text("under review\n")
    git("commit", "-q", "-am", "under review")
    git("switch", "-q", "main")
    monkeypatch.setattr(ratchet, "ROOT", repo)
    copy = tmp_path / "base"
    ratchet.copy_of("main", copy)
    seen = subprocess.run(
        ["git", "log", "--all", "--format=%s"],
        cwd=copy,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert (copy / "notes.txt").read_text() == "main\n"
    assert "under review" not in seen
