# SPDX-License-Identifier: GPL-3.0-or-later
"""The owner's settings file: where the reader is, read from outside the repository."""

import asyncio

import pytest

from ams_mcp import cli
from ams_mcp.settings import SettingsError, load_settings


def _file(tmp_path, text):
    path = tmp_path / "config.toml"
    path.write_text(text)
    return path


def test_the_settings_give_the_reader_and_the_time_zone(tmp_path):
    s = load_settings(_file(tmp_path, '[reader]\naddress = "192.0.2.10"\ntimezone = "Europe/Oslo"\n'))
    assert s.address == "192.0.2.10"
    assert str(s.timezone) == "Europe/Oslo"


def test_a_host_name_will_do(tmp_path):
    assert (
        load_settings(_file(tmp_path, '[reader]\naddress = "reader.example"\n')).address == "reader.example"
    )


@pytest.mark.parametrize(
    "address",
    ["http://192.0.2.10", "192.0.2.10/reboot", "192.0.2.10:80", "192.0.2.10/../save", "", "a b"],
)
def test_the_address_is_a_bare_host_so_no_other_page_can_be_reached_through_it(tmp_path, address):
    with pytest.raises(SettingsError):
        load_settings(_file(tmp_path, f'[reader]\naddress = "{address}"\n'))


def test_a_missing_file_says_where_it_should_be(tmp_path):
    with pytest.raises(SettingsError, match="--simulate"):
        load_settings(tmp_path / "nothing.toml")


def test_an_unknown_time_zone_is_refused(tmp_path):
    with pytest.raises(SettingsError, match="time zone"):
        load_settings(_file(tmp_path, '[reader]\naddress = "192.0.2.10"\ntimezone = "Mars/Olympus"\n'))


def test_the_command_refuses_to_start_without_settings(tmp_path, capsys):
    assert cli.main(["ask", "now", "--settings-file", str(tmp_path / "none.toml")]) == 2
    assert "There is no settings file" in capsys.readouterr().err


def test_a_server_without_settings_answers_each_tool_with_what_to_write(tmp_path, capsys):
    missing = tmp_path / "config.toml"
    service = cli.make_service(simulate=False, settings_file=str(missing), serving=True)
    assert "There is no settings file" in capsys.readouterr().err
    for answer in (asyncio.run(service.now()), asyncio.run(service.history("month"))):
        assert answer["success"] is False
        assert answer["say"].startswith("ams-mcp can't read its settings. There is no settings file at ")
        assert str(missing) in answer["say"]
        assert answer["say"].endswith("Then restart the plugin.")


def test_a_question_without_settings_still_stops(tmp_path):
    with pytest.raises(SettingsError):
        cli.make_service(simulate=False, settings_file=str(tmp_path / "none.toml"), serving=False)
