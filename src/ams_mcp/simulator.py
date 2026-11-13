# SPDX-License-Identifier: GPL-3.0-or-later
"""A pretend meter reader, for tests and a first try by voice.

It shows the pages recorded off a real reader (src/ams_mcp/recorded, with
nothing in them that identifies the home), set to the present moment, so
every tool answers as it would at home. The pages are read through the same
HttpReader as the real reader, from a pretend one with the real one's limits,
and it can be put in a condition the real one has been seen in: asleep, off,
with the meter gone quiet, or asking for a login. The server marks each
answer "Simulated:" first.
"""

from __future__ import annotations

import asyncio
import copy
import functools
import json
import time
from importlib import resources
from typing import TYPE_CHECKING, Any

import httpx

from ams_mcp.reader import PAGES, HttpReader, RefusedPageError

if TYPE_CHECKING:
    from collections.abc import Callable


@functools.cache
def _load(name: str) -> dict[str, Any]:
    text = resources.files("ams_mcp").joinpath("recorded", name).read_text()
    body: dict[str, Any] = json.loads(text)
    return body


def recorded(name: str) -> dict[str, Any]:
    """A page as it was recorded, a fresh copy each time."""
    if name not in PAGES:
        msg = f"{name!r} is not one of the pages this plugin may read"
        raise RefusedPageError(msg)
    return copy.deepcopy(_load(name))


#: The pretend reader's name, where a message names the reader's address.
ADDRESS = "pretend.reader"
#: The states the pretend reader can be in, each one seen in the real reader.
CONDITIONS = ("awake", "asleep", "off", "meter-quiet", "login")
#: Seconds the pretend reader takes over a page when it is awake.
ANSWER = 0.05
#: Seconds the first visitor waits while an idle reader wakes its Wi-Fi: 5 to 8 were
#: seen on 2026-11-11.
WAKE = 6.0
#: Seconds a reader that is off takes to be found missing: on 2026-11-13 the network
#: said "no route to host" after 3.08.
OFF_AFTER = 3.0
#: The reader's view of the meter when it has not had a reading lately (docs/pages.md).
METER_QUIET = 3


class PretendReader(httpx.AsyncBaseTransport):
    """The recorded pages, served the way the firmware serves them.

    Like the firmware's WebServer it serves one visitor at a time, in order,
    and finishes each request it began, even one the visitor gave up on.
    """

    def __init__(self, condition: str = "awake", wall: Callable[[], float] = time.time) -> None:
        """A reader in this condition, on this clock."""
        if condition not in CONDITIONS:
            msg = f"{condition!r} is not one of {', '.join(CONDITIONS)}"
            raise ValueError(msg)
        self.condition, self._wall = condition, wall
        self._busy = asyncio.Lock()
        self._next_wait = {"asleep": WAKE}.get(condition, ANSWER)
        self._meter = {"meter-quiet": METER_QUIET}.get(condition, 1)
        self.asked: list[str] = []
        self.asked_while_busy = 0
        self.served: list[tuple[float, float]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """A page, a refusal, or nothing, as the condition says."""
        name = request.url.path.lstrip("/")
        self.asked.append(name)
        self.asked_while_busy += self._busy.locked()
        if self.condition == "off":
            await asyncio.sleep(OFF_AFTER)
            msg = "no route to host"
            raise httpx.ConnectError(msg, request=request)
        # The visitor may leave; what the reader began, it finishes.
        return await asyncio.shield(asyncio.ensure_future(self._serve(name)))

    async def _serve(self, name: str) -> httpx.Response:
        # RULE: the pretend reader serves one visitor at a time
        async with self._busy:
            began = time.monotonic()
            await asyncio.sleep(self._next_wait)
            self._next_wait = ANSWER
            self.served.append((began, time.monotonic()))
        if self.condition == "login":
            return httpx.Response(401, text="Unauthorized")
        return httpx.Response(200, json=self._page(name))

    def _page(self, name: str) -> dict[str, Any]:
        body = recorded(name)
        if name == "data.json":
            body["c"] = int(self._wall())
            body["hm"] = self._meter
        return body


class SimulatedReader(HttpReader):
    """A pretend reader, read through the same code as the real one."""

    def __init__(self, condition: str = "awake", wall: Callable[[], float] = time.time) -> None:
        """A pretend reader in this condition, on this clock."""
        self.pretend = PretendReader(condition, wall)
        super().__init__(ADDRESS, transport=self.pretend, wall=wall)
