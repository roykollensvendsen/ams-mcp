# SPDX-License-Identifier: GPL-3.0-or-later
"""Waits on a test's own child processes, each with a limit the test sets.

Running out fails the test, so a switched-off rule that makes a child hang counts
as caught, the way one that crashes does. This is no limit on a mutation run or
on a test: only on a wait.
"""

import os
import queue
import signal
import subprocess
import threading

import pytest

#: Seconds any wait in a test gives a child: generous, so it fires only on a real hang.
WITHIN = 300


def run_bounded(command, within=WITHIN, **kwargs):
    """`subprocess.run`, with the child and everything it started ended if it runs out.

    The child gets a session of its own, so its group holds whatever it starts, and
    the whole group is killed. SIGKILL, never Ctrl-C: under `uv run` a group's Ctrl-C
    reaches Python twice, and SIGKILL cannot be caught.
    """
    if kwargs.pop("capture_output", False):
        kwargs["stdout"] = kwargs["stderr"] = subprocess.PIPE
    check = kwargs.pop("check", False)
    with subprocess.Popen(command, start_new_session=True, **kwargs) as child:
        try:
            out, err = child.communicate(timeout=within)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            out, err = child.communicate()
            pytest.fail(f"{command!r} did not end within {within} s; it said:\n{out or ''}{err or ''}")
    done = subprocess.CompletedProcess(child.args, child.returncode, out, err)
    if check:
        done.check_returncode()
    return done


def line_within(stream, seconds=WITHIN):
    """The next line from `stream`; a failure, not a hang, if none comes within `seconds`.

    The read runs on a thread of its own, since a pipe's readline cannot be given a limit.
    Not select(): it sees the pipe, not a text buffer that may already hold the line.
    A thread left reading is ended with the child, when the test's clean-up kills it.
    """
    got = queue.Queue()
    threading.Thread(target=lambda: got.put(stream.readline()), daemon=True).start()
    try:
        return got.get(timeout=seconds)
    except queue.Empty:
        pytest.fail(f"no line within {seconds} s")
