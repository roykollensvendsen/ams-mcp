# SPDX-License-Identifier: GPL-3.0-or-later
"""Guards that hold for the whole test session."""

import pathlib

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
