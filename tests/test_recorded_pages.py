# SPDX-License-Identifier: GPL-3.0-or-later
"""The pages recorded off a real reader: read right, and with nothing about the home in them.

They were read once from an AmsToMqttBridge v2.4.11 reader on 2026-11-11, with
GET, and then had every identifying value replaced: the meter's id and model,
the chip id, both hardware addresses, the host name, the network addresses and
the price region. These tests keep it that way, and pin the key map to the
firmware it was read from.
"""

import json
import re
from importlib import resources

import pytest

from ams_mcp import pages
from ams_mcp.reader import PAGES
from ams_mcp.simulator import recorded

#: Values that stand in for the real ones, from ranges set aside for examples.
PLACEHOLDERS = {
    ("chipId",): "000000000000",
    ("mac",): "00:00:5E:00:53:01",
    ("apmac",): "00:00:5E:00:53:02",
    ("hostname",): "ams-reader",
    ("meter", "id"): "0000000000000000",
    ("meter", "model"): "EXAMPLE-MODEL",
    ("net", "ip"): "192.0.2.10",
    ("net", "gw"): "192.0.2.1",
}


def _at(body, path):
    for key in path:
        body = body[key]
    return body


def test_every_allowed_page_has_a_recording():
    folder = resources.files("ams_mcp").joinpath("recorded")
    assert {p.name for p in folder.iterdir() if p.name.endswith(".json")} == set(PAGES)


@pytest.mark.parametrize(("path", "placeholder"), PLACEHOLDERS.items())
def test_nothing_that_identifies_the_home_is_in_the_recordings(path, placeholder):
    assert _at(recorded("sysinfo.json"), path) == placeholder


def test_no_recording_holds_an_address_a_sensor_id_or_a_network_name():
    text = " ".join(json.dumps(recorded(p)) for p in PAGES)
    hardware = [
        m for m in re.findall(r"(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}", text) if "00:5E:00:53" not in m
    ]
    assert not hardware
    assert not re.search(r"(?<![\d.])(?:10|172\.(?:1[6-9]|2\d|3[01])|192\.168)\.\d+\.\d+", text)
    assert not any(k in text for k in ('"ssid"', '"psk"', '"pass"', '"wifi"'))
    assert all("a" not in s for s in recorded("temperature.json")["s"])
    assert recorded("data.json")["pr"] == "NO1"


def test_the_key_map_was_read_from_the_firmware_the_recordings_came_from():
    assert recorded("sysinfo.json")["version"] == pages.FIRMWARE


def test_data_json_short_keys_mean_what_the_firmware_says():
    live = pages.live(recorded("data.json"))
    assert (live.import_w, live.export_w) == (1048, 0)  # "i", "e": active power in and out, W
    assert live.phases[0].volts == 233.0  # "l1"."u"
    assert live.phases[0].amps == 2.89  # "l1"."i"
    assert live.phases[0].import_w is None  # "l1"."p" is 0 while current flows: not sent
    assert live.today.import_kwh == 33.18  # "ea"."d"."u"
    assert live.month.cost == 548.12  # "ea"."m"."c"
    assert live.top_hours_kwh == (5.88, 4.96, 4.92)  # "ea"."p"
    assert live.step_kw == 10  # "ea"."t"
    assert live.han_status == 1  # "hm"
    assert live.fresh


def test_tariff_json_short_keys_mean_what_the_firmware_says():
    t = pages.tariff(recorded("tariff.json"))
    assert t.steps_kw == (5, 10, 15, 20, 25, 50, 75, 100, 150)  # "t", without the 65535 end mark
    assert t.peaks[0] == pages.Peak(day=9, kwh=5.88)  # "p": "d" day of month, "v" kWh
    assert (t.step_kw, t.month_max_kwh) == (10, 5.25)  # "c", "m"


def test_the_plots_and_prices_are_indexed_as_the_firmware_writes_them():
    assert pages.prices(recorded("energyprice.json")).by_hour[0] == 1.0165  # "00" is this hour
    assert (
        pages.hours(recorded("dayplot.json")).import_kwh[0] == 3.21
    )  # "i00": the hour from 00:00 UTC, 01:00 in Oslo
    assert pages.days(recorded("monthplot.json")).import_kwh[9] == 70.85  # "i09": the 9th
    assert pages.system(recorded("sysinfo.json")).last_month.import_kwh == 1561.95
