# SPDX-License-Identifier: GPL-3.0-or-later
"""The mutation tool and the ratchet leave nothing in the temporary folder, however they end.

Both run the whole suite, many times over, and the ratchet also copies main and
installs it. Each run now keeps everything it and its commands make in one folder
of its own, removed when it ends, also when it is stopped with SIGTERM or SIGHUP,
as a timeout or a closed terminal does. A run killed outright is swept at the next
start. These tests point the temporary folder at their own, and look at what is left.
"""

import contextlib
import fcntl
import importlib.util
import json
import os
import pathlib
import signal
import subprocess
import sys
import tempfile
import time

import pytest
from bounded import WITHIN, run_bounded

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
    # The runner checks that the suite loads its package from this project's src/.
    runner = (ROOT / "scripts/mutate.py").read_text().replace('PACKAGE = "ams_mcp"', 'PACKAGE = "x"')
    (project / "scripts/mutate.py").write_text(runner)
    (project / "scripts/mutations/src/x.py/the-value-is-one.toml").write_text(ROW)
    (project / "src/x.py").write_text("# RULE: the value is one\nvalue = 1\n")
    (project / "tests/test_x.py").write_text(test)
    (project / "pyproject.toml").write_text('[tool.pytest.ini_options]\npythonpath = ["src"]\n')
    return project


def _its_own(project):
    """What makes a plain `import x` in `project` find its own src/, as the suite does."""
    return {"PYTHONPATH": str(project / "src")}


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
    run = subprocess.Popen(command, cwd=cwd, env={**os.environ, "TMPDIR": str(tmp), **_its_own(cwd)})
    deadline = time.monotonic() + WITHIN
    while not started.exists():
        assert run.poll() is None, "the run ended before it got going"
        assert time.monotonic() < deadline, "the run never got going"
        time.sleep(0.05)
    run.send_signal(signal.SIGTERM)
    return run.wait(timeout=WITHIN)


def test_a_suite_run_s_own_temporary_files_stay_in_its_folder(tmp_path, monkeypatch):
    """A file a test forgets is made in the run's folder, and goes with it."""
    project = _project(tmp_path, LEAKY_TEST)
    mutate = _load(project / "scripts/mutate.py", "mutate_leaky", tmp_path / "tmp", monkeypatch)
    mutate.run_suite(timeout=WITHIN)
    assert [p.name for p in (tmp_path / "tmp").rglob("*")] == [mutate.MUTATE_TMP.name]


def test_a_finished_run_leaves_no_empty_folder_behind(tmp_path, monkeypatch):
    project = _project(tmp_path, LEAKY_TEST.replace("tempfile.mkstemp()", "pass"))
    mutate = _load(project / "scripts/mutate.py", "mutate_finished", tmp_path / "tmp", monkeypatch)
    monkeypatch.setenv("PYTHONPATH", _its_own(project)["PYTHONPATH"])
    monkeypatch.setattr("sys.argv", ["mutate.py"])
    assert mutate.main() == 0
    assert list((tmp_path / "tmp").iterdir()) == []


def test_the_runs_folder_is_removed_before_the_checkout_is_let_go(tmp_path, monkeypatch):
    """The next run may start the moment the lock is let go, and make its folder in there.

    Removed after that, the folder could vanish under the new run as it starts.
    The check is made at the moment the lock's handle is closed.
    """
    project = _project(tmp_path, LEAKY_TEST.replace("tempfile.mkstemp()", "pass"))
    mutate = _load(project / "scripts/mutate.py", "mutate_let_go", tmp_path / "tmp", monkeypatch)
    monkeypatch.setenv("PYTHONPATH", _its_own(project)["PYTHONPATH"])
    monkeypatch.setattr("sys.argv", ["mutate.py"])
    held, there_when_let_go = [], []
    take = mutate._the_only_run  # noqa: SLF001

    def taken():
        held.append(take())
        return held[0]

    real_close = os.close

    def close(fd):
        if held and fd == held[0]:
            there_when_let_go.append(mutate.MUTATE_TMP.exists())
        real_close(fd)

    monkeypatch.setattr(mutate, "_the_only_run", taken)
    monkeypatch.setattr(mutate.os, "close", close)
    assert mutate.main() == 0
    assert there_when_let_go == [False], "the checkout was let go with the runs' folder still there"


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
        said = run_bounded(
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
    ratchet.RATCHET_TMP.mkdir(mode=0o700, exist_ok=True)
    folder = ratchet.RATCHET_TMP / name
    folder.mkdir()
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
    """A folder is made a moment before its lock; until it is an hour old, it may be in that moment."""
    ratchet = _ratchet(tmp_path / "tmp", monkeypatch)
    ratchet.RATCHET_TMP.mkdir(mode=0o700)
    young, old = ratchet.RATCHET_TMP / "run-young", ratchet.RATCHET_TMP / "run-old"
    young.mkdir()
    old.mkdir()
    an_hour_ago = time.time() - ratchet.GRACE_SECONDS - 1
    os.utime(old, (an_hour_ago, an_hour_ago))
    ratchet.sweep()
    assert (young.exists(), old.exists()) == (True, False)


def test_a_folder_another_sweep_removed_is_passed_over(tmp_path, monkeypatch):
    """Two runs starting together sweep together; the one that finds a folder gone goes on."""
    ratchet = _ratchet(tmp_path / "tmp", monkeypatch)
    assert ratchet.left_behind(tmp_path / "run-gone") is False


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
    for mode in (0o777, 0o755):  # writable by others, and only readable by them
        ratchet.RATCHET_TMP.chmod(mode)
        with (
            pytest.raises(SystemExit, match=r"not a folder of this user's alone.*chmod 700 .*rm -rf"),
            ratchet.run_folder(),
        ):
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
    for mode in (0o777, 0o755):  # writable by others, and only readable by them
        mutate.MUTATE_TMP.chmod(mode)
        with pytest.raises(SystemExit, match=r"not a folder of this user's alone.*chmod 700 .*rm -rf"):
            mutate.run_suite()


def _wait_for(path, run):
    deadline = time.monotonic() + WITHIN
    while not path.exists():
        assert run.poll() is None, run.communicate()[0]
        assert time.monotonic() < deadline, f"never saw {path.name}"
        time.sleep(0.05)


def _ctrl_c_then_a_stop(command, tmp_path, cwd):
    """Ctrl-C, and once the clean-up has begun, SIGTERM, as GitHub cancels a job."""
    run = subprocess.Popen(
        command,
        cwd=cwd,
        env={**os.environ, "TMPDIR": str(tmp_path / "tmp"), **_its_own(cwd)},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    _wait_for(tmp_path / "started", run)
    run.send_signal(signal.SIGINT)
    _wait_for(tmp_path / "cleaning", run)
    run.send_signal(signal.SIGTERM)
    return run.communicate(timeout=WITHIN)[0]


SLOW_MUTATE = """
import importlib.util, pathlib, sys, time
project, tmp = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
spec = importlib.util.spec_from_file_location("mutate", project / "scripts/mutate.py")
mutate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mutate)
restore = mutate.restore

def slow_restore(*args):
    (tmp / "cleaning").write_text("")
    time.sleep(2)  # putting a file back takes a moment; a second stop must wait for it
    return restore(*args)

mutate.restore = slow_restore
sys.argv = ["mutate.py"]
sys.exit(mutate.main())
"""


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no SIGTERM to send")
def test_a_second_stop_does_not_cut_the_clean_up_short(tmp_path):
    (tmp_path / "tmp").mkdir()
    project = _project(tmp_path, SLOW_TEST.format(started=str(tmp_path / "started")))
    out = _ctrl_c_then_a_stop(
        [sys.executable, "-c", SLOW_MUTATE, str(project), str(tmp_path)], tmp_path, project
    )
    assert (project / "src/x.py").read_text() == "# RULE: the value is one\nvalue = 1\n", out
    assert not (project / "scripts/.mutate-recovery.json").exists()
    assert list((tmp_path / "tmp").iterdir()) == []


SLOW_RATCHET = """
import importlib.util, pathlib, sys, time
tmp = pathlib.Path(sys.argv[2])
spec = importlib.util.spec_from_file_location("ratchet", sys.argv[1])
ratchet = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ratchet)
remove = ratchet._remove

def slow_remove(folder):
    (tmp / "cleaning").write_text("")
    time.sleep(2)  # removing a copy of main takes a moment; a second stop must wait for it
    remove(folder)

ratchet._remove = slow_remove
sleeper = f"import pathlib, time; pathlib.Path({str(tmp / 'started')!r}).touch(); time.sleep(60)"
with ratchet.stopped_cleanly(), ratchet.run_folder():
    ratchet._run(sys.executable, "-c", sleeper)
"""


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no SIGTERM to send")
def test_a_second_stop_does_not_cut_a_ratchet_s_clean_up_short(tmp_path):
    (tmp_path / "tmp").mkdir()
    command = [sys.executable, "-c", SLOW_RATCHET, str(ROOT / "scripts/ratchet.py"), str(tmp_path)]
    out = _ctrl_c_then_a_stop(command, tmp_path, tmp_path)
    assert list((tmp_path / "tmp").iterdir()) == [], out


STEP = """
import os, pathlib, signal, sys, time
tmp = pathlib.Path(sys.argv[1])

def put_back(signum, frame):
    time.sleep(1)  # putting a line back takes a moment; the ratchet must wait for it
    (tmp / "put-back").write_text("")
    sys.exit(1)

signal.signal(signal.SIGINT, put_back)
(tmp / "step.pid").write_text(str(os.getpid()))
(tmp / "ready").write_text("")
time.sleep(20)  # never told: give up, and leave no put-back behind
"""

FORWARDING_RATCHET = """
import importlib.util, sys
spec = importlib.util.spec_from_file_location("ratchet", sys.argv[1])
ratchet = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ratchet)
with ratchet.stopped_cleanly():
    ratchet._run(sys.executable, "-c", sys.argv[3], sys.argv[2])
"""


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no SIGTERM to send")
@pytest.mark.parametrize("stop", [signal.SIGINT, signal.SIGTERM, signal.SIGHUP], ids=["INT", "TERM", "HUP"])
def test_a_stopped_measurement_lets_its_step_put_back_what_it_changed(tmp_path, stop):
    """The step may be a mutation run with a rule switched off; it must hear the stop and finish."""
    command = [
        sys.executable,
        "-c",
        FORWARDING_RATCHET,
        str(ROOT / "scripts/ratchet.py"),
        str(tmp_path),
        STEP,
    ]
    ratchet = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        _wait_for(tmp_path / "ready", ratchet)
        ratchet.send_signal(stop)  # to the ratchet alone, as when its job is cancelled
        out, _ = ratchet.communicate(timeout=WITHIN)
        assert (tmp_path / "put-back").exists(), out
    finally:
        if ratchet.poll() is None:
            ratchet.kill()
        if (tmp_path / "step.pid").exists():
            with contextlib.suppress(ProcessLookupError):
                os.kill(int((tmp_path / "step.pid").read_text()), signal.SIGKILL)


def test_a_ratchet_s_step_hears_a_stop_only_through_the_ratchet(tmp_path, monkeypatch):
    """In a session of its own, a terminal's Ctrl-C reaches the step once, by way of the ratchet."""
    ratchet = _ratchet(tmp_path / "tmp", monkeypatch)
    said = ratchet._run(sys.executable, "-c", "import os; print(os.getsid(0) == os.getpid())")  # noqa: SLF001
    assert said.stdout.strip() == "True"


def test_a_link_in_the_runs_folder_is_never_followed(tmp_path, monkeypatch):
    """The sweep removes real folders of ours; a link planted there is left, and so is its target."""
    project = _project(tmp_path, LEAKY_TEST)
    mutate = _load(project / "scripts/mutate.py", "mutate_planted", tmp_path / "tmp", monkeypatch)
    outside = tmp_path / "outside"
    (outside / "kept").mkdir(parents=True)
    (outside / "kept").chmod(0o500)  # short of what a removal gives back, so any touch shows
    mutate.MUTATE_TMP.mkdir(mode=0o700)
    (mutate.MUTATE_TMP / "run-planted").symlink_to(outside)
    mutate.sweep()
    assert (mutate.MUTATE_TMP / "run-planted").is_symlink()
    assert (outside / "kept").stat().st_mode & 0o777 == 0o500


def test_a_sweep_takes_only_folders_named_as_runs(tmp_path, monkeypatch):
    """A folder of ours that no run made, by its name, is left where it is."""
    project = _project(tmp_path, LEAKY_TEST)
    mutate = _load(project / "scripts/mutate.py", "mutate_named", tmp_path / "tmp", monkeypatch)
    mutate.MUTATE_TMP.mkdir(mode=0o700)
    (mutate.MUTATE_TMP / "run-left").mkdir()
    (mutate.MUTATE_TMP / "notes").mkdir()
    mutate.sweep()
    assert not (mutate.MUTATE_TMP / "run-left").exists()
    assert (mutate.MUTATE_TMP / "notes").is_dir()


def test_a_second_mutation_run_in_one_checkout_stops_at_once(tmp_path, monkeypatch, capsys):
    project = _project(tmp_path, LEAKY_TEST)
    mutate = _load(project / "scripts/mutate.py", "mutate_second", tmp_path / "tmp", monkeypatch)
    monkeypatch.setattr(mutate, "run_suite", _must_not_run)
    monkeypatch.setattr("sys.argv", ["mutate.py"])
    first = os.open(mutate.RECOVERY.parent / ".mutate.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(first, fcntl.LOCK_EX)
        assert mutate.main() == 1
    finally:
        os.close(first)
    assert "already going in this checkout" in capsys.readouterr().out


def _must_not_run():
    raise AssertionError("the suite ran")


def test_a_repair_only_run_puts_back_and_does_nothing_else(tmp_path, monkeypatch):
    project = _project(tmp_path, LEAKY_TEST)
    mutate = _load(project / "scripts/mutate.py", "mutate_repair", tmp_path / "tmp", monkeypatch)
    original, mutated = "# RULE: the value is one\nvalue = 1\n", "# RULE: the value is one\nvalue = 2\n"
    (project / "src/x.py").write_text(mutated)
    mutate.RECOVERY.write_text(json.dumps({"file": "src/x.py", "original": original, "mutated": mutated}))
    monkeypatch.setattr(mutate, "run_suite", _must_not_run)
    monkeypatch.setattr("sys.argv", ["mutate.py", "--repair-only"])
    assert mutate.main() == 0
    assert (project / "src/x.py").read_text() == original
    assert not mutate.RECOVERY.exists()


def test_the_base_is_measured_where_no_other_branch_can_be_seen(tmp_path, monkeypatch):
    """A worktree shares every branch of this clone; a one-commit copy needs no cleaning in it after."""
    ratchet = _ratchet(tmp_path / "tmp", monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, timeout=WITHIN)

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
        timeout=WITHIN,
    ).stdout
    assert (copy / "notes.txt").read_text() == "main\n"
    assert "under review" not in seen
