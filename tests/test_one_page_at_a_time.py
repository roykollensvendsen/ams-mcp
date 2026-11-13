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
import time

import httpx
import pytest

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
        self.paths = []

    async def handle_async_request(self, request):
        if self.open:
            msg = "the reader is serving another connection"
            raise httpx.ConnectError(msg, request=request)
        self.open += 1
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


def test_a_page_waiting_its_turn_still_gives_up_within_its_own_time(monkeypatch):
    # The time a page waits behind another counts against its own limit, so a
    # reader that hangs never makes an answer wait for several pages in a row.
    monkeypatch.setattr(reader, "TIMEOUT", 0.3)
    fake = OneAtATime(hang={"data.json"})

    async def two_at_once():
        r = _reader(fake)
        return await asyncio.gather(r.page("data.json"), r.page("monthplot.json"), return_exceptions=True)

    started = time.monotonic()
    first, second = asyncio.run(two_at_once())
    assert isinstance(first, reader.ReaderError)
    assert "data.json was not answered" in str(first)
    assert isinstance(second, reader.ReaderError)
    assert "monthplot.json waited its turn" in str(second)
    assert time.monotonic() - started < 0.5
