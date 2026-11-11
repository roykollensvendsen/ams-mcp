# SPDX-License-Identifier: GPL-3.0-or-later
"""Reads the meter reader's JSON pages over HTTP, with GET only.

AmsToMqttBridge also answers POST for saving settings, rebooting and
uploading firmware (decisions/ADR-AM-001). None of that is reachable from
here: the only method this module sends is GET, and only to the pages in PAGES.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

import httpx

if TYPE_CHECKING:
    from collections.abc import Callable

#: The only pages ever asked for. Each is read-only in the pinned firmware.
PAGES = (
    "data.json",
    "dayplot.json",
    "monthplot.json",
    "energyprice.json",
    "tariff.json",
    "temperature.json",
    "sysinfo.json",
)
#: Seconds one page may take, connecting and reading together; a tool reads its pages
#: side by side. The reader answers in well under a second, but once took 5.7 s waking its
#: Wi-Fi (2026-11-11). This leaves room for that inside the server's 12 s for an answer.
TIMEOUT = 9.0
#: Seconds before a page not yet answered is asked for once more, on a new connection.
#: The reader drops a packet now and then, and TCP waits 1, 3, then 7 s before trying
#: again; a fresh connection is usually answered at once. A GET changes nothing, so
#: asking twice is safe, and the first answer wins.
HEDGE_AFTER = 3.0
#: Seconds a page is reused before it is read again, so a burst of questions is one read.
CACHE_FOR = 3.0


class ReaderError(RuntimeError):
    """The reader did not answer, or not with a page."""


class RefusedPageError(ValueError):
    """A page outside the allow-list was asked for, and nothing was sent."""


@dataclass(frozen=True)
class Page:
    """A page's JSON, and the wall-clock time it was read."""

    body: dict[str, Any]
    read_at: float


class Source(Protocol):
    """Anything that can show a page: the real reader, or the simulator."""

    async def page(self, name: str) -> Page:
        """The named page."""
        ...


class HttpReader:
    """The real reader, at an address on the home network."""

    def __init__(
        self,
        address: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
    ) -> None:
        """A reader at this address; transport, clock and wall are for tests."""
        self._base = f"http://{address}"
        self._transport, self._clock, self._wall = transport, clock, wall
        self._cache: dict[str, tuple[float, Page]] = {}

    async def page(self, name: str) -> Page:
        """The named page, read now or a moment ago."""
        # RULE: only an allow-listed page is ever asked for
        if name not in PAGES:
            msg = f"{name!r} is not one of the pages this plugin may read"
            raise RefusedPageError(msg)
        cached = self._cache.get(name)
        if cached and self._clock() - cached[0] < CACHE_FOR:
            return cached[1]
        page = Page(await self._get(name), self._wall())
        self._cache[name] = (self._clock(), page)
        return page

    async def _get(self, name: str) -> dict[str, Any]:
        try:
            async with asyncio.timeout(TIMEOUT):
                response = await self._first_of_two(name)
        except (httpx.HTTPError, TimeoutError) as error:
            msg = f"the reader did not answer: {type(error).__name__}"
            raise ReaderError(msg) from error
        if response.status_code != httpx.codes.OK:
            msg = f"the reader answered {response.status_code} for {name}"
            raise ReaderError(msg)
        try:
            body = json.loads(response.content)
        except ValueError as error:
            msg = f"{name} was not JSON"
            raise ReaderError(msg) from error
        if not isinstance(body, dict):
            msg = f"{name} was not a JSON object"
            raise ReaderError(msg)
        return body

    async def _first_of_two(self, name: str) -> httpx.Response:
        """The page, asked for once more when the first try fails or hangs; the first answer wins."""
        first = asyncio.ensure_future(self._once(name))
        running = {first}
        try:
            done, _ = await asyncio.wait(running, timeout=HEDGE_AFTER)
            if first in done and first.exception() is None:
                return first.result()
            # RULE: a page is asked for at most twice
            running.add(asyncio.ensure_future(self._once(name)))
            running -= done
            failure: BaseException | None = first.exception() if first in done else None
            while running:
                done, running = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    if task.exception() is None:
                        return task.result()
                    failure = task.exception()
            raise failure or ReaderError(name)
        finally:
            for task in running:
                task.cancel()

    async def _once(self, name: str) -> httpx.Response:
        async with httpx.AsyncClient(
            transport=self._transport, timeout=TIMEOUT, follow_redirects=False
        ) as client:
            # RULE: a page is only ever read with get
            return await client.get(f"{self._base}/{name}")
