# SPDX-License-Identifier: GPL-3.0-or-later
"""A test's own child process ends with the test, and a read from it gives up in time.

A timeout on `subprocess.run` ends only the child it started. A grandchild, under a
shell or `uv run`, keeps running after the test and holds its files, ports and CPU
into the next tests of a mutation run. A line read from a child that never writes
waits for ever, and with it the whole run.
"""

import os
import subprocess
import sys
import time

import pytest
from bounded import WITHIN, line_within, run_bounded


def test_a_child_that_runs_too_long_leaves_nothing_of_its_group_running(tmp_path):
    """A grandchild that kept the output would hold up the read until it ended by itself.

    So this one writes elsewhere: a kill that reached only the direct child then leaves
    it running, and the check below sees that at once rather than after its sleep.
    """
    record = tmp_path / "group"
    # A shell that leaves a grandchild behind, as a shell=True command would.
    command = f"echo $$ > {record}; sleep 60 > /dev/null 2>&1 & echo started; wait"
    with pytest.raises(pytest.fail.Exception, match="did not end within 1 s") as failed:
        run_bounded(command, 1, shell=True, capture_output=True, text=True)  # noqa: S604 - its own fixed line
    assert "started" in str(failed.value)
    group = int(record.read_text())
    deadline = time.monotonic() + 10
    while True:  # a killed process is gone once the kernel has reaped it
        try:
            os.killpg(group, 0)
        except ProcessLookupError:
            break
        assert time.monotonic() < deadline, "a process of the child's group is still running"
        time.sleep(0.05)


def test_a_child_that_ends_in_time_gives_back_what_it_said():
    said = run_bounded([sys.executable, "-c", "print('done')"], 300, capture_output=True, text=True)
    assert (said.returncode, said.stdout) == (0, "done\n")


def _writer(script):
    return subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)


def test_a_child_that_never_writes_fails_the_read_within_its_bound():
    child = _writer("import time; time.sleep(60)")
    try:
        started = time.monotonic()
        with pytest.raises(pytest.fail.Exception, match="no line within 1 s"):
            line_within(child.stdout, 1)
        assert time.monotonic() - started < 30
    finally:
        child.kill()
        child.wait(timeout=WITHIN)


def test_two_lines_written_at_once_are_both_read():
    """A read that asked the pipe, not the text buffer, would miss the second line."""
    child = _writer("import sys, time; sys.stdout.write('one\\ntwo\\n'); sys.stdout.flush(); time.sleep(60)")
    try:
        assert (line_within(child.stdout, 300), line_within(child.stdout, 300)) == ("one\n", "two\n")
    finally:
        child.kill()
        child.wait(timeout=WITHIN)
