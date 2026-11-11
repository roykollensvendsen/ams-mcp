# SPDX-License-Identifier: GPL-3.0-or-later
"""The MCP tools, as the voice sees them, against the simulated reader."""

import asyncio
import re
import time
import zoneinfo

import httpx

from ams_mcp import reader, server, service
from ams_mcp.server import build_server
from ams_mcp.service import AmsService
from ams_mcp.simulator import SimulatedReader

TOOLS = {"ams_now", "ams_energy", "ams_price", "ams_capacity", "ams_history"}
#: The characters of a description the voice saw before loading a tool, 2026-11-10 (tv-mcp).
OPENING_LIMIT = 26
#: How long the Claude app waits for a tool, in seconds (the plugin standard's rule 3).
VOICE_LIMIT = 15.0
OSLO = zoneinfo.ZoneInfo("Europe/Oslo")


def _server(source=None, *, simulated=True):
    return build_server(AmsService(source or SimulatedReader(), timezone=OSLO), simulated=simulated)


def _tools(srv):
    return {tool.name: tool for tool in asyncio.run(srv.list_tools())}


def _call(srv, tool, arguments=None):
    return asyncio.run(srv.call_tool(tool, arguments or {})).structured_content


def test_the_five_tools_are_there_and_named_after_the_plugin():
    assert set(_tools(_server())) == TOOLS


def test_every_tool_is_marked_read_only():
    for name, tool in _tools(_server()).items():
        assert tool.annotations is not None, name
        assert tool.annotations.read_only_hint is True, name


def test_every_tool_opens_with_a_whole_sentence_the_voice_sees_in_full():
    openings = {}
    for name, tool in _tools(_server()).items():
        opening = (tool.description or "").split("\n", 1)[0]
        assert len(opening) <= OPENING_LIMIT, f"{name}: {opening!r}"
        assert re.fullmatch(r"[A-Z][^.?;:]*[.?]", opening), f"{name}: {opening!r}"
        start = " ".join(opening.casefold().split()[:2])
        assert openings.setdefault(start, name) == name, f"{name} and {openings[start]} open alike"


#: The first word of each opening: a verb, or a question word, so it reads as a sentence.
OPENING_VERBS = {"How", "Tells", "Gives", "Shows", "Sums"}


def test_every_tool_opens_with_a_sentence_not_a_phrase():
    for name, tool in _tools(_server()).items():
        first = (tool.description or "").split()[0]
        assert first in OPENING_VERBS, f"{name} opens with {first!r}"


def test_every_simulated_answer_says_simulated_first():
    srv = _server()
    for tool in TOOLS:
        answer = _call(srv, tool)
        assert answer["say"].startswith("Simulated: "), tool
        assert answer["simulated"] is True, tool
        assert answer["success"] is True, tool
        assert answer["confirmed"] is True, tool
        assert "read_at" in answer, tool


def test_a_real_answer_does_not_say_simulated():
    answer = _call(_server(simulated=False), "ams_energy")
    assert not answer["say"].startswith("Simulated")
    assert answer["simulated"] is False


def test_history_takes_day_or_month():
    srv = _server()
    assert "This month so far" in _call(srv, "ams_history", {"period": "month"})["say"]
    assert "Today so far" in _call(srv, "ams_history", {"period": "day"})["say"]


class _Broken:
    async def page(self, name):
        msg = "a bug"
        raise ZeroDivisionError(msg)


class _Slow:
    async def page(self, name):
        await asyncio.sleep(2)


def test_every_tool_answers_with_a_sentence_even_when_it_fails():
    for tool in TOOLS:
        answer = _call(_server(_Broken(), simulated=False), tool)
        assert answer["success"] is False, tool
        question = tool.removeprefix("ams_")
        assert answer["say"] == server.BROKE.format(question=question), tool
        assert answer["say"].endswith(f"run: cd ~/ams-mcp && uv run ams-mcp ask {question}"), tool


def test_a_tool_still_working_at_the_deadline_says_so(monkeypatch):
    monkeypatch.setattr(server, "ANSWER_WITHIN", 0.05)
    answer = _call(_server(_Slow(), simulated=False), "ams_now")
    assert answer["success"] is False
    assert answer["say"] == server.WORKING


def test_a_tools_longest_wait_fits_in_the_voices_time():
    # A tool reads its pages side by side, so its longest wait is one page's
    # two tries, which share TIMEOUT; the guard cuts any tool off at ANSWER_WITHIN.
    assert reader.FIRST_TRY < reader.TIMEOUT < server.ANSWER_WITHIN <= 12.0
    assert server.ANSWER_WITHIN + 2.0 < VOICE_LIMIT  # room for the bridge and the network


def test_a_reader_that_hangs_gets_the_error_and_its_next_step_inside_the_budget(monkeypatch):
    # The same path as the real thing, with every limit scaled down a hundredfold.
    monkeypatch.setattr(reader, "TIMEOUT", reader.TIMEOUT / 100)
    monkeypatch.setattr(reader, "FIRST_TRY", reader.FIRST_TRY / 100)
    monkeypatch.setattr(server, "ANSWER_WITHIN", server.ANSWER_WITHIN / 100)

    async def hang(request):
        await asyncio.sleep(5)

    source = reader.HttpReader("reader.example", transport=httpx.MockTransport(hang))
    srv = build_server(AmsService(source, timezone=OSLO, where="reader.example"))
    started = time.monotonic()
    answer = _call(srv, "ams_now")
    assert time.monotonic() - started < server.ANSWER_WITHIN
    assert answer["say"] == service.UNREACHABLE.format(where="reader.example")
