# SPDX-License-Identifier: GPL-3.0-or-later
"""Each test starts with the stop handlers the test run started with.

The mutation tool and the ratchet install their own for SIGINT, SIGTERM and SIGHUP,
and a test that runs one in-process would otherwise leave them for every later test.
"""

import signal

from conftest import STOPS, stop_handlers_kept


def test_a_test_s_stop_handlers_are_put_back_after_it():
    before = {each: signal.getsignal(each) for each in STOPS}
    with stop_handlers_kept():
        for each in STOPS:
            signal.signal(each, signal.SIG_IGN)
    assert {each: signal.getsignal(each) for each in STOPS} == before
