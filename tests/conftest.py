# SPDX-License-Identifier: GPL-3.0-or-later
"""Guards that hold for the whole test session.

How each tool's run folders are kept from each other's sweeps, by the designs of
the plugin standard's cleanup checklist (new-plugin-v8, points 1 and 3):

- ``scripts/ratchet.py`` uses A2, the renamed lock file, as the bridge's
  ``held_folder.py`` does. A folder with no lock is swept only after a grace period.
- ``scripts/mutate.py`` uses B, the parent guard. Its parent folder belongs to one
  checkout, so the guard is the checkout's own lock, held from the sweep to the final
  removal of the parent folder.
"""

import contextlib
import pathlib
import signal

import pytest

ROOT = pathlib.Path(__file__).parent.parent
RECOVERY = ROOT / "scripts/.mutate-recovery.json"


def _record():
    return RECOVERY.read_bytes() if RECOVERY.exists() else None


@pytest.fixture(autouse=True, scope="session")
def no_test_touches_the_real_recovery_record():
    """The mutation runner's recovery record is the runner's alone.

    Inside a mutation run it names the file being switched off, for the whole
    suite. A test that wrote, changed or removed it could put that file back
    mid-run, or leave the next run repairing a file nobody mutated. So the
    record must be exactly as it was when the session began, there or not.
    """
    before = _record()
    yield
    assert _record() == before, (
        f"a test changed {RECOVERY.relative_to(ROOT)}; point mutate.RECOVERY at tmp_path in that test"
    )


#: How a run is stopped from outside: Ctrl-C, a cancelled CI job, a closed terminal.
STOPS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


@contextlib.contextmanager
def stop_handlers_kept():
    """Put SIGINT, SIGTERM and SIGHUP back as they were, whatever ran in between."""
    before = {each: signal.getsignal(each) for each in STOPS}
    try:
        yield
    finally:
        for each, handler in before.items():
            signal.signal(each, handler)


@pytest.fixture(autouse=True)
def _stop_handlers_kept():
    """A test that runs a script's main() installs its stop handlers in this process.

    Put the test run's own back afterwards, so Ctrl-C still stops pytest and the next
    test of those handlers starts from a process that has none.
    """
    with stop_handlers_kept():
        yield
