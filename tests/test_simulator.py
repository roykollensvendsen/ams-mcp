# SPDX-License-Identifier: GPL-3.0-or-later
"""The pretend reader keeps the real one's limits, so --simulate tries the real code.

A pretend reader that answers everything at once lets a change pass in
simulate mode that fails at home. This one is read through the same
HttpReader as the real reader, and serves its recorded pages the way the
firmware does: one visitor at a time, finishing each request it began. Each
condition is a state the real reader has been seen in, so it can be heard by
voice before it happens at home.
"""

import asyncio
import time

import httpx
import pytest

from ams_mcp import cli, simulator
from ams_mcp.reader import HttpReader
from ams_mcp.service import AmsService
from ams_mcp.settings import local_timezone


def test_the_pretend_reader_serves_one_visitor_at_a_time(monkeypatch):
    monkeypatch.setattr(simulator, "ANSWER", 0.05)
    pretend = simulator.PretendReader()

    async def two_at_once():
        async with httpx.AsyncClient(transport=pretend) as client:
            await asyncio.gather(*(client.get(f"http://pretend/{n}") for n in ("data.json", "tariff.json")))

    asyncio.run(two_at_once())
    (_, first_done), (second_began, _) = sorted(pretend.served)
    assert second_began >= first_done


def test_the_pretend_reader_finishes_a_request_the_plugin_gave_up_on(monkeypatch):
    monkeypatch.setattr(simulator, "ANSWER", 0.1)
    pretend = simulator.PretendReader()

    async def give_up_then_ask():
        async with httpx.AsyncClient(transport=pretend) as client:
            with pytest.raises(TimeoutError):
                async with asyncio.timeout(0.02):
                    await client.get("http://pretend/data.json")
            started = time.monotonic()
            await client.get("http://pretend/tariff.json")
            return time.monotonic() - started

    assert asyncio.run(give_up_then_ask()) >= 0.15


def test_a_simulated_answer_is_read_through_the_real_reader_code():
    pretend = simulator.SimulatedReader()
    assert isinstance(pretend, HttpReader)
    answer = asyncio.run(AmsService(pretend, timezone=local_timezone()).history("month"))
    assert answer["success"] is True, answer["say"]
    assert pretend.pretend.asked_while_busy == 0
    assert sorted(pretend.pretend.asked) == ["data.json", "monthplot.json", "sysinfo.json"]


def _ask(capsys, question, condition):
    code = cli.main(["ask", question, "--simulate", condition])
    return code, capsys.readouterr().out.strip()


def test_a_reader_asleep_answers_after_it_wakes(monkeypatch, capsys):
    monkeypatch.setattr(simulator, "WAKE", 0.3)
    started = time.monotonic()
    code, said = _ask(capsys, "now", "asleep")
    assert time.monotonic() - started >= 0.3
    assert code == 0
    assert said.startswith("Simulated: Drawing ")


def test_a_reader_switched_off_is_said_with_what_to_check(monkeypatch, capsys):
    monkeypatch.setattr(simulator, "OFF_AFTER", 0.05)
    code, said = _ask(capsys, "now", "off")
    assert code == 1
    assert said.startswith("Simulated: The meter reader at ")
    assert "Check that it has power and is on the Wi-Fi" in said


def test_a_meter_gone_quiet_is_not_confirmed(capsys):
    code, said = _ask(capsys, "now", "meter-quiet")
    assert code == 0
    assert said.startswith("Simulated: Not confirmed: the reader has not heard from the meter")


def test_a_reader_asking_for_a_login_says_how_to_let_the_plugin_read(capsys):
    code, said = _ask(capsys, "now", "login")
    assert code == 1
    assert said.startswith("Simulated: ")
    assert "never sends one" in said


def test_simulate_alone_is_a_reader_that_is_awake_and_well(capsys):
    assert cli.main(["ask", "energy", "--simulate"]) == 0
    assert capsys.readouterr().out.startswith("Simulated: Today ")
