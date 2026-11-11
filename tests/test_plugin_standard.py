# SPDX-License-Identifier: GPL-3.0-or-later
"""Each rule of the voice plugins' shared standard has a test here named after it.

The standard is claude-voice's docs/plugin-standard.md. Rules 1 to 11 each map
to the tests that guard them in ams-mcp; renaming or deleting one of those
tests turns this red, so a rule cannot lose its guard quietly. Rule 12 is this
file, and its second half, asking the voice what it sees after a change to a
name or a first line, is a step for whoever changes one.
"""

import ast
import pathlib

TESTS = pathlib.Path(__file__).parent

#: Each rule of the standard, and the tests in ams-mcp that guard it.
STANDARD = {
    1: ["test_tool_names.py::test_no_two_tool_names_can_be_confused"],
    2: ["test_server.py::test_every_tool_opens_with_a_whole_sentence_the_voice_sees_in_full"],
    3: [
        "test_server.py::test_a_tools_longest_wait_fits_in_the_voices_time",
        "test_server.py::test_a_tool_still_working_at_the_deadline_says_so",
        "test_cold_start.py::test_a_cold_start_lists_the_tools_in_time",
        "test_cold_start.py::test_a_cold_start_never_waits_on_the_reader",
    ],
    4: [
        "test_server.py::test_every_tool_answers_with_a_sentence_even_when_it_fails",
        "test_answers.py::test_a_reader_that_does_not_answer_is_said_and_nothing_is_guessed",
    ],
    # Rule 5 is about reading the state back after an action; every tool here
    # only reads, so what it says is the reading itself, with its time.
    5: ["test_answers.py::test_every_answer_carries_the_time_of_its_reading"],
    6: [
        "test_answers.py::test_a_reader_that_does_not_answer_is_said_and_nothing_is_guessed",
        "test_answers.py::test_a_page_in_a_shape_the_firmware_does_not_give_is_refused",
        "test_answers.py::test_a_reading_older_than_about_half_a_minute_is_not_confirmed",
    ],
    7: ["test_tool_names.py::test_no_tool_asks_for_a_confirmation_because_none_changes_anything"],
    # Rule 8: the one device this plugin reaches is touched only by GET on its read-only pages.
    8: [
        "test_reader.py::test_only_an_allow_listed_page_is_ever_asked_for",
        "test_reader.py::test_a_page_is_only_ever_read_with_get",
    ],
    9: ["test_server.py::test_every_tool_is_marked_read_only"],
    10: [
        "test_private_data_scan.py::test_a_value_from_the_settings_file_is_never_committed",
        "test_recorded_pages.py::test_nothing_that_identifies_the_home_is_in_the_recordings",
    ],
    11: ["test_server.py::test_every_simulated_answer_says_simulated_first"],
}


def _defined(file):
    tree = ast.parse((TESTS / file).read_text())
    return {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}


def test_each_rule_has_a_test_named_after_it():
    assert set(STANDARD) == set(range(1, 12))
    missing = [
        f"rule {rule}: {test}"
        for rule, tests in STANDARD.items()
        for test in tests
        if test.split("::")[1] not in _defined(test.split("::")[0])
    ]
    assert not missing, missing
