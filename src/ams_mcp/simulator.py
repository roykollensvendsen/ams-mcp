# SPDX-License-Identifier: GPL-3.0-or-later
"""A pretend meter reader, for tests and a first try by voice.

It shows the pages recorded off a real reader (src/ams_mcp/recorded, with
nothing in them that identifies the home), set to the present moment, so
every tool answers as it would at home. The server marks each answer
"Simulated:" first.
"""

from __future__ import annotations

import copy
import functools
import json
import time
from importlib import resources
from typing import TYPE_CHECKING, Any

from ams_mcp.reader import PAGES, Page, RefusedPageError

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


class SimulatedReader:
    """The recorded pages, read just now, from a meter that has just reported."""

    def __init__(self, wall: Callable[[], float] = time.time) -> None:
        """A reader on this clock."""
        self._wall = wall

    async def page(self, name: str) -> Page:
        """The named page, as a real reader shows it."""
        body, now = recorded(name), self._wall()
        if name == "data.json":
            body["c"], body["hm"] = int(now), 1
        return Page(body, now)
