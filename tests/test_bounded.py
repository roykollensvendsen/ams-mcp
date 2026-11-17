# SPDX-License-Identifier: GPL-3.0-or-later
"""A test's own child process ends with the test, everything it started included.

A timeout on `subprocess.run` ends only the child it started. A grandchild, under a
shell or `uv run`, keeps running after the test and holds its files, ports and CPU
into the next tests of a mutation run.
"""

import os
import sys
import time

import pytest
from bounded import run_bounded


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
