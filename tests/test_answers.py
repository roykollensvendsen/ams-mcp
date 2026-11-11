# SPDX-License-Identifier: GPL-3.0-or-later
"""What each tool says, from pages recorded off a real reader (src/ams_mcp/recorded).

The pages were read at 14:10:46 on 11 November 2026, Oslo time, which is the
clock these tests stand at. Every figure below was worked out by hand from
those pages, so a change in how a page is read shows here as a wrong number.
"""

import asyncio
import copy
import zoneinfo

import pytest

from ams_mcp import service
from ams_mcp.reader import Page, ReaderError
from ams_mcp.service import AmsService
from ams_mcp.simulator import recorded

OSLO = zoneinfo.ZoneInfo("Europe/Oslo")
READ_AT = 1794402646.0  # 2026-11-11 14:10:46 in Oslo


class Pages:
    """The recorded pages, read at READ_AT, with any changed as a test asks."""

    def __init__(self, changes=None, read_at=READ_AT, broken=False):
        self.changes, self.read_at, self.broken, self.asked = changes or {}, read_at, broken, []

    async def page(self, name):
        self.asked.append(name)
        if self.broken:
            raise ReaderError("the reader did not answer: ConnectTimeout")
        body = copy.deepcopy(recorded(name))
        self.changes.get(name, lambda b: None)(body)
        return Page(body, self.read_at)


def _ask(tool, pages=None, now=READ_AT + 1, **arguments):
    svc = AmsService(pages or Pages(), timezone=OSLO, clock=lambda: now)
    return asyncio.run(getattr(svc, tool)(**arguments))


def _set(**values):
    def change(body):
        body.update(values)

    return change


# ams_now


def test_now_says_the_power_drawn_and_each_phase():
    a = _ask("now")
    assert a["success"] is True
    assert a["say"] == (
        "Drawing 1.05 kW from the grid now. The phases carry 2.9, 0.9 and 1.5 A. "
        "This meter sends no power factor."
    )
    assert a["import_kw"] == 1.048
    assert a["export_kw"] == 0.0
    assert [(p["phase"], p["volts"], p["amps"]) for p in a["phases"]] == [
        ("L1", 233.0, 2.89),
        ("L2", 235.0, 0.94),
        ("L3", 234.0, 1.47),
    ]
    assert a["power_factor"] is None


def test_now_says_power_sent_out_and_the_power_factor_when_the_meter_gives_them():
    a = _ask("now", Pages({"data.json": _set(i=0, e=2300, f=0.97)}))
    assert a["say"].startswith("Sending 2.30 kW to the grid now.")
    assert a["say"].endswith("Power factor 0.97.")


def test_every_answer_carries_the_time_of_its_reading():
    for tool in ("now", "energy", "price", "capacity", "history"):
        a = _ask(tool)
        assert a["read_at"] == "2026-11-11T14:10:46+01:00", tool
        assert a["confirmed"] is True, tool


def test_a_reading_older_than_about_half_a_minute_is_not_confirmed():
    a = _ask("now", now=READ_AT + service.STALE_AFTER + 1)
    assert a["confirmed"] is False
    assert a["say"].startswith("Not confirmed: this reading is 31 seconds old. Drawing 1.05 kW")


def test_a_meter_the_reader_has_not_heard_from_lately_is_not_confirmed():
    a = _ask("energy", Pages({"data.json": _set(hm=3)}))
    assert a["confirmed"] is False
    assert a["say"].startswith("Not confirmed: the reader has not heard from the meter for over 30 seconds.")


# ams_energy


def test_energy_says_today_this_hour_and_this_month():
    a = _ask("energy")
    assert a["say"] == "Today 33.2 kWh, 30.85 kr. This hour 0.32 kWh. This month 605 kWh, 548 kr."
    assert a["today"] == {"import_kwh": 33.18, "export_kwh": 0.0, "cost": 30.85, "income": 0.0}
    assert a["hour"]["import_kwh"] == 0.32
    assert a["month"]["cost"] == 548.12
    assert a["currency"] == "NOK"


def test_energy_sent_out_is_said_only_when_there_is_some():
    def sent(body):
        body["ea"]["d"]["p"] = 4.5

    assert "4.50 kWh out" in _ask("energy", Pages({"data.json": sent}))["say"]


# ams_price


def test_price_says_now_the_next_hours_and_the_cheapest_left_today():
    a = _ask("price")
    assert a["say"] == (
        "1.02 kr per kWh now. The next three hours 1.02, 1.00 and 1.06 kr. "
        "Cheapest left today: 23:00 at 0.87, 22:00 at 0.89 and 21:00 at 0.93 kr."
    )
    assert a["now"] == {"hour": "14:00", "price": 1.0165}
    assert a["next"][0] == {"hour": "15:00", "price": 1.0195}
    assert len(a["next"]) == service.NEXT_HOURS
    assert [c["hour"] for c in a["cheapest_today"]] == ["23:00", "22:00", "21:00"]
    assert a["currency"] == "NOK"


def test_a_reader_with_no_prices_says_so():
    def empty(body):
        for k in list(body):
            if k.isdigit():
                body[k] = None

    a = _ask("price", Pages({"energyprice.json": empty}))
    assert a["success"] is False
    assert "no prices" in a["say"]


# ams_capacity


def test_capacity_says_the_step_the_top_hours_and_how_far_this_hour_is_from_the_next():
    a = _ask("capacity")
    assert a["say"] == (
        "Capacity step 5 to 10 kW. Your top three hours average 5.25 kW: "
        "5.88 on the 9th, 4.96 on the 6th and 4.92 on the 7th. "
        "This hour has used 0.32 kWh, and at this rate ends near 1.18. "
        "It would take over 19.2 kWh this hour to reach the next step."
    )
    assert a["step"] == {"from_kw": 5, "to_kw": 10}
    assert a["top_hours"] == [
        {"day": 9, "kwh": 5.88},
        {"day": 6, "kwh": 4.96},
        {"day": 7, "kwh": 4.92},
    ]
    assert a["next_step_after_kwh"] == pytest.approx(19.16)
    assert a["projected_hour_kwh"] == pytest.approx(1.18, abs=0.01)


def test_a_day_already_among_the_top_hours_counts_once():
    # Today (the 11th) already has 4.96 among the top three: a bigger hour today
    # replaces it rather than pushing out the 7th.
    def today_in_top(body):
        body["p"] = [{"d": 9, "v": 5.88}, {"d": 11, "v": 4.96}, {"d": 7, "v": 4.92}]

    a = _ask("capacity", Pages({"tariff.json": today_in_top}))
    assert a["next_step_after_kwh"] == pytest.approx(30 - 5.88 - 4.92)


def test_the_top_step_has_no_next_one():
    a = _ask("capacity", Pages({"tariff.json": _set(c=65535, m=160.0)}))
    assert a["step"] == {"from_kw": 150, "to_kw": None}
    assert "highest step" in a["say"]
    assert a["next_step_after_kwh"] is None


# ams_history


def test_history_of_the_day_says_today_so_far_and_its_busiest_and_quietest_hours():
    a = _ask("history")
    assert a["say"] == (
        "Today so far 33.2 kWh, 30.85 kr. Busiest hour 00:00 with 3.51 kWh, quietest 11:00 with 1.32 kWh."
    )
    assert len(a["hours"]) == 24
    assert a["hours"][0] == {"start": "2026-11-10T14:00:00+01:00", "import_kwh": 1.09, "export_kwh": 0.0}
    assert a["hours"][-1] == {"start": "2026-11-11T13:00:00+01:00", "import_kwh": 1.92, "export_kwh": 0.0}


def test_history_of_the_month_says_the_month_so_far_its_extremes_and_last_month():
    a = _ask("history", period="month")
    assert a["say"] == (
        "This month so far 605 kWh, 548 kr. Busiest day the 9th with 70.9 kWh, "
        "quietest the 2nd with 47.8 kWh. Last month 1562 kWh, 1255 kr."
    )
    assert [d["day"] for d in a["days"]] == list(range(1, 11))
    assert a["last_month"]["import_kwh"] == 1561.95


def test_history_refuses_a_period_it_does_not_know():
    a = _ask("history", period="year")
    assert a["success"] is False
    assert "day or month" in a["say"]


# Failing closed


def test_a_reader_that_does_not_answer_is_said_and_nothing_is_guessed():
    for tool in ("now", "energy", "price", "capacity", "history"):
        a = _ask(tool, Pages(broken=True))
        assert a["success"] is False, tool
        assert a["say"].startswith("The meter reader did not answer."), tool
        assert "kW" not in a["say"], tool


def test_a_page_in_a_shape_the_firmware_does_not_give_is_refused():
    a = _ask("now", Pages({"data.json": lambda b: b.pop("ea")}))
    assert a["success"] is False
    assert "v2.4.11" in a["say"]
