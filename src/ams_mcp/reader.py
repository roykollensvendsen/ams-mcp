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
#: Seconds one page may take in all, both tries together. The reader answers in well under
#: a second, but after a few idle seconds it can take five to eight while it wakes its Wi-Fi
#: (2026-11-11). Every tool stays inside server.ANSWER_WITHIN, which is inside the voice's 15.
TIMEOUT = 11.0
#: How many times a page is asked for, at most. A second try is made only when the reader
#: dropped the first, never while the first may still be served: its WebServer serves one
#: visitor at a time and finishes what it began, so a second request would only wait
#: behind the first and leave the reader busy after it. A lost packet is sent again by TCP
#: itself, after 1, 3 and 7 s, all inside TIMEOUT. A GET changes nothing, so asking twice
#: is safe.
TRIES = 2
#: Seconds a page is reused before it is read again, so a burst of questions is one read.
CACHE_FOR = 3.0


class ReaderError(RuntimeError):
    """The reader did not answer, or not with a page."""


class StrangeAnswerError(ReaderError):
    """The reader answered, but not with the page: a firmware it was not written for, or a login."""

    def __init__(self, message: str, *, login: bool = False) -> None:
        """Why the answer was strange; login when the reader asked for one."""
        super().__init__(message)
        self.login = login


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
        # The firmware's WebServer serves one client at a time (AmsWebServer.cpp,
        # server.handleClient()), so a second connection waits or is refused.
        self._turn = asyncio.Lock()

    async def page(self, name: str) -> Page:
        """The named page, read now or a moment ago."""
        # RULE: only an allow-listed page is ever asked for
        if name not in PAGES:
            msg = f"{name!r} is not one of the pages this plugin may read"
            raise RefusedPageError(msg)
        waiting = True
        try:
            async with asyncio.timeout(TIMEOUT):
                # RULE: the reader is asked for one page at a time
                async with self._turn:
                    waiting = False
                    return self._fresh(name) or await self._kept(name)
        except TimeoutError as error:
            why = "waited its turn behind another page" if waiting else "was not answered"
            msg = f"the reader did not answer: {name} {why} for {TIMEOUT:g} s"
            raise ReaderError(msg) from error

    def _fresh(self, name: str) -> Page | None:
        """The page read under CACHE_FOR seconds ago, if there is one."""
        read_at, page = self._cache.get(name, (float("-inf"), None))
        return page if self._clock() - read_at < CACHE_FOR else None

    async def _kept(self, name: str) -> Page:
        """The page read now, and kept for CACHE_FOR seconds."""
        page = Page(await self._get(name), self._wall())
        self._cache[name] = (self._clock(), page)
        return page

    async def _get(self, name: str) -> dict[str, Any]:
        try:
            response = await self._read(name)
        except httpx.HTTPError as error:
            msg = f"the reader did not answer: {type(error).__name__}"
            raise ReaderError(msg) from error
        if response.status_code != httpx.codes.OK:
            msg = f"the reader answered {response.status_code} for {name}"
            login = response.status_code in {httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN}
            raise StrangeAnswerError(msg, login=login)
        try:
            body = json.loads(response.content)
        except ValueError as error:
            msg = f"{name} was not JSON"
            raise StrangeAnswerError(msg) from error
        if not isinstance(body, dict):
            msg = f"{name} was not a JSON object"
            raise StrangeAnswerError(msg)
        return body

    async def _read(self, name: str) -> httpx.Response:
        """The page, asked for once more only if the reader dropped the first request."""
        failure = None
        # RULE: a page is asked for at most twice
        for _ in range(TRIES):
            try:
                return await self._once(name)
            except httpx.HTTPError as error:
                failure = error
        raise failure or httpx.TransportError(name)

    async def _once(self, name: str) -> httpx.Response:
        # RULE: a reader still serving a page is not asked for it again
        # httpx's own limit is a backstop past TIMEOUT: page() gives up first, and then nothing
        # is asked again, so an error here means the reader dropped the request, not that it
        # is slow.
        async with httpx.AsyncClient(
            transport=self._transport, timeout=TIMEOUT + 1, follow_redirects=False
        ) as client:
            # RULE: a page is only ever read with get
            return await client.get(f"{self._base}/{name}")
