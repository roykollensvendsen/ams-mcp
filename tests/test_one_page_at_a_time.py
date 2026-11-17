# SPDX-License-Identifier: GPL-3.0-or-later
"""The reader is asked for one page at a time, as its firmware serves them.

The pinned firmware (v2.4.11, lib/SvelteUi/src/AmsWebServer.cpp) runs the
Arduino WebServer and calls server.handleClient() from its main loop, which
serves one client at a time. A second connection waits behind the first, or
is refused when the reader is busy, and the reader can take five to eight
seconds to wake its Wi-Fi. The fake here refuses a second connection while
one is open, which is the harshest case: every answer must still come.
"""

import asyncio
import contextlib
import time

import httpx
import pytest
from bounded import WITHIN

from ams_mcp import reader
from ams_mcp.reader import HttpReader
from ams_mcp.service import AmsService
from ams_mcp.settings import local_timezone
from ams_mcp.simulator import recorded


class OneAtATime(httpx.AsyncBaseTransport):
    """A reader that serves one connection and refuses any other while it does."""

    def __init__(self, seconds=0.01, hang=()):
        self.seconds, self.hang = seconds, set(hang)
        self.open = self.most_open = 0
        self.serving = asyncio.Event()  # set once a request holds the connection
        self.paths = []

    async def handle_async_request(self, request):
        if self.open:
            msg = "the reader is serving another connection"
            raise httpx.ConnectError(msg, request=request)
        self.open += 1
        self.serving.set()
        self.most_open = max(self.most_open, self.open)
        self.paths.append(request.url.path)
        try:
            name = request.url.path.lstrip("/")
            await asyncio.sleep(60 if name in self.hang else self.seconds)
            return httpx.Response(200, json=recorded(name))
        finally:
            self.open -= 1


def _reader(fake):
    return HttpReader("192.0.2.10", transport=fake)


def test_the_reader_is_asked_for_one_page_at_a_time():
    fake = OneAtATime()

    async def three_at_once():
        r = _reader(fake)
        return await asyncio.gather(*(r.page(n) for n in ("data.json", "monthplot.json", "sysinfo.json")))

    data, plot, info = asyncio.run(three_at_once())
    assert "i" in data.body
    assert "i01" in plot.body
    assert "version" in info.body
    assert fake.most_open == 1
    assert sorted(fake.paths) == ["/data.json", "/monthplot.json", "/sysinfo.json"]


@pytest.mark.parametrize(
    "question", ["now", "energy", "price", "capacity", ("history", "day"), ("history", "month")]
)
def test_every_answer_comes_from_a_reader_that_serves_one_connection_at_a_time(question):
    fake = OneAtATime()
    service = AmsService(_reader(fake), timezone=local_timezone())
    name, *args = question if isinstance(question, tuple) else (question,)
    answer = asyncio.run(getattr(service, name)(*args))
    assert answer["success"] is True, answer["say"]
    assert fake.most_open == 1


def test_two_questions_at_the_same_moment_read_a_page_once():
    fake = OneAtATime()

    async def both():
        service = AmsService(_reader(fake), timezone=local_timezone())
        return await asyncio.gather(service.now(), service.energy())

    assert all(a["success"] for a in asyncio.run(both()))
    assert fake.paths.count("/data.json") == 1


@pytest.mark.parametrize("stall", [0.0, 0.35], ids=["a quiet machine", "a busy machine"])
def test_a_page_waiting_its_turn_still_gives_up_within_its_own_time(monkeypatch, stall):
    # The time a page waits behind another counts against its own limit, so a
    # reader that hangs never makes an answer wait for several pages in a row.
    # The page ahead keeps its turn for as long as the test needs: started under a
    # long limit, it cannot time out at the same moment as the one waiting. With
    # both on 0.3 s, a late wake-up let both limits pass at once, the first gave
    # up its turn, and the second got it before its own limit fired.
    fake = OneAtATime(hang={"data.json"})
    r = _reader(fake)

    async def one_waits_behind_another():
        ahead = asyncio.create_task(r.page("data.json"))
        # The page ahead holds the turn and is being read; one that never gets there fails.
        await asyncio.wait_for(fake.serving.wait(), WITHIN)
        monkeypatch.setattr(reader, "TIMEOUT", 0.3)
        if stall:  # a busy machine: the loop wakes late, past the waiting page's limit
            asyncio.get_running_loop().call_later(0.01, time.sleep, stall)
        started = time.monotonic()
        try:
            await r.page("monthplot.json")
        except reader.ReaderError as error:
            return error, time.monotonic() - started
        finally:
            ahead.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await ahead
        return None, time.monotonic() - started

    monkeypatch.setattr(reader, "TIMEOUT", 60.0)
    error, took = asyncio.run(one_waits_behind_another())
    assert "monthplot.json waited its turn" in str(error)
    assert took < stall + 0.5  # a stalled loop wakes late; room for a loaded machine on top
