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
#: Seconds the first try may take before the page is asked for once more, on a new
#: connection, with what is left of TIMEOUT. A lost packet costs TCP 1, 3, then 7 s before
#: it tries again; a fresh connection is usually answered at once. A GET changes nothing,
#: so asking twice is safe.
FIRST_TRY = 6.0
#: How many times a page is asked for, at most.
TRIES = 2
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
            response = await self._read(name)
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

    async def _read(self, name: str) -> httpx.Response:
        """The page, asked for once more if the first try fails or hangs, all within TIMEOUT."""
        now = asyncio.get_running_loop().time()
        deadline, failure = now + TIMEOUT, None
        # RULE: a page is asked for at most twice
        for attempt in range(TRIES):
            limit = deadline if attempt else min(deadline, now + FIRST_TRY)
            try:
                async with asyncio.timeout_at(limit):
                    return await self._once(name)
            except (httpx.HTTPError, TimeoutError) as error:
                failure = error
        raise failure or TimeoutError

    async def _once(self, name: str) -> httpx.Response:
        async with httpx.AsyncClient(
            transport=self._transport, timeout=TIMEOUT, follow_redirects=False
        ) as client:
            # RULE: a page is only ever read with get
            return await client.get(f"{self._base}/{name}")
