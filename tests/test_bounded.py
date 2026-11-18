# SPDX-License-Identifier: GPL-3.0-or-later
"""A test's own child process ends with the test, everything it started included.

A timeout on `subprocess.run` ends only the child it started. A grandchild, under a
shell or `uv run`, keeps running after the test and holds its files, ports and CPU
into the next tests of a mutation run.
"""

import contextlib
import os
import pathlib
import signal
import sys
import time

import pytest
from bounded import WITHIN, run_bounded

pytestmark = pytest.mark.skipif(
    not pathlib.Path("/proc").is_dir(), reason="reads running processes from /proc"
)


def _running(pid):
    """Whether process `pid` still runs, rather than having ended or being a zombie left to reap."""
    try:
        state = pathlib.Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError):
        return False
    return state != "Z"


def test_a_child_that_runs_too_long_leaves_nothing_of_its_group_running(tmp_path):
    """The grandchild itself is checked, by its own pid, not the group it should be in.

    A grandchild that kept the output would hold up the read until it ended by itself,
    so this one writes elsewhere. A kill that reached only the shell, in a new session
    or not, then leaves it running, and the check sees that at once.
    """
    told = tmp_path / "pids"
    command = f'sleep 600 > /dev/null 2>&1 & echo $$ $! > "{told}"; echo started; wait'
    started = time.monotonic()
    try:
        with pytest.raises(pytest.fail.Exception, match="did not end within 1 s") as failed:
            run_bounded(command, 1, shell=True, capture_output=True, text=True)  # noqa: S604 - its own fixed line
        assert time.monotonic() - started < 15, "the limit waited for the child to end by itself"
        assert "started" in str(failed.value)
        _, grandchild = (int(pid) for pid in told.read_text().split())
        deadline = time.monotonic() + 10
        while _running(grandchild) and time.monotonic() < deadline:  # a kill lands a moment later
            time.sleep(0.05)
        assert not _running(grandchild), "the shell's grandchild was left running"
    finally:
        _end_if_still_the_sleep(told)


def _end_if_still_the_sleep(told):
    """End the grandchild by its own pid, and only while it is still the sleep this test started."""
    if not told.exists():
        return
    grandchild = int(told.read_text().split()[1])
    with contextlib.suppress(OSError):
        if pathlib.Path(f"/proc/{grandchild}/cmdline").read_bytes().split(b"\0")[:2] == [b"sleep", b"600"]:
            os.kill(grandchild, signal.SIGKILL)


def test_a_child_that_ends_in_time_gives_back_what_it_said():
    said = run_bounded([sys.executable, "-c", "print('done')"], WITHIN, capture_output=True, text=True)
    assert (said.returncode, said.stdout) == (0, "done\n")


def test_a_group_gone_as_the_limit_runs_out_still_fails_cleanly(monkeypatch):
    """The group can end between the limit running out and the kill; that is still the test's failure."""
    real_killpg = os.killpg

    def gone_first(pid, sig):
        real_killpg(pid, sig)
        raise ProcessLookupError

    monkeypatch.setattr("bounded.os.killpg", gone_first)
    with pytest.raises(pytest.fail.Exception, match="did not end within"):
        run_bounded(["sleep", "30"], within=0.2)
